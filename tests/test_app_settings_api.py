from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import grid_control
from api.routes import app_settings
from config.settings import Settings, apply_app_flag_overrides
from database.db_manager import DBManager


def _app(tmp_path, *, initial=None):
    db = DBManager(f"sqlite:///{tmp_path / 'flags.db'}")
    settings_data = {"GRID_API_TOKEN": "local-test"}
    if initial is not None:
        settings_data["ADJUST_IDLE_SHRINK_ENABLED"] = initial
    settings = Settings(_env_file=None, **settings_data)
    apply_app_flag_overrides(settings, db)
    grid = {"id": 1, "symbol": "XRPUSDT", "status": "ACTIVE", "strategy": "smart",
            "range_low": 90, "range_high": 110, "capital_total": 100, "n_levels": 10,
            "params": {}}
    db.get_grid = lambda grid_id: grid if int(grid_id) == 1 else None
    db.get_grid_levels = lambda _grid_id: []
    def merge_grid_params(grid_id, updates, *, remove, allowed):
        grid["params"].update({k: v for k, v in updates.items() if k not in remove})
        for key in remove:
            grid["params"].pop(key, None)
        return grid["params"]
    db.merge_grid_params = merge_grid_params
    app = FastAPI()
    app.include_router(app_settings.router, prefix="/api/settings")
    app.include_router(grid_control.router, prefix="/api/grids")
    app.state.db, app.state.settings = db, settings
    app.state.grid_engine = object()
    app.state.testnet_client = SimpleNamespace(client=SimpleNamespace(testnet=True))
    app.state.grid_monitor = None
    return app, db, settings, TestClient(app, headers={"X-API-Token": "local-test"}, raise_server_exceptions=False)


def test_get_lists_allowlisted_flag_with_default_origin(tmp_path):
    _app_obj, _db, _settings, client = _app(tmp_path)
    response = client.get("/api/settings/flags")
    assert response.status_code == 200
    row, = response.json()["flags"]
    assert row == {
        "key": "adjust_idle_shrink_enabled",
        "label": "Reducci\u00f3n de niveles ociosos",
        "description": "Permite que los grids Smart que lo activen quiten niveles sin ciclos en 24 h. Apagado por defecto: en simulaci\u00f3n XRP no mejor\u00f3 la ganancia. Encenderlo aqu\u00ed solo habilita el bot\u00f3n por grid; no activa la regla en ning\u00fan grid.",
        "current": False, "default": False, "origin": "default", "requires_restart": False,
    }


def test_confirmed_enable_persists_mutates_live_settings_and_allows_grid_toggle(tmp_path):
    app, db, settings, client = _app(tmp_path)
    preview = client.post("/api/settings/flags", json={
        "key": "adjust_idle_shrink_enabled", "value": True,
    })
    assert preview.status_code == 200 and preview.json()["dry_run"] is True
    assert settings.adjust_idle_shrink_enabled is False
    assert db.list_app_flag_overrides() == []
    rejected = client.post("/api/settings/flags", json={
        "key": "adjust_idle_shrink_enabled", "value": True, "dry_run": False,
    })
    assert rejected.status_code == 422
    assert settings.adjust_idle_shrink_enabled is False
    assert db.list_app_flag_overrides() == []

    changed = client.post("/api/settings/flags", json={
        "key": "adjust_idle_shrink_enabled", "value": True, "dry_run": False, "confirm": True,
    })
    assert changed.status_code == 200, changed.text
    assert settings.adjust_idle_shrink_enabled is True
    assert db.list_app_flag_overrides()[0]["value"] is True
    grid_result = client.post("/api/grids/1/params", json={
        "adjust_idle_shrink": True, "dry_run": False, "confirm": True,
    })
    assert grid_result.status_code == 200, grid_result.text
    assert app.state.settings is settings
    assert db.get_grid(1)["params"]["adjust_idle_shrink"] is True
    assert any(event["event_type"] == "APP_SETTING_CHANGED" for event in db.list_grid_events())


def test_disable_is_allowed_without_confirm_and_audited(tmp_path):
    app, db, settings, client = _app(tmp_path, initial=True)
    response = client.post("/api/settings/flags", json={
        "key": "adjust_idle_shrink_enabled", "value": False, "dry_run": False,
    })
    assert response.status_code == 200, response.text
    assert settings.adjust_idle_shrink_enabled is False
    assert db.list_app_flag_overrides()[0]["value"] is False
    event, = [row for row in db.list_grid_events() if row["event_type"] == "APP_SETTING_CHANGED"]
    assert event["details"]["key"] == "adjust_idle_shrink_enabled"
    assert event["details"]["previous"] is True
    assert event["details"]["new"] is False
    assert event["details"]["origin"] == "app"
    assert event["details"]["previous_origin"] == ".env"


def test_unknown_key_extra_field_and_non_boolean_value_are_rejected(tmp_path):
    _app_obj, _db, settings, client = _app(tmp_path)
    for body in (
        {"key": "testnet_api_secret", "value": True, "dry_run": False, "confirm": True},
        {"key": "adjust_idle_shrink_enabled", "value": "true"},
        {"key": "adjust_idle_shrink_enabled", "value": True, "other": "x"},
    ):
        response = client.post("/api/settings/flags", json=body)
        assert response.status_code == 422
    assert settings.adjust_idle_shrink_enabled is False


def test_database_override_wins_after_restart_and_env_is_reported(tmp_path, monkeypatch):
    app, _db, _settings, client = _app(tmp_path)
    enabled = client.post("/api/settings/flags", json={
        "key": "adjust_idle_shrink_enabled", "value": True,
        "dry_run": False, "confirm": True,
    })
    assert enabled.status_code == 200
    db_url = app.state.db.engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("ADJUST_IDLE_SHRINK_ENABLED", "false")
    monkeypatch.setenv("GRID_API_TOKEN", "local-test")
    restarted_db = DBManager(db_url)
    restarted_settings = Settings(_env_file=None)
    assert restarted_settings.adjust_idle_shrink_enabled is False
    apply_app_flag_overrides(restarted_settings, restarted_db)
    assert restarted_settings.adjust_idle_shrink_enabled is True
    restarted_app = FastAPI()
    restarted_app.include_router(app_settings.router, prefix="/api/settings")
    restarted_app.state.db, restarted_app.state.settings = restarted_db, restarted_settings
    restarted_row = TestClient(restarted_app, headers={"X-API-Token": "local-test"},
        raise_server_exceptions=False).get("/api/settings/flags").json()["flags"][0]
    assert restarted_row["current"] is True and restarted_row["origin"] == "app"

    monkeypatch.setenv("ADJUST_IDLE_SHRINK_ENABLED", "true")
    monkeypatch.setenv("GRID_API_TOKEN", "local-test")
    env_settings = Settings(_env_file=None)
    empty_db = DBManager(f"sqlite:///{tmp_path / 'empty.db'}")
    env_app = FastAPI()
    env_app.include_router(app_settings.router, prefix="/api/settings")
    env_app.state.db, env_app.state.settings = empty_db, env_settings
    env_row = TestClient(env_app, headers={"X-API-Token": "local-test"}, raise_server_exceptions=False).get("/api/settings/flags").json()["flags"][0]
    assert env_row["current"] is True and env_row["origin"] == ".env"
    assert restarted_settings.adjust_idle_shrink_enabled is True
