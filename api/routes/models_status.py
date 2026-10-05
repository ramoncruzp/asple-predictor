from fastapi import APIRouter, Query, Request
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL, MODELS_CONFIG, SHADOW_MODEL_NAME
router = APIRouter()

MODEL_DISPLAY_NAMES = {
    "model_a": "XGBoost",
    "model_b": "GRU (PyTorch)",
    "model_c": "Prophet+XGBoost",
    "model_d": "TFT (Temporal Fusion)",
    "ensemble": "Ensemble",
}

@router.get("/status")
def status(request: Request):
    db, items = request.app.state.db, []
    loaded_models = request.app.state.models
    load_report = getattr(request.app.state, "model_load_report", {})
    for name, configuration in MODELS_CONFIG.items():
        if not configuration.get("enabled", False):
            continue
        model = loaded_models.get(name)
        display_name = MODEL_DISPLAY_NAMES.get(name, name)
        stats = db.get_battle_stats(name)
        report = load_report.get(name, {})
        if model is None:
            items.append({
                **stats, "display_name": display_name, "nombre": display_name,
                "accuracy_30d": stats["accuracy"],
                "verified_predictions": stats["verified_count"],
                "last_trained": None, "available": False,
                "unavailable_reason": report.get("reason", "Modelo no cargado"),
                "validation_status": configuration.get("validation_status", "not_validated"),
                "signal_threshold": configuration.get("signal_threshold"),
                "load_duration_ms": report.get("duration_ms"),
                "rss_delta_bytes": report.get("rss_delta_bytes"),
            })
            continue
        info = model.get_model_info()
        items.append({
            **stats, "display_name": display_name,
            "nombre": info.get("nombre", display_name),
            "accuracy_30d": stats["accuracy"],
            "verified_predictions": stats["verified_count"],
            "last_trained": info.get("ultima_actualizacion"),
            "available": True,
            "validation_status": configuration.get("validation_status", "not_validated"),
            "signal_threshold": configuration.get("signal_threshold"),
            "load_duration_ms": report.get("duration_ms"),
            "rss_delta_bytes": report.get("rss_delta_bytes"),
        })
    ranked = [item for item in items if item["accuracy_30d"] is not None]
    winner = max(ranked, key=lambda item: item["accuracy_30d"], default=None)
    return {"models": items, "winner_by_accuracy": winner["model_name"] if winner else None, "winner_by_condition": None}

@router.get("/accuracy-by-condition")
def accuracy_by_condition(request: Request, model: str = Query(...)):
    return request.app.state.db.get_accuracy_by_condition(model)

@router.get("/shadow-status")
def shadow_status(request: Request):
    return request.app.state.db.get_shadow_stats(SHADOW_MODEL_NAME, ACTIVE_SYMBOL, ACTIVE_INTERVAL)
