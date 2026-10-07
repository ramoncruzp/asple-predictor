from datetime import datetime, timedelta, timezone

from grid.policy import DEFAULT_SMART_PARAMS, adjust_decision, validate_params


def _grid(params=None):
    return {"id": 7, "strategy": "smart", "status": "ACTIVE", "range_low": 90,
            "range_high": 110, "n_levels": 5, "capital_total": 1000,
            "params": {**DEFAULT_SMART_PARAMS, "horizon_h": 24, **(params or {})}}


def test_adjust_decision_triggers_near_edge_and_keeps_log_width_centered():
    now = datetime.now(timezone.utc)
    free = [{"state": "IDLE", "held_qty": 0}, {"state": "BUY_OPEN", "held_qty": 0}]
    decision = adjust_decision(_grid(), free, 90.5, 0.01, now, None)
    assert decision.action == "ADJUST"
    assert abs(decision.metrics["range_low"] * decision.metrics["range_high"] - 90.5**2) < 1e-7
    assert decision.metrics["n_levels"] == 5


def test_adjust_decision_obeys_cooldown_and_trapped_capital_guards():
    now = datetime.now(timezone.utc)
    cooldown = adjust_decision(_grid(), [], 90.5, 0.01, now, now - timedelta(hours=1))
    assert cooldown.action == "BLOCKED" and "cooldown" in cooldown.reasons
    cells = [{"state": "SELL_OPEN", "held_qty": 4, "entry_price": 100,
              "bought_at": now - timedelta(hours=30), "level_idx": 0}]
    trapped = adjust_decision(_grid(), cells, 90.5, 0.01, now, None)
    assert trapped.action == "BLOCKED" and "trapped_capital_pct" in trapped.reasons


def test_old_grid_without_adjust_flag_stays_disabled_and_params_validate():
    old = _grid()
    old["params"].pop("adjust_enabled")
    assert adjust_decision(old, [], 90.5, 0.01, datetime.now(timezone.utc), None).action == "NONE"
    assert validate_params({"adjust_n": 7}, 8)["adjust_n"] == 7
    for invalid in ({"adjust_trigger_z": 0}, {"adjust_cooldown_h": -1},
                    {"adjust_trapped_cap_pct": 0}, {"adjust_n": 3}):
        try:
            validate_params(invalid, 8)
        except ValueError:
            continue
        raise AssertionError(f"invalid adjustment params accepted: {invalid}")
