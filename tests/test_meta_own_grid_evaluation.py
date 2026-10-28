"""La evaluación de reemplazo excluye solo una pieza de la propia Meta."""
import ast
import copy
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from types import MethodType, SimpleNamespace

import pytest

from data.exchange_filters import SymbolFilters
from grid.meta_operator import MetaOperator
from tests.grid_fakes import FakeExchange, fake_symbol_info


def plan_setup(*, owner="own", status="ACTIVE", other_same_symbol=False, max_grids=5):
    records = {12: {"id": 12, "status": status, "symbol": "JUPUSDT", "strategy": "smart",
        "capital_total": 70, "range_low": 90, "range_high": 110, "n_levels": 4,
        "created_at": datetime.fromtimestamp(1_800_000_000 - 7200, timezone.utc)}}
    if other_same_symbol:
        records[13] = {**records[12], "id": 13}

    class DB:
        def get_grid(self, grid_id): return records.get(grid_id)
        def get_grid_levels(self, _grid_id):
            return [{"held_qty": .4, "entry_price": 100, "price": 100, "sell_price": 110, "state": "SELL_OPEN"}]
        def get_active_coins(self): return [{"symbol": s} for s in ("JUPUSDT", "XRPUSDT")]
        def get_readiness(self, _symbol): return {}
        def get_latest_vol_model_stats_forecast_at(self, *_args): return None
        def get_vol_model_stats_rows(self, *_args): return []
        def get_latest_prediction(self, *_args): return None
        def list_grids_by_status(self, statuses):
            return [row for row in records.values() if row["status"] in statuses]
        def count_open_grids(self):
            return sum(row["status"] in {"OPENING", "ACTIVE", "PAUSED", "CLOSING"} for row in records.values())

    goal = {"id": 3, "status": "ACTIVE", "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 10, "config_snapshot": {"capital_per_coin_pct": 70, "max_grids": 1,
            "max_bots": 0, "max_days": 20, "min_margin_pct": .3, "warning_loss_pct": 13.5}}

    class Core:
        def __init__(self): self.events = []
        def _owned_grids(self, meta_id):
            return [{"grid_id": 12, "piece_type": "grid"}] if owner == "own" and meta_id == 3 else []
        def get_goal(self, _meta_id): return goal
        def valuation(self, _meta_id): return {"status": "KNOWN", "result_pct": 0, "cash_usdt": 30}
        def ledger(self, _meta_id): return self.events
        def _ledger(self, meta_id, run_id, event_type, details, **kwargs):
            self.events.append({"event_type": event_type, "details": copy.deepcopy(details),
                "grid_id": kwargs.get("grid_id"), "ts": datetime.fromtimestamp(1_800_000_000, timezone.utc)})

    class Exchange(FakeExchange):
        def get_open_orders(self, symbol=None):
            return [{**row, "clientOrderId": row["client_order_id"], "origQty": row["quantity"]}
                for row in super().get_open_orders(symbol)]

    exchange = Exchange()
    info = fake_symbol_info()
    info["filters"][1].update({"minQty": "0.001", "stepSize": "0.001"})
    exchange.filters = SymbolFilters.from_symbol_info(info)
    exchange.place_order("JUPUSDT", "SELL", .4, 110, client_order_id="g12L3S1")
    if other_same_symbol:
        exchange.place_order("JUPUSDT", "SELL", .4, 110, client_order_id="g13L3S1")

    class Scanner:
        client = exchange
        def scan(self, **_kwargs):
            return {"results": [{"symbol": s, "eligible": True, "score": .8,
                "sigma_24h_open": .05} for s in ("JUPUSDT", "XRPUSDT")]}

    settings = SimpleNamespace(max_grids_simultaneos=max_grids, same_coin_sell_tolerance_pct=.05,
        grid_min_step_pct=.003, capital_max_por_nivel_pct=.3)
    engine = SimpleNamespace(exchange=exchange,
        _market_context=lambda symbol: (exchange.filters,
            {**exchange.get_book_ticker(symbol), "avg_price": exchange.avg}, exchange.avg))
    core, db = Core(), DB()
    operator = MetaOperator(core, db, Scanner(), engine, settings, clock=lambda: 1_800_000_000)
    options_seen = []

    def options(row, capital, target, cfg):
        symbol = row["symbol"]
        successful = symbol == "XRPUSDT"
        windows = [{"window_start": f"ventana-{i:02d}", "pnl_usdt": target * (1.2 if successful or i % 2 else .2),
            "days": 2 if successful else 12, "drawdown_pct": 1, "step_days": 7} for i in range(12)]
        piece = {"symbol": symbol, "strategy": "smart" if symbol == "JUPUSDT" else "simple",
            "piece_type": "grid", "capital_usdt": capital, "n_levels": 4, "floor": 90, "ceiling": 110}
        alternative = {"symbol": symbol, "strategy": piece["strategy"], "pieces": [piece],
            "estimate": {"probability": 1 if successful else .5, "evidence_sufficient": True},
            "replay_probability": 1 if successful else .5, "shadow_probability": 1 if successful else .5,
            "window_results": windows, "signals_used": [], "shadow_signals": [], "advisor": {}}
        options_seen.append(alternative)
        return [alternative], []

    operator._candidate_alternatives = options
    return operator, core, goal, options_seen, records


