"""Mide un analisis Meta de solo lectura; no crea metas ni envia ordenes."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace
import sys

# Permite correrlo como "python scripts/medir_analisis_meta.py" desde la raíz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url
from starlette.requests import Request

from api.routes import grid_advisor
from config.models_config import ACTIVE_SYMBOL
from config.settings import Settings
from data.binance_client import BinanceClient
from database.db_manager import DBManager
from grid.meta import DEFAULT_PROFILES
from grid.meta_operator import MetaOperator
from grid.scan_service import GridScanService
from grid.volatility_provider import VolatilityProvider
from models.volatility.live import VolPredictorRegistry


def read_only_db(db_url: str):
    """Refleja tablas existentes sin llamar al constructor que ejecuta create_all."""
    url = make_url(db_url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "asple_predictor.db").resolve()
        ro_url = f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
        engine = create_engine(ro_url, future=True, connect_args={"uri": True})
    else:
        engine = create_engine(db_url, future=True)
        if engine.dialect.name == "postgresql":
            from sqlalchemy import event
            @event.listens_for(engine, "connect")
            def _read_only_connection(connection, _record):
                cursor = connection.cursor()
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cursor.close()
    metadata = MetaData()
    metadata.reflect(engine)
    db = DBManager.__new__(DBManager)
    db.engine, db.metadata = engine, metadata
    for name, table in metadata.tables.items():
        setattr(db, name, table)
    return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, default=100)
    parser.add_argument("--objetivo", type=float, default=10, help="ganancia objetivo en USDT")
    parser.add_argument("--perfil", choices=("PRUDENTE", "RAPIDO"), default="RAPIDO")
    parser.add_argument("--database-url", default=None,
                        help="URL existente; de forma predeterminada usa DATABASE_URL del entorno (no lee .env)")
    args = parser.parse_args()

    settings = Settings(_env_file=None)
    db_url = args.database_url or settings.database_url
    db = read_only_db(db_url)
    client = BinanceClient("", "")
    registry = VolPredictorRegistry()
    registry.load_available()
    scanner = GridScanService(db, client, settings)
    state = SimpleNamespace(settings=settings, db=db, client=client,
        public_market_client=client, grid_scan_service=scanner,
        vol_registry=registry, prediction_loop=SimpleNamespace(latest={}),
        vol_provider=VolatilityProvider(db, data_client=client, registry=registry))
    app = SimpleNamespace(state=state)

    def advisor(symbol, capital, margin_target_pct, days):
        scope = {"type": "http", "method": "GET", "path": "/api/grid/recommend",
            "query_string": b"", "headers": [], "app": app,
            "scheme": "http", "server": ("localhost", 80), "client": ("127.0.0.1", 0)}
        return grid_advisor.recommend(Request(scope), symbol=symbol, capital=capital,
            risk="high", days=days, margin_target_pct=margin_target_pct, range_mode="centrado")

    class ReadOnlyCore:
        def _ledger(self, *args, **kwargs):
            return None

    core = ReadOnlyCore()
    operator = MetaOperator(core, db, scanner, None, settings, advisor=advisor)
    profile = args.perfil.upper()
    goal = {"id": 0, "capital_initial": args.capital, "target_usdt": args.objetivo,
            "profile": profile, "config_snapshot": dict(DEFAULT_PROFILES[profile])}
    started = time.perf_counter()
    plan = operator._build_plan(goal)
    plan["timings_s"]["full_analysis"] = round(time.perf_counter() - started, 3)
    print(json.dumps({"read_only": True, "opened_orders": False, "created_goal": False,
        "profile": profile, "capital_usdt": args.capital, "target_gain_usdt": args.objetivo,
        "timings_s": plan["timings_s"], "candidate_count": len(plan["candidates"]),
        "evaluated_combinations": len(plan["combinations_evaluated"]),
        "selected_pieces": plan["pieces"], "estimate": plan["estimated_time"],
        "warnings": plan.get("warnings", [])}, ensure_ascii=False, indent=2))
    db.engine.dispose()


if __name__ == "__main__":
    main()
