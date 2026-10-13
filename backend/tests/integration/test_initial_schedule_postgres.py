"""첫 콘텐츠 일정 자동 생성 — 실 Postgres(잠금·유일 인덱스·감사 트리거)."""

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule
from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.initial_schedule import (
    INITIAL_SCHEDULE_ACTOR,
    ensure_initial_content_schedule,
)

TODAY = date(2026, 9, 30)  # 내일(10/1)부터 PLAN_12를 엄격 배치할 수 있는 날


@pytest.fixture
def pg_session(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _seed(pg_session, *, status=HospitalStatus.ACTIVE, plan=Plan.PLAN_12, approved=True):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="첫일정 자동생성 병원",
        slug=f"initial-schedule-{uuid.uuid4().hex[:8]}",
        status=status,
        site_live=status == HospitalStatus.ACTIVE,
        site_built=True,
        profile_complete=True,
        plan=plan,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명합니다.",
        content_hash=f"initial-{uuid.uuid4().hex[:8]}",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분한 설명",
        source_excerpt="진료 전에 충분히 설명합니다.",
        confidence=0.95,
        note_metadata={},
    )
    rows = [hospital, source, note]
    if approved:
        rows.append(
            HospitalContentPhilosophy(
                id=uuid.uuid4(),
                hospital_id=hospital.id,
                version=1,
                status=PhilosophyStatus.APPROVED,
                is_base=True,
                approved_at=datetime.now(timezone.utc),
                content_principles=[], tone_guidelines=[], must_use_messages=[],
                avoid_messages=[], treatment_narratives=[], local_context={},
                medical_ad_risk_rules=[], evidence_map={}, source_asset_ids=[str(source.id)],
                unsupported_gaps=[], conflict_notes=[],
                source_snapshot_hash=compute_sources_snapshot_hash([source]),
            )
        )
    pg_session.add_all(rows)
    pg_session.flush()
    return hospital


def _audit_rows(pg_session, hospital_id):
    return pg_session.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.hospital_id == hospital_id, AdminAuditLog.action == "set_schedule"
        )
    ).scalars().all()


def test_creates_the_contract_schedule_slots_and_audit_once(pg_session) -> None:
    hospital = _seed(pg_session)

    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=TODAY, trigger="essence_auto_approval"
    )
    pg_session.commit()

    assert outcome.created is True and outcome.reason == "CREATED"
    assert outcome.active_from == date(2026, 10, 1)
    schedule = pg_session.get(ContentSchedule, outcome.schedule_id)
    assert schedule.plan == "PLAN_12" and schedule.publish_days == [0, 2, 4]
    assert schedule.is_active is True
    slots = pg_session.scalar(
        select(func.count()).select_from(ContentItem).where(ContentItem.schedule_id == schedule.id)
    )
    assert slots == 12 == outcome.slots_created
    assert pg_session.get(Hospital, hospital.id).schedule_set is True
    [audit] = _audit_rows(pg_session, hospital.id)
    assert audit.actor == INITIAL_SCHEDULE_ACTOR == "system:essence-auto-approval"
    assert audit.target_type == "content_schedule" and audit.target_id == str(schedule.id)
    assert {
        "plan", "publish_days", "active_from", "slots_created", "slots_rescheduled",
        "future_contract_preserved", "old_schedule_ids", "previous_status", "new_status",
        "trigger", "contract_effective_on",
    } <= set(audit.detail)

    again = ensure_initial_content_schedule(
        pg_session, hospital.id, today=TODAY, trigger="reconcile_sweep"
    )
    assert again.created is False and again.reason == "ALREADY_SET"
    assert len(_audit_rows(pg_session, hospital.id)) == 1


@pytest.mark.parametrize(
    ("seed_kwargs", "reason"),
    [
        ({"status": HospitalStatus.PAUSED}, "STATUS_NOT_ELIGIBLE"),
        ({"status": HospitalStatus.ONBOARDING}, "STATUS_NOT_ELIGIBLE"),
        ({"plan": None}, "PLAN_MISSING"),
        ({"approved": False}, "ESSENCE_NOT_CURRENT"),
    ],
)
def test_does_nothing_when_a_precondition_is_missing(pg_session, seed_kwargs, reason) -> None:
    hospital = _seed(pg_session, **seed_kwargs)

    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=TODAY, trigger="reconcile_sweep"
    )

    assert outcome.created is False and outcome.reason == reason
    assert pg_session.get(Hospital, hospital.id).schedule_set in (False, None)
    assert _audit_rows(pg_session, hospital.id) == []