@pytest.mark.parametrize("status", ["ACTIVE", "PAUSED", "OPENING", "HOLDING"])
def test_plan_includes_own_jup_with_live_sells_and_records_excluded_id(status):
    operator, core, goal, options, _ = plan_setup(status=status)
    orders = copy.deepcopy(operator.engine.exchange.get_open_orders())
    plan = operator._build_plan(goal, record=False, plan_kind="revision")
    jup = next(row for row in plan["alternatives"] if row["symbol"] == "JUPUSDT")
    assert len(jup["pieces"]) == 1
    assert jup["testnet_openability"]["own_grid_excluded_from_check"] == 12
    assert next(row for row in options if row["symbol"] == "JUPUSDT")["testnet_openability"]["allowed"]
    assert next(row for row in plan["candidates"] if row["symbol"] == "JUPUSDT")["own_grid_excluded_from_check"] == 12
    compact = operator._plan_ledger_payload(plan, plan["candidates"], jup, plan_kind="revision")
    assert next(row for row in compact["candidates"] if row["symbol"] == "JUPUSDT")["own_grid_excluded_from_check"] == 12
    assert operator.engine.exchange.get_open_orders() == orders
    if status in {"ACTIVE", "PAUSED"}:
        operator._build_plan = lambda *_args, **_kwargs: plan
        assert operator.review_once(3)["decision"] == "no_hacer_nada"
        evaluation = next(row["details"] for row in core.events
            if row["event_type"] == "META_OPERATOR_ROTATION_EVALUATED")
        assert evaluation["current"]["estimate"]["probability_replay"] == .5
        review = next(row["details"] for row in core.events
            if row["event_type"] == "META_OPERATOR_REVIEW_DECISION")
        assert not review["decision_choices"].get("current_alternative_missing")


@pytest.mark.parametrize("owner", ["other_meta", "manual"])
def test_other_meta_or_manual_jup_still_conflicts(owner):
    operator, _, goal, _, _ = plan_setup(owner=owner)
    plan = operator._build_plan(goal, record=False)
    jup = next(row for row in plan["candidates"] if row["symbol"] == "JUPUSDT")
    assert "conflicto con niveles de venta vivos" in jup["excluded_reason"]
    assert "own_grid_excluded_from_check" not in jup
    assert all(row["symbol"] != "JUPUSDT" for row in plan["alternatives"])


def test_excluding_own_grid_keeps_other_grid_sells_as_conflicts():
    operator, _, goal, _, _ = plan_setup(other_same_symbol=True)
    plan = operator._build_plan(goal, record=False)
    jup = next(row for row in plan["candidates"] if row["symbol"] == "JUPUSDT")
    assert jup["own_grid_excluded_from_check"] == 12
    assert "conflicto con niveles de venta vivos" in jup["excluded_reason"]


