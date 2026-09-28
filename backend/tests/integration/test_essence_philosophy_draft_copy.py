"""승인된 운영 기준을 사람이 고칠 초안으로 복사한다.

승인본은 수정할 수 없고 초안은 워커의 자동 재합성만 만들었다. 사람이 승인본의 문장
하나를 고치려면 승인본을 그대로 옮긴 초안이 필요하다. 이 파일은 그 복사가
- 내용·근거·자료 snapshot을 그대로 옮기고 승인본을 건드리지 않는지,
- 병원당 초안이 둘 생기지 않는지(실제 동시 커밋 포함),
- 자료가 승인본의 snapshot과 어긋나 승인이 거절할 사본을 만들지 않는지,
- 합성·검수·큐 호출 없이 끝나는지,
- 복사와 문장 수정이 감사 기록에 남는지,
- 복사 → 문장 수정 → 기존 승인 경로로 새 판이 되는지를 고정한다.
"""

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from celery.app.task import Task
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.admin import essence as essence_api
from app.models.audit import AdminAuditLog
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
from app.services import essence_auto_review, essence_engine, openrouter
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
from app.services.knowledge_changes import AUTHORITY_CHANGE_FIELD

OPERATOR = "copy.operator@example.com"
PREVIOUS_APPROVER = "first.approver@example.com"
COPY_ACTION = "copy_philosophy_to_draft"
PATCH_ACTION = "patch_philosophy"

# 복사하지 않는 식별자·생애주기·승인 메타데이터. 모델에 컬럼이 늘면 복사 목록이나
# 이 목록 중 한쪽에 명시적으로 넣어야 이 파일이 통과한다.
NOT_COPIED_COLUMNS = {
    "id",
    "hospital_id",
    "version",
    "status",
    "is_base",
    "created_by",
    "reviewed_by",
    "approved_at",
    "approval_note",
    "evidence_noise_hash",
    "created_at",
    "updated_at",
}


def _seed_objects(*, extra_gaps: list[dict] | None = None):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"초안복사 병원 {label}",
        slug=f"draft-copy-{label}",
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
    note_id = str(note.id)
    approved = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=3,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        positioning_statement="충분한 설명과 개인별 선택지 안내",
        doctor_voice="차분하게 설명하는 원장",
        patient_promise="진료 전 충분한 설명",
        content_principles=["환자마다 다른 선택지를 안내한다"],
        tone_guidelines=["단정하지 않는다"],
        must_use_messages=["진료 전에 충분히 설명합니다", "환자마다 선택지가 다릅니다"],
        avoid_messages=list(MANDATORY_AVOID_MESSAGES),
        treatment_narratives=[{"treatment": "상담", "narrative": "충분한 설명"}],
        local_context={},
        medical_ad_risk_rules=list(MANDATORY_MEDICAL_AD_RISK_RULES),
        evidence_map={
            "positioning_statement": [note_id],
            "doctor_voice": [note_id],
            "patient_promise": [note_id],
            "content_principles": [note_id],
            "tone_guidelines": [note_id],
            "must_use_messages": [[note_id], [note_id]],
            "treatment_narratives": [note_id],
        },
        source_asset_ids=[str(source.id)],
        unsupported_gaps=[
            {"field": "local_context", "reason": "지역 근거 부족"},
            *(extra_gaps or []),
        ],
        conflict_notes=[{"note": "상충 없음"}],
        synthesis_notes="합성 메모",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash="a" * 64,
        created_by="system:essence-auto",
        reviewed_by=PREVIOUS_APPROVER,
        approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        approval_note="첫 승인",
    )
    # 과거 판들이 남아 있어도 새 초안은 병원 최대 version 다음이다.
    archived = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=4,
        status=PhilosophyStatus.ARCHIVED,
        is_base=False,
        positioning_statement="보관된 옛 초안",
        source_asset_ids=[str(source.id)],
    )
    return hospital, source, note, approved, archived


async def _seed(db, *, extra_gaps: list[dict] | None = None):
    hospital, source, note, approved, archived = _seed_objects(extra_gaps=extra_gaps)
    db.add_all([hospital, source, note, approved, archived])
    await db.commit()
    return hospital, note, approved, archived


