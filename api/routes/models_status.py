import json
from fastapi import APIRouter, HTTPException, Query, Request
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL, MODELS_CONFIG, SHADOW_MODEL_NAME
from pathlib import Path
from models.training_jobs import artifact_trained_at, predictions_before_training, read_metrics_manifest
from config.models_config import VOL_ARTIFACT_DIR, VOL_SYMBOL, vol_base, vol_consensus_path, vol_manifest_path
router = APIRouter()


@router.get("/vol/artifacts")
def volatility_artifacts(request: Request, symbol: str = Query(VOL_SYMBOL)):
    symbol = (symbol or "").strip().upper().replace("/", "")
    try:
        vol_base(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        manifest_path = (Path(VOL_ARTIFACT_DIR) / "manifest_xrp.json" if symbol == VOL_SYMBOL
                         else vol_manifest_path(symbol))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        manifest = {}
    try:
        consensus_path = (Path(VOL_ARTIFACT_DIR) / "consensus_xrp.json" if symbol == VOL_SYMBOL
                          else vol_consensus_path(symbol))
        consensus = json.loads(consensus_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        consensus = {}
    registry = getattr(request.app.state, "vol_registry", None)
    predictor = registry.get(symbol) if registry is not None else None
    if predictor is None and symbol == VOL_SYMBOL:
        predictor = getattr(request.app.state, "vol_predictor", None)
    loaded_manifest = getattr(predictor, "manifest", None) if predictor is not None else None
    digest = consensus.get("source_csv_sha256")
    return {"symbol": symbol, "artifact_trained_at": manifest.get("trained_at"),
            "loaded_trained_at": loaded_manifest.get("trained_at") if isinstance(loaded_manifest, dict) else None,
            "data_range": manifest.get("data_range"),
            "consensus": {"created_at": consensus.get("created_at"),
                          "source_csv": consensus.get("source_csv"),
                          "source_csv_sha256_short": str(digest)[:12] if digest else None}}

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
    manifest = read_metrics_manifest(Path(__file__).resolve().parents[2], ACTIVE_SYMBOL, ACTIVE_INTERVAL)
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
                "artifact_trained_at": artifact_trained_at(
                    manifest, name, symbol=ACTIVE_SYMBOL, interval=ACTIVE_INTERVAL,
                ),
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
            "artifact_trained_at": artifact_trained_at(
                manifest, name, symbol=ACTIVE_SYMBOL, interval=ACTIVE_INTERVAL,
            ),
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


@router.get("/page-context")
def page_context(
    request: Request,
    symbol: str = Query(ACTIVE_SYMBOL),
    interval: str = Query(ACTIVE_INTERVAL),
):
    """Read-only registry and signal summary for the Models page; no market-data calls."""
    db = request.app.state.db
    coins = [row["symbol"] for row in db.get_active_coins()]
    symbol = symbol.strip().upper().replace("/", "")
    interval = interval.strip().lower()
    if symbol not in coins:
        raise HTTPException(status_code=404, detail="La moneda no está activa en el registro")
    names = list(dict.fromkeys(("model_a", "model_b", "model_c", "model_d", "ensemble", *MODELS_CONFIG)))
    summaries = {}
    manifest = read_metrics_manifest(Path(__file__).resolve().parents[2], symbol, interval)
    for name in names:
        aggregate = getattr(db, "get_prediction_signal_summary", None)
        if aggregate:
            summaries[name] = aggregate(symbol, interval, name)
        else:
            rows = db.get_predictions_with_outcomes(symbol=symbol, interval=interval, model_name=name, limit=100000)
            unverifiable_late = [row for row in rows if row.get("verification_status") == "unverifiable_late"]
            rows = [row for row in rows if row.get("verification_status") != "unverifiable_late"]
            verified = [row for row in rows if row.get("is_verified")]
            bullish = [row for row in rows if str(row.get("signal") or "").upper() == "ALCISTA"]
            evaluated = [row for row in bullish if row.get("was_correct") is not None]
            actual = [str(row.get("actual_direction") or "").upper() for row in verified]
            summaries[name] = {
                "total_predictions": len(rows), "verified_count": len(verified),
                "n_unverifiable_late": len(unverifiable_late),
                "pending_count": len(rows) - len(verified), "bullish_count": len(bullish),
                "bullish_correct": sum(row.get("was_correct") is True for row in evaluated),
                "bullish_failed": sum(row.get("was_correct") is False for row in evaluated),
                "bullish_pending": len(bullish) - len(evaluated),
                "neutral_count": sum(str(row.get("signal") or "").upper() == "NEUTRAL" for row in rows),
                "base_rate": sum(direction == "UP" for direction in actual) / len(actual) if actual else None,
            }
        trained_at = artifact_trained_at(manifest, name, symbol=symbol, interval=interval)
        summaries[name]["predictions_before_last_training"] = predictions_before_training(
            db, symbol, interval, name, trained_at,
        )
    return {"symbol": symbol, "interval": interval, "coins": coins, "models": summaries}
