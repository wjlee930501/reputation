from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.operations import NotificationOutboxState
from app.services.content_publish_notifications import (
    build_publish_notification_intent,
    parse_publish_notification_identity,
    project_publish_notification,
)


def _published_item() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        title="진료 전 확인할 점",
        content_type=SimpleNamespace(value="FAQ"),
        sequence_no=1,
        total_count=12,
        scheduled_date=datetime(2026, 8, 10, tzinfo=UTC).date(),
        published_at=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        published_by="SYSTEM_MANUAL_RECOVERY",
        carried_over_from=None,
    )


def test_publish_intent_keeps_publication_identity_and_one_admin_action() -> None:
    item = _published_item()
    hospital = SimpleNamespace(id=item.hospital_id, name="테스트의원")

    intent = build_publish_notification_intent(item, hospital)

    identity = parse_publish_notification_identity(intent.dedupe_key)
    assert identity is not None
    assert identity.content_id == item.id
    assert identity.published_at == item.published_at
    assert intent.notification_type == "CONTENT_PUBLISHED"
    assert intent.max_attempts == 1
    assert intent.message.admin_url.endswith(f"/hospitals/{hospital.id}/content?content={item.id}")
    payload = intent.message.payload_json()
    assert all(
        label in payload
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )
    assert payload.count('"type": "button"') == 1


def test_publish_intent_sanitizes_visible_identity() -> None:
    item = _published_item()
    item.title = "queue /tmp/patient.pdf doctor@example.com 010-1234-5678"
    hospital = SimpleNamespace(id=item.hospital_id, name="/Users/private/patient@example.com")

    intent = build_publish_notification_intent(item, hospital)

    visible = " ".join(
        [intent.message.fallback_text]
        + [
            str(block.get("text", {}).get("text", ""))
            for block in intent.message.blocks
            if isinstance(block.get("text"), dict)
        ]
    )
    for forbidden in ("/tmp/", "/Users/", "patient@example.com", "010-1234-5678"):
        assert forbidden not in visible


def test_publish_projection_uses_outbox_state_instead_of_legacy_timestamp() -> None:
    sent = project_publish_notification(
        NotificationOutboxState.SENT.value,
        notification_id=uuid.uuid4(),
        safe_error_code=None,
    )
    failed = project_publish_notification(
        NotificationOutboxState.FAILED.value,
        notification_id=uuid.uuid4(),
        safe_error_code="DELIVERY_RETRY_EXHAUSTED",
    )

    assert sent["state"] == "SENT"
    assert sent["label"] == "Slack 전달 완료"
    assert failed["state"] == "FAILED"
    assert failed["label"] == "Slack 전달 실패"
    assert failed["publication_impact"] == "콘텐츠 발행에는 영향이 없습니다."
    assert failed["next_action"]


def test_publish_projection_treats_missing_success_alert_as_intentional_silence() -> None:
    projection = project_publish_notification(
        None,
        notification_id=None,
        safe_error_code=None,
    )

    assert projection["state"] == "NOT_REQUIRED"
    assert projection["label"] == "자동 관제 중"
    assert projection["problem"] is None
