from __future__ import annotations

import numpy as np
from decimal import Decimal
import hashlib
from pathlib import Path
from datetime import timezone
import pytest

from grid.sim.data import CandleData, ewma_sigma_24h
from grid.sim.data import load_candles
from grid.sim.runner import run_simulation
from grid.sim.exchange import SimExchange, SimInsufficientFunds
from tests.sim_replay_adapter import CandleFakeExchange, run_smart_engine
from data.testnet_client import TestnetOrderError


CAPITAL = 12000
LOW, HIGH, N = 94.0, 106.0, 6


def candles_for(close, lows=None, highs=None):
    close = np.asarray(close, dtype=float)
    return CandleData(np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100,
                      close.copy(),
                      close.copy() if highs is None else np.asarray(highs, dtype=float),
                      close.copy() if lows is None else np.asarray(lows, dtype=float),
                      close.copy(), 0)


def smart_pair(candles, *, params=None, sigma_scale=1.0, halflife_h=72.0,
               n=N, capital=CAPITAL, low=LOW, high=HIGH, resync_candles=3,
               sigma_values=None, csv_hash=None):
    sigma = (ewma_sigma_24h(candles.close, halflife_h) * sigma_scale
             if sigma_values is None else np.asarray(
                 [np.nan if value is None else float(value) for value in sigma_values], dtype=float))
    fake_filters = CandleFakeExchange().filters
    sim_trace = []
    sim = run_simulation(candles, strategy="smart", n=n, capital=capital,
                         low=low, high=high, params=params, fee_pct=.1, fee_asset="XRP",
                         filters=fake_filters, halflife_h=halflife_h,
                         sigma_scale=sigma_scale, resync_candles=resync_candles,
                         include_details=True, trace_callback=sim_trace.append,
                         sigma_values=sigma_values, csv_hash=csv_hash)
    real = run_smart_engine(candles, sigma, n=n, capital=capital, low=low, high=high,
                            params=params, fee_pct=.1, resync_candles=resync_candles)
    return real, sim, sim_trace


def canonical_sim_decisions(snapshot):
    return [(event["type"], tuple(event.get("reason") or ()))
            for event in snapshot["events"]]


