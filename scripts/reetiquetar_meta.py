"""Vista previa y marcado de etiquetas Meta obsoletas; nunca recalcula etiquetas."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GENERIC_TICK = 0.0001
G1G1_AT = "2026-10-28 09:45:46"


def _connect(path: Path, *, write=False):
    absolute = str(path.resolve()).replace("\\", "/")
    suffix = "" if write else "?mode=ro"
    conn = sqlite3.connect(f"file:{quote(absolute, safe='/:')}" + suffix, uri=True)
    conn.row_factory = sqlite3.Row
    if not write:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _backup_exists(today=None, home=None):
    today = today or date.today()
    root = Path(home or Path.home()) / "respaldos"
    stamps = {today.isoformat(), today.strftime("%Y%m%d")}
    return root.is_dir() and any(path.is_file() and any(stamp in path.name for stamp in stamps)
        for path in root.iterdir())


def _tick_from_json(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"tick_size", "ticksize", "price_tick"}:
                try:
                    return float(item)
                except (TypeError, ValueError):
                    pass
            found = _tick_from_json(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _tick_from_json(item)
            if found is not None:
                return found
    return None


def _stored_ticks(conn):
    result = {}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta_learning_samples" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(meta_learning_samples)")}
        if "symbol" in columns:
            for row in conn.execute("SELECT symbol,provenance_json,parameters_json FROM meta_learning_samples"):
                for raw in row[1:]:
                    try: tick = _tick_from_json(json.loads(raw or "{}"))
                    except (TypeError, json.JSONDecodeError): tick = None
                    if tick is not None: result.setdefault(str(row[0]).upper(), tick)
    return result


def _exchange_ticks(symbols, provider=None):
    if provider is None:
        from data.binance_client import BinanceClient
        client = BinanceClient(None, None)  # Cliente público; no carga configuración ni claves.
        provider = client.client.get_symbol_info
    ticks = {}
    for symbol in symbols:
        info = provider(symbol)
        if not info:
            continue
        for item in info.get("filters", []):
            if item.get("filterType") == "PRICE_FILTER":
                try: ticks[symbol] = float(item["tickSize"])
                except (KeyError, TypeError, ValueError): pass
                break
    return ticks


def inspect(path: Path, *, exchange_info=False, provider=None):
    with _connect(path) as conn:
        rows = conn.execute("""SELECT s.symbol,COUNT(*) AS n
            FROM meta_learning_samples s JOIN meta_learning_labels l ON l.sample_id=s.id
            WHERE datetime(l.labeled_at) < datetime(?) AND
              COALESCE(json_extract(l.label_json,'$.status'),'labeled') != 'superseded'
            GROUP BY s.symbol ORDER BY s.symbol""", (G1G1_AT,)).fetchall()
        stored = _stored_ticks(conn)
    symbols = [str(row["symbol"]).upper() for row in rows]
    fetched = _exchange_ticks([s for s in symbols if s not in stored], provider) if exchange_info else {}
    result = []
    for row in rows:
        symbol = str(row["symbol"]).upper()
        tick = stored.get(symbol, fetched.get(symbol))
        result.append({"symbol": symbol, "labels_before_g1g1": int(row["n"]),
            "tick_size": tick, "tick_status": "desconocido" if tick is None else
                "difiere" if abs(float(tick) - GENERIC_TICK) > 1e-15 else "igual",
            "affected": tick is not None and abs(float(tick) - GENERIC_TICK) > 1e-15})
    return result


def apply(path: Path, rows, *, backup_ok=False, backup_check=_backup_exists):
    if not backup_ok and not backup_check():
        raise RuntimeError("falta respaldo de hoy en ~/respaldos/; usa --backup-ok tras verificarlo")
    symbols = [row["symbol"] for row in rows if row["affected"]]
    if not symbols:
        return 0
    conn = _connect(path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = 0
        for symbol in symbols:
            records = conn.execute("""SELECT l.id,l.label_json FROM meta_learning_labels l
                JOIN meta_learning_samples s ON s.id=l.sample_id
                WHERE s.symbol=? AND datetime(l.labeled_at)<datetime(?)""", (symbol, G1G1_AT)).fetchall()
            for record in records:
                payload = json.loads(record["label_json"] or "{}")
                if payload.get("status") == "superseded":
                    continue
                payload["superseded_previous_label"] = payload.copy()
                payload["status"] = "superseded"
                payload["superseded_reason"] = "etiqueta anterior a G1g1 con tick genérico potencialmente incorrecto"
                conn.execute("UPDATE meta_learning_labels SET label_json=? WHERE id=?",
                    (json.dumps(payload, sort_keys=True), int(record["id"])))
                count += 1
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="vista previa; comportamiento predeterminado")
    parser.add_argument("--apply", action="store_true", help="marca etiquetas afectadas como superseded")
    parser.add_argument("--backup-ok", action="store_true")
    parser.add_argument("--exchange-info", action="store_true", help="consulta filtros públicos de Binance para ticks desconocidos")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("elige --apply o --dry-run, no ambos")
    before = _hash(args.db)
    rows = inspect(args.db, exchange_info=args.exchange_info)
    print("símbolo | etiquetas anteriores a G1g1 | tick | estado")
    for row in rows:
        print(f"{row['symbol']} | {row['labels_before_g1g1']} | {row['tick_size'] or 'desconocido'} | {row['tick_status']}")
    if args.apply:
        count = apply(args.db, rows, backup_ok=args.backup_ok)
        print(f"marcadas superseded: {count}")
    else:
        print("vista previa; no se modificó la base")
        if before != _hash(args.db):
            raise RuntimeError("la vista previa modificó inesperadamente la base")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
