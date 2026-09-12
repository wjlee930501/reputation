from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.operations import NotificationOutboxState
from app.services.content_publish_notifications import (
    build_generation_blocked_digest_intent,
    build_generation_rejection_weekly_rollup_intent,
    build_missing_approved_essence_digest_intent,
    build_publish_notification_intent,
    enqueue_generation_rejection_weekly_rollup_sync,
    parse_publish_notification_identity,
    project_publish_notification,
)
from app.services.notification_contracts import NotificationPayloadError
from app.workers.generation_incident_control import (
    PREPUBLISH_MORNING_BATCH,
    PUBLISH_MORNING_BATCH,
    generation_block_digest_due,
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


def _blocked(
    hospital_name: str,
    code: str,
    title: str,
    *,
    hospital_id: uuid.UUID | None = None,
) -> dict[str, object]:
    return {
        "hospital_id": hospital_id or uuid.uuid4(),
        "hospital_name": hospital_name,
        "content_id": uuid.uuid4(),
        "scheduled_date": "2026-08-19",
        "title": title,
        "code": code,
        "cause": f"{code} 상태로 공개를 중단했습니다.",
    }


def test_many_blocked_slots_collapse_into_one_morning_slack_message() -> None:
    # Given: one morning batch that blocked five slots across two hospitals
    first_hospital_id = uuid.uuid4()
    second_hospital_id = uuid.uuid4()
    blocked = [
        _blocked(
            "가나의원",
            "FORBIDDEN_EXPRESSION",
            f"금지 표현 {index}",
            hospital_id=first_hospital_id,
        )
        for index in range(3)
    ] + [
        _blocked(
            "다라의원",
            "MISSING_REFERENCES",
            f"참고 자료 {index}",
            hospital_id=second_hospital_id,
        )
        for index in range(2)
    ]

    # When
    intent = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PUBLISH_MORNING_BATCH, blocked
    )

    # Then: one intent carries every hospital and item instead of five pages
    payload = intent.message.payload_json()
    assert intent.notification_type == "GENERATION_BLOCKED_DIGEST"
    assert "병원 2곳 · 글 5건" in payload
    assert "가나의원" in payload
    assert "다라의원" in payload
    assert intent.dedupe_key.startswith("GENERATION_BLOCKED_DIGEST:v2:")
    assert len(intent.message.blocks) <= 50


def test_blocked_digest_identity_is_stable_for_the_same_blocked_set() -> None:
    # Given: the same blocked set observed twice in one batch, in different order
    first = _blocked("가나의원", "FORBIDDEN_EXPRESSION", "금지 표현")
    second = _blocked("다라의원", "MISSING_REFERENCES", "참고 자료")

    # When
    forward = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PUBLISH_MORNING_BATCH, [first, second]
    )
    reverse = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PUBLISH_MORNING_BATCH, [second, first]
    )
    changed = build_generation_blocked_digest_intent(
        date(2026, 8, 19),
        PUBLISH_MORNING_BATCH,
        [first, second, _blocked("마바의원", "ESSENCE_NOT_ALIGNED", "운영 기준")],
    )

    # Then: a repeated batch re-sends nothing, and a genuinely new blocker does
    assert forward.dedupe_key == reverse.dedupe_key
    assert changed.dedupe_key != forward.dedupe_key


def test_blocked_digest_separates_same_name_hospitals_by_id() -> None:
    # Hospital names are not unique tenant identities.
    blocked = [
        _blocked("같은이름의원", "FORBIDDEN_EXPRESSION", "첫 병원"),
        _blocked("같은이름의원", "MISSING_REFERENCES", "둘째 병원"),
    ]

    intent = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PUBLISH_MORNING_BATCH, blocked
    )

    assert "병원 2곳 · 글 2건" in intent.message.payload_json()


def test_blocked_digest_realerts_only_for_meaningful_episode_changes() -> None:
    blocked = _blocked("변경감지의원", "ESSENCE_NOT_ALIGNED", "운영 기준 확인")

    original = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PUBLISH_MORNING_BATCH, [blocked]
    )
    next_day_same = build_generation_blocked_digest_intent(
        date(2026, 8, 20), PREPUBLISH_MORNING_BATCH, [blocked]
    )
    rescheduled = build_generation_blocked_digest_intent(
        date(2026, 8, 20),
        PUBLISH_MORNING_BATCH,
        [{**blocked, "scheduled_date": "2026-08-20"}],
    )
    changed_cause = build_generation_blocked_digest_intent(
        date(2026, 8, 20),
        PUBLISH_MORNING_BATCH,
        [{**blocked, "cause": "새 운영 기준에서 근거 충돌이 확인되었습니다."}],
    )

    assert next_day_same.dedupe_key == original.dedupe_key
    assert rescheduled.dedupe_key != original.dedupe_key
    assert changed_cause.dedupe_key != original.dedupe_key


def test_unchanged_rejected_slot_is_suppressed_across_mornings() -> None:
    rejected = {
        **_blocked("거절확인의원", "GENERATION_REJECTED", ""),
        "cause": "가격·지역·검색 구조 자동 검수 게이트가 재작성 후에도 통과되지 않았습니다.",
        "attempt_fingerprint": "same generation inputs",
    }

    first = build_generation_blocked_digest_intent(
        date(2026, 8, 19), PREPUBLISH_MORNING_BATCH, [rejected]
    )
    second = build_generation_blocked_digest_intent(
        date(2026, 8, 20), PUBLISH_MORNING_BATCH, [rejected]
    )
    changed = build_generation_blocked_digest_intent(
        date(2026, 8, 20),
        PUBLISH_MORNING_BATCH,
        [{**rejected, "attempt_fingerprint": "new inputs"}],
    )

    payload = first.message.payload_json()
    assert first.dedupe_key == second.dedupe_key
    assert changed.dedupe_key != first.dedupe_key
    assert "생성 검수 게이트 거절" in payload
    assert "가격·지역·검색 구조 자동 검수 게이트가" in payload
    assert "제목 없는 콘텐츠" not in payload


