"""PostgreSQL contracts for the bounded public-content re-review backfill."""

from __future__ import annotations

import copy
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
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
from app.models.operations import OperationRun, OperationRunState
from app.services import content_public_review_backfill as backfill
from app.services import cost_guard
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    ContentAiReviewUnavailableReason,
    candidate_review_coverage,
    candidate_review_payload,
    candidate_sha256,
)
from app.services.cost_guard import CostGuardDecision, ReservationReceipt
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.evidence_noise import compute_evidence_noise_hash
from app.services.sync_async_bridge import SyncAsyncBridge


@dataclass
class Seed:
    hospital: Hospital
    source: HospitalSourceAsset
    philosophy: HospitalContentPhilosophy
    schedule: ContentSchedule
    item: ContentItem


@pytest.fixture
def committed_db(pg_engine):
    """A real committed session because the service commits around provider calls."""

    db = Session(pg_engine, expire_on_commit=False)
    hospital_ids: list[uuid.UUID] = []
    try:
        yield db, hospital_ids
    finally:
        db.rollback()
        if hospital_ids:
            db.execute(delete(OperationRun).where(OperationRun.hospital_id.in_(hospital_ids)))
            db.execute(delete(Hospital).where(Hospital.id.in_(hospital_ids)))
            db.commit()
        db.close()


def _old_review(item: ContentItem, status: str) -> dict:
    return {
        "status": status,
        "confidence": 0.2,
        "blocking": True,
        "findings": [{"message": "기존 미해결 검수"}],
        "summary": "기존 검수 결과",
        "model": "legacy-reviewer",
        "candidate_sha256": candidate_sha256(item),
        "coverage": candidate_review_coverage(item),
    }


def _seed(
    db: Session,
    tracked: list[uuid.UUID],
    *,
    review_status: str = "REVISE",
    hospital_status: HospitalStatus = HospitalStatus.ACTIVE,
    site_live: bool = True,
) -> Seed:
    suffix = uuid.uuid4().hex[:12]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"공개검수 통합테스트 {suffix}",
        slug=f"public-review-it-{suffix}",
        status=hospital_status,
        site_live=site_live,
        region=["서울"],
        specialties=["내과"],
        treatments=["건강상담"],
    )
    tracked.append(hospital.id)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="병원 공식 자료",
        raw_text="확인된 병원 소개와 진료 안내",
        content_hash=uuid.uuid4().hex,
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
    )
    db.add_all([hospital, source])
    db.flush()
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="확인된 정보를 충분히 설명합니다.",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        source_asset_ids=[str(source.id)],
        unsupported_gaps=[],
        conflict_notes=[],
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        approved_at=datetime.now(timezone.utc),
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 9, 1),
    )
    db.add_all([philosophy, schedule])
    db.flush()
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="공개된 건강 안내",
        body="환자 상태에 따라 의료진의 확인이 필요합니다.",
        meta_description="확인된 정보에 따른 건강 안내입니다.",
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr"}],
        content_brief={
            "schema_version": "content-brief-v2",
            "target_query": "건강 상담은 언제 받아야 하나요?",
            "operator_notes": ["공식 자료만 사용"],
        },
        brief_status="APPROVED",
        scheduled_date=date(2026, 9, 7),
        status=ContentStatus.PUBLISHED,
        content_revision=10,
        content_philosophy_id=philosophy.id,
        generation_philosophy_id=philosophy.id,
        last_reviewed_philosophy_id=philosophy.id,
        essence_status="ALIGNED",
        published_at=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc),
        published_by="SYSTEM_AUTO_PUBLISH",
    )
    db.add(item)
    db.flush()
    if review_status == "PASS":
        item.essence_check_summary = {
            "kept": "기존 요약",
            "ai_review": {
                "schema_version": "content-review-v2",
                "status": "PASS",
                "blocking": False,
                "candidate_sha256": candidate_sha256(item),
                "coverage": candidate_review_coverage(item),
                "findings": [],
            },
        }
    elif review_status == "NONE":
        item.essence_check_summary = {"kept": "기존 요약"}
    else:
        item.essence_check_summary = {
            "kept": "기존 요약",
            "blocking": True,
            "findings": ["기존 미해결 검수"],
            "ai_review": _old_review(item, review_status),
        }
    db.commit()
    return Seed(hospital, source, philosophy, schedule, item)


def _refresh(db: Session, model, object_id):
    return db.execute(
        select(model).where(model.id == object_id).execution_options(populate_existing=True)
    ).scalar_one()


