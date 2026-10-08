from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
from fastapi import FastAPI

from api.routes import grid_structure, grids
from database.db_manager import DBManager
from tests.test_grid_status_api import LocalClient, _counts
from tests.grid_fakes import FakeExchange


class MarketService:
    def __init__(self, sigma=0.035435):
        self.sigma = sigma
        self.calls = []
    def clock(self):
        return 1.0
    def _market(self, symbol, capital, deadline):
        self.calls.append((symbol, capital, deadline))
        increments = np.tile([1.0, -1.0], 12) * self.sigma / np.sqrt(24 * 24 / 23)
        closes = np.exp(np.r_[0.0, np.cumsum(increments)]) * 1.4912
        return {"bid": Decimal("1.49115"), "ask": Decimal("1.49125"),
                "klines_1h": pd.DataFrame({"close": closes})}, FakeExchange().filters
    def scan(self, **kwargs):
        return {"results": []}


def make_client(tmp_path, monkeypatch, *, token="", sigma=.035435):
    db = DBManager(f"sqlite:///{tmp_path}/preview.db")
    db.add_or_reactivate_coin("XRPUSDT")
    settings = SimpleNamespace(grid_api_token=token, scanner_timeout_seconds=60,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        scanner_min_cell_floor_usdt=Decimal("5.5"), max_grids_simultaneos=5,
        grid_min_margin_after_fees_pct=.7)
    app = FastAPI()
    app.include_router(grids.router, prefix="/api/grids")
    app.include_router(grid_structure.router, prefix="/api/grids")
    app.state.db, app.state.settings = db, settings
    app.state.grid_scan_service = MarketService(sigma)
    app.state.grid_engine, app.state.testnet_client = None, None
    return LocalClient(app), db, app


def test_preview_returns_three_variants_and_matches_structure_math(tmp_path, monkeypatch):
    client, db, app = make_client(tmp_path, monkeypatch)
    before = _counts(db)
    response = client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100", "strategy":"simple", "target_usdt":"1"})
    assert response.status_code == 200, response.body
    body = response.json()
    assert set(body) == {"symbol", "strategy", "capital", "minimum_margin_after_fees_pct", "mid", "fee_pct", "variants", "edited", "definitions", "data_source", "warning", "minimum_cell_usdt", "min_cell_warning", "functional_cell_warning", "dust_target_pct", "dust_min_cell_usdt"}
    assert set(body["variants"]) == {"balanced", "wide"}
    assert body["definitions"]["merged_variants"] and body["definitions"]["balanced"] and body["definitions"]["wide"]
    balanced = body["variants"]["balanced"]
    assert balanced["n_levels"] == 15
    assert balanced["spacing_pct"] > .9
    assert balanced["edge_gross_pct"] == pytest.approx(balanced["spacing_pct"] - 2 * body["fee_pct"])
    assert balanced["edge_after_dust_pct"] < 0
    assert balanced["edge_gross_pct"] - balanced["edge_after_dust_pct"] == pytest.approx(balanced["dust_estimate_pct"])
    assert balanced["dust_estimate_label"] == "estimaci\u00f3n conservadora, no medida"
    assert balanced["feasible"] is True
    assert balanced["dust_warning"] is True
    assert balanced["cycles_to_target"]["cycles"] is None
    assert _counts(db) == before
    assert app.state.grid_scan_service.calls


