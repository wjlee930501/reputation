"""현황 한 번 호출 — 실제 SQL로 상태·예외·이번 달과 쿼리 예산을 함께 확인한다.

화면이 8~10번 나눠 부르던 것을 한 호출로 합쳤으므로, 합친 호출이 병원 규모에 비례해
커지지 않는지가 이 화면의 계약이다. 공개 보류 판정·인시던트 그룹핑·JSONB gap 필터는
모의 세션으로 검증할 수 없다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import arrow
import pytest
from sqlalchemy import event

from app.api.admin.hospital_overview import get_hospital_overview
from app.api.admin.operations_center_read_routes import get_operations_queue
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity
from app.schemas.operations import OperationsQueue
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.evidence_noise import compute_evidence_noise_hash
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)

pytestmark = pytest.mark.asyncio

# 병원 1 + 콘텐츠 준비 묶음 4 + 인시던트 2 + 이번 달 콘텐츠 1 + 공개 기준 묶음 2 +
# 활성 일정 1 + 주간 언급률 1. 인시던트·콘텐츠가 몇 건이든 이 수는 그대로여야 한다.
_OVERVIEW_STATEMENT_BUDGET = 12


async def _hospital(
    db,
    name: str,
    *,
    status: HospitalStatus = HospitalStatus.ACTIVE,
    site_live: bool = True,
    site_built: bool = True,
    profile_complete: bool = True,
    schedule_set: bool = True,
    plan: str = "PLAN_12",
    with_schedule: bool = True,
    approved_essence: bool = True,
    unprocessed_source: bool = False,
    escalated_findings: tuple[str, ...] = (),
) -> Hospital:
    hospital = Hospital(
        name=name,
        slug=f"clinic-{uuid.uuid4().hex[:12]}",
        status=status,
        site_live=site_live,
        site_built=site_built,
        profile_complete=profile_complete,
        schedule_set=schedule_set,
    )
    db.add(hospital)
    await db.flush()

    hospital._test_schedule_id = None
    if with_schedule:
        schedule = ContentSchedule(
            hospital_id=hospital.id,
            plan=plan,
            publish_days=[1, 4],
            active_from=date.today(),
        )
        db.add(schedule)
        await db.flush()
        hospital._test_schedule_id = schedule.id

    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{name} 홈페이지",
        raw_text="원장이 직접 설명한 진료 원칙 본문",
        content_hash=f"hash-{uuid.uuid4().hex[:12]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(UTC),
    )
    db.add(source)
    await db.flush()
    if unprocessed_source:
        db.add(
            HospitalSourceAsset(
                hospital_id=hospital.id,
                source_type=SourceType.INTERVIEW,
                title=f"{name} 인터뷰",
                raw_text="아직 처리되지 않은 근거 자료",
                content_hash=f"hash-{uuid.uuid4().hex[:12]}",
                status=SourceStatus.PENDING,
            )
        )

    hospital._test_philosophy_id = None
    if approved_essence:
        philosophy = HospitalContentPhilosophy(
            hospital_id=hospital.id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            positioning_statement=f"{name}은 근거 중심으로 충분히 설명합니다.",
            patient_promise="확인된 정보만 환자에게 안내합니다.",
            source_snapshot_hash=compute_sources_snapshot_hash([source]),
            evidence_noise_hash=compute_evidence_noise_hash([]),
            source_asset_ids=[str(source.id)],
            approved_at=datetime.now(UTC),
        )
        db.add(philosophy)
        await db.flush()
        hospital._test_philosophy_id = philosophy.id
    if escalated_findings:
        db.add(
            HospitalContentPhilosophy(
                hospital_id=hospital.id,
                version=2,
                status=PhilosophyStatus.DRAFT,
                positioning_statement=f"{name} 초안",
                unsupported_gaps=[
                    {"field": AUTO_REVIEW_GAP_FIELD, "reason": finding}
                    for finding in escalated_findings
                ],
            )
        )
    await db.flush()
    hospital._test_seq = 0
    return hospital


async def _content(db, hospital: Hospital, *, withheld: bool = False) -> ContentItem:
    """이번 달 예정일의 발행 글. `withheld`는 이미지 인증만 비워 공개 보류로 만든다."""
    hospital._test_seq += 1
    title = f"{hospital.name} 안내 {hospital._test_seq}"
    image_url = f"https://storage.googleapis.com/reputation-images/content/{'b' * 64}-ok.png"
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=hospital._test_schedule_id,
        content_type=ContentType.FAQ,
        sequence_no=hospital._test_seq,
        total_count=12,
        scheduled_date=arrow.now("Asia/Seoul").date(),
        status=ContentStatus.PUBLISHED,
        published_at=datetime.now(UTC) - timedelta(hours=1),
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


async def _operations_actor(db) -> AdminUser:
    actor = AdminUser(
        email=f"{uuid.uuid4().hex}@example.com",
        name="AE QA",
        role=ROLE_OWNER,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    db.add(actor)
    await db.flush()
    return actor


async def _incident(db, hospital: Hospital, *, incident_type: str = "PROVIDER_TIMEOUT") -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type=incident_type,
        state="OPEN",
        severity=IncidentSeverity.HIGH,
        customer_impact="오늘 콘텐츠 초안 생성이 멈췄습니다.",
        source_type="content_generation",
        safe_error_code=incident_type,
        safe_error_message="AI 공급자 응답이 지연되고 있습니다.",
        next_action="작업을 다시 시도해 주세요.",
        admin_path=f"/hospitals/{hospital.id}/content",
    )
    db.add(incident)
    await db.flush()
    return incident


def _next_month_first_day() -> date:
    next_month = arrow.now("Asia/Seoul").shift(months=1)
    return date(next_month.year, next_month.month, 1)


async def test_live_hospital_reports_states_exceptions_and_this_month(pg_async_session):
    """정상 운영 중인 병원의 현황 — 세 카드는 상태만, 예외는 서버가 허용한 행동만 말한다."""
    db = pg_async_session
    actor = await _operations_actor(db)
    hospital = await _hospital(db, "현황 의원")
    await _content(db, hospital)
    await _content(db, hospital)
    await _content(db, hospital, withheld=True)
    incident = await _incident(db, hospital)

    overview = await get_hospital_overview(hospital.id, db)

    assert overview.public_service.kind == "live"
    assert overview.public_service.label == "공개 중"
    assert overview.public_service.remaining == []
    assert overview.content.kind == "auto"
    assert overview.content.label == "자동 발행 중"
    assert overview.domain.kind == "unused"

    queue = await get_operations_queue(
        OperationsQueue.INCIDENTS, hospital_id=hospital.id, db=db, _actor=actor,
        owner=None, status=None, severity=None, sla=None, recovery=None,
        page=1, page_size=25,
    )
    row = next(item for item in queue.items if item.incident_id == incident.id)
    assert len(overview.exceptions) == 1
    card = overview.exceptions[0]
    assert card.kind == "incident"
    assert card.id == str(incident.id)
    assert card.allowed_actions == [row.action.kind]
    assert card.href.startswith("/operations")

    assert overview.month.published_count == 3
    assert overview.month.public_count == 2
    assert overview.month.withheld_count == 1
    assert overview.month.planned_total == 12
    assert overview.month.next_report_date == _next_month_first_day()


async def test_preparing_hospital_splits_human_work_from_system_work(pg_async_session):
    """준비 중인 병원 — 링크는 사람이 지금 할 수 있는 조건에만 붙는다."""
    db = pg_async_session
    hospital = await _hospital(
        db,
        "준비 중 의원",
        status=HospitalStatus.BUILDING,
        site_live=False,
        site_built=False,
        profile_complete=False,
        schedule_set=False,
        with_schedule=False,
        approved_essence=False,
        unprocessed_source=True,
        escalated_findings=("근거 없는 효과 표현", "출처가 확인되지 않은 수치"),
    )

    overview = await get_hospital_overview(hospital.id, db)

    assert overview.public_service.kind == "not_live"
    assert [(c.key, c.actor, c.href) for c in overview.public_service.remaining] == [
        ("profile_complete", "human", f"/hospitals/{hospital.id}/profile"),
        ("site_built", "system", None),
    ]
    # 예외는 준비 중보다 앞선다 — 사람이 손대야 나머지가 풀린다.
    assert overview.content.kind == "exception"
    assert overview.content.label == "예외 있음"

    card = next(item for item in overview.exceptions if item.kind == "escalated_draft")
    assert "근거 없는 효과 표현" in card.evidence
    assert "출처가 확인되지 않은 수치" in card.evidence
    assert card.allowed_actions == ["re_review", "approve_with_override"]
    assert card.href == f"/hospitals/{hospital.id}/essence"

    assert overview.month.published_count == 0
    assert overview.month.planned_total == 0
    assert overview.month.mention_rate is None


async def test_preparing_hospital_lists_schedule_and_source_conditions(pg_async_session):
    """예외가 없으면 남은 조건이 그대로 보인다 — `sources:N`은 건수까지 문구에 들어간다."""
    db = pg_async_session
    hospital = await _hospital(
        db,
        "조건 나열 의원",
        schedule_set=False,
        with_schedule=False,
        unprocessed_source=True,
    )

    overview = await get_hospital_overview(hospital.id, db)

    assert overview.content.kind == "preparing"
    assert [(c.key, c.label, c.actor) for c in overview.content.remaining] == [
        ("schedule", "발행 요일 설정", "human"),
        ("sources:1", "근거 자료 처리 1건", "system"),
        ("essence_review", "콘텐츠 운영 기준 자동 검수", "system"),
    ]
    assert overview.content.remaining[0].href == f"/hospitals/{hospital.id}/schedule"


async def _overview_query_count(db, hospital_id) -> int:
    statements: list[str] = []

    # 시드가 남긴 세션 캐시가 병원 행 조회를 가리면 예산이 실제 요청보다 작게 나온다.
    db.expire_all()

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await get_hospital_overview(hospital_id, db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_overview_query_count_is_constant_across_incidents_and_content(pg_async_session):
    db = pg_async_session
    small = await _hospital(db, "쿼리예산 작은 의원")
    await _content(db, small)
    await _incident(db, small)
    # 세션 identity map이 첫 호출의 SQL을 가리지 않도록 두 병원 모두 같은 방식으로 잰다.
    small_count = await _overview_query_count(db, small.id)

    large = await _hospital(db, "쿼리예산 큰 의원")
    for index in range(5):
        await _content(db, large, withheld=index == 0)
        await _incident(db, large, incident_type=f"PROVIDER_TIMEOUT_{index}")
    large_count = await _overview_query_count(db, large.id)

    assert small_count == _OVERVIEW_STATEMENT_BUDGET
    assert large_count == small_count