def first_divergence(real_snapshots, sim_snapshots):
    def numeric_diff(left, right):
        return abs(float(left) - float(right)) > 1e-8

    event_map = {"GRID_PAUSED": "PAUSE", "GRID_RESUMED": "RESUME",
                 "GRID_ADJUSTED": "ADJUST", "ADJUST_BLOCKED": "ADJUST_REJECTED",
                 "GRID_AUTO_CLOSE": "CLOSE_REPOSITORY", "CELL_STOPLOSS": "STOP_LOSS"}
    fields = ("state", "price", "sell_price", "held_qty", "capital", "cycles_completed")
    for index, (real, sim) in enumerate(zip(real_snapshots, sim_snapshots)):
        for key in ("status", "range_low", "range_high", "n_levels"):
            left, right = real[key], sim[key]
            if key in {"range_low", "range_high"}:
                different = numeric_diff(left, right)
            else:
                different = left != right
            if different:
                return {"pass": index, "candle": real["candle"], "field": key,
                        "engine": left, "simulator": right}
        left, right = real["sigma_24h"], sim["sigma_24h"]
        if left is None or right is None:
            if left is not right:
                return {"pass": index, "candle": real["candle"], "field": "sigma_24h",
                        "engine": left, "simulator": right}
        elif numeric_diff(left, right):
            return {"pass": index, "candle": real["candle"], "field": "sigma_24h",
                    "engine": left, "simulator": right}
        real_cells = {int(row["level_idx"]): row for row in real["cells"]}
        sim_cells = {int(row["level_idx"]): row for row in sim["cells"]}
        if set(real_cells) != set(sim_cells):
            return {"pass": index, "candle": real["candle"], "field": "cell_ids",
                    "engine": sorted(real_cells), "simulator": sorted(sim_cells)}
        for level_idx in sorted(real_cells):
            for key in fields:
                left, right = real_cells[level_idx][key], sim_cells[level_idx][key]
                different = numeric_diff(left, right) if key not in {"state", "cycles_completed"} else left != right
                if different:
                    return {"pass": index, "candle": real["candle"],
                            "field": f"cell[{level_idx}].{key}", "engine": left, "simulator": right}
        real_orders = sorted((int(row["cell"]), row["side"], Decimal(str(row["price"])),
                              Decimal(str(row["qty"]))) for row in real["orders"])
        sim_orders = sorted((int(row["cell"]), row["side"], Decimal(str(row["price"])),
                             Decimal(str(row["qty"]))) for row in sim["orders"])
        if real_orders != sim_orders:
            return {"pass": index, "candle": real["candle"], "field": "open_orders",
                    "engine": real_orders, "simulator": sim_orders}
        if numeric_diff(real["equity"], sim["equity"]):
            return {"pass": index, "candle": real["candle"], "field": "equity",
                    "engine": real["equity"], "simulator": sim["equity"]}
        for key in ("fees",):
            if numeric_diff(real[key], sim[key]):
                return {"pass": index, "candle": real["candle"], "field": key,
                        "engine": real[key], "simulator": sim[key]}
        for key in ("USDT", "XRP"):
            if numeric_diff(real["balances"][key], sim["balances"][key]):
                return {"pass": index, "candle": real["candle"],
                        "field": f"balance.{key}", "engine": real["balances"][key],
                        "simulator": sim["balances"][key]}
        def motives(reason):
            if reason is None or reason == "policy_adjust":
                return ()
            if isinstance(reason, (tuple, list)):
                return tuple(reason)
            return tuple(str(reason).split(","))

        engine_decisions = [(event_map[event["type"]], motives(
                                (event.get("details") or {}).get("reasons", event.get("reason"))))
                            for event in real["decisions"] if event["type"] in event_map]
        sim_decisions = [(event["type"], motives(event.get("reason"))) for event in sim["events"]]
        if engine_decisions != sim_decisions:
            return {"pass": index, "candle": real["candle"], "field": "decisions",
                    "engine": engine_decisions, "simulator": sim_decisions}
    return None


def test_smart_monitor_clock_and_sigma_are_injected_at_each_replay_pass():
    candles = candles_for(np.full(10, 100.0))
    sigma = ewma_sigma_24h(candles.close)
    seen = []
    real = run_smart_engine(candles, sigma, params={"adjust_enabled": False},
                            trace_callback=seen.append)
    assert [row["candle"] for row in seen] == [0, 3, 6, 9]
    assert [row["timestamp"] for row in seen] == [int(candles.timestamp[i]) for i in (0, 3, 6, 9)]
    assert all(row["status"] == "ACTIVE" for row in seen)
    assert all(row["candle_index"] == row["candle"] for row in seen)
    assert all(row["sigma_24h"] == float(sigma[row["candle"]]) for row in seen)
    assert real["snapshots"] == seen


def test_candle_replay_exchange_reserves_usdt_and_rejects_unfunded_buy():
    exchange = CandleFakeExchange(fee_rate="0.001", fee_asset="USDT")
    exchange.move_price(10, 10.01, 10)
    exchange.initial_usdt = Decimal("20.1")
    exchange.free_usdt = Decimal("20.1")
    first = exchange.place_order("XRPUSDT", "BUY", Decimal("1"), Decimal("10"),
                                 client_order_id="g71L0B0")
    second = exchange.place_order("XRPUSDT", "BUY", Decimal("1"), Decimal("10"),
                                  client_order_id="g71L1B0")
    balance = exchange.get_balance("USDT")["USDT"]
    assert Decimal(str(balance["free"])) == Decimal("0.08")
    assert Decimal(str(balance["locked"])) == Decimal("20.02")
    with pytest.raises(TestnetOrderError, match="insufficient") as rejected:
        exchange.place_order("XRPUSDT", "BUY", Decimal("1"), Decimal("0.1"),
                             client_order_id="g71L2B0")
    assert rejected.value.code == -2010
    exchange.cancel_order("XRPUSDT", second["order_id"])
    balance = exchange.get_balance("USDT")["USDT"]
    assert Decimal(str(balance["free"])) > 0
    assert exchange.get_order("XRPUSDT", order_id=first["order_id"])["status"] == "NEW"


