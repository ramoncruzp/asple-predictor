from fastapi import FastAPI, Request

from api.routes.models_status import status


class FakeModel:
    def get_model_info(self):
        return {"nombre": "XGBoost", "ultima_actualizacion": "today"}


class FakeDB:
    def get_battle_stats(self, model_name):
        return {
            "model_name": model_name,
            "total_predictions": 0,
            "verified_count": 0,
            "correct_count": 0,
            "pending_count": 0,
            "accuracy": None,
        }


def test_status_lists_unavailable_shadow_models_without_claiming_they_loaded():
    app = FastAPI()
    app.state.db = FakeDB()
    app.state.models = {"model_a": FakeModel()}
    request = Request({"type": "http", "app": app})

    response = status(request)

    assert [model["model_name"] for model in response["models"]] == ["model_a", "model_b", "model_c"]
    assert response["models"][0]["available"] is True
    assert response["models"][1]["available"] is False
    assert response["models"][2]["available"] is False
    assert response["models"][1]["validation_status"] == "not_validated"

def test_status_and_page_context_report_manifest_training_time_for_loaded_and_missing_models(tmp_path, monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.routes.models_status as route_module
    from models.training_jobs import artifact_trained_at as real_artifact_trained_at
    from models.training_jobs import read_metrics_manifest as real_read_metrics_manifest

    saved = tmp_path / "models" / "saved"
    saved.mkdir(parents=True)
    manifest = {"models": {
        "model_a": {"trained_at": "2026-10-01T12:34:56+00:00"},
        "model_b": {"trained_at": "2026-10-02T12:34:56+00:00"},
    }}
    (saved / "metrics_xrp_1h.json").write_text(json.dumps(manifest), encoding="utf-8")
    (saved / "metrics_btc_4h.json").write_text(json.dumps(manifest), encoding="utf-8")
    manifest_calls, trained_at_inputs = [], []

    def read_manifest(_root, symbol, interval):
        manifest_calls.append((symbol, interval))
        return real_read_metrics_manifest(tmp_path, symbol, interval)

    def artifact_time(data, model_name, *, symbol="XRPUSDT", interval="1h", root=None):
        return real_artifact_trained_at(data, model_name, symbol=symbol, interval=interval, root=tmp_path)

    class PageDB(FakeDB):
        def get_active_coins(self):
            return [{"symbol": "BTCUSDT"}]
        def get_prediction_signal_summary(self, symbol, interval, model_name):
            return {"total_predictions": 0, "verified_count": 0, "pending_count": 0}

    def before_training(_db, symbol, interval, model_name, trained_at):
        trained_at_inputs.append((model_name, symbol, interval, trained_at))
        return 0

    monkeypatch.setattr(route_module, "read_metrics_manifest", read_manifest)
    monkeypatch.setattr(route_module, "artifact_trained_at", artifact_time)
    monkeypatch.setattr(route_module, "predictions_before_training", before_training)
    app = FastAPI()
    app.include_router(route_module.router, prefix="/api/models")
    app.state.db = PageDB()
    app.state.models = {"model_a": FakeModel()}
    with TestClient(app) as client:
        status_response = client.get("/api/models/status")
        context_response = client.get("/api/models/page-context?symbol=BTCUSDT&interval=4h")

    assert status_response.status_code == 200
    status_models = {row["model_name"]: row for row in status_response.json()["models"]}
    assert status_models["model_a"]["available"] is True
    assert status_models["model_a"]["artifact_trained_at"] == "2026-10-01T12:34:56+00:00"
    assert status_models["model_b"]["available"] is False
    assert status_models["model_b"]["artifact_trained_at"] == "2026-10-02T12:34:56+00:00"
    assert context_response.status_code == 200
    assert context_response.json()["models"]["model_a"]["predictions_before_last_training"] == 0
    assert ("model_a", "BTCUSDT", "4h", "2026-10-01T12:34:56+00:00") in trained_at_inputs
    assert ("model_b", "BTCUSDT", "4h", "2026-10-02T12:34:56+00:00") in trained_at_inputs
    assert ("BTCUSDT", "4h") in manifest_calls

