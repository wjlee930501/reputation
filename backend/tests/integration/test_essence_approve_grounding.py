"""Phase 4(D33): 사람 승인 경로는 없다 — 남은 초안은 보관만 할 수 있고 자동 요약을 막지 않는다.

승인·재검수·판 직접 수정 라우트는 410이다. 옛 보류 초안이 남아 있어도 적용 중인 판이 없는
병원은 정기 조정이 곧바로 자동 요약을 만든다(초안이 그 병원을 사람의 일로 묶지 않는다).
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
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.essence_auto_review import essence_refresh_needed
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


async def _seed_draft_for_findings(pg_async_session):
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    draft.unsupported_gaps = [{"field": "automatic_ai_review", "reason": "근거 부족"}]
    await pg_async_session.commit()
    return hospital, draft, note


@pytest.mark.asyncio
async def test_a_leftover_review_draft_never_holds_the_automatic_essence(pg_async_session):
    """`essence_refresh_needed`는 동기 `Session`만 받으므로 같은 세션의 `run_sync`로 부른다."""

    hospital, _draft, _note = await _seed_draft_for_findings(pg_async_session)

    assert await pg_async_session.run_sync(essence_refresh_needed, hospital.id) is True


@pytest.mark.asyncio
async def test_approve_re_review_and_patch_are_gone_without_touching_the_draft(pg_async_session):
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    token = set_request_actor("reviewer@example.com")
    try:
        for call in (
            essence_api.approve_philosophy(hospital.id, draft.id),
            essence_api.request_philosophy_re_review(hospital.id, draft.id),
            essence_api.patch_philosophy(hospital.id, draft.id),
        ):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 410
            assert exc.value.detail["code"] == "ESSENCE_APPROVAL_RETIRED"
    finally:
        reset_request_actor(token)

    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT
    assert draft.approved_at is None


@pytest.mark.asyncio
async def test_archive_marks_the_draft_archived(pg_async_session):
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.archive_philosophy(
            hospital.id, draft.id, db=pg_async_session
        )
    finally:
        reset_request_actor(token)

    assert response["status"] == PhilosophyStatus.ARCHIVED.value
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.ARCHIVED


@pytest.mark.asyncio
async def test_archive_only_accepts_drafts(pg_async_session):
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.status = PhilosophyStatus.APPROVED
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await essence_api.archive_philosophy(hospital.id, draft.id, db=pg_async_session)

    assert exc.value.status_code == 400