def test_simulator_funds_rejection_emits_event_and_leaves_cell_idle(monkeypatch):
    original = SimExchange.place

    def reject_cell_zero(self, cell, side, price, qty, active_from, created_at):
        if int(cell) == 0 and side == "BUY":
            raise SimInsufficientFunds("simulated USDT balance insufficient")
        return original(self, cell, side, price, qty, active_from, created_at)

    monkeypatch.setattr(SimExchange, "place", reject_cell_zero)
    candles = candles_for(np.full(12, 100.0))
    result = run_simulation(candles, strategy="simple", n=6, capital=12000,
                            low=94, high=106, include_details=True)
    rejected = [event for event in result["events"]
                if event.get("type") == "BUY_REJECTED" and event.get("level_idx") == 0]
    cell = next(item for item in result["details"]["cells"] if item["level_idx"] == 0)
    assert rejected
    assert cell["state"] == "IDLE"


def test_smart_adjust_preserves_existing_live_sell_order():
    close = [100, 96, 96, 96, 96, 96, 96, 96, 96, 96]
    lows, highs = close.copy(), close.copy()
    lows[1], highs[2] = 95.9, 96.1
    candles = candles_for(close, lows=lows, highs=highs)
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "stop_loss_pct": 50, "adjust_trigger_z": .75,
              "adjust_cooldown_h": 0, "adjust_n": 8}
    sigma = np.full(len(close), .001)
    sigma[0] = 0
    sigma[6] = .2
    real, sim, trace = smart_pair(candles, params=params, sigma_values=sigma)
    assert any(event.get("event_type") == "GRID_ADJUSTED" for event in real["events"])
    assert any(event.get("type") == "ADJUST" for event in sim["events"])
    adjusted = min((event for event in real["events"] if event.get("event_type") == "GRID_ADJUSTED"),
                   key=lambda event: event["ts"])
    adjusted_value = adjusted["ts"]
    adjusted_ts = (int((adjusted_value.replace(tzinfo=timezone.utc)
                        if adjusted_value.tzinfo is None else adjusted_value.astimezone(timezone.utc)).timestamp())
                   if hasattr(adjusted_value, "timestamp") else int(adjusted_value))
    after = min((row for row in real["snapshots"] if row["timestamp"] >= adjusted_ts),
                key=lambda row: row["timestamp"])
    assert after["n_levels"] == 8
    prior_sell_events = {(int(event["level_idx"]), int(event["order_id"]))
                         for event in real["events"]
                         if event.get("event_type") == "SELL_PLACED"
                         and int(event["id"]) < int(adjusted["id"])}
    sell_ids_after = {(int(order["cell"]), int(order["order_id"]))
                      for order in after["orders"] if order["side"] == "SELL"}
    preserved = prior_sell_events & sell_ids_after
    assert preserved, "ADJUST did not preserve an existing SELL order identity"
    for idx, _order_id in preserved:
        assert not any(
            event.get("event_type") == "SELL_PLACED"
            and int(event.get("level_idx", -1)) == idx
            and int(event.get("id", 0)) > int(adjusted["id"])
            and ((int((event["ts"].replace(tzinfo=timezone.utc)
                       if event["ts"].tzinfo is None else event["ts"].astimezone(timezone.utc)).timestamp())
                  if hasattr(event.get("ts"), "timestamp") else int(event.get("ts", 0))) == adjusted_ts)
            for event in real["events"])
    trace_after = min((row for row in trace if row["timestamp"] >= adjusted_ts),
                      key=lambda row: row["timestamp"])
    assert any(order["side"] == "SELL" and int(order["created_at"]) < int(trace_after["candle"])
               for order in trace_after["orders"]), "simulator replaced a live SELL during ADJUST"
    divergence = first_divergence(real["snapshots"], trace)
    assert divergence is None, f"first divergence={divergence}"
    # Every held cell keeps its protective sell through range repositioning.
    for row in real["snapshots"]:
        for cell in row["cells"]:
            if cell["state"] == "SELL_OPEN":
                assert any(order["side"] == "SELL" and order["price"] == cell["sell_price"]
                           for order in row["orders"])
    assert trace


