"""C-1: 화면에서 확인할 수 없었던 근거 연결은 승인도 통과하지 못한다.

운영 화면은 초안의 evidence_map 항목을 전부 그린다. 자료를 다시 처리하면 옛 근거
노트는 삭제되고 새 id가 생기므로, 그 사이에 만들어진 초안은 화면에서 모든 항목이
"근거 노트를 찾지 못했습니다"로 보인다. 서버가 그 초안을 승인해 주면 클라이언트의
잠금은 우회 가능한 장식일 뿐이다.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.admin import essence as essence_api
from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.audit_log import (
    UNVERIFIED_ACTOR_PREFIX,
    reset_request_actor,
    set_request_actor,
)
from app.services.essence_engine import (
    MANDATORY_AVOID_MESSAGES,
    MANDATORY_MEDICAL_AD_RISK_RULES,
    compute_sources_snapshot_hash,
)


async def _seed_draft(db, *, mapped_note_ids: list[str]):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"근거검증 병원 {label}",
        slug=f"grounding-{label}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash=f"{label}-source-hash",
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    note = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="충분한 설명을 중시한다.",
        source_excerpt="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        confidence=0.95,
        note_metadata={},
    )
    draft = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.DRAFT,
        positioning_statement="충분한 설명과 개인별 선택지 안내",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        # 플랫폼 공통 의료광고 규칙은 승인 게이트의 별도 조건이다. 이 파일이 검증하려는
        # 것은 근거 연결이므로, 그 조건은 채워 두고 근거 쪽만 달리한다.
        avoid_messages=list(MANDATORY_AVOID_MESSAGES),
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=list(MANDATORY_MEDICAL_AD_RISK_RULES),
        evidence_map={"positioning_statement": mapped_note_ids},
        source_asset_ids=[str(source.id)],
        unsupported_gaps=[],
        conflict_notes=[],
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
    )
    db.add_all([hospital, source, note, draft])
    await db.commit()
    return hospital, draft, note


def _approval() -> essence_api.PhilosophyApprove:
    return essence_api.PhilosophyApprove(
        reviewed_by="MotionLabs",
        approval_note=None,
        confirm_evidence_reviewed=True,
    )


@pytest.mark.asyncio
async def test_approve_rejects_a_draft_whose_evidence_notes_no_longer_exist(pg_async_session):
    dead_note_id = str(uuid.uuid4())
    hospital, draft, _note = await _seed_draft(
        pg_async_session, mapped_note_ids=[dead_note_id]
    )

    with pytest.raises(HTTPException) as exc:
        await essence_api.approve_philosophy(
            hospital.id, draft.id, _approval(), db=pg_async_session
        )

    assert exc.value.status_code == 422
    errors = exc.value.detail["grounding_errors"]
    assert any(dead_note_id in message for message in errors)
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_approve_rejects_orphan_references_even_on_an_empty_field(pg_async_session):
    """비어 있는 필드에 남은 죽은 참조도 화면에는 확인 불가 항목으로 보인다."""
    dead_note_id = str(uuid.uuid4())
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {
        "positioning_statement": [str(note.id)],
        "local_context": [dead_note_id],
    }
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await essence_api.approve_philosophy(
            hospital.id, draft.id, _approval(), db=pg_async_session
        )

    assert exc.value.status_code == 422
    errors = exc.value.detail["grounding_errors"]
    assert any("local_context" in message and dead_note_id in message for message in errors)


@pytest.mark.asyncio
async def test_approve_succeeds_when_every_reference_resolves(pg_async_session):
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    result = await essence_api.approve_philosophy(
        hospital.id, draft.id, _approval(), db=pg_async_session
    )

    assert result["status"] == PhilosophyStatus.APPROVED.value


@pytest.mark.asyncio
async def test_the_recorded_reviewer_is_the_verified_account_not_the_request_body(
    pg_async_session,
):
    """C-3: 승인 기록은 실제로 승인한 계정을 가리켜야 한다.

    화면이 검토자 칸을 'MotionLabs'로 채워 보내던 동안, 승인 기록은 누가 눌렀는지
    말하지 못했다. 요청자 계정이 확인되면 그 계정을 남긴다.
    """
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    token = set_request_actor("operator.owner@example.com")
    try:
        result = await essence_api.approve_philosophy(
            hospital.id, draft.id, _approval(), db=pg_async_session
        )
    finally:
        reset_request_actor(token)

    assert result["status"] == PhilosophyStatus.APPROVED.value
    await pg_async_session.refresh(draft)
    assert draft.reviewed_by == "operator.owner@example.com"


@pytest.mark.asyncio
async def test_an_unverified_actor_never_becomes_the_recorded_reviewer(pg_async_session):
    """활성 계정과 매칭되지 않은 헤더 값은 승인자로 남기지 않는다."""
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    token = set_request_actor(f"{UNVERIFIED_ACTOR_PREFIX}someone@example.com")
    try:
        await essence_api.approve_philosophy(
            hospital.id, draft.id, _approval(), db=pg_async_session
        )
    finally:
        reset_request_actor(token)

    await pg_async_session.refresh(draft)
    assert not draft.reviewed_by.startswith(UNVERIFIED_ACTOR_PREFIX)
    assert draft.reviewed_by == "MotionLabs"