async def _column_snapshot(db, model, row_id) -> dict:
    table = model.__table__
    row = (await db.execute(select(table).where(table.c.id == row_id))).mappings().one()
    return dict(row)


async def _copy_as(db, hospital_id, philosophy_id, actor: str | None = OPERATOR):
    token = set_request_actor(actor)
    try:
        return await essence_api.copy_approved_philosophy_to_draft(
            hospital_id, philosophy_id, db=db
        )
    finally:
        reset_request_actor(token)


async def _philosophies(db, hospital_id) -> list[HospitalContentPhilosophy]:
    result = await db.execute(
        select(HospitalContentPhilosophy)
        .where(HospitalContentPhilosophy.hospital_id == hospital_id)
        .order_by(HospitalContentPhilosophy.version)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _audit_rows(db, hospital_id, action: str) -> list[AdminAuditLog]:
    result = await db.execute(
        select(AdminAuditLog)
        .where(AdminAuditLog.hospital_id == hospital_id, AdminAuditLog.action == action)
        .order_by(AdminAuditLog.created_at)
    )
    return list(result.scalars().all())


def _record_llm_and_queue_calls(monkeypatch) -> list[str]:
    """합성·검수·공급자 클라이언트와 큐 등록 진입점을 모두 기록기로 바꾼다."""

    calls: list[str] = []

    def recorder(name: str):
        def _record(*_args, **_kwargs):
            calls.append(name)

        return _record

    async def _record_async(*_args, **_kwargs):
        calls.append("recover_ops_incident")

    targets = [
        (openrouter, "sync_client"),
        (openrouter, "async_client"),
        (openrouter, "generate_image"),
        (essence_engine, "_llm_client"),
        (essence_engine, "_call_llm_json"),
        (essence_engine, "synthesize_philosophy"),
        (essence_engine, "process_source_asset"),
        (essence_auto_review, "review_essence_candidate"),
        (essence_auto_review, "refresh_essence_snapshot"),
    ]
    # essence_auto_review가 이름을 직접 import했다면 그 사본도 막는다.
    for name in ("synthesize_philosophy", "_call_llm_json", "_llm_client"):
        if hasattr(essence_auto_review, name):
            targets.append((essence_auto_review, name))
    for module, name in targets:
        monkeypatch.setattr(module, name, recorder(f"{module.__name__}.{name}"))

    monkeypatch.setattr(
        essence_api.auto_review_essence_snapshot, "apply_async", recorder("apply_async")
    )
    monkeypatch.setattr(essence_api.auto_review_essence_snapshot, "delay", recorder("delay"))
    monkeypatch.setattr(Task, "apply_async", recorder("Task.apply_async"))
    monkeypatch.setattr(essence_api.celery_app, "send_task", recorder("send_task"))
    monkeypatch.setattr(essence_api, "recover_ops_incident", _record_async)
    return calls


# ── 복사 목록 ───────────────────────────────────────────────────────


def test_every_philosophy_column_is_either_copied_or_explicitly_skipped():
    columns = {column.name for column in HospitalContentPhilosophy.__table__.columns}
    copied = set(essence_api.PHILOSOPHY_DRAFT_COPY_FIELDS)

    assert copied.isdisjoint(NOT_COPIED_COLUMNS)
    assert copied | NOT_COPIED_COLUMNS == columns


# ── 복사 성공 ───────────────────────────────────────────────────────


async def test_copy_creates_an_identical_human_draft_and_leaves_the_approved_row_untouched(
    pg_async_session, monkeypatch
):
    calls = _record_llm_and_queue_calls(monkeypatch)
    # 서버 소유 finding도 그대로 옮긴다 — 복사는 gap을 걸러 내지 않는다.
    server_gap = {"field": AUTO_REVIEW_GAP_FIELD, "reason": "근거 없는 효과 주장"}
    hospital, _note, approved, _archived = await _seed(
        pg_async_session, extra_gaps=[server_gap]
    )
    approved_before = await _column_snapshot(
        pg_async_session, HospitalContentPhilosophy, approved.id
    )
    hospital_before = await _column_snapshot(pg_async_session, Hospital, hospital.id)

    response = await _copy_as(pg_async_session, hospital.id, approved.id)

    assert response["status"] == PhilosophyStatus.DRAFT
    rows = await _philosophies(pg_async_session, hospital.id)
    assert len(rows) == 3
    draft = next(row for row in rows if row.status == PhilosophyStatus.DRAFT)
    assert response["id"] == str(draft.id)
    assert draft.id != approved.id
    assert draft.version == 5  # 병원 최대 version(보관된 4) + 1
    assert draft.is_base is False
    assert draft.created_by == OPERATOR
    assert draft.reviewed_by is None
    assert draft.approved_at is None
    assert draft.approval_note is None
    assert draft.evidence_noise_hash is None

    draft_values = await _column_snapshot(pg_async_session, HospitalContentPhilosophy, draft.id)
    for field_name in essence_api.PHILOSOPHY_DRAFT_COPY_FIELDS:
        assert draft_values[field_name] == approved_before[field_name], field_name
    assert draft_values["must_use_messages"] == [
        "진료 전에 충분히 설명합니다",
        "환자마다 선택지가 다릅니다",
    ]
    assert draft_values["evidence_map"] == approved_before["evidence_map"]
    assert draft_values["source_snapshot_hash"] == approved_before["source_snapshot_hash"]
    assert draft_values["source_asset_ids"] == approved_before["source_asset_ids"]
    assert server_gap in draft_values["unsupported_gaps"]

    # 승인본은 updated_at까지 그대로다. 병원 행도 바뀌지 않는다.
    assert (
        await _column_snapshot(pg_async_session, HospitalContentPhilosophy, approved.id)
        == approved_before
    )
    assert await _column_snapshot(pg_async_session, Hospital, hospital.id) == hospital_before
    # 합성·검수·큐 호출 없음. 사람이 만든 초안이 있어도 재합성이 필요해지지 않는다.
    assert calls == []
    assert await pg_async_session.run_sync(essence_refresh_needed, hospital.id) is False


async def test_copy_writes_one_audit_row_with_source_new_id_and_actor(pg_async_session):
    hospital, _note, approved, _archived = await _seed(pg_async_session)

    response = await _copy_as(pg_async_session, hospital.id, approved.id)

    rows = await _audit_rows(pg_async_session, hospital.id, COPY_ACTION)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor == OPERATOR
    assert audit.target_type == "philosophy"
    assert audit.target_id == response["id"]
    assert audit.detail == {
        "source_philosophy_id": str(approved.id),
        "source_version": 3,
        "new_philosophy_id": response["id"],
        "new_version": 5,
    }


# ── 거절 ────────────────────────────────────────────────────────────


async def test_copy_is_refused_while_the_hospital_already_has_a_draft(
    pg_async_session, monkeypatch
):
    calls = _record_llm_and_queue_calls(monkeypatch)
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    existing = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=6,
        status=PhilosophyStatus.DRAFT,
        # 다른 snapshot의 초안이어도 병원에 초안이 있으면 만들지 않는다.
        source_snapshot_hash="other-snapshot",
        created_by="system:essence-auto",
    )
    pg_async_session.add(existing)
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, approved.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == essence_api.COPY_DRAFT_EXISTS_DETAIL
    rows = await _philosophies(pg_async_session, hospital.id)
    assert len(rows) == 3
    assert [row.id for row in rows if row.status == PhilosophyStatus.DRAFT] == [existing.id]
    assert await _audit_rows(pg_async_session, hospital.id, COPY_ACTION) == []
    assert calls == []


