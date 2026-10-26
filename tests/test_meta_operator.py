import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from grid.meta_operator import (loss_reallocation_allowed, rank_alternatives,
                                profile_candidate_exclusion, signal_adjusted_probability,
                                summarize_replays, allocation_vectors, combine_aligned_replays)


def test_rotation_gate_uses_verified_pnl_and_recorded_trade_fees():
    from grid.meta_operator import actual_close_reallocation_allowed

    closed_at_loss = {"liquidated_cells": [{"pnl_realized": "-1.20",
        "buy_fee_usdt": "0.20", "sell_fee_usdt": "0.30"}]}
    allowed, costs = actual_close_reallocation_allowed(1.8, closed_at_loss, .25)
    assert allowed
    assert costs == {"realized_pnl_usdt": -1.2, "realized_loss_usdt": 1.2,
        "recorded_trade_fees_usdt": .5, "reopen_fee_estimate_usdt": .25}
    rejected, costs = actual_close_reallocation_allowed(1.4, closed_at_loss, .25)
    assert not rejected
    assert costs["recorded_trade_fees_usdt"] == .5


def test_meta_replay_reports_probability_time_drawdown_and_independent_windows():
    rows = [{"reached": index % 2 == 0, "days": 12 + index, "drawdown_pct": index,
             "step_days": 7} for index in range(15)]
    result = summarize_replays(rows, independent_window_days=30)
    assert result["rolling_windows"] == 15
    assert result["independent_windows"] == 3
    assert result["evidence_sufficient"] is True
    assert result["probability"] == pytest.approx(8 / 15)
    assert result["days_p10"] <= result["days_median"] <= result["days_p90"]
    assert result["worst_drawdown_pct"] == 14
    assert summarize_replays(rows, independent_window_days=45)["window_days"] == 45


def test_individual_bot_replay_sells_once_and_never_rebuys():
    import numpy as np
    from grid.sim.data import CandleData
    from grid.sim.meta_bot import run_meta_bot
    candles = CandleData(timestamp=np.array([0, 300, 600, 900]),
        open=np.array([10.0, 10.5, 9.0, 10.0]), high=np.array([10.2, 11.1, 12.0, 12.0]),
        low=np.array([9.5, 10.5, 9.0, 10.0]), close=np.array([10.0, 11.0, 10.0, 11.0]), gaps=0)
    result = run_meta_bot(candles, capital=100, buy_price=10, sell_price=11, target_usdt=5)
    assert result["reached"] is True
    assert result["state"] == "DONE"
    assert result["rebuys"] == 0
    assert result["final_inventory"] == 0
    assert result["bought"] and result["sold"] and not result["loss_cut"]


def test_meta_bot_loss_cut_sells_at_low_and_reports_realized_loss():
    import numpy as np
    from grid.sim.data import CandleData
    from grid.sim.meta_bot import run_meta_bot
    candles = CandleData(timestamp=np.array([0, 300, 600]),
        open=np.array([10.0, 10.0, 8.0]), high=np.array([10.1, 10.1, 8.2]),
        low=np.array([9.8, 9.8, 7.0]), close=np.array([10.0, 10.0, 8.0]), gaps=0)
    result = run_meta_bot(candles, capital=100, buy_price=9.9, sell_price=10.2,
        target_usdt=10, max_loss_pct=5, min_margin_pct=.3)
    assert result["loss_cut"] is True
    assert result["reached"] is False
    assert result["event"]["reason"] == "CORTADA_POR_PERDIDA"
    assert result["final_inventory"] == 0
    assert result["final_cash"] < 95
    assert result["pnl_usdt"] == pytest.approx(result["final_cash"] - 100)


def test_grid_replay_cuts_at_intracandle_low_with_realized_fees():
    import numpy as np
    from grid.sim.data import CandleData
    from grid.sim.runner import run_simulation
    candles = CandleData(timestamp=np.array([0, 300, 600, 900]),
        open=np.array([1.05, 1.05, .8, .8]), high=np.array([1.06, 1.06, .9, .81]),
        low=np.array([1.04, 1.04, .75, .79]), close=np.array([1.05, 1.05, .8, .8]), gaps=0)
    result = run_simulation(candles, strategy="simple", n=4, capital=100, low=.9, high=1.1,
        params={"target_usdt": 10, "target_basis": "cash"}, stoploss_enabled=False,
        meta_max_loss_pct=5)
    cuts = [event for event in result["events"] if event["type"] == "META_LOSS_CUT"]
    assert len(cuts) == result["metrics"]["loss_cut_windows"] == 1
    assert cuts[0]["reason"] == "CORTADA_POR_PERDIDA"
    assert float(cuts[0]["details"]["fees_usdt"]) > 0
    assert result["metrics"]["cash_final"] == pytest.approx(
        float(cuts[0]["details"]["realized_cash_usdt"]))
    assert result["metrics"]["cash_final"] - 100 < -5


def test_profiles_rank_zero_hit_replays_by_median_progress_then_time_and_drawdown():
    alternatives = [
        {"symbol": "LOW_PROGRESS", "estimate": {"probability": .95, "probability_replay": 0,
            "median_progress_pct": 35, "days_median": 2, "worst_drawdown_pct": 1}},
        {"symbol": "HIGH_PROGRESS", "estimate": {"probability": .05, "probability_replay": 0,
            "median_progress_pct": 60, "days_median": 8, "worst_drawdown_pct": 5}},
        {"symbol": "FASTER", "estimate": {"probability": .05, "probability_replay": 0,
            "median_progress_pct": 60, "days_median": 4, "worst_drawdown_pct": 5}},
        {"symbol": "LOWER_DD", "estimate": {"probability": .05, "probability_replay": 0,
            "median_progress_pct": 60, "days_median": 4, "worst_drawdown_pct": 2}},
    ]
    assert [row["symbol"] for row in rank_alternatives(alternatives)] == [
        "LOWER_DD", "FASTER", "HIGH_PROGRESS", "LOW_PROGRESS"]


def test_replay_probability_remains_the_primary_ranking_signal():
    rows = [
        {"symbol": "MORE_PROGRESS", "estimate": {"probability_replay": .4,
            "probability": .1, "median_progress_pct": 90, "days_median": 5}},
        {"symbol": "MORE_PROBABLE", "estimate": {"probability_replay": .5,
            "probability": .9, "median_progress_pct": 20, "days_median": 10}},
    ]
    assert rank_alternatives(rows)[0]["symbol"] == "MORE_PROBABLE"


def test_learned_and_real_ranking_share_all_tie_breakers():
    rows = [
        {"symbol": "NO_WINDOWS", "learned_probability": .9, "estimate": {
            "rolling_windows": 0, "probability_replay": .25, "median_progress_pct": 99}},
        {"symbol": "WITH_WINDOWS", "learned_probability": .25, "estimate": {
            "rolling_windows": 4, "probability_replay": .25, "median_progress_pct": 10,
            "days_median": 5, "worst_drawdown_pct": 1}},
    ]
    assert rank_alternatives(rows)[0]["symbol"] == "WITH_WINDOWS"
    assert rank_alternatives(rows, probability_key="learned_probability")[0]["symbol"] == "WITH_WINDOWS"


def test_learned_non_default_probability_can_change_shared_ranking():
    rows = [
        {"symbol": "BASE", "learned_probability": .2, "estimate": {
            "rolling_windows": 5, "probability_replay": .6, "median_progress_pct": 20,
            "days_median": 5, "worst_drawdown_pct": 1}},
        {"symbol": "LEARNED", "learned_probability": .7, "estimate": {
            "rolling_windows": 5, "probability_replay": .5, "median_progress_pct": 20,
            "days_median": 5, "worst_drawdown_pct": 1}},
    ]
    assert rank_alternatives(rows)[0]["symbol"] == "BASE"
    assert rank_alternatives(rows, probability_key="learned_probability")[0]["symbol"] == "LEARNED"


def test_public_binance_client_book_ticker_shape_is_supported():
    from grid.meta_operator import MetaOperator

    class RawClient:
        def get_orderbook_ticker(self, *, symbol):
            assert symbol == "XRPUSDT"
            return {"bidPrice": "1.2", "askPrice": "1.3"}

    operator = MetaOperator(object(), None, type("Scanner", (), {
        "client": type("BinanceClient", (), {"client": RawClient()})()})(), None, None)
    assert operator._book_ticker("XRPUSDT") == {
        "bidPrice": "1.2", "askPrice": "1.3", "bid_price": "1.2", "ask_price": "1.3"}


