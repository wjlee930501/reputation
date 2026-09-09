"""Real-Postgres proofs for atomic initial approval and Essence refresh."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.services.essence_auto_review import (
    AUTO_ESSENCE_ACTOR,
    EssenceAiReview,
    EssenceRefreshStatus,
    essence_refresh_needed,
    refresh_essence_snapshot,
)
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import get_essence_readiness_sync
from app.services.evidence_noise import compute_evidence_noise_hash


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()


def _seed_baseline(pg_session, *, label: str):
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"{label} 병원",
        slug=f"essence-{label}-{uuid.uuid4().hex[:8]}",
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
    previous = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        source_asset_ids=[],
        unsupported_gaps=[],
        conflict_notes=[],
        source_snapshot_hash="previous-snapshot",
    )
    pg_session.add_all([hospital, source, note, previous])
    pg_session.flush()
    return hospital, source, note, previous


def _candidate_payload(source, note) -> dict:
    return {
        "positioning_statement": "충분한 설명과 개인별 선택지 안내",
        "doctor_voice": None,
        "patient_promise": None,
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": [],
        "avoid_messages": [],
        "treatment_narratives": [],
        "local_context": {},
        "medical_ad_risk_rules": [],
        "evidence_map": {"positioning_statement": [str(note.id)]},
        "source_asset_ids": [str(source.id)],
        "unsupported_gaps": [],
        "conflict_notes": [],
        "synthesis_notes": "integration test",
        "source_snapshot_hash": compute_sources_snapshot_hash([source]),
    }


def _approved_review(note: HospitalSourceEvidenceNote) -> EssenceAiReview:
    return EssenceAiReview(
        decision="APPROVE",
        confidence=0.98,
        findings=(),
        reviewed_evidence_note_ids=(str(note.id),),
        summary="전체 근거 확인",
        model="reviewer-test",
    )


def test_initial_snapshot_is_auto_approved_and_idempotent(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="initial-approval")
    pg_session.delete(previous)
    pg_session.flush()
    synthesis_calls = 0

    def synthesize(*_args, **_kwargs):
        nonlocal synthesis_calls
        synthesis_calls += 1
        return _candidate_payload(source, note)

    assert essence_refresh_needed(pg_session, hospital.id) is True
    first = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=synthesize,
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert first.status == EssenceRefreshStatus.AUTO_APPROVED
    assert first.previous_philosophy_id is None
    assert synthesis_calls == 1
    approved = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital.id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
        )
    )
    assert len(approved) == 1
    assert approved[0].version == 1
    assert approved[0].reviewed_by == AUTO_ESSENCE_ACTOR
    assert approved[0].is_base is True

    second = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("up-to-date initial approval must not synthesize again")
        ),
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )
    assert second.status == EssenceRefreshStatus.UP_TO_DATE
    assert synthesis_calls == 1


def test_drifted_snapshot_preserves_base_without_synthesis(pg_session) -> None:
    hospital = Hospital(
        id=uuid.uuid4(),
        name="AI 운영 기준 통합테스트 병원",
        slug=f"essence-auto-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=False,
    )
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰",
        raw_text="진료 전에 충분히 설명하고 환자마다 다른 선택지를 안내합니다.",
        content_hash="new-source-hash",
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
    previous = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        positioning_statement=None,
        doctor_voice=None,
        patient_promise=None,
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        source_asset_ids=[],
        unsupported_gaps=[],
        conflict_notes=[],
        source_snapshot_hash="previous-snapshot",
    )
    pg_session.add_all([hospital, source, note, previous])
    pg_session.flush()

    original_hash = previous.source_snapshot_hash
    first = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("drift must not rewrite the base")
        ),
        reviewer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("drift must not review the base")
        ),
    )

    assert first.status == EssenceRefreshStatus.UP_TO_DATE
    assert first.philosophy_id == previous.id
    versions = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy)
            .where(HospitalContentPhilosophy.hospital_id == hospital.id)
            .order_by(HospitalContentPhilosophy.version)
        )
    )
    assert [(item.version, item.status, item.is_base) for item in versions] == [
        (1, PhilosophyStatus.APPROVED, True),
    ]
    assert previous.source_snapshot_hash == original_hash

    second = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("up-to-date replay must not synthesize")
        ),
        reviewer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("drift replay must not review")
        ),
    )

    assert second.status == EssenceRefreshStatus.UP_TO_DATE
    approved_count = len(
        list(
            pg_session.scalars(
                select(HospitalContentPhilosophy).where(
                    HospitalContentPhilosophy.hospital_id == hospital.id,
                    HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
                )
            )
        )
    )
    assert approved_count == 1


def test_existing_base_skips_low_confidence_review_and_creates_no_draft(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="escalation")
    hospital_id = hospital.id
    synth_calls = 0

    def synthesize(*_args, **_kwargs):
        nonlocal synth_calls
        synth_calls += 1
        return _candidate_payload(source, note)

    def uncertain(*_args, **_kwargs):
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.70,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="확신 부족",
            model="reviewer-test",
        )

    first = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=synthesize,
        reviewer=uncertain,
    )
    second = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=synthesize,
        reviewer=uncertain,
    )

    assert first.status == EssenceRefreshStatus.UP_TO_DATE
    assert second.status == EssenceRefreshStatus.UP_TO_DATE
    assert synth_calls == 0
    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital_id
            )
        )
    )
    assert sum(item.status == PhilosophyStatus.APPROVED for item in records) == 1
    assert sum(item.status == PhilosophyStatus.DRAFT for item in records) == 0
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )


def test_forbidden_candidate_is_resynthesized_once_then_auto_approved(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="bounded-remediation")
    pg_session.delete(previous)
    pg_session.flush()
    operator_notes: list[str | None] = []

    def synthesize(*_args, operator_note=None, **_kwargs):
        operator_notes.append(operator_note)
        payload = _candidate_payload(source, note)
        if operator_note is None:
            payload["content_principles"] = ["완치와 성공률 표현을 사용하지 않는다."]
            payload["evidence_map"]["content_principles"] = [str(note.id)]
        else:
            payload["content_principles"] = ["치료 효과를 단정하지 않는다."]
            payload["evidence_map"]["content_principles"] = [str(note.id)]
        return payload

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=synthesize,
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert result.status == EssenceRefreshStatus.AUTO_APPROVED
    assert result.synthesis_attempts == 2
    assert len(operator_notes) == 2
    assert operator_notes[0] is None
    assert "의료광고 금지 표현" in str(operator_notes[1])
    assert pg_session.get(HospitalContentPhilosophy, result.philosophy_id).is_base is True


def test_independent_review_finding_drives_one_fresh_synthesis(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="review-remediation")
    pg_session.delete(previous)
    pg_session.flush()
    operator_notes: list[str | None] = []
    review_calls = 0

    def synthesize(*_args, operator_note=None, **_kwargs):
        operator_notes.append(operator_note)
        return _candidate_payload(source, note)

    def review(*_args, **_kwargs):
        nonlocal review_calls
        review_calls += 1
        if review_calls == 1:
            return EssenceAiReview(
                decision="ESCALATE",
                confidence=0.97,
                findings=("환자 선택지 설명이 근거보다 넓습니다.",),
                reviewed_evidence_note_ids=(str(note.id),),
                summary="한정 재작성 필요",
                model="reviewer-test",
            )
        return _approved_review(note)

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=synthesize,
        reviewer=review,
    )

    assert result.status == EssenceRefreshStatus.AUTO_APPROVED
    assert result.synthesis_attempts == 2
    assert review_calls == 2
    assert operator_notes[0] is None
    assert "환자 선택지 설명이 근거보다 넓습니다." in str(operator_notes[1])


def test_refresh_never_rewrites_current_grounded_approved_core(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="carry-forward")
    previous.positioning_statement = "충분한 설명과 환자별 선택지 안내"
    previous.must_use_messages = ["환자 상태에 맞춰 선택지를 안내합니다."]
    previous.content_principles = [
        "완치와 성공률 표현을 사용하지 않습니다.",
        "환자 상태에 따라 설명합니다.",
    ]
    previous.evidence_map = {
        "positioning_statement": [str(note.id)],
        "must_use_messages": [str(note.id)],
        "content_principles": [str(note.id)],
    }
    pg_session.flush()

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("base drift must not synthesize")
        ),
        reviewer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("base drift must not review")
        ),
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    stored = pg_session.get(HospitalContentPhilosophy, previous.id)
    assert stored.positioning_statement == "충분한 설명과 환자별 선택지 안내"
    assert stored.must_use_messages == ["환자 상태에 맞춰 선택지를 안내합니다."]
    assert stored.content_principles == [
        "완치와 성공률 표현을 사용하지 않습니다.",
        "환자 상태에 따라 설명합니다.",
    ]


def test_existing_base_leaves_legacy_automatic_draft_unchanged(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="legacy-auto-draft")
    legacy_payload = _candidate_payload(source, note)
    legacy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=2,
        status=PhilosophyStatus.DRAFT,
        created_by=AUTO_ESSENCE_ACTOR,
        **legacy_payload,
    )
    pg_session.add(legacy)
    pg_session.flush()
    # Match the production escalation path: INSERT/flush, then append findings in
    # the same transaction. PostgreSQL now() is transaction-stable, so an untouched
    # system artifact keeps equal creation/update timestamps.
    legacy.unsupported_gaps = [
        {"field": "automatic_ai_review", "reason": "이전 자동 안전검사 차단"},
        {"field": "automatic_recovery_cycle", "reason": "7"},
    ]
    pg_session.flush()

    assert legacy.created_at == legacy.updated_at
    assert essence_refresh_needed(pg_session, hospital.id) is False
    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    assert result.synthesis_attempts == 0
    assert pg_session.get(HospitalContentPhilosophy, legacy.id).status == PhilosophyStatus.DRAFT
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )
    assert result.philosophy_id == previous.id


def test_operator_touched_automatic_draft_is_never_superseded(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="touched-auto-draft")
    payload = _candidate_payload(source, note)
    payload["unsupported_gaps"] = [
        {"field": "automatic_ai_review", "reason": "이전 자동 안전검사 차단"}
    ]
    draft = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=2,
        status=PhilosophyStatus.DRAFT,
        created_by=AUTO_ESSENCE_ACTOR,
        **payload,
    )
    pg_session.add(draft)
    pg_session.flush()
    draft.positioning_statement = "운영자가 근거를 확인해 수정한 문안"
    draft.updated_at = draft.created_at + timedelta(seconds=1)
    pg_session.flush()

    assert essence_refresh_needed(pg_session, hospital.id) is False
    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("operator-touched auto draft must stop automatic synthesis")
        ),
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    assert pg_session.get(HospitalContentPhilosophy, draft.id).status == PhilosophyStatus.DRAFT
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )


def test_persistent_candidate_failure_stops_periodic_retry_loop(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="persistent-failure")
    pg_session.delete(previous)
    pg_session.flush()
    synth_calls = 0

    def still_forbidden(*_args, **_kwargs):
        nonlocal synth_calls
        synth_calls += 1
        payload = _candidate_payload(source, note)
        payload["content_principles"] = ["완치 표현을 사용하지 않는다."]
        payload["evidence_map"]["content_principles"] = [str(note.id)]
        return payload

    first = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=still_forbidden,
        reviewer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deterministic failure must not reach AI review")
        ),
    )

    assert first.status == EssenceRefreshStatus.ESCALATED
    assert first.synthesis_attempts == 2
    assert synth_calls == 2
    assert essence_refresh_needed(pg_session, hospital.id) is False
    escalated = pg_session.get(HospitalContentPhilosophy, first.philosophy_id)
    assert any(
        item.get("field") == "automatic_recovery_cycle" and item.get("reason") == "8"
        for item in escalated.unsupported_gaps
        if isinstance(item, dict)
    )

    second = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=still_forbidden,
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )
    assert second.status == EssenceRefreshStatus.ESCALATED
    assert synth_calls == 2
    assert pg_session.get(HospitalContentPhilosophy, previous.id) is None


def test_manual_same_snapshot_draft_is_never_superseded(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="manual-draft")
    manual = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=2,
        status=PhilosophyStatus.DRAFT,
        created_by="OPERATOR",
        **_candidate_payload(source, note),
    )
    pg_session.add(manual)
    pg_session.flush()

    assert essence_refresh_needed(pg_session, hospital.id) is False
    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual draft must stop automatic synthesis")
        ),
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    assert pg_session.get(HospitalContentPhilosophy, manual.id).status == PhilosophyStatus.DRAFT
    assert (
        pg_session.get(HospitalContentPhilosophy, previous.id).status == PhilosophyStatus.APPROVED
    )


def test_existing_base_never_calls_reviewer_and_preserves_approval(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="provider-error")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("review provider unavailable")

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("existing base must not synthesize")
        ),
        reviewer=unavailable,
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital.id
            )
        )
    )
    assert [(item.id, item.status) for item in records] == [
        (previous.id, PhilosophyStatus.APPROVED)
    ]


def test_draft_created_during_review_is_never_approved_with_another_payload(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="draft-race")
    pg_session.delete(previous)
    pg_session.flush()
    external_draft_id = uuid.uuid4()

    def create_competing_draft(*_args, **_kwargs):
        payload = _candidate_payload(source, note)
        payload["positioning_statement"] = "사람이 별도로 작성한 미검수 문안"
        pg_session.add(
            HospitalContentPhilosophy(
                id=external_draft_id,
                hospital_id=hospital.id,
                version=2,
                status=PhilosophyStatus.DRAFT,
                created_by="OPERATOR",
                **payload,
            )
        )
        pg_session.flush()
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="원래 AI 후보만 검수함",
            model="reviewer-test",
        )

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=create_competing_draft,
    )

    assert result.status == EssenceRefreshStatus.ESCALATED
    assert result.philosophy_id == external_draft_id
    assert "별도 초안" in result.findings[0]
    assert pg_session.get(HospitalContentPhilosophy, previous.id) is None
    assert (
        pg_session.get(HospitalContentPhilosophy, external_draft_id).status
        == PhilosophyStatus.DRAFT
    )


def test_source_change_during_review_aborts_promotion(pg_session) -> None:
    hospital, source, note, previous = _seed_baseline(pg_session, label="source-race")
    hospital_id = hospital.id
    previous_id = previous.id
    pg_session.delete(previous)
    pg_session.commit()

    def mutate_source(*_args, **_kwargs):
        source.content_hash = "changed-during-review"
        pg_session.flush()
        return EssenceAiReview(
            decision="APPROVE",
            confidence=0.99,
            findings=(),
            reviewed_evidence_note_ids=(str(note.id),),
            summary="검수 직후 자료가 바뀜",
            model="reviewer-test",
        )

    result = refresh_essence_snapshot(
        pg_session,
        hospital_id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=mutate_source,
    )

    assert result.status == EssenceRefreshStatus.SNAPSHOT_CHANGED
    records = list(
        pg_session.scalars(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital_id
            )
        )
    )
    assert records == []
    assert pg_session.get(HospitalContentPhilosophy, previous_id) is None


def test_marking_a_note_as_noise_keeps_base_without_refresh(pg_session) -> None:
    hospital, source, note, approved = _seed_baseline(pg_session, label="noise")
    # 제외 후에도 합성할 근거가 남아 있어야 재검수가 의미 있다.
    remaining = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.KEY_MESSAGE,
        claim="선택지를 함께 정한다.",
        source_excerpt=source.raw_text[:10],
        confidence=0.9,
        note_metadata={},
    )
    pg_session.add(remaining)
    approved.source_snapshot_hash = compute_sources_snapshot_hash([source])
    approved.evidence_noise_hash = compute_evidence_noise_hash([])
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is False

    note.note_metadata = {**(note.note_metadata or {}), "is_noise": True}
    pg_session.commit()

    assert essence_refresh_needed(pg_session, hospital.id) is False
    readiness = get_essence_readiness_sync(pg_session, hospital.id)
    assert readiness.current is not None and readiness.current.id == approved.id
    assert readiness.public_philosophy is not None and readiness.public_philosophy.id == approved.id


def test_legacy_approval_without_noise_hash_keeps_base(pg_session) -> None:
    hospital, source, note, approved = _seed_baseline(pg_session, label="legacy-null")
    approved.source_snapshot_hash = compute_sources_snapshot_hash([source])
    approved.evidence_noise_hash = None
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is False


def test_noise_only_change_does_not_churn_base(pg_session) -> None:
    hospital, source, noise_note, approved = _seed_baseline(pg_session, label="noise-only")
    # 제외 후에도 합성할 근거가 남아 있어야 재검수가 의미 있다.
    remaining = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_asset_id=source.id,
        note_type=EvidenceNoteType.KEY_MESSAGE,
        claim="선택지를 함께 정한다.",
        source_excerpt=source.raw_text[:10],
        confidence=0.9,
        note_metadata={},
    )
    pg_session.add(remaining)
    approved.source_snapshot_hash = compute_sources_snapshot_hash([source])
    approved.evidence_noise_hash = compute_evidence_noise_hash([])
    pg_session.commit()

    noise_note.note_metadata = {**(noise_note.note_metadata or {}), "is_noise": True}
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is False

    synthesized_note_ids: list[str] = []
    reviewed_note_ids: list[str] = []

    def _synth(_hospital, _sources, notes, **_kwargs):
        synthesized_note_ids.extend(str(item.id) for item in notes)
        return _candidate_payload(source, remaining)

    def _review(_hospital, _previous, _payload, notes, **_kwargs):
        reviewed_note_ids.extend(str(item.id) for item in notes)
        return _approved_review(remaining)

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=_synth,
        reviewer=_review,
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    stored = pg_session.get(HospitalContentPhilosophy, result.philosophy_id)
    assert stored.id == approved.id
    assert stored.evidence_noise_hash == compute_evidence_noise_hash([])
    pg_session.refresh(approved)
    assert approved.status == PhilosophyStatus.APPROVED
    assert synthesized_note_ids == []
    assert reviewed_note_ids == []
    assert essence_refresh_needed(pg_session, hospital.id) is False


def test_legacy_null_hash_stays_audit_metadata_without_refresh(pg_session) -> None:
    hospital, source, note, approved = _seed_baseline(pg_session, label="legacy-null-settle")
    approved.source_snapshot_hash = compute_sources_snapshot_hash([source])
    approved.evidence_noise_hash = None
    pg_session.commit()
    assert essence_refresh_needed(pg_session, hospital.id) is False

    result = refresh_essence_snapshot(
        pg_session,
        hospital.id,
        synthesizer=lambda *_args, **_kwargs: _candidate_payload(source, note),
        reviewer=lambda *_args, **_kwargs: _approved_review(note),
    )

    assert result.status == EssenceRefreshStatus.UP_TO_DATE
    stored = pg_session.get(HospitalContentPhilosophy, result.philosophy_id)
    assert stored.evidence_noise_hash is None
    pg_session.refresh(approved)
    assert approved.status == PhilosophyStatus.APPROVED
    assert essence_refresh_needed(pg_session, hospital.id) is False
