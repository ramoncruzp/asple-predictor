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
    }
    assert all(call["symbol"] == ACTIVE_SYMBOL and call["interval"] == ACTIVE_INTERVAL for call in db.calls)
    assert all("model_name" in call and call["limit"] == 100000 for call in db.calls)
