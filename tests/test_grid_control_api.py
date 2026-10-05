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
    def __init__(self, testnet=True, ticker_fail=False): self.client = SimpleNamespace(testnet=testnet); self.calls = []; self.ticker_fail=ticker_fail
    def get_open_orders(self, symbol): return [{"side":"BUY"}, {"side":"SELL"}]
    def get_book_ticker(self, symbol):
        if self.ticker_fail: raise RuntimeError("ticker unavailable")
        return {"bid_price": 100, "ask_price": 101}


class FakeControlEngine:
    environment = "testnet"
    def __init__(self, db): self.db=db; self.calls=[]
    def _market_context(self, symbol):
        return (FakeExchange().filters, {}, 100)
    def _build_profit_cells(self, grid_id, symbol, levels, *, use_cache=False,
                            deadline=None, clock=None, on_fee_estimated=None):
        cells = []
        for row in levels:
            if row.get("held_qty", 0) <= 0:
                continue
            cost = float(row["entry_price"] * row["held_qty"])
            if on_fee_estimated is not None:
                on_fee_estimated()
            cells.append({"level_idx": row["level_idx"], "held_qty": float(row["held_qty"]),
                          "entry_cost": cost, "entry_fee_usdt": cost * .001})
        return cells
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
    assert db.events[-1]["details"]["outcome"] == "completed"


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


def test_profit_repository_close_preview_reports_sell_and_repository_estimates(tmp_path):
    client, _db, _exchange, _engine = build_client(tmp_path)
    response = client.post("/api/grids/1/close", json={"mode":"profit_repository"})
    assert response.status_code == 200, response.body
    plan = response.body["plan"]
    assert plan["mode"] == "profit_repository"
    assert plan["cells_to_sell"] == 1 and plan["cells_to_repository"] == 0
    assert float(plan["sell_gain_usdt"]) > 0
    assert "bid_used" in plan and "estimated_commission_usdt" in plan
    liquidate = client.post("/api/grids/1/close", json={"mode":"liquidate"})
    assert liquidate.status_code == 200
    assert liquidate.body["plan"]["cells_total"] == 1
    assert liquidate.body["plan"]["cells_winning"] == 1
    assert float(liquidate.body["plan"]["net_result_usdt"]) > 0
    no_price, _, _, _ = build_client(tmp_path, client=FakeControlExchange(ticker_fail=True))
    unavailable = no_price.post("/api/grids/1/close", json={"mode":"profit_repository"})
    assert unavailable.status_code == 503


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
    db.grid["params"]["target_close_plan"] = {"phase":"MARKED"}
    assert client.post("/api/grids/1/params", json={"max_days":3}).status_code == 409