def _run(db: Session, seed: Seed) -> OperationRun:
    return db.execute(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == seed.hospital.id,
            OperationRun.operation_type == backfill.OPERATION_TYPE,
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


def _allowed_decision() -> CostGuardDecision:
    return CostGuardDecision(
        True,
        receipt=ReservationReceipt(
            id=str(uuid.uuid4()),
            category="content",
            daily_period="2026-09-07",
            monthly_period="2026-09",
            reserved_units=1,
        ),
    )


async def _allow(**_kwargs) -> CostGuardDecision:
    return _allowed_decision()


def _review(kwargs, *, status=ContentAiReviewStatus.PASS, findings=(), attempted=True, reason=None):
    candidate = kwargs["content"]
    return ContentAiReview(
        status=status,
        confidence=0.97 if status != ContentAiReviewStatus.UNAVAILABLE else 0.0,
        findings=tuple(findings),
        summary="독립 검수 결과",
        model="fake-independent-reviewer",
        candidate_sha256=candidate_sha256(candidate),
        coverage=candidate_review_coverage(candidate),
        provider_attempted=attempted,
        unavailable_reason=reason,
    )


def _content_state(item: ContentItem) -> dict:
    return {
        "candidate": candidate_review_payload(item),
        "brief": copy.deepcopy(item.content_brief),
        "brief_status": item.brief_status,
        "content_philosophy_id": item.content_philosophy_id,
        "generation_philosophy_id": item.generation_philosophy_id,
        "last_reviewed_philosophy_id": item.last_reviewed_philosophy_id,
        "essence_status": item.essence_status,
        "published_at": item.published_at,
        "published_by": item.published_by,
        "revision": item.content_revision,
        "summary": copy.deepcopy(item.essence_check_summary),
    }


def test_dry_run_counts_are_mutually_exclusive_and_never_touch_cost_or_reviewer(
    committed_db,
) -> None:
    db, tracked = committed_db
    stale_revise = _seed(db, tracked, review_status="REVISE")
    stale_unavailable = _seed(db, tracked, review_status="UNAVAILABLE")
    current = _seed(db, tracked, review_status="PASS")
    inactive = _seed(db, tracked, review_status="REVISE", site_live=False)
    missing_id = uuid.uuid4()
    calls = {"reserve": 0, "reviewer": 0}

    async def reserve(**_kwargs):
        calls["reserve"] += 1
        raise AssertionError("dry-run reserved cost")

    async def reviewer(**_kwargs):
        calls["reviewer"] += 1
        raise AssertionError("dry-run called reviewer")

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={
            stale_revise.item.id,
            stale_unavailable.item.id,
            current.item.id,
            inactive.item.id,
            missing_id,
        },
        dry_run=True,
        reserve=reserve,
        reviewer=reviewer,
    )

    assert result.to_dict() == {
        "allowlisted": 5,
        "missing": 1,
        "inactive": 1,
        "current": 1,
        "cleared": 0,
        "blocked": 0,
        "unavailable": 0,
        "costblocked": 0,
        "stale": 2,
        "unresolved": 4,
    }
    assert calls == {"reserve": 0, "reviewer": 0}


@pytest.mark.parametrize("soft", [False, True], ids=["pass", "soft-only"])
def test_allowed_review_precommits_attempt_then_preserves_candidate_and_provenance(
    committed_db, pg_engine, soft
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    before = _content_state(seed.item)
    observed: dict[str, object] = {}

    async def reviewer(**kwargs):
        with Session(pg_engine) as witness:
            run = witness.scalar(
                select(OperationRun).where(
                    OperationRun.hospital_id == seed.hospital.id,
                    OperationRun.operation_type == backfill.OPERATION_TYPE,
                )
            )
            observed.update(state=run.state, attempt_count=run.attempt_count)
        findings = (
            ContentAiFinding(
                severity=ContentAiFindingSeverity.SOFT,
                kind=ContentAiFindingKind.STYLE,
                message="문장을 조금 더 짧게 쓸 수 있습니다.",
            ),
        ) if soft else ()
        status = ContentAiReviewStatus.REVISE if soft else ContentAiReviewStatus.PASS
        return _review(kwargs, status=status, findings=findings)

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=reviewer,
    )

    assert result.cleared == 1
    assert observed == {"state": OperationRunState.RUNNING, "attempt_count": 1}
    item = _refresh(db, ContentItem, seed.item.id)
    after = _content_state(item)
    assert after["candidate"] == before["candidate"]
    for field in (
        "brief",
        "brief_status",
        "content_philosophy_id",
        "generation_philosophy_id",
        "last_reviewed_philosophy_id",
        "essence_status",
        "published_at",
        "published_by",
    ):
        assert after[field] == before[field]
    assert after["revision"] == before["revision"] + 1
    assert after["summary"]["kept"] == before["summary"]["kept"]
    assert after["summary"]["ai_review"]["candidate_sha256"] == candidate_sha256(item)
    run = _run(db, seed)
    assert run.state == OperationRunState.SUCCEEDED
    assert run.attempt_count == 1
    assert run.success_count == 1