def test_blocked_digest_refuses_an_empty_batch() -> None:
    with pytest.raises(NotificationPayloadError):
        build_generation_blocked_digest_intent(date(2026, 8, 19), PUBLISH_MORNING_BATCH, [])


@pytest.mark.parametrize(
    ("code", "prepublish_due", "publish_due"),
    [
        ("PROVIDER_TIMEOUT", False, True),
        ("PROVIDER_UNAVAILABLE", False, True),
        ("GENERATION_FAILED", True, True),
        ("CONTENT_AI_REVIEW_UNAVAILABLE", False, False),
    ],
)
def test_generation_blocker_digest_cadence(
    code: str, prepublish_due: bool, publish_due: bool
) -> None:
    prepublish = generation_block_digest_due(code, batch=PREPUBLISH_MORNING_BATCH)
    publish = generation_block_digest_due(code, batch=PUBLISH_MORNING_BATCH)

    assert prepublish is prepublish_due
    assert publish is publish_due


@pytest.mark.parametrize(
    "code",
    [
        "GENERATION_LEASE_ACTIVE",
        "STALE_GENERATION_CLAIM",
        "IMAGE_GENERATION_FAILED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    ],
)
def test_recoverable_artifact_blockers_wait_for_eight_oclock(code: str) -> None:
    assert not generation_block_digest_due(code, batch=PREPUBLISH_MORNING_BATCH)
    assert generation_block_digest_due(code, batch=PUBLISH_MORNING_BATCH)


@pytest.mark.parametrize("code", ["FORBIDDEN_EXPRESSION", "MISSING_REFERENCES"])
def test_rejected_class_blockers_are_reserved_for_the_weekly_rollup(code: str) -> None:
    assert generation_block_digest_due(code, batch=PREPUBLISH_MORNING_BATCH) is False
    assert generation_block_digest_due(code, batch=PUBLISH_MORNING_BATCH) is False


@pytest.mark.parametrize("batch", [PREPUBLISH_MORNING_BATCH, PUBLISH_MORNING_BATCH])
@pytest.mark.parametrize("exhausted", [False, True])
def test_essence_digest_requires_exhausted_remediation(batch, exhausted):
    # Exhausted deterministic gates are weekly; the morning digest cannot duplicate them.
    assert not generation_block_digest_due(
        "ESSENCE_NOT_ALIGNED", batch=batch, remediation_exhausted=exhausted
    )
    # Even escalation is owned by ESSENCE_AUTO_REVIEW_ESCALATED, not this digest.
    assert not generation_block_digest_due("MISSING_APPROVED_ESSENCE", batch=batch)


def test_rejected_items_aggregate_by_hospital_and_reason_in_one_weekly_rollup() -> None:
    hospital_id = uuid.uuid4()
    outcomes = [
        {
            "hospital_id": hospital_id,
            "hospital_name": "주간요약의원",
            "reason": "검증되지 않은 가격 표현이 남았습니다.",
        },
        {
            "hospital_id": hospital_id,
            "hospital_name": "주간요약의원",
            "reason": "검증되지 않은 가격 표현이 남았습니다.",
        },
        {
            "hospital_id": uuid.uuid4(),
            "hospital_name": "근거확인의원",
            "reason": "공신력 있는 참고 자료를 확보하지 못했습니다.",
        },
    ]

    intent = build_generation_rejection_weekly_rollup_intent(date(2026, 9, 7), outcomes)
    repeated = build_generation_rejection_weekly_rollup_intent(
        date(2026, 9, 7), list(reversed(outcomes))
    )

    payload = intent.message.payload_json()
    assert intent.notification_type == "GENERATION_REJECTION_WEEKLY_ROLLUP"
    assert intent.dedupe_key == "GENERATION_REJECTION_WEEKLY_ROLLUP:2026-09-07"
    assert repeated.dedupe_key == intent.dedupe_key
    assert "병원 2곳 · 차단 3건" in payload
    assert "주간요약의원" in payload
    assert "검증되지 않은 가격 표현이 남았습니다. 2건" in payload
    assert "근거확인의원" in payload
    assert "다시 시도" not in payload
    assert payload.count('"type": "button"') == 1


def test_weekly_rejection_rollup_reuses_the_calendar_week_outbox_row() -> None:
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class DB:
        stored = None
        additions = 0

        def execute(self, _statement):
            return Result(self.stored)

        def add(self, row):
            self.stored = row
            self.additions += 1

    db = DB()
    outcomes = [
        {
            "hospital_id": uuid.uuid4(),
            "hospital_name": "중복억제의원",
            "reason": "검색 문서 구조 검수가 통과되지 않았습니다.",
        }
    ]

    first = enqueue_generation_rejection_weekly_rollup_sync(
        db, date(2026, 9, 7), outcomes
    )
    repeated = enqueue_generation_rejection_weekly_rollup_sync(
        db, date(2026, 9, 7), outcomes
    )

    assert first is repeated
    assert db.additions == 1
