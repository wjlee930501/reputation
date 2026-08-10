from typing import TypedDict

from app.services import notifier

_BlockValue = str | dict[str, str] | list[dict[str, str | dict[str, str]]]
_SlackBlock = dict[str, _BlockValue]


class _CapturedPayload(TypedDict):
    text: str
    blocks: list[_SlackBlock]


def _capture_send(monkeypatch) -> _CapturedPayload:
    captured: _CapturedPayload = {"text": "", "blocks": []}

    async def fake_send(text: str, blocks: list[_SlackBlock] | None = None) -> bool:
        captured["text"] = text
        captured["blocks"] = blocks or []
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)
    return captured


def _section_text(payload: _CapturedPayload) -> str:
    section = payload["blocks"][0]
    text = section["text"]
    assert isinstance(text, dict)
    value = text["text"]
    assert isinstance(value, str)
    return value


def _button_url(payload: _CapturedPayload) -> str:
    assert len(payload["blocks"]) == 2
    assert payload["blocks"][0]["type"] == "section"
    actions = payload["blocks"][1]
    assert actions["type"] == "actions"
    elements = actions["elements"]
    assert isinstance(elements, list)
    assert len(elements) == 1
    button = elements[0]
    assert isinstance(button, dict)
    assert button["type"] == "button"
    url = button["url"]
    assert isinstance(url, str)
    return url


async def test_ops_alert_hides_untrusted_details_and_links_one_incident_action(monkeypatch):
    # Given
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    payload = _capture_send(monkeypatch)
    unsafe = "queue cache claim spec kill-switch /tmp/patient.pdf doctor@example.com 010-1234-5678"

    # When
    sent = await notifier.notify_ops_alert(title=unsafe, message=f"RuntimeError: {unsafe}")

    # Then
    assert sent is True
    body = _section_text(payload)
    assert all(label in body for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:"))
    assert "개발팀 전달용 참조:" in body
    assert _button_url(payload) == "https://admin.example.test/operations?queue=INCIDENTS"
    rendered = f"{payload['text']} {body}"
    for forbidden in (
        "queue",
        "cache",
        "claim",
        "spec",
        "kill-switch",
        "/tmp/patient.pdf",
        "doctor@example.com",
        "010-1234-5678",
        "RuntimeError",
    ):
        assert forbidden.lower() not in rendered.lower()


async def test_v0_ready_uses_plain_measurement_copy_and_one_report_action(monkeypatch):
    # Given
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    payload = _capture_send(monkeypatch)

    # When
    sent = await notifier.notify_v0_report_ready(
        "장편한외과의원",
        12.5,
        "gs://private/reports/patient@example.com.pdf",
        platforms=["chatgpt", "gemini"],
    )

    # Then
    assert sent is True
    body = _section_text(payload)
    assert all(label in body for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:"))
    assert "ChatGPT · Gemini" in body
    assert "개발팀 전달용 참조:" in body
    assert _button_url(payload) == "https://admin.example.test/operations?queue=REPORTS"
    rendered = f"{payload['text']} {body}"
    for forbidden in ("gs://", "patient@example.com", "Responses API", "gpt-4o", "그라운디드"):
        assert forbidden.lower() not in rendered.lower()


async def test_purge_failure_hides_raw_error_and_links_one_incident_action(monkeypatch):
    # Given
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    payload = _capture_send(monkeypatch)
    unsafe = "RuntimeError queue /tmp/export.csv doctor@example.com 010-1234-5678"

    # When
    sent = await notifier.notify_lead_purge_result(purged=3, error=unsafe)

    # Then
    assert sent is True
    body = _section_text(payload)
    assert all(label in body for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:"))
    assert "개발팀 전달용 참조:" in body
    assert _button_url(payload) == "https://admin.example.test/operations?queue=INCIDENTS"
    rendered = f"{payload['text']} {body}"
    for forbidden in (
        "RuntimeError",
        "queue",
        "/tmp/export.csv",
        "doctor@example.com",
        "010-1234-5678",
        "PII",
        "cron",
        "lead",
    ):
        assert forbidden.lower() not in rendered.lower()


async def test_purge_success_explains_outcome_and_links_one_incident_action(monkeypatch):
    # Given
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    payload = _capture_send(monkeypatch)

    # When
    sent = await notifier.notify_lead_purge_result(purged=3, skipped=1)

    # Then
    assert sent is True
    body = _section_text(payload)
    assert all(label in body for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:"))
    assert "3건" in body
    assert "개발팀 전달용 참조:" in body
    assert _button_url(payload) == "https://admin.example.test/operations?queue=INCIDENTS"
    rendered = f"{payload['text']} {body}"
    for forbidden in ("PII", "cron", "lead"):
        assert forbidden.lower() not in rendered.lower()