def test_smart_pause_resume_hysteresis_keeps_sells_and_does_not_rebuy():
    close = np.array([100, 100, 100, 96, 96, 96, 96, 96, 96, 96, 96, 96], dtype=float)
    lows = close.copy()
    highs = close.copy()
    lows[1] = 95.9
    highs[2] = 96.1
    candles = candles_for(close, lows, highs)
    params = {"pause_enter_prob": .1, "pause_exit_prob": .05,
              "adjust_enabled": False, "stop_loss_pct": 50}
    real, sim, trace = smart_pair(candles, params=params, sigma_scale=10.0, halflife_h=.005)
    divergence = first_divergence(real["snapshots"], trace)
    assert divergence is None, f"first divergence={divergence}"
    real_actions = [e["event_type"] for e in real["events"]]
    sim_actions = [e["type"] for e in sim["events"]]
    assert "GRID_PAUSED" in real_actions
    assert "PAUSE" in sim_actions
    assert "GRID_RESUMED" in real_actions
    assert "RESUME" in sim_actions
    placed_buys = [(int(event["level_idx"]), event.get("client_order_id"))
                   for event in real["events"] if event.get("event_type") == "BUY_PLACED"]
    for idx in {item[0] for item in placed_buys}:
        cids = [cid for level_idx, cid in placed_buys if level_idx == idx]
        assert len(cids) == len(set(cids)), f"reused BUY CID for cell {idx}: {cids}"
    for row in real["snapshots"]:
        owners = {int(cell["level_idx"]): cell for cell in row["cells"]}
        for order in row["orders"]:
            owner = owners[int(order["cell"])]
            assert owner["state"] in {"BUY_OPEN", "SELL_OPEN"}
            assert owner["state"].startswith(order["side"])
        if row["status"] == "PAUSED":
            assert not any(order["side"] == "BUY" for order in row["orders"])
            for cell in row["cells"]:
                if cell["state"] == "SELL_OPEN":
                    assert any(order["side"] == "SELL" for order in row["orders"])


def test_pause_execution_cancels_buys_and_keeps_existing_sells():
    close = np.array([100, 100, 100, 96, 96, 96, 96], dtype=float)
    lows, highs = close.copy(), close.copy()
    lows[1], highs[2] = 95.9, 96.1
    candles = candles_for(close, lows, highs)
    params = {"pause_enter_prob": .1, "pause_exit_prob": .05,
              "adjust_enabled": False, "stop_loss_pct": 50}
    real, sim, trace = smart_pair(candles, params=params, sigma_scale=10, halflife_h=.005)
    sim_paused = next(row for row in trace if row["status"] == "PAUSED")
    assert not any(order["side"] == "BUY" for order in sim_paused["orders"])
    assert any(order["side"] == "SELL" for order in sim_paused["orders"])
    assert any(row["status"] == "PAUSED" for row in real["snapshots"])


