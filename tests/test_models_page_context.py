from types import SimpleNamespace

from api.routes.models_status import page_context
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL


def test_models_page_context_scopes_signal_counts_and_reads_only_registry():
    class FakeDB:
        calls = []

        def get_active_coins(self):
            return [{"symbol": ACTIVE_SYMBOL}, {"symbol": "BTCUSDT"}]

        def get_predictions_with_outcomes(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model_name"] != "model_a":
                return []
            return [
                {"signal": "ALCISTA", "is_verified": True, "was_correct": True, "actual_direction": "UP"},
                {"signal": "ALCISTA", "is_verified": True, "was_correct": False, "actual_direction": "DOWN"},
                {"signal": "ALCISTA", "is_verified": False, "was_correct": None, "actual_direction": None},
                {"signal": "NEUTRAL", "is_verified": True, "was_correct": None, "actual_direction": "UP"},
            ]

    db = FakeDB()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    result = page_context(request, symbol=ACTIVE_SYMBOL, interval=ACTIVE_INTERVAL)
    assert result["coins"] == [ACTIVE_SYMBOL, "BTCUSDT"]
    assert result["symbol"] == ACTIVE_SYMBOL and result["interval"] == ACTIVE_INTERVAL
    assert result["models"]["model_a"] == {
        "total_predictions": 4, "verified_count": 3, "pending_count": 1,
        "bullish_count": 3, "bullish_correct": 1, "bullish_failed": 1,
        "bullish_pending": 1, "neutral_count": 1, "base_rate": 2 / 3,
        "predictions_before_last_training": None,
    }
    assert all(call["symbol"] == ACTIVE_SYMBOL and call["interval"] == ACTIVE_INTERVAL for call in db.calls)
    assert all("model_name" in call and call["limit"] == 100000 for call in db.calls)


def test_page_context_counts_predictions_before_artifact_training(tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from database.db_manager import DBManager
    import api.routes.models_status as route
    from models.training_jobs import artifact_trained_at

    db = DBManager(f"sqlite:///{tmp_path / 'before-training.sqlite'}")
    with db.engine.begin() as conn:
        for idx, stamp in enumerate((datetime(2026, 1, 1), datetime(2026, 2, 1)), 1):
            conn.execute(db.predictions.insert().values(
                prediction_id=f"p-{idx}", symbol=ACTIVE_SYMBOL, interval=ACTIVE_INTERVAL,
                model_name="model_a", predicted_at=stamp, verify_at=datetime(2026, 3, 1),
                probability_up=0.5, signal="NEUTRAL", confidence="baja",
                price_at_prediction=1.0,
            ))

    class ContextDB:
        engine = db.engine
        predictions = db.predictions

        def get_active_coins(self):
            return [{"symbol": ACTIVE_SYMBOL}]

        def get_predictions_with_outcomes(self, **_kwargs):
            return []

    monkeypatch.setattr(route, "read_metrics_manifest", lambda *_args: {
        "models": {"model_a": {"trained_at": "2026-01-15T00:00:00+00:00"}}
    })
    monkeypatch.setattr(route, "artifact_trained_at", lambda manifest, name, **kwargs:
                        artifact_trained_at(manifest, name, root=tmp_path, **kwargs))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=ContextDB())))
    result = route.page_context(request, symbol=ACTIVE_SYMBOL, interval=ACTIVE_INTERVAL)
    assert result["models"]["model_a"]["predictions_before_last_training"] == 1
    assert result["models"]["model_b"]["predictions_before_last_training"] is None
    assert result["models"]["model_a"]["predictions_before_last_training"] == 1
    db.engine.dispose()