def test_structure_viability_uses_gross_edge_target_and_exchange_cell_floor(monkeypatch):
    from api.routes import grid_structure as route

    filters = FakeExchange().filters
    body = route.StructurePreviewRequest(symbol="ADAUSDT", capital=Decimal("100"),
        range_low=Decimal("1.3"), range_high=Decimal("1.7"), n_levels=18,
        margin_target_pct=.29)
    evaluation = {"n":18,"spacing_pct":.942,"cell_usdt":Decimal("100")/18,
        "dust_pct":.460,"required_spacing":1.0,"edge_gross_pct":.742,
        "net_edge_pct":.282,"spacing_ok":False,"cell_ok":True}
    monkeypatch.setattr(route,"evaluate_levels",lambda *args,**kwargs:evaluation)
    monkeypatch.setattr(route,"compute_lines",lambda *args,**kwargs:None)
    result=route._custom_variant(body,Decimal("1.5"),filters,.1,.8,Decimal("5.5"),.7,.29,None)
    assert result["feasible"] is True
    assert result["edge_gross_pct"] == pytest.approx(.742)
    assert result["edge_after_dust_pct"] == pytest.approx(.282)
    assert result["margin_target_met"] is True
    assert result["dust_warning"] is True
    assert not any("incluye el coste estimado del polvo" in reason for reason in result["reasons"])
    assert not any("margen neto estimado" in reason for reason in result["reasons"])

    evaluation.update(edge_gross_pct=.50,spacing_pct=.70,net_edge_pct=.04,dust_pct=.66)
    low_margin=route._make_variant("edited",12,Decimal("1.3"),Decimal("1.7"),18,
        body,Decimal("1.5"),filters,.1,.8,Decimal("5.5"),.7,None)
    assert low_margin["feasible"] is False
    assert any("Margen tras comisiones" in reason and "mínimo 0.700 %" in reason
               for reason in low_margin["reasons"])

    evaluation.update(edge_gross_pct=.742,spacing_pct=.942,net_edge_pct=.282,
                      dust_pct=.460,cell_ok=False)
    below_floor=route._make_variant("edited",16.956,Decimal("1.3"),Decimal("1.7"),18,
        body,Decimal("1.5"),filters,.1,.8,Decimal("5.5"),.7,None)
    assert below_floor["feasible"] is False
    assert any("Celda inferior" in reason for reason in below_floor["reasons"])


def test_infeasible_variant_is_present_and_custom_structure_returns_metrics(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch, sigma=.0001)
    response = client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"20", "strategy":"simple"})
    assert response.status_code == 200
    assert set(response.body["variants"]) == {"balanced", "wide"}
    assert any(not row["feasible"] and row["reasons"] for row in response.body["variants"].values())
    edited = client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100", "range_low":"1.3", "range_high":"1.7", "n_levels":8})
    assert edited.status_code == 200 and edited.body["edited"]["name"] == "edited"


def test_preview_contract_exposes_cell_floor_warning_and_dust_size_estimate(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch)
    payload = {"symbol":"XRPUSDT", "capital":"20", "range_low":"1.3",
        "range_high":"1.7", "n_levels":8}
    response = client.post("/api/grids/structure-preview", json=payload)
    assert response.status_code == 200
    edited = response.json()["edited"]
    assert edited["min_cell_warning"] == "Mínimo de celda aplicado: 5.5 USDT (5 USDT típico; no verificado en Testnet)."
    assert edited["minimum_cell_usdt"] == "5.5"
    assert edited["dust_target_pct"] == .1
    assert abs(Decimal(edited["dust_min_cell_usdt"]) - Decimal("149.12")) <= Decimal("0.01")
    payload["capital"] = "1000"
    larger = client.post("/api/grids/structure-preview", json=payload).json()["edited"]
    assert larger["min_cell_warning"] is None


def test_authorization_closed_schema_registry_and_market_errors(tmp_path, monkeypatch):
    client, _, app = make_client(tmp_path, monkeypatch, token="secret")
    payload={"symbol":"XRPUSDT", "capital":"100", "strategy":"simple"}
    assert client.post("/api/grids/structure-preview", json=payload).status_code == 403
    assert client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100", "strategy":"simple", "surprise":1}, headers={"X-API-Token":"secret"}).status_code == 422
    assert client.post("/api/grids/structure-preview", json={"symbol":"NOPEUSDT", "capital":"100"}, headers={"X-API-Token":"secret"}).status_code == 422
    other=LocalClient(client.app, host="192.0.2.5")
    assert other.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100"}).status_code == 403
    db=app.state.db; db.deactivate_coin("XRPUSDT")
    inactive=client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100"}, headers={"X-API-Token":"secret"})
    assert inactive.status_code == 422 and "activo" in inactive.body["detail"]
    db.add_or_reactivate_coin("XRPUSDT")
    app.state.grid_scan_service._market=lambda *args: (_ for _ in ()).throw(RuntimeError("secret traceback detail"))
    result=client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100"}, headers={"X-API-Token":"secret"})
    assert result.status_code == 503 and "secret traceback detail" not in str(result.body)


import pytest