def test_never_revives_a_schedule_a_person_turned_off(pg_session) -> None:
    hospital = _seed(pg_session)
    pg_session.add(
        ContentSchedule(
            hospital_id=hospital.id, plan="PLAN_12", publish_days=[0],
            active_from=date(2026, 8, 1), is_active=False,
        )
    )
    pg_session.flush()

    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=TODAY, trigger="reconcile_sweep"
    )

    assert outcome.reason == "SCHEDULE_EXISTS"


# ── 첫 계약 월 안분(D3) ─────────────────────────────────────────────────────

MID_MONTH = date(2026, 10, 11)  # 내일(10/12)부터 10월의 남은 월·수·금은 9일, 전체는 13일


def _month_rows(pg_session, hospital_id, start: date, end: date):
    return pg_session.execute(
        select(ContentItem.scheduled_date, ContentItem.total_count, ContentItem.sequence_no)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= start,
            ContentItem.scheduled_date <= end,
        )
        .order_by(ContentItem.sequence_no)
    ).all()


def test_mid_month_approval_starts_tomorrow_with_a_prorated_first_month(pg_session) -> None:
    import arrow

    from app.services.content_month_target import month_content_target_sync
    from app.workers.monthly_slots import create_next_month_slots_for_schedule

    hospital = _seed(pg_session)

    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=MID_MONTH, trigger="essence_auto_approval"
    )

    assert outcome.created is True
    assert outcome.active_from == date(2026, 10, 12)
    rows = _month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31))
    # ceil(9 / 13 × 12) = 9편이 이번 달 남은 주에만 놓이고, 슬롯이 그 약속 편수를 기록한다.
    assert outcome.slots_created == len(rows) == 9
    assert all(row.scheduled_date >= date(2026, 10, 12) for row in rows)
    assert {row.total_count for row in rows} == {9}
    [audit] = _audit_rows(pg_session, hospital.id)
    assert audit.detail["first_month_target"] == 9

    october = arrow.get(date(2026, 10, 1))
    november = arrow.get(date(2026, 11, 1))
    assert month_content_target_sync(pg_session, hospital.id, october, "PLAN_12") == 9
    assert month_content_target_sync(pg_session, hospital.id, november, "PLAN_12") == 12

    # 매일 도는 당월 조정(`monthly_slot_generation(current_month=True)`)과 같은 호출이
    # 안분된 첫 달을 요금제 편수로 다시 채우지 않는다.
    schedule = pg_session.get(ContentSchedule, outcome.schedule_id)
    assert (
        create_next_month_slots_for_schedule(
            pg_session,
            schedule,
            october,
            october.date(),
            october.ceil("month").date(),
            not_before=date(2026, 10, 13),
        )
        is False
    )
    assert len(_month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31))) == 9

    # 다음 달은 월말 배치가 요금제 편수 그대로 만든다.
    assert create_next_month_slots_for_schedule(
        pg_session, schedule, november, november.date(), november.ceil("month").date()
    )
    november_rows = _month_rows(pg_session, hospital.id, date(2026, 11, 1), date(2026, 11, 30))
    assert len(november_rows) == 12 and {row.total_count for row in november_rows} == {12}


def test_a_human_mid_month_first_schedule_keeps_the_full_quota(pg_session) -> None:
    import arrow

    from app.services.content_month_target import month_content_target_sync

    hospital = _seed(pg_session)
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[0, 1, 2, 3, 4, 5, 6],
        active_from=date(2026, 10, 12), is_active=True,
    )
    pg_session.add(schedule)
    pg_session.flush()
    for seq in range(1, 13):
        pg_session.add(
            ContentItem(
                hospital_id=hospital.id, schedule_id=schedule.id, content_type="FAQ",
                sequence_no=seq, total_count=12, scheduled_date=date(2026, 10, 12 + seq),
            )
        )
    pg_session.flush()

    # 사람이 만든 첫 달은 슬롯이 요금제 편수를 기록하므로 약속 편수가 소급해 줄지 않는다.
    assert (
        month_content_target_sync(pg_session, hospital.id, arrow.get(date(2026, 10, 1)), "PLAN_12")
        == 12
    )