def test_sigma_provider_and_simulator_use_current_pass_not_lagged_value():
    candles = candles_for(np.full(12, 100.0))
    sigma = np.asarray([.01, .02, .03, .4, .5, .6, .7, .8, .9, 1.0, 1.1, 1.2])
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "adjust_enabled": False, "stop_loss_pct": 50}
    real, _sim, trace = smart_pair(candles, params=params, sigma_values=sigma)
    assert [row["sigma_24h"] for row in real["snapshots"]] == [sigma[i] for i in (0, 3, 6, 9)]
    assert [row["sigma_24h"] for row in trace] == [sigma[i] for i in (0, 3, 6, 9)]
    assert first_divergence(real["snapshots"], trace) is None


def test_smart_close_values_repository_inventory_at_market():
    close = np.array([100, 96, 100, 100, 88, 88, 88, 90, 90, 90], dtype=float)
    lows = close.copy()
    lows[1] = 95.9
    candles = candles_for(close, lows=lows)
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "adjust_enabled": False, "stop_loss_pct": 50}
    real, sim, trace = smart_pair(candles, params=params)
    holding = [row for row in real["snapshots"] if row["status"] == "HOLDING"]
    sim_holding = [row for row in trace if row["status"] == "HOLDING"]
    assert holding and sim_holding
    assert holding[-1]["equity"] == sim_holding[-1]["equity"]
    assert holding[0]["equity"] != holding[-1]["equity"]
    assert not any(order["side"] == "BUY" for order in sim_holding[0]["orders"])
    divergence = first_divergence(real["snapshots"], trace)
    assert divergence is None, f"first divergence={divergence}"


def test_smart_close_cancels_pending_buy_orders_and_records_each_cancellation():
    close = np.array([100, 120, 120, 120, 120], dtype=float)
    candles = candles_for(close)
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "adjust_enabled": False, "stop_loss_pct": 50,
              "close_out_of_range_pct": 5}
    real, sim, trace = smart_pair(candles, params=params, low=90, high=110,
                                  sigma_values=np.full(len(close), .01))
    closing = next(event for event in sim["events"] if event.get("type") == "CLOSE_REPOSITORY")
    prior = max((row for row in trace if row["timestamp"] < int(closing["ts"])),
                key=lambda row: row["timestamp"])
    after = min((row for row in trace if row["timestamp"] >= int(closing["ts"])),
                key=lambda row: row["timestamp"])
    pending = [order for order in prior["orders"] if order["side"] == "BUY"]
    assert pending, "CLOSE scenario must begin with at least one pending BUY"
    assert not any(order["side"] == "BUY" for order in after["orders"])
    canceled_ids = {int(event["order_id"]) for event in sim["events"]
                    if event.get("type") == "BUY_CANCELED"
                    and event.get("reason") == "CLOSE_REPOSITORY"}
    assert {int(order["order_id"]) for order in pending} <= canceled_ids
    assert all(real["exchange"].orders[int(order["order_id"])]["status"] == "CANCELED"
               for order in real["snapshots"][0]["orders"] if order["side"] == "BUY")