def test_denied_cost_records_costblocked_without_attempt_and_preserves_old_review(
    committed_db,
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    before = _content_state(seed.item)
    provider_attempts = 0

    async def denied(**_kwargs):
        return CostGuardDecision(False, "일일 비용 상한")

    async def reviewer(**kwargs):
        nonlocal provider_attempts
        assert kwargs["cost_decision"].allowed is False
        return _review(
            kwargs,
            status=ContentAiReviewStatus.UNAVAILABLE,
            attempted=False,
            reason=ContentAiReviewUnavailableReason.COST_BLOCKED,
        )

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=denied,
        reviewer=reviewer,
    )

    assert result.costblocked == 1
    assert provider_attempts == 0
    item = _refresh(db, ContentItem, seed.item.id)
    assert _content_state(item) == before
    run = _run(db, seed)
    assert run.attempt_count == 0
    assert run.state == OperationRunState.REQUESTED
    assert run.safe_error_code == "COST_BLOCKED"


def test_typed_provider_unavailable_preserves_old_review_and_stops_at_three(
    committed_db,
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked, review_status="UNAVAILABLE")
    before = _content_state(seed.item)
    calls = {"reserve": 0, "reviewer": 0}

    async def reserve(**_kwargs):
        calls["reserve"] += 1
        return _allowed_decision()

    async def reviewer(**kwargs):
        calls["reviewer"] += 1
        return _review(
            kwargs,
            status=ContentAiReviewStatus.UNAVAILABLE,
            attempted=True,
            reason=ContentAiReviewUnavailableReason.PROVIDER_ERROR,
        )

    for _ in range(4):
        result = backfill.run_content_public_review_backfill(
            db,
            content_ids={seed.item.id},
            dry_run=False,
            reserve=reserve,
            reviewer=reviewer,
        )
        assert result.unavailable == 1

    assert calls == {"reserve": 3, "reviewer": 3}
    assert _content_state(_refresh(db, ContentItem, seed.item.id)) == before
    run = _run(db, seed)
    assert run.attempt_count == 3
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code == ContentAiReviewUnavailableReason.PROVIDER_ERROR.value


@pytest.mark.parametrize("concurrent_change", ["content", "source", "essence"])
def test_concurrent_candidate_or_context_change_is_stale_and_preserves_editor_state(
    committed_db, pg_engine, concurrent_change
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    old_review = copy.deepcopy(seed.item.essence_check_summary["ai_review"])

    async def reviewer(**kwargs):
        with Session(pg_engine) as editor:
            if concurrent_change == "content":
                editor.execute(
                    update(ContentItem)
                    .where(ContentItem.id == seed.item.id)
                    .values(body="운영자가 저장한 새 본문", content_revision=11)
                )
            elif concurrent_change == "source":
                editor.execute(
                    update(HospitalSourceAsset)
                    .where(HospitalSourceAsset.id == seed.source.id)
                    .values(content_hash="운영자가-바꾼-자료-hash")
                )
            else:
                editor.execute(
                    update(HospitalContentPhilosophy)
                    .where(HospitalContentPhilosophy.id == seed.philosophy.id)
                    .values(positioning_statement="운영자가 승인한 새 운영 기준")
                )
            editor.commit()
        return _review(kwargs)

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=reviewer,
    )

    assert result.stale == 1
    item = _refresh(db, ContentItem, seed.item.id)
    assert item.essence_check_summary["ai_review"] == old_review
    if concurrent_change == "content":
        assert item.body == "운영자가 저장한 새 본문"
        assert item.content_revision == 11
    elif concurrent_change == "source":
        assert _refresh(db, HospitalSourceAsset, seed.source.id).content_hash == (
            "운영자가-바꾼-자료-hash"
        )
        assert item.content_revision == 10
    else:
        assert _refresh(db, HospitalContentPhilosophy, seed.philosophy.id).positioning_statement == (
            "운영자가 승인한 새 운영 기준"
        )
        assert item.content_revision == 10
    run = _run(db, seed)
    assert run.state == OperationRunState.CANCELLED
    assert run.attempt_count == 1


