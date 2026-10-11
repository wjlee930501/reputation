"""운영 경보 메일 — Slack이 닿지 않을 때의 2차 경로(감사 F8)."""

import httpx
import pytest

from app.services import ops_alert_email


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ops_alert_email.settings, "OPS_ALERT_EMAIL", "ops@example.test")
    monkeypatch.setattr(ops_alert_email.settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(ops_alert_email.settings, "LEAD_MAIL_FROM", "Re:putation <noreply@example.test>")
    monkeypatch.setattr(ops_alert_email.settings, "LEAD_MAIL_REPLY_TO", "")


async def test_sends_one_mail_with_the_given_idempotency_key(configured):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="watchdog:operator:2026-10-11T00:00:00+00:00",
            title="외부 감시 경보를 Slack에 전달하지 못했습니다",
            lines=("아침 자동 발행이 멈췄습니다.", "<b>escape</b>"),
            client=client,
        )

    assert sent is True
    assert len(seen) == 1
    assert seen[0].headers["Idempotency-Key"] == "watchdog:operator:2026-10-11T00:00:00+00:00"
    body = seen[0].content.decode()
    assert "ops@example.test" in body
    assert "&lt;b&gt;escape&lt;/b&gt;" in body
    assert "<b>escape</b>" not in body


async def test_conflict_means_the_key_was_already_used(configured):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(409, text="invalid_idempotent_request"))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is True


async def test_server_error_is_reported_as_not_sent(configured, caplog):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="boom"))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is False
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_missing_address_is_not_sent_and_logged_for_the_gcp_alert(monkeypatch, caplog):
    monkeypatch.setattr(ops_alert_email.settings, "OPS_ALERT_EMAIL", "")
    sent = await ops_alert_email.send_ops_alert(idempotency_key="k", title="t", lines=("a",))
    assert sent is False
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    # API에서는 GCP 경보 slack_path_failures("ops alert email"), Worker에서는 worker_beat_errors가 이 줄을 잡는다.
    assert errors and errors[0].startswith("ops alert email: not configured")
