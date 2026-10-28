from datetime import datetime, timezone

from grid.readiness import (
    STATE_COLLECTING, STATE_EVALUATING, STATE_NOT_RECOMMENDED, STATE_READY,
    evaluate_readiness, paired_bootstrap_mean_ci,
)


def test_common_readiness_states_cover_collection_evaluation_ready_and_adverse():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    ok = [{"text": "mejora", "ok": True, "value": 0.1}]
    no = [{"text": "mejora", "ok": False, "value": -0.1}]
    assert evaluate_readiness(n=0, criteria=ok, now=now)["state"] == STATE_COLLECTING
    assert evaluate_readiness(n=30, criteria=no, now=now)["state"] == STATE_EVALUATING
    ready = evaluate_readiness(n=30, criteria=ok, now=now)
    assert ready["state"] == STATE_READY
    assert ready["progress_pct"] == 100
    assert ready["criteria"] == ok
    assert evaluate_readiness(n=30, criteria=no, adverse_significant=True, now=now)["state"] == STATE_NOT_RECOMMENDED


def test_readiness_contract_has_estimate_and_common_fields():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    result = evaluate_readiness(n=10, criteria=[], sample_times=[now], now=now)
    assert set(result) == {"state", "n", "n_required", "progress_pct", "criteria",
                           "estimated_ready_at", "evaluated_at", "summary"}
    assert result["estimated_ready_at"] is not None
    assert result["n_required"] == 30


def test_bootstrap_interval_is_paired_and_deterministic():
    first = paired_bootstrap_mean_ci([1, 2, 3, 4])
    assert first == paired_bootstrap_mean_ci([1, 2, 3, 4])
    assert first[0] <= 2.5 <= first[1]
    assert paired_bootstrap_mean_ci([1]) is None


def test_probability_comparison_detects_improvement_with_paired_bootstrap():
    from grid.readiness import probability_comparison

    actual = [(.5, index % 2) for index in range(30)]
    candidate = [(.9 if truth else .1, truth) for _, truth in actual]
    result = probability_comparison(actual, candidate)
    assert result["n"] == 30
    assert result["candidate_log_loss"] < result["actual_log_loss"]
    assert result["candidate_brier"] < result["actual_brier"]
    assert result["log_loss_delta_ci95"][1] < 0
