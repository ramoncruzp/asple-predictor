from fastapi import APIRouter, Query, Request
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL, SHADOW_MODEL_NAME
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
    for name, model in loaded_models.items():
        display_name = MODEL_DISPLAY_NAMES.get(name, name)
        info = model.get_model_info()
        stats = db.get_battle_stats(name)
        items.append({
            **stats,
            "display_name": display_name,
            "nombre": info.get("nombre", display_name),
            "accuracy_30d": stats["accuracy"],
            "verified_predictions": stats["verified_count"],
            "last_trained": info.get("ultima_actualizacion"),
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
