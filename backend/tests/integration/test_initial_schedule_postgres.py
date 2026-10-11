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
