"""Download closed Binance candles to a local CSV."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_client import BinanceClient


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--interval", default="5m", choices=sorted(BinanceClient.VALID_INTERVALS))
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--out", type=Path, default=Path("data/cache/xrp_5m.csv"))
    return parser.parse_args(argv)


def download_closed_candles(symbol: str, interval: str, days: int, out: Path, *, client=None) -> pd.DataFrame:
    """Download closed candles to a sibling temporary file, then atomically publish it."""
    out = Path(out)
    temporary = out.with_name(out.name + ".tmp")
    downloader = client or BinanceClient("", "")
    try:
        candles = downloader.get_historical_klines(symbol, interval, lookback_days=days)
        if "close_time" in candles:
            now = pd.Timestamp(datetime.now(timezone.utc))
            candles = candles.loc[pd.to_datetime(candles["close_time"], utc=True) <= now].copy()
        out.parent.mkdir(parents=True, exist_ok=True)
        candles.to_csv(temporary, index=False)
        os.replace(temporary, out)
        return candles
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    args = parse_args(argv)
    candles = download_closed_candles(args.symbol, args.interval, args.days, args.out)
    print(f"Guardadas {len(candles)} velas cerradas en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