def test_daily_advisor_cache_uses_profile_reference_capital():
    from grid.meta_operator import MetaOperator

    calls = []
    def advisor(symbol, capital, margin, days):
        calls.append((symbol, capital, margin, days))
        return {"recommended_floor": 90, "recommended_ceiling": 110, "suggested_grids": 4}

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 100, "ask_price": 100}

    operator = MetaOperator(object(), object(), type("Scanner", (), {"client": Client()})(),
        None, None, advisor=advisor)
    operator._analysis_timing = {"advisor_s": 0, "replay_s": 0}
    operator._evaluate = lambda row, strategy, capital, target, low, high, n, cfg, margin_pct=None: {
        "symbol": row["symbol"], "strategy": strategy, "pieces": [{"symbol": row["symbol"],
        "strategy": strategy, "piece_type": "grid", "capital_usdt": capital, "n_levels": n,
        "floor": low, "ceiling": high, "margin_pct": margin_pct}],
        "estimate": {"probability": .5, "evidence_sufficient": False}, "replay_probability": .5}
    operator._apply_intelligence = lambda *_args, **_kwargs: None
    cfg = {"min_margin_pct": .3, "max_days": 20, "_advisor_reference_capital": 70}
    operator._candidate_alternatives({"symbol": "XRPUSDT"}, 30, 3, cfg)
    operator._candidate_alternatives({"symbol": "XRPUSDT"}, 20, 2, cfg)
    assert len(calls) == 3
    assert {call[1] for call in calls} == {70}
    assert operator._analysis_timing["advisor_cache_hits"] == 3


def test_individual_bot_optimizer_compares_near_and_far_x_y():
    from grid.meta_operator import MetaOperator, rank_alternatives

    class Client:
        def get_book_ticker(self, _symbol):
            return {"bid_price": 99.99, "ask_price": 100.01}

    operator = MetaOperator(object(), object(), type("Scanner", (), {"client": Client()})(),
        None, None, advisor=lambda *_args: {"recommended_floor": 90, "recommended_ceiling": 110,
            "suggested_grids": 1})
    operator._analysis_timing = {"advisor_s": 0, "replay_s": 0}
    operator._evaluate = lambda row, strategy, capital, target, low, high, n, cfg, margin_pct=None: {
        "symbol": row["symbol"], "strategy": strategy,
        "pieces": [{"symbol": row["symbol"], "floor": low, "ceiling": high,
            "piece_type": "individual_bot_grid"}],
        "estimate": {"probability": .9 if low > 99 else .1,
            "probability_replay": .9 if low > 99 else .1, "evidence_sufficient": True}}
    operator._apply_intelligence = lambda *_args, **_kwargs: None
    alternatives, _ = operator._candidate_alternatives({"symbol": "XRPUSDT"}, 20, 10,
        {"min_margin_pct": .3, "max_days": 20})
    assert {row["bot_buy_distance_pct"] for row in alternatives} == {.5, 1.5, 3.0}
    assert len(alternatives) == 9
    assert rank_alternatives(alternatives)[0]["bot_buy_distance_pct"] == .5
    import numpy as np
    from grid.sim.data import CandleData
    from grid.sim.meta_bot import run_meta_bot
    candles = CandleData(timestamp=np.array([0, 300]), open=np.array([100., 100.]),
        high=np.array([100.1, 100.1]), low=np.array([100., 100.]),
        close=np.array([100., 100.]), gaps=0)
    for row in alternatives:
        run_meta_bot(candles, capital=20, buy_price=row["bot_x"], sell_price=row["bot_y"],
            target_usdt=2, min_margin_pct=.3)


def test_daily_cache_persists_results_for_reuse(tmp_path):
    from database.db_manager import DBManager
    from grid.meta_operator import MetaOperator

    db = DBManager(f"sqlite:///{tmp_path / 'meta-cache.sqlite'}")
    operator = MetaOperator(object(), db, None, None, None, clock=lambda: 1_800_000_000)
    payload = ("XRPUSDT", 70, .3, 20)
    value = {"recommended_floor": 1.0, "recommended_ceiling": 2.0}
    operator._persistent_cache_set("advisor", payload, value)
    assert operator._persistent_cache_get("advisor", payload) == value


def test_medidor_cache_is_read_only_and_reports_separate_cache_sources(tmp_path):
    import json
    from datetime import datetime, timezone
    from sqlalchemy import func, select
    from database.db_manager import DBManager
    from grid.meta_operator import MetaOperator

    db = DBManager(f"sqlite:///{tmp_path / 'meta-cache-readonly.sqlite'}")
    clock = lambda: 1_800_000_000
    reader = MetaOperator(object(), db, None, None, None, clock=clock,
        cache_enabled=True, cache_writes=False)
    payload, value = ("XRPUSDT", 70, .3, 20), {"recommended_floor": 1.0}
    key, day = reader._cache_key("advisor", payload)
    with db.engine.begin() as conn:
        conn.execute(db.meta_analysis_cache.insert().values(cache_key=key, cache_kind="advisor",
            analysis_day=day, value_json=json.dumps(value), created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    assert reader._persistent_cache_get("advisor", payload) == value
    reader._persistent_cache_set("advisor", payload, {"recommended_floor": 2.0})
    with db.engine.connect() as conn:
        assert conn.execute(db.meta_analysis_cache.select()).mappings().one()["value_json"] == json.dumps(value)
        assert conn.execute(select(func.count()).select_from(db.meta_analysis_cache)).scalar_one() == 1


def test_daily_precompute_obeys_switch_uses_no_ops_lock_and_warms_shared_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from database.db_manager import DBManager
    from grid.meta import DEFAULT_PROFILES
    from grid.meta_operator import MetaOperator

    db = DBManager(f"sqlite:///{tmp_path / 'meta-precompute.sqlite'}")
    class ForbiddenLock:
        def __enter__(self):
            raise AssertionError("precompute no puede tomar ops_lock")
        def __exit__(self, *_args):
            return False
    class Core:
        monitor = SimpleNamespace(ops_lock=ForbiddenLock())
        def _profile_settings(self):
            return DEFAULT_PROFILES

    settings = SimpleNamespace(meta_mode_enabled=False)
    operator = MetaOperator(Core(), db, None, None, settings,
        clock=lambda: 1_800_000_000)
    assert operator.precompute_daily()["status"] == "disabled"
    settings.meta_mode_enabled = True
    calls = []
    def warm(self, goal, *, record=True):
        calls.append((goal["profile"], record))
        self._persistent_cache_set("advisor", (goal["profile"],), {"ok": True})
        return {"timings_s": {}}
    monkeypatch.setattr(MetaOperator, "_build_plan", warm)
    result = operator.precompute_daily()
    assert result["status"] == "completed"
    assert result["workers"] == 1
    assert calls == [("PRUDENTE", False), ("RAPIDO", False)]
    assert result["cache_entries_after"] == 2
    assert operator._persistent_cache_get("advisor", ("PRUDENTE",)) == {"ok": True}
    calls.clear()
    def one_profile_has_no_candidates(self, goal, *, record=True):
        calls.append(goal["profile"])
        if goal["profile"] == "PRUDENTE":
            raise ValueError("Ninguna moneda cumple los filtros")
        return {"timings_s": {}}
    monkeypatch.setattr(MetaOperator, "_build_plan", one_profile_has_no_candidates)
    partial = operator.precompute_daily()
    assert partial["status"] == "partial"
    assert [row["status"] for row in partial["profiles"]] == ["no_candidates", "completed"]
    assert calls == ["PRUDENTE", "RAPIDO"]


def test_meta_reconfiguration_uses_materiality_and_repositions_only_unfilled_bots():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self): self.events, self.rows = [], []
        def ledger(self, _meta_id): return list(self.rows)
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))

    class Engine:
        def __init__(self): self.calls = []
        def reposition_meta_bot(self, *args, **kwargs):
            self.calls.append(("bot", args, kwargs))
            return {"ok": True, "changed": True}
        def adjust_grid(self, *args, **kwargs):
            self.calls.append(("grid", args, kwargs))
            return {"ok": True, "changed": True}

    def inputs(piece_type="grid", held=0, probability=.56, old_probability=.50,
               new_progress=20, old_progress=20):
        core, engine = Core(), Engine()
        operator = MetaOperator(core, object(), None, engine, None, clock=lambda: 1_800_000_000)
        grid = {"id": 7, "status": "ACTIVE", "symbol": "AAAUSDT", "strategy": "simple",
                "range_low": 90, "range_high": 110, "n_levels": 4}
        levels = [{"held_qty": held}]
        def alt(low, high, p, progress):
            return {"symbol": "AAAUSDT", "strategy": "simple", "pieces": [{
                "piece_type": piece_type, "floor": low, "ceiling": high, "n_levels": 1 if piece_type == "individual_bot_grid" else 4}],
                "estimate": {"evidence_sufficient": True, "probability": p,
                "median_progress_pct": progress}, "alternative_id": str(low)}
        plan = {"alternatives": [alt(90, 110, old_probability, old_progress),
                                 alt(92, 112, probability, new_progress)],
                "shadow_decision": {"symbol": "AAAUSDT", "strategy": "simple"}}
        return core, engine, operator, grid, levels, plan

    core, engine, operator, grid, levels, plan = inputs()
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "grid"}, (1, 7))
    assert result["decision"] == "reajustar_pieza"
    assert engine.calls[0][0] == "grid"
    event = core.events[-1][0]
    assert event[2] == "META_OPERATOR_RECONFIGURE"
    assert event[3]["estimated_probability_improvement_pp"] == pytest.approx(6)

    core, engine, operator, grid, levels, plan = inputs(probability=.53)
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "grid"}, (1, 7))
    assert result is None  # 3 pp no supera la materialidad de 5 pp.
    assert engine.calls == []

    core, engine, operator, grid, levels, plan = inputs(probability=.51, new_progress=22)
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "grid"}, (1, 7))
    assert result["decision"] == "reajustar_pieza"  # empate ±2 pp y avance mediano +10 %.

    core, engine, operator, grid, levels, plan = inputs()
    assert operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 84, plan, {"piece_type": "grid"}, (1, 7)) is None
    operator._outside_range_count[(1, 7)] = 2
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 84, plan, {"piece_type": "grid"}, (1, 7))
    assert result["decision"] == "reajustar_pieza"  # dos revisiones, desplazamiento > un nivel.

    core, engine, operator, grid, levels, plan = inputs()
    core.rows = [{"event_type": "META_OPERATOR_RECONFIGURE", "grid_id": 7,
        "ts": datetime.fromtimestamp(1_800_000_000 - 1800, timezone.utc).replace(tzinfo=None)}]
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "grid"}, (1, 7))
    assert result is None and engine.calls == []  # enfriamiento de 1 h.

    core, engine, operator, grid, levels, plan = inputs()
    core.rows = [{"event_type": "META_OPERATOR_RECONFIGURE", "grid_id": i,
        "ts": datetime.fromtimestamp(1_800_000_000 - 3600, timezone.utc).replace(tzinfo=None)}
        for i in range(4)]
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "grid"}, (1, 7))
    assert result is None and engine.calls == []  # máximo de cuatro en 24 h.

    core, engine, operator, grid, levels, plan = inputs("individual_bot_grid")
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "individual_bot_grid"}, (1, 7))
    assert result["decision"] == "reajustar_pieza"
    assert engine.calls[0][0] == "bot"
    assert engine.calls[0][1][1:] == (92, 112)

    core, engine, operator, grid, levels, plan = inputs("individual_bot_grid", held=1)
    result = operator._consider_piece_reconfiguration({"id": 1, "config_snapshot": {}}, grid,
        levels, 108, plan, {"piece_type": "individual_bot_grid"}, (1, 7))
    assert result is None and engine.calls == []


