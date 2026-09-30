"""Read-only adapter for the latest persisted 24-hour volatility forecast."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, isfinite, sqrt
from typing import Any, Callable

from config.models_config import VOL_CHAMPIONS, VOL_SYMBOL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolView:
    sigma_24h: float
    stale: bool
    regime: str | None
    as_of: datetime


class VolatilityProvider:
    """Expose the same calibrated 24h sigma used by the forecast API, without HTTP."""

    def __init__(
        self,
        db_manager: Any,
        *,
        manifest: dict | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db = db_manager
        self.manifest = manifest or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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
            self.last_reason = "unsupported_symbol"
            return None
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
            return VolView(sigma_24h=sigma_24h, stale=False, regime=regime, as_of=as_of)
        except Exception:
            self.last_reason = "exception"
            logger.exception("volatility lookup failed for %s", normalized)
            return None