def test_current_piece_uses_live_geometry_even_when_foreign_sell_blocks_jup_destination(monkeypatch):
    operator, core, goal, _, records = plan_setup()
    goal["config_snapshot"]["max_grids"] = 2
    records[14] = {**records[12], "id": 14, "status": "HOLDING", "symbol": "JUPUSDT"}
    operator._testnet_book_status = lambda symbol: ({"state": "one_sided", "reason": "sin asks"}
        if symbol == "JUPUSDT" else {"state": "two_sided", "reason": None})
    evaluated = []

    def evaluate(row, strategy, capital, target, low, high, n, cfg, *, margin_pct=None):
        evaluated.append((row["symbol"], strategy, low, high, n, capital))
        return {"symbol": row["symbol"], "strategy": strategy,
            "pieces": [{"symbol": row["symbol"], "strategy": strategy, "piece_type": "grid",
                "capital_usdt": capital, "n_levels": n, "floor": low, "ceiling": high}],
            "estimate": {"probability": .4, "probability_replay": .4, "days_median": 9,
                "days_p10": 5, "days_p90": 14, "median_progress_pct": 42,
                "independent_windows": 8, "evidence_sufficient": True}}

    monkeypatch.setattr(operator, "_evaluate", evaluate)
    advisor_calls, advisor_inputs = [], []
    operator.advisor = lambda *args: (advisor_calls.append(args) or
        {"net_per_cycle_after_dust_usdt": .5})
    def apply_intelligence(alternative, *_args, advisor_result=None, **_kwargs):
        advisor_inputs.append(advisor_result)
        alternative["estimate"].update({"probability": .45, "probability_adjusted": .45})
    monkeypatch.setattr(operator, "_apply_intelligence", apply_intelligence)
    plan = operator._build_plan(goal, record=False, plan_kind="revision")

    current = plan["current_pieces"]["12"]
    assert current["piece"]["floor"] == 90 and current["piece"]["ceiling"] == 110
    assert current["piece"]["n_levels"] == 4 and current["piece"]["capital_usdt"] == 70
    assert current["estimate"]["probability_replay"] == .4
    assert current["estimate"]["probability"] == .45
    assert advisor_calls[0][0] == "JUPUSDT"
    assert advisor_inputs == [{"net_per_cycle_after_dust_usdt": .5}]
    assert evaluated[0] == ("JUPUSDT", "smart", 90, 110, 4, 70.0)
    assert all(not (row["symbol"] == "JUPUSDT" and row["strategy"] == "smart")
        for row in plan["alternatives"])
    blocked = operator._testnet_openability({"symbol": "JUPUSDT", "strategy": "smart",
        "piece_type": "grid", "capital_usdt": 70, "n_levels": 4, "floor": 90, "ceiling": 110},
        goal, replacing_grid_id=12)
    assert blocked["allowed"] is False
    assert "conflicto con niveles de venta vivos" in blocked["reason"]

    destination = {"alternative_id": "dest-xrp", "symbol": "XRPUSDT", "strategy": "simple",
        "pieces": [{"symbol": "XRPUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": 70, "n_levels": 4, "floor": 90, "ceiling": 110}],
        "estimate": {"probability": .8, "probability_replay": .8,
            "days_median": 5, "days_p90": 8, "median_progress_pct": 55,
            "evidence_sufficient": True}}
    plan["alternatives"].append(destination)
    gate = operator._rotation_gate(goal, records[12], {"grid_id": 12}, destination, None, plan)
    rotation = next(row["details"] for row in core.events
        if row["event_type"] == "META_OPERATOR_ROTATION_EVALUATED")
    assert rotation["current"]["estimate"]["probability_replay"] == .4
    assert gate["reason"] != "no hay estimación comparable de la pieza actual"
    choices = operator._active_review_choices(plan, {"grid_id": 12, "meta_id": 3,
        "decision": "no_hacer_nada", "reason": "mantener"}, meta_id=3)
    assert choices["current_piece_estimate"]["piece"]["floor"] == 90
    assert not choices.get("current_alternative_missing")


def test_grid_count_excludes_only_counted_own_grid_and_never_releases_holding_slot():
    operator, _, goal, _, records = plan_setup(max_grids=1)
    piece = {"symbol": "JUPUSDT", "strategy": "smart", "piece_type": "grid", "capital_usdt": 70,
        "floor": 90, "ceiling": 110, "n_levels": 4}
    assert operator._testnet_openability(piece, goal, replacing_grid_id=12)["allowed"]
    assert "máximo de grids" in operator._testnet_openability(piece, goal)["reason"]
    records[12]["status"] = "HOLDING"
    records[13] = {**records[12], "id": 13, "status": "ACTIVE", "symbol": "XRPUSDT"}
    assert "máximo de grids" in operator._testnet_openability(piece, goal, replacing_grid_id=12)["reason"]


def test_held_proceeds_are_estimated_only_for_replacement_not_keeping_inventory():
    operator, _, goal, _, _ = plan_setup()
    operator.engine.exchange.free_usdt = Decimal(0)
    piece = {"symbol": "JUPUSDT", "strategy": "smart", "piece_type": "grid", "capital_usdt": 70,
        "floor": 90, "ceiling": 110, "n_levels": 4}
    assert operator._testnet_openability(piece, goal, replacing_grid_id=12, include_held_proceeds=True)["allowed"]
    assert not operator._testnet_openability(piece, goal, replacing_grid_id=12, include_held_proceeds=False)["allowed"]


