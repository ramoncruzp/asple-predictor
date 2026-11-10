"""Lista o repara enlaces de grids vacíos asociados a metas finalizadas."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FINAL = ("CUMPLIDA", "CERRADA", "DESCARTADA", "CANCELADA",
    "CORTADA_POR_PERDIDA", "VENCIDA")


def listar(db_path: Path):
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        goals = conn.execute("SELECT id,status FROM meta_goals WHERE status IN (%s) ORDER BY id" %
            ",".join("?" for _ in FINAL), FINAL).fetchall()
        candidates = []
        for goal in goals:
            rows = conn.execute("""SELECT l.id AS link_id,l.grid_id,g.status,g.symbol,
                SUM(CASE WHEN CAST(COALESCE(gl.held_qty,'0') AS REAL)>0 THEN 1 ELSE 0 END) AS held,
                SUM(CASE WHEN gl.state IN ('BUY_OPEN','SELL_OPEN') THEN 1 ELSE 0 END) AS opened
                FROM meta_piece_links l JOIN grids g ON g.id=l.grid_id
                LEFT JOIN grid_levels gl ON gl.grid_id=g.id
                WHERE l.meta_id=? AND l.released_at IS NULL GROUP BY l.id,l.grid_id,g.status,g.symbol
                ORDER BY l.grid_id""", (goal["id"],)).fetchall()
            for row in rows:
                item = dict(row)
                item["meta_id"] = int(goal["id"])
                item["meta_status"] = goal["status"]
                item["eligible_local"] = not item["held"] and not item["opened"]
                candidates.append(item)
    return candidates


def reparar(db_path: Path):
    # Settings solo se carga en el modo explícito de reparación, para que el
    # comando de inspección no necesite configuración ni pueda escribir.
    from config.settings import Settings
    from data.testnet_client import TestnetClient
    from data.proxy_config import resolve_binance_proxy
    from database.db_manager import DBManager
    from grid.engine import GridEngine
    from grid.meta import MetaCore

    settings = Settings()
    key, secret = str(settings.testnet_api_key or "").strip(), str(settings.testnet_api_secret or "").strip()
    if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
        raise RuntimeError("el modo confirmar requiere credenciales válidas de Testnet")
    settings.database_url = f"sqlite:///{db_path.resolve().as_posix()}"
    db = DBManager(settings.database_url)
    exchange = TestnetClient(key, secret, production_api_key=settings.binance_api_key)
    proxy, _source = resolve_binance_proxy()
    if proxy and getattr(exchange.client, "session", None) is not None:
        exchange.client.session.proxies.update({"http": proxy, "https": proxy})
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings)
    from sqlalchemy import select
    with db.engine.connect() as conn:
        goals = conn.execute(select(db.meta_goals.c.id).where(
            db.meta_goals.c.status.in_(FINAL))).scalars().all()
    released, pending = [], []
    for goal_id in goals:
        core._finalize_empty_piece_links(int(goal_id))
        last = next((row for row in reversed(core.ledger(int(goal_id)))
            if row.get("event_type") == "META_FINAL_PIECES_CLEANUP"), None)
        details = (last or {}).get("details") or {}
        released.extend((int(goal_id), int(grid_id)) for grid_id in details.get("released_grid_ids", []))
        pending.extend({"meta_id": int(goal_id), **row} for row in details.get("pending", []))
    return {"released": released, "pending": pending}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="ruta a la BD de la aplicación")
    parser.add_argument("--confirmar", action="store_true", help="verifica en Testnet, cierra y libera piezas vacías")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"no existe la BD: {args.db}")
    if args.confirmar:
        from data.proxy_config import proxy_label, resolve_binance_proxy
        proxy, proxy_source = resolve_binance_proxy()
        print(proxy_label(proxy, proxy_source))
        try:
            result = reparar(args.db)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            lowered = detail.lower()
            if any(token in lowered for token in ("proxy", "connection", "connect", "timeout",
                    "timed out", "tunnel", "restricted location")):
                print(f"Error: no se pudo conectar con Binance (¿túnel caído?): {detail}")
            else:
                print(f"Error: reparación no completada: {detail}")
            return 2
        print(f"Enlaces liberados: {len(result['released'])}")
        for meta_id, grid_id in result["released"]:
            print(f"  Meta #{meta_id} · grid #{grid_id}")
        print(f"Pendientes: {len(result['pending'])}")
        for row in result["pending"]:
            print(f"  Meta #{row['meta_id']} · grid #{row['grid_id']}: {row['reason']}")
        return 1 if result["pending"] else 0
    else:
        rows = listar(args.db)
        if not rows:
            print("No hay enlaces sin liberar de metas finalizadas.")
        for row in rows:
            marca = "candidato local (requiere verificación Testnet)" if row["eligible_local"] else "se conserva: inventario/orden local"
            print(f"Meta #{row['meta_id']} {row['meta_status']} · grid #{row['grid_id']} "
                f"{row['symbol']} {row['status']} · {marca}")
        print("Modo lectura: no se escribió en la BD ni se consultó Testnet.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
