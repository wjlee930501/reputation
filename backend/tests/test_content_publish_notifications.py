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