@pytest.mark.parametrize(("reason", "params", "capital", "with_inventory"), [
    ("cooldown", {"adjust_cooldown_h": 6}, CAPITAL, False),
    ("trapped_capital_pct", {"trapped_age_h": .01, "adjust_trapped_cap_pct": 5}, CAPITAL, True),
    ("capital_per_cell_below_min_notional_margin", {"adjust_n": 100}, 600, True),
    ("planned_cell_below_binance_minimum_margin", {"adjust_n": 50}, 600, False),
])
def test_adjust_rejections_match_engine_and_simulator(reason, params, capital, with_inventory):
    close = np.full(12, 100.0)
    close[3:] = 135.0
    low, high = 60.0, 140.0
    if reason == "cooldown":
        close[6:] = 190.0
    lows = close.copy()
    if reason == "trapped_capital_pct":
        low, high = 94.0, 106.0
        close[1:3] = 96.0
        lows[1] = 95.9
        close[3:] = 103.0
    elif reason == "capital_per_cell_below_min_notional_margin":
        low, high = 60.0, 120.0
        close[1:3] = 96.0
        lows[1] = 89.9
        close[3:] = 120.0
    elif reason == "planned_cell_below_binance_minimum_margin":
        low, high = 60.0, 120.0
        close[3:] = 120.0
    candles = candles_for(close, lows=lows)
    sigma = np.zeros(len(close))
    sigma[3:] = .2
    effective = {"pause_enter_prob": None, "pause_exit_prob": None,
                 "adjust_trigger_z": .75, "adjust_cooldown_h": 0,
                 "stop_loss_pct": 50, **params}
    real, sim, _trace = smart_pair(candles, params=effective, sigma_values=sigma,
                                   capital=capital, low=low, high=high)
    engine_reasons = [str(event.get("reason")) for event in real["events"]
                      if event.get("event_type") == "ADJUST_BLOCKED"]
    sim_reasons = [(",".join(event.get("reason") or []) if isinstance(event.get("reason"), list)
                    else str(event.get("reason") or "")) for event in sim["events"]
                   if event.get("type") == "ADJUST_REJECTED"]
    assert reason in engine_reasons, f"engine blocked reasons={engine_reasons}"
    assert reason in sim_reasons, f"sim blocked reasons={sim_reasons}"


def test_unavailable_sigma_is_reported_by_both_replays():
    candles = candles_for(np.full(10, 100.0))
    sigma = np.full(len(candles.close), .1)
    sigma[3] = np.nan
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "stop_loss_pct": 50}
    real, sim, _trace = smart_pair(candles, params=params, sigma_values=sigma)
    assert real["snapshots"]
    assert any(event.get("type") == "ADJUST_REJECTED"
               and "vol_unavailable" in (event.get("reason") or []) for event in sim["events"])


def test_pause_max_duration_closes_inventory_to_repository():
    close = np.array([100, 96, 100, 96, 96, 96, 96, 96, 96], dtype=float)
    lows = close.copy()
    lows[1] = 95.9
    candles = candles_for(close, lows=lows)
    sigma = np.full(len(close), .2)
    sigma[0] = 0
    params = {"pause_enter_prob": .1, "pause_exit_prob": .05,
              "pause_max_h": .05, "adjust_enabled": False, "stop_loss_pct": 50}
    real, sim, trace = smart_pair(candles, params=params, sigma_values=sigma)
    assert any(event.get("event_type") == "GRID_AUTO_CLOSE"
               and "pause_max_h" in (event.get("reason") or "") for event in real["events"])
    assert any(event.get("type") == "CLOSE_REPOSITORY"
               and "pause_max_h" in (event.get("reason") or []) for event in sim["events"])
    assert first_divergence(real["snapshots"], trace) is None


def test_stoploss_executes_before_policy_and_matches_fee_and_cell_state():
    close = np.array([100, 96, 96, 96, 96, 90, 90, 90, 90, 90], dtype=float)
    lows = close.copy()
    lows[1] = 95.9
    candles = candles_for(close, lows=lows)
    sigma = np.full(len(close), .01)
    sigma[0] = 0
    params = {"pause_enter_prob": None, "pause_exit_prob": None,
              "adjust_enabled": False, "stop_loss_pct": 5}
    real, sim, trace = smart_pair(candles, params=params, sigma_values=sigma)
    stop_events = sorted((event for event in real["events"]
                          if event.get("event_type") == "CELL_STOPLOSS"),
                         key=lambda event: int(event["id"]))
    sim_stops = [event for event in sim["events"] if event.get("type") == "STOP_LOSS"]
    assert stop_events and sim_stops
    assert [int(event["level_idx"]) for event in stop_events] == [
        int(event["level_idx"]) for event in sim_stops]
    assert first_divergence(real["snapshots"], trace) is None
    operation_order = next(row["execution_order"] for row in real["snapshots"]
                           if any(item[0] == "stoploss" for item in row["execution_order"]))
    assert next(i for i, item in enumerate(operation_order) if item[0] == "stoploss") < next(
        i for i, item in enumerate(operation_order) if item[0] == "sync")
    stop_ts = sim_stops[0]["ts"]
    same_pass = [event["type"] for event in sim["events"] if event.get("ts") == stop_ts
                 and event.get("type") in {"STOP_LOSS", "PAUSE", "RESUME", "ADJUST",
                                             "ADJUST_REJECTED", "CLOSE_REPOSITORY"}]
    assert same_pass.index("STOP_LOSS") < min((same_pass.index(kind) for kind in
                          ("PAUSE", "RESUME", "ADJUST", "CLOSE_REPOSITORY") if kind in same_pass),
                          default=len(same_pass))


