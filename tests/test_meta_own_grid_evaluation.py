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


def test_no_own_symbol_plan_probabilities_and_decision_match_dd10892(monkeypatch):
    import grid.meta_operator as module

    source = subprocess.check_output(["git", "show", "dd10892:grid/meta_operator.py"]).decode("utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "MetaOperator")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_plan")
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "dd10892:_build_plan", "exec"), namespace)
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
    assert current_plan == old_plan
    for operator in (actual, reference):
        operator._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    assert actual._review_allocations(goal, {}, plan=current_plan) == reference._review_allocations(goal, {}, plan=old_plan)