@pytest.mark.parametrize("target_status", [PhilosophyStatus.DRAFT, PhilosophyStatus.ARCHIVED])
async def test_copy_only_accepts_an_approved_row(pg_async_session, target_status):
    hospital, _note, approved, archived = await _seed(pg_async_session)
    if target_status == PhilosophyStatus.DRAFT:
        target = HospitalContentPhilosophy(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            version=7,
            status=PhilosophyStatus.DRAFT,
            created_by=OPERATOR,
        )
        pg_async_session.add(target)
        await pg_async_session.commit()
    else:
        target = archived
    before = [
        await _column_snapshot(pg_async_session, HospitalContentPhilosophy, row.id)
        for row in await _philosophies(pg_async_session, hospital.id)
    ]

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, target.id)

    # 이 파일의 다른 상태 전이(보관·승인·수정)와 같이 잘못된 상태는 400이다.
    assert exc.value.status_code == 400
    after = [
        await _column_snapshot(pg_async_session, HospitalContentPhilosophy, row.id)
        for row in await _philosophies(pg_async_session, hospital.id)
    ]
    assert after == before
    assert approved.id in {row["id"] for row in after}


async def test_copy_cannot_reach_another_hospitals_approved_row(pg_async_session):
    hospital, _note, _approved, _archived = await _seed(pg_async_session)
    other_hospital, _other_note, other_approved, _other_archived = await _seed(pg_async_session)

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, other_approved.id)

    assert exc.value.status_code == 404
    assert len(await _philosophies(pg_async_session, hospital.id)) == 2
    assert len(await _philosophies(pg_async_session, other_hospital.id)) == 2


