"""Validated OHLCV loading and causal realized-volatility estimates."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class CandleDataError(ValueError):
    pass


@dataclass(frozen=True)
class CandleData:
    timestamp: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    gaps: int


def load_candles(path, start=None, end=None, allow_gaps=False) -> CandleData:
    path = Path(path)
    required = {"timestamp", "open", "high", "low", "close"}
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise CandleDataError("CSV requires timestamp, open, high, low and close columns")
        try:
            for row in reader:
                stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                ts = int(stamp.timestamp())
                values = [float(row[key]) for key in ("open", "high", "low", "close")]
                if not np.isfinite(values).all() or min(values) <= 0:
                    raise CandleDataError("prices must be finite and positive")
                if values[2] > min(values[0], values[3]) or values[1] < max(values[0], values[3]) or values[2] > values[1]:
                    raise CandleDataError("invalid OHLC relationship")
                rows.append((ts, *values))
        except (ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, CandleDataError):
                raise
            raise CandleDataError(f"invalid candle row: {exc}") from exc
    if len(rows) < 2:
        raise CandleDataError("at least two candles are required")
    times = np.asarray([r[0] for r in rows], dtype=np.int64)
    if np.any(np.diff(times) <= 0):
        raise CandleDataError("timestamps must be strictly increasing and unique")
    gap_sizes = np.diff(times) // 300
    if np.any(np.diff(times) % 300):
        raise CandleDataError("timestamps must align to five-minute intervals")
    begin = _timestamp(start) if start else times[0]
    finish = _timestamp(end) if end else times[-1]
    keep = (times >= begin) & (times <= finish)
    if keep.sum() < 2:
        raise CandleDataError("requested window contains fewer than two candles")
    chosen = np.flatnonzero(keep)
    inner_gaps = np.maximum(gap_sizes[chosen[:-1]] - 1, 0)
    gaps = int(inner_gaps.sum())
    if gaps and gaps / (int(keep.sum()) + gaps) > .005 and not allow_gaps:
        raise CandleDataError(f"window contains {gaps} missing candles (>0.5%); pass --allow-gaps")
    selected = np.asarray(rows, dtype=float)[keep]
    return CandleData(selected[:, 0].astype(np.int64), *(selected[:, i] for i in range(1, 5)), gaps)


def _timestamp(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int((stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).timestamp())


def ewma_sigma_24h(closes, halflife_h=72.0):
    prices = np.asarray(closes, dtype=float)
    if len(prices) < 2 or not np.isfinite(prices).all() or np.any(prices <= 0) or halflife_h <= 0:
        raise CandleDataError("closes must be positive finite prices and halflife_h positive")
    returns = np.diff(np.log(prices), prepend=np.log(prices[0]))
    alpha = 1.0 - np.exp(np.log(.5) / (halflife_h * 12.0))
    variance = np.zeros(len(prices), dtype=float)
    for i in range(1, len(prices)):
        variance[i] = (1 - alpha) * variance[i - 1] + alpha * returns[i] ** 2
    return np.sqrt(variance * 288.0)
