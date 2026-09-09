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
from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy import select

from app.api.admin import essence as essence_api
from app.models.audit import AdminAuditLog
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
from app.services.essence_auto_review import essence_refresh_needed
from app.services.essence_engine import (
    MANDATORY_AVOID_MESSAGES,
    MANDATORY_MEDICAL_AD_RISK_RULES,
    compute_sources_snapshot_hash,
)
from app.services.evidence_noise import compute_evidence_noise_hash


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


async def _approve_as_verified(db, hospital_id, philosophy_id):
    token = set_request_actor("grounding.operator@example.com")
    try:
        return await essence_api.approve_philosophy(
            hospital_id, philosophy_id, _approval(), db=db
        )
    finally:
        reset_request_actor(token)


@pytest.mark.asyncio
async def test_approve_rejects_a_draft_whose_evidence_notes_no_longer_exist(pg_async_session):
    dead_note_id = str(uuid.uuid4())
    hospital, draft, _note = await _seed_draft(
        pg_async_session, mapped_note_ids=[dead_note_id]
    )

    with pytest.raises(HTTPException) as exc:
        await _approve_as_verified(pg_async_session, hospital.id, draft.id)

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
        await _approve_as_verified(pg_async_session, hospital.id, draft.id)

    assert exc.value.status_code == 422
    errors = exc.value.detail["grounding_errors"]
    assert any("local_context" in message and dead_note_id in message for message in errors)


