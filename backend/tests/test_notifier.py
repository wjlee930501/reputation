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


class _RejectedWebhookClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        return httpx.Response(410, request=httpx.Request("POST", url), text="revoked")


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


async def test_slack_http_failure_logs_safe_status_code_only(monkeypatch, caplog):
    webhook_url = "https://hooks.slack.com/services/T000/B000/super-secret-token"
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", webhook_url)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _RejectedWebhookClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "status=410" in caplog.text
    assert webhook_url not in caplog.text
    assert "revoked" not in caplog.text


def _capture_send(monkeypatch):
    captured = {}

    async def fake_send(text, blocks=None):
        captured["text"] = text
        captured["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)
    return captured


async def test_lead_diagnosis_intake_message_is_actionable_and_omits_pii(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_lead_diagnosis_received(
        clinic_name="장편한외과의원",
        clinic_type="외과",
        region="수서역",
        keywords=["대장내시경", "치질"],
        contact="010-1234-5678",
        email="doctor@example.com",
        slot_no=4,
        admin_url="https://admin.example.test/leads",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "무료 AI 노출 진단 접수" in body
    assert "장편한외과의원" in body
    assert "무슨 문제인지:" in body
    assert "고객 영향:" in body
    assert "지금 할 일:" in body
    assert "처리 기한:" in body
    assert "외과 · 수서역" not in body
    assert "대장내시경" not in body
    assert "010-1234-5678" not in body
    assert "doctor@example.com" not in body
    assert "오늘 4번째 접수" in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_zero_pii_purge_does_not_send_daily_noise(monkeypatch):
    async def should_not_send(*_args, **_kwargs):
        raise AssertionError("zero-work purge must stay in logs instead of Slack")

    monkeypatch.setattr(notifier, "_send", should_not_send)
    assert await notifier.notify_lead_purge_result(purged=0) is False
