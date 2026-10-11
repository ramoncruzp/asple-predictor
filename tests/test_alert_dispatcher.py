"""Fase 10 - AlertDispatcher: polls grid_events against a real engine+monitor
(FakeExchange, SQLite temporary, no network) and verifies the Telegram
messages it would send, without ever touching a real Telegram chat.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from alerts.dispatcher import AlertDispatcher
from config.settings import Settings
from grid.monitor import GridMonitor
from tests.test_grid_engine import create, make_engine
from tests.test_grid_monitor import monitor_settings


class FakeSender:
    def __init__(self, *, fail_on=None):
        self.sent: list[str] = []
        self.fail_on = fail_on

    def send(self, text: str) -> dict:
        if self.fail_on is not None and self.fail_on in text:
            return {"ok": False, "error": "simulated failure"}
        self.sent.append(text)
        return {"ok": True, "error": None}


def _alerts_settings(**overrides):
    values = {"ALERTS_ENABLED": True, "TELEGRAM_BOT_TOKEN": "123:TOK", "TELEGRAM_CHAT_ID": "chat1",
              "ALERTS_COOLDOWN_MIN": 60, "ALERTS_DAILY_CAP": 30, "ALERTS_FILLS_DAILY_CAP": 200,
              "ALERTS_DIGEST_ENABLED": False, "ALERTS_FILLS_ENABLED": True}
    values.update(overrides)
    return lambda: Settings(_env_file=None, **values)


def _dispatcher(db, *, now=None, sender=None, probe=None, **settings_overrides):
    clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    sender = sender or FakeSender()
    disp = AlertDispatcher(
        db, _alerts_settings(**settings_overrides), clock=clock,
        sender_factory=lambda token, chat_id: sender,
        probe=probe or (lambda: True), sleep=lambda _s: None,
    )
    return disp, sender


# -- (2) watermark initializes to MAX(id): no history is sent ---------------

def test_initial_watermark_is_max_id_and_sends_no_history():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"])
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()  # produces BUY_FILLED + SELL_PLACED events, pre-dating alerts
    disp, sender = _dispatcher(db)
    result = disp.run_once()
    assert result["status"] == "ok"
    # The first-ever poll does send the one-time startup message, but nothing
    # about the pre-existing BUY_FILLED/SELL_PLACED history.
    assert sender.sent == ["⚠️ ASPLE iniciado."]
    assert db.get_alert_state("last_event_id") == db.get_max_grid_event_id()


# -- (3) a new GRID_AUTO_CLOSE sends exactly one alert, never resent --------

def test_new_grid_auto_close_sends_exactly_one_alert_with_reason_and_symbol():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    disp, sender = _dispatcher(db)
    disp.run_once()  # establish watermark before the event we care about
    sender.sent.clear()
    db.add_grid_event(run_id=None, source="MONITOR", event_type="GRID_AUTO_CLOSE",
                       grid_id=grid["id"], reason="out_of_range", details={})
    disp.run_once()
    assert len(sender.sent) == 1
    assert "XRPUSDT #" in sender.sent[0]
    assert "out_of_range" in sender.sent[0]
    disp.run_once()  # no new event -> no resend
    assert len(sender.sent) == 1


# -- (4) DUST_RECONCILIATION reason filter -----------------------------------

@pytest.mark.parametrize("reason, expect_alert", [
    ("baseline", False), ("no_material_drift", False), ("drift", True),
])
def test_dust_reconciliation_only_alerts_on_material_drift(reason, expect_alert):
    _engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    disp, sender = _dispatcher(db)
    disp.run_once()
    sender.sent.clear()
    db.add_grid_event(run_id=None, source="MONITOR", event_type="DUST_RECONCILIATION",
                       grid_id=None, reason=reason, details={"asset": "XRP"})
    disp.run_once()
    assert (len(sender.sent) == 1) is expect_alert


# -- (5) who: api events never alert -----------------------------------------

def test_manual_api_events_do_not_alert():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    disp, sender = _dispatcher(db)
    disp.run_once()
    sender.sent.clear()
    db.add_grid_event(run_id=None, source="CLI", event_type="GRID_PAUSED",
                       grid_id=grid["id"], reason="api_pause", details={"source": "API"})
    db.add_grid_event(run_id=None, source="CLI", event_type="GRID_AUTO_CLOSE",
                       grid_id=grid["id"], reason="manual", details={"who": "api"})
    disp.run_once()
    assert sender.sent == []


# -- (6) probe: 2 fails silent, 3rd alerts once, 4th does not repeat,
#        recovery alerts once ------------------------------------------------

def test_probe_alerts_after_threshold_and_on_recovery():
    _engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    calls = {"n": 0, "down": True}
    def probe():
        calls["n"] += 1
        return not calls["down"]
    disp, sender = _dispatcher(db, probe=probe)
    disp.run_once()  # startup message, fail 1
    sender.sent.clear()
    disp.run_once()  # fail 2
    assert sender.sent == []
    disp.run_once()  # fail 3 -> alert
    assert len(sender.sent) == 1
    assert "inalcanzable" in sender.sent[0]
    disp.run_once()  # fail 4 -> no repeat
    assert len(sender.sent) == 1
    calls["down"] = False
    disp.run_once()  # recovers -> one message
    assert len(sender.sent) == 2
    assert "volvió" in sender.sent[1]


# -- (7) daily caps -----------------------------------------------------------

def test_daily_cap_for_operational_alerts_sends_one_cap_message():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    disp, sender = _dispatcher(db, **{"ALERTS_DAILY_CAP": 1, "ALERTS_COOLDOWN_MIN": 0})
    disp.run_once()
    sender.sent.clear()
    db.add_grid_event(run_id=None, source="MONITOR", event_type="GRID_AUTO_CLOSE",
                       grid_id=grid["id"], reason="r1", details={})
    disp.run_once()
    assert len(sender.sent) == 1
    db.add_grid_event(run_id=None, source="MONITOR", event_type="TESTNET_RESET_DETECTED",
                       grid_id=None, reason="r2", details={})
    disp.run_once()
    # Exactly ONE cap-reached message total, not one per over-cap event.
    assert len(sender.sent) == 1
    assert "Tope diario" in sender.sent[0]


def test_daily_cap_for_fills():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    disp, sender = _dispatcher(db, **{"ALERTS_FILLS_DAILY_CAP": 0})
    disp.run_once()
    sender.sent.clear()
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"])
    GridMonitor(db, exchange, engine, monitor_settings()).run_once()
    disp.run_once()
    assert len(sender.sent) == 1
    assert "Tope de avisos" in sender.sent[0]


# -- (8) disabled / unconfigured never sends, never errors ------------------

def test_alerts_disabled_sends_nothing():
    _engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    disp, sender = _dispatcher(db, **{"ALERTS_ENABLED": False})
    result = disp.run_once()
    assert result == {"status": "disabled"}
    assert sender.sent == []


def test_alerts_unconfigured_token_sends_nothing():
    _engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    disp, sender = _dispatcher(db, **{"TELEGRAM_BOT_TOKEN": ""})
    result = disp.run_once()
    assert result == {"status": "unconfigured"}
    assert sender.sent == []


# -- (10) Telegram failure at startup never crashes --------------------------

def test_dispatcher_does_not_raise_when_telegram_send_fails():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    sender = FakeSender(fail_on="ASPLE")
    disp, _ = _dispatcher(db, sender=sender)
    result = disp.run_once()  # triggers the startup message, which fails
    assert result["status"] == "ok"
    log = db.list_alert_log()
    assert any(not row["sent"] for row in log)


# -- (11) buy/sell fill formatting, grouping, and no-duplicate -------------

def test_buy_then_sell_fill_messages_use_real_stored_prices_and_pnl():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)  # XRPUSDT, range 90-110, 5 levels, bid=100/ask=100.01
    disp, sender = _dispatcher(db)
    disp.run_once()  # establish watermark past grid creation, before the fill
    sender.sent.clear()
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    buy_price = Decimal(str(buy_level["price"]))
    exchange.fill(buy_level["order_id"])
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()
    disp.run_once()
    assert len(sender.sent) == 1
    buy_message = sender.sent[0]
    assert "Compra ejecutada" in buy_message
    assert str(buy_price) in buy_message
    refreshed = next(row for row in db.get_grid_levels(grid["id"])
                      if int(row["level_idx"]) == int(buy_level["level_idx"]))
    assert refreshed["state"] == "SELL_OPEN"
    sell_order_id = refreshed["order_id"]
    exchange.fill(sell_order_id)
    monitor.run_once()
    disp.run_once()
    assert len(sender.sent) == 2
    sell_message = sender.sent[1]
    assert "Venta ejecutada" in sell_message
    assert "Ganancia neta" in sell_message
    events = db.list_grid_events(grid_id=grid["id"], event_type="SELL_FILLED")
    stored_pnl = Decimal(str(events[0]["details"]["cycle_pnl"]))
    assert str(stored_pnl) in sell_message or f"+{stored_pnl}" in sell_message
    # Same fill never re-alerted.
    disp.run_once()
    assert len(sender.sent) == 2


def test_two_fills_same_grid_same_pass_produce_one_grouped_message():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    disp, sender = _dispatcher(db)
    disp.run_once()  # establish watermark past grid creation
    sender.sent.clear()
    buy_levels = [row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN"]
    assert len(buy_levels) >= 2
    exchange.fill(buy_levels[0]["order_id"])
    exchange.fill(buy_levels[1]["order_id"])
    GridMonitor(db, exchange, engine, monitor_settings()).run_once()
    disp.run_once()
    assert len(sender.sent) == 1
    assert "Movimientos" in sender.sent[0]
    assert sender.sent[0].count("Compró") == 2


# -- (12) ALERTS_FILLS_ENABLED=false suppresses fills but not operational ---

def test_fills_disabled_still_allows_operational_alerts():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"])
    GridMonitor(db, exchange, engine, monitor_settings()).run_once()
    disp, sender = _dispatcher(db, **{"ALERTS_FILLS_ENABLED": False})
    disp.run_once()
    sender.sent.clear()
    disp.run_once()
    assert sender.sent == []
    db.add_grid_event(run_id=None, source="MONITOR", event_type="GRID_AUTO_CLOSE",
                       grid_id=grid["id"], reason="r", details={})
    disp.run_once()
    assert len(sender.sent) == 1


# -- (14) BOOK_ONE_SIDED alerts as operational with cooldown -----------------

# -- (13) daily digest: once per local day, at/after the configured hour ---

def test_digest_sends_once_per_local_day_at_configured_hour():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"])
    GridMonitor(db, exchange, engine, monitor_settings()).run_once()
    # America/Santo_Domingo is UTC-4: 11:30 UTC on this date is 07:30 local,
    # before the configured 08:00 local hour.
    before_hour = datetime(2026, 10, 12, 11, 30, tzinfo=timezone.utc)
    disp, sender = _dispatcher(db, now=before_hour, **{"ALERTS_DIGEST_ENABLED": True})
    disp.run_once()
    assert not any("Resumen diario" in text for text in sender.sent)
    sender.sent.clear()
    at_hour = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)  # 08:30 local
    disp.clock = lambda: at_hour
    disp.run_once()
    digest_messages = [text for text in sender.sent if "Resumen diario" in text]
    assert len(digest_messages) == 1
    assert "XRPUSDT #" in digest_messages[0]
    # Same local day, still past the hour -> not sent again.
    sender.sent.clear()
    later_same_day = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
    disp.clock = lambda: later_same_day
    disp.run_once()
    assert not any("Resumen diario" in text for text in sender.sent)
    # Next day at/after the hour -> sent again.
    sender.sent.clear()
    next_day = datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc)
    disp.clock = lambda: next_day
    disp.run_once()
    assert any("Resumen diario" in text for text in sender.sent)


def test_book_one_sided_alerts_with_cooldown():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    disp, sender = _dispatcher(db, now=now)
    disp.run_once()
    sender.sent.clear()
    db.add_grid_event(run_id=None, source="MONITOR", event_type="BOOK_ONE_SIDED",
                       grid_id=None, reason="libro_unilateral", details={"symbol": "XRPUSDT"})
    disp.run_once()
    assert len(sender.sent) == 1
    db.add_grid_event(run_id=None, source="MONITOR", event_type="BOOK_ONE_SIDED",
                       grid_id=None, reason="libro_unilateral", details={"symbol": "XRPUSDT"})
    disp.run_once()
    assert len(sender.sent) == 1  # within cooldown


# -- Fase 10b item 1 (CRITICAL): default_probe must not crash on every call,
#    and must still go through the tunnel proxy (trust_env left at default) --

def test_default_probe_returns_true_when_the_endpoint_responds(monkeypatch):
    import http.server
    import socket
    import threading

    from alerts import dispatcher as dispatcher_module

    class PingHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/v3/ping":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = http.server.HTTPServer(("127.0.0.1", port), PingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(dispatcher_module, "BINANCE_PING_URL",
                             f"http://127.0.0.1:{port}/api/v3/ping")
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        assert dispatcher_module.default_probe() is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert dispatcher_module.default_probe() is False  # server is down now


def test_default_probe_respects_env_proxy_settings(monkeypatch):
    """default_probe must use requests' normal env-driven proxy behavior
    (it should go THROUGH the tunnel so a dead tunnel is detected), not
    bypass it like TelegramSender does."""
    from alerts import dispatcher as dispatcher_module

    calls = []

    def fake_get(url, timeout=None, **kwargs):
        calls.append((url, kwargs))
        class Resp:
            status_code = 200
        return Resp()

    monkeypatch.setattr(dispatcher_module.requests, "get", fake_get)
    assert dispatcher_module.default_probe() is True
    assert calls
    # requests.get doesn't take trust_env at all -- passing it would raise
    # TypeError, which is exactly the bug being fixed here.
    assert "trust_env" not in calls[0][1]


# -- Fase 10b item 2: startup message fires once per process start, not
#    once ever; two restarts >10 min apart each announce, two restarts
#    within 10 min only announce once total ---------------------------------

def test_startup_message_fires_once_per_process_but_not_forever_silenced():
    _engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)
    first, sender1 = _dispatcher(db, now=t0)
    first.run_once()
    assert sender1.sent == ["⚠️ ASPLE iniciado."]
    first.run_once()  # same process, same instance -> never again
    assert sender1.sent == ["⚠️ ASPLE iniciado."]

    # A second process restarting 5 minutes later (within the 10-min
    # cooldown): its own instance tries once, but the shared DB cooldown
    # blocks it -- no second message.
    t1 = t0 + timedelta(minutes=5)
    second, sender2 = _dispatcher(db, now=t1)
    second.run_once()
    assert sender2.sent == []

    # A third process restarting >10 minutes after the first message: new
    # instance, cooldown has expired -> announces again.
    t2 = t0 + timedelta(minutes=11)
    third, sender3 = _dispatcher(db, now=t2)
    third.run_once()
    assert sender3.sent == ["⚠️ ASPLE iniciado."]


# -- Fase 10b item 4: the digest's "today" must use the local day
#    (America/Santo_Domingo, UTC-4), not the UTC calendar day ---------------

def test_digest_pnl_today_uses_local_day_boundary_not_utc_midnight():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    # 02:00 UTC on Oct 12 is 22:00 local on Oct 11 -- "yesterday" locally,
    # even though it is the same UTC calendar day as the two events below it.
    yesterday_local_evening = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)
    today_local_morning = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)  # 06:00 local
    db.add_grid_event(run_id=None, source="MONITOR", event_type="SELL_FILLED",
                       grid_id=grid["id"], level_idx=0, ts=yesterday_local_evening,
                       details={"cycle_pnl": "5.0", "cycles_completed": 1, "executed_qty": "1"})
    db.add_grid_event(run_id=None, source="MONITOR", event_type="SELL_FILLED",
                       grid_id=grid["id"], level_idx=1, ts=today_local_morning,
                       details={"cycle_pnl": "3.0", "cycles_completed": 1, "executed_qty": "1"})
    disp, _sender = _dispatcher(db)
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)  # 10:00 local, same local day as the 2nd event
    digest_text = disp._build_digest(now)
    assert "3.0" in digest_text or "3" in digest_text
    assert "8.0" not in digest_text  # would be 5.0+3.0 under the old UTC-midnight bug
