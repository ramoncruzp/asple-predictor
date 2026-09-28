"""Hourly volatility forecast and delayed-realization verification loop."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

from config.models_config import VOL_SYMBOL
from data.volatility import aggregate_intraday_to_hourly


class VolLoop:
    def __init__(self, binance_client, predictor, db_manager, scheduler=None):
        self.binance_client = binance_client
        self.predictor = predictor
        self.db_manager = db_manager
        self.scheduler = scheduler or BackgroundScheduler()
        self.logger = logging.getLogger(__name__)
        self.latest: dict = {}

    @staticmethod
    def _closed(frame: pd.DataFrame, now: datetime) -> pd.DataFrame:
        result = frame.copy(deep=True)
        if "close_time" in result:
            close_times = pd.to_datetime(result["close_time"], utc=True)
            result = result.loc[close_times <= pd.Timestamp(now)]
        return result.reset_index(drop=True)

    @staticmethod
    def realized_for_forecast(
        forecast: dict, hourly_intraday: pd.DataFrame, latest_close: datetime
    ) -> float | None:
        forecast_at = pd.Timestamp(forecast["forecast_at"])
        if forecast_at.tzinfo is None:
            forecast_at = forecast_at.tz_localize("UTC")
        else:
            forecast_at = forecast_at.tz_convert("UTC")
        latest = pd.Timestamp(latest_close)
        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        else:
            latest = latest.tz_convert("UTC")
        horizon = int(forecast["horizon_h"])
        if latest < forecast_at + pd.Timedelta(hours=horizon):
            return None
        first_future_hour = forecast_at.floor("h") + pd.Timedelta(hours=1)
        end_exclusive = first_future_hour + pd.Timedelta(hours=horizon)
        future = hourly_intraday.loc[
            (pd.to_datetime(hourly_intraday["timestamp"], utc=True) >= first_future_hour)
            & (pd.to_datetime(hourly_intraday["timestamp"], utc=True) < end_exclusive)
        ].sort_values("timestamp")
        if len(future) != horizon or not future["complete_hour"].astype(bool).all():
            return None
        realized_variance = pd.to_numeric(future["rv_intra"], errors="coerce").to_numpy(dtype="float64")
        if not np.isfinite(realized_variance).all():
            return None
        return float(0.5 * np.log(realized_variance.mean() + 1e-12))

    def run_cycle(self):
        now = datetime.now(timezone.utc)
        try:
            hourly = self._closed(
                self.binance_client.get_historical_klines(VOL_SYMBOL, "1h", lookback_days=12), now
            )
            five_minute = self._closed(
                self.binance_client.get_historical_klines(VOL_SYMBOL, "5m", lookback_days=12), now
            )
            if hourly.empty or five_minute.empty:
                self.logger.warning("VolLoop: Binance devolvió velas cerradas insuficientes")
                return
            predictions = self.predictor.predict_latest(hourly, five_minute, now=now)
            inserted = self.db_manager.save_vol_forecasts(predictions)
            if predictions:
                self.latest = {
                    "forecast_at": predictions[0]["forecast_at"],
                    "made_at": predictions[0]["made_at"],
                    "price": predictions[0]["price"],
                    "forecasts": predictions,
                }
            hourly_intraday = aggregate_intraday_to_hourly(five_minute)
            if "close_time" in hourly and not hourly.empty:
                latest_close = pd.to_datetime(hourly["close_time"], utc=True).max().to_pydatetime()
                for pending in self.db_manager.get_pending_vol_verifications(now):
                    realized = self.realized_for_forecast(pending, hourly_intraday, latest_close)
                    if realized is not None:
                        self.db_manager.save_vol_realized(pending["id"], realized)
            self.logger.info("VolLoop: %d forecasts inserted; processed verification cycle", inserted)
        except Exception:
            self.logger.exception("Volatility prediction cycle failed")

    def start(self):
        self.run_cycle()
        self.scheduler.add_job(
            self.run_cycle,
            "cron",
            hour="*",
            minute=1,
            timezone="UTC",
            id="volatility_cycle",
            replace_existing=True,
        )
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
