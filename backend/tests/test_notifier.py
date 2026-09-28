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

    async def fake_send(text, blocks=None, **_kwargs):
        captured["text"] = text
        captured["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)
    return captured


async def test_introduction_inquiry_message_never_claims_free_diagnosis(monkeypatch):
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_lead_created(
        clinic_name="도입문의의원",
        contact="010-1234-5678",
        admin_url="https://admin.example.test/leads",
    )

    assert sent is True
    rendered = f"{captured['text']} {captured['blocks'][0]['text']['text']}"
    assert "[새 문의]" in rendered
    assert "문의 유형: 일반 문의" in rendered
    assert "무료 진단" not in rendered
    assert captured["blocks"][1]["elements"][0]["url"] == "https://admin.example.test/leads"


async def test_inquiry_message_uses_introduction_copy_without_clinic_type(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_lead_created(
        clinic_name="기존진단의원",
        contact="010-1234-5678",
    )

    rendered = f"{captured['text']} {captured['blocks'][0]['text']['text']}"
    assert "[새 문의]" in rendered
    assert "문의 유형: 일반 문의" in rendered
    assert "무료 진단" not in rendered
    assert "진료과/지역" not in rendered


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
    assert "무료 AI 노출 진단" in body
    assert "장편한외과의원" in body
    assert "[새 신청]" in body
    assert "상담 일정" in body
    assert "담당자를 정한 뒤" in body
    assert "오늘 4번째 신청" in body
    assert "외과 · 수서역" not in body
    assert "대장내시경" not in body
    assert "010-1234-5678" not in body
    assert "doctor@example.com" not in body
    assert "오늘 4번째 신청" in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_zero_pii_purge_does_not_send_daily_noise(monkeypatch):
    async def should_not_send(*_args, **_kwargs):
        raise AssertionError("zero-work purge must stay in logs instead of Slack")

    monkeypatch.setattr(notifier, "_send", should_not_send)
    assert await notifier.notify_lead_purge_result(purged=0) is False


class _RecordingClient:
    posted: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        _RecordingClient.posted.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url), text="ok")


async def test_inquiry_goes_to_the_inquiry_webhook_when_configured(monkeypatch):
    _RecordingClient.posted = []
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/ops")
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL_INQUIRY", "https://hooks.slack.com/services/inquiry")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _RecordingClient)

    assert await notifier.notify_lead_created(clinic_name="도입문의의원", contact="010-1234-5678") is True

    assert [url for url, _ in _RecordingClient.posted] == ["https://hooks.slack.com/services/inquiry"]


async def test_inquiry_falls_back_to_the_operator_webhook_when_unset(monkeypatch):
    _RecordingClient.posted = []
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/ops")
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL_INQUIRY", "")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _RecordingClient)

    assert await notifier.notify_lead_created(clinic_name="도입문의의원", contact="010-1234-5678") is True

    assert [url for url, _ in _RecordingClient.posted] == ["https://hooks.slack.com/services/ops"]


async def test_inquiry_webhook_is_held_to_the_same_host_allowlist(monkeypatch):
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/ops")
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL_INQUIRY", "https://evil.example.test/hook")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _ShouldNotPostClient)

    assert await notifier.notify_lead_created(clinic_name="도입문의의원", contact="010-1234-5678") is False


async def test_other_notifications_stay_on_the_operator_webhook(monkeypatch):
    _RecordingClient.posted = []
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/ops")
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL_INQUIRY", "https://hooks.slack.com/services/inquiry")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _RecordingClient)

    await notifier.notify_lead_purge_result(purged=0, error="파기 결과 확정 실패")

    assert [url for url, _ in _RecordingClient.posted] == ["https://hooks.slack.com/services/ops"]


async def test_inquiry_message_summarizes_the_application_and_names_reputation(monkeypatch):
    from datetime import UTC, datetime

    captured = _capture_send(monkeypatch)

    await notifier.notify_lead_created(
        clinic_name="장편한외과의원",
        contact="010-1234-5678",
        diagnosis_note="초도 노출 진단 자동 시작",
        specialty="외과",
        region_keyword="강남역",
        core_keywords=["치질", "탈장"],
        source_path="/#contact",
        created_at=datetime(2026, 9, 28, 5, 3, tzinfo=UTC),
    )

    body = captured["blocks"][0]["text"]["text"]
    assert captured["text"].startswith("[Lead : 도입 문의] [Re:putation] [새 문의] 장편한외과의원")
    assert "*[Re:putation] [새 문의] 장편한외과의원*" in body
    for line in (
        "진료과: 외과",
        "지역: 강남역",
        "핵심 키워드: 치질, 탈장",
        "연락처: `010-****-5678`",
        "유입 경로: /#contact",
        "접수 시각: 2026-09-28 14:03 KST",
        "자동 처리: 초도 노출 진단 자동 시작",
    ):
        assert line in body
    assert "010-1234-5678" not in f"{captured['text']} {body}"


async def test_inquiry_summary_marks_missing_fields_and_masks_keywords(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_lead_created(
        clinic_name="도입문의의원",
        contact="010-1234-5678",
        core_keywords=["무릎", "문의 010-9999-8888"],
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "진료과: (미입력)" in body
    assert "지역: (미입력)" in body
    assert "접수 시각" not in body
    assert "010-9999-8888" not in body
