"""Recorrido temporal del torneo con el API, el vigilante y el operador reales."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.routes import metas
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.meta import MetaCore
from grid.meta_operator import MetaOperator
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


def test_exchange_falso_llena_limit_al_cruzar_y_libera_reserva():
    exchange = FakeExchange(initial_usdt=100)
    order = exchange.place_order("AAAUSDT", "BUY", "1", price="99",
        client_order_id="e2e-limit-buy")
    assert order["status"] == "NEW"
    assert exchange.locked["USDT"] > 0
    exchange.advance("caida", 864)
    assert exchange.get_order("AAAUSDT", order_id=order["order_id"])["status"] == "FILLED"
    assert exchange.get_my_trades("AAAUSDT", order["order_id"])[0]["quoteQty"]


@pytest.mark.e2e
@pytest.mark.parametrize("scenario", ["subida", "caida", "oscilacion"])
def test_torneo_multidia_recorridos_reales_offline(tmp_path, scenario):
    """Tres días simulados por guion, con las rutas de vigilancia y revisión."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    exchange = FakeExchange(initial_usdt=500)
    db = DBManager(f"sqlite:///{tmp_path / (scenario + '.sqlite')}")
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=7,
        alerts_enabled=False, grid_api_token="e2e-local-token", max_grids_simultaneos=30,
        usdt_por_grid=50, capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=1,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30)
    clock = [exchange.now]
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings, clock=lambda: clock[0])

    class Scanner:
        client = exchange
        def clock(self):
            return clock[0].timestamp()
        def scan(self, *_args, **_kwargs):
            return {"results": []}

    operator = MetaOperator(core, db, Scanner(), engine, settings,
        clock=lambda: clock[0].timestamp())
    # Fuerza la ruta de presupuesto/pendientes que usa producción cuando el
    # ciclo de revisión no alcanza a revisar todas las metas activas.
    operator.review_cycle_budget_s = 0.0
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.settings, app.state.db = settings, db
    app.state.grid_engine, app.state.testnet_client = engine, exchange
    app.state.grid_scan_service = operator.scanner
    app.state.vol_provider = None
    with TestClient(app, headers={"X-API-Token": "e2e-local-token"}) as client:
        legacy = core.create_goal(50, 1.5, profile="RAPIDO")
        core.start_goal(legacy["id"])
        response = client.post("/api/metas/tournament/rounds", json={
            "competitors": ["v1", "v2", "cazador", "escalera", "control"],
            "capital_usdt": 50, "target_value": 1.5, "target_type": "USDT",
            "profile": "RAPIDO", "duration_days": 20})
        assert response.status_code == 200, response.text
        round_goals = [row["id"] for row in response.json()["goals"]]
        goal_ids = [legacy["id"], *round_goals]
        assert [core.get_goal(meta_id)["config_snapshot"].get("competidor")
            for meta_id in round_goals] == ["v1", "v2", "cazador", "escalera", "control"]
        assert core.get_goal(legacy["id"])["config_snapshot"].get("competidor") is None

        for step in range(1, 3 * 24 * 12 + 1):
            exchange.advance(scenario, step)
            clock[0] = exchange.now
            # Sigue el ciclo de producción: el monitor procesa fills; MetaCore
            # valúa, aplica límites de pérdida y cierra metas.
            values = {row["meta_id"]: row.get("valuation") for row in core.run_once()}
            if step % 3 == 0:
                operator.review_active()
            for meta_id in goal_ids:
                value = values.get(meta_id)
                if value is None:
                    continue
                assert float(value["cash_usdt"]) >= -0.01, (scenario, step, meta_id, value)
                assert value.get("cash_coherent") is not False, (scenario, step, meta_id, value)
                assert float(value["value_usdt"]) == pytest.approx(
                    float(value["cash_usdt"]) + float(value["inventory_executable_usdt"]), abs=.01), (
                        scenario, step, meta_id, value)
            actual_usdt = exchange.get_balance("USDT")["USDT"]
            attributable = sum(float(value["cash_usdt"]) for value in values.values()
                if value is not None)
            assert attributable <= actual_usdt["free"] + actual_usdt["locked"] + .01, (
                scenario, step, attributable, actual_usdt)

    # La secuencia termina en el día 3; se comprueba la salida registrada por
    # cada participante para alimentar el informe de resultados.
    for meta_id in goal_ids:
        value = core.valuation(meta_id)
        assert float(value["result_pct"]) == pytest.approx(0, abs=.01)
        assert core._owned_grids(meta_id) == []
    assert exchange.get_open_orders() == []
    assert not any(row["event_type"] == "META_VALUACION_INCOHERENTE"
        for meta_id in goal_ids for row in core.ledger(meta_id)), scenario
