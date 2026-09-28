"""Configuration for the prediction models."""

TARGET_HORIZON_CANDLES = 4
TARGET_UP_THRESHOLD = 0.005

ACTIVE_SYMBOL = "XRPUSDT"
ACTIVE_INTERVAL = "1h"
SHADOW_MODEL_NAME = "model_a"
SHADOW_THRESHOLD = 0.60
SHADOW_ARTIFACT = "models/saved/model_a_xrp_1h.joblib"
SHADOW_KILL_MIN_SIGNALS = 40
SHADOW_KILL_MIN_LIFT_PTS = 0.05
SHADOW_KILL_MIN_MEAN_RETURN = 0.002

VOL_SYMBOL = "XRPUSDT"
VOL_HORIZONS = [1, 2, 4, 24]
VOL_MODELS = [
    "Persistence", "EWMA", "HAR", "HAR_range", "HAR_asym", "GBM", "NexoHAR", "GARCH_t"
]
VOL_CHAMPIONS = {1: "GBM", 2: "GBM", 4: "GBM", 24: "NexoHAR"}
VOL_ARTIFACT_DIR = "models/saved/vol"
VOL_LIVE_MIN_VERIFIED = 30

MODELS_CONFIG = {
    "model_a": {
        "nombre": "XGBoost",
        "version": "1.0",
        "enabled": True,
        "min_accuracy_threshold": 0.52,
    },
    "model_b": {
        "nombre": "GRU",
        "version": "1.0",
        "enabled": True,
        "min_accuracy_threshold": 0.52,
    },
    "model_c": {
        "nombre": "Prophet+XGBoost",
        "version": "1.0",
        "enabled": True,
        "min_accuracy_threshold": 0.52,
    },
}
