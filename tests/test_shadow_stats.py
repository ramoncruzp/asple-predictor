from datetime import datetime, timedelta, timezone

from database.db_manager import DBManager


def add(db, prediction_id, predicted_at, signal, change_pct):
    db.save_prediction({
        "prediction_id": prediction_id, "symbol": "XRPUSDT", "interval": "1h",
        "model_name": "model_a", "predicted_at": predicted_at,
        "probability_up": 0.65, "signal": signal, "confidence": "alta",
        "price_at_prediction": 100.0,
    })
    db.save_outcome(prediction_id, 100 * (1 + change_pct))


def test_shadow_stats_nonoverlap_and_neutral_base_rate_and_pending():
    db = DBManager("sqlite:///:memory:")
    try:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index, hour in enumerate((0, 1, 5)):
            add(db, f"signal-{index}", start + timedelta(hours=hour, minutes=1), "ALCISTA", 0.01)
        add(db, "neutral-up", start + timedelta(hours=6), "NEUTRAL", 0.01)
        add(db, "neutral-flat", start + timedelta(hours=7), "NEUTRAL", 0.0)
        stats = db.get_shadow_stats("model_a", "XRPUSDT", "1h")
        assert stats["n_signals"] == 3
        assert stats["n_nonoverlap"] == 2
        assert stats["base_rate"] == 0.8
        assert stats["kill_status"] == "pending"
    finally:
        db.engine.dispose()


def test_shadow_stats_pass_with_40_nonoverlapping_signals():
    db = DBManager("sqlite:///:memory:")
    try:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(40):
            add(db, f"signal-{index}", start + timedelta(hours=index * 4), "ALCISTA", 0.01)
        for index in range(40):
            add(db, f"neutral-{index}", start + timedelta(hours=index * 4, minutes=1), "NEUTRAL", 0.0)
        stats = db.get_shadow_stats("model_a", "XRPUSDT", "1h")
        assert stats["n_nonoverlap"] == 40
        assert stats["kill_status"] == "pass"
    finally:
        db.engine.dispose()


def test_shadow_stats_fails_when_nonoverlap_mean_return_is_too_low():
    db = DBManager("sqlite:///:memory:")
    try:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(40):
            change = 0.006 if index < 24 else -0.01
            add(db, f"signal-{index}", start + timedelta(hours=index * 4), "ALCISTA", change)
        for index in range(40):
            add(db, f"neutral-{index}", start + timedelta(hours=index * 4, minutes=1), "NEUTRAL", 0.0)
        stats = db.get_shadow_stats("model_a", "XRPUSDT", "1h")
        assert stats["n_nonoverlap"] == 40
        assert stats["precision_nonoverlap"] >= stats["base_rate"] + 0.05
        assert stats["mean_return_nonoverlap"] < 0.002
        assert stats["kill_status"] == "fail"
    finally:
        db.engine.dispose()
