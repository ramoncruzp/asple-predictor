"""Read-only adapter for per-symbol, per-horizon volatility forecasts."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import exp, isfinite, sqrt, log
from typing import Any, Callable

from config.models_config import VOL_CHAMPIONS, VOL_SYMBOL, vol_champions, vol_manifest_path
from models.coin_onboarding import coin_is_ready

logger = logging.getLogger(__name__)
SUPPORTED_HORIZONS = frozenset({1, 2, 4, 24})
_MANIFEST_CACHE: dict[str, tuple[tuple[int, int] | None, dict]] = {}
_MANIFEST_CACHE_LOCK = threading.Lock()


def _load_symbol_manifest(symbol: str) -> dict:
    path = vol_manifest_path(symbol)
    with _MANIFEST_CACHE_LOCK:
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        cached = _MANIFEST_CACHE.get(symbol)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _MANIFEST_CACHE[symbol] = (signature, payload)
        return payload


@dataclass(frozen=True)
class VolView:
    sigma_24h: float
    stale: bool
    regime: str | None
    as_of: datetime
    source: str = "model"
    horizon_h: int = 24
    sigma_h: float | None = None
    fallback: bool = False
    fallback_reason: str | None = None


class VolatilityProvider:
    """Expose calibrated hourly volatility as compatible 24h and requested-horizon sigma."""

    def __init__(self, db_manager: Any, *, manifest: dict | None = None,
                 clock: Callable[[], datetime] | None = None, data_client: Any = None,
                 registry: Any = None):
        self.db = db_manager
        self.manifest = manifest or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.data_client = data_client
        self.registry = registry
        self._realized_cache: dict[str, tuple[datetime, VolView | None]] = {}
        self._realized_lock = threading.Lock()
        self.last_reason: str | None = None

    @staticmethod
    def _utc(value: Any) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("forecast_at must be a datetime or ISO string")
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def _manifest_for(self, symbol: str) -> dict:
        if symbol == VOL_SYMBOL:
            return self.manifest
        return _load_symbol_manifest(symbol)

    def _champion_row(self, rows: list[dict], symbol: str, horizon_h: int) -> dict | None:
        champions = VOL_CHAMPIONS if symbol == VOL_SYMBOL else vol_champions(symbol)[0]
        champion_name = champions.get(horizon_h)
        return next((row for row in rows if (
            int(row.get("horizon_h", 0)) == horizon_h
            and str(row.get("symbol", symbol)).strip().upper().replace("/", "") == symbol
            and row.get("model_name") == champion_name
            and bool(row.get("is_champion"))
        )), None)

    def _model_view(self, row: dict, symbol: str, requested_h: int, *,
                    fallback: bool = False, fallback_reason: str | None = None) -> VolView | None:
        as_of = self._utc(row["forecast_at"])
        if self._utc(self.clock()) - as_of > timedelta(hours=2):
            return None
        hourly_sigma = exp(float(row["pred_logvol_cal"]))
        if not isfinite(hourly_sigma) or hourly_sigma <= 0:
            return None
        manifest = self._manifest_for(symbol)
        percentiles = manifest.get("regime_percentiles_24h", {})
        p33, p66 = percentiles.get("p33"), percentiles.get("p66")
        regime = None
        if p33 is not None and p66 is not None:
            regime = "CALMA" if hourly_sigma < float(p33) else "NORMAL" if hourly_sigma < float(p66) else "AGITADO"
        return VolView(sigma_24h=hourly_sigma * sqrt(24.0), stale=False, regime=regime,
                       as_of=as_of, source="model", horizon_h=requested_h,
                       sigma_h=hourly_sigma * sqrt(requested_h), fallback=fallback,
                       fallback_reason=fallback_reason)

    def get(self, symbol: str, horizon_h: int = 24) -> VolView | None:
        self.last_reason = None
        normalized = str(symbol).strip().upper().replace("/", "")
        requested_h = int(horizon_h)
        if requested_h not in SUPPORTED_HORIZONS:
            requested_h = 24
        if normalized != VOL_SYMBOL and not coin_is_ready(self.db, self.registry, normalized):
            return self._realized_for_horizon(normalized, requested_h)
        try:
            rows = self.db.get_latest_vol_forecasts(normalized)
            row = self._champion_row(rows, normalized, requested_h)
            if row is not None:
                view = self._model_view(row, normalized, requested_h)
                if view is not None:
                    return view
                target_reason = "stale" if self._utc(self.clock()) - self._utc(row["forecast_at"]) > timedelta(hours=2) else "invalid_sigma"
            else:
                target_reason = "forecast_unavailable"
            if requested_h < 24:
                fallback_row = self._champion_row(rows, normalized, 24)
                if fallback_row is not None:
                    fallback_view = self._model_view(fallback_row, normalized, requested_h,
                                                     fallback=True, fallback_reason=target_reason)
                    if fallback_view is not None:
                        return fallback_view
                    target_reason = "stale" if self._utc(self.clock()) - self._utc(fallback_row["forecast_at"]) > timedelta(hours=2) else "invalid_sigma"
            if normalized != VOL_SYMBOL:
                realized = self._realized_for_horizon(normalized, requested_h)
                if realized is not None:
                    return realized
                target_reason = self.last_reason or target_reason
            self.last_reason = target_reason
            return None
        except Exception:
            self.last_reason = "exception"
            logger.exception("volatility lookup failed for %s", normalized)
            if normalized != VOL_SYMBOL:
                return self._realized_for_horizon(normalized, requested_h)
            return None

    def _realized_for_horizon(self, symbol: str, horizon_h: int) -> VolView | None:
        view = self._realized(symbol)
        if view is None or horizon_h == 24:
            return view
        return replace(view, horizon_h=horizon_h,
                       sigma_h=view.sigma_24h * sqrt(horizon_h / 24.0))

    def _realized(self, symbol: str) -> VolView | None:
        with self._realized_lock:
            now = self._utc(self.clock())
            cached = self._realized_cache.get(symbol)
            ttl = timedelta(minutes=30) if cached and cached[1] is not None else timedelta(seconds=90)
            if cached and now - cached[0] < ttl:
                self.last_reason = None if cached[1] is not None else "insufficient_or_unavailable_data"
                return cached[1]
            try:
                if self.data_client is None:
                    raise RuntimeError("data_client_unavailable")
                frame = self.data_client.get_historical_klines(symbol, "1h", lookback_days=7)
                closes = [float(value) for value in frame["close"].tolist()]
                returns = [log(current / previous) for previous, current in zip(closes, closes[1:])
                           if previous > 0 and current > 0]
                if len(returns) < 99:
                    self.last_reason = "insufficient_bars"
                    self._realized_cache[symbol] = (now, None)
                    return None
                mean = sum(returns) / len(returns)
                variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
                ewma = 0.0
                for value in returns:
                    ewma = 0.94 * ewma + 0.06 * value * value
                sigma_h = max(sqrt(ewma), sqrt(variance))
                if not isfinite(sigma_h) or sigma_h <= 0:
                    raise ValueError("invalid_realized_sigma")
                view = VolView(sigma_24h=sigma_h * sqrt(24), stale=False, regime=None,
                               as_of=now, source="realized", horizon_h=24, sigma_h=sigma_h * sqrt(24))
                self._realized_cache[symbol] = (now, view)
                self.last_reason = None
                return view
            except Exception as exc:
                self.last_reason = str(exc) or "realized_volatility_unavailable"
                self._realized_cache[symbol] = (now, None)
                return None
