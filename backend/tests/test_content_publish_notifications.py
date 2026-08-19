from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.models.operations import NotificationOutboxState
from app.services.content_publish_notifications import (
    build_content_publish_digest_intent,
    build_missing_approved_essence_digest_intent,
    build_post_publish_review_overdue_intent,
    build_publish_notification_intent,
    enqueue_post_publish_review_overdue_notification_sync,
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
    assert intent.max_attempts == 3
    assert intent.message.admin_url.endswith(f"/hospitals/{hospital.id}/content?content={item.id}")
    payload = intent.message.payload_json()
    assert all(
        label in payload
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )
    assert payload.count('"type": "button"') == 1


def test_publish_digest_counts_neutral_copy_one_action_and_cycle_dedupe() -> None:
    cycle_date = date(2026, 8, 19)
    first_hospital_id = uuid.uuid4()
    outcomes = [
        {"hospital_id": first_hospital_id, "hospital_name": "첫번째의원"},
        {"hospital_id": first_hospital_id, "hospital_name": "첫번째의원"},
        {"hospital_id": uuid.uuid4(), "hospital_name": "두번째의원"},
    ]

    intent = build_content_publish_digest_intent(cycle_date, outcomes)
    same_cycle = build_content_publish_digest_intent(cycle_date, outcomes[:1])

    assert intent.notification_type == "CONTENT_PUBLISH_DIGEST"
    assert intent.dedupe_key == "CONTENT_PUBLISH_DIGEST:2026-08-19"
    assert same_cycle.dedupe_key == intent.dedupe_key
    payload = intent.message.payload_json()
    assert "병원 2곳 · 글 3건" in payload
    assert "사실 확인해주세요" in payload
    assert payload.count('"type": "button"') == 1
    assert intent.message.admin_url.endswith("/operations?queue=TODAY")
    for warning_copy in ("무슨 문제인지", "고객 영향", "잘못된 정보가 공개"):
        assert warning_copy not in payload


def test_missing_essence_digest_counts_one_cycle_and_one_admin_action() -> None:
    cycle_date = date(2026, 8, 19)
    first_hospital_id = uuid.uuid4()
    outcomes = [
        {"hospital_id": first_hospital_id},
        {"hospital_id": first_hospital_id},
        {"hospital_id": uuid.uuid4()},
    ]

    intent = build_missing_approved_essence_digest_intent(cycle_date, outcomes)
    same_cycle = build_missing_approved_essence_digest_intent(cycle_date, outcomes[:1])

    assert intent.notification_type == "MISSING_APPROVED_ESSENCE_DIGEST"
    assert intent.dedupe_key == "MISSING_APPROVED_ESSENCE_DIGEST:2026-08-19"
    assert same_cycle.dedupe_key == intent.dedupe_key
    payload = intent.message.payload_json()
    assert "온보딩 병원 2곳 · 글 3건" in payload
    assert "승인 기준이 없어 생성을 건너뜀" in payload
    assert payload.count('"type": "button"') == 1
    assert intent.message.admin_url.endswith("/operations?queue=onboarding")


def test_post_publish_review_overdue_intent_is_episode_deduped() -> None:
    item = _published_item()
    hospital = SimpleNamespace(id=item.hospital_id, name="테스트의원")

    intent = build_post_publish_review_overdue_intent(item, hospital)
    second = build_post_publish_review_overdue_intent(item, hospital)

    assert intent.dedupe_key == second.dedupe_key
    assert intent.notification_type == "POST_PUBLISH_REVIEW_OVERDUE"
    assert intent.max_attempts == 3
    assert intent.message.admin_url.endswith(f"/hospitals/{hospital.id}/content?content={item.id}")
    payload = intent.message.payload_json()
    assert "24시간" in payload
    assert payload.count('"type": "button"') == 1


def test_enqueue_post_publish_review_overdue_sync_reuses_existing_row() -> None:
    item = _published_item()
    hospital = SimpleNamespace(id=item.hospital_id, name="테스트의원")
    existing = SimpleNamespace(dedupe_key="existing")

    class _Result:
        def scalar_one_or_none(self):
            return existing

    class _DB:
        added = []

        def execute(self, _stmt):
            return _Result()

        def add(self, value):
            self.added.append(value)

    db = _DB()

    row = enqueue_post_publish_review_overdue_notification_sync(db, item, hospital)

    assert row is existing
    assert db.added == []


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
