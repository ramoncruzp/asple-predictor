"""Telegram delivery. The bot token must never survive into a log, an event,
an API response or an error message -- every error returned by send() has
already had the token substituted for "bot***" before it leaves this module.
"""
from __future__ import annotations

import re
import time
from typing import Callable

import requests

_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+/")


def redact(text: str, token: str | None) -> str:
    """Replace every occurrence of the literal token, and any /bot<...>/ URL
    segment, with a placeholder. Safe to call on arbitrary exception text."""
    out = str(text)
    if token:
        out = out.replace(token, "bot***")
    return _TOKEN_IN_URL.sub("/bot***/", out)


class TelegramSender:
    """Sends one message at a time to a single chat. No background retry loop:
    at most one retry, 5 seconds later, per call to send()."""

    def __init__(
        self, token: str, chat_id: str, *, timeout: float = 10.0,
        base_url: str = "https://api.telegram.org",
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.base_url = base_url
        self.sleep = sleep
        # The service's own HTTPS_PROXY points at the reverse tunnel, which
        # cannot reach Telegram -- and the "tunnel is down" alert cannot
        # depend on the tunnel being up. Never honor proxy env vars here.
        self.session = requests.Session()
        self.session.trust_env = False

    def send(self, text: str) -> dict:
        url = f"{self.base_url}/bot{self.token}/sendMessage"
        last_error = None
        for attempt in (0, 1):
            try:
                response = self.session.post(
                    url, json={"chat_id": self.chat_id, "text": text}, timeout=self.timeout)
                if response.status_code == 200:
                    return {"ok": True, "error": None}
                last_error = f"http {response.status_code}: {response.text[:300]}"
            except Exception as exc:
                last_error = str(exc)
            if attempt == 0:
                self.sleep(5)
        return {"ok": False, "error": redact(last_error or "unknown error", self.token)}