def test_no_publish_weekday_left_creates_no_slots_this_month_and_full_next_month(
    pg_session,
) -> None:
    import arrow

    from app.workers.monthly_slots import create_next_month_slots_for_schedule

    hospital = _seed(pg_session, plan=Plan.PLAN_20)

    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=date(2026, 10, 30), trigger="essence_auto_approval"
    )

    assert outcome.created is True and outcome.slots_created == 0
    assert outcome.active_from == date(2026, 10, 31)
    assert pg_session.get(Hospital, hospital.id).schedule_set is True
    [audit] = _audit_rows(pg_session, hospital.id)
    assert audit.detail["first_month_target"] == 0
    schedule = pg_session.get(ContentSchedule, outcome.schedule_id)
    october = arrow.get(date(2026, 10, 1))
    # 매일 당월 조정도 10/31 하루에 20편을 몰아 넣지 않는다.
    assert (
        create_next_month_slots_for_schedule(
            pg_session, schedule, october, october.date(), october.ceil("month").date(),
            not_before=date(2026, 10, 31),
        )
        is False
    )
    assert _month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31)) == []
    november = arrow.get(date(2026, 11, 1))
    assert create_next_month_slots_for_schedule(
        pg_session, schedule, november, november.date(), november.ceil("month").date()
    )
    assert len(_month_rows(pg_session, hospital.id, date(2026, 11, 1), date(2026, 11, 30))) == 20


class _AsyncSessionShim:
    """현황 카드(async)를 같은 롤백 트랜잭션의 동기 세션으로 읽는다."""

    def __init__(self, session):
        self._session = session

    async def execute(self, stmt):
        return self._session.execute(stmt)


async def test_a_first_month_without_publish_days_promises_zero_everywhere(
    pg_session, monkeypatch
) -> None:
    """10월 약속 0편을 계약 이행 집계·현황 카드·월간 보고서가 같이 읽는다(요금제 20편이 아니다)."""
    from unittest.mock import AsyncMock

    import arrow

    from app.api.admin import hospital_overview as overview
    from app.services.content_month_target import month_content_target_sync
    from app.services.contract_delivery_coverage import collect_contract_delivery_coverage
    from app.services.monthly_content_operations import build_monthly_content_operations_snapshot

    hospital = _seed(pg_session, plan=Plan.PLAN_20)
    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=date(2026, 10, 30), trigger="essence_auto_approval"
    )
    assert outcome.active_from == date(2026, 10, 31) and outcome.slots_created == 0
    october = arrow.get(date(2026, 10, 1))
    observed_at = datetime(2026, 10, 31, 3, tzinfo=timezone.utc)  # 10/31 12:00 KST

    # 월간 보고서의 약속 편수(`period_quota`)와 그 운영 스냅샷.
    period_quota = month_content_target_sync(pg_session, hospital.id, october, "PLAN_20")
    assert period_quota == 0
    snapshot = build_monthly_content_operations_snapshot(
        plan="PLAN_20",
        contract_quota=period_quota,
        scheduled_items=[],
        published_items=[],
        cutoff_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        contract_published_count=0,
    )
    assert snapshot.payload["plan_quota"] == 0 and snapshot.payload["shortfall_count"] == 0
    assert not any("약정 콘텐츠" in warning for warning in snapshot.delivery_warnings)
    # 다음 달은 요금제 편수 그대로다.
    assert (
        month_content_target_sync(pg_session, hospital.id, arrow.get(date(2026, 11, 1)), "PLAN_20")
        == 20
    )

    # 계약 이행 집계: 약속 0편이면 기대·누락도 0이다.
    coverage = collect_contract_delivery_coverage(pg_session, now=observed_at)
    assert coverage.expected == 0
    assert coverage.missing_allocations == 0
    assert coverage.delivery_at_risk is False

    # 현황 카드의 이번 달 약속 편수.
    monkeypatch.setattr(overview.arrow, "now", lambda *args: arrow.get(observed_at))
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))
    summary = await overview._month_summary(
        _AsyncSessionShim(pg_session), pg_session.get(Hospital, hospital.id)
    )
    assert summary.planned_total == 0


class _AsyncWriteShim(_AsyncSessionShim):
    """사람의 일정 교체(async 경로)를 같은 롤백 트랜잭션의 동기 세션으로 돌린다."""

    def add(self, row):
        self._session.add(row)

    async def get(self, model, key):
        return self._session.get(model, key)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.flush()

    async def refresh(self, row):
        self._session.refresh(row)