@pytest.mark.asyncio
async def test_approve_succeeds_when_every_reference_resolves(pg_async_session):
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    result = await _approve_as_verified(pg_async_session, hospital.id, draft.id)

    assert result["status"] == PhilosophyStatus.APPROVED.value
    await pg_async_session.refresh(draft)
    assert draft.is_base is True
    # 수동 승인도 그 시점의 제외 집합(여기서는 비어 있음)을 함께 기록한다.
    assert draft.evidence_noise_hash == compute_evidence_noise_hash([])


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
    """활성 계정과 매칭되지 않은 헤더 값은 본문 이름으로 우회할 수 없다."""
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    token = set_request_actor(f"{UNVERIFIED_ACTOR_PREFIX}someone@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id, draft.id, _approval(), db=pg_async_session
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 403
    assert "로그인 계정" in exc.value.detail
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT
    assert draft.reviewed_by is None


@pytest.mark.asyncio
async def test_approve_requires_a_request_actor_instead_of_trusting_the_body(pg_async_session):
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()

    token = set_request_actor(None)
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id, draft.id, _approval(), db=pg_async_session
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 403
    assert "다시 로그인" in exc.value.detail
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT
    assert draft.reviewed_by is None


async def _seed_draft_for_findings(pg_async_session):
    """근거 연결은 통과하고 자동 검수 보류 사유만 남은 초안."""
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    await pg_async_session.commit()
    return hospital, draft, note


def _approve_body(**overrides) -> essence_api.PhilosophyApprove:
    base = dict(
        reviewed_by="reviewer@example.com",
        approval_note=None,
        confirm_evidence_reviewed=True,
    )
    base.update(overrides)
    return essence_api.PhilosophyApprove(**base)


@pytest.mark.asyncio
async def test_manual_approve_refuses_unresolved_auto_review_findings(pg_async_session):
    """H-03: 자동 검수가 보류한 사유를 체크박스 하나로 지나칠 수 없다."""
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id, draft.id, _approve_body(), db=pg_async_session
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AUTO_REVIEW_FINDINGS_UNRESOLVED"
    assert "근거 없는 효과 주장" in exc.value.detail["findings"]
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_manual_approve_with_override_reason_records_the_overridden_findings(
    pg_async_session,
):
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.approve_philosophy(
            hospital.id,
            draft.id,
            _approve_body(
                override_reason=(
                    "원장 인터뷰 원문 2문단에 해당 효과의 근거가 직접 서술되어 있음을 확인함"
                )
            ),
            db=pg_async_session,
        )
    finally:
        reset_request_actor(token)

    assert response["status"] == PhilosophyStatus.APPROVED.value
    audit = (
        await pg_async_session.execute(
            select(AdminAuditLog)
            .where(AdminAuditLog.action == "approve_philosophy")
            .order_by(AdminAuditLog.created_at.desc())
        )
    ).scalars().first()
    assert audit.detail["override_reason"].startswith("원장 인터뷰")
    assert audit.detail["overridden_auto_review_findings"] == ["근거 없는 효과 주장"]


def test_manual_approve_rejects_a_short_override_reason():
    with pytest.raises(ValueError):
        essence_api.PhilosophyApprove(
            reviewed_by="r",
            approval_note=None,
            confirm_evidence_reviewed=True,
            override_reason="짧음",
        )


@pytest.mark.asyncio
async def test_re_review_archives_the_draft_and_dispatches_auto_review(
    pg_async_session, monkeypatch
):
    """H-04: 초안을 보관하고 자료·근거 노트로 다시 합성·검수하는 유일한 경로."""
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    dispatched: list[dict] = []
    monkeypatch.setattr(
        essence_api.auto_review_essence_snapshot,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.request_philosophy_re_review(
            hospital.id, draft.id, db=pg_async_session
        )
    finally:
        reset_request_actor(token)

    assert response["status"] == PhilosophyStatus.ARCHIVED.value
    assert response["re_review_dispatched"] is True
    assert dispatched
    assert dispatched[0]["args"] == [str(hospital.id)]
    assert dispatched[0]["queue"] == "content"
    audit = (
        await pg_async_session.execute(
            select(AdminAuditLog)
            .where(AdminAuditLog.action == "request_philosophy_re_review")
            .order_by(AdminAuditLog.created_at.desc())
        )
    ).scalars().first()
    assert audit.detail["previous_status"] == PhilosophyStatus.DRAFT.value


@pytest.mark.asyncio
async def test_re_review_keeps_the_archive_when_the_broker_is_down(
    pg_async_session, monkeypatch
):
    """보관은 커밋됐는데 dispatch가 500이 되면 재시도는 400('초안 상태만')으로 막힌다."""

    def _broker_down(**_kwargs):
        raise BrokerOperationalError("down")

    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    monkeypatch.setattr(
        essence_api.auto_review_essence_snapshot, "apply_async", _broker_down
    )

    token = set_request_actor("reviewer@example.com")
    try:
        response = await essence_api.request_philosophy_re_review(
            hospital.id, draft.id, db=pg_async_session
        )
    finally:
        reset_request_actor(token)

    assert response["status"] == PhilosophyStatus.ARCHIVED.value
    assert response["re_review_dispatched"] is False
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.ARCHIVED


@pytest.mark.asyncio
async def test_re_review_is_throttled_per_hospital(pg_async_session, monkeypatch):
    """재검수 한 번이 유료 합성을 최대 두 번 부른다 — 전역 비용 가드만으로는 부족하다."""
    hospital, first_draft, note = await _seed_draft_for_findings(pg_async_session)
    second_draft = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=2,
        status=PhilosophyStatus.DRAFT,
        positioning_statement="충분한 설명과 개인별 선택지 안내",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=list(MANDATORY_AVOID_MESSAGES),
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=list(MANDATORY_MEDICAL_AD_RISK_RULES),
        evidence_map={"positioning_statement": [str(note.id)]},
        source_asset_ids=first_draft.source_asset_ids,
        unsupported_gaps=[],
        conflict_notes=[],
        source_snapshot_hash=first_draft.source_snapshot_hash,
    )
    pg_async_session.add(second_draft)
    await pg_async_session.commit()
    monkeypatch.setattr(
        essence_api.auto_review_essence_snapshot, "apply_async", lambda **_kwargs: None
    )

    token = set_request_actor("reviewer@example.com")
    try:
        await essence_api.request_philosophy_re_review(
            hospital.id, first_draft.id, db=pg_async_session
        )
        with pytest.raises(HTTPException) as exc:
            await essence_api.request_philosophy_re_review(
                hospital.id, second_draft.id, db=pg_async_session
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "RE_REVIEW_COOLDOWN"
    assert exc.value.detail["retry_after_seconds"] > 0
    await pg_async_session.refresh(second_draft)
    assert second_draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_archiving_the_only_draft_lets_reconciliation_pick_the_snapshot_back_up(
    pg_async_session,
):
    """dispatch가 유실돼도 15분 reconcile이 회수한다 — 그 선행 조건을 고정한다.

    `essence_refresh_needed`는 동기 `Session`만 받는다. 이 파일의 seed·엔드포인트는
    모두 async라 별도 psycopg2 연결로는 같은 트랜잭션을 볼 수 없으므로,
    같은 세션의 `run_sync`로 동기 등가물을 호출한다.
    """
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)

    assert await pg_async_session.run_sync(essence_refresh_needed, hospital.id) is False

    token = set_request_actor("reviewer@example.com")
    try:
        await essence_api.archive_philosophy(hospital.id, draft.id, db=pg_async_session)
    finally:
        reset_request_actor(token)

    assert await pg_async_session.run_sync(essence_refresh_needed, hospital.id) is True


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


def test_whitespace_only_override_reason_is_no_reason():
    body = essence_api.PhilosophyApprove(
        reviewed_by="r",
        approval_note=None,
        confirm_evidence_reviewed=True,
        override_reason="   ",
    )

    assert body.override_reason is None


@pytest.mark.asyncio
async def test_whitespace_override_reason_does_not_satisfy_the_gate(pg_async_session):
    """공백 20자는 사유가 아니다 — min_length를 채워도 게이트는 그대로 걸린다."""
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id,
                draft.id,
                _approve_body(override_reason=" " * 20),
                db=pg_async_session,
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AUTO_REVIEW_FINDINGS_UNRESOLVED"
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_changed_snapshot_still_blocks_an_overridden_approval(pg_async_session):
    """예외 승인 사유가 있어도 자료가 바뀐 초안은 승인되지 않는다."""
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    draft.source_snapshot_hash = "stale"
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id,
                draft.id,
                _approve_body(
                    override_reason=(
                        "원장 인터뷰 원문 2문단에 해당 효과의 근거가 직접 서술되어 있음을 확인함"
                    )
                ),
                db=pg_async_session,
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert "처리된 병원 자료가 변경되었습니다" in exc.value.detail
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_approve_rejects_a_draft_declaring_sources_outside_the_required_set(
    pg_async_session,
):
    """M-01 후속: snapshot hash가 같아도 초안이 선언한 자료 집합이 다르면 승인하지 않는다.

    hash는 지금 필수인 자료만 요약하므로, 초안이 본문 없는 URL 전용 자료까지 근거로
    선언해 두면 hash 비교만으로는 그 차이를 볼 수 없다.
    """
    hospital, draft, note = await _seed_draft(pg_async_session, mapped_note_ids=[])
    draft.evidence_map = {"positioning_statement": [str(note.id)]}
    url_only = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="홈페이지",
        url="https://clinic.example.com",
        raw_text=None,
        content_hash=f"{uuid.uuid4().hex[:8]}-url-only",
        status=SourceStatus.PENDING,
    )
    pg_async_session.add(url_only)
    draft.source_asset_ids = [*(draft.source_asset_ids or []), str(url_only.id)]
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _approve_as_verified(pg_async_session, hospital.id, draft.id)

    assert exc.value.status_code == 409
    assert "자료 집합이 다릅니다" in exc.value.detail
    await pg_async_session.refresh(draft)
    assert draft.status == PhilosophyStatus.DRAFT


