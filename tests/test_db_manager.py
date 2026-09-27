import pytest

from database.db_manager import DBManager


@pytest.fixture
def db():
    return DBManager("sqlite:///:memory:")


def prediction(db, signal, price=100.0, prediction_id="prediction"):
    return db.save_prediction({
        "prediction_id": prediction_id,
        "symbol": "XRPUSDT",
        "interval": "4h",
        "model_name": "model_a",
        "probability_up": 0.6,
        "signal": signal,
        "confidence": "high",
        "price_at_prediction": price,
    })


@pytest.mark.parametrize(
    ("signal", "verification_price", "expected"),
    [
        ("ALCISTA", 101.0, 1),
        ("ALCISTA", 100.3, 0),
        ("BAJISTA", 100.3, 1),
        ("BAJISTA", 101.0, 0),
        ("NEUTRAL", 101.0, None),
    ],
)
def test_save_outcome_uses_target_label_and_skips_neutral(
    db, signal, verification_price, expected
):
    prediction_id = prediction(db, signal)

    outcome = db.save_outcome(prediction_id, verification_price)

    assert outcome["was_correct"] == expected
    if expected is None:
        assert outcome["why_correct"] is None
        assert outcome["why_wrong"] is None


def test_battle_stats_excludes_neutral_from_accuracy_denominator(db):
    bullish_id = prediction(db, "ALCISTA", prediction_id="bullish")
    neutral_id = prediction(db, "NEUTRAL", prediction_id="neutral")
    db.save_outcome(bullish_id, 101.0)
    db.save_outcome(neutral_id, 101.0)

    stats = db.get_battle_stats("model_a")

    assert stats["verified_count"] == 2
    assert stats["evaluated_count"] == 1
    assert stats["neutral_count"] == 1
    assert stats["accuracy"] == 1.0
