from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.operations import NotificationOutboxState
from app.services.content_publish_notifications import (
    IMAGE_REUSE_NEXT_ACTION,
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
    generation_notification_cadence,
    generation_notify_requested,
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


def _reused(hospital_name: str, failure_class: str, hospital_id=None) -> dict:
    return {
        "hospital_id": hospital_id or uuid.uuid4(),
        "hospital_name": hospital_name,
        "content_id": uuid.uuid4(),
        "image_failure_reason": "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "image_failure_class": failure_class,
    }


def test_reused_image_publications_ride_the_existing_eight_oclock_digest() -> None:
    """새 Slack 메시지가 아니라 08:00 요약 안의 한 섹션이다."""

    hospital_id = uuid.uuid4()
    reused = [
        _reused("가나의원", "PROVIDER_QUOTA", hospital_id=hospital_id),
        _reused("가나의원", "PROVIDER_QUOTA", hospital_id=hospital_id),
        _reused("가나의원", "PROVIDER_ERROR", hospital_id=hospital_id),
    ]

    intent = build_generation_blocked_digest_intent(
        date(2026, 9, 12),
        PUBLISH_MORNING_BATCH,
        [_blocked("다라의원", "MISSING_REFERENCES", "참고 자료")],
        reused_outcomes=reused,
    )
    payload = intent.message.payload_json()

    assert intent.notification_type == "GENERATION_BLOCKED_DIGEST"
    assert "대표 이미지 재사용 발행" in payload
    assert "가나의원" in payload
    assert "재사용 발행 3건" in payload
    assert "공급자 한도·크레딧 오류" in payload, "가장 많은 실패 분류를 평문으로 적는다"
    assert IMAGE_REUSE_NEXT_ACTION.split(".")[0] in payload
    # 운영센터 링크는 요약에 이미 한 번 있으므로 두 번 넣지 않는다.
    assert payload.count("/operations?queue=incidents&status=OPEN") == 1


def test_reuse_only_batch_still_produces_exactly_one_digest() -> None:
    intent = build_generation_blocked_digest_intent(
        date(2026, 9, 12), PUBLISH_MORNING_BATCH, [], reused_outcomes=[
            _reused("가나의원", "COST_GUARD"),
        ]
    )
    payload = intent.message.payload_json()

    assert "재사용 발행 1건" in payload
    assert "비용 가드 한도" in payload
    assert len(intent.message.blocks) <= 50


def test_reuse_section_is_deduped_by_its_own_identity() -> None:
    blocked = [_blocked("다라의원", "MISSING_REFERENCES", "참고 자료")]
    reused = [_reused("가나의원", "POLICY_REJECTED")]
    without = build_generation_blocked_digest_intent(
        date(2026, 9, 12), PUBLISH_MORNING_BATCH, blocked
    )
    with_reuse = build_generation_blocked_digest_intent(
        date(2026, 9, 12), PUBLISH_MORNING_BATCH, blocked, reused_outcomes=reused
    )
    repeated = build_generation_blocked_digest_intent(
        date(2026, 9, 12), PUBLISH_MORNING_BATCH, blocked, reused_outcomes=reused
    )

    assert without.dedupe_key != with_reuse.dedupe_key, "재사용 사실은 새 요약을 만든다"
    # 같은 배치가 다시 관측돼도 outbox 키가 같아 Slack은 한 번만 나간다.
    assert repeated.dedupe_key == with_reuse.dedupe_key


def test_review_configuration_error_is_an_immediate_owner_not_a_digest_line() -> None:
    assert generation_notification_cadence("CONTENT_AI_REVIEW_CONFIG_ERROR") == "IMMEDIATE"
    assert generation_notify_requested("CONTENT_AI_REVIEW_CONFIG_ERROR") is True
    for batch in (PREPUBLISH_MORNING_BATCH, PUBLISH_MORNING_BATCH):
        assert not generation_block_digest_due(
            "CONTENT_AI_REVIEW_CONFIG_ERROR", batch=batch
        )


def test_generation_notification_cadences_stay_mutually_exclusive() -> None:
    cadences = {}
    for code in (
        "COST_BLOCKED",
        "CONTENT_AI_REVIEW_CONFIG_ERROR",
        "GENERATION_REJECTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "CONTENT_AI_HARD_FINDING",
        "PROVIDER_TIMEOUT",
        "CONTENT_IMAGE_NOT_READY",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "MISSING_APPROVED_ESSENCE",
    ):
        cadences.setdefault(generation_notification_cadence(code), []).append(code)
    assert set(cadences) == {"IMMEDIATE", "WEEKLY", "MORNING"}
    assert sum(len(codes) for codes in cadences.values()) == 9


def _yield_fact(
    name: str,
    *,
    due: int = 0,
    published: int = 0,
    reused: int = 0,
    retrying: int = 0,
    operator_required: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        hospital_name=name,
        due=due,
        published=published,
        published_with_reused_image=reused,
        retrying=retrying,
        operator_required=operator_required,
    )


def test_weekly_rollup_leads_with_one_yield_line_per_hospital() -> None:
    outcomes = [
        {
            "hospital_id": uuid.uuid4(),
            "hospital_name": "주간요약의원",
            "reason": "검증되지 않은 가격 표현이 남았습니다.",
        }
    ]
    facts = [
        _yield_fact("수율낮은의원", due=5, published=1, retrying=2, operator_required=1),
        _yield_fact("수율높은의원", due=4, published=4, reused=1),
        # 계약도 발행도 없는 병원은 줄을 만들지 않는다.
        _yield_fact("계약없는의원"),
    ]

    intent = build_generation_rejection_weekly_rollup_intent(
        date(2026, 9, 7), outcomes, facts
    )
    payload = intent.message.payload_json()
    blocks = intent.message.payload()["blocks"]
    block_ids = [block["block_id"] for block in blocks]

    assert "발행 1/5 (재사용 이미지 0, 재시도 중 2, 조치 필요 1)" in payload
    assert "발행 4/4 (재사용 이미지 1, 재시도 중 0, 조치 필요 0)" in payload
    assert "계약없는의원" not in payload
    assert "발행 5/9" in payload
    # 수율 줄이 차단 목록보다 위에 온다.
    assert block_ids.index("generation_rejection_weekly_yield_1") < block_ids.index(
        "generation_rejection_weekly_items_1"
    )
    # 부족분이 큰 병원이 먼저 보인다.
    assert payload.index("수율낮은의원") < payload.index("수율높은의원")
    # 새 메시지를 만들지 않는다 — 주 시작일 하나가 그대로 중복 키다.
    assert intent.dedupe_key == "GENERATION_REJECTION_WEEKLY_ROLLUP:2026-09-07"
    assert payload.count('"type": "button"') == 1


def test_weekly_rollup_yield_lines_cap_at_fifteen_hospitals() -> None:
    facts = [
        _yield_fact(f"수율{index:02d}의원", due=10, published=index)
        for index in range(20)
    ]

    payload = build_generation_rejection_weekly_rollup_intent(
        date(2026, 9, 7), [], facts
    ).message.payload_json()

    assert "그 외 5개" in payload
    # 부족분이 가장 큰 15곳만 남는다.
    assert "수율00의원" in payload
    assert "수율15의원" not in payload


def test_weekly_rollup_is_sent_with_zero_rejections_when_slots_were_due() -> None:
    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.rows = []

        def execute(self, _statement):
            return Result()

        def add(self, row):
            self.rows.append(row)

    db = DB()
    row = enqueue_generation_rejection_weekly_rollup_sync(
        db, date(2026, 9, 7), [], yield_facts=[_yield_fact("무차단의원", due=3, published=3)]
    )

    assert row is not None
    assert db.rows == [row]
    assert "발행 3/3" in row.fallback_text
    assert "차단 없음" in row.fallback_text


def test_weekly_rollup_is_skipped_when_there_is_nothing_to_report() -> None:
    class DB:
        def execute(self, _statement):  # pragma: no cover - 호출되면 계약 위반이다
            raise AssertionError("보낼 사실이 없으면 outbox를 조회하지 않는다")

    assert (
        enqueue_generation_rejection_weekly_rollup_sync(
            DB(), date(2026, 9, 7), [], yield_facts=[_yield_fact("조용한의원")]
        )
        is None
    )
    with pytest.raises(NotificationPayloadError):
        build_generation_rejection_weekly_rollup_intent(date(2026, 9, 7), [], [])