def test_no_own_symbol_plan_probabilities_and_decision_match_a25818b(monkeypatch):
    import grid.meta_operator as module

    source = subprocess.check_output(["git", "show", "a25818b:grid/meta_operator.py"]).decode("utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "MetaOperator")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_plan")
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "a25818b:_build_plan", "exec"), namespace)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex="plan-fijo"))
    actual, _, goal, _, _ = plan_setup(owner="manual")
    reference, _, _, _, _ = plan_setup(owner="manual")
    reference._build_plan = MethodType(namespace["_build_plan"], reference)
    current_plan = actual._build_plan(goal, record=False)
    old_plan = reference._build_plan(goal, record=False)
    current_plan.pop("timings_s")
    old_plan.pop("timings_s")
    from grid.meta_learning import REPLAY_METHOD_VERSION
    assert current_plan.pop("base_method_version") == REPLAY_METHOD_VERSION
    assert old_plan.pop("base_method_version") == REPLAY_METHOD_VERSION
    assert set(current_plan) == set(old_plan)
    assert len(current_plan["alternatives"]) == len(old_plan["alternatives"])
    for current, previous in zip(current_plan["alternatives"], old_plan["alternatives"]):
        assert current["alternative_id"] == previous["alternative_id"]
        assert current["replay_probability"] == previous["replay_probability"]
        assert current["estimate"]["probability"] == previous["estimate"]["probability"]
        assert current["pieces"] == previous["pieces"]
    assert "current_pieces" in current_plan and "allocation_shadow" in current_plan
    assert "centered_signal_shadow" in current_plan
    assert current_plan["current_pieces"] == {}
    assert current_plan["allocation_shadow"]["decision"] == "agregar"
    assert current_plan["allocation_shadow"]["selected"]["capital_usdt"] <= 30
    assert "capital_usage" in current_plan and current_plan["capital_usage"]["initial_capital_usdt"] == 100
    assert current_plan["capital_usage"]["unused_usdt"] == old_plan["capital_usage"]["unused_usdt"]
    assert current_plan["use_extended_allocation"] is False
    assert current_plan["allocation_tie_tolerance_pp"] == 5
    assert current_plan["centered_signal_shadow"]["alternatives_count"] == len(current_plan["alternatives"])
    assert all("allocation_shadow" not in item for item in current_plan["alternatives"])
    comparable = {key: value for key, value in current_plan.items() if key in old_plan and key != "allocation_shadow"}
    old_comparable = {key: value for key, value in old_plan.items() if key != "allocation_shadow"}
    assert comparable == old_comparable
    for field in ("decision", "selected", "best_evaluated", "evaluated_top",
                  "probability_before", "reason"):
        assert current_plan["allocation_shadow"].get(field) == old_plan["allocation_shadow"].get(field)
    for operator in (actual, reference):
        operator._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    assert actual._review_allocations(goal, {}, plan=current_plan) == reference._review_allocations(goal, {}, plan=old_plan)


def test_allocation_shadow_keeps_source_testnet_checked_alternative_id(monkeypatch):
    import grid.meta_allocation as allocation_module

    seen = {}
    def capture(plan, *_args, **_kwargs):
        seen["alternatives"] = plan["alternatives"]
        return {"decision": "no_agregar", "selected": None, "evaluated_top": []}
    monkeypatch.setattr(allocation_module, "evaluate_extended_allocation", capture)
    operator, _, goal, _, _ = plan_setup(owner="manual")
    monkeypatch.setattr(operator, "_allocation_curve_variants",
        lambda candidates, *_args: (candidates, {"conteo_por_motivo": {}, "ejemplos": [], "no_evaluados": 0}))
    plan = operator._build_plan(goal, record=False)
    by_id = {row["alternative_id"]: row for row in plan["alternatives"]}
    assert seen["alternatives"]
    assert all(row["alternative_id"] in by_id for row in seen["alternatives"])
    assert all(row["testnet_openability"].get("allowed") is True
        for row in seen["alternatives"])
    assert set(("preparacion_s", "evaluacion_s", "cierre_s", "total_s")) <= set(
        plan["allocation_shadow"]["timings_s"])