def test_prudent_requires_medium_sigma_and_complete_model_stats_rapid_does_not():
    assert profile_candidate_exclusion("PRUDENTE", .9, .2, .6, True) == "sigma fuera del tercil medio Prudente"
    assert "30 verificaciones" in profile_candidate_exclusion("PRUDENTE", .4, .2, .6, False)
    assert profile_candidate_exclusion("RAPIDO", .9, .2, .6, False) is None
    assert profile_candidate_exclusion("RAPIDO", .9, None, None, False) is None


def test_prudent_verification_threshold_is_reachable_with_current_model_window():
    from grid.meta import DEFAULT_PROFILES
    from models.volatility.model_stats import N_MIN, ROLLING_VERIFICATIONS

    max_rows = max(ROLLING_VERIFICATIONS, N_MIN * 24)
    maximum_effective_days = max_rows / 24
    assert DEFAULT_PROFILES["PRUDENTE"]["min_effective_verifications"] == 30
    assert maximum_effective_days >= DEFAULT_PROFILES["PRUDENTE"]["min_effective_verifications"]


def test_meta_signal_formula_and_loss_reallocation_gate():
    p = signal_adjusted_probability(.4, [{"name": "direction", "normalized": 1}], {"direction": 1})
    assert p > .4
    assert not loss_reallocation_allowed(1.49, 1.0, .25, .25)
    assert loss_reallocation_allowed(1.51, 1.0, .25, .25)


def test_signal_logit_adjustment_has_default_cap_of_one():
    signals = [{"name": name, "normalized": 1.0} for name in
               ("volatility", "break_prob", "direction", "scanner", "advisor")]
    weights = {name: 1.0 for name in (row["name"] for row in signals)}
    adjusted = signal_adjusted_probability(.1, signals, weights, max_abs_logit=1.0)
    assert adjusted == pytest.approx(1 / (1 + __import__("math").exp(-(-__import__("math").log(9) + 1))))
    assert adjusted < .24


def test_default_signal_weight_is_quarter_per_signal():
    adjusted = signal_adjusted_probability(.5, [{"name": "direction", "normalized": 1}], {})
    assert adjusted == pytest.approx(1 / (1 + __import__("math").exp(-.25)))


def test_allocation_vectors_use_ten_percent_steps_and_coin_cap():
    prudent = allocation_vectors(2, 40)
    assert prudent and all(sum(row) <= 100 and max(row) <= 40 for row in prudent)
    assert all(value % 10 == 0 for row in prudent for value in row)
    assert (40, 40) in prudent


def test_portfolio_replay_sums_aligned_date_windows():
    alternatives = [
        {"window_results": [{"window_start": "2026-01-01", "pnl_usdt": 6, "days": 10,
                             "drawdown_pct": 2, "step_days": 7},
                            {"window_start": "2026-01-08", "pnl_usdt": -1, "days": None,
                             "drawdown_pct": 5, "step_days": 7}]},
        {"window_results": [{"window_start": "2026-01-01", "pnl_usdt": 5, "days": 12,
                             "drawdown_pct": 3, "step_days": 7},
                            {"window_start": "2026-01-15", "pnl_usdt": 20, "days": 8,
                             "drawdown_pct": 1, "step_days": 7}]},
    ]
    summary, rows = combine_aligned_replays(alternatives, 10, 20)
    assert len(rows) == 1
    assert rows[0]["pnl_usdt"] == 11
    assert summary["probability"] == 1


