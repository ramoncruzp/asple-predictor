"""Almacén SQLite separado para velas públicas de mercado."""
from __future__ import annotations

import math
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from grid.sim.data import CandleData


_INTERVAL_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400}
_ROOT = Path(__file__).resolve().parents[1]


def market_db_path(path=None) -> Path:
    value = path or os.environ.get("MARKET_DATA_DB") or (_ROOT / "market_data.db")
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class HourlyCandleData:
    timestamp: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    trades: np.ndarray
    gaps: int


class CandleStore:
    def __init__(self, path=None):
        self.path = market_db_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS symbols(
                    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL CHECK(role IN ('operar','datos')),
                    active INTEGER NOT NULL, added_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS candles(
                    symbol_id INTEGER NOT NULL REFERENCES symbols(id), interval TEXT NOT NULL,
                    open_ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
                    low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                    quote_volume REAL NOT NULL, trades INTEGER NOT NULL,
                    PRIMARY KEY(symbol_id, interval, open_ts)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS sync_state(
                    symbol_id INTEGER NOT NULL REFERENCES symbols(id), interval TEXT NOT NULL,
                    first_ts INTEGER, last_closed_ts INTEGER, last_sync_at TEXT,
                    last_source TEXT, last_error TEXT,
                    PRIMARY KEY(symbol_id, interval));
                CREATE TABLE IF NOT EXISTS gaps(
                    symbol_id INTEGER NOT NULL REFERENCES symbols(id), interval TEXT NOT NULL,
                    from_ts INTEGER NOT NULL, to_ts INTEGER NOT NULL,
                    detected_at TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_market_gap
                    ON gaps(symbol_id, interval, from_ts, to_ts);
                CREATE INDEX IF NOT EXISTS ix_market_candles_ts
                    ON candles(symbol_id, interval, open_ts);
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _step(interval):
        try:
            return _INTERVAL_SECONDS[str(interval)]
        except KeyError as exc:
            raise ValueError(f"Intervalo no soportado: {interval}") from exc

    @staticmethod
    def _timestamp(value):
        if isinstance(value, datetime):
            stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return int(stamp.timestamp())
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            return int(stamp.timestamp())
        return int(value)

    def register_symbol(self, symbol, role="operar", active=True):
        symbol = str(symbol).strip().upper().replace("/", "")
        role = str(role).strip().lower()
        if not symbol or role not in {"operar", "datos"}:
            raise ValueError("símbolo o papel inválido")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT INTO symbols(symbol,role,active,added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET role=CASE WHEN excluded.role='operar' "
                "THEN 'operar' ELSE symbols.role END,active=excluded.active",
                (symbol, role, int(bool(active)), now))
            row = conn.execute("SELECT id FROM symbols WHERE symbol=?", (symbol,)).fetchone()
        return int(row[0])

    @staticmethod
    def _row_values(row):
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        if isinstance(row, dict):
            timestamp = row.get("open_ts", row.get("timestamp", row.get("open_time")))
            values = (timestamp, row.get("open"), row.get("high"), row.get("low"), row.get("close"),
                row.get("volume", 0), row.get("quote_volume", row.get("quote_asset_volume", 0)),
                row.get("trades", row.get("number_of_trades", 0)))
        else:
            values = (row[0], row[1], row[2], row[3], row[4], row[5], row[7], row[8])
        stamp = CandleStore._timestamp(values[0])
        numeric = [float(value) for value in values[1:7]]
        trades = int(values[7])
        return (stamp, *numeric, trades)

    @classmethod
    def validate_rows(cls, interval, rows):
        step = cls._step(interval)
        accepted, rejected = [], 0
        for row in rows:
            try:
                stamp, opn, high, low, close, volume, quote_volume, trades = cls._row_values(row)
                if stamp % step or not all(math.isfinite(value) for value in
                        (opn, high, low, close, volume, quote_volume)):
                    raise ValueError("timestamp desalineado o valor no finito")
                if min(opn, high, low, close) <= 0 or volume < 0 or quote_volume < 0 or trades < 0:
                    raise ValueError("precio, volumen o trades fuera de rango")
                if low > min(opn, close) or high < max(opn, close) or low > high:
                    raise ValueError("relación OHLC inválida")
                accepted.append((stamp, opn, high, low, close, volume, quote_volume, trades))
            except (IndexError, KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
        return accepted, rejected

    def upsert(self, symbol, interval, rows, source, *, role="operar", active=True):
        step = self._step(interval)
        symbol_id = self.register_symbol(symbol, role=role, active=active)
        valid_rows, rejected = self.validate_rows(interval, rows)
        accepted = [(symbol_id, str(interval), *row) for row in valid_rows]
        now = datetime.now(timezone.utc).isoformat()
        gaps_detected = 0
        with self._connect() as conn:
            if accepted:
                conn.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)", accepted)
                begin, finish = min(row[2] for row in accepted), max(row[2] for row in accepted)
                nearby = conn.execute("SELECT open_ts FROM candles WHERE symbol_id=? AND interval=? "
                    "AND open_ts BETWEEN ? AND ? ORDER BY open_ts", (symbol_id, str(interval),
                    begin - step, finish + step)).fetchall()
                stamps = [int(row[0]) for row in nearby]
                for previous, current in zip(stamps, stamps[1:]):
                    if current - previous <= step:
                        continue
                    gap_from, gap_to = previous + step, current - step
                    cursor = conn.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?,?,?)",
                        (symbol_id, str(interval), gap_from, gap_to, now,
                         f"faltan velas entre {previous} y {current}"))
                    gaps_detected += cursor.rowcount
            bounds = conn.execute("SELECT MIN(open_ts),MAX(open_ts) FROM candles "
                "WHERE symbol_id=? AND interval=?", (symbol_id, str(interval))).fetchone()
            if bounds[0] is not None:
                conn.execute("INSERT INTO sync_state(symbol_id,interval,first_ts,last_closed_ts,last_sync_at,last_source,last_error) "
                    "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(symbol_id,interval) DO UPDATE SET "
                    "first_ts=excluded.first_ts,last_closed_ts=excluded.last_closed_ts,last_sync_at=excluded.last_sync_at,"
                    "last_source=excluded.last_source,last_error=NULL", (symbol_id, str(interval),
                    int(bounds[0]), int(bounds[1]), now, str(source)))
        return {"symbol": str(symbol).upper().replace("/", ""), "interval": str(interval),
            "accepted": len(accepted), "rejected": rejected, "gaps_detected": gaps_detected}

    def set_sync_error(self, symbol, interval, error):
        with self._connect() as conn:
            symbol_id_existing = self._symbol_id(conn, symbol)
            role = (conn.execute("SELECT role FROM symbols WHERE id=?", (symbol_id_existing,)).fetchone()[0]
                if symbol_id_existing is not None else "operar")
        symbol_id = self.register_symbol(symbol, role=role)
        with self._connect() as conn:
            conn.execute("INSERT INTO sync_state(symbol_id,interval,last_sync_at,last_error) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol_id,interval) DO UPDATE SET last_sync_at=excluded.last_sync_at,last_error=excluded.last_error",
                (symbol_id, str(interval), datetime.now(timezone.utc).isoformat(), str(error)))

    def _symbol_id(self, conn, symbol):
        key = str(symbol).strip().upper().replace("/", "")
        row = conn.execute("SELECT id FROM symbols WHERE symbol=?", (key,)).fetchone()
        return None if row is None else int(row[0])

    @staticmethod
    def _gap_stats(timestamps, step):
        missing = [max(0, (right - left) // step - 1)
            for left, right in zip(timestamps, timestamps[1:])]
        return {"gaps": int(sum(missing)), "gap_ranges": sum(value > 0 for value in missing),
            "largest_gap": max(missing, default=0)}

    def _gap_stats_for_id(self, symbol_id, interval):
        with self._connect() as conn:
            rows = conn.execute("SELECT open_ts FROM candles WHERE symbol_id=? AND interval=? ORDER BY open_ts",
                (symbol_id, str(interval))).fetchall()
        return self._gap_stats([int(row[0]) for row in rows], self._step(interval))

    def load(self, symbol, interval="5m", start_ts=None, end_ts=None):
        step = self._step(interval)
        clauses, params = ["symbol_id=?", "interval=?"], []
        with self._connect() as conn:
            symbol_id = self._symbol_id(conn, symbol)
            if symbol_id is None:
                return CandleData(*(np.asarray([], dtype=np.int64 if index == 0 else float)
                    for index in range(5)), 0)
            params.extend((symbol_id, str(interval)))
            if start_ts is not None:
                clauses.append("open_ts>=?"); params.append(self._timestamp(start_ts))
            if end_ts is not None:
                clauses.append("open_ts<=?"); params.append(self._timestamp(end_ts))
            rows = conn.execute("SELECT open_ts,open,high,low,close FROM candles WHERE " +
                " AND ".join(clauses) + " ORDER BY open_ts", params).fetchall()
        stamps = np.asarray([int(row[0]) for row in rows], dtype=np.int64)
        gaps = self._gap_stats(stamps.tolist(), step)["gaps"]
        arrays = [np.asarray([row[index] for row in rows], dtype=float) for index in range(1, 5)]
        return CandleData(stamps, *arrays, gaps)

    def load_hourly(self, symbol, start_ts=None, end_ts=None):
        data = self.load(symbol, "5m", start_ts, end_ts)
        if not len(data.timestamp):
            empty = np.asarray([], dtype=float)
            return HourlyCandleData(np.asarray([], dtype=np.int64), empty, empty, empty, empty,
                empty, empty, np.asarray([], dtype=np.int64), 0)
        volume_rows = self._load_volume_rows(symbol, start_ts, end_ts)
        grouped = {}
        for row in volume_rows:
            hour = int(row[0]) // 3600 * 3600
            grouped.setdefault(hour, []).append(row)
        output = []
        for hour, bars in sorted(grouped.items()):
            bars.sort(key=lambda row: row[0])
            if len(bars) != 12 or any(int(row[0]) != hour + index * 300 for index, row in enumerate(bars)):
                continue
            output.append((hour, bars[0][1], max(row[2] for row in bars), min(row[3] for row in bars),
                bars[-1][4], sum(row[5] for row in bars), sum(row[6] for row in bars),
                sum(int(row[7]) for row in bars)))
        stamps = [row[0] for row in output]
        gap_stats = self._gap_stats(stamps, 3600)
        return HourlyCandleData(np.asarray(stamps, dtype=np.int64),
            *(np.asarray([row[index] for row in output], dtype=float) for index in range(1, 7)),
            np.asarray([row[7] for row in output], dtype=np.int64), gap_stats["gaps"])

    def _load_volume_rows(self, symbol, start_ts, end_ts):
        with self._connect() as conn:
            symbol_id = self._symbol_id(conn, symbol)
            if symbol_id is None:
                return []
            clauses, params = ["symbol_id=?", "interval='5m'"], [symbol_id]
            if start_ts is not None:
                clauses.append("open_ts>=?"); params.append(self._timestamp(start_ts))
            if end_ts is not None:
                clauses.append("open_ts<=?"); params.append(self._timestamp(end_ts))
            return [tuple(row) for row in conn.execute("SELECT open_ts,open,high,low,close,volume,quote_volume,trades "
                "FROM candles WHERE " + " AND ".join(clauses) + " ORDER BY open_ts", params)]

    def coverage(self, symbol, interval="5m"):
        step = self._step(interval)
        with self._connect() as conn:
            symbol_id = self._symbol_id(conn, symbol)
            if symbol_id is None:
                return {"symbol": str(symbol).upper().replace("/", ""), "role": None,
                    "first_ts": None, "last_ts": None, "candles": 0, "gaps": 0,
                    "first_candle": None, "last_candle": None,
                    "gap_ranges": 0, "largest_gap": 0, "age_minutes": None, "sync_state": None}
            symbol_row = conn.execute("SELECT symbol,role FROM symbols WHERE id=?", (symbol_id,)).fetchone()
            rows = conn.execute("SELECT open_ts,open,high,low,close,volume,quote_volume,trades FROM candles "
                "WHERE symbol_id=? AND interval=? ORDER BY open_ts",
                (symbol_id, str(interval))).fetchall()
            state = conn.execute("SELECT first_ts,last_closed_ts,last_sync_at,last_source,last_error FROM sync_state "
                "WHERE symbol_id=? AND interval=?", (symbol_id, str(interval))).fetchone()
        stamps = [int(row[0]) for row in rows]
        stats = self._gap_stats(stamps, step)
        now = int(datetime.now(timezone.utc).timestamp())
        def candle_dict(row):
            if row is None:
                return None
            return {"open_ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
                "quote_volume": float(row[6]), "trades": int(row[7])}
        return {"symbol": symbol_row["symbol"], "role": symbol_row["role"],
            "first_ts": stamps[0] if stamps else None, "last_ts": stamps[-1] if stamps else None,
            "first_candle": candle_dict(rows[0] if rows else None),
            "last_candle": candle_dict(rows[-1] if rows else None),
            "candles": len(stamps), **stats,
            "age_minutes": (max(0.0, (now - stamps[-1]) / 60) if stamps else None),
            "sync_state": dict(state) if state else None}
