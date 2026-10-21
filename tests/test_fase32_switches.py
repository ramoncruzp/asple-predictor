from datetime import datetime, timedelta, timezone

import numpy as np

from config.settings import ADVISOR_VOL_SPACING_K, APP_FLAG_DEFINITIONS
from grid.policy import DEFAULT_SMART_PARAMS, adjust_decision, evaluate_grid
from grid.sim.data import CandleData
from grid.sim.runner import FILTERS, run_simulation
from grid.structure import suggest_structure

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _cells():
    return [{"level_idx": i, "state": "IDLE", "held_qty": 0, "entry_price": None,
             "bought_at": None, "pnl": 0, "cycles_completed": 0} for i in range(8)]


def test_new_global_flags_have_live_defaults_and_restart_metadata():
    expected = {"smart_adjust_enabled": True, "smart_close_wait_enabled": False,
                "advisor_vol_spacing_enabled": False}
    for key, default in expected.items():
        assert APP_FLAG_DEFINITIONS[key]["default"] is default
        assert APP_FLAG_DEFINITIONS[key]["requires_restart"] is False
        assert APP_FLAG_DEFINITIONS[key]["description"]


def test_close_wait_policy_pauses_and_resumes_only_inside_range():
    params = {**DEFAULT_SMART_PARAMS, "out_of_range_mode": "wait", "pause_enter_prob": None}
    outside = evaluate_grid("ACTIVE", params, _cells(), 116, 90, 110, 100, None, None, NOW)
    assert (outside.action, outside.reasons) == ("PAUSE", ("out_of_range_wait",))
    still_outside = evaluate_grid("PAUSED", params, _cells(), 116, 90, 110, 100, None,
        NOW - timedelta(hours=100), NOW, ("out_of_range_wait",))
    assert still_outside.action == "NONE"
    assert "pause_max_h" not in still_outside.reasons
    inside = evaluate_grid("PAUSED", params, _cells(), 100, 90, 110, 100, None,
        NOW - timedelta(hours=100), NOW, ("out_of_range_wait",))
    assert inside.action == "RESUME"
    default = evaluate_grid("ACTIVE", DEFAULT_SMART_PARAMS, _cells(), 116, 90, 110, 100,
                            None, None, NOW)
    assert (default.action, default.reasons) == ("CLOSE_REPOSITORY", ("out_of_range",))


def test_close_wait_keeps_max_loss_and_disables_outside_buffer_adjust():
    params = {**DEFAULT_SMART_PARAMS, "out_of_range_mode": "wait", "pause_enter_prob": None}
    losing = [{**_cells()[0], "state": "SELL_OPEN", "held_qty": 1,
               "entry_price": 120, "bought_at": NOW}]
    closed = evaluate_grid("ACTIVE", params, losing, 100, 90, 110, 100, None, None, NOW)
    assert closed.action == "CLOSE_REPOSITORY" and "max_loss_pct" in closed.reasons
    grid = {"strategy": "smart", "status": "ACTIVE", "params": {**DEFAULT_SMART_PARAMS,
            "out_of_range_mode": "wait"}, "n_levels": 8, "range_low": 90,
            "range_high": 110, "capital_total": 100}
    no_adjust = adjust_decision(grid, _cells(), 114, .02, NOW, None)
    assert no_adjust.action == "NONE"


def test_pause_max_age_waits_for_break_prob_pause_but_close_mode_still_closes():
    params = {**DEFAULT_SMART_PARAMS, "pause_enter_prob": 0.01,
              "pause_exit_prob": 0.001, "out_of_range_mode": "wait"}
    started = evaluate_grid("ACTIVE", params, _cells(), 0.2081, 0.1938, 0.2030,
                            1000, 0.02, None, NOW - timedelta(hours=49))
    assert (started.action, started.reasons) == ("PAUSE", ("break_prob",))
    waiting = evaluate_grid("PAUSED", params, _cells(), 0.2012, 0.1938, 0.2030,
        1000, 0.02, NOW - timedelta(hours=49), NOW, ("break_prob",))
    assert waiting.action != "CLOSE_REPOSITORY"
    assert "pause_max_h" not in waiting.reasons

    close_params = {**params, "out_of_range_mode": "close"}
    closed = evaluate_grid("PAUSED", close_params, _cells(), 0.2012, 0.1938, 0.2030,
        1000, 0.02, NOW - timedelta(hours=49), NOW, ("break_prob",))
    assert (closed.action, closed.reasons) == ("CLOSE_REPOSITORY", ("pause_max_h",))


def test_sim_global_adjust_switch_skips_both_adjust_sources(monkeypatch):
    import grid.sim.runner as runner
    calls = []
    monkeypatch.setattr(runner, "adjust_decision", lambda *a, **k:
        (calls.append("adjust") or runner.PolicyDecision("NONE", (), {})))
    candles = CandleData(np.arange(12, dtype=np.int64)*300+1_700_000_100,
                        np.full(12, 100.), np.full(12, 101.), np.full(12, 99.), np.full(12, 100.), 0)
    run_simulation(candles, strategy="smart", n=6, capital=12000, low=90, high=110,
                   sigma_values=[.02]*12, adjust_enabled_global=False)
    assert calls == []


