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


def _credential_client(tmp_path, *, validator=None, host=("127.0.0.1", 51000)):
    local, db = make_client(tmp_path, AccountClient())
    local.app.state.testnet_credentials_validator = validator or (lambda key, secret: None)
    local.host = host[0]
    return local, db


def test_testnet_credential_update_is_atomic_preserves_env_and_redacts_secret(tmp_path, monkeypatch, caplog):
    env_path = tmp_path / "settings.env"
    original = "# keep this comment\nOTHER=value\nTESTNET_API_KEY=old-key\nTESTNET_SECRET=old-secret\n"
    env_path.write_text(original, encoding="utf-8", newline="")
    monkeypatch.setenv("ASPLE_ENV_FILE", str(env_path))
    client, _ = _credential_client(tmp_path)
    response = client.post("/api/account/testnet-credentials", json={
        "api_key": "new-key-secret-sentinel", "api_secret": "new-secret-sentinel"})
    assert response.status_code == 200
    assert response.body == {"saved": True, "key_suffix": "inel", "message": "Reinicia el servidor para aplicar el cambio."}
    assert env_path.with_name("settings.env.bak").read_text(encoding="utf-8") == original
    saved = env_path.read_text(encoding="utf-8")
    assert "# keep this comment\nOTHER=value\n" in saved
    assert 'TESTNET_API_KEY="new-key-secret-sentinel"' in saved
    assert 'TESTNET_SECRET="new-secret-sentinel"' in saved
    assert "new-key-secret-sentinel" not in str(response.body) and "new-secret-sentinel" not in str(response.body)
    assert "new-key-secret-sentinel" not in caplog.text and "new-secret-sentinel" not in caplog.text