def test_plan_search_can_choose_two_distinct_coins_with_bounded_allocation(monkeypatch):
    from grid.meta_operator import MetaOperator

    class DB:
        def get_active_coins(self): return [{"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}]
        def get_readiness(self, _symbol): return {}
        def get_latest_vol_model_stats_forecast_at(self, *_args): return None
        def get_vol_model_stats_rows(self, *_args): return []
        def get_latest_prediction(self, *_args): return None

    class Core:
        def _ledger(self, *_args, **_kwargs): pass

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 100, "ask_price": 100.1}

    class Scanner:
        client = Client()
        def scan(self, **_kwargs):
            return {"results": [{"symbol": "AAAUSDT", "eligible": True, "score": .9,
                "sigma_24h_open": .1}, {"symbol": "BBBUSDT", "eligible": True,
                "score": .8, "sigma_24h_open": .2}]}

    operator = MetaOperator(Core(), DB(), Scanner(), None, None)
    operator._testnet_book_status = lambda _symbol: {"state": "two_sided", "reason": None}
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    def options(row, capital, target, _cfg):
        piece = {"symbol": row["symbol"], "strategy": "simple", "piece_type": "grid",
            "capital_usdt": capital, "n_levels": 4, "floor": 90, "ceiling": 110,
            "mid_at_plan": 100, "margin_pct": .7, "loans_pct": 0}
        outcome = [{"window_start": f"2026-01-{day:02d}", "pnl_usdt": capital * .12,
            "days": 10, "drawdown_pct": 1, "step_days": 7} for day in (1, 8, 15, 22, 29, 36, 43)]
        estimate = {"probability": .7 if capital >= 70 else 0,
            "days_median": 10 if capital >= 70 else None, "days_p10": 10 if capital >= 70 else None,
            "days_p90": 10 if capital >= 70 else None, "worst_drawdown_pct": 1,
            "rolling_windows": 7, "independent_windows": 2, "evidence_sufficient": False,
            "low_likelihood": capital < 70}
        return [{"symbol": row["symbol"], "strategy": "simple", "pieces": [piece],
            "estimate": estimate, "replay_probability": estimate["probability"],
            "window_results": outcome, "signals_used": [], "signals_omitted": [],
            "shadow_probability": estimate["probability"], "shadow_signals": [], "advisor": {},
            "reason": "fake"}], []

    monkeypatch.setattr(operator, "_candidate_alternatives", options)
    monkeypatch.setattr(operator, "_control_plan", lambda _best, capital: {"capital_usdt": capital})
    plan = operator._build_plan({"id": 5, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 10, "config_snapshot": {"capital_per_coin_pct": 70,
        "max_grids": 4, "max_bots": 8, "max_days": 20, "min_margin_pct": .3,
        "signal_weights": {}, "signal_logit_cap": 1}})
    assert len(plan["pieces"]) == 2
    assert {piece["symbol"] for piece in plan["pieces"]} == {"AAAUSDT", "BBBUSDT"}
    assert 80 <= sum(piece["capital_usdt"] for piece in plan["pieces"]) <= 100
    assert all(piece["capital_usdt"] <= 70 for piece in plan["pieces"])
    assert any(len(row["symbols"]) == 2 for row in plan["combinations_evaluated"])
    # Fase 40c, parte B2: el medidor necesita un desglose por pieza, con
    # valores por defecto honestos cuando el estimate no trae esos campos.
    assert len(plan["piece_estimates"]) == len(plan["pieces"])
    for item in plan["piece_estimates"]:
        assert item["skipped_windows"] == 0
        assert item["loss_cut_windows"] == 0
        assert item["bot_windows_bought"] is None
    operator._testnet_book_status = lambda symbol: ({"state": "one_sided", "reason": "sin ventas"}
        if symbol == "BBBUSDT" else {"state": "two_sided", "reason": None})
    filtered = operator._build_plan({"id": 5, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 10, "config_snapshot": {"capital_per_coin_pct": 70,
        "max_grids": 4, "max_bots": 8, "max_days": 20, "min_margin_pct": .3,
        "signal_weights": {}, "signal_logit_cap": 1}})
    pepe = next(row for row in filtered["candidates"] if row["symbol"] == "BBBUSDT")
    assert pepe["testnet_book_state"] == "one_sided"
    assert "alternativa no abrible en Testnet" in pepe["excluded_reason"]
    assert all("BBBUSDT" not in row["symbols"] for row in filtered["alternatives"])


def test_portfolio_loss_cut_windows_matches_sum_of_piece_estimates(monkeypatch):
    """Fase 40c-3, defecto 6: combine_aligned_replays no sabe de "loss_cut" y
    el total de cartera quedaba en 0 aunque las piezas sí tuvieran ventanas
    cortadas por pérdida (ej. PEPE 2 + JUP 1 = 3, pero la pantalla mostraba 0).
    El total a nivel de plan debe coincidir con la suma real de las piezas."""
    from grid.meta_operator import MetaOperator

    class DB:
        def get_active_coins(self): return [{"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}]
        def get_readiness(self, _symbol): return {}
        def get_latest_vol_model_stats_forecast_at(self, *_args): return None
        def get_vol_model_stats_rows(self, *_args): return []
        def get_latest_prediction(self, *_args): return None

    class Core:
        def _ledger(self, *_args, **_kwargs): pass

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 100, "ask_price": 100.1}

    class Scanner:
        client = Client()
        def scan(self, **_kwargs):
            return {"results": [{"symbol": "AAAUSDT", "eligible": True, "score": .9,
                "sigma_24h_open": .1}, {"symbol": "BBBUSDT", "eligible": True,
                "score": .8, "sigma_24h_open": .2}]}

    operator = MetaOperator(Core(), DB(), Scanner(), None, None)
    operator._testnet_book_status = lambda _symbol: {"state": "two_sided", "reason": None}
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    loss_cuts = {"AAAUSDT": 2, "BBBUSDT": 1}

    def options(row, capital, target, _cfg):
        piece = {"symbol": row["symbol"], "strategy": "simple", "piece_type": "grid",
            "capital_usdt": capital, "n_levels": 4, "floor": 90, "ceiling": 110,
            "mid_at_plan": 100, "margin_pct": .7, "loans_pct": 0}
        outcome = [{"window_start": f"2026-01-{day:02d}", "pnl_usdt": capital * .12,
            "days": 10, "drawdown_pct": 1, "step_days": 7} for day in (1, 8, 15, 22, 29, 36, 43)]
        estimate = {"probability": .7, "days_median": 10, "days_p10": 10, "days_p90": 10,
            "worst_drawdown_pct": 1, "rolling_windows": 7, "independent_windows": 2,
            "evidence_sufficient": False, "low_likelihood": False,
            "loss_cut_windows": loss_cuts[row["symbol"]]}
        return [{"symbol": row["symbol"], "strategy": "simple", "pieces": [piece],
            "estimate": estimate, "replay_probability": estimate["probability"],
            "window_results": outcome, "signals_used": [], "signals_omitted": [],
            "shadow_probability": estimate["probability"], "shadow_signals": [], "advisor": {},
            "reason": "fake"}], []

    monkeypatch.setattr(operator, "_candidate_alternatives", options)
    monkeypatch.setattr(operator, "_control_plan", lambda _best, capital: {"capital_usdt": capital})
    plan = operator._build_plan({"id": 5, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 10, "config_snapshot": {"capital_per_coin_pct": 70,
        "max_grids": 4, "max_bots": 8, "max_days": 20, "min_margin_pct": .3,
        "signal_weights": {}, "signal_logit_cap": 1}})
    assert len(plan["pieces"]) == 2
    piece_total = sum(item["loss_cut_windows"] for item in plan["piece_estimates"])
    assert piece_total == 3
    assert plan["estimated_time"]["loss_cut_windows"] == piece_total


def test_advisor_signal_comes_from_advisor_result_not_scanner_structure(monkeypatch):
    from grid.meta_operator import MetaOperator
    import grid.meta_operator as module

    class DB:
        def get_latest_prediction(self, *_args): return None

    operator = MetaOperator(object(), DB(), None, None, None)
    operator._model_snapshot = lambda _symbol: {"volatility_forecasts": {}}
    monkeypatch.setattr(module, "break_prob", lambda *_args: (.5, None, None, None))
    row = {"symbol": "XRPUSDT", "sigma_24h_open": .02, "score": .5,
           "suggested_structure": {"net_edge_pct_per_cycle": 99}, "model_statistics": {}}
    alt = {"replay_probability": .5, "estimate": {"probability": .5, "evidence_sufficient": True},
           "pieces": [{"capital_usdt": 100}]}
    operator._apply_intelligence(alt, row, 100, 90, 110, {"signal_weights": {"advisor": .25}},
                                 advisor_result={"net_per_cycle_after_dust_usdt": 2})
    signal = next(item for item in alt["signals_used"] if item["name"] == "advisor")
    assert signal["value"] == 2
    assert signal["normalized"] == 1


def test_review_can_add_one_piece_using_only_free_goal_cash():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self): self.events = []
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))

    class DB:
        def get_grid(self, _grid_id):
            return {"status": "ACTIVE", "capital_total": 60, "symbol": "ADAUSDT"}

    operator = MetaOperator(Core(), DB(), None, None, None)
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    opened = []
    operator._open_replacement = lambda _goal, _alternative, cash: (
        opened.append(cash) or {"grid_id": 2, "capital_usdt": cash})
    goal = {"id": 1, "capital_initial": 100, "config_snapshot": {
        "capital_per_coin_pct": 40, "max_grids": 2, "max_bots": 4}}
    links = [{"grid_id": 1, "piece_type": "grid"}]
    plan = {"alternatives": [{"pieces": [{"piece_type": "grid", "symbol": "XRPUSDT",
        "capital_usdt": 40}], "estimate": {"probability": .6}}]}
    result = operator._add_piece_from_free_cash(goal, links, plan)
    assert result["decision"] == "abrir_pieza_con_efectivo_libre"
    assert opened == [40]
    assert operator._add_piece_from_free_cash(goal, links, {"alternatives": []}) is None


@pytest.mark.parametrize("trigger", ["scanner", "ceiling"])
def test_review_withdrawal_requires_two_consecutive_hard_filter_or_ceiling_checks(trigger):
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self): self.events = []
        def _owned_grids(self, _goal_id): return [{"grid_id": 7, "piece_type": "grid"}]
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))

    class DB:
        def get_grid(self, _grid_id): return {"id": 7, "status": "ACTIVE", "symbol": "AAAUSDT",
            "strategy": "simple", "capital_total": 40, "range_high": 110,
            "range_low": 90, "open_price": 100, "n_levels": 4}
        def get_grid_levels(self, _grid_id): return [{"held_qty": 1, "entry_price": 100, "price": 100}]

    class Exchange:
        def get_book_ticker(self, _symbol): return {"bid_price": 120 if trigger == "ceiling" else 100}

    class Engine:
        exchange = Exchange()
        def __init__(self): self.buy_only = []
        def cancel_grid_orders(self, grid_id, **kwargs): self.buy_only.append((grid_id, kwargs))

    core, engine = Core(), Engine()
    operator = MetaOperator(core, DB(), None, engine, None)
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    bad = trigger == "scanner"
    plan = {"candidates": [{"symbol": "AAAUSDT", "scanner_eligible": not bad,
        "eligible": not bad}], "alternatives": [{"symbol": "AAAUSDT", "strategy": "simple",
        "pieces": [{"piece_type": "grid", "symbol": "AAAUSDT"}],
        "estimate": {"evidence_sufficient": True, "probability": .7, "days_median": 3, "days_p90": 4}}]}
    operator._build_plan = lambda _goal: plan
    operator._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    goal = {"id": 1, "target_usdt": 10, "config_snapshot": {}}
    assert operator._review_allocations(goal, {})["decision"] == "no_hacer_nada"
    result = operator._review_allocations(goal, {})
    if trigger == "scanner":
        assert result["decision"] == "no_hacer_nada"
        assert "materialidad" in result["reason"]
        assert engine.buy_only == []
    else:
        # Dos revisiones fuera habilitan evaluación; no retira sin mejora material.
        assert result["decision"] == "no_hacer_nada"
        assert engine.buy_only == []
    assert not any(event[0][2] == "META_OPERATOR_WITHDRAW" for event in core.events)


def test_review_does_not_add_piece_when_75_percent_or_unknown_blocks_it():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self, value): self.value, self.events = value, []
        def get_goal(self, _id): return {"id": 1, "status": "ACTIVE", "config_snapshot": {
            "warning_loss_pct": 6}}
        def valuation(self, _id): return self.value
        def _ledger(self, *args, **kwargs): self.events.append(args)

    for value in ({"status": "UNKNOWN"}, {"status": "KNOWN", "result_pct": "-6"}):
        core = Core(value)
        operator = MetaOperator(core, None, None, None, None)
        operator._add_piece_from_free_cash = lambda *_args: pytest.fail("blocked review reached allocator")
        assert operator.review_once(1)["decision"] == "no_hacer_nada"
        assert any(event[2] == "META_OPERATOR_NOOP" for event in core.events)
        assert any(event[2] == "META_OPERATOR_REVIEW_DECISION" for event in core.events)


