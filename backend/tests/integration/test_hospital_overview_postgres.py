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
from app.api.admin.operations_center_incident_queries import count_operator_incidents
from app.api.admin.operations_center_read_routes import get_operations_queue
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import Incident, IncidentSeverity, OperationRun
from app.models.sov import QueryMatrix, SovRecord
from app.schemas.operations import OperationsQueue
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.evidence_noise import compute_evidence_noise_hash
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)

pytestmark = pytest.mark.asyncio

# 병원 1 + 콘텐츠 준비 묶음 4(승인·자료·노이즈·예외 초안) + 인시던트 2 + 이번 달 콘텐츠 1 +
# 공개 기준 묶음 2 + 주간 언급률 1. 인시던트·콘텐츠·예외 초안이 몇 건이든 이 수는 그대로여야
# 한다 — 예외 초안은 준비 묶음이 이미 읽고, 약정 편수는 계약 요금제라 일정 조회가 없다.
_OVERVIEW_STATEMENT_BUDGET = 11


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
    hospital_plan: Plan | None = Plan.PLAN_12,
    with_schedule: bool = True,
    approved_essence: bool = True,
    unprocessed_source: bool = False,
    without_sources: bool = False,
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
        plan=hospital_plan,
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
    # 자료가 한 건도 없는 병원 — 자동 검수가 기다리기만 하는 상태를 만든다.
    if not without_sources:
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

    hospital._test_snapshot_hash = compute_sources_snapshot_hash(
        [] if without_sources else [source]
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
                # 예외는 지금 자료 판의 초안만이다(쓰기 게이트의 `_drafts_for_snapshot`).
                source_snapshot_hash=hospital._test_snapshot_hash,
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


async def _operations_actor(db, *, role: str = ROLE_OWNER) -> AdminUser:
    actor = AdminUser(
        email=f"{uuid.uuid4().hex}@example.com",
        name="AE QA",
        role=role,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    db.add(actor)
    await db.flush()
    return actor


async def _incident(
    db,
    hospital: Hospital,
    *,
    incident_type: str = "PROVIDER_TIMEOUT",
    state: str = "OPEN",
    sla_due_at: datetime | None = None,
) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        sla_due_at=sla_due_at,
        recovered_at=datetime.now(UTC) if state in ("RECOVERED", "ACKNOWLEDGED") else None,
        acknowledged_at=datetime.now(UTC) if state == "ACKNOWLEDGED" else None,
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


async def _overview(db, hospital_id, actor: AdminUser | None = None):
    """현황 호출. 예외 카드의 행동 가능 여부가 요청자 권한에 달려 actor가 필수다."""
    return await get_hospital_overview(hospital_id, db, actor or await _operations_actor(db))


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

    overview = await _overview(db, hospital.id)

    assert overview.public_service.kind == "live"
    assert overview.public_service.label == "공개 중"
    assert overview.public_service.remaining == []
    assert overview.content.kind == "auto"
    assert overview.content.label == "자동 발행 중"
    assert overview.domain.kind == "unused"

    queue = await get_operations_queue(
        OperationsQueue.INCIDENTS, hospital_id=hospital.id, db=db, actor=actor,
        owner=None, status=None, severity=None, sla=None, recovery=None,
        page=1, page_size=25,
    )
    row = next(item for item in queue.items if item.incident_id == incident.id)
    assert len(overview.exceptions) == 1
    card = overview.exceptions[0]
    assert card.kind == "incident"
    assert card.id == str(incident.id)
    # 카드가 싣는 행동 코드는 운영 센터 행이 낸 것과 같은 집합이다.
    assert card.allowed_actions == [
        action.kind for action in (row.action, row.retry, row.resolve, row.assign)
        if action is not None and action.enabled
    ]
    assert card.href == (
        f"/operations?queue=incidents&hospital_id={hospital.id}&detail=incident:{incident.id}"
    )

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
        hospital_plan=None,
        with_schedule=False,
        approved_essence=False,
        unprocessed_source=True,
        escalated_findings=("근거 없는 효과 표현", "출처가 확인되지 않은 수치"),
    )

    overview = await _overview(db, hospital.id)

    assert overview.public_service.kind == "not_live"
    assert [(c.key, c.actor, c.href) for c in overview.public_service.remaining] == [
        ("profile_complete", "human", f"/hospitals/{hospital.id}/info#info-director"),
        ("site_built", "system", None),
    ]
    # 예외는 준비 중보다 앞선다 — 사람이 손대야 나머지가 풀린다.
    assert overview.content.kind == "exception"
    assert overview.content.label == "예외 있음"

    card = next(item for item in overview.exceptions if item.kind == "escalated_draft")
    assert "근거 없는 효과 표현" in card.evidence
    assert "출처가 확인되지 않은 수치" in card.evidence
    assert card.allowed_actions == ["re_review", "approve_with_override"]
    # 재검수·예외 승인은 essence 라우트다 — 운영 센터 mutation 서술자로 만들지 않는다.
    assert card.actions == []
    assert card.hospital_id == hospital.id
    assert card.incident_id is None
    assert card.href == f"/hospitals/{hospital.id}/essence"

    assert overview.month.published_count == 0
    assert overview.month.planned_total == 0
    # 측정이 한 번도 없으면 값도 측정 시점도 없다 — 0%로 접지 않는다.
    assert overview.month.mention_rate is None
    assert overview.month.mention_rate_measured_at is None


async def test_preparing_hospital_with_base_only_lists_schedule_condition(pg_async_session):
    """승인된 base가 있으면 자료 처리 중에도 사람의 발행 요일 설정만 남는다."""
    db = pg_async_session
    hospital = await _hospital(
        db,
        "조건 나열 의원",
        schedule_set=False,
        with_schedule=False,
        unprocessed_source=True,
    )

    overview = await _overview(db, hospital.id)

    assert overview.content.kind == "preparing"
    assert [(c.key, c.label, c.actor) for c in overview.content.remaining] == [
        ("schedule", "발행 요일 설정", "human"),
    ]
    assert overview.content.remaining[0].href == (
        f"/hospitals/{hospital.id}/content#content-schedule"
    )


async def test_overview_and_list_agree_on_what_needs_an_operator(pg_async_session):
    """자동 복구 중인 건은 예외가 아니다 — 현황의 카드 수와 목록의 예외 수는 같은 규칙이다."""
    db = pg_async_session
    hospital = await _hospital(db, "복구 중 의원")
    await _incident(db, hospital, incident_type="PROVIDER_TIMEOUT")
    # 복구를 확인해 닫은 건과 약속한 재시도 창이 남은 건은 AE의 할 일이 아니다.
    await _incident(db, hospital, incident_type="IMAGE_BLOCKED", state="ACKNOWLEDGED")
    await _incident(
        db,
        hospital,
        incident_type="SITE_BUILD_SLOW",
        state="RETRYING",
        sla_due_at=datetime.now(UTC) + timedelta(hours=2),
    )

    overview = await _overview(db, hospital.id)
    counts = await count_operator_incidents(db, [hospital.id], now=datetime.now(UTC))

    assert [card.kind for card in overview.exceptions] == ["incident"]
    assert counts[hospital.id] == len(overview.exceptions) == 1

    # 재시도 기한이 지나면 마지막 전이가 여전히 RETRYING이어도 사람의 일이 된다.
    overdue = await _hospital(db, "기한 지난 의원")
    await _incident(
        db,
        overdue,
        incident_type="SITE_BUILD_SLOW",
        state="RETRYING",
        sla_due_at=datetime.now(UTC) - timedelta(hours=2),
    )

    overdue_overview = await _overview(db, overdue.id)
    overdue_counts = await count_operator_incidents(db, [overdue.id], now=datetime.now(UTC))

    assert overdue_counts[overdue.id] == len(overdue_overview.exceptions) == 1


async def test_same_cause_incidents_are_one_exception_on_both_screens(pg_async_session):
    """같은 원인 두 건은 현황에서 카드 하나다 — 목록도 같은 묶음으로 센다."""
    db = pg_async_session
    hospital = await _hospital(db, "같은 원인 의원", escalated_findings=("근거 없는 효과 표현",))
    await _incident(db, hospital)
    await _incident(db, hospital)

    overview = await _overview(db, hospital.id)
    counts = await count_operator_incidents(db, [hospital.id], now=datetime.now(UTC))

    assert [card.kind for card in overview.exceptions] == ["incident", "escalated_draft"]
    # 목록이 내려보내는 예외 수 = 인시던트 묶음 + 예외 초안(hospitals.list_hospitals와 같은 합).
    assert counts[hospital.id] + 1 == len(overview.exceptions) == 2


async def test_paused_hospital_withholds_every_published_item(pg_async_session):
    """공개 API가 병원 게이트에서 막으면 발행 글은 한 편도 공개 페이지에 없다."""
    db = pg_async_session
    hospital = await _hospital(db, "일시정지 의원", status=HospitalStatus.PAUSED)
    for _ in range(3):
        await _content(db, hospital)

    overview = await _overview(db, hospital.id)

    assert overview.public_service.kind == "paused"
    # 일시정지 병원에는 야간 생성이 돌지 않는다 — "자동 발행 중"이라 말하면 화면이 거짓말을 한다.
    assert overview.content.kind == "preparing"
    assert [(c.key, c.label, c.actor, c.href) for c in overview.content.remaining] == [
        ("service_paused", "서비스 재개", "human", None)
    ]
    assert overview.month.published_count == 3
    assert overview.month.public_count == 0
    assert overview.month.withheld_count == 3


async def test_a_hospital_without_sources_asks_a_person_not_the_system(pg_async_session):
    """자료가 하나도 없으면 자동 검수는 시작조차 못 한다 — 남은 일은 사람이 자료를 등록하는 것이다."""
    db = pg_async_session
    hospital = await _hospital(db, "자료 없는 의원", approved_essence=False, without_sources=True)

    overview = await _overview(db, hospital.id)

    assert overview.content.kind == "preparing"
    assert [(c.key, c.label, c.actor, c.href) for c in overview.content.remaining] == [
        (
            "sources_required",
            "공식 채널·근거 자료 등록",
            "human",
            f"/hospitals/{hospital.id}/info#info-channels",
        )
    ]


async def test_an_escalated_draft_from_an_older_snapshot_stops_being_an_exception(
    pg_async_session,
):
    """자료가 바뀌어 새 판이 승인되면 옛 초안은 예외가 아니다 — 안 그러면 영영 "예외 있음"이다."""
    db = pg_async_session
    hospital = await _hospital(
        db, "옛 초안 의원", escalated_findings=("근거 없는 효과 표현",)
    )
    # 새 자료가 처리되면 지금 자료 판이 바뀐다 — 초안이 선언한 판은 옛 판이 된다.
    db.add(
        HospitalSourceAsset(
            hospital_id=hospital.id,
            source_type=SourceType.INTERVIEW,
            title=f"{hospital.name} 인터뷰",
            raw_text="새로 처리된 근거 자료",
            content_hash=f"hash-{uuid.uuid4().hex[:12]}",
            status=SourceStatus.PROCESSED,
            processed_at=datetime.now(UTC),
        )
    )
    await db.flush()

    overview = await _overview(db, hospital.id)

    assert overview.content.kind != "exception"
    assert [card.kind for card in overview.exceptions] == []


async def test_a_gap_without_a_reason_is_not_an_exception_card(pg_async_session):
    """사유가 없으면 승인 게이트도 막지 않는다 — 목록의 예외 수와 카드 수가 갈리지 않게."""
    db = pg_async_session
    hospital = await _hospital(db, "사유 없는 초안 의원", escalated_findings=("",))

    overview = await _overview(db, hospital.id)
    counts = await count_operator_incidents(db, [hospital.id], now=datetime.now(UTC))

    assert overview.content.kind == "auto"
    assert overview.exceptions == []
    assert counts.get(hospital.id, 0) == 0


async def test_an_open_incident_is_a_card_even_behind_an_in_sla_retry(pg_async_session):
    """같은 원인 묶음의 대표가 자동 복구 중이라고 사람 몫 예외가 사라지면 안 된다."""
    db = pg_async_session
    hospital = await _hospital(db, "대표 뒤집힘 의원")
    await _incident(db, hospital, incident_type="PROVIDER_TIMEOUT")
    # 같은 원인 · 기한이 남은 RETRYING — 정렬상 대표가 되기 쉬운 자리다.
    await _incident(
        db,
        hospital,
        incident_type="PROVIDER_TIMEOUT",
        state="RETRYING",
        sla_due_at=datetime.now(UTC) + timedelta(hours=2),
    )

    overview = await _overview(db, hospital.id)
    counts = await count_operator_incidents(db, [hospital.id], now=datetime.now(UTC))

    assert [card.kind for card in overview.exceptions] == ["incident"]
    assert counts[hospital.id] == len(overview.exceptions) == 1


async def test_many_recovering_groups_do_not_push_the_operator_incident_off(pg_async_session):
    """자동 복구 중인 묶음이 아무리 많아도 사람 몫 예외는 카드로 남는다(페이지 밀림 금지)."""
    db = pg_async_session
    hospital = await _hospital(db, "묶음 많은 의원")
    for index in range(25):
        await _incident(
            db,
            hospital,
            incident_type=f"RECOVERING_{index}",
            state="RETRYING",
            sla_due_at=datetime.now(UTC) + timedelta(hours=2),
        )
    await _incident(db, hospital, incident_type="PROVIDER_TIMEOUT")

    overview = await _overview(db, hospital.id)
    counts = await count_operator_incidents(db, [hospital.id], now=datetime.now(UTC))

    assert [card.kind for card in overview.exceptions] == ["incident"]
    assert counts[hospital.id] == len(overview.exceptions) == 1


async def test_planned_total_is_the_contracted_plan_not_next_month_replacement(pg_async_session):
    """다음 달부터 적용될 교체 일정의 편수가 이번 달 분모로 새어 들면 안 된다."""
    db = pg_async_session
    hospital = await _hospital(db, "요금제 의원")
    next_month = arrow.now("Asia/Seoul").shift(months=1)
    db.add(
        ContentSchedule(
            hospital_id=hospital.id,
            plan="PLAN_20",
            publish_days=[1, 4],
            active_from=date(next_month.year, next_month.month, 1),
        )
    )
    await db.flush()

    overview = await _overview(db, hospital.id)

    assert overview.month.planned_total == 12


async def test_planned_total_falls_back_to_the_schedule_already_active(pg_async_session):
    """계약 요금제가 없는 레거시 병원만 일정으로 되돌아간다 — 이번 달에 시작한 일정만 본다."""
    db = pg_async_session
    hospital = await _hospital(db, "레거시 의원", hospital_plan=None, plan="PLAN_16")
    next_month = arrow.now("Asia/Seoul").shift(months=1)
    db.add(
        ContentSchedule(
            hospital_id=hospital.id,
            plan="PLAN_20",
            publish_days=[1, 4],
            active_from=date(next_month.year, next_month.month, 1),
        )
    )
    await db.flush()

    overview = await _overview(db, hospital.id)

    assert overview.month.planned_total == 16


async def test_mention_rate_says_which_week_it_was_measured(pg_async_session):
    """추이의 마지막 버킷은 롤링 7일이라 거의 늘 비어 있다 — 값과 함께 잰 주를 말한다."""
    db = pg_async_session
    hospital = await _hospital(db, "측정 의원")
    query = QueryMatrix(
        hospital_id=hospital.id, query_text="강남 임플란트 잘하는 곳", query_intent="LOCAL"
    )
    db.add(query)
    await db.flush()
    db.add(
        SovRecord(
            hospital_id=hospital.id,
            query_id=query.id,
            ai_platform="chatgpt",
            measured_at=datetime.now(UTC) - timedelta(days=13),
            mention_verdict="MATCHED",
            is_mentioned=True,
            raw_response="답변에 병원 이름이 있습니다.",
            measurement_status="SUCCESS",
        )
    )
    await db.flush()

    overview = await _overview(db, hospital.id)

    assert overview.month.mention_rate == 100.0
    assert overview.month.mention_rate_measured_at == arrow.now("Asia/Seoul").shift(weeks=-2).date()


async def _overview_query_count(db, hospital_id) -> int:
    statements: list[str] = []

    # 시드가 남긴 세션 캐시가 병원 행 조회를 가리면 예산이 실제 요청보다 작게 나온다.
    db.expire_all()
    # 요청자는 라우터 의존성이 이미 읽어 둔 행이다(만료 상태가 아니다) — 측정 전에 만든다.
    actor = await _operations_actor(db)

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await get_hospital_overview(hospital_id, db, actor)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_overview_query_count_is_constant_across_incidents_and_content(pg_async_session):
    db = pg_async_session
    # 예외 초안까지 함께 있는 병원으로 잰다 — 초안 카드가 조회를 하나 더 내면 여기서 잡힌다.
    small = await _hospital(
        db, "쿼리예산 작은 의원", escalated_findings=("근거 없는 효과 표현",)
    )
    await _content(db, small)
    await _incident(db, small)
    # 세션 identity map이 첫 호출의 SQL을 가리지 않도록 두 병원 모두 같은 방식으로 잰다.
    small_count = await _overview_query_count(db, small.id)

    large = await _hospital(
        db, "쿼리예산 큰 의원", escalated_findings=("근거 없는 효과 표현", "출처 미확인 수치")
    )
    for index in range(5):
        await _content(db, large, withheld=index == 0)
        await _incident(db, large, incident_type=f"PROVIDER_TIMEOUT_{index}")
    large_count = await _overview_query_count(db, large.id)

    assert small_count == _OVERVIEW_STATEMENT_BUDGET
    assert large_count == small_count


async def test_exception_card_carries_the_actions_the_server_will_accept(pg_async_session):
    """예외 카드는 운영 센터의 mutation 서술자를 그대로 싣는다 — 화면이 경로·권한을 새로 쓰지 않는다."""
    db = pg_async_session
    owner = await _operations_actor(db)
    operator = await _operations_actor(db, role=ROLE_OPERATOR)
    hospital = await _hospital(db, "행동 카드 의원")
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="REGENERATE_CONTENT",
        state="FAILED",
        request_payload={},
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    incident = await _incident(db, hospital)
    incident.operation_run_id = run.id
    await db.flush()

    owner_card = (await _overview(db, hospital.id, owner)).exceptions[0]
    operator_card = (await _overview(db, hospital.id, operator)).exceptions[0]

    actions = {action.kind: action for action in owner_card.actions}
    assert {"OPEN_INCIDENT", "RETRY_RUN", "ASSIGN_INCIDENT"} <= set(actions)
    assert (actions["OPEN_INCIDENT"].method, actions["OPEN_INCIDENT"].path) == (
        "GET",
        f"/operations?queue=incidents&hospital_id={hospital.id}&detail=incident:{incident.id}",
    )
    assert actions["RETRY_RUN"].method == "POST"
    assert actions["RETRY_RUN"].requires_idempotency_key is True
    assert actions["RETRY_RUN"].path == (
        f"/api/admin/operations/hospitals/{hospital.id}/runs/{run.id}/retry"
    )
    assert actions["ASSIGN_INCIDENT"].method == "POST"
    assert actions["ASSIGN_INCIDENT"].requires_version is True
    assert actions["ASSIGN_INCIDENT"].path == (
        f"/api/admin/operations/hospitals/{hospital.id}/incidents/{incident.id}/assign"
    )

    # 행동을 실행할 대상을 카드만 보고 알 수 있어야 한다.
    assert owner_card.hospital_id == hospital.id
    assert owner_card.incident_id == incident.id
    assert owner_card.operation_run_id == run.id
    assert owner_card.version == incident.version

    # 담당도 아니고 OWNER도 아닌 운영자에게는 같은 행동이 비활성으로 온다.
    operator_actions = {action.kind: action for action in operator_card.actions}
    assert operator_actions["ASSIGN_INCIDENT"].enabled is False
    assert operator_actions["RETRY_RUN"].enabled is False
    assert operator_card.allowed_actions == ["OPEN_INCIDENT"]
    assert owner_card.allowed_actions == ["OPEN_INCIDENT", "RETRY_RUN", "ASSIGN_INCIDENT"]
