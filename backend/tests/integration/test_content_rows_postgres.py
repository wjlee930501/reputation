"""월 표의 행 상태와 차단 링크 — 실제 SQL로 검증.

행 상태는 공개 사이트와 같은 판정을 쓰고, 차단이면 운영 센터의 그 글의 인시던트·실행을
가리켜야 한다. 링크 조회는 JSON 컬럼 조건과 IN 절이라 모의 세션으로는 검증할 수 없고,
글 수에 비례해 쿼리가 늘어나는 실수도 실제 문장 수를 세야만 드러난다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import arrow
import pytest
from sqlalchemy import event

from app.api.admin.content import list_content
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity, OperationRun, OperationRunState
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

pytestmark = pytest.mark.asyncio

# 목록 1회의 SQL 문장 수: 콘텐츠 조회 1 + 발행 알림 투영 1 + 승인 기준 2
# (`get_public_approved_philosophy_id`) + 차단 링크 2(인시던트·실행). 글 수와 무관하다.
_CONTENT_LIST_STATEMENT_BUDGET = 6


async def _hospital(db, name: str, **overrides) -> Hospital:
    # 공개 사이트의 병원 게이트를 실제로 통과하는 상태 — 이게 아니면 사이트는 이 병원의
    # 어떤 글도 내보내지 않는다.
    hospital = Hospital(
        **{
            "name": name,
            "slug": f"clinic-{uuid.uuid4().hex[:12]}",
            "status": HospitalStatus.ACTIVE,
            "site_live": True,
            "profile_complete": True,
            "site_built": True,
            "schedule_set": True,
            **overrides,
        }
    )
    db.add(hospital)
    await db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan="PLAN_20",
        publish_days=[1, 4],
        active_from=date.today(),
    )
    db.add(schedule)
    await db.flush()
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{name} 홈페이지",
        raw_text="근거 자료 본문",
        content_hash=f"hash-{uuid.uuid4().hex[:12]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(UTC),
    )
    db.add(source)
    await db.flush()
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement=f"{name}은 근거 중심으로 충분히 설명합니다.",
        patient_promise="확인된 정보만 환자에게 안내합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        approved_at=datetime.now(UTC),
    )
    db.add(philosophy)
    await db.flush()
    hospital._test_schedule_id = schedule.id
    hospital._test_philosophy_id = philosophy.id
    hospital._test_seq = 0
    return hospital


async def _content(
    db,
    hospital: Hospital,
    *,
    status: ContentStatus = ContentStatus.PUBLISHED,
    scheduled_date: date,
    withheld: bool = False,
) -> ContentItem:
    hospital._test_seq += 1
    title = f"{hospital.name} 안내 {hospital._test_seq}"
    image_url = f"https://storage.googleapis.com/reputation-images/content/{'b' * 64}-ok.png"
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=hospital._test_schedule_id,
        content_type=ContentType.FAQ,
        sequence_no=hospital._test_seq,
        total_count=20,
        scheduled_date=scheduled_date,
        status=status,
        published_at=(
            datetime.now(UTC) - timedelta(hours=2)
            if status == ContentStatus.PUBLISHED
            else None
        ),
        title=title,
        body="환자 상태에 따라 치료 방향을 설명합니다.",
        faq_question="회복 기간은 얼마나 걸리나요?",
        faq_answer_summary="상태에 따라 다르며 진료 후 안내합니다.",
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo.do",
            }
        ],
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=hospital._test_philosophy_id,
        image_url=None if withheld else image_url,
        image_policy_verified_at=None if withheld else datetime.now(UTC),
        image_content_hash=None if withheld else image_content_hash_from_url(image_url),
        image_subject_hash=None if withheld else image_subject_hash(ContentType.FAQ, title),
        image_policy_version=None if withheld else IMAGE_POLICY_VERSION,
    )
    db.add(item)
    await db.flush()
    return item


async def _incident(
    db,
    hospital: Hospital,
    item: ContentItem,
    *,
    state: str = "OPEN",
    sla_due_at: datetime | None = None,
) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type="PROVIDER_TIMEOUT",
        state=state,
        sla_due_at=sla_due_at,
        severity=IncidentSeverity.HIGH,
        customer_impact="오늘 콘텐츠 초안 생성이 멈췄습니다.",
        source_type="content_item",
        source_id=str(item.id),
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="AI 공급자 응답이 지연되고 있습니다.",
        next_action="작업을 다시 시도해 주세요.",
        admin_path=f"/hospitals/{hospital.id}/content",
    )
    db.add(incident)
    await db.flush()
    return incident


async def _failed_run(
    db,
    hospital: Hospital,
    item: ContentItem,
    operation_type: str,
    *,
    state: str = OperationRunState.FAILED.value,
    requested_at: datetime | None = None,
) -> OperationRun:
    succeeded = state == OperationRunState.SUCCEEDED.value
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type=operation_type,
        state=state,
        idempotency_key=f"qa:{uuid.uuid4()}",
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(item.id), "content", (str(item.id),))
        ),
        attempt_count=1,
        total_count=1,
        success_count=1 if succeeded else 0,
        failure_count=0 if succeeded else 1,
        skipped_count=0,
        safe_error_code=None if succeeded else "CONTENT_IMAGE_NOT_VERIFIED",
        safe_error_message=None if succeeded else "대표 이미지 인증이 만료됐습니다.",
        requested_at=requested_at or datetime.now(UTC),
        completed_at=requested_at or datetime.now(UTC),
        version=1,
    )
    db.add(run)
    await db.flush()
    return run


def _month_bounds() -> tuple[date, date, date]:
    today = arrow.now("Asia/Seoul").date()
    month_end = arrow.get(today).ceil("month").date()
    return today, min(today + timedelta(days=1), month_end), month_end


async def _rows(db, hospital: Hospital) -> dict[str, dict]:
    today, _, _ = _month_bounds()
    items = await list_content(
        hospital_id=hospital.id, year=today.year, month=today.month, status_filter=None, db=db
    )
    return {item["id"]: item for item in items}


async def _seed_month(db, hospital: Hospital) -> dict[str, ContentItem]:
    today, upcoming, _ = _month_bounds()
    published = [
        await _content(db, hospital, scheduled_date=today) for _ in range(15)
    ]
    withheld = await _content(db, hospital, scheduled_date=today, withheld=True)
    drafts = [
        await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
        for _ in range(2)
    ]
    rejected = await _content(
        db, hospital, status=ContentStatus.REJECTED, scheduled_date=upcoming
    )
    cancelled = await _content(
        db, hospital, status=ContentStatus.CANCELLED, scheduled_date=upcoming
    )
    # 초안 두 건은 열린 인시던트로 막혔고, 공개 보류 글은 이미지 재인증 실행이 실패했다.
    # 초안 1건에는 실패한 재생성 실행도 같이 있다 — 사람에게는 인시던트가 먼저다.
    incidents = [
        await _incident(db, hospital, drafts[0]),
        await _incident(db, hospital, drafts[1]),
    ]
    await _failed_run(db, hospital, drafts[0], "REGENERATE_CONTENT")
    await _failed_run(db, hospital, withheld, "RECERTIFY_PUBLISHED_IMAGE")
    return {
        "published": published,
        "withheld": withheld,
        "drafts": drafts,
        "incidents": incidents,
        "rejected": rejected,
        "cancelled": cancelled,
    }


async def test_every_row_carries_the_site_judgment_and_its_block_link(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "행 상태 의원")
    seeded = await _seed_month(db, hospital)

    rows = await _rows(db, hospital)

    assert len(rows) == 20
    for item in seeded["published"]:
        assert rows[str(item.id)]["row_state"] == {
            "kind": "public",
            "label": "공개 중",
            "reason": None,
            "link": None,
        }

    withheld = rows[str(seeded["withheld"].id)]["row_state"]
    assert withheld["kind"] == "withheld"
    assert withheld["label"] == "공개 보류"
    assert withheld["reason"] == "대표 이미지 재인증 대기"
    assert withheld["link"]["kind"] == "run"
    # run 전용 화면은 없다 — 그 병원의 인시던트 큐로 보낸다.
    assert withheld["link"]["href"] == f"/operations?queue=incidents&hospital_id={hospital.id}"
    assert withheld["link"]["next_action"] == "대표 이미지 인증이 만료됐습니다."

    for draft, incident in zip(seeded["drafts"], seeded["incidents"], strict=True):
        state = rows[str(draft.id)]["row_state"]
        assert state["kind"] == "blocked"
        assert state["label"] == "차단"
        # 인시던트가 실패한 실행보다 앞선다 — 사람이 읽을 조치 문장이 거기에 있다.
        assert state["link"]["kind"] == "incident"
        assert state["link"]["href"] == (
            f"/operations?queue=incidents&hospital_id={hospital.id}"
            f"&detail=incident:{incident.id}"
        )
        assert state["reason"] == "작업을 다시 시도해 주세요."

    assert rows[str(seeded["rejected"].id)]["row_state"] == {
        "kind": "generating",
        "label": "초안 생성 중",
        "reason": "야간 재생성 대기",
        "link": None,
    }
    assert rows[str(seeded["cancelled"].id)]["row_state"] == {
        "kind": "closed",
        "label": "종료",
        "reason": "종료됨",
        "link": None,
    }


async def test_a_paused_hospitals_published_rows_are_withheld_with_the_hospital_reason(
    pg_async_session,
):
    """일시정지 병원의 발행 글은 사이트에 없다 — admin 표가 "공개 중"이라고 말하면 안 된다."""
    db = pg_async_session
    hospital = await _hospital(db, "일시정지 의원", status=HospitalStatus.PAUSED)
    today, _, _ = _month_bounds()
    item = await _content(db, hospital, scheduled_date=today)

    row = (await _rows(db, hospital))[str(item.id)]

    assert row["compliance"]["public_visibility"] == {
        "visible": False,
        "blockers": ["HOSPITAL_NOT_SERVING"],
        "blocker_labels": ["병원 공개 서비스 중이 아님"],
    }
    assert row["row_state"]["kind"] == "withheld"
    assert row["row_state"]["reason"] == "병원 공개 서비스 중이 아님"


async def test_retrying_incident_inside_its_window_is_not_operator_work(pg_async_session):
    """약속한 시간 안에서 재시도 중인 인시던트는 자동 복구다 — 행을 차단으로 만들지 않는다."""
    db = pg_async_session
    hospital = await _hospital(db, "자동 복구 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    await _incident(
        db,
        hospital,
        draft,
        state="RETRYING",
        sla_due_at=datetime.now(UTC) + timedelta(hours=2),
    )

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "scheduled"
    assert state["link"] is None


async def test_retrying_incident_past_its_deadline_becomes_operator_work(pg_async_session):
    """약속한 시각이 지나도 안 풀린 재시도는 사람의 할 일이다 — 큐와 같은 기준."""
    db = pg_async_session
    hospital = await _hospital(db, "기한 초과 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    incident = await _incident(
        db,
        hospital,
        draft,
        state="RETRYING",
        sla_due_at=datetime.now(UTC) - timedelta(hours=2),
    )

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "blocked"
    assert state["link"]["href"] == (
        f"/operations?queue=incidents&hospital_id={hospital.id}&detail=incident:{incident.id}"
    )


async def test_a_failed_run_stays_quiet_while_a_retrying_incident_owns_it(pg_async_session):
    """자동 복구가 아직 쥔 실패 run은 행을 차단으로 만들지 않는다 (Astra B4).

    첫 일시 실패는 쿨다운 뒤 스윕이 다시 집어 간다. 그동안 인시던트는 기한 안의
    RETRYING이고, 실패 run만 보고 "운영 센터에서 조치"를 띄우면 기계가 소유한 복구가
    사람의 할 일로 새어 나간다.
    """
    db = pg_async_session
    hospital = await _hospital(db, "쿨다운 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    await _failed_run(db, hospital, draft, "REGENERATE_CONTENT")
    await _incident(
        db,
        hospital,
        draft,
        state="RETRYING",
        sla_due_at=datetime.now(UTC) + timedelta(hours=2),
    )

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "scheduled"
    assert state["link"] is None


async def test_a_failed_run_with_an_open_incident_links_to_the_incident(pg_async_session):
    """열린 인시던트가 있으면 그 인시던트가 링크다 — run 대체 링크는 중복이다."""
    db = pg_async_session
    hospital = await _hospital(db, "열린 사고 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    await _failed_run(db, hospital, draft, "REGENERATE_CONTENT")
    incident = await _incident(db, hospital, draft)

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "blocked"
    assert state["link"]["kind"] == "incident"
    assert state["link"]["href"] == (
        f"/operations?queue=incidents&hospital_id={hospital.id}&detail=incident:{incident.id}"
    )
    assert state["reason"] == "작업을 다시 시도해 주세요."


async def test_a_failed_run_without_any_incident_still_links_to_the_run(pg_async_session):
    """인시던트가 아예 없는 실패는 사람이 볼 수 있는 유일한 흔적이다 — 그것마저 지우지 않는다."""
    db = pg_async_session
    hospital = await _hospital(db, "사고 없는 실패 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    await _failed_run(db, hospital, draft, "REGENERATE_CONTENT")

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "blocked"
    assert state["link"]["kind"] == "run"
    assert state["link"]["href"] == f"/operations?queue=incidents&hospital_id={hospital.id}"
    assert state["link"]["next_action"] == "대표 이미지 인증이 만료됐습니다."


async def test_a_superseded_failed_run_does_not_block_a_healthy_draft(pg_async_session):
    """실패 뒤에 성공한 재시도가 있으면 지난 실패는 이미 지나간 일이다."""
    db = pg_async_session
    hospital = await _hospital(db, "복구 완료 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    now = datetime.now(UTC)
    await _failed_run(
        db, hospital, draft, "REGENERATE_CONTENT", requested_at=now - timedelta(hours=3)
    )
    await _failed_run(
        db,
        hospital,
        draft,
        "REGENERATE_CONTENT",
        state=OperationRunState.SUCCEEDED.value,
        requested_at=now - timedelta(hours=1),
    )

    state = (await _rows(db, hospital))[str(draft.id)]["row_state"]

    assert state["kind"] == "scheduled"
    assert state["link"] is None


async def test_block_links_do_not_leak_to_other_rows_or_hospitals(pg_async_session):
    """다른 병원의 인시던트가 같은 글 번호를 가리켜도 이 병원 표에는 붙지 않는다."""
    db = pg_async_session
    hospital = await _hospital(db, "격리 의원")
    other = await _hospital(db, "다른 의원")
    _, upcoming, _ = _month_bounds()
    draft = await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)
    # 병원 범위를 빠뜨리면 남의 인시던트가 이 글의 링크가 된다.
    await _incident(db, other, draft)

    rows = await _rows(db, hospital)

    assert rows[str(draft.id)]["row_state"]["kind"] == "scheduled"
    assert rows[str(draft.id)]["row_state"]["link"] is None


async def _list_statement_count(db, hospital: Hospital) -> int:
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await _rows(db, hospital)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_list_statement_count_is_constant_in_the_number_of_rows(pg_async_session):
    db = pg_async_session
    hospital = await _hospital(db, "쿼리 예산 의원")
    today, upcoming, _ = _month_bounds()
    for _ in range(4):
        await _content(db, hospital, scheduled_date=today)
    await _content(db, hospital, status=ContentStatus.DRAFT, scheduled_date=upcoming)

    five = await _list_statement_count(db, hospital)

    await _seed_month(db, hospital)
    twenty_five = await _list_statement_count(db, hospital)

    assert five == _CONTENT_LIST_STATEMENT_BUDGET
    assert twenty_five == five
