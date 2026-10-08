from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import api.routes.grids as grids_api


def _summary(monkeypatch, cohort_pnls, control_pnls, zero_day_ids=(), duration_days_by_id=None, loans_by_group=None):
    rows = []
    pnl_by_id = {}
    next_id = 1
    for group, values in (("loans", cohort_pnls), ("control", control_pnls)):
        for pnl in values:
            duration = 0 if next_id in zero_day_ids else (duration_days_by_id or {}).get(next_id, 10)
            created = datetime(2026, 1, 1, tzinfo=timezone.utc)
            closed_at = (created + timedelta(days=duration)).isoformat().replace("+00:00", "Z")
            rows.append({"id": next_id, "symbol": "ADAUSDT", "strategy": "smart",
                "status": "CLOSED", "params": {"loans_group": group}, "capital_total": 1000.0,
                "created_at": created.isoformat().replace("+00:00", "Z"), "closed_at": closed_at})
            pnl_by_id[next_id] = pnl
            next_id += 1
    class DB:
        def get_grid_levels(self, grid_id):
            return [{"pnl": pnl_by_id[grid_id], "cycles_completed": 2, "fee_paid": 0.5}]
        def list_grid_loans(self, grid_id):
            group = next(row["params"]["loans_group"] for row in rows if row["id"] == grid_id)
            return [{"status": "OPEN"}] * int((loans_by_group or {}).get(group, 0))
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=DB())))
    return grids_api.loans_summary(request)


def test_bootstrap_cohort_comparison_is_reproducible_and_reports_small_sample(monkeypatch):
    result1 = _summary(monkeypatch, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], loans_by_group={"loans": 1})
    result2 = _summary(monkeypatch, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], loans_by_group={"loans": 1})
    pair1 = result1["cohort_comparisons"]["loans_vs_control"]
    pair2 = result2["cohort_comparisons"]["loans_vs_control"]
    assert pair1 == pair2
    assert pair1["n_cohort"] == pair1["n_control"] == 10
    assert pair1["diff_pct_per_day"] == 0
    assert pair1["ci_low"] <= 0 <= pair1["ci_high"]
    assert pair1["conclusive"] is False
    assert pair1["reason"] == "el IC incluye 0"
    assert pair1["small_sample"] is False


def test_one_grid_per_cohort_is_not_conclusive_and_marks_small_sample(monkeypatch):
    result = _summary(monkeypatch, [10], [0])
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["n_cohort"] == pair["n_control"] == 1
    assert pair["conclusive"] is False
    assert pair["reason"] == "muestra insuficiente"
    assert pair["small_sample"] is True


def test_separated_ten_grid_cohorts_can_be_conclusive(monkeypatch):
    result = _summary(monkeypatch, [100] * 10, [0] * 10, loans_by_group={"loans": 1})
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["n_cohort"] == pair["n_control"] == 10
    assert pair["ci_low"] > 0
    assert pair["conclusive"] is True
    assert pair["reason"] is None


def test_empty_cohort_returns_null_and_old_summary_fields_remain(monkeypatch):
    result = _summary(monkeypatch, [], [10, 20])
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["diff_pct_per_day"] is None
    assert pair["ci_low"] is None and pair["ci_high"] is None
    assert pair["conclusive"] is False
    assert pair["reason"] == "muestra insuficiente"
    loans = next(group for group in result["groups"] if group["group"] == "loans")
    assert loans["grid_count"] == 0
    control = next(group for group in result["groups"] if group["group"] == "control")
    assert control["grid_count"] == 2
    assert control["realized_pnl_usdt"] == 30
    assert control["pnl_pct_capital"] == 1.5
    assert control["pnl_pct_capital_per_day"] == 0.075
    assert len(result["grids"]) == 2
    assert "P&L realizado" in result["comparison_note"]


def test_unknown_pnl_and_nonpositive_duration_are_excluded_and_counted(monkeypatch):
    result = _summary(monkeypatch, [None, 10], [5, 10], zero_day_ids={2})
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["n_cohort"] == 0
    assert pair["excluded_cohort"] == 2
    assert pair["diff_pct_per_day"] is None
    assert pair["reason"] == "muestra insuficiente"


def test_two_hour_grid_uses_one_day_floor_for_cohort_metric(monkeypatch):
    result = _summary(monkeypatch, [10], [0], duration_days_by_id={1: 2 / 24})
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["diff_pct_per_day"] == 1.0


def test_three_day_grid_uses_actual_three_days_for_cohort_metric(monkeypatch):
    result = _summary(monkeypatch, [30], [0], duration_days_by_id={1: 3})
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["diff_pct_per_day"] == 1.0


def test_separated_cohorts_without_actual_loan_are_not_conclusive(monkeypatch):
    result = _summary(monkeypatch, [100] * 10, [0] * 10)
    pair = result["cohort_comparisons"]["loans_vs_control"]
    assert pair["conclusive"] is False
    assert pair["reason"] == "sin préstamos creados"