async def test_copy_is_refused_while_an_authority_refresh_is_pending(pg_async_session):
    """근거 철회로 재합성이 예정된 승인본을 복사하면 워커의 새 판이 사람의 초안을 넘는다."""
    hospital, _note, approved, _archived = await _seed(
        pg_async_session,
        extra_gaps=[{"field": AUTHORITY_CHANGE_FIELD, "reason": "근거 자료 철회"}],
    )

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, approved.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == essence_api.COPY_AUTHORITY_REFRESH_PENDING_DETAIL
    assert len(await _philosophies(pg_async_session, hospital.id)) == 2


@pytest.mark.parametrize("actor", [None, f"{UNVERIFIED_ACTOR_PREFIX}someone@example.com"])
async def test_copy_requires_a_verified_account(pg_async_session, actor):
    hospital, _note, approved, _archived = await _seed(pg_async_session)

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, approved.id, actor=actor)

    assert exc.value.status_code == 403
    assert len(await _philosophies(pg_async_session, hospital.id)) == 2


# ── 자료가 승인본의 snapshot과 어긋난 복사 ─────────────────────────────


async def _add_required_source(db, hospital_id, *, status: SourceStatus) -> HospitalSourceAsset:
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        source_type=SourceType.INTERVIEW,
        title="승인 뒤 추가된 인터뷰",
        raw_text="예약 전 상담에서 비용과 기간을 먼저 안내합니다.",
        content_hash=f"{uuid.uuid4().hex[:8]}-added",
        status=status,
        processed_at=datetime.now(timezone.utc) if status == SourceStatus.PROCESSED else None,
    )
    db.add(source)
    await db.commit()
    return source


async def _assert_nothing_was_copied(db, hospital_id) -> None:
    rows = await _philosophies(db, hospital_id)
    assert [row.status for row in rows] == [PhilosophyStatus.APPROVED, PhilosophyStatus.ARCHIVED]
    assert await _audit_rows(db, hospital_id, COPY_ACTION) == []


@pytest.mark.parametrize(
    "added_status",
    [SourceStatus.PROCESSED, SourceStatus.PENDING],
    ids=["processed_source_added", "unprocessed_source_added"],
)
async def test_copy_is_refused_while_sources_are_out_of_sync_with_the_approved_snapshot(
    pg_async_session, monkeypatch, added_status
):
    """승인이 거절할 사본은 만들지 않는다. 만들면 그 초안이 다음 복사까지 막는다."""
    calls = _record_llm_and_queue_calls(monkeypatch)
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    added = await _add_required_source(pg_async_session, hospital.id, status=added_status)

    for _attempt in range(2):
        # 두 번째 시도도 '초안 있음'이 아니라 같은 이유로 거절된다 — 남은 초안이 없다.
        with pytest.raises(HTTPException) as exc:
            await _copy_as(pg_async_session, hospital.id, approved.id)
        assert exc.value.status_code == 409
        assert exc.value.detail == (
            essence_api.COPY_SOURCES_CHANGED_DETAIL
            if added_status == SourceStatus.PROCESSED
            else essence_api._copy_unprocessed_sources_detail(1)
        )
        await _assert_nothing_was_copied(pg_async_session, hospital.id)
    assert calls == []

    # 자료가 다시 승인본의 snapshot과 같아지면 복사가 된다.
    added.status = SourceStatus.EXCLUDED
    await pg_async_session.commit()
    response = await _copy_as(pg_async_session, hospital.id, approved.id)
    assert response["status"] == PhilosophyStatus.DRAFT
    assert len(await _audit_rows(pg_async_session, hospital.id, COPY_ACTION)) == 1


