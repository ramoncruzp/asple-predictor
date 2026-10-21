"""Periodic live prediction scheduler."""
from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
        self._down_models = {}

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
                            self.latest[(symbol, interval)] = self.ensemble.predict(
                                symbol, interval, df.tail(200)
                            )
                            continue
                    df = df.tail(200)
                    result = self.ensemble.predict_and_save(symbol, interval, df)
                    self.latest[(symbol, interval)] = result
                    self.logger.info("Prediction %s %s: %s", symbol, interval, result["consensus_signal"])
                except Exception:
                    self.logger.exception("Prediction cycle failed for %s %s", symbol, interval)
        if self.db_manager is not None:
            saved = Path(__file__).resolve().parents[1] / "models" / "saved"
            for artifact in sorted(saved.glob("model_a_down_*_1h.joblib")):
                base = artifact.name[len("model_a_down_"):-len("_1h.joblib")]
                symbol = f"{base.upper()}USDT"
                try:
                    frame = self.binance_client.get_historical_klines(symbol, "1h", lookback_days=30)
                    if frame.empty:
                        continue
                    if self._utc_datetime(frame.iloc[-1]["close_time"]) > datetime.now(timezone.utc):
                        frame = frame.iloc[:-1]
                    if not frame.empty:
                        self._predict_down(symbol, "1h", frame.tail(200))
                except Exception:
                    self.logger.exception("Down prediction cycle failed for %s", symbol)

    def _predict_down(self, symbol, interval, frame):
        """Emit and persist an independent four-hour down forecast when available."""
        if interval != "1h" or self.db_manager is None:
            return
        base = symbol.upper().removesuffix("USDT").lower()
        artifact = Path(__file__).resolve().parents[1] / "models" / "saved" / f"model_a_down_{base}_1h.joblib"
        if not artifact.is_file():
            return
        from models.model_a_xgboost import ModelADown
        key = str(artifact.resolve())
        model = self._down_models.get(key)
        if model is None:
            model = ModelADown()
            model.load(str(artifact))
            self._down_models[key] = model
        forecast = model.predict_down(frame)
        p_down = float(forecast["p_down"])
        timestamp = self._utc_datetime(frame.iloc[-1]["close_time"])
        result = {**forecast, "model_name": "model_a_down", "symbol": symbol,
                  "interval": interval, "p_down": p_down, "timestamp": timestamp}
        self.latest[(symbol, interval, "model_a_down")] = result
        if not self.db_manager.has_prediction_since(symbol, interval, "model_a_down", timestamp):
            signal = "BAJISTA" if p_down >= .5 else "ALCISTA"
            self.db_manager.save_prediction({
                "symbol": symbol, "interval": interval, "model_name": "model_a_down",
                "predicted_at": timestamp, "verify_at": timestamp + timedelta(hours=4),
                "verification_delay_candles": 4, "probability_up": p_down,
                "signal": signal, "confidence": "alta" if abs(p_down - .5) >= .25 else "media",
                "price_at_prediction": float(frame.iloc[-1]["close"]),
                "features_snapshot": {"p_down": p_down, "direction_target": "down_4h"},
            })

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