def test_noise_marked_during_review_is_stale_and_never_saves_the_result(
    committed_db, pg_engine
) -> None:
    """H-02: 검수 중 근거 노트를 빼면 CAS 체크포인트가 승인을 stale로 보고 결과를 버린다."""
    db, tracked = committed_db
    seed = _seed(db, tracked)
    note = HospitalSourceEvidenceNote(
        id=uuid.uuid4(),
        hospital_id=seed.hospital.id,
        source_asset_id=seed.source.id,
        note_type=EvidenceNoteType.DOCTOR_PHILOSOPHY,
        claim="확인된 정보를 충분히 설명한다.",
        source_excerpt="확인된 병원 소개와 진료 안내",
        confidence=0.95,
        note_metadata={"is_noise": False},
    )
    db.add(note)
    seed.philosophy.evidence_noise_hash = compute_evidence_noise_hash([])
    db.commit()
    old_review = copy.deepcopy(seed.item.essence_check_summary["ai_review"])

    async def reviewer(**kwargs):
        with Session(pg_engine) as operator:
            operator.execute(
                update(HospitalSourceEvidenceNote)
                .where(HospitalSourceEvidenceNote.id == note.id)
                .values(note_metadata={"is_noise": True})
            )
            operator.commit()
        return _review(kwargs)

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=reviewer,
    )

    assert result.stale == 1
    item = _refresh(db, ContentItem, seed.item.id)
    assert item.essence_check_summary["ai_review"] == old_review
    assert item.content_revision == 10
    run = _run(db, seed)
    assert run.state == OperationRunState.CANCELLED
    assert run.safe_error_code == "SOURCE_CHANGED"