@pytest.mark.parametrize(("profile", "cap"), [("PRUDENTE", 30), ("RAPIDO", 50)])
def test_meta_smart_piece_activates_profile_loan_cap(profile, cap):
    from grid.meta import smart_grid_params_for_goal
    params = smart_grid_params_for_goal({"target_usdt": 10,
        "config_snapshot": {"loan_cap_pct": cap}})
    assert params["loans_enabled"] is True
    assert params["loan_cap_pct"] == params["loan_topup_pct"] == params["loan_lender_max_pct"] == cap
    assert "target_usdt" not in params and "target_basis" not in params


def test_candidate_search_asks_advisor_for_three_margin_alternatives():
    from grid.meta_operator import MetaOperator

    calls = []
    def advisor(symbol, capital, margin, days):
        calls.append((symbol, capital, margin, days))
        return {"recommended_floor": 90, "recommended_ceiling": 110,
            "suggested_grids": 5, "suggested_strategy": "smart",
            "net_per_cycle_after_dust_usdt": 1, "simulations": {"strategies": {}}}

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 100, "ask_price": 100.01}
    operator = MetaOperator(object(), object(), type("Scanner", (), {"client": Client()})(),
                            None, None, advisor=advisor)
    operator._advisor_cache = {}
    operator._analysis_timing = {"advisor_s": 0, "replay_s": 0}
    operator._evaluate = lambda row, strategy, capital, target, low, high, n, cfg, margin_pct=None: {
        "symbol": row["symbol"], "strategy": strategy, "pieces": [{"symbol": row["symbol"],
        "strategy": strategy, "piece_type": "grid", "capital_usdt": capital,
        "n_levels": n, "floor": low, "ceiling": high, "margin_pct": margin_pct}],
        "estimate": {"probability": .5, "evidence_sufficient": False},
        "replay_probability": .5, "signals_used": [], "signals_omitted": []}
    operator._apply_intelligence = lambda *_args, **_kwargs: None
    options, comparisons = operator._candidate_alternatives({"symbol": "XRPUSDT", "score": .7},
        100, 10, {"min_margin_pct": .7, "max_days": 45})
    assert [call[2] for call in calls] == pytest.approx([.7, .9, 1.1])
    assert all(call[3] == 45 for call in calls)
    assert len(options) == 6
    assert len(comparisons) == 3


def test_replay_builder_uses_profile_deadline_for_window_length(monkeypatch):
    import pandas as pd
    import numpy as np
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    class Client:
        def get_historical_klines(self, *_args):
            return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=5, freq="5min", tz="UTC"),
                "open": np.ones(5), "high": np.ones(5), "low": np.ones(5), "close": np.ones(5)})

    class Scanner:
        client = Client()

    operator = MetaOperator(object(), object(), Scanner(), None, None, clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"replay_s": 0, "advisor_s": 0}
    observed = []
    monkeypatch.setattr(module, "build_windows", lambda _candles, days, step:
        (observed.append((days, step)) or []))
    result = operator._evaluate({"symbol": "XRPUSDT", "book": {"bid_price": 1, "ask_price": 1},
        "sigma_24h_open": .1}, "simple", 100, 10, .9, 1.1, 4,
        {"max_days": 45, "min_margin_pct": .7})
    assert observed == [(45, 7)]
    assert result["estimate"]["window_days"] == 45


def test_replay_rescales_plan_range_for_historical_prices_over_fifty_percent_away(monkeypatch):
    import pandas as pd
    import numpy as np
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    class Client:
        def get_historical_klines(self, *_args):
            return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=4, freq="5min", tz="UTC"),
                "open": [200.0, 201, 50, 51], "high": [202.0, 202, 52, 53],
                "low": [198.0, 199, 49, 50], "close": [201.0, 200, 51, 52]})

    operator = MetaOperator(object(), object(), type("Scanner", (), {"client": Client()})(), None, None,
        clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"replay_s": 0, "advisor_s": 0}
    monkeypatch.setattr(module, "build_windows", lambda *_args: [
        {"start_idx": 0, "end_idx": 2, "start": 1767225600},
        {"start_idx": 2, "end_idx": 4, "start": 1767225900}])
    ranges = []

    def replay(_sample, **kwargs):
        ranges.append((kwargs["low"], kwargs["high"]))
        return {"events": [], "metrics": {"equity_final_after_repository_liquidation": kwargs["capital"],
            "max_drawdown_pct": 0}}

    monkeypatch.setattr(module, "run_simulation", replay)
    operator._evaluate({"symbol": "XRPUSDT", "book": {"bid_price": 100, "ask_price": 100},
        "sigma_24h_open": .1}, "simple", 100, 10, 90, 110, 4, {"max_days": 20})
    assert ranges[0] == pytest.approx((180, 220))
    assert ranges[1] == pytest.approx((45, 55))


@pytest.mark.parametrize(("profile", "piece_cap"), [("PRUDENTE", 100), ("RAPIDO", 175)])
def test_meta_analysis_async_then_start_opens_and_links_piece_through_api(tmp_path, monkeypatch,
                                                                          profile, piece_cap):
    import time
    import pandas as pd
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from database.db_manager import DBManager
    from grid.meta import MetaCore
    from grid.engine import GridEngine
    from tests.grid_fakes import FakeExchange, fake_symbol_info
    from data.exchange_filters import SymbolFilters
    from grid.meta_operator import MetaOperator
    from api.routes import metas, grids

    class TestExchange(FakeExchange):
        testnet = True

    exchange = TestExchange()
    db = DBManager(f"sqlite:///{tmp_path / 'operator.sqlite'}")
    db.add_or_reactivate_coin("XRPUSDT")
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False,
        grid_api_token="test-token", max_grids_simultaneos=5, usdt_por_grid=100,
        capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=5,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30)
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings)
    core.start = lambda: None

    class Scanner:
        client = exchange
        last_scan = None
        def _market(self, _symbol, _capital, _deadline):
            frame = pd.DataFrame({"close": [100.0 + i * .01 for i in range(100)]})
            return {"bid": 100, "ask": 100.01, "klines_1h": frame}, SymbolFilters.from_symbol_info(fake_symbol_info())
        def clock(self): return time.monotonic()

    scanner = Scanner()
    operator = MetaOperator(core, db, scanner, engine, settings)
    operator._build_plan = lambda goal: {"meta_id": goal["id"], "candidates": [
        {"symbol": "XRPUSDT", "excluded_reason": None},
        {"symbol": "BADUSDT", "excluded_reason": "filtro duro Scanner"}],
        "pieces": [{"symbol": "XRPUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": piece_cap, "n_levels": 4, "floor": 90, "ceiling": 110,
            "mid_at_plan": 100.005, "margin_pct": .7}],
        "control_plan": {"type": "control", "opened": False},
        "estimated_time": {"probability": .5, "days_median": 12}}
    monkeypatch.setattr(grids, "coin_is_ready", lambda *_args: True)
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.settings, app.state.db = settings, db
    app.state.grid_engine = engine
    app.state.testnet_client = exchange
    app.state.vol_provider = None
    app.state.grid_scan_service = scanner
    goal = core.create_goal(250, 10, profile=profile)
    with TestClient(app, headers={"X-API-Token": "test-token"}) as client:
        job = client.post(f"/api/metas/{goal['id']}/analysis").json()
        assert "job_id" in job, job
        for _ in range(100):
            state = client.get(f"/api/metas/{goal['id']}/analysis/{job['job_id']}").json()
            if state["status"] != "PENDING":
                break
            time.sleep(.01)
        assert state["status"] == "COMPLETE"
        assert len(state["plan"]["candidates"]) == 2
        started = client.post(f"/api/metas/{goal['id']}/start",
            json={"plan_id": state["plan"]["plan_id"]})
        assert started.status_code == 200, core.ledger(goal["id"])[-1]["details"]
        assert len(started.json()["opened"]) == 1
    linked = core._owned_grids(goal["id"])
    assert len(linked) == 1
    assert db.get_grid(linked[0]["grid_id"])["symbol"] == "XRPUSDT"
    with db.engine.connect() as conn:
        run = conn.execute(db.meta_runs.select().where(db.meta_runs.c.meta_id == goal["id"])).mappings().one()
    assert run["control_plan_json"] == '{"opened": false, "type": "control"}'


