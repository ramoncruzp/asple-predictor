"""Periodic outcome verification scheduler."""
from __future__ import annotations

import logging
from apscheduler.schedulers.background import BackgroundScheduler


class VerificationLoop:
    MODELS = ["model_a", "model_b", "model_c"]

    def __init__(self, learning_engine, scheduler=None):
        self.learning_engine = learning_engine
        self.scheduler = scheduler or BackgroundScheduler()
        self.logger = logging.getLogger(__name__)

    def run_verification_cycle(self):
        verified = self.learning_engine.verify_pending_predictions()
        self.logger.info("Verified %d pending predictions", verified)
        for model_name in self.MODELS:
            self.learning_engine.update_condition_accuracy(model_name)
        self.check_retraining_needed()
        return verified

    def check_retraining_needed(self):
        for model_name in self.MODELS:
            if self.learning_engine.should_retrain(model_name):
                self.logger.info("REENTRENAMIENTO RECOMENDADO para %s", model_name)

    def start(self):
        self.scheduler.add_job(self.run_verification_cycle, "interval", minutes=30, id="verification_cycle", replace_existing=True)
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
