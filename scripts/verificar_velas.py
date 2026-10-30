"""Verifica cobertura del almacén de mercado en modo de solo lectura."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.binance_client import BinanceClient


def inspect_db(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        symbols = conn.execute("SELECT id,symbol,role FROM symbols ORDER BY symbol").fetchall()
        reports = []
        now = int(datetime.now(timezone.utc).timestamp())
        for symbol in symbols:
            candles = conn.execute("SELECT open_ts FROM candles WHERE symbol_id=? AND interval='5m' ORDER BY open_ts",
                (symbol["id"],)).fetchall()
            stamps = [int(row[0]) for row in candles]
            gaps = [max(0, (right-left)//300-1) for left, right in zip(stamps, stamps[1:])]
            expected = max(0, (min(now, stamps[-1] if stamps else now) - stamps[0] + 300)//300) if stamps else 0
            state = conn.execute("SELECT last_error FROM sync_state WHERE symbol_id=? AND interval='5m'",
                (symbol["id"],)).fetchone()
            reports.append({"symbol": symbol["symbol"], "role": symbol["role"],
                "first_ts": stamps[0] if stamps else None, "last_ts": stamps[-1] if stamps else None,
                "candles": len(stamps), "coverage_pct": 100*len(stamps)/expected if expected else 0,
                "gaps": sum(gaps), "largest_gap": max(gaps, default=0),
                "age_minutes": (now-stamps[-1])/60 if stamps else None,
                "last_error": state[0] if state else None})
        return reports
    finally:
        conn.close()


def compare_api(path, days, client=None):
    client = client or BinanceClient("", "")
    now = datetime.now(timezone.utc)
    last_closed = int(now.timestamp())//300*300-300
    start = last_closed-days*86400+300
    conn = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    differences = {}
    try:
        symbols = conn.execute("SELECT id,symbol FROM symbols WHERE active=1 ORDER BY symbol").fetchall()
        for item in symbols:
            state = conn.execute("SELECT last_closed_ts FROM sync_state WHERE symbol_id=? AND interval='5m'",
                (item["id"],)).fetchone()
            stored_last = (int(state[0]) if state and state[0] is not None else conn.execute(
                "SELECT MAX(open_ts) FROM candles WHERE symbol_id=? AND interval='5m'", (item["id"],)).fetchone()[0])
            if stored_last is None:
                differences[item["symbol"]] = {"differences": 0, "pending": 0}
                continue
            stored_last = int(stored_last)
            rows = conn.execute("SELECT open_ts,open,high,low,close,volume,quote_volume,trades FROM candles "
                "WHERE symbol_id=? AND interval='5m' AND open_ts BETWEEN ? AND ? ORDER BY open_ts",
                (item["id"], start, stored_last)).fetchall()
            local = {int(r[0]): tuple(r[1:]) for r in rows}
            frame = client.get_historical_klines(item["symbol"], "5m", days,
                start_time=datetime.fromtimestamp(start, timezone.utc))
            remote = {}
            for row in frame.to_dict("records"):
                stamp = row["timestamp"]
                stamp = int(stamp.timestamp()) if hasattr(stamp, "timestamp") else int(stamp)
                if stamp > last_closed:
                    continue
                values = tuple(row[k] for k in ("open", "high", "low", "close", "volume", "quote_volume", "trades"))
                remote[stamp] = values
            mismatch_count = sum(
                1 for stamp in local.keys() | remote.keys()
                if stamp <= stored_last and (stamp not in local or stamp not in remote or
                any(float(a) != float(b) for a, b in zip(local[stamp], remote[stamp])))
            )
            pending = sum(1 for stamp in remote if stored_last < stamp <= last_closed)
            differences[item["symbol"]] = {"differences": mismatch_count, "pending": pending}
    finally:
        conn.close()
    return differences


def main(argv=None, *, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--comparar-api", type=int)
    args = parser.parse_args(argv)
    for row in inspect_db(args.db):
        print("VELAS " + " ".join(f"{key}={value}" for key,value in row.items()))
    print(f"TAMAÑO archivo_bytes={Path(args.db).stat().st_size}")
    if args.comparar_api:
        for symbol, counts in compare_api(args.db, args.comparar_api, client).items():
            print(f"COMPARACION_API símbolo={symbol} diferencias={counts['differences']} posteriores_a_ultima_guardada={counts['pending']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