def test_start_plan_keeps_successful_pieces_when_one_open_fails(tmp_path):
    import json
    from datetime import datetime, timezone
    from database.db_manager import DBManager
    from grid.meta import MetaCore
    from grid.meta_operator import MetaOperator

    class Exchange:
        testnet = True
        def get_balance(self, *_args): return {"USDT": {"free": 1000}}

    db = DBManager(f"sqlite:///{tmp_path / 'partial.sqlite'}")
    core = MetaCore(db, Exchange(), settings=type("Settings", (), {"meta_mode_enabled": True})())
    goal = core.create_goal(100, 10, profile="RAPIDO")
    core.start_goal = lambda _meta_id: None
    linked = []
    core.link_grid = lambda _meta_id, grid_id, kind: linked.append((grid_id, kind))
    with db.engine.begin() as conn:
        conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=datetime.now(timezone.utc), realized_extra_usdt=0))

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 100, "ask_price": 100.01}
    operator = MetaOperator(core, db, type("Scanner", (), {"client": Client()})(), None, None,
                            clock=lambda: 100)
    pieces = [{"symbol": symbol, "strategy": "simple", "piece_type": "grid",
        "capital_usdt": 20, "n_levels": 4, "floor": 90, "ceiling": 110,
        "mid_at_plan": 100.005} for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT")]
    operator._plans["partial-plan-0001"] = {"meta_id": goal["id"], "expires_at": 200,
        "pieces": pieces, "control_plan": {"type": "smart-control"}}
    attempts = []
    def open_piece(piece):
        attempts.append(piece["symbol"])
        if piece["symbol"] == "AAAUSDT": raise RuntimeError("orden rechazada")
        return {"grid_id": len(attempts)}
    result = operator.start_plan(goal["id"], "partial-plan-0001", open_piece)
    assert result["failed"] == 1
    assert len(result["opened"]) == 2
    assert linked == [(2, "grid"), (3, "grid")]
    assert any(row["event_type"] == "META_OPERATOR_OPEN_FAILED" for row in core.ledger(goal["id"]))
    with db.engine.connect() as conn:
        run = conn.execute(db.meta_runs.select().where(db.meta_runs.c.meta_id == goal["id"])).mappings().one()
    assert json.loads(run["control_plan_json"]) == {"type": "smart-control"}


def test_start_rejects_expired_plan_or_price_drift_before_reserving():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self): self.started = False
        def get_goal(self, _meta_id): return {"config_snapshot": {"plan_price_drift_pct": 1}}
        def start_goal(self, _meta_id): self.started = True

    class Client:
        def get_book_ticker(self, _symbol): return {"bid_price": 102, "ask_price": 102.1}

    core = Core()
    operator = MetaOperator(core, None, type("Scanner", (), {"client": Client()})(), None, None)
    operator._plans["expired"] = {"meta_id": 4, "expires_at": 1, "pieces": []}
    with pytest.raises(ValueError, match="vencido"):
        operator.start_plan(4, "expired", lambda _piece: None)
    operator._plans["drift"] = {"meta_id": 4, "expires_at": 9999999999,
        "pieces": [{"symbol": "XRPUSDT", "mid_at_plan": 100, "floor": 90, "ceiling": 110}]}
    with pytest.raises(ValueError, match="se movió"):
        operator.start_plan(4, "drift", lambda _piece: None)
    assert core.started is False


def test_review_does_not_move_capital_when_valuation_unknown_or_loss_warning_reached():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self):
            self.status = "ACTIVE"
            self.value = {"status": "UNKNOWN"}
            self.ledger_rows = []
        def get_goal(self, _meta_id):
            return {"id": 1, "status": self.status, "config_snapshot": {"warning_loss_pct": 6}}
        def valuation(self, _meta_id): return self.value
        def _ledger(self, *args, **kwargs): self.ledger_rows.append((args, kwargs))

    core = Core()
    operator = MetaOperator(core, None, None, None, None)
    first = operator.review_once(1)
    assert first["decision"] == "no_hacer_nada"
    assert "DESCONOCIDO" in first["reason"]
    core.value = {"status": "KNOWN", "result_pct": "-6.0"}
    second = operator.review_once(1)
    assert second["decision"] == "no_hacer_nada"
    assert "75%" in second["reason"]
    assert sum(row[0][2] == "META_OPERATOR_REVIEW_DECISION" for row in core.ledger_rows) == 2



def test_meta_replay_uses_real_symbol_filters_pepe_like_price_not_omitted(monkeypatch):
    """Fase 40c, parte B1: con el tick generico 0.0001 del simulador, un rango
    PEPE-like (~0.00001) colapsa a una sola linea y la ventana se omite. Con los
    filtros reales del simbolo (tick fino), la misma ventana se simula bien."""
    import pandas as pd
    import numpy as np
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    n_candles = 300
    base = 0.00001
    prices = base * (1 + 0.01 * np.sin(np.linspace(0, 6.28, n_candles)))

    class Client:
        def get_historical_klines(self, *_args):
            return pd.DataFrame({
                "timestamp": pd.date_range("2026-01-01", periods=n_candles, freq="5min", tz="UTC"),
                "open": prices, "high": prices * 1.001, "low": prices * 0.999, "close": prices,
            })

        def get_symbol_info(self, _symbol):
            return {"filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.00000001", "maxPrice": "1000", "tickSize": "0.00000001"},
                {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "100000000000", "stepSize": "1"},
                {"filterType": "NOTIONAL", "minNotional": "1", "applyMinToMarket": True},
            ]}

    class Scanner:
        client = Client()

    operator = MetaOperator(object(), object(), Scanner(), None, None, clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"replay_s": 0, "advisor_s": 0, "touch_signal_s": 0}
    monkeypatch.setattr(module, "build_windows", lambda _candles, days, step: [
        {"start_idx": 0, "end_idx": n_candles, "start": 1767225600}])

    low, high = base * 0.95, base * 1.05
    result = operator._evaluate({"symbol": "PEPEUSDT", "book": {"bid_price": base, "ask_price": base},
        "sigma_24h_open": .1}, "simple", 100, 10, low, high, 4, {"max_days": 20, "min_margin_pct": .3})
    assert result["estimate"]["skipped_windows"] == 0


def test_meta_replay_without_real_filters_falls_back_to_generic_tick_and_omits(monkeypatch):
    """Mismo escenario pero sin filtros reales disponibles (get_symbol_info
    falla): cae al tick generico 0.0001 de antes, la ventana PEPE-like se omite
    y se cuenta -- no rompe el analisis (arreglo 5d23260, sin cambios)."""
    import pandas as pd
    import numpy as np
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    n_candles = 300
    base = 0.00001
    prices = base * (1 + 0.01 * np.sin(np.linspace(0, 6.28, n_candles)))

    class Client:
        def get_historical_klines(self, *_args):
            return pd.DataFrame({
                "timestamp": pd.date_range("2026-01-01", periods=n_candles, freq="5min", tz="UTC"),
                "open": prices, "high": prices * 1.001, "low": prices * 0.999, "close": prices,
            })

        def get_symbol_info(self, _symbol):
            raise RuntimeError("sin acceso al publico en esta prueba")

    class Scanner:
        client = Client()

    operator = MetaOperator(object(), object(), Scanner(), None, None, clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"replay_s": 0, "advisor_s": 0, "touch_signal_s": 0}
    monkeypatch.setattr(module, "build_windows", lambda _candles, days, step: [
        {"start_idx": 0, "end_idx": n_candles, "start": 1767225600}])

    low, high = base * 0.95, base * 1.05
    result = operator._evaluate({"symbol": "PEPEUSDT", "book": {"bid_price": base, "ask_price": base},
        "sigma_24h_open": .1}, "simple", 100, 10, low, high, 4, {"max_days": 20, "min_margin_pct": .3})
    assert result["estimate"]["skipped_windows"] == 1


def _rotation_setup(liquidated_cells):
    """Pieza perdedora (AAAUSDT) con una alternativa mejor (BBBUSDT); el cierre
    verificado trae las comisiones y el P&L real de `liquidated_cells`."""
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self):
            self.events = []
        def _owned_grids(self, _goal_id):
            return [{"grid_id": 7, "piece_type": "grid"}]
        def _ledger(self, *args, **kwargs):
            self.events.append((args, kwargs))

    class DB:
        def get_grid(self, _grid_id):
            return {"id": 7, "status": "ACTIVE", "symbol": "AAAUSDT", "strategy": "simple",
                    "capital_total": 40, "range_high": 110, "range_low": 90,
                    "open_price": 100, "n_levels": 4,
                    "created_at": datetime.now(timezone.utc) - timedelta(hours=2)}
        def get_grid_levels(self, _grid_id):
            return [{"held_qty": 1, "entry_price": 120, "price": 120}]

    class Exchange:
        def get_book_ticker(self, _symbol):
            return {"bid_price": 100}

    class Engine:
        exchange = Exchange()
        def __init__(self):
            self.closed = []
        def close_grid(self, grid_id, mode, close_reason=None):
            self.closed.append((grid_id, mode, close_reason))
            return {"status": "CLOSED", "liquidated_cells": liquidated_cells}

    core, engine = Core(), Engine()
    operator = MetaOperator(core, DB(), None, engine, None)
    operator._add_piece_from_free_cash = lambda *_args: None
    plan = {"candidates": [{"symbol": "AAAUSDT", "scanner_eligible": True, "eligible": True}],
            "alternatives": [
                {"symbol": "BBBUSDT", "strategy": "simple",
                 "pieces": [{"piece_type": "grid", "symbol": "BBBUSDT"}],
                 "estimate": {"evidence_sufficient": True, "probability": .9,
                              "probability_replay": .9, "median_progress_pct": 40,
                              "days_median": 2, "days_p90": 3}},
                {"symbol": "AAAUSDT", "strategy": "simple",
                 "pieces": [{"piece_type": "grid", "symbol": "AAAUSDT"}],
                 "estimate": {"probability_replay": .5, "median_progress_pct": 20,
                              "days_p90": 10}},
            ], "decision_id": "rotation-review-1"}
    goal = {"id": 1, "target_usdt": 50, "config_snapshot": {}}
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    return core, engine, operator, plan, goal


