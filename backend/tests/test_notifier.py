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
