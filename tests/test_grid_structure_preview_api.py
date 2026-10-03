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
        scanner_min_cell_floor_usdt=Decimal("5.5"), max_grids_simultaneos=5)
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
    assert set(body) == {"symbol", "strategy", "capital", "mid", "fee_pct", "variants", "edited", "definitions", "data_source", "warning"}
    assert set(body["variants"]) == {"dense", "balanced", "wide"}
    assert body["definitions"]["dense"] and body["definitions"]["balanced"] and body["definitions"]["wide"]
    balanced = body["variants"]["balanced"]
    assert balanced["n_levels"] == 9
    assert balanced["spacing_pct"] == pytest.approx(1.576, abs=.02)
    assert balanced["edge_gross_pct"] == pytest.approx(balanced["spacing_pct"] - 2 * body["fee_pct"])
    assert balanced["edge_after_dust_pct"] == pytest.approx(.034, abs=.08)
    assert balanced["edge_gross_pct"] - balanced["edge_after_dust_pct"] == pytest.approx(balanced["dust_estimate_pct"])
    assert balanced["dust_estimate_label"] == "estimaci\u00f3n conservadora, no medida"
    assert balanced["cycles_to_target"]["cycles"] > 0
    assert _counts(db) == before
    assert app.state.grid_scan_service.calls


def test_infeasible_variant_is_present_and_custom_structure_returns_metrics(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch, sigma=.0001)
    response = client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"20", "strategy":"simple"})
    assert response.status_code == 200
    assert set(response.body["variants"]) == {"dense", "balanced", "wide"}
    assert any(not row["feasible"] and row["reasons"] for row in response.body["variants"].values())
    edited = client.post("/api/grids/structure-preview", json={"symbol":"XRPUSDT", "capital":"100", "range_low":"1.3", "range_high":"1.7", "n_levels":8})
    assert edited.status_code == 200 and edited.body["edited"]["name"] == "edited"


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
