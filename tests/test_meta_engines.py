import pytest

from grid.engines import DEFAULT_ENGINE, ENGINES, EngineUnavailable, get_engine


def test_engine_registry_and_unknown_name_default_to_v1():
    assert DEFAULT_ENGINE == "v1"
    assert set(ENGINES) == {"v1", "v2"}
    assert get_engine("v1").available is True
    assert get_engine("v2").available is False
    assert get_engine("desconocido") is get_engine("v1")
    assert get_engine("") is get_engine("v1")


def test_v2_plan_and_review_are_unavailable_but_shadow_is_safe():
    engine = get_engine("v2")
    with pytest.raises(EngineUnavailable, match="motor v2 no disponible todavía"):
        engine.plan(None, {}, plan_kind="revision")
    with pytest.raises(EngineUnavailable, match="motor v2 no disponible todavía"):
        engine.review(None, {}, {}, {})
    assert engine.shadow_review(None, {}, {}, {}) == {
        "decision": "no_disponible", "reason": "motor v2 no disponible todavía"}


def test_decision_engine_switch_api_validates_and_records_event(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from database.db_manager import DBManager
    from grid.meta import MetaCore
    from grid.meta_operator import MetaOperator
    from api.routes import metas

    class Testnet:
        testnet = True

    db = DBManager(f"sqlite:///{tmp_path / 'engine-switch.sqlite'}")
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False,
        grid_api_token="fase43-test-token")
    core = MetaCore(db, Testnet(), settings=settings)
    operator = MetaOperator(core, db, None, None, settings)
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.settings, app.state.db = settings, db
    app.state.meta_core, app.state.meta_operator = core, operator

    with TestClient(app, headers={"X-API-Token": settings.grid_api_token}) as client:
        config = client.get("/api/metas/config")
        assert config.status_code == 200
        assert config.json()["profiles"]["PRUDENTE"]["decision_engine"] == "v1"
        assert {item["name"]: item["available"] for item in config.json()["engines"]} == {"v1": True, "v2": False}
        valid = client.put("/api/metas/decision-engine/switch", json={
            "profile": "PRUDENTE", "engine": "v1", "confirm": True, "actor": "Ramón"})
        assert valid.status_code == 200, valid.text
        assert valid.json()["engine"] == "v1"
        unavailable = client.put("/api/metas/decision-engine/switch", json={
            "profile": "PRUDENTE", "engine": "v2", "confirm": True, "actor": "Ramón"})
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"] == "El motor v2 todavía no está disponible."
        unconfirmed = client.put("/api/metas/decision-engine/switch", json={
            "profile": "PRUDENTE", "engine": "v1", "confirm": False, "actor": "Ramón"})
        assert unconfirmed.status_code == 409
        missing_actor = client.put("/api/metas/decision-engine/switch", json={
            "profile": "PRUDENTE", "engine": "v1", "confirm": True, "actor": ""})
        assert missing_actor.status_code == 422
    with db.engine.connect() as conn:
        rows = conn.execute(select(db.meta_engine_switch_events)).mappings().all()
    assert len(rows) == 1
    assert (rows[0]["profile"], rows[0]["engine"], rows[0]["actor"]) == ("PRUDENTE", "v1", "Ramón")
