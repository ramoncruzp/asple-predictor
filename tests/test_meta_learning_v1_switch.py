import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.routes import metas
from database.db_manager import DBManager
from grid.meta_operator import MetaOperator


def _operator(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta_learning_v1.sqlite'}")
    settings = SimpleNamespace(meta_mode_enabled=True, grid_api_token="test-token")
    return db, MetaOperator(None, db, None, None, settings)


def test_apagado_bloquea_job_manual_persistencia_y_etiquetas(tmp_path):
    db, operator = _operator(tmp_path)
    operator.set_learning_v1_enabled(False, confirm=True, actor="Ramón")
    assert operator.learning_status("RAPIDO")["readiness"]["label"].startswith(
        "Aprendizaje v1 apagado desde ")
    assert operator.run_learning_daily() == {"status": "disabled", "reason": "aprendizaje v1 apagado"}
    assert operator.schedule_learning_manual() == {"status": "disabled", "reason": "aprendizaje v1 apagado"}
    assert operator._persist_learning_samples({"id": 1}, "d", [{"alternative_id": "a"}]) == 0
    assert operator._persist_touch_observations(1, "d", [{"_touch_observations": [{"cell_index": 1}]}]) is None
    assert operator._label_allocation_shadows({}, {}, 0) == 0
    assert operator._label_touch_bot_shadows(0) == 0
    with db.engine.connect() as conn:
        assert conn.execute(select(db.meta_learning_runs.c.id)).all() == []
        assert conn.execute(select(db.meta_learning_samples.c.id)).all() == []
        assert conn.execute(select(db.meta_signal_observations.c.id)).all() == []
        assert conn.execute(select(db.meta_learning_v1_switch_events.c.id)).all()


def test_no_se_apaga_con_pesos_activos_y_evento_y_reencendido(tmp_path):
    db, operator = _operator(tmp_path)
    assert operator.learning_v1_status()["enabled"] is True
    with db.engine.begin() as conn:
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps({"use_learned_weights": True})))
    with pytest.raises(ValueError, match="Apaga primero 'usar pesos aprendidos' en Prudente y Rápido"):
        operator.set_learning_v1_enabled(False, confirm=True, actor="Ramón")
    with db.engine.begin() as conn:
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps({"use_learned_weights": False})))
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "RAPIDO").values(
                settings=json.dumps({"use_learned_weights": True})))
    with pytest.raises(ValueError, match="Apaga primero 'usar pesos aprendidos' en Prudente y Rápido"):
        operator.set_learning_v1_enabled(False, confirm=True, actor="Ramón")
    with db.engine.begin() as conn:
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "RAPIDO").values(
                settings=json.dumps({"use_learned_weights": False})))
    operator.set_learning_v1_enabled(False, confirm=True, actor="Ramón")
    operator.set_learning_v1_enabled(True, confirm=True, actor="Ramón")
    status = operator.learning_v1_status()
    assert status["enabled"] is True and status["changed_at"]
    stored = []
    operator._store_learning_sample = lambda values: stored.append(values) or True
    created = operator._persist_learning_samples({"id": 1, "capital_initial": 100,
        "target_usdt": 5, "profile": "RAPIDO", "config_snapshot": {}}, "decision", [{
            "alternative_id": "alternative", "pieces": [{"symbol": "XRPUSDT"}],
            "estimate": {}, "signals_used": []}])
    assert created == 1 and stored[0]["symbol"] == "XRPUSDT"
    with db.engine.connect() as conn:
        events = conn.execute(select(db.meta_learning_v1_switch_events).order_by(
            db.meta_learning_v1_switch_events.c.id)).mappings().all()
    assert [row["enabled"] for row in events] == [False, True]


def test_apagado_no_cambia_decision_de_revision_ni_genera_ordenes(tmp_path):
    db, operator = _operator(tmp_path)
    class Core:
        def __init__(self): self.events = []
        def _owned_grids(self, _meta_id): return []
        def _ledger(self, *args, **kwargs): self.events.append(args)
    operator.core = Core()
    operator.db = SimpleNamespace()
    goal = {"id": 1}
    plan = {"alternatives": [], "candidates": []}
    before = operator._review_allocations(goal, {}, plan=plan)
    operator.db = db
    operator.set_learning_v1_enabled(False, confirm=True, actor="Ramón")
    after = operator._review_allocations(goal, {}, plan=plan)
    assert before == after == {"decision": "no_hacer_nada", "reason": "evidencia independiente insuficiente"}
    assert not any(event[2] in {"META_OPERATOR_OPEN", "META_OPERATOR_WITHDRAW"}
        for event in operator.core.events)


def test_api_con_token_y_contrato_visual_global():
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.settings = SimpleNamespace(grid_api_token="test-token")
    app.state.meta_operator = SimpleNamespace(
        learning_v1_status=lambda: {"enabled": False, "changed_at": "2026-10-30T08:30:00+00:00",
            "label": "Aprendizaje v1 apagado desde 2026-10-30T08:30:00+00:00"},
        set_learning_v1_enabled=lambda enabled, **kwargs: {"enabled": enabled, **kwargs})
    with TestClient(app) as client:
        assert client.get("/api/metas/learning-v1").status_code == 403
        status = client.get("/api/metas/learning-v1", headers={"X-API-Token": "test-token"})
        assert status.json()["enabled"] is False
        changed = client.put("/api/metas/learning-v1/switch", headers={"X-API-Token": "test-token"},
            json={"enabled": True, "confirm": True, "actor": "Prueba"})
        assert changed.json()["enabled"] is True
    from pathlib import Path
    lab = Path("frontend/metas.js").read_text(encoding="utf-8")
    intelligence = Path("frontend/intelligence.js").read_text(encoding="utf-8")
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "Aprendizaje diario v1 (motor actual)" in lab
    assert "No cambia decisiones. No borra datos." in lab
    assert "/api/metas/learning-v1" in intelligence and "runButton.disabled = !switchState.enabled" in intelligence
    assert "Reglas globales" in html
