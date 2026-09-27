"""Configuration for the prediction models."""

TARGET_HORIZON_CANDLES = 4
TARGET_UP_THRESHOLD = 0.005

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
