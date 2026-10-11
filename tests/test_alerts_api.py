"""Fase 10 - GET /api/alerts/status and POST /api/alerts/test."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from api.routes import alerts
from database.db_manager import DBManager
from tests.test_grid_status_api import LocalClient


class _Sender:
    def __init__(self, *, ok=True, error=None):
        self.ok, self.error, self.sent = ok, error, []

    def send(self, text: str) -> dict:
        self.sent.append(text)
        return {"ok": self.ok, "error": self.error}


class _Dispatcher:
    def __init__(self, sender):
        self.sender_factory = lambda token, chat_id: sender


def _app(tmp_path, *, enabled=True, token="123:TOK", chat_id="chat1", sender=None, no_dispatcher=False):
    db = DBManager(f"sqlite:///{tmp_path}/alerts.db")
    app = FastAPI()
    app.include_router(alerts.router, prefix="/api/alerts")
    app.state.db = db
    app.state.settings = SimpleNamespace(
        grid_api_token="", alerts_enabled=enabled,
        telegram_bot_token=token, telegram_chat_id=chat_id,
    )
    app.state.alert_dispatcher = None if no_dispatcher else _Dispatcher(sender or _Sender())
    return LocalClient(app), db


def test_status_reports_enabled_configured_and_never_leaks_token(tmp_path):
    client, db = _app(tmp_path)
    db.add_alert_log(alert_key="k", kind="operativa", sent=True)
    response = client.get("/api/alerts/status")
    assert response.status_code == 200
    body = response.body
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["counters_today"]["operativa"] == 1
    assert "123:TOK" not in str(body)
    assert "token" not in {k.lower() for k in body}


def test_status_reports_not_configured_when_token_missing(tmp_path):
    client, _db = _app(tmp_path, token="")
    body = client.get("/api/alerts/status").body
    assert body["configured"] is False


def test_test_endpoint_sends_and_logs_success(tmp_path):
    sender = _Sender(ok=True)
    client, db = _app(tmp_path, sender=sender)
    response = client.post("/api/alerts/test", json={})
    assert response.status_code == 200
    assert response.body == {"sent": True, "error": None}
    assert sender.sent == ["✅ Mensaje de prueba de ASPLE."]
    log = db.list_alert_log()
    assert log[0]["sent"] is True


def test_test_endpoint_reports_redacted_error_without_crashing(tmp_path):
    sender = _Sender(ok=False, error="bot*** failure")
    client, db = _app(tmp_path, sender=sender)
    response = client.post("/api/alerts/test", json={})
    assert response.body == {"sent": False, "error": "bot*** failure"}
    log = db.list_alert_log()
    assert log[0]["sent"] is False


def test_test_endpoint_without_token_does_not_call_dispatcher(tmp_path):
    client, _db = _app(tmp_path, token="")
    response = client.post("/api/alerts/test", json={})
    assert response.body["sent"] is False
    assert "no configurados" in response.body["error"]


def test_both_routes_require_authorization(tmp_path):
    client, _db = _app(tmp_path)
    client.app.state.settings.grid_api_token = "secret"
    assert client.get("/api/alerts/status").status_code == 403
    assert client.post("/api/alerts/test", json={}).status_code == 403
