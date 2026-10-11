"""Read-only status + manual test-send for the Telegram alert dispatcher.

Never returns the bot token or chat id; only whether they are configured.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from api.routes.grids import _authorize

router = APIRouter()


def _day_start_utc(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


@router.get("/status")
def status(request: Request) -> dict:
    _authorize(request)
    settings = request.app.state.settings
    db = request.app.state.db
    enabled = bool(getattr(settings, "alerts_enabled", False))
    token = str(getattr(settings, "telegram_bot_token", "") or "").strip()
    chat_id = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    configured = bool(token and chat_id)
    now = datetime.now(timezone.utc)
    last_log = db.list_alert_log(limit=1)
    last_error_entries = db.list_alert_log(limit=50)
    last_error = next((row["error"] for row in last_error_entries if row.get("error")), None)
    return {
        "enabled": enabled,
        "configured": configured,
        "watermarks": {
            "last_event_id": db.get_alert_state("last_event_id"),
            "probe_down": bool(db.get_alert_state("probe_down") or False),
            "probe_fail_count": int(db.get_alert_state("probe_fail_count") or 0),
            "digest_last_date": db.get_alert_state("digest_last_date"),
        },
        "last_sent": last_log[0] if last_log else None,
        "last_error": last_error,
        "counters_today": {
            "operativa": db.count_alert_log_since("operativa", _day_start_utc(now)),
            "fill": db.count_alert_log_since("fill", _day_start_utc(now)),
        },
    }


@router.post("/test")
def send_test(request: Request) -> dict:
    _authorize(request)
    settings = request.app.state.settings
    token = str(getattr(settings, "telegram_bot_token", "") or "").strip()
    chat_id = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    if not token or not chat_id:
        return {"sent": False, "error": "ALERTS_ENABLED/TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados."}
    dispatcher = getattr(request.app.state, "alert_dispatcher", None)
    if dispatcher is None:
        return {"sent": False, "error": "El despachador de alertas no está inicializado."}
    sender = dispatcher.sender_factory(token, chat_id)
    result = sender.send("✅ Mensaje de prueba de ASPLE.")
    db = request.app.state.db
    db.add_alert_log(alert_key="manual_test", kind="operativa", sent=bool(result.get("ok")),
                      error=None if result.get("ok") else result.get("error"))
    return {"sent": bool(result.get("ok")), "error": None if result.get("ok") else result.get("error")}