def test_shadow_allocation_regenerates_geometry_for_each_available_amount(monkeypatch):
    operator, _, goal, _, _ = plan_setup(owner="manual")
    generated_amounts = []
    def generate(row, capital, target, cfg):
        generated_amounts.append(capital)
        piece = {"symbol": row["symbol"], "strategy": "simple", "piece_type": "grid",
            "capital_usdt": capital, "n_levels": 4, "floor": 100-capital/100,
            "ceiling": 100+capital/100, "margin_pct": .3}
        return [{"symbol": row["symbol"], "pieces": [piece],
            "estimate": {"probability_replay": .2, "evidence_sufficient": True}, "replay_probability": .2,
            "window_results": [{"window_start": "w1", "curve": {"curve_format_version": 3}}],
            "alternative_id": "new-geometry"}], []
    monkeypatch.setattr(operator, "_candidate_alternatives", generate)
    monkeypatch.setattr(operator, "_testnet_openability", lambda *_args, **_kwargs: {"allowed": True})
    original = {"symbol": "XRPUSDT", "pieces": [{"symbol": "XRPUSDT", "piece_type": "grid",
        "strategy": "simple", "capital_usdt": 70, "n_levels": 8, "floor": 80, "ceiling": 120}],
        "replay_probability": .8, "alternative_id": "large-geometry"}
    selected, discards = operator._allocation_curve_variants([original],
        [{"symbol": "XRPUSDT"}], goal, goal["config_snapshot"], 30, {})
    assert generated_amounts
    assert generated_amounts[0] < 30
    assert selected[0]["capital_variants"]
    variant_piece = selected[0]["capital_variants"][0]["pieces"][0]
    assert variant_piece["floor"] != 80
    assert variant_piece["capital_usdt"] == generated_amounts[0]
    assert discards["conteo_por_motivo"] == {}


def test_shadow_allocation_evaluates_small_grids_and_bots_with_testnet_notional(monkeypatch):
    import grid.meta_operator as module
    operator, _, goal, _, _ = plan_setup(owner="manual")
    generated = []
    def generate(row, capital, _target, _cfg):
        kind = "grid" if capital >= 22 else "individual_bot_grid"
        piece = {"symbol": row["symbol"], "strategy": "simple", "piece_type": kind,
            "capital_usdt": capital, "n_levels": 4 if kind == "grid" else 1,
            "floor": 99.0, "ceiling": 101.0, "margin_pct": .3}
        generated.append((kind, capital))
        return [{"symbol": row["symbol"], "pieces": [piece],
            "estimate": {"probability_replay": .2, "evidence_sufficient": True},
            "replay_probability": .2, "window_results": [{"window_start": "w1",
                "curve": {"curve_format_version": 3}}], "alternative_id": f"{kind}-{capital}"}], []
    monkeypatch.setattr(operator, "_candidate_alternatives", generate)
    monkeypatch.setattr(operator, "_testnet_openability", lambda piece, *_args, **_kwargs:
        {"allowed": piece["capital_usdt"] >= (22.0 if piece["piece_type"] == "grid" else 10.0),
         "reason": None if piece["capital_usdt"] >= (22.0 if piece["piece_type"] == "grid" else 10.0)
                else "minimum notional"})
    sources = [{"symbol": symbol, "pieces": [{"symbol": symbol, "piece_type": kind,
        "strategy": "simple", "capital_usdt": 70, "n_levels": 4,
        "floor": 90, "ceiling": 110}], "testnet_openability": {"allowed": True},
        "replay_probability": .2, "alternative_id": f"source-{kind}"}
        for symbol, kind in (("XRPUSDT", "grid"), ("JUPUSDT", "individual_bot_grid"))]
    variants, discards = operator._allocation_curve_variants(sources,
        [{"symbol": "XRPUSDT"}, {"symbol": "JUPUSDT"}], goal,
        {**goal["config_snapshot"], "max_grids": 3, "max_bots": 3,
            "capital_per_coin_pct": 100}, 30, {})
    by_kind = {row["pieces"][0]["piece_type"]: row for row in variants}
    assert {"grid", "individual_bot_grid"} <= set(by_kind), discards
    grid_caps = [piece["pieces"][0]["capital_usdt"] for piece in by_kind["grid"]["capital_variants"]]
    bot_caps = [piece["pieces"][0]["capital_usdt"] for piece in by_kind["individual_bot_grid"]["capital_variants"]]
    assert 20.0 <= min(grid_caps) < 30 and 10.0 <= min(bot_caps) < 30
    assert any(kind == "individual_bot_grid" and amount < 10 for kind, amount in generated)
    assert any("minimum notional" in reason for reason in discards["conteo_por_motivo"])

    def replay(alternatives, *_args, **_kwargs):
        piece = alternatives[-1]["pieces"][0]
        probability = .20 if piece["piece_type"] == "individual_bot_grid" else .12
        return {"probability": probability, "median_progress_pct": 20,
            "days_median": 4, "worst_drawdown_pct": 2}, []
    monkeypatch.setattr(module, "combine_aligned_replays", replay)
    from grid.meta_allocation import evaluate_extended_allocation
    result = evaluate_extended_allocation({"alternatives": variants}, {},
        free_cash=30, capital_initial=100, target_usdt=10, window_days=20,
        limits={**goal["config_snapshot"], "max_grids": 3, "max_bots": 3,
            "capital_per_coin_pct": 100})
    assert result["decision"] == "agregar"
    assert result["selected"]["piece_type"] == "individual_bot_grid"
    assert result["selected"]["capital_usdt"] >= 10
    assert result["selected"]["delta_probability_pp"] >= 2


