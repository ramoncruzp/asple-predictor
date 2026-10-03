from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI

from api.routes import grid_control
from api.routes.grid_control import CloseBody
from tests.test_grid_status_api import LocalClient
from tests.grid_fakes import FakeExchange


class FakeControlDB:
    def __init__(self):
        self.grid = {"id": 1, "symbol": "XRPUSDT", "status": "ACTIVE", "strategy": "smart",
                     "range_low": 90, "range_high": 110, "capital_total": 100,
                     "n_levels": 10,
                     "params": {"dust_cash_proceeds": "2", "dust_sweep_seq": 2}}
        self.levels = [{"level_idx": 0, "state": "SELL_OPEN", "price": 90, "sell_price": 91,
                        "entry_price": 90, "held_qty": 1, "capital": 50},
                       {"level_idx": 1, "state": "BUY_OPEN", "price": 95, "sell_price": 96,
                        "entry_price": None, "held_qty": 0, "capital": 50}]
        self.events = []
    def get_grid(self, grid_id): return self.grid if grid_id == 1 else None
    def get_grid_levels(self, grid_id): return list(self.levels)
    def add_grid_event(self, **event): self.events.append(event)
    def merge_grid_params(self, grid_id, updates, *, remove, allowed):
        self.grid["params"].update({k:v for k,v in updates.items() if k not in remove})
        for key in remove: self.grid["params"].pop(key, None)
        return self.grid["params"]


class FakeControlExchange:
    def __init__(self, testnet=True): self.client = SimpleNamespace(testnet=testnet); self.calls = []
    def get_open_orders(self, symbol): return [{"side":"BUY"}, {"side":"SELL"}]
    def get_book_ticker(self, symbol): return {"bid_price": 100, "ask_price": 101}


class FakeControlEngine:
    environment = "testnet"
    def __init__(self, db): self.db=db; self.calls=[]
    def _market_context(self, symbol):
        return (FakeExchange().filters, {}, 100)
    def preview_adjust(self, *args): return {"ok":True,"plan":{"movable_cells":[1],"trapped_cells":[0]}}
    def pause_grid(self, *args): self.calls.append(("pause",args)); self.db.grid["status"]="PAUSED"; return {"ok":True}
    def resume_grid(self, *args): self.calls.append(("resume",args)); self.db.grid["status"]="ACTIVE"; return {"ok":True}
    def close_grid(self, *args, **kwargs): self.calls.append(("close",args,kwargs)); self.db.grid["status"]="CLOSED"; return {"status":"CLOSED"}
    def adjust_grid(self, *args, **kwargs): self.calls.append(("adjust",args,kwargs)); return {"ok":True}
    def sweep_grid_dust(self, *args, **kwargs): self.calls.append(("sweep-dust",args,kwargs)); return {"status":"DEFERRED"}


def build_client(tmp_path, *, client=None, token="", no_client=False):
    app = FastAPI()
    app.include_router(grid_control.router, prefix="/api/grids")
    db = FakeControlDB()
    exchange = None if no_client else (client or FakeControlExchange())
    engine = FakeControlEngine(db) if exchange is not None else None
    app.state.db, app.state.settings = db, SimpleNamespace(grid_api_token=token)
    app.state.testnet_client, app.state.grid_engine, app.state.grid_monitor = exchange, engine, None
    return LocalClient(app), db, exchange, engine


def test_every_control_preview_is_read_only_and_quantified(tmp_path):
    client, db, exchange, engine = build_client(tmp_path)
    cases = [
        ("pause", {}), ("resume", {}), ("close", {"mode":"repository"}),
        ("adjust", {"new_low":89,"new_high":111}), ("sweep-dust", {}),
        ("params", {"max_days":30}),
    ]
    for action, values in cases:
        db.grid["status"] = "PAUSED" if action == "resume" else "ACTIVE"
        response = client.post(f"/api/grids/1/{action}", json={**values,"dry_run":True,"confirm":False})
        assert response.status_code == 200, response.body
        assert response.body["dry_run"] is True
        assert response.body["plan"]["testnet_warning"]
    assert db.events == []
    assert engine.calls == []
    assert db.grid["params"]["dust_cash_proceeds"] == "2"


def test_execution_requires_both_flags_and_writes_audit_event(tmp_path):
    client, db, _, engine = build_client(tmp_path)
    missing_confirm = client.post("/api/grids/1/pause", json={"dry_run":False})
    both_flags = client.post("/api/grids/1/pause", json={"dry_run":True,"confirm":True})
    assert missing_confirm.status_code == 422
    assert both_flags.status_code == 422
    result = client.post("/api/grids/1/pause", json={"dry_run":False,"confirm":True})
    assert result.status_code == 200
    assert engine.calls[0][0] == "pause"
    assert db.events[-1]["event_type"] == "GRID_ACTION_API"
    assert db.events[-1]["details"]["who"] == "api"