async def test_copy_is_refused_when_the_approved_snapshot_source_was_excluded(pg_async_session):
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    source = await pg_async_session.get(
        HospitalSourceAsset, uuid.UUID(approved.source_asset_ids[0])
    )
    source.status = SourceStatus.EXCLUDED
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, approved.id)

    assert exc.value.status_code == 409
    assert exc.value.detail == essence_api.COPY_SOURCES_CHANGED_DETAIL
    await _assert_nothing_was_copied(pg_async_session, hospital.id)


def test_copy_sources_changed_detail_ends_with_the_approve_next_action():
    """복사의 자료 불일치 409도 승인 409와 같은 다음 행동 문장으로 끝난다."""
    assert essence_api.COPY_SOURCES_CHANGED_DETAIL == (
        "승인본을 만든 뒤 처리된 병원 자료가 변경되어(자료 집합이 다릅니다) 이 승인본을 "
        "복사한 초안은 승인할 수 없습니다. 초안을 만들지 않았습니다. "
        "현재 전체 자료로 콘텐츠 운영 기준 초안을 다시 생성해 주세요."
    )


@pytest.mark.parametrize(
    ("added_status", "expected_detail"),
    [
        (
            SourceStatus.PROCESSED,
            "초안 생성 후 처리된 병원 자료가 변경되었습니다(자료 집합이 다릅니다). "
            "현재 전체 자료로 콘텐츠 운영 기준 초안을 다시 생성해 주세요.",
        ),
        (
            SourceStatus.PENDING,
            "처리되지 않은 병원 자료 1개가 남아 있습니다. "
            "자료를 처리하거나 제외한 뒤 초안을 다시 생성해 주세요.",
        ),
    ],
    ids=["processed_source_added", "unprocessed_source_added"],
)
async def test_approve_keeps_its_source_snapshot_refusals_unchanged(
    pg_async_session, added_status, expected_detail
):
    """복사와 판정을 나눠 써도 승인의 409는 상태 코드·문자열 detail·무변경 그대로다."""
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    hospital_id = hospital.id
    response = await _copy_as(pg_async_session, hospital_id, approved.id)
    draft_id = uuid.UUID(response["id"])
    await _add_required_source(pg_async_session, hospital_id, status=added_status)
    row_ids = [row.id for row in await _philosophies(pg_async_session, hospital_id)]
    before = [
        await _column_snapshot(pg_async_session, HospitalContentPhilosophy, row_id)
        for row_id in row_ids
    ]

    token = set_request_actor(OPERATOR)
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                hospital_id,
                draft_id,
                essence_api.PhilosophyApprove(
                    reviewed_by="MotionLabs", confirm_evidence_reviewed=True
                ),
                db=pg_async_session,
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 409
    assert exc.value.detail == expected_detail
    await pg_async_session.rollback()
    after = [
        await _column_snapshot(pg_async_session, HospitalContentPhilosophy, row_id)
        for row_id in row_ids
    ]
    assert after == before
    assert await _audit_rows(pg_async_session, hospital_id, "approve_philosophy") == []