def test_expired_running_at_attempt_three_never_calls_a_fourth_provider(
    committed_db,
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    outcome, expectation, _hospital, _philosophy = backfill._inspect(db, seed.item.id)
    assert outcome == "stale" and expectation is not None
    db.rollback()
    run = OperationRun(
        hospital_id=seed.hospital.id,
        operation_type=backfill.OPERATION_TYPE,
        idempotency_key=expectation.operation_key,
        state=OperationRunState.RUNNING,
        attempt_count=3,
        total_count=1,
        request_payload=backfill._request_payload(expectation),
        lease_owner="expired-third-attempt",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(run)
    db.commit()

    async def must_not_run(**_kwargs):
        raise AssertionError("attempt four was started")

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=must_not_run,
        reviewer=must_not_run,
    )

    assert result.unavailable == 1
    stored = _refresh(db, OperationRun, run.id)
    assert stored.attempt_count == 3
    assert stored.state == OperationRunState.FAILED
    assert stored.lease_owner is None
    assert stored.failure_count == 1


def test_unrelated_revision_stale_reuses_cancelled_run_and_remaining_attempt_budget(
    committed_db, pg_engine
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    original_candidate = candidate_review_payload(seed.item)
    calls = 0

    async def reviewer(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            with Session(pg_engine) as editor:
                editor.execute(
                    update(ContentItem)
                    .where(ContentItem.id == seed.item.id)
                    .values(content_revision=ContentItem.content_revision + 1)
                )
                editor.commit()
        else:
            with Session(pg_engine) as witness:
                run = witness.scalar(
                    select(OperationRun).where(
                        OperationRun.hospital_id == seed.hospital.id,
                        OperationRun.operation_type == backfill.OPERATION_TYPE,
                    )
                )
                assert run.attempt_count == 2
                assert run.state == OperationRunState.RUNNING
        return _review(kwargs)

    first = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=reviewer,
    )
    cancelled = _run(db, seed)
    cancelled_id = cancelled.id

    assert first.stale == 1
    assert cancelled.state == OperationRunState.CANCELLED
    assert cancelled.attempt_count == 1
    assert candidate_review_payload(_refresh(db, ContentItem, seed.item.id)) == original_candidate

    second = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=reviewer,
    )

    assert second.cleared == 1
    assert calls == 2
    succeeded = _run(db, seed)
    assert succeeded.id == cancelled_id
    assert succeeded.state == OperationRunState.SUCCEEDED
    assert succeeded.attempt_count == 2
    item = _refresh(db, ContentItem, seed.item.id)
    assert item.content_revision == 12
    assert candidate_review_payload(item) == original_candidate


def test_cancelled_retry_counters_remain_exclusive_when_attempt_three_fails(
    committed_db, pg_engine
) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked)
    calls = 0

    async def reviewer(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            with Session(pg_engine) as editor:
                editor.execute(
                    update(ContentItem)
                    .where(ContentItem.id == seed.item.id)
                    .values(content_revision=ContentItem.content_revision + 1)
                )
                editor.commit()
            return _review(kwargs)
        return _review(
            kwargs,
            status=ContentAiReviewStatus.UNAVAILABLE,
            attempted=True,
            reason=ContentAiReviewUnavailableReason.PROVIDER_ERROR,
        )

    outcomes = [
        backfill.run_content_public_review_backfill(
            db,
            content_ids={seed.item.id},
            dry_run=False,
            reserve=_allow,
            reviewer=reviewer,
        )
        for _ in range(3)
    ]

    assert [outcome.stale for outcome in outcomes] == [1, 0, 0]
    assert [outcome.unavailable for outcome in outcomes] == [0, 1, 1]
    run = _run(db, seed)
    assert run.state == OperationRunState.FAILED
    assert run.attempt_count == 3
    assert (run.success_count, run.failure_count, run.skipped_count) == (0, 1, 0)


@pytest.mark.skipif(
    not os.getenv("COST_GUARD_REDIS_URL"),
    reason="COST_GUARD_REDIS_URL is not configured",
)
def test_two_items_share_real_redis_loop_and_fully_refund_unused_reservations(
    committed_db, monkeypatch
) -> None:
    db, tracked = committed_db
    first = _seed(db, tracked)
    second = _seed(db, tracked)
    url = os.environ["COST_GUARD_REDIS_URL"]
    monkeypatch.setattr(cost_guard.settings, "REDIS_URL", url)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_DAILY_CONTENT_CALLS", 10000)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_MONTHLY_CONTENT_CALLS", 10000)
    cost_guard._redis_client = None
    receipts = []

    async def snapshot(client):
        now = cost_guard._now()
        daily_key = cost_guard._daily_key("content", cost_guard._daily_period(now))
        monthly_key = cost_guard._monthly_key("content", cost_guard._monthly_period(now))
        return (
            daily_key,
            monthly_key,
            int(await client.get(daily_key) or 0),
            int(await client.get(monthly_key) or 0),
            await client.get(cost_guard.KILL_SWITCH_KEY),
        )

    with SyncAsyncBridge() as bridge:
        client = cost_guard._client()
        daily_key, monthly_key, daily_before, monthly_before, kill_switch = bridge.run(
            snapshot(client)
        )
    if kill_switch not in (None, b"0", b"false", b"False"):
        with SyncAsyncBridge() as bridge:
            bridge.run(cost_guard._client().aclose())
        cost_guard._redis_client = None
        pytest.skip("isolated Redis cost guard kill switch is active")

    async def no_provider(**kwargs):
        decision = kwargs["cost_decision"]
        assert decision.receipt is not None, "real Redis reservation fail-opened"
        receipts.append(decision.receipt)
        settled = await cost_guard.settle_reservation(
            decision.receipt,
            consumed_units=0,
        )
        assert settled is not None
        assert settled.released_units == 1
        return _review(
            kwargs,
            status=ContentAiReviewStatus.UNAVAILABLE,
            attempted=False,
            reason=ContentAiReviewUnavailableReason.PROVIDER_UNCONFIGURED,
        )

    try:
        result = backfill.run_content_public_review_backfill(
            db,
            content_ids={first.item.id, second.item.id},
            dry_run=False,
            reserve=cost_guard.reserve,
            reviewer=no_provider,
        )

        assert result.unavailable == 2
        assert len(receipts) == 2
        assert receipts[0].id != receipts[1].id
        with SyncAsyncBridge() as bridge:
            client = cost_guard._client()
            assert int(bridge.run(client.get(daily_key)) or 0) == daily_before
            assert int(bridge.run(client.get(monthly_key)) or 0) == monthly_before
    finally:
        with SyncAsyncBridge() as bridge:
            client = cost_guard._client()
            for receipt in receipts:
                bridge.run(cost_guard.settle_reservation(receipt, consumed_units=0))
                bridge.run(client.delete(cost_guard._reservation_key(receipt.id)))
            bridge.run(client.aclose())
        cost_guard._redis_client = None