def test_combined_360_candle_sequence_adjust_pause_resume_stoploss():
    close = np.full(360, 100.0)
    close[1:3] = 96.0
    close[3:6] = 95.0
    close[6:12] = 94.0
    close[12:] = 90.0
    lows, highs = close.copy(), close.copy()
    lows[1] = 95.9
    candles = candles_for(close, lows=lows, highs=highs)
    params = {"pause_enter_prob": .1, "pause_exit_prob": .05,
              "adjust_cooldown_h": 12, "stop_loss_pct": 5}
    real, sim, trace = smart_pair(candles, params=params, halflife_h=.005)
    sim_actions = [event["type"] for event in sim["events"]]
    engine_actions = [event["event_type"] for event in real["events"]]
    assert "ADJUST" in sim_actions and "GRID_ADJUSTED" in engine_actions
    assert "PAUSE" in sim_actions and "GRID_PAUSED" in engine_actions
    assert "RESUME" in sim_actions and "GRID_RESUMED" in engine_actions
    assert "STOP_LOSS" in sim_actions and "CELL_STOPLOSS" in engine_actions
    assert first_divergence(real["snapshots"], trace) is None


def test_real_two_day_xrp_segment_has_smart_intervention():
    path = Path("data/cache/xrp_5m.csv")
    if not path.exists():
        pytest.skip("falta data/cache/xrp_5m.csv; escenario real g no disponible")
    raw = load_candles(path)
    assert len(raw.timestamp) >= 576, "CSV real tiene menos de dos días completos"
    start = len(raw.timestamp) - 576
    timestamps = raw.timestamp[start:]
    assert np.all(np.diff(timestamps) == 300), "segmento real de dos días con huecos"
    factor = 100.0
    candles = CandleData(timestamps.copy(), raw.open[start:] * factor,
                         raw.high[start:] * factor, raw.low[start:] * factor,
                         raw.close[start:] * factor, 0)
    low, high = float(candles.low.min()), float(candles.high.max())
    params = {"stop_loss_pct": 50}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    real, sim, trace = smart_pair(candles, params=params, low=low, high=high,
                                  csv_hash=digest)
    sim_interventions = sim["metrics"]["interventions_by_type"]
    engine_interventions = {kind: sum(event.get("event_type") == kind for event in real["events"])
                            for kind in ("GRID_ADJUSTED", "GRID_PAUSED", "GRID_RESUMED")}
    assert any(sim_interventions.get(kind, 0) for kind in ("ADJUST", "PAUSE")), \
        "CSV real segment produced no ADJUST or PAUSE"
    assert any(engine_interventions.values()), "real engine produced no ADJUST or PAUSE"
    assert len(digest) == 64
    divergence = first_divergence(real["snapshots"], trace)
    print(f"scenario_g dates={timestamps[0]}..{timestamps[-1]} sha256={digest} "
          f"passes={len(trace)} first_divergence={divergence}")
    assert divergence is None, f"first divergence={divergence}"