@pytest.mark.asyncio
async def test_patch_cannot_erase_automatic_review_findings(pg_async_session):
    """H-03: PATCH로 finding을 비우고 승인하는 우회를 막는다."""
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        await essence_api.patch_philosophy(
            hospital.id,
            draft.id,
            essence_api.PhilosophyPatch(unsupported_gaps=[]),
            db=pg_async_session,
        )
        await pg_async_session.refresh(draft)
        assert [gap["reason"] for gap in draft.unsupported_gaps] == ["근거 없는 효과 주장"]
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital.id, draft.id, _approve_body(), db=pg_async_session
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "AUTO_REVIEW_FINDINGS_UNRESOLVED"


@pytest.mark.asyncio
async def test_patch_keeps_operator_gaps_alongside_server_findings(pg_async_session):
    hospital, draft, _note = await _seed_draft_for_findings(pg_async_session)
    draft.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "근거 없는 효과 주장"}
    ]
    await pg_async_session.commit()

    token = set_request_actor("reviewer@example.com")
    try:
        await essence_api.patch_philosophy(
            hospital.id,
            draft.id,
            essence_api.PhilosophyPatch(
                unsupported_gaps=[{"field": "positioning_statement", "reason": "근거 부족"}]
            ),
            db=pg_async_session,
        )
    finally:
        reset_request_actor(token)

    await pg_async_session.refresh(draft)
    assert {(gap["field"], gap["reason"]) for gap in draft.unsupported_gaps} == {
        ("positioning_statement", "근거 부족"),
        ("automatic_ai_review", "근거 없는 효과 주장"),
    }
