from fastapi import APIRouter, Query, Request
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
    for name in MODEL_DISPLAY_NAMES:
        model = loaded_models.get(name)
        info = model.get_model_info() if model is not None else {}
        stats = db.get_battle_stats(name)
        items.append({
            **stats,
            "display_name": MODEL_DISPLAY_NAMES[name],
            "nombre": info.get("nombre", MODEL_DISPLAY_NAMES[name]),
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
