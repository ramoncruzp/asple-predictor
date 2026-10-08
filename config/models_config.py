"""Configuration for the prediction models."""

import json
import re
import threading
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
VOL_CHAMPIONS = {1: "GBM", 2: "GBM", 4: "GBM", 24: "NexoHAR"}
VOL_ARTIFACT_DIR = "models/saved/vol"
_VOL_CONSENSUS_CACHE: dict[str, tuple[tuple[int, int] | None, dict | None]] = {}
_VOL_CONSENSUS_LOCK = threading.Lock()


def load_vol_consensus(symbol: str) -> dict | None:
    """Return a complete, symbol-matching consensus report, otherwise None."""
    if symbol == VOL_SYMBOL:
        return None
    path = vol_consensus_path(symbol)
    with _VOL_CONSENSUS_LOCK:
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        cached = _VOL_CONSENSUS_CACHE.get(symbol)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("report"), dict):
            _VOL_CONSENSUS_CACHE[symbol] = (signature, None)
            return None
        horizons = payload["report"].get("horizons", {})
        if payload.get("symbol") != symbol or not isinstance(horizons, dict):
            _VOL_CONSENSUS_CACHE[symbol] = (signature, None)
            return None
        if not all(str(h) in horizons for h in VOL_HORIZONS):
            _VOL_CONSENSUS_CACHE[symbol] = (signature, None)
            return None
        if any(not isinstance(horizons[str(h)], dict) or horizons[str(h)].get("champion") not in VOL_MODELS
               for h in VOL_HORIZONS):
            _VOL_CONSENSUS_CACHE[symbol] = (signature, None)
            return None
        _VOL_CONSENSUS_CACHE[symbol] = (signature, payload)
        return payload


def vol_champions(symbol: str) -> tuple[dict[int, str], bool]:
    """Return per-symbol champions and whether global XRP defaults are provisional."""
    if symbol == VOL_SYMBOL:
        return VOL_CHAMPIONS, False
    payload = load_vol_consensus(symbol)
    if payload is None:
        return VOL_CHAMPIONS, True
    horizons = payload["report"]["horizons"]
    return {h: horizons[str(h)]["champion"] for h in VOL_HORIZONS}, False


def vol_base(symbol: str) -> str:
    """Validate a USDT pair and return its lowercase base asset."""
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol) is None:
        raise ValueError(f"Símbolo de volatilidad no válido: {symbol!r}")
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


def vol_consensus_path(symbol: str) -> Path:
    """Return the legacy XRP consensus path or a symbol-scoped path."""
    base = vol_base(symbol)
    return Path(vol_artifact_dir(symbol)) / f"consensus_{base}.json"
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
