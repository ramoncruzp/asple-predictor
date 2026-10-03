from types import SimpleNamespace

from fastapi import FastAPI

from api.routes import grid_account
from database.db_manager import DBManager
from tests.test_grid_status_api import LocalClient


class AccountClient:
    def __init__(self, secret="testnet-secret-sentinel", fail=False):
        self.client = SimpleNamespace(testnet=True, get_account=lambda: {
            "canTrade": True, "accountType": "SPOT", "balances": []})
        self.secret, self.fail = secret, fail
    def _run_read(self, fn):
        if self.fail: raise RuntimeError(f"exchange error {self.secret}")
        return fn()
    def get_balance(self):
        return {"USDT":{"free":10,"locked":2}, "XRP":{"free":3,"locked":1},
                "ODD":{"free":2,"locked":0}, "ABC":{"free":2,"locked":0}}
    def get_avg_price(self, symbol):
        if symbol == "XRPUSDT": return 0.5
        if symbol == "ODDUSDT": raise RuntimeError(self.secret)
        return 1


def make_client(tmp_path, exchange, *, api_key="testnet-key-sentinel", secret="testnet-secret-sentinel", token=""):
    app = FastAPI()
    app.include_router(grid_account.router)
    db = DBManager(f"sqlite:///{tmp_path}/account.db")
    app.state.db = db
    app.state.settings = SimpleNamespace(grid_api_token=token, testnet_api_key=api_key,
        testnet_api_secret=secret, binance_api_key="production-key-sentinel",
        binance_api_secret="production-secret-sentinel", scanner_fee_pct=.1)
    app.state.testnet_client = exchange
    app.state.grid_engine = None
    return LocalClient(app), db


def test_account_summary_values_balances_and_keeps_unpriced_assets_separate(tmp_path):
    client, db = make_client(tmp_path, AccountClient())
    from sqlalchemy import func, select
    def counts():
        with db.engine.connect() as conn:
            return {name:conn.execute(select(func.count()).select_from(getattr(db,name))).scalar_one()
                    for name in ("grids", "grid_levels", "grid_events")}
    before = counts()
    response = client.get("/api/account/summary")
    assert response.status_code == 200
    result = response.body
    assert result["balance"]["equity_usdt"] == "16.0"
    assert [row["asset"] for row in result["balance"]["unvalued"]] == ["ODD"]
    assert float(result["balance"]["usdt_free"]) == 10
    assert counts() == before


def test_account_connection_redacts_error_and_only_exposes_testnet_suffix(tmp_path, caplog):
    exchange = AccountClient(fail=True)
    client, _ = make_client(tmp_path, exchange)
    connection = client.get("/api/account/connection")
    assert connection.status_code == 200
    assert connection.body["key_suffix"] == "inel"
    serialized = str(connection.body) + caplog.text
    for secret in ("testnet-key-sentinel", "testnet-secret-sentinel",
                   "production-key-sentinel", "production-secret-sentinel"):
        assert secret not in serialized


def test_account_without_testnet_still_returns_database_gains_and_null_balance(tmp_path):
    client, _ = make_client(tmp_path, None)
    result = client.get("/api/account/summary")
    assert result.status_code == 200
    assert result.body["balance"] is None
    assert result.body["gains"]["realized_usdt"] == 0
    assert result.body["unavailable_reason"]


def test_placeholder_testnet_settings_are_not_reported_as_configured(tmp_path):
    client, _ = make_client(tmp_path, None, api_key="tu_testnet_api_key_aqui",
                            secret="tu_testnet_secret_aqui")
    result = client.get("/api/account/connection")
    assert result.status_code == 200
    assert result.body["configured"] is False
    assert result.body["key_suffix"] is None


def test_account_routes_are_get_only(tmp_path):
    client, _ = make_client(tmp_path, AccountClient())
    assert client.post("/api/account/summary", json={}).status_code == 405
    assert client._call("PUT", "/api/account/connection").status_code == 405
    assert client._call("DELETE", "/api/account/connection").status_code == 405
    guarded, _ = make_client(tmp_path, AccountClient(), token="account-sentinel")
    assert guarded.get("/api/account/summary").status_code == 403


def test_account_keeps_same_symbol_grids_separate_and_reports_reconciliation_signs(tmp_path):
    client, db = make_client(tmp_path, AccountClient())
    for symbol, status, qty, pnl in (("XRPUSDT","ACTIVE",2,5), ("XRPUSDT","HOLDING",1,9),
                                     ("ETHUSDT","ACTIVE",1,2), ("ABCUSDT","ACTIVE",2,3)):
        db.create_grid_with_levels({"symbol":symbol,"range_low":90,"range_high":110,
            "n_levels":4,"capital_total":100,"status":status,"strategy":"smart"},
            [{"level_idx":0,"price":90,"sell_price":91,"capital":100,"state":"SELL_OPEN",
              "held_qty":qty,"entry_price":90,"pnl":pnl}])
    result = client.get("/api/account/summary").body
    xrp = [row for group in result["grids"].values() for row in group if row["symbol"] == "XRPUSDT"]
    assert len(xrp) == 2
    assert {row["realized_usdt"] for row in xrp} == {5.0, 9.0}
    assert all(row["total_with_inventory_usdt"] == row["realized_usdt"] + row["unrealized_usdt"] for row in xrp)
    reconciliation = {row["asset"]:row for row in result["reconciliation"]["assets"]}
    assert reconciliation["XRP"]["severity"] == "unassigned"
    assert reconciliation["ETH"]["severity"] == "inconsistency"
    assert reconciliation["ABC"]["severity"] == "ok"