# ── 복사 409의 detail 형식 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "scenario",
    ["draft_exists", "authority_pending", "sources_changed", "sources_unprocessed", "concurrent"],
)
async def test_every_copy_conflict_uses_a_plain_string_detail(
    pg_async_session, monkeypatch, scenario
):
    """이 파일의 409 detail 관례(문자열)를 복사의 모든 409가 따른다."""
    extra_gaps = (
        [{"field": AUTHORITY_CHANGE_FIELD, "reason": "근거 자료 철회"}]
        if scenario == "authority_pending"
        else None
    )
    hospital, _note, approved, _archived = await _seed(pg_async_session, extra_gaps=extra_gaps)
    if scenario == "draft_exists":
        pg_async_session.add(
            HospitalContentPhilosophy(
                id=uuid.uuid4(),
                hospital_id=hospital.id,
                version=6,
                status=PhilosophyStatus.DRAFT,
                created_by="system:essence-auto",
            )
        )
        await pg_async_session.commit()
    elif scenario == "sources_changed":
        await _add_required_source(pg_async_session, hospital.id, status=SourceStatus.PROCESSED)
    elif scenario == "sources_unprocessed":
        await _add_required_source(pg_async_session, hospital.id, status=SourceStatus.PENDING)
    elif scenario == "concurrent":

        async def _commit_loses_the_race():
            raise IntegrityError("INSERT", {}, Exception("duplicate version"))

        monkeypatch.setattr(pg_async_session, "commit", _commit_loses_the_race)

    with pytest.raises(HTTPException) as exc:
        await _copy_as(pg_async_session, hospital.id, approved.id)

    assert exc.value.status_code == 409
    expected_detail = {
        "draft_exists": essence_api.COPY_DRAFT_EXISTS_DETAIL,
        "authority_pending": essence_api.COPY_AUTHORITY_REFRESH_PENDING_DETAIL,
        "sources_changed": essence_api.COPY_SOURCES_CHANGED_DETAIL,
        "sources_unprocessed": essence_api._copy_unprocessed_sources_detail(1),
        "concurrent": essence_api.COPY_CONCURRENT_CHANGE_DETAIL,
    }[scenario]
    assert exc.value.detail == expected_detail


# ── PATCH 감사 ──────────────────────────────────────────────────────


async def test_patch_records_before_and_after_of_changed_fields_only(pg_async_session):
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    response = await _copy_as(pg_async_session, hospital.id, approved.id)
    draft_id = uuid.UUID(response["id"])
    new_messages = ["진료 전에 충분히, 천천히 설명합니다", "환자마다 선택지가 다릅니다"]

    token = set_request_actor(OPERATOR)
    try:
        await essence_api.patch_philosophy(
            hospital.id,
            draft_id,
            essence_api.PhilosophyPatch(
                must_use_messages=new_messages,
                # 같은 값을 보낸 필드는 변경으로 기록하지 않는다.
                positioning_statement="충분한 설명과 개인별 선택지 안내",
            ),
            db=pg_async_session,
        )
    finally:
        reset_request_actor(token)

    rows = await _audit_rows(pg_async_session, hospital.id, PATCH_ACTION)
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor == OPERATOR
    assert audit.target_type == "philosophy"
    assert audit.target_id == str(draft_id)
    assert audit.detail == {
        "version": 5,
        "changes": {
            "must_use_messages": {
                "before": ["진료 전에 충분히 설명합니다", "환자마다 선택지가 다릅니다"],
                "after": new_messages,
            }
        },
    }


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


async def test_patch_audit_caps_oversized_values_with_preview_hash_and_length(pg_async_session):
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    response = await _copy_as(pg_async_session, hospital.id, approved.id)
    draft_id = uuid.UUID(response["id"])
    long_notes = "근거를 다시 확인한 메모. " * 400
    long_conflicts = [{"note": f"상충 {index}", "detail": "가" * 200} for index in range(40)]
    new_messages = ["진료 전에 충분히, 천천히 설명합니다", "환자마다 선택지가 다릅니다"]

    await essence_api.patch_philosophy(
        hospital.id,
        draft_id,
        essence_api.PhilosophyPatch(
            synthesis_notes=long_notes,
            conflict_notes=long_conflicts,
            must_use_messages=new_messages,
        ),
        db=pg_async_session,
    )

    rows = await _audit_rows(pg_async_session, hospital.id, PATCH_ACTION)
    assert len(rows) == 1
    changes = rows[0].detail["changes"]
    # 작은 값은 그대로다.
    assert changes["synthesis_notes"]["before"] == "합성 메모"
    assert changes["conflict_notes"]["before"] == [{"note": "상충 없음"}]
    assert changes["must_use_messages"] == {
        "before": ["진료 전에 충분히 설명합니다", "환자마다 선택지가 다릅니다"],
        "after": new_messages,
    }
    # 큰 값은 앞부분·전체 hash·길이만 남는다.
    for field_name, full_value in (
        ("synthesis_notes", long_notes),
        ("conflict_notes", long_conflicts),
    ):
        serialized = _serialized(full_value)
        assert len(serialized) > essence_api.PATCH_AUDIT_VALUE_MAX_CHARS
        assert changes[field_name]["after"] == {
            "truncated": True,
            "preview": serialized[: essence_api.PATCH_AUDIT_PREVIEW_CHARS],
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "length": len(serialized),
        }
    # 저장된 초안 자체는 자르지 않는다.
    draft = next(
        row for row in await _philosophies(pg_async_session, hospital.id) if row.id == draft_id
    )
    assert draft.synthesis_notes == long_notes
    assert draft.conflict_notes == long_conflicts


