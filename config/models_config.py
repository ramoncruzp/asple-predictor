"""Configuration for the prediction models."""

import re
from pathlib import Path

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
# Champions remain global in this phase; per-symbol champions belong to 20B-2c.
VOL_CHAMPIONS = {1: "GBM", 2: "GBM", 4: "GBM", 24: "NexoHAR"}
VOL_ARTIFACT_DIR = "models/saved/vol"


def vol_base(symbol: str) -> str:
    """Validate a USDT pair and return its lowercase base asset."""
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol) is None:
        raise ValueError(f"S?mbolo de volatilidad no v?lido: {symbol!r}")
    return symbol[:-4].lower()


def vol_artifact_dir(symbol: str) -> str:
    """Return the legacy XRP artifact directory or the isolated symbol directory."""
    base = vol_base(symbol)
    if symbol == VOL_SYMBOL:
        return VOL_ARTIFACT_DIR
    return str(Path(VOL_ARTIFACT_DIR) / base)


def vol_manifest_path(symbol: str) -> Path:
    """Return the manifest path for a validated USDT pair."""
    base = vol_base(symbol)
    return Path(vol_artifact_dir(symbol)) / f"manifest_{base}.json"
VOL_LIVE_MIN_VERIFIED = 30
VOL_SOURCE = "auto"
VOL_WIDEN_AUTO = False
VOL_WIDEN_K_ACTIVE = 1.25
VOL_WIDEN_DISAGREEMENT_PCT = 15.0
VOL_WIDEN_COMPUTE_HOUR_UTC = 3
STRESS_PERCENTILE = 80
WIDEN_MIN_EFFECTIVE = 20
WIDEN_MAX_CI_WIDTH = 0.30
WIDEN_EMA_ALPHA = 0.30
WIDEN_DISAGREEMENT_MIN = 200

MODELS_CONFIG = {
    "model_a": {
        "nombre": "XGBoost",
        "version": "1.0",
        "enabled": True,
        "validation_status": "shadow",
        "signal_threshold": 0.60,
        "min_accuracy_threshold": 0.52,
    },
    "model_b": {
        "nombre": "GRU",
        "version": "1.0",
        "enabled": True,
        "validation_status": "not_validated",
        "signal_threshold": 0.55,
        "min_accuracy_threshold": 0.52,
    },
    "model_c": {
        "nombre": "Prophet+XGBoost",
        "version": "1.0",
        "enabled": True,
        "validation_status": "not_validated",
        "signal_threshold": 0.55,
        "min_accuracy_threshold": 0.52,
    },
}
