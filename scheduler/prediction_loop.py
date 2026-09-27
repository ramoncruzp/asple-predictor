"""Periodic live prediction scheduler."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL


class PredictionLoop:
    SYMBOLS_TO_MONITOR = [ACTIVE_SYMBOL]
    INTERVALS = [ACTIVE_INTERVAL]

    def __init__(self, binance_client, ensemble, scheduler=None, db_manager=None):
        self.binance_client = binance_client
        self.ensemble = ensemble
        self.scheduler = scheduler or BackgroundScheduler()
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)
        self.latest = {}

    @staticmethod
    def _utc_datetime(value):
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _run_cycle(self, *, startup=False):
        for symbol in self.SYMBOLS_TO_MONITOR:
            for interval in self.INTERVALS:
                try:
                    df = self.binance_client.get_historical_klines(
                        symbol, interval, lookback_days=30
                    )
                    if df.empty:
                        continue
                    if self._utc_datetime(df.iloc[-1]["close_time"]) > datetime.now(timezone.utc):
                        df = df.iloc[:-1]
                    if df.empty:
                        continue
                    if startup and self.db_manager is not None:
                        last_closed_close = self._utc_datetime(df.iloc[-1]["close_time"])
                        if self.db_manager.has_prediction_since(
                            symbol,
                            interval,
                            getattr(self.ensemble, "primary_model_name", "ensemble"),
                            last_closed_close,
                        ):
                            self.logger.info(
                                "Startup prediction already exists for %s %s since %s",
                                symbol, interval, last_closed_close,
                            )
                            continue
                    df = df.tail(200)
                    result = self.ensemble.predict_and_save(symbol, interval, df)
                    self.latest[(symbol, interval)] = result
                    self.logger.info("Prediction %s %s: %s", symbol, interval, result["consensus_signal"])
                except Exception:
                    self.logger.exception("Prediction cycle failed for %s %s", symbol, interval)

    def run_prediction_cycle(self):
        self._run_cycle()

    def run_startup_cycle(self):
        self._run_cycle(startup=True)

    def start(self):
        self.logger.info("Prediction models enabled: %s", list(self.ensemble.models))
        self.run_startup_cycle()
        self.scheduler.add_job(
            self.run_prediction_cycle,
            "cron",
            hour="*",
            minute=1,
            timezone="UTC",
            id="prediction_cycle",
            replace_existing=True,
        )
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