def test_review_rotation_accepts_and_reopens_when_verified_gain_covers_loss_and_fees():
    # P&L real de la liquidación: pérdida pequeña (10 USDT) y comisiones registradas.
    core, engine, operator, plan, goal = _rotation_setup(
        [{"pnl_realized": -10, "buy_fee_usdt": 0.1, "sell_fee_usdt": 0.1}])
    opened_calls = []
    operator._open_replacement = lambda _goal, alternative, cash: (
        opened_calls.append((alternative["symbol"], cash)) or {"grid_id": 99, "capital_usdt": cash})

    first = operator._review_allocations(goal, {}, plan=plan)
    assert first["decision"] == "no_hacer_nada"
    plan["decision_id"] = "rotation-review-2"
    result = operator._review_allocations(goal, {}, plan=plan)

    assert result["decision"] == "cerrar_y_reasignar"
    assert engine.closed == [(7, "liquidate", "META_OPERATOR_ROTATION")]
    assert opened_calls == [("BBBUSDT", 40)]
    close_events = [event for event in core.events if event[0][2] == "META_OPERATOR_CLOSE"]
    assert close_events, "se espera un evento META_OPERATOR_CLOSE con las comisiones registradas"
    details = close_events[0][0][3]
    assert details["actual_costs"]["recorded_trade_fees_usdt"] == pytest.approx(0.2)
    assert details["reallocation_allowed_after_verified_close"] is True
    assert not any(event[0][2] == "META_OPERATOR_ROTATION_REJECTED" for event in core.events)


def test_review_rotation_rejects_reopen_when_verified_loss_exceeds_expected_gain():
    # P&L real de la liquidación: pérdida grande (100 USDT) que ninguna ganancia
    # esperada cubre; no debe reabrir aunque haya una alternativa mejor.
    core, engine, operator, plan, goal = _rotation_setup(
        [{"pnl_realized": -100, "buy_fee_usdt": 0.3, "sell_fee_usdt": 0.3}])
    operator._open_replacement = lambda *_args: pytest.fail("no debía reabrir")

    first = operator._review_allocations(goal, {}, plan=plan)
    assert first["decision"] == "no_hacer_nada"
    plan["decision_id"] = "rotation-review-2"
    result = operator._review_allocations(goal, {}, plan=plan)

    assert result["decision"] == "cerrar_sin_reasignar"
    assert engine.closed == [(7, "liquidate", "META_OPERATOR_ROTATION")]
    close_events = [event for event in core.events if event[0][2] == "META_OPERATOR_CLOSE"]
    assert close_events
    details = close_events[0][0][3]
    assert details["actual_costs"]["recorded_trade_fees_usdt"] == pytest.approx(0.6)
    assert details["reallocation_allowed_after_verified_close"] is False
    rejected_events = [event for event in core.events if event[0][2] == "META_OPERATOR_ROTATION_REJECTED"]
    assert rejected_events, "se espera un evento META_OPERATOR_ROTATION_REJECTED"


def test_rotation_with_marginal_edge_and_one_sided_testnet_book_never_closes():
    core, engine, operator, plan, goal = _rotation_setup(
        [{"pnl_realized": -0.0814, "buy_fee_usdt": .04, "sell_fee_usdt": .04}])
    plan["alternatives"][0]["estimate"].update({"probability": .51,
        "probability_replay": .51, "median_progress_pct": 20})
    plan["alternatives"][1]["estimate"].update({"probability": .50,
        "probability_replay": .50, "median_progress_pct": 20})

    class Testnet:
        def get_book_ticker(self, symbol):
            if symbol == "BBBUSDT":
                return {"bid_price": "0.00000452", "ask_price": "0E-8"}
            return {"bid_price": "100", "ask_price": "100.01"}

    engine.exchange = Testnet()
    engine._market_context = lambda symbol: (None, engine.exchange.get_book_ticker(symbol), Decimal("0"))
    del operator._testnet_openability

    result = operator._review_allocations(goal, {}, plan=plan)

    assert result["decision"] == "no_hacer_nada"
    assert "alternativa no abrible en Testnet" in result["reason"]
    assert "libro de Testnet unilateral" in result["reason"]
    assert engine.closed == []
    assert any(event[0][2] == "META_OPERATOR_ROTATION_REJECTED" for event in core.events)


def test_rotation_below_materiality_keeps_open_piece_even_with_two_review_candidate():
    core, engine, operator, plan, goal = _rotation_setup(
        [{"pnl_realized": -0.01, "buy_fee_usdt": 0, "sell_fee_usdt": 0}])
    plan["alternatives"][0]["estimate"].update({"probability_replay": .51,
        "median_progress_pct": 20})
    plan["alternatives"][1]["estimate"].update({"probability_replay": .50,
        "median_progress_pct": 20})

    result = operator._review_allocations(goal, {}, plan=plan)

    assert result["decision"] == "no_hacer_nada"
    assert "materialidad" in result["reason"]
    assert engine.closed == []


def test_free_cash_after_realized_loss_uses_attributable_cash_amount():
    from grid.meta_operator import MetaOperator

    class Core:
        def __init__(self): self.events = []
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))
        def valuation(self, _meta_id): return {"cash_usdt": "99.9186"}

    class DB:
        def get_grid(self, _grid_id):
            return {"id": 7, "status": "CLOSED", "symbol": "AAAUSDT", "capital_total": 70}

    operator = MetaOperator(Core(), DB(), None, None, None)
    operator._testnet_openability = lambda *_args, **_kwargs: {"allowed": True, "reason": None}
    operator._open_replacement = lambda _goal, _alternative, amount: {
        "grid_id": 8, "capital_usdt": amount}
    goal = {"id": 1, "capital_initial": 100, "config_snapshot": {
        "capital_per_coin_pct": 100, "max_grids": 2, "max_bots": 4}}
    plan = {"alternatives": [{"pieces": [{"piece_type": "grid", "symbol": "XRPUSDT",
        "capital_usdt": 99.9}], "estimate": {"probability": .5}}]}

    result = operator._add_piece_from_free_cash(goal, [{"grid_id": 7, "piece_type": "grid"}], plan)

    assert result["free_before_usdt"] == pytest.approx(99.9186)
    assert result["attributable_cash_usdt"] == pytest.approx(99.9186)
    assert result["allocated_reserve_free_usdt"] == pytest.approx(100)


def test_review_once_opens_a_real_piece_on_the_fake_exchange_using_free_cash(tmp_path):
    """Integración de punta a punta de 40c, parte C: una meta sin piezas tiene
    efectivo libre; review_once debe abrir un grid de verdad en el exchange
    falso (no un mock de apertura) y enlazarlo a la meta."""
    import time
    from types import SimpleNamespace

    from database.db_manager import DBManager
    from grid.engine import GridEngine
    from grid.meta import MetaCore
    from grid.meta_operator import MetaOperator
    from tests.grid_fakes import FakeExchange

    class TestExchange(FakeExchange):
        testnet = True

    exchange = TestExchange()
    db = DBManager(f"sqlite:///{tmp_path / 'free_cash.sqlite'}")
    db.add_or_reactivate_coin("XRPUSDT")
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False,
        grid_api_token="", max_grids_simultaneos=5, usdt_por_grid=100,
        capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=5,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30)
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings)
    core.start = lambda: None

    class Scanner:
        client = exchange
        def clock(self): return time.monotonic()

    operator = MetaOperator(core, db, Scanner(), engine, settings)
    plan = {"candidates": [{"symbol": "XRPUSDT", "excluded_reason": None}],
            "alternatives": [{"symbol": "XRPUSDT", "strategy": "simple", "reason": "efectivo libre",
                "pieces": [{"piece_type": "grid", "symbol": "XRPUSDT", "strategy": "simple",
                    "capital_usdt": 100, "n_levels": 4, "floor": 90, "ceiling": 110,
                    "mid_at_plan": 100.005}],
                "estimate": {"evidence_sufficient": True, "probability": .6}}]}
    operator._build_plan = lambda _goal: plan

    goal = core.create_goal(250, 10, profile="RAPIDO")
    core.start_goal(goal["id"])

    result = operator.review_once(goal["id"])

    assert result["decision"] == "abrir_pieza_con_efectivo_libre"
    linked = core._owned_grids(goal["id"])
    assert len(linked) == 1
    opened_grid = db.get_grid(linked[0]["grid_id"])
    assert opened_grid["symbol"] == "XRPUSDT"
    assert opened_grid["status"] in {"OPENING", "ACTIVE"}
    assert float(opened_grid["capital_total"]) == pytest.approx(100)


