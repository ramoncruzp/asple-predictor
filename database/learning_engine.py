"""Verification and condition analysis for prediction outcomes."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import text


def _as_utc(value):
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def price_at(verify_at, candles):
    """Interpolate price at verify_at from the containing 1h candle.

    This is a linear approximation between candle open and close; hourly OHLC
    data cannot reveal the exact intrahour path. Candle intervals are half-open
    so an exact hour boundary belongs to the candle opening at that instant.
    """
    target = _as_utc(verify_at)
    if candles is None or getattr(candles, "empty", False):
        return None
    rows = candles.iterrows() if hasattr(candles, "iterrows") else enumerate(candles)
    matches = []
    for index, row in rows:
        get = row.get if hasattr(row, "get") else lambda key, default=None: default
        try:
            candle_open = _as_utc(get("timestamp", index))
            open_price = float(get("open"))
            close_price = float(get("close"))
        except (TypeError, ValueError, OverflowError, AttributeError):
            continue
        effective_close = candle_open + timedelta(hours=1)
        if candle_open <= target < effective_close:
            fraction = (target - candle_open).total_seconds() / 3600.0
            fraction = min(1.0, max(0.0, fraction))
            matches.append((candle_open, open_price + (close_price - open_price) * fraction))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


class LearningEngine:
    def __init__(self, db_manager, binance_client):
        self.db_manager = db_manager
        self.binance_client = binance_client
        self.logger = logging.getLogger(__name__)

    _utc = staticmethod(_as_utc)

    def _close_for_verify_at(self, symbol, verify_at):
        start = verify_at - timedelta(hours=1)
        try:
            frame = self.binance_client.get_historical_klines(
                symbol, "1h", lookback_days=2, start_time=start,
            )
        except Exception:
            self.logger.warning("Could not load 1h candle for delayed prediction verification %s", symbol,
                                exc_info=True)
            return None
        return price_at(verify_at, frame)

    def verify_pending_predictions(self) -> int:
        verified = 0
        for prediction in self.db_manager.get_pending_verifications():
            symbol = str(prediction["symbol"])
            verify_at = self._utc(prediction["verify_at"])
            delay_h = max(0.0, (datetime.now(timezone.utc) - verify_at).total_seconds() / 3600)
            if delay_h <= 1.0:
                current_symbol = symbol
                if "/" not in current_symbol and current_symbol.endswith("USDT"):
                    current_symbol = f"{current_symbol[:-4]}/USDT"
                price = self.binance_client.get_current_price(current_symbol)["price"]
            else:
                price = self._close_for_verify_at(symbol, verify_at)
                if price is None:
                    self.db_manager.mark_prediction_unverifiable_late(
                        prediction["prediction_id"], delay_h,
                    )
                    continue
            self.db_manager.save_outcome(
                prediction["prediction_id"], float(price), verify_delay_h=delay_h,
            )
            verified += 1
        return verified

    @staticmethod
    def _snapshot(prediction):
        value = prediction.get("features_snapshot")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value or {}

    def analyze_failure(self, prediction, outcome) -> dict:
        features = self._snapshot(prediction)
        reasons = []
        if float(features.get("rsi_14", 50)) > 70:
            reasons.append("RSI>70 ignorado")
        if float(features.get("vol_ratio", 1)) < 1:
            reasons.append("volumen caía")
        return {"prediction_id": prediction.get("prediction_id"), "reasons": reasons or ["condición no identificada"], "features": features}

    def analyze_success(self, prediction, outcome) -> dict:
        features = self._snapshot(prediction)
        correlated = [key for key in ("rsi_14", "adx", "vol_ratio", "ema_cross", "prophet_trend") if key in features]
        return {"prediction_id": prediction.get("prediction_id"), "correlated_features": correlated, "features": features}

    def update_condition_accuracy(self, model_name: str):
        # No current reader consumes model_accuracy_by_condition; avoid the
        # 10,000-row fetch, per-prediction outcome queries, and table rewrites.
        return []

    def should_retrain(self, model_name: str) -> bool:
        with self.db_manager.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM outcomes o JOIN predictions p ON p.prediction_id=o.prediction_id WHERE p.model_name=:m AND o.was_correct IS NOT NULL"), {"m": model_name}).scalar_one()
        return int(count) >= 168
