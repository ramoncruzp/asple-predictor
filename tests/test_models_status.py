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