def test_full_api_driven_analysis_runs_real_scanner_advisor_and_replay_over_short_history(tmp_path):
    """Fase 40c, parte C: análisis disparado por la API sin plan sintético --
    `_build_plan` corre de verdad (Scanner, Advisor y replay reales) sobre un
    historial corto (25 días de velas de 5 min), no un plan mockeado."""
    import time
    import numpy as np
    import pandas as pd
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace

    from database.db_manager import DBManager
    from grid.engine import GridEngine
    from grid.meta import MetaCore
    from grid.meta_operator import MetaOperator
    from tests.grid_fakes import FakeExchange
    from api.routes import metas

    n_candles = 25 * 288  # 25 días de velas de 5 min: historial corto, no los 540 días de producción.
    prices = 100 * (1 + 0.03 * np.sin(np.linspace(0, 6.28 * 3, n_candles)))
    candles = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n_candles, freq="5min", tz="UTC"),
        "open": prices, "high": prices * 1.001, "low": prices * 0.999, "close": prices,
    })

    class TestExchange(FakeExchange):
        testnet = True
        def get_historical_klines(self, *_args, **_kwargs):
            return candles
        def get_symbol_info(self, _symbol):
            # stepSize fino para que el redondeo de cantidad no colapse celdas
            # de ~17 USDT a precio ~100 (edge case de notional, no lo que se prueba aquí).
            return {"filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "100000", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1000000", "stepSize": "0.01"},
                {"filterType": "NOTIONAL", "minNotional": "1", "applyMinToMarket": True},
            ]}

    exchange = TestExchange()
    db = DBManager(f"sqlite:///{tmp_path / 'real_analysis.sqlite'}")
    db.add_or_reactivate_coin("XRPUSDT")
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False,
        grid_api_token="test-token", max_grids_simultaneos=5, usdt_por_grid=100,
        capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=5,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30)
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings)
    core.start = lambda: None

    def advisor_fn(_symbol, _capital, _margin_pct, _max_days):
        # Recomendación real del Advisor (no mockeamos _build_plan/_candidate_alternatives);
        # la réplica/replay que sigue sí corre con el historial corto de verdad.
        return {"recommended_floor": 90, "recommended_ceiling": 110, "suggested_grids": 4,
                "suggested_strategy": "simple", "net_per_cycle_after_dust_usdt": 0.05}

    class Scanner:
        client = exchange
        def scan(self, capital, with_sim=False):
            return {"results": [{"symbol": "XRPUSDT", "eligible": True, "score": .9,
                "sigma_24h_open": .05, "hard_filters": []}]}
        def clock(self): return time.monotonic()

    operator = MetaOperator(core, db, Scanner(), engine, settings, advisor=advisor_fn)

    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.settings, app.state.db = settings, db
    app.state.grid_engine = engine
    app.state.testnet_client = exchange

    goal = core.create_goal(100, 10, profile="RAPIDO")
    with TestClient(app, headers={"X-API-Token": "test-token"}) as client:
        job = client.post(f"/api/metas/{goal['id']}/analysis").json()
        assert "job_id" in job, job
        state = None
        for _ in range(200):
            state = client.get(f"/api/metas/{goal['id']}/analysis/{job['job_id']}").json()
            if state["status"] != "PENDING":
                break
            time.sleep(.05)
        assert state["status"] == "COMPLETE", state

    plan = state["plan"]
    assert plan["candidates"][0]["symbol"] == "XRPUSDT"
    assert len(plan["pieces"]) == 1
    assert plan["pieces"][0]["symbol"] == "XRPUSDT"
    # El replay real produjo al menos una ventana histórica (no un valor sintético fijo).
    assert plan["estimated_time"]["rolling_windows"] >= 1
    assert plan["piece_estimates"][0]["symbol"] == "XRPUSDT"


def test_quantize_relative_range_steps_at_quarter_percent():
    from grid.meta_operator import MetaOperator

    operator = MetaOperator(object(), object(), object(), None, None)
    # 0.25% de paso: valores dentro del mismo paso cuantizan igual.
    assert operator._quantize_relative_range(0.899) == pytest.approx(0.9)
    assert operator._quantize_relative_range(0.9008) == pytest.approx(0.9)
    assert operator._quantize_relative_range(0.891) == pytest.approx(0.89)
    assert operator._quantize_relative_range(0) == 0


def test_advisor_cache_reanchors_on_small_drift_and_recalculates_past_threshold(monkeypatch):
    """Fase 40c, parte B4.2: el Advisor se cachea en relativo al precio de
    cuando se calculó; un movimiento de 0.1% reutiliza el caché, uno de 5% lo
    invalida porque supera el umbral de reanclaje (3%)."""
    import numpy as np
    import pandas as pd
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    n_candles = 50
    prices = np.full(n_candles, 100.0)

    class Client:
        price = 100.0
        def get_historical_klines(self, *_args):
            return pd.DataFrame({
                "timestamp": pd.date_range("2026-01-01", periods=n_candles, freq="5min", tz="UTC"),
                "open": prices, "high": prices * 1.001, "low": prices * 0.999, "close": prices})
        def get_symbol_info(self, _symbol):
            raise RuntimeError("sin filtros reales en esta prueba")
        def get_book_ticker(self, _symbol):
            return {"bid_price": self.price, "ask_price": self.price}

    client = Client()
    scanner = type("Scanner", (), {"client": client})()
    calls = []

    def advisor_fn(_symbol, _capital, _margin, _max_days):
        calls.append(client.price)
        return {"recommended_floor": 90, "recommended_ceiling": 110, "suggested_grids": 4,
                "suggested_strategy": "simple", "net_per_cycle_after_dust_usdt": 0.05}

    class DB:
        def get_readiness(self, _symbol): return {}
        def get_latest_vol_model_stats_forecast_at(self, *_args): return None
        def get_vol_model_stats_rows(self, *_args): return []
        def get_latest_prediction(self, *_args): return None

    operator = MetaOperator(object(), DB(), scanner, None, None,
                             advisor=advisor_fn, clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"advisor_s": 0, "replay_s": 0, "touch_signal_s": 0}
    # Sin ventanas: esta prueba es sobre el caché del Advisor, no sobre el replay.
    monkeypatch.setattr(module, "build_windows", lambda _candles, _days, _step: [])
    cfg = {"max_days": 20, "min_margin_pct": .3}
    row = {"symbol": "XRPUSDT", "eligible": True}

    operator._candidate_alternatives(row, 100, 10, cfg)
    assert len(calls) == 3  # tres márgenes; primera vez, todas llaman al Advisor real

    client.price = 100.1  # +0.1%: dentro del umbral, reancla desde caché
    operator._candidate_alternatives(row, 100, 10, cfg)
    assert len(calls) == 3

    client.price = 105  # +5%: supera el umbral de 3%; recalcula
    operator._candidate_alternatives(row, 100, 10, cfg)
    assert len(calls) == 6


def test_replay_cache_reused_for_subquantum_price_move_and_missed_past_the_step(monkeypatch):
    """Fase 40c, parte B4.1: la clave de replay usa el rango relativo cuantizado
    en pasos de 0.25%; un movimiento menor al paso reutiliza el mismo resultado
    y uno mayor obliga a recalcular."""
    import numpy as np
    import pandas as pd
    import grid.meta_operator as module
    from grid.meta_operator import MetaOperator

    n_candles = 300
    prices = 100 + 2 * np.sin(np.linspace(0, 6.28, n_candles))
    candles_frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n_candles, freq="5min", tz="UTC"),
        "open": prices, "high": prices * 1.001, "low": prices * 0.999, "close": prices})

    class Client:
        def get_historical_klines(self, *_args):
            return candles_frame
        def get_symbol_info(self, _symbol):
            raise RuntimeError("sin filtros reales en esta prueba")

    class Scanner:
        client = Client()

    operator = MetaOperator(object(), object(), Scanner(), None, None, clock=lambda: 1_800_000_000)
    operator._analysis_timing = {"advisor_s": 0, "replay_s": 0, "touch_signal_s": 0}
    build_calls = []
    real_build_windows = module.build_windows

    def counting_build_windows(candles, days, step):
        build_calls.append(1)
        return real_build_windows(candles, days, step)
    monkeypatch.setattr(module, "build_windows", counting_build_windows)
    cfg = {"max_days": 20, "min_margin_pct": .3}

    row_a = {"symbol": "XRPUSDT", "book": {"bid_price": 100.0, "ask_price": 100.02}}
    operator._evaluate(row_a, "simple", 100, 10, 90, 110, 4, cfg)
    assert len(build_calls) == 1

    row_b = {"symbol": "XRPUSDT", "book": {"bid_price": 100.08, "ask_price": 100.1}}  # ~+0.08%
    operator._evaluate(row_b, "simple", 100, 10, 90, 110, 4, cfg)
    assert len(build_calls) == 1, "un movimiento bajo el paso de 0.25% debe reutilizar el replay cacheado"

    row_c = {"symbol": "XRPUSDT", "book": {"bid_price": 101.0, "ask_price": 101.02}}  # +1%
    operator._evaluate(row_c, "simple", 100, 10, 90, 110, 4, cfg)
    assert len(build_calls) == 2, "un movimiento que cruza el paso de cuantización debe recalcular"
