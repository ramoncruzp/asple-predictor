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
    # Fase 40c, parte B4.4: en S1 la terminal no siempre usa UTF-8 (cp1252 por
    # defecto en Windows) y la salida mostraba "pr?stamos" en vez de "préstamos".
    # Forzar UTF-8 en stdout no depende de la configuración regional del host.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capital", type=float, default=100)
    parser.add_argument("--objetivo", type=float, default=10, help="ganancia objetivo en USDT")
    parser.add_argument("--perfil", choices=("PRUDENTE", "RAPIDO"), default="RAPIDO")
    parser.add_argument("--con-cache", action="store_true",
                        help="lee meta_analysis_cache existente en solo lectura; nunca escribe")
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
    operator = MetaOperator(core, db, scanner, None, settings, advisor=advisor,
                            cache_enabled=args.con_cache, cache_writes=False)
    profile = args.perfil.upper()
    goal = {"id": 0, "capital_initial": args.capital, "target_usdt": args.objetivo,
            "profile": profile, "config_snapshot": dict(DEFAULT_PROFILES[profile])}
    started = time.perf_counter()
    plan = operator._build_plan(goal)
    plan["timings_s"]["full_analysis"] = round(time.perf_counter() - started, 3)

    piece_estimates = plan.get("piece_estimates", [])
    most_common_reason = None
    reasons = [item.get("skipped_reason") for item in piece_estimates if item.get("skipped_reason")]
    if reasons:
        most_common_reason = max(set(reasons), key=reasons.count)

    # Tiempo que la app (vía POST /api/metas/{id}/analysis) reportó la última
    # vez, si meta_ledger existe y tiene algún META_ANALYSIS_COMPLETE/FAILED
    # real -- esto es puramente informativo, de solo lectura.
    app_reported = None
    if hasattr(db, "meta_ledger"):
        from sqlalchemy import select
        try:
            with db.engine.connect() as conn:
                row = conn.execute(select(db.meta_ledger.c.event_type, db.meta_ledger.c.details,
                    db.meta_ledger.c.ts).where(db.meta_ledger.c.event_type.in_(
                        ["META_ANALYSIS_COMPLETE", "META_ANALYSIS_FAILED"])).order_by(
                            db.meta_ledger.c.id.desc()).limit(1)).first()
            if row is not None:
                details = json.loads(row[1] or "{}")
                app_reported = {"event_type": row[0], "analysis_seconds": details.get("analysis_seconds"),
                                 "ts": str(row[2])}
        except Exception as exc:
            app_reported = {"error": f"no se pudo leer meta_ledger: {exc}"}

    print(json.dumps({"read_only": True, "opened_orders": False, "created_goal": False,
        "profile": profile, "capital_usdt": args.capital, "target_gain_usdt": args.objetivo,
        "persistent_cache_read": bool(args.con_cache), "persistent_cache_write": False,
        "timings_s": plan["timings_s"], "app_reported_last_analysis": app_reported,
        "candidate_count": len(plan["candidates"]),
        "evaluated_combinations": len(plan["combinations_evaluated"]),
        "selected_pieces": plan["pieces"], "estimate": plan["estimated_time"],
        "piece_estimates": piece_estimates,
        "piece_estimates_summary": {
            "most_common_skip_reason": most_common_reason,
            "total_skipped_windows": sum(item.get("skipped_windows", 0) for item in piece_estimates),
            "total_loss_cut_windows": sum(item.get("loss_cut_windows", 0) for item in piece_estimates),
            "bots": [{"symbol": item["symbol"], "bought": item.get("bot_windows_bought"),
                      "sold": item.get("bot_windows_sold")} for item in piece_estimates
                     if item.get("bot_windows_bought") is not None]},
        "warnings": plan.get("warnings", [])}, ensure_ascii=False, indent=2))
    db.engine.dispose()


if __name__ == "__main__":
    main()
