"""Pruebas de contrato y recorrido operativo del torneo, solo con datos sintéticos."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.routes import metas
from data.market_store import CandleStore
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.meta import MetaCore
from grid.meta_operator import MetaOperator
from grid.monitor import GridMonitor
from tests.e2e.fake_exchange import FakeExchange


def test_exchange_falso_market_publica_quote_cero_y_comision_real():
    exchange = FakeExchange(initial_usdt=110)
    before = exchange.free["USDT"]
    order = exchange.place_order("AAAUSDT", "BUY", "1", order_type="MARKET",
        client_order_id="e2e-market-buy")
    trade = exchange.get_my_trades("AAAUSDT", order["order_id"])[0]
    assert order["status"] == "FILLED"
    assert order["cummulative_quote_qty"] == 0
    assert float(trade["quoteQty"]) > 0
    assert float(trade["commission"]) == pytest.approx(float(trade["quoteQty"]) * .001)
    assert float(before - exchange.free["USDT"]) == pytest.approx(
        float(trade["quoteQty"]) + float(trade["commission"]))


def test_exchange_falso_llena_limit_por_minimo_maximo_de_vela_15m():
    exchange = FakeExchange(initial_usdt=100)
    order = exchange.place_order("AAAUSDT", "BUY", "1", price="99",
        client_order_id="e2e-limit-buy")
    assert order["status"] == "NEW"
    assert exchange.locked["USDT"] > 0
    exchange.advance_15m("caida", 192, duration_days=2)
    assert exchange.get_order("AAAUSDT", order_id=order["order_id"])["status"] == "FILLED"
    assert exchange.get_my_trades("AAAUSDT", order["order_id"])[0]["quoteQty"]


def _historical_rows(symbol, start, bars, scale):
    initial = float(100 * (FakeExchange.symbols.index(symbol) + 1))
    rows = []
    previous = initial
    for bar in range(-bars, 0):
        value = initial * (1 + scale * (.04 * math.sin(2 * math.pi * bar / 288)
            + .005 * math.sin(bar * .173)))
        rows.append({"open_ts": int(start.timestamp()) + bar * 300,
            "open": previous, "high": max(previous, value) * 1.0001,
            "low": min(previous, value) * .9999, "close": value,
            "volume": 10000, "quote_volume": 1000000, "trades": 20})
        previous = value
    return rows


def _write_step_candles(store, exchange, start, step):
    end = exchange.now
    for symbol, candle in exchange.last_candles.items():
        rows = []
        opening, closing = float(candle["open"]), float(candle["close"])
        for offset in range(3):
            previous = opening + (closing - opening) * offset / 3
            current = opening + (closing - opening) * (offset + 1) / 3
            stamp = int((end - timedelta(minutes=15)).timestamp()) + offset * 300
            rows.append({"open_ts": stamp, "open": previous,
                "high": max(previous, current) * 1.0001,
                "low": min(previous, current) * .9999, "close": current,
                "volume": 10000, "quote_volume": 1000000, "trades": 20})
        store.upsert(symbol, "5m", rows, "fixture_e2e", role="operar")


def _v1_plan_stub(operator, exchange):
    """Alternativa operable para el camino v1, como el stub de regresión v1."""
    def build(goal, *, record=True, plan_kind="analisis", allocation_free_cash=None):
        competitor = (goal.get("config_snapshot") or {}).get("competidor")
        if competitor not in {None, "v1"}:
            raise AssertionError(f"stub v1 usado por {competitor}")
        ticker = exchange.get_book_ticker("CCCUSDT")
        mid = (float(ticker["bid_price"]) + float(ticker["ask_price"])) / 2
        capital = min(50.0, float(goal["capital_initial"]) *
            float((goal.get("config_snapshot") or {}).get("capital_per_coin_pct", 70)) / 100)
        piece = {"symbol": "CCCUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": capital, "n_levels": 4, "floor": mid * .85,
            "ceiling": mid * 1.01, "mid_at_plan": mid, "margin_pct": .7}
        alternative = {"alternative_id": "e2e-v1-aaa", "symbol": "CCCUSDT",
            "strategy": "simple", "pieces": [piece],
            "estimate": {"evidence_sufficient": True, "probability": .9,
                "probability_replay": .9, "days_median": 1, "days_p90": 2},
            "replay_probability": .9, "window_results": [{"pnl_usdt": 1.0}],
            "reason": "scanner offline; alternativa determinista del fixture"}
        return {"decision_id": "e2e-v1", "profile": goal["profile"],
            "alternatives": [alternative], "pieces": [piece],
            "candidates": [{"symbol": "CCCUSDT", "scanner_eligible": True,
                "eligible": True, "price": mid, "current_price": mid,
                "sigma_24h_open": .02, "score": .9, "excluded_reason": None}],
            "current_pieces": {}, "allocation_shadow": {"decision": "no_agregar",
                "selected": None, "reason": "candidato del fixture", "evaluated_top": []},
            "decision_choices": {"comparison_version": 2}}
    operator._build_plan = build


@pytest.mark.e2e
@pytest.mark.parametrize("scenario", ["subida", "caida", "oscilacion"])
def test_torneo_operativo_multidia_offline(tmp_path, monkeypatch, scenario):
    """Opera cinco metas con pasos de quince minutos y rutas reales de Meta/Grid."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import data.market_store

    started_wall = time.perf_counter()
    duration_days = 2
    exchange = FakeExchange(initial_usdt=500)
    exchange.unavailable_symbols.add("DDDUSDT")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exchange.now = start
    market_path = tmp_path / "market_data.db"
    store = CandleStore(market_path)
    monkeypatch.setattr(data.market_store, "market_db_path", lambda: market_path)
    for symbol in exchange.symbols:
        index = exchange.symbols.index(symbol)
        store.upsert(symbol, "5m", _historical_rows(symbol, start, 2 * 24 * 12,
            exchange.scales[symbol]), "fixture_e2e", role="operar")
        store.save_symbol_filters(symbol, exchange.get_symbol_info(symbol),
            fetched_at=start.isoformat())

    db = DBManager(f"sqlite:///{tmp_path / (scenario + '.sqlite')}")
    for symbol in exchange.symbols:
        db.add_or_reactivate_coin(symbol)
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=7,
        alerts_enabled=False, grid_api_token="e2e-local-token", max_grids_simultaneos=30,
        usdt_por_grid=50, capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=1,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30,
        grid_monitor_gap_minutes=20, grid_monitor_interval=900,
        grid_policy_enabled=False)
    clock = [start]
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings, clock=lambda: clock[0])
    monitor = GridMonitor(db, exchange, engine, settings, clock=lambda: clock[0])
    core.monitor = monitor

    class Scanner:
        client = exchange
        def clock(self):
            return clock[0].timestamp()
        def scan(self, capital, with_sim=False):
            return {"results": [{"symbol": symbol, "eligible": True, "score": .9,
                "sigma_24h_open": .02} for symbol in exchange.symbols]}

    operator = MetaOperator(core, db, Scanner(), engine, settings,
        clock=lambda: clock[0].timestamp())
    # El ciclo lógico no vence por el reloj de pared; todas las metas se revisan.
    operator.review_cycle_budget_s = float("inf")
    _v1_plan_stub(operator, exchange)
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.settings, app.state.db = settings, db
    app.state.grid_engine, app.state.testnet_client = engine, exchange
    app.state.grid_scan_service, app.state.vol_provider = operator.scanner, None

    with TestClient(app, headers={"X-API-Token": "e2e-local-token"}) as client:
        response = client.post("/api/metas/tournament/rounds", json={
            "competitors": ["v1", "v2", "cazador", "escalera", "control"],
            "capital_usdt": 50, "target_value": 1.5, "target_type": "USDT",
            "profile": "RAPIDO", "duration_days": 20})
        assert response.status_code == 200, response.text
        goals = response.json()["goals"]
        meta_by_competitor = {row["config_snapshot"]["competidor"]: row["id"]
            for row in goals}
        assert set(meta_by_competitor) == {"v1", "v2", "cazador", "escalera", "control"}
        with db.engine.begin() as conn:
            for row in goals:
                cfg = dict(row["config_snapshot"])
                cfg.update({"capital_per_coin_pct": 100, "max_loss_pct": 18,
                    "warning_loss_pct": 13.5, "loss_confirm_minutes": 2})
                conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == row["id"])
                    .values(config_snapshot=json.dumps(cfg, sort_keys=True)))
        from grid.engines.cazador import build_runtime_context
        cazador_id = meta_by_competitor["cazador"]
        cazador_goal = core.get_goal(cazador_id)
        cazador_value = core.valuation(cazador_id)
        assert "DDDUSDT" not in build_runtime_context(
            operator, cazador_goal, cazador_value)["markets"]

        latest_values = {}
        for step in range(1, duration_days * 24 * 4 + 1):
            exchange.advance_15m(scenario, step, duration_days=duration_days)
            clock[0] = exchange.now
            _write_step_candles(store, exchange, start, step)
            monitor.run_once()
            watched = core.run_once()
            latest_values = {row["meta_id"]: row.get("valuation") for row in watched
                if row.get("valuation") is not None}
            reviews = operator.review_active()
            assert reviews.get("status") == "completed", (scenario, step, reviews)

            for meta_id, value in latest_values.items():
                assert float(value["cash_usdt"]) >= -.01, (scenario, step, meta_id, value)
                assert value.get("cash_coherent") is True, (scenario, step, meta_id, value)
                assert float(value["value_usdt"]) == pytest.approx(
                    float(value["cash_usdt"]) + float(value["inventory_executable_usdt"]), abs=.01)
                equity = value.get("cash_coherence", {}).get("equity", {})
                assert abs(float(equity["equity_usdt"]) - float(equity["expected_equity_usdt"])) <= .01, (
                    scenario, step, meta_id, equity)
            balance = exchange.get_balance("USDT")["USDT"]
            attributable = sum(float(value["cash_usdt"]) for value in latest_values.values())
            assert attributable <= balance["free"] + balance["locked"] + .01, (
                scenario, step, attributable, balance)

    # Resultado final y acciones observadas por competidor.
    final = {name: core.valuation(meta_id) for name, meta_id in meta_by_competitor.items()}
    owned = {name: core._owned_grids(meta_id)
        for name, meta_id in meta_by_competitor.items()}
    history = {name: core._all_piece_links(meta_id)
        for name, meta_id in meta_by_competitor.items()}
    assert history["v1"], (scenario, core.ledger(meta_by_competitor["v1"])[-10:])
    assert history["control"], (scenario, core.ledger(meta_by_competitor["control"])[-10:])
    assert history["escalera"], (scenario, core.ledger(meta_by_competitor["escalera"])[-10:])
    assert history["cazador"] or any(row["event_type"] == "META_CAZADOR_AGREGADO"
        for row in core.ledger(meta_by_competitor["cazador"])), scenario
    assert not owned["v2"], "v2 se deja sin acción cuando no hay caché sintética compatible"
    v2_ledger = core.ledger(meta_by_competitor["v2"])
    assert any(row["event_type"] == "META_OPERATOR_NOOP" for row in v2_ledger), v2_ledger[-10:]

    if scenario == "oscilacion":
        ladder_ledger = core.ledger(meta_by_competitor["escalera"])
        assert any(row.get("event_type") == "SELL_FILLED" for link in history["escalera"]
            for row in db.list_grid_events(grid_id=int(link["grid_id"]), limit=100000))
        assert any(row["event_type"] == "META_ESCALERA_REARME" for row in ladder_ledger)
        assert any(row.get("event_type") == "SELL_FILLED" and
            float((row.get("details") or {}).get("cycle_pnl") or 0) != 0
            for link in history["escalera"] for row in
                db.list_grid_events(grid_id=int(link["grid_id"]), limit=100000))
    if scenario == "caida":
        cut = [name for name, meta_id in meta_by_competitor.items()
            if core.get_goal(meta_id)["status"] == "CORTADA_POR_PERDIDA"]
        assert cut, {name: (core.get_goal(meta_id)["status"], final[name].get("result_pct"))
            for name, meta_id in meta_by_competitor.items()}
        for name in cut:
            meta_id = meta_by_competitor[name]
            owned_ids = {int(link["grid_id"]) for link in core._all_piece_links(meta_id)}
            assert not [order for order in exchange.get_open_orders()
                if any(str(order.get("client_order_id") or "").startswith(f"g{grid_id}")
                    for grid_id in owned_ids)]

    for meta_id in meta_by_competitor.values():
        assert not any(row["event_type"] == "META_VALUACION_INCOHERENTE"
            for row in core.ledger(meta_id)), scenario
    with sqlite3.connect(market_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candles WHERE interval='5m'").fetchone()[0] >= 4 * 2 * 24 * 12
        assert conn.execute("SELECT COUNT(*) FROM symbol_filters").fetchone()[0] == 4
    print("TORNEO_E2E_RESULTADO " + json.dumps({"guion": scenario,
        "result_pct": {name: value.get("result_pct") for name, value in final.items()},
        "segundos": round(time.perf_counter() - started_wall, 2)}, sort_keys=True))


@pytest.mark.e2e
def test_ronda_uno_abre_competidores_en_dos_revisiones_con_filtros_reales(
        tmp_path, monkeypatch):
    """Arranca los cinco competidores con 50 USDT y el perfil RAPIDO al 70 %."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import data.market_store

    exchange = FakeExchange(initial_usdt=500)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exchange.now = start
    market_path = tmp_path / "market_data.db"
    store = CandleStore(market_path)
    monkeypatch.setattr(data.market_store, "market_db_path", lambda: market_path)
    for symbol in exchange.symbols:
        store.upsert(symbol, "5m", _historical_rows(symbol, start, 2 * 24 * 12,
            exchange.scales[symbol]), "fixture_e2e", role="operar")
        filters = exchange.get_symbol_info(symbol)
        assert next(row for row in filters["filters"]
            if row["filterType"] == "NOTIONAL")["minNotional"] == "5"
        store.save_symbol_filters(symbol, filters, fetched_at=start.isoformat())

    db = DBManager(f"sqlite:///{tmp_path / 'round1.sqlite'}")
    for symbol in exchange.symbols:
        db.add_or_reactivate_coin(symbol)
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=7,
        alerts_enabled=False, grid_api_token="e2e-local-token", max_grids_simultaneos=30,
        usdt_por_grid=50, capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=1,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30,
        grid_monitor_gap_minutes=20, grid_monitor_interval=900,
        grid_policy_enabled=False)
    clock = [start]
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings, clock=lambda: clock[0])
    monitor = GridMonitor(db, exchange, engine, settings, clock=lambda: clock[0])
    core.monitor = monitor

    class Scanner:
        client = exchange
        def clock(self):
            return clock[0].timestamp()
        def scan(self, capital, with_sim=False):
            return {"results": [{"symbol": symbol, "eligible": True, "score": .9,
                "sigma_24h_open": .02} for symbol in exchange.symbols]}

    operator = MetaOperator(core, db, Scanner(), engine, settings,
        clock=lambda: clock[0].timestamp())
    operator.review_cycle_budget_s = float("inf")
    _v1_plan_stub(operator, exchange)
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.settings, app.state.db = settings, db
    app.state.grid_engine, app.state.testnet_client = engine, exchange
    app.state.grid_scan_service, app.state.vol_provider = operator.scanner, None
    with TestClient(app, headers={"X-API-Token": "e2e-local-token"}) as client:
        response = client.post("/api/metas/tournament/rounds", json={
            "competitors": ["v1", "v2", "cazador", "escalera", "control"],
            "capital_usdt": 50, "target_value": 1.5, "target_type": "USDT",
            "profile": "RAPIDO", "duration_days": 20})
        assert response.status_code == 200, response.text
        goals = response.json()["goals"]
        ids = {row["config_snapshot"]["competidor"]: int(row["id"]) for row in goals}
        assert all(float(row["capital_initial"]) == 50 for row in goals)
        assert all(row["config_snapshot"]["capital_per_coin_pct"] == 70 for row in goals)
        for review_index in range(2):
            clock[0] = start + timedelta(minutes=15 * review_index)
            exchange.now = clock[0]
            cycle = operator.review_active()
            assert cycle["status"] == "completed", cycle

        histories = {name: core._owned_grids(meta_id) for name, meta_id in ids.items()}
        for name in ("v1", "cazador", "escalera", "control"):
            assert histories[name], (name, core.ledger(ids[name])[-10:])
            assert all(float(db.get_grid(row["grid_id"])["capital_total"]) >= 10
                for row in histories[name])
        assert histories["v2"] == []
        v2_reviews = [row for row in core.ledger(ids["v2"])
            if row["event_type"] == "META_OPERATOR_NOOP"]
        assert v2_reviews and all("esperando cach" in row["details"]["reason"].lower()
            for row in v2_reviews)

        ladder_grids = [db.get_grid(row["grid_id"]) for row in histories["escalera"]]
        assert len(ladder_grids) == 4
        assert {row["symbol"] for row in ladder_grids}.__len__() == 2
        assert all(float(row["capital_total"]) == pytest.approx(12) for row in ladder_grids)
        assert all(row["n_levels"] == 1 for row in ladder_grids)
        assert all(sum(grid["symbol"] == row["symbol"] for grid in ladder_grids) == 2
            for row in ladder_grids)

        control_grid = db.get_grid(histories["control"][0]["grid_id"])
        assert control_grid["n_levels"] >= 4
        assert float(control_grid["capital_total"]) / control_grid["n_levels"] >= 6
        board = core.tournament_scoreboard(1)
        control_row = next(row for row in board if row["competidor"] == "control")
        assert control_row["control_n_levels"] == control_grid["n_levels"]
        assert control_row["control_capital_per_level_usdt"] >= 6

        cazador_value = core.valuation(ids["cazador"])
        assert cazador_value["cash_coherent"] is True
        revalidated = client.post(
            f"/api/metas/{ids['cazador']}/coherence/revalidate", json={})
        assert revalidated.status_code == 200, revalidated.text
        assert revalidated.json()["coherent"] is True
        cazador_grid_ids = {int(row["grid_id"]) for row in histories["cazador"]}
        assert any(event["event_type"] == "GRID_COMPRA_INICIAL"
            for grid_id in cazador_grid_ids
            for event in db.list_grid_events(grid_id=grid_id, limit=100000))
        assert not any(row["event_type"] == "META_VALUACION_INCOHERENTE"
            for row in core.ledger(ids["cazador"]))