def test_patch_audit_value_cap_boundary():
    at_cap = "x" * (essence_api.PATCH_AUDIT_VALUE_MAX_CHARS - 2)  # 따옴표 2자 포함 = 상한
    over_cap = at_cap + "x"

    assert essence_api._patch_audit_value(at_cap) == at_cap
    assert essence_api._patch_audit_value(None) is None
    capped = essence_api._patch_audit_value(over_cap)
    assert capped == {
        "truncated": True,
        "preview": _serialized(over_cap)[: essence_api.PATCH_AUDIT_PREVIEW_CHARS],
        "sha256": hashlib.sha256(_serialized(over_cap).encode("utf-8")).hexdigest(),
        "length": essence_api.PATCH_AUDIT_VALUE_MAX_CHARS + 1,
    }


async def test_patch_without_an_actual_change_writes_no_audit_row(pg_async_session):
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    response = await _copy_as(pg_async_session, hospital.id, approved.id)

    await essence_api.patch_philosophy(
        hospital.id,
        uuid.UUID(response["id"]),
        essence_api.PhilosophyPatch(synthesis_notes="합성 메모"),
        db=pg_async_session,
    )

    assert await _audit_rows(pg_async_session, hospital.id, PATCH_ACTION) == []


# ── 복사 → 수정 → 승인 ─────────────────────────────────────────────


async def test_copy_patch_approve_makes_the_edited_sentence_the_new_standard(
    pg_async_session, monkeypatch
):
    calls = _record_llm_and_queue_calls(monkeypatch)
    hospital, _note, approved, _archived = await _seed(pg_async_session)
    sentence = "진료 전에 충분히 설명하고, 결정은 환자와 함께 내립니다"

    response = await _copy_as(pg_async_session, hospital.id, approved.id)
    draft_id = uuid.UUID(response["id"])
    assert calls == []

    token = set_request_actor(OPERATOR)
    try:
        await essence_api.patch_philosophy(
            hospital.id,
            draft_id,
            essence_api.PhilosophyPatch(
                must_use_messages=[sentence, "환자마다 선택지가 다릅니다"]
            ),
            db=pg_async_session,
        )
        assert calls == []
        approval = await essence_api.approve_philosophy(
            hospital.id,
            draft_id,
            essence_api.PhilosophyApprove(
                reviewed_by="MotionLabs",
                approval_note="문장 수정",
                confirm_evidence_reviewed=True,
            ),
            db=pg_async_session,
        )
    finally:
        reset_request_actor(token)

    assert approval["status"] == PhilosophyStatus.APPROVED
    rows = {row.id: row for row in await _philosophies(pg_async_session, hospital.id)}
    new_approved = rows[draft_id]
    assert new_approved.status == PhilosophyStatus.APPROVED
    assert new_approved.is_base is True
    assert new_approved.reviewed_by == OPERATOR
    assert new_approved.must_use_messages[0] == sentence
    assert rows[approved.id].status == PhilosophyStatus.ARCHIVED
    assert rows[approved.id].is_base is False
    assert rows[approved.id].must_use_messages[0] == "진료 전에 충분히 설명합니다"
    # 기존 승인 경로의 사후 자동 검수 등록만 있고, 합성·검수 호출은 없다.
    assert sorted(calls) == ["recover_ops_incident", "send_task"]


# ── 동시성(실제 커밋) ────────────────────────────────────────────────


def _integration_async_url() -> str:
    url = os.getenv("INTEGRATION_DATABASE_URL") or (
        "postgresql://reputation:reputation@localhost:5434/reputation_test"
    )
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