async def _human_replaces_schedule(pg_session, monkeypatch, hospital, *, today, body):
    from unittest.mock import AsyncMock

    import arrow

    from app.api.admin import content as content_api

    monkeypatch.setattr(content_api, "_schedule_readiness_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        content_api.arrow, "now", lambda *args: arrow.get(today).replace(tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(content_api.regenerate_content_item, "apply_async", lambda **_kw: None)
    return await content_api.set_schedule(hospital.id, body, _AsyncWriteShim(pg_session))


async def test_a_human_weekday_change_keeps_the_prorated_first_month(
    pg_session, monkeypatch
) -> None:
    """자동 일정(PLAN_12, 첫 달 9편) 뒤 사람이 같은 달 요일을 바꿔도 약속은 9편 그대로다."""
    from unittest.mock import AsyncMock

    import arrow

    from app.api.admin import hospital_overview as overview
    from app.api.admin.content import ScheduleCreate
    from app.services.content_month_target import month_content_target_sync
    from app.workers.monthly_slots import create_next_month_slots_for_schedule

    hospital = _seed(pg_session)
    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=MID_MONTH, trigger="essence_auto_approval"
    )
    assert outcome.slots_created == 9

    response = await _human_replaces_schedule(
        pg_session,
        monkeypatch,
        hospital,
        today=MID_MONTH,
        body=ScheduleCreate(plan="PLAN_12", publish_days=[1, 3], active_from=date(2026, 10, 13)),
    )

    assert response["slots_created"] == 0
    october = arrow.get(date(2026, 10, 1))
    rows = _month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31))
    assert len(rows) == 9 and {row.total_count for row in rows} == {9}
    assert month_content_target_sync(pg_session, hospital.id, october, "PLAN_12") == 9

    # 매일 당월 조정도 새 일정으로 요금제 편수까지 채우지 않는다.
    latest = pg_session.execute(
        select(ContentSchedule).where(
            ContentSchedule.hospital_id == hospital.id, ContentSchedule.is_active
        )
    ).scalar_one()
    assert latest.id != outcome.schedule_id
    assert (
        create_next_month_slots_for_schedule(
            pg_session, latest, october, october.date(), october.ceil("month").date(),
            not_before=date(2026, 10, 13),
        )
        is False
    )
    assert len(_month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31))) == 9

    monkeypatch.setattr(
        overview.arrow, "now", lambda *args: arrow.get(datetime(2026, 10, 13, 3, tzinfo=timezone.utc))
    )
    monkeypatch.setattr(overview, "weekly_mention_trend", AsyncMock(return_value=[]))
    summary = await overview._month_summary(
        _AsyncSessionShim(pg_session), pg_session.get(Hospital, hospital.id)
    )
    assert summary.planned_total == 9


async def test_a_human_edit_of_a_zero_slot_first_month_stays_zero(
    pg_session, monkeypatch
) -> None:
    """첫 달 0편(게시 요일이 남지 않은 달)을 사람이 다른 요일로 바꿔도 그 달 슬롯은 0편이다."""
    import arrow

    from app.api.admin.content import ScheduleCreate
    from app.services.content_month_target import month_content_target_sync

    hospital = _seed(pg_session, plan=Plan.PLAN_20)
    outcome = ensure_initial_content_schedule(
        pg_session, hospital.id, today=date(2026, 10, 30), trigger="essence_auto_approval"
    )
    assert outcome.active_from == date(2026, 10, 31) and outcome.slots_created == 0

    # 10/31(토)이 게시 요일이 되도록 바꿔도 10월 약속은 늘지 않는다.
    response = await _human_replaces_schedule(
        pg_session,
        monkeypatch,
        hospital,
        today=date(2026, 10, 30),
        body=ScheduleCreate(
            plan="PLAN_20", publish_days=[0, 1, 2, 3, 5], active_from=date(2026, 10, 31)
        ),
    )

    assert response["slots_created"] == 0
    assert _month_rows(pg_session, hospital.id, date(2026, 10, 1), date(2026, 10, 31)) == []
    october = arrow.get(date(2026, 10, 1))
    assert month_content_target_sync(pg_session, hospital.id, october, "PLAN_20") == 0
