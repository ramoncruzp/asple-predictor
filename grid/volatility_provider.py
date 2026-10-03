"""Read-only adapter for the latest persisted 24-hour volatility forecast."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, isfinite, sqrt, log
from typing import Any, Callable

from config.models_config import VOL_CHAMPIONS, VOL_SYMBOL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolView:
    sigma_24h: float
    stale: bool
    regime: str | None
    as_of: datetime
    source: str = "model"


class VolatilityProvider:
    """Expose the same calibrated 24h sigma used by the forecast API, without HTTP."""

    def __init__(
        self,
        db_manager: Any,
        *,
        manifest: dict | None = None,
        clock: Callable[[], datetime] | None = None,
        data_client: Any = None,
    ):
        self.db = db_manager
        self.manifest = manifest or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.data_client = data_client
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

    def get(self, symbol: str) -> VolView | None:
        self.last_reason = None
        normalized = str(symbol).strip().upper().replace("/", "")
        if normalized != VOL_SYMBOL:
            return self._realized(normalized)
        try:
            rows = self.db.get_latest_vol_forecasts(normalized)
            champion_name = VOL_CHAMPIONS[24]
            row = next((item for item in rows if (
                int(item.get("horizon_h", 0)) == 24
                and item.get("model_name") == champion_name
                and bool(item.get("is_champion"))
            )), None)
            if row is None:
                self.last_reason = "forecast_unavailable"
                return None
            as_of = self._utc(row["forecast_at"])
            now = self._utc(self.clock())
            if now - as_of > timedelta(hours=2):
                self.last_reason = "stale"
                return None
            hourly_sigma = exp(float(row["pred_logvol_cal"]))
            if not isfinite(hourly_sigma) or hourly_sigma <= 0:
                self.last_reason = "invalid_sigma"
                return None
            sigma_24h = hourly_sigma * sqrt(24.0)
            percentiles = self.manifest.get("regime_percentiles_24h", {})
            p33, p66 = percentiles.get("p33"), percentiles.get("p66")
            regime = None
            if p33 is not None and p66 is not None:
                regime = (
                    "CALMA" if hourly_sigma < float(p33)
                    else "NORMAL" if hourly_sigma < float(p66)
                    else "AGITADO"
                )
            return VolView(sigma_24h=sigma_24h, stale=False, regime=regime, as_of=as_of,
                           source="model")
        except Exception:
            self.last_reason = "exception"
            logger.exception("volatility lookup failed for %s", normalized)
            return None

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
                               as_of=now, source="realized")
                self._realized_cache[symbol] = (now, view)
                self.last_reason = None
                return view
            except Exception as exc:
                self.last_reason = str(exc) or "realized_volatility_unavailable"
                self._realized_cache[symbol] = (now, None)
                return None