def test_params_simple_grid_accepts_compound_and_rejects_other_smart_keys(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    db.grid["strategy"] = "simple"
    assert client.post("/api/grids/1/params", json={"target_pct":2}).status_code == 422
    assert client.post("/api/grids/1/params", json={"dust_sweep_seq":9}).status_code == 422
    preview = client.post("/api/grids/1/params", json={"compound_enabled":True,
        "compound_ratio":0.5,"compound_max_growth_pct":25,"dry_run":True,"confirm":False})
    assert preview.status_code == 200, preview.body
    assert preview.body["plan"]["updates"]["compound_ratio"] == 0.5
    updated = client.post("/api/grids/1/params", json={"compound_enabled":True,
        "compound_ratio":0.5,"compound_max_growth_pct":25,"dry_run":False,"confirm":True})
    assert updated.status_code == 200, updated.body
    assert db.grid["params"]["compound_enabled"] is True
    assert db.grid["params"]["compound_ratio"] == 0.5
    assert client.post("/api/grids/1/params", json={"compound_enabled":"true"}).status_code == 422
    assert client.post("/api/grids/1/params", json={"compound_ratio":0}).status_code == 422


def test_null_params_remove_meta_without_clobbering_runtime_state(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    db.grid["params"].update({"target_pct":2,"max_days":30,"target_close_plan":{"phase":"COMPLETE"}})
    response = client.post("/api/grids/1/params", json={"target_pct":None,"max_days":None,
        "dry_run":False,"confirm":True})
    assert response.status_code == 200, response.body
    assert "target_pct" not in db.grid["params"] and "max_days" not in db.grid["params"]
    assert db.grid["params"]["target_close_plan"] == {"phase":"COMPLETE"}


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
        ("close", "ACTIVE", {"mode":"profit_repository"}, "close"),
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


def test_close_partial_outcome_and_audit_event(tmp_path):
    client, db, _, engine = build_client(tmp_path)
    def partial(*args, **kwargs):
        engine.db.grid["status"] = "CLOSING"
        return {"status":"CLOSING", "errors":[{"reason":"cancel rejected"}]}
    engine.close_grid = partial
    response = client.post("/api/grids/1/close", json={"mode":"cancel","dry_run":False,"confirm":True})
    assert response.status_code == 200
    assert response.body["outcome"] == "partial"
    assert response.body["status_after"] == "CLOSING"
    assert response.body["errors"] == [{"reason":"cancel rejected"}]
    assert db.events[-1]["details"]["outcome"] == "partial"
    db.grid["status"] = "ACTIVE"
    def closed_with_error(*a, **k):
        db.grid["status"] = "CLOSED"
        return {"status":"CLOSED", "errors":[{"reason":"residual failure"}]}
    engine.close_grid = closed_with_error
    response = client.post("/api/grids/1/close", json={"mode":"cancel","dry_run":False,"confirm":True})
    assert response.body["outcome"] == "partial"
    db.grid["status"] = "ACTIVE"
    engine.close_grid = lambda *a, **k: (db.grid.update(status="CLOSED") or {"status":"CLOSED"})
    response = client.post("/api/grids/1/close", json={"mode":"cancel","dry_run":False,"confirm":True})
    assert response.body["outcome"] == "completed"


def test_optional_price_for_pause_and_cancel_but_required_for_liquidation(tmp_path):
    exchange = FakeControlExchange(ticker_fail=True)
    client, _, _, _ = build_client(tmp_path, client=exchange)
    pause = client.post("/api/grids/1/pause", json={"dry_run":True})
    assert pause.status_code == 200
    assert pause.body["plan"]["held_market_value_usdt"] is None
    assert pause.body["plan"]["unrealized_pnl_usdt"] is None
    cancel = client.post("/api/grids/1/close", json={"mode":"cancel","dry_run":True})
    assert cancel.status_code == 200
    assert cancel.body["plan"]["held_market_value_usdt"] is None
    liquidate = client.post("/api/grids/1/close", json={"mode":"liquidate","dry_run":True})
    assert liquidate.status_code == 503


def test_rejected_control_is_audited_but_dry_run_is_not(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    preview = client.post("/api/grids/1/pause", json={"dry_run":True})
    assert preview.status_code == 200
    assert db.events == []
    db.grid["status"] = "PAUSED"
    rejected = client.post("/api/grids/1/pause", json={"dry_run":False,"confirm":True})
    assert rejected.status_code == 409
    assert len(db.events) == 1
    assert db.events[0]["event_type"] == "GRID_ACTION_REJECTED"
    assert db.events[0]["details"]["who"] == "api"
    assert "parameters" not in db.events[0]["details"]
    db.grid["status"] = "ACTIVE"
    invalid_params = client.post("/api/grids/1/params", json={"target_pct":2,"target_usdt":4})
    assert invalid_params.status_code == 422
    assert len(db.events) == 2
    assert db.events[-1]["event_type"] == "GRID_ACTION_REJECTED"


def test_invalid_parameter_value_is_audited(tmp_path):
    client, db, _, _ = build_client(tmp_path)
    response = client.post("/api/grids/1/params", json={"max_days":0})
    assert response.status_code == 422
    assert len(db.events) == 1
    assert db.events[0]["event_type"] == "GRID_ACTION_REJECTED"
    assert db.events[0]["details"]["who"] == "api"
    malformed = client.post("/api/grids/1/params", json={"max_days":"not-a-number"})
    assert malformed.status_code == 422
    assert len(db.events) == 2
    assert db.events[-1]["details"]["reason"] == "request validation failed"
