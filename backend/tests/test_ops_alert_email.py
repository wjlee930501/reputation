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

    assert sent is ops_alert_email.OpsAlertResult.SENT and sent.delivered
    assert len(seen) == 1
    assert seen[0].headers["Idempotency-Key"] == "watchdog:operator:2026-10-11T00:00:00+00:00"
    body = seen[0].content.decode()
    assert "ops@example.test" in body
    assert "&lt;b&gt;escape&lt;/b&gt;" in body
    assert "<b>escape</b>" not in body


@pytest.mark.parametrize(
    "body", ["concurrent_idempotent_requests", "invalid_idempotent_request"]
)
async def test_an_idempotency_conflict_is_unknown_not_sent(configured, caplog, body):
    """409는 같은 키가 처리 중이거나 다른 본문으로 쓰였다는 뜻이다 — 이 요청이 메일을 보냈다는 확인이 아니다."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(409, text=body))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is ops_alert_email.OpsAlertResult.UNKNOWN
    assert sent.delivered is False
    # 결과 불명은 오류 경보(ERROR)가 아니라 재시도할 일이다.
    assert not any(record.levelname == "ERROR" for record in caplog.records)


async def test_server_error_is_reported_as_not_sent(configured, caplog):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="boom"))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is ops_alert_email.OpsAlertResult.FAILED
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_missing_address_is_not_sent_and_logged_for_the_gcp_alert(monkeypatch, caplog):
    monkeypatch.setattr(ops_alert_email.settings, "OPS_ALERT_EMAIL", "")
    sent = await ops_alert_email.send_ops_alert(idempotency_key="k", title="t", lines=("a",))
    assert sent is ops_alert_email.OpsAlertResult.FAILED
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    # API에서는 GCP 경보 slack_path_failures("ops alert email"), Worker에서는 worker_beat_errors가 이 줄을 잡는다.
    assert errors and errors[0].startswith("ops alert email: not configured")
