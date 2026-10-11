"""Fase 10 - Telegram sender: the bot token must never leak, even on error."""
from __future__ import annotations

from alerts.telegram_sender import TelegramSender, redact


class _ExplodingSession:
    """Simulates requests.Session().post raising with the token embedded in
    the exception text, the way a real connection error would (the URL is
    part of the exception message)."""

    def __init__(self, token: str):
        self.token = token
        self.trust_env = True  # overwritten by TelegramSender.__init__
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        raise ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{self.token}/sendMessage "
            f"(Caused by ...)")


def test_exploding_exception_never_leaks_token_in_the_returned_error(monkeypatch):
    token = "123456789:AAFakeSecretTokenValueForTestingOnly"
    sender = TelegramSender(token, "chat1", sleep=lambda _seconds: None)
    exploding = _ExplodingSession(token)
    sender.session = exploding
    result = sender.send("hola")
    assert result["ok"] is False
    assert token not in result["error"]
    assert "bot***" in result["error"]
    assert exploding.calls == 2  # one retry, 5s later (sleep stubbed out)


def test_redact_handles_token_and_url_form():
    token = "999:SECRET"
    assert redact(f"error at /bot{token}/sendMessage", token) == "error at /bot***/sendMessage"
    assert redact("plain text, no token here", token) == "plain text, no token here"
    assert redact("", None) == ""


def test_trust_env_is_disabled_so_the_tunnel_proxy_is_never_used():
    sender = TelegramSender("tok", "chat1")
    assert sender.session.trust_env is False


def test_successful_send_returns_ok_without_retry():
    class _OkSession:
        def __init__(self):
            self.calls = 0

        def post(self, url, json=None, timeout=None):
            self.calls += 1
            class Resp:
                status_code = 200
                text = "{}"
            return Resp()

    sender = TelegramSender("tok", "chat1", sleep=lambda _s: (_ for _ in ()).throw(
        AssertionError("should not sleep/retry on success")))
    sender.session = _OkSession()
    result = sender.send("hola")
    assert result == {"ok": True, "error": None}
    assert sender.session.calls == 1