def test_shadow_allocation_records_minimum_cell_discard(monkeypatch):
    operator, _, goal, _, _ = plan_setup(owner="manual")
    source = {"symbol": "XRPUSDT", "pieces": [{"symbol": "XRPUSDT", "piece_type": "grid"}],
        "replay_probability": .8}
    variants, discards = operator._allocation_curve_variants([source],
        [{"symbol": "XRPUSDT"}], goal, goal["config_snapshot"], 4, {})
    assert variants == []
    assert discards["conteo_por_motivo"]["efectivo libre inferior al mínimo de celda"] == 1
    assert discards["ejemplos"][0] == {"symbol": "XRPUSDT", "piece_type": "grid",
        "amount_usdt": 4.0, "stage": "mínimo de celda", "reason": "efectivo libre inferior al mínimo de celda"}


def test_shadow_allocation_empty_input_has_counted_reason_and_stage_timings():
    operator, _, goal, _, _ = plan_setup(owner="manual")
    variants, discards = operator._allocation_curve_variants([], [], goal,
        goal["config_snapshot"], 30, {})
    assert variants == []
    assert discards["conteo_por_motivo"] == {"sin alternativas de entrada (0)": 1}
    assert discards["ejemplos"][0]["stage"] == "entrada"
    assert discards["status"] == "completed"
    assert "total" in discards["timings_s"]


def test_shadow_allocation_stops_candidate_generation_at_named_25_second_budget(monkeypatch):
    import grid.meta_operator as module
    assert module.ALLOCATION_SHADOW_EVALUATION_BUDGET_S == 25.0
    operator, _, goal, _, _ = plan_setup(owner="manual")
    clock_values = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 31.0, 32.0, 33.0))
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(clock_values))
    called = []
    def generate(_row, _capital, _target, cfg):
        if module.time.perf_counter() >= cfg["_allocation_shadow_deadline"]:
            raise TimeoutError("presupuesto agotado")
        called.append(True)
        return [], []
    monkeypatch.setattr(operator, "_candidate_alternatives", generate)
    source = {"symbol": "XRPUSDT", "pieces": [{"symbol": "XRPUSDT", "piece_type": "grid"}],
        "replay_probability": .8}
    _, discards = operator._allocation_curve_variants([source],
        [{"symbol": "XRPUSDT"}], goal, goal["config_snapshot"], 30, {})
    assert not called
    assert discards["no_evaluados"] >= 1
    assert any(reason.startswith("presupuesto agotado")
        for reason in discards["conteo_por_motivo"])
    assert discards["status"] == "budget_exceeded"


def test_real_review_decision_matches_head_when_shadow_allocator_changes(monkeypatch):
    import grid.meta_operator as module
    source = subprocess.check_output(["git", "show", "a25818b:grid/meta_operator.py"]).decode("utf-8")
    cls = next(node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "MetaOperator")
    method = next(node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_review_allocations")
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "a25818b:_review_allocations", "exec"), namespace)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex="plan-fijo"))
    actual, _, goal, _, _ = plan_setup(owner="manual")
    reference, _, _, _, _ = plan_setup(owner="manual")
    reference._review_allocations = MethodType(namespace["_review_allocations"], reference)
    plan = actual._build_plan(goal, record=False)
    actual._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    reference._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    valuation = {"cash_usdt": 0, "free_capital_usdt": 0}
    actual_result = actual._review_allocations(goal, valuation, plan=plan)
    head_result = reference._review_allocations(goal, valuation, plan=plan)
    assert actual_result == head_result
