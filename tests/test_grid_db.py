from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from api.routes import coins as coins_module
from config.models_config import ACTIVE_SYMBOL
from database.db_manager import DBManager


def make_grid_db(db_url="sqlite:///:memory:"):
    db = DBManager(db_url)
    db.add_or_reactivate_coin("DOGEUSDT", "grid test")
    return db


def grid_values(capital=100):
    return {
        "symbol": "DOGEUSDT", "range_low": 90, "range_high": 110,
        "n_levels": 4, "capital_total": capital, "status": "OPENING",
        "environment": "testnet", "open_price": 100,
    }


def level_values():
    return [
        {"level_idx": i, "price": 90 + i, "sell_price": 91 + i,
         "capital": 25, "state": "IDLE"}
        for i in range(4)
    ]


def test_grid_and_levels_create_atomically_and_round_trip():
    db = make_grid_db()
    grid = db.create_grid_with_levels(grid_values(), level_values())
    levels = db.get_grid_levels(grid["id"])
    assert grid["status"] == "OPENING"
    assert grid["environment"] == "testnet"
    assert len(levels) == 4
    assert [row["level_idx"] for row in levels] == [0, 1, 2, 3]


def test_grid_creation_rolls_back_parent_if_level_insert_fails():
    db = make_grid_db()
    broken = level_values()
    del broken[-1]["sell_price"]
    with pytest.raises(KeyError):
        db.create_grid_with_levels(grid_values(), broken)
    assert db.count_open_grids() == 0
    assert db.list_open_grids() == []


def test_open_grid_queries_follow_status_and_symbol():
    db = make_grid_db()
    first = db.create_grid_with_levels(grid_values(), level_values())
    second = db.create_grid_with_levels({**grid_values(), "symbol": "OTHERUSDT"}, level_values())
    assert db.has_open_grid("DOGEUSDT") is True
    assert db.count_open_grids() == 2
    db.update_grid(first["id"], status="CLOSED")
    db.update_grid(second["id"], status="CANCELLED")
    assert db.has_open_grid("DOGEUSDT") is False
    assert db.count_open_grids() == 0


def test_update_level_always_advances_updated_at():
    db = make_grid_db()
    grid = db.create_grid_with_levels(grid_values(), level_values())
    before = db.get_grid_levels(grid["id"])[0]["updated_at"]
    updated = db.update_level(grid["id"], 0, state="BUY_OPEN")
    assert updated["state"] == "BUY_OPEN"
    assert updated["updated_at"] >= before


def test_default_capital_is_copied_for_each_new_grid_only():
    db = make_grid_db()
    class Settings:
        usdt_por_grid = 100
        max_grids_simultaneos = 5
        capital_max_por_nivel_pct = 0.30
        grid_min_step_pct = 0.003

    settings = Settings()
    first = db.create_grid_with_levels(
        {**grid_values(settings.usdt_por_grid), "status": "ACTIVE"}, level_values()
    )
    settings.usdt_por_grid = 250
    assert db.get_grid(first["id"])["capital_total"] == 100
    second = db.create_grid_with_levels(
        {**grid_values(settings.usdt_por_grid), "status": "ACTIVE"}, level_values()
    )
    assert db.get_grid(second["id"])["capital_total"] == 250
    assert db.count_open_grids() == 2


class FakeClient:
    def get_symbol_status(self, symbol):
        return "TRADING"


def make_route_app(db):
    app = FastAPI()
    app.include_router(coins_module.router, prefix="/api/coins")
    app.state.db = db
    app.state.client = FakeClient()
    return app


def request(app, method, path, body=None):
    async def run():
        messages = []
        payload = b"" if body is None else json.dumps(body).encode()
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json")] if body is not None else [],
            "client": ("test", 123), "server": ("test", 80),
        }, receive, send)
        status = next(item["status"] for item in messages if item["type"] == "http.response.start")
        content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return status, json.loads(content) if content else None
    return asyncio.run(run())


def test_delete_coin_returns_409_with_open_grid_and_200_after_close(tmp_path):
    db = make_grid_db(f"sqlite:///{tmp_path / 'route-grid.db'}")
    app = make_route_app(db)
    grid = db.create_grid_with_levels({**grid_values(), "status": "ACTIVE"}, level_values())
    status, _ = request(app, "DELETE", "/api/coins/DOGEUSDT")
    assert status == 409
    db.update_grid(grid["id"], status="CLOSED")
    status, payload = request(app, "DELETE", "/api/coins/DOGEUSDT")
    assert status == 200
    assert payload == {"symbol": "DOGEUSDT", "active": False}


def test_delete_active_predictor_symbol_still_returns_409():
    db = make_grid_db()
    db.add_or_reactivate_coin(ACTIVE_SYMBOL)
    status, _ = request(make_route_app(db), "DELETE", f"/api/coins/{ACTIVE_SYMBOL}")
    assert status == 409
