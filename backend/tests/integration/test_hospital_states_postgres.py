"""3상태의 콘텐츠 준비 묶음 조회 — 실제 SQL로 판정과 쿼리 예산을 함께 확인한다.

병원 목록·헤더·현황이 병원마다 기준을 조회하면 화면 하나가 병원 수에 비례하는 쿼리를 낸다.
JSONB 노트 metadata·부분 인덱스·enum 비교는 모의 세션으로 검증할 수 없다.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import get_essence_readiness_states
from app.services.evidence_noise import compute_evidence_noise_hash
from app.services.hospital_states import content_state

pytestmark = pytest.mark.asyncio

# 승인 행 1 + 필수 자료 1 + 노이즈 제외 노트 1 + 예외 초안 1. 병원 수와 무관해야 한다.
_READINESS_STATEMENT_BUDGET = 4


async def _source(db, hospital: Hospital, *, status: SourceStatus = SourceStatus.PROCESSED):
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{hospital.name} 홈페이지",
        raw_text="원장이 직접 설명한 진료 원칙 본문",
        content_hash=f"hash-{uuid.uuid4().hex[:12]}",
        status=status,
        processed_at=datetime.now(UTC) if status == SourceStatus.PROCESSED else None,
    )
    db.add(source)
    await db.flush()
    return source


async def _hospital(
    db,
    name: str,
    *,
    unprocessed_source: bool = False,
    escalated_draft: bool = False,
    excluded_note: bool = False,
) -> Hospital:
    hospital = Hospital(
        name=name,
        slug=f"clinic-{uuid.uuid4().hex[:12]}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
        site_built=True,
        profile_complete=True,
        schedule_set=True,
    )
    db.add(hospital)
    await db.flush()
    source = await _source(db, hospital)
    if unprocessed_source:
        await _source(db, hospital, status=SourceStatus.PENDING)
    if excluded_note:
        # 승인 이후 운영자가 근거에서 뺀 노트 — 승인이 기록한 빈 집합과 달라 current가 막힌다.
        db.add(
            HospitalSourceEvidenceNote(
                hospital_id=hospital.id,
                source_asset_id=source.id,
                note_type=EvidenceNoteType.KEY_MESSAGE,
                claim="충분히 설명하고 진료합니다.",
                source_excerpt="원장이 직접 설명한 진료 원칙 본문",
                note_metadata={"is_noise": True},
            )
        )
    db.add(
        HospitalContentPhilosophy(
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
    )
    if escalated_draft:
        db.add(
            HospitalContentPhilosophy(
                hospital_id=hospital.id,
                version=2,
                status=PhilosophyStatus.DRAFT,
                positioning_statement=f"{name} 초안",
                unsupported_gaps=[
                    {"field": AUTO_REVIEW_GAP_FIELD, "reason": "근거 없는 효과 표현"}
                ],
            )
        )
    await db.flush()
    return hospital


async def _states_query_count(db, hospital_ids) -> int:
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await get_essence_readiness_states(db, hospital_ids)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_readiness_states_query_count_is_constant_across_hospitals(pg_async_session):
    db = pg_async_session
    first = await _hospital(db, "쿼리예산 첫 의원")

    one_count = await _states_query_count(db, [first.id])

    many = [first.id]
    for index in range(11):
        extra = await _hospital(db, f"쿼리예산 {index} 의원")
        many.append(extra.id)
    many_count = await _states_query_count(db, many)

    assert one_count == _READINESS_STATEMENT_BUDGET
    assert many_count == one_count


async def test_readiness_states_answer_each_hospital_from_its_own_rows(pg_async_session):
    db = pg_async_session
    ready = await _hospital(db, "준비된 의원")
    waiting = await _hospital(db, "자료 처리 중 의원", unprocessed_source=True)
    escalated = await _hospital(db, "예외 초안 의원", escalated_draft=True)
    noisy = await _hospital(db, "근거 제외 의원", excluded_note=True)

    states = await get_essence_readiness_states(
        db, [ready.id, waiting.id, escalated.id, noisy.id]
    )

    assert states[ready.id].current is True
    assert states[ready.id].unprocessed_sources == 0
    assert states[ready.id].escalated_draft is False
    assert states[waiting.id].current is False
    assert states[waiting.id].unprocessed_sources == 1
    assert states[escalated.id].escalated_draft is True
    # 승인이 기록한 제외 집합과 지금 집합이 다르면 새 글의 근거가 될 수 없다(H-02).
    assert states[noisy.id].current is False


async def test_content_state_reads_the_batched_rows(pg_async_session):
    """3상태 판정 함수가 이 묶음 결과를 그대로 받는다 — 화면은 라벨만 붙인다."""
    db = pg_async_session
    ready = await _hospital(db, "발행 자동 의원")
    escalated = await _hospital(db, "예외 대기 의원", escalated_draft=True)
    states = await get_essence_readiness_states(db, [ready.id, escalated.id])

    def _state(hospital: Hospital):
        state = states[hospital.id]
        return content_state(
            hospital,
            essence_current=state.current,
            unprocessed_sources=state.unprocessed_sources,
            escalated_draft=state.escalated_draft,
        )

    assert _state(ready).kind == "auto"
    assert _state(escalated).kind == "exception"