def test_testnet_credential_update_rejects_invalid_key_without_echo(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("ASPLE_ENV_FILE", str(tmp_path / "unused.env"))
    def reject(key, secret):
        raise RuntimeError(f"invalid {key} {secret}")
    client, _ = _credential_client(tmp_path, validator=reject)
    response = client.post("/api/account/testnet-credentials", json={
        "api_key": "invalid-key-sentinel", "api_secret": "invalid-secret-sentinel"})
    assert response.status_code == 422
    assert "invalid-key-sentinel" not in str(response.body) and "invalid-secret-sentinel" not in str(response.body)
    assert "invalid-key-sentinel" not in caplog.text and "invalid-secret-sentinel" not in caplog.text
    assert not (tmp_path / "unused.env").exists()


def test_testnet_credential_update_requires_direct_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("ASPLE_ENV_FILE", str(tmp_path / "unused.env"))
    client, _ = _credential_client(tmp_path, host=("192.0.2.5", 51000))
    response = client.post("/api/account/testnet-credentials", json={"api_key": "key", "api_secret": "secret"})
    assert response.status_code == 403
    loopback, _ = _credential_client(tmp_path)
    forwarded = loopback._call("POST", "/api/account/testnet-credentials", json={"api_key": "key", "api_secret": "secret"},
                               headers={"X-Forwarded-For": "127.0.0.1"})
    assert forwarded.status_code == 403


def test_testnet_credential_update_blocked_by_open_grid(tmp_path, monkeypatch):
    monkeypatch.setenv("ASPLE_ENV_FILE", str(tmp_path / "unused.env"))
    client, db = _credential_client(tmp_path)
    db.list_grids_by_status = lambda statuses: [{"id": 1, "status": "ACTIVE"}]
    response = client.post("/api/account/testnet-credentials", json={"api_key": "key", "api_secret": "secret"})
    assert response.status_code == 409
    assert not (tmp_path / "unused.env").exists()


def test_testnet_credential_update_blocked_by_pending_replenishment(tmp_path, monkeypatch):
    monkeypatch.setenv("ASPLE_ENV_FILE", str(tmp_path / "unused.env"))
    client, db = _credential_client(tmp_path)
    db.list_grids_by_status = lambda statuses: [{"id": 7, "status": "CLOSED"}]
    db.list_grid_loans = lambda grid_id: [{"status": "PENDING"}]
    response = client.post("/api/account/testnet-credentials", json={"api_key": "key", "api_secret": "secret"})
    assert response.status_code == 409
    assert not (tmp_path / "unused.env").exists()


def test_testnet_credential_update_interrupted_replace_keeps_original(tmp_path, monkeypatch):
    env_path = tmp_path / "settings.env"
    original = "OTHER=preserved\n"
    env_path.write_text(original, encoding="utf-8", newline="")
    monkeypatch.setenv("ASPLE_ENV_FILE", str(env_path))
    client, _ = _credential_client(tmp_path)
    monkeypatch.setattr("api.routes.grid_account.os.replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    response = client.post("/api/account/testnet-credentials", json={"api_key": "key", "api_secret": "secret"})
    assert response.status_code == 500
    assert env_path.read_text(encoding="utf-8") == original


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
    assert reconciliation["XRP"]["relevant"] is True
    assert reconciliation["ETH"]["severity"] == "inconsistency"
    assert reconciliation["ABC"]["severity"] == "ok"


def test_account_reconciliation_excludes_usdt_and_does_not_double_count_done_dust(tmp_path):
    client, db = make_client(tmp_path, AccountClient())
    grid = db.create_grid_with_levels({"symbol":"XRPUSDT","range_low":90,"range_high":110,
        "n_levels":4,"capital_total":100,"status":"ACTIVE","strategy":"smart","dust_qty":"0.25"},
        [{"level_idx":0,"price":90,"sell_price":91,"capital":100,"state":"DONE","held_qty":"0.25","entry_price":90}])
    db.update_grid(grid["id"], dust_qty="0.25")
    db.update_level(grid["id"], 0, held_qty="0.25", state="DONE")
    result = client.get("/api/account/summary").body
    assets = {row["asset"]: row for row in result["reconciliation"]["assets"]}
    assert "USDT" not in assets
    assert assets["XRP"]["assigned_to_grids"] == "0.25"
    assert assets["XRP"]["difference"] == "3.75"


def test_capital_share_excludes_closed_grids(tmp_path):
    client, db = make_client(tmp_path, AccountClient())
    for status in ("ACTIVE", "CLOSED"):
        db.create_grid_with_levels({"symbol":"XRPUSDT","range_low":90,"range_high":110,
            "n_levels":4,"capital_total":100,"status":status,"strategy":"smart"},
            [{"level_idx":0,"price":90,"sell_price":91,"capital":100,"state":"IDLE","held_qty":0}])
    result = client.get("/api/account/summary").body
    opened = result["grids"]["open"][0]
    closed = result["grids"]["closed"][0]
    assert opened["capital_share_pct"] == 100
    assert opened["capital_share_basis"] == "abiertos+repositorio"
    assert closed["capital_share_pct"] is None


def test_daily_event_stream_is_loaded_once_per_grid(tmp_path):
    client, db = make_client(tmp_path, AccountClient())
    grid = db.create_grid_with_levels({"symbol":"XRPUSDT","range_low":90,"range_high":110,
        "n_levels":4,"capital_total":100,"status":"ACTIVE","strategy":"smart"},
        [{"level_idx":0,"price":90,"sell_price":91,"capital":100,"state":"IDLE","held_qty":0}])
    db.add_grid_event(run_id=None, source="CLI", grid_id=grid["id"], event_type="SELL_FILLED",
                      price=91, details={"net_pnl_usdt":1.25})
    calls = []
    original = db.list_grid_events
    def counted(*args, **kwargs):
        calls.append(kwargs.get("grid_id"))
        return original(*args, **kwargs)
    db.list_grid_events = counted
    result = client.get("/api/account/summary")
    assert result.status_code == 200
    assert calls == [grid["id"]]
    assert result.body["gains"]["daily"]["7d"] == result.body["gains"]["daily"]["30d"]