def test_control_guards_testnet_auth_state_and_schema(tmp_path):
    client, _, _, _ = build_client(tmp_path, token="sentinel-token")
    assert client.post("/api/grids/1/pause", json={}).status_code == 403
    assert client.post("/api/grids/1/pause", json={"extra":1}, headers={"X-API-Token":"sentinel-token"}).status_code == 422
    app_client, _, _, _ = build_client(tmp_path, no_client=True)
    assert app_client.post("/api/grids/1/pause", json={}).status_code == 503
    prod_client, _, _, _ = build_client(tmp_path, client=FakeControlExchange(testnet=False))
    assert prod_client.post("/api/grids/1/pause", json={}).status_code == 503


def test_close_mode_and_liquidation_confirmation_are_mandatory(tmp_path):
    assert CloseBody.model_fields["mode"].is_required()
    client, _, _, _ = build_client(tmp_path)
    missing_mode = client.post("/api/grids/1/close", json={"dry_run":True})
    missing_text = client.post("/api/grids/1/close", json={"mode":"liquidate","dry_run":False,"confirm":True})
    wrong_text = client.post("/api/grids/1/close", json={"mode":"liquidate","confirm_text":"VENDER","dry_run":False,"confirm":True})
    assert missing_mode.status_code == 422
    assert missing_text.status_code == 422
    assert wrong_text.status_code == 422


def test_invalid_status_missing_grid_and_invalid_params_are_rejected(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    db.grid["status"] = "PAUSED"
    assert client.post("/api/grids/1/pause", json={}).status_code == 409
    assert client.post("/api/grids/99/resume", json={}).status_code == 404
    db.grid["status"] = "ACTIVE"
    assert client.post("/api/grids/1/params", json={"max_days":0}).status_code == 422
    assert client.post("/api/grids/1/params", json={"target_pct":2,"target_usdt":4}).status_code == 422
    db.grid["status"] = "CLOSING"
    assert client.post("/api/grids/1/params", json={"max_days":3}).status_code == 409
    db.grid["status"] = "ACTIVE"
    db.grid["params"]["max_days_close_plan"] = {"phase":"STARTED"}
    assert client.post("/api/grids/1/params", json={"max_days":3}).status_code == 409


def test_params_runtime_key_is_forbidden_simple_grid_only_allows_max_days(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    db.grid["strategy"] = "simple"
    assert client.post("/api/grids/1/params", json={"target_pct":2}).status_code == 422
    assert client.post("/api/grids/1/params", json={"dust_sweep_seq":9}).status_code == 422


def test_null_params_remove_meta_without_clobbering_runtime_state(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    db.grid["params"].update({"target_pct":2,"max_days":30,"target_close_plan":{"phase":"DONE"}})
    response = client.post("/api/grids/1/params", json={"target_pct":None,"max_days":None,
        "dry_run":False,"confirm":True})
    assert response.status_code == 200, response.body
    assert "target_pct" not in db.grid["params"] and "max_days" not in db.grid["params"]
    assert db.grid["params"]["target_close_plan"] == {"phase":"DONE"}


def test_non_loopback_without_api_token_is_forbidden(tmp_path):
    client, _, _, _ = build_client(tmp_path)
    client.host = "8.8.8.8"
    assert client.post("/api/grids/1/pause", json={}).status_code == 403


def test_simultaneous_close_has_one_winner(tmp_path):
    client, db, _, engine = build_client(tmp_path)
    def close():
        return client.post("/api/grids/1/close", json={"mode":"repository","dry_run":False,"confirm":True}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(close), pool.submit(close))
        results = sorted(future.result(timeout=5) for future in futures)
    assert results == [200, 409]
    assert len([call for call in engine.calls if call[0] == "close"]) == 1


def test_confirmed_controls_dispatch_expected_engine_methods(tmp_path):
    client, db, _, engine = build_client(tmp_path)
    cases = [
        ("resume", "PAUSED", {}, "resume"),
        ("close", "ACTIVE", {"mode":"repository"}, "close"),
        ("adjust", "ACTIVE", {"new_low":89,"new_high":111,"n":10}, "adjust"),
        ("sweep-dust", "ACTIVE", {}, "sweep-dust"),
    ]
    for action, status, body, method in cases:
        db.grid["status"] = status
        response = client.post(f"/api/grids/1/{action}", json={**body,"dry_run":False,"confirm":True})
        assert response.status_code == 200, response.body
        assert engine.calls[-1][0] == method
        assert db.events[-1]["event_type"] == "GRID_ACTION_API"
    db.grid["status"] = "ACTIVE"
    response = client.post("/api/grids/1/params", json={"max_days":30,"dry_run":False,"confirm":True})
    assert response.status_code == 200
    assert db.grid["params"]["max_days"] == 30
