import logging

import httpx

from app.services import notifier


class FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        request = httpx.Request("POST", url)
        raise httpx.ConnectError(f"failed to connect to {url}", request=request)


class _ShouldNotPostClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):  # pragma: no cover - must never run
        raise AssertionError("disallowed webhook host should never be POSTed to")


async def test_send_rejects_non_allowlisted_webhook_host(monkeypatch, caplog):
    # SSRF/exfil 방어: 허용 호스트가 아니면 POST 자체를 하지 않는다 (EXT-1/V-013).
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "http://169.254.169.254/latest/meta-data")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _ShouldNotPostClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "allowlist" in caplog.text


async def test_send_rejects_lookalike_webhook_host(monkeypatch):
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com.evil.test/x")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _ShouldNotPostClient)
    assert await notifier._send("hello") is False


def test_is_allowed_webhook_accepts_slack_only(monkeypatch):
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_ALLOWED_HOSTS", "hooks.slack.com")
    assert notifier._is_allowed_webhook("https://hooks.slack.com/services/T/B/x") is True
    assert notifier._is_allowed_webhook("http://hooks.slack.com/services/T/B/x") is False  # not https
    assert notifier._is_allowed_webhook("https://evil.test/x") is False


async def test_slack_failure_log_does_not_include_webhook_url(monkeypatch, caplog):
    webhook_url = "https://hooks.slack.com/services/T000/B000/super-secret-token"
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", webhook_url)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", FailingAsyncClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "ConnectError" in caplog.text
    assert webhook_url not in caplog.text
    assert "super-secret-token" not in caplog.text