def test_monitor_global_adjust_off_skips_decision_and_emits_once(monkeypatch):
    from grid import monitor as monitor_module
    from grid.monitor import GridMonitor
    from tests.test_grid_engine import make_engine
    from tests.test_grid_monitor import monitor_settings
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 10, strategy="smart",
                              params={"adjust_enabled": True})
    settings = monitor_settings()
    settings.smart_adjust_enabled = False
    provider = type("Provider", (), {"get": lambda *_a, **_k:
        type("View", (), {"sigma_24h": .02, "source": "model", "fallback": False})()})()
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=provider)
    calls = []
    monkeypatch.setattr(monitor_module, "adjust_decision", lambda *a, **k:
        (calls.append(1) or monitor_module.PolicyDecision("NONE", (), {})))
    assert monitor.run_once()["status"] == "OK"
    assert monitor.run_once()["status"] == "OK"
    events = db.list_grid_events(grid_id=grid["id"], event_type="ADJUST_SUSPENDED")
    assert calls == []
    assert len(events) == 1 and events[0]["reason"] == "global_switch_off"
    assert events[0]["details"] == {"reason": "global_switch_off"}
    settings.smart_adjust_enabled = True
    assert monitor.run_once()["status"] == "OK"
    assert calls


def test_monitor_close_wait_switch_passes_pause_reason_and_keeps_grid_live():
    from decimal import Decimal
    from grid.monitor import GridMonitor
    from tests.test_grid_engine import make_engine
    from tests.test_grid_monitor import monitor_settings
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 10, strategy="smart",
        params={"pause_enter_prob": None, "adjust_enabled": False})
    exchange.get_book_ticker = lambda _symbol: {
        "bid_price": Decimal("120"), "ask_price": Decimal("120.01"),
        "bid_qty": Decimal("100"), "ask_qty": Decimal("100")}
    settings = monitor_settings()
    settings.smart_close_wait_enabled = True
    settings.smart_adjust_enabled = False
    provider = type("Provider", (), {"get": lambda *_a, **_k:
        type("View", (), {"sigma_24h": .02, "source": "model", "fallback": False})()})()
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=provider)
    result = monitor.run_once()
    current = db.get_grid(grid["id"])
    assert result["status"] == "OK"
    assert current["status"] == "PAUSED"
    paused = db.get_last_event(grid["id"], "GRID_PAUSED")
    assert paused["reason"] == "out_of_range_wait"
    assert "out_of_range_wait" in paused["details"]["reasons"]


def test_sim_close_wait_pauses_then_resumes_in_range():
    close = np.array([100.]*3 + [120.]*3 + [100.]*6)
    ts = np.arange(len(close), dtype=np.int64)*300 + 1_700_000_100
    candles = CandleData(ts, close, close, close, close, 0)
    result = run_simulation(candles, strategy="smart", n=4, capital=1000, low=95, high=105,
        params={"adjust_enabled": False, "pause_enter_prob": None}, sigma_values=[.02]*len(close),
        close_wait_enabled=True)
    pauses = [event for event in result["events"] if event["type"] == "PAUSE"]
    assert pauses and "out_of_range_wait" in pauses[0]["reason"]
    assert any(event["type"] == "RESUME" for event in result["events"])
    assert not any(event["type"] == "CLOSE_REPOSITORY" for event in result["events"])


def test_sim_close_wait_does_not_close_after_break_prob_pause_max_age():
    close = np.array([0.2012] + [0.2081] * 49 + [0.2012])
    timestamps = np.arange(len(close), dtype=np.int64) * 3600 + 1_700_000_100
    candles = CandleData(timestamps, close, close, close, close, 0)
    result = run_simulation(candles, strategy="smart", n=5, capital=1000,
        low=0.1938, high=0.2030, resync_candles=1,
        params={"adjust_enabled": False, "pause_enter_prob": 0.01,
                "pause_exit_prob": 0.001}, sigma_values=[0.02] * len(close),
        adjust_enabled_global=False, stoploss_enabled=False, close_wait_enabled=True)
    pauses = [event for event in result["events"] if event["type"] == "PAUSE"]
    assert pauses and pauses[0]["reason"] == ["break_prob"]
    assert not any(event["type"] == "CLOSE_REPOSITORY" for event in result["events"])


def test_vol_spacing_structure_uses_sigma24_formula_only_when_enabled():
    common = (0.03, 1000, 100, FILTERS, .1)
    baseline = suggest_structure(*common)
    spaced = suggest_structure(*common, vol_spacing_enabled=True)
    expected_min = ADVISOR_VOL_SPACING_K * .03 * 100
    assert spaced["spacing_pct"] >= expected_min - 1e-9
    assert spaced["n_levels"] < baseline["n_levels"]
    assert suggest_structure(*common, vol_spacing_enabled=False) == baseline
