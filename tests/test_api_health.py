import asyncio
import json
import logging

import api.main as api_main


def _request(path):
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if request_sent:
            await asyncio.Future()
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path.split("?", 1)[0], "raw_path": path.split("?", 1)[0].encode(),
        "query_string": path.partition("?")[2].encode(), "root_path": "",
        "headers": [(b"host", b"testserver"), (b"accept", b"application/json")],
        "server": ("testserver", 80), "client": ("testclient", 123), "state": {},
    }

    async def call():
        await api_main.app(scope, receive, send)

    asyncio.run(call())
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], json.loads(body or b"null")


def test_health_is_fast_and_does_not_open_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing' / 'never-created.db'}")
    calls = []

    def fail_if_database_is_opened(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("health must not open the database")

    monkeypatch.setattr(api_main, "DBManager", fail_if_database_is_opened)
    status, body = _request("/api/health")
    assert status == 200
    assert set(body) == {"status", "uptime_s"}
    assert body["status"] == "ok"
    assert isinstance(body["uptime_s"], int) and body["uptime_s"] >= 0
    assert calls == []
    health_index = next(i for i, route in enumerate(api_main.app.routes) if getattr(route, "path", None) == "/api/health")
    static_index = next(i for i, route in enumerate(api_main.app.routes) if getattr(route, "name", None) == "frontend")
    assert health_index < static_index


def test_slow_request_warning_has_route_status_duration_and_thread_count(monkeypatch, caplog):
    monkeypatch.setattr(api_main, "SLOW_REQUEST_THRESHOLD_SECONDS", 0.01)

    async def slow_test_route():
        await asyncio.sleep(0.03)
        return {"ok": True}

    api_main.app.add_api_route("/__test_slow_health", slow_test_route, methods=["GET"], include_in_schema=False)
    slow_route = api_main.app.router.routes.pop()
    static_index = next(i for i, route in enumerate(api_main.app.router.routes) if getattr(route, "name", None) == "frontend")
    api_main.app.router.routes.insert(static_index, slow_route)
    with caplog.at_level(logging.WARNING, logger="asple.slow"):
        status, _ = _request("/__test_slow_health?private-token=must-not-be-logged")

    assert status == 200
    record = next(record for record in caplog.records if record.name == "asple.slow")
    message = record.getMessage()
    assert "GET /__test_slow_health status=200" in message
    assert "duration_ms=" in message and "active_threads=" in message
    assert "private-token" not in message and "must-not-be-logged" not in message
