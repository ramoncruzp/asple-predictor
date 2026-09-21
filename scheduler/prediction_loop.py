"""Periodic live prediction scheduler."""
from __future__ import annotations

import logging
from apscheduler.schedulers.background import BackgroundScheduler


class PredictionLoop:
    SYMBOLS_TO_MONITOR = ["XRPUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
    INTERVALS = ["4h"]

    def __init__(self, binance_client, ensemble, scheduler=None):
        self.binance_client = binance_client
        self.ensemble = ensemble
        self.scheduler = scheduler or BackgroundScheduler()
        self.logger = logging.getLogger(__name__)

    def run_prediction_cycle(self):
        for symbol in self.SYMBOLS_TO_MONITOR:
            for interval in self.INTERVALS:
                try:
                    df = self.binance_client.get_historical_klines(symbol, interval, lookback_days=60).tail(200)
                    result = self.ensemble.predict_and_save(symbol, interval, df)
                    self.logger.info("Prediction %s %s: %s", symbol, interval, result["consensus_signal"])
                except Exception:
                    self.logger.exception("Prediction cycle failed for %s %s", symbol, interval)

    def start(self):
        self.logger.info("Prediction models enabled: %s", list(self.ensemble.models))
        self.run_prediction_cycle()
        self.scheduler.add_job(self.run_prediction_cycle, "interval", hours=4, id="prediction_cycle", replace_existing=True)
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