async def test_concurrent_copies_create_exactly_one_draft(pg_engine, monkeypatch):
    """동시 요청 셋이 각자 커밋해도 초안은 하나이고 나머지는 '초안 있음' 409다.

    그냥 gather로 띄우면 요청이 순서대로 끝나 잠금이 없어도 통과할 수 있다. 그래서
    감사 기록(모든 검사를 통과한 뒤, 커밋 직전) 지점에 랑데부를 심는다. 첫 요청은
    나머지 요청의 연결이 모두 잠금을 기다리는 모습(pg_locks)이 보이거나 랑데부에
    도착할 때까지 커밋하지 않는다. 잠금이 있으면 나머지는 잠금 뒤에서 커밋된 초안을
    보고 '초안 있음' 409로 물러난다. 잠금이 없으면 모두 검사를 통과해 같은
    version을 만들다 부딪히므로 그 detail이 나오지 않는다.

    감사 기록은 append-only(0024 트리거)라 지울 수 없어 남는다. 나머지 행은 병원 삭제
    cascade로 지운다.
    """
    del pg_engine
    _record_llm_and_queue_calls(monkeypatch)
    concurrency = 3
    engine = create_async_engine(_integration_async_url())
    hospital, source, note, approved, archived = _seed_objects()
    backend_pids: list[int] = []
    arrivals = {"n": 0}
    rendezvous = {"met": False}
    real_write_audit_log = essence_api.write_audit_log

    async def _requests_waiting_on_a_lock() -> int:
        async with AsyncSession(engine) as monitor:
            return int(
                await monitor.scalar(
                    text(
                        "SELECT count(DISTINCT pid) FROM pg_locks "
                        "WHERE NOT granted AND pid = ANY(:pids)"
                    ),
                    {"pids": list(backend_pids)},
                )
            )

    async def rendezvous_write_audit_log(*args, **kwargs):
        if kwargs.get("action") == COPY_ACTION:
            arrivals["n"] += 1
            deadline = asyncio.get_running_loop().time() + 10.0
            while asyncio.get_running_loop().time() < deadline:
                in_flight = arrivals["n"] + await _requests_waiting_on_a_lock()
                if in_flight >= concurrency:
                    rendezvous["met"] = True
                    break
                await asyncio.sleep(0.02)
        return await real_write_audit_log(*args, **kwargs)

    monkeypatch.setattr(essence_api, "write_audit_log", rendezvous_write_audit_log)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            setup.add_all([hospital, source, note, approved, archived])
            await setup.commit()

        async def copy_once():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                # 요청이 쓸 연결을 기록한다(같은 트랜잭션이 이어진다). 이 요청이 잠금을
                # 기다리는지 볼 때 쓴다.
                backend_pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
                return await _copy_as(session, hospital.id, approved.id)

        results = await asyncio.gather(
            *(copy_once() for _ in range(concurrency)), return_exceptions=True
        )

        assert rendezvous["met"], "동시 요청이 함께 진행되지 않았다 — 경쟁을 재현하지 못함"
        successes = [result for result in results if isinstance(result, dict)]
        refusals = [result for result in results if isinstance(result, HTTPException)]
        assert len(successes) == 1, results
        assert len(refusals) == concurrency - 1, results
        for refusal in refusals:
            assert refusal.status_code == 409
            # 잠금 없이 같은 version으로 부딪힌 409(동시 변경)가 아니어야 한다.
            assert refusal.detail == essence_api.COPY_DRAFT_EXISTS_DETAIL

        async with AsyncSession(engine) as check:
            drafts = (
                await check.execute(
                    select(HospitalContentPhilosophy.id, HospitalContentPhilosophy.version)
                    .where(
                        HospitalContentPhilosophy.hospital_id == hospital.id,
                        HospitalContentPhilosophy.status == PhilosophyStatus.DRAFT,
                    )
                )
            ).all()
            copy_audits = await check.scalar(
                select(func.count())
                .select_from(AdminAuditLog)
                .where(
                    AdminAuditLog.hospital_id == hospital.id,
                    AdminAuditLog.action == COPY_ACTION,
                )
            )
        assert [(str(row.id), row.version) for row in drafts] == [(successes[0]["id"], 5)]
        assert copy_audits == 1
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Hospital).where(Hospital.id == hospital.id))
            await cleanup.commit()
        await engine.dispose()
