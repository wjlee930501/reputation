from pathlib import Path
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


def test_legacy_ops_alert_contract_is_removed_from_the_repository():
    root = Path(__file__).resolve().parents[2]
    forbidden_symbol = "notify_" + "ops_alert"
    forbidden_reference = "LEGACY" + "-OPS-ALERT"

    assert not hasattr(notifier, forbidden_symbol)
    remnants = []
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if forbidden_reference in text:
                remnants.append(str(path.relative_to(root)))
    assert remnants == []


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


async def test_lead_diagnosis_received_omits_contact_and_uses_one_lead_action(monkeypatch):
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    payload = _capture_send(monkeypatch)

    sent = await notifier.notify_lead_diagnosis_received(
        clinic_name="테스트의원",
        clinic_type="외과",
        region="서울",
        keywords=["진단"],
        contact="010-1234-5678",
        email="doctor@example.com",
        slot_no=3,
        admin_url="https://admin.example.test/leads",
    )

    assert sent is True
    body = _section_text(payload)
    assert all(
        label in body
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )
    assert _button_url(payload) == "https://admin.example.test/leads"
    assert "010-1234-5678" not in f"{payload['text']} {body}"
    assert "doctor@example.com" not in f"{payload['text']} {body}"


