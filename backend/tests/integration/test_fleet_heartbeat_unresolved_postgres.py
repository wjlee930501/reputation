"""일일 요약의 '복구 근거가 아직 없는 작업 실패'를 실제 실행 계보로 센다(10/4 77건).

`collect_fleet_facts`는 지난 24시간의 FAILED/PARTIAL 실행 가운데 사고 링크
(`Incident.operation_run_id`)나 명시적 재시도 계보(`parent_run_id`=실패 실행, 같은 종류·병원의
더 늦은 성공)가 없는 것을 '복구 근거 없음'으로 센다. 실제 자동화는 그 계보를 남기지 않는다.

- 생성: 23:00·스윕 배치(`NIGHTLY_CONTENT_GENERATION`, 병원 없음) → 글마다 `GENERATE_CONTENT_ITEM`
  (parent=배치, `_dispatch.target_id`=글) → 시도 기록 `REGENERATE_CONTENT(_IMAGE)`(parent=그 실행,
  lease-active·stale-claim은 parent=배치). 다음 스윕의 재시도는 **새 배치 아래 새 실행**이라 옛
  실패를 가리키지 않고, 사고의 `operation_run_id`는 다음 실패·성공으로 옮겨 간다.
- 월간: `MONTHLY_REPORT_BATCH`(병원 없음, `source_id`=YYYY-MM)는 1~7일 매일 같은 기간을 다시 돈다.
  병원별 `SCHEDULED_MONTHLY_REPORT`·`GENERATE_MONTHLY_REPORT`는 배치에 연결되지 않는다.

그래서 이미 같은 대상에서 성공했거나 자동 재시도가 예산 안에서 진행 중인 실패까지 사람의 일로
셌다. 새 규칙은 **같은 대상**(같은 글 · 같은 병원·같은 기간 · 배치라면 실패한 글 하나하나)의
구체적 근거만 인정한다. 다른 글·다른 병원·다른 달·더 이른 성공은 근거가 아니고, 소진·사람
차례(OPERATOR_REQUIRED, 기한 지난 RETRYING)는 '진행 중'이 아니다. 원시 `failed_runs`는 그대로다.

DB는 공유된다. 모든 행은 롤백되는 트랜잭션 안에 있고, 시각은 다른 테스트의 커밋과 겹치지 않는
먼 미래(NOW)를 쓰며, 각 시나리오는 행을 넣기 전후의 차이만 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, OperationRun
from app.models.report import MonthlyReport
from app.services.fleet_heartbeat import collect_fleet_facts
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

NOW = datetime(2031, 3, 4, 0, 0, tzinfo=UTC)  # 09:00 KST
FAILED_AT = NOW - timedelta(hours=20)
RETRIED_AT = NOW - timedelta(hours=10)
LATER = NOW - timedelta(hours=4)
TERMINAL = {"SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"}


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


class _Facts:
    """Before/after deltas, so committed rows of other tests never matter."""

    def __init__(self, db):
        self.db = db
        self.base = collect_fleet_facts(db, now=NOW)

    def delta(self):
        self.db.flush()
        after = collect_fleet_facts(self.db, now=NOW)
        return (
            after.failed_runs - self.base.failed_runs,
            after.unresolved_failed_runs - self.base.unresolved_failed_runs,
        )


def _hospital(db) -> Hospital:
    hospital = Hospital(
        name="일일요약 가상의원",
        slug=f"heartbeat-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _item(db, hospital, *, attempt=None, first_published_at=None) -> ContentItem:
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2031, 3, 1)
    )
    db.add(schedule)
    db.flush()
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        scheduled_date=date(2031, 3, 5),
        title="가상 글",
        status=ContentStatus.PUBLISHED if first_published_at else ContentStatus.DRAFT,
        first_published_at=first_published_at,
        published_at=first_published_at,
        essence_check_summary={"generation_attempt": attempt} if attempt else None,
    )
    db.add(item)
    db.flush()
    return item


def _run(
    db,
    operation_type,
    state,
    *,
    at,
    hospital=None,
    parent=None,
    payload=None,
    result=None,
    idempotency_key=None,
    counts=None,
    completed=None,
    error_code=None,
) -> OperationRun:
    """`at` is the request time; `completed` (default `at`) is when a terminal run finished."""
    done = state in TERMINAL
    finished = (completed or at) if done else None
    total, success, failure = counts or (
        1,
        int(state == "SUCCEEDED"),
        int(state in ("FAILED", "PARTIAL")),
    )
    run = OperationRun(
        id=uuid.uuid4(),
        operation_type=operation_type,
        state=state,
        hospital_id=hospital.id if hospital else None,
        parent_run_id=parent.id if parent else None,
        idempotency_key=idempotency_key or f"heartbeat-test:{uuid.uuid4()}",
        request_payload=payload or {},
        result_summary=result,
        attempt_count=1,
        total_count=total,
        success_count=success,
        failure_count=failure,
        skipped_count=0,
        requested_at=at,
        started_at=at,
        completed_at=finished,
        lease_owner=None if done else "heartbeat-test",
        lease_expires_at=None if done else at + timedelta(hours=2),
        created_at=at,
        updated_at=finished or at,
        safe_error_code=error_code or (None if state == "SUCCEEDED" else "TEST_FAILURE"),
        version=1,
    )
    db.add(run)
    db.flush()
    return run


def _item_payload(item, *task_args):
    args = task_args or (str(item.id),)
    return build_request_payload(DispatchPayload("content_item", str(item.id), "content", args))


def _batch(db, at, items: dict, state="SUCCEEDED", *, error_code=None) -> OperationRun:
    """The 23:00/sweep dispatcher: one hospital-less run whose items are dispatch outcomes.

    A FAILED/PARTIAL batch is terminalized by its recorder (`GenerationBatchRecorder._persist`)
    with `CONTENT_GENERATION_PARTIAL` unless `error_code` says another path ended it.
    """
    states = [entry["state"] for entry in items.values()]
    failed = states.count("FAILED") + states.count("PARTIAL")
    if state in ("FAILED", "PARTIAL"):
        error_code = error_code or "CONTENT_GENERATION_PARTIAL"
    return _run(
        db,
        "NIGHTLY_CONTENT_GENERATION",
        state,
        at=at,
        payload={"window_start": "2031-03-04", "window_end": "2031-03-06"},
        result={"items": items},
        idempotency_key=f"nightly:{uuid.uuid4()}",
        counts=(len(states), states.count("SUCCEEDED"), failed),
        error_code=error_code,
    )


def _attempt(db, item, state, at, *, batch=None):
    """One dispatched generation of `item`: batch → GENERATE_CONTENT_ITEM → REGENERATE_CONTENT."""
    hospital = db.get(Hospital, item.hospital_id)
    batch = batch or _batch(db, at, {str(item.id): {"state": "RUNNING"}})
    child = _run(
        db,
        "GENERATE_CONTENT_ITEM",
        state,
        at=at,
        hospital=hospital,
        parent=batch,
        payload=_item_payload(item, str(item.id), str(uuid.uuid4()), None),
        result={"items": {str(item.id): {"state": state}}},
    )
    regenerate = _run(
        db,
        "REGENERATE_CONTENT",
        state,
        at=at,
        hospital=hospital,
        parent=child,
        payload=_item_payload(item),
        result={"items": {str(item.id): {"state": state}}},
    )
    return batch, child, regenerate


def _lease_blocked_batch(
    db, at, failed_items, *, state="FAILED", succeeded_items=(), error_code=None
):
    """A batch whose slots were held by a live lease: FAILED item + REGENERATE_CONTENT off the batch."""
    items = {
        str(item.id): {"state": "FAILED", "safe_error_code": "GENERATION_LEASE_ACTIVE"}
        for item in failed_items
    }
    items.update({str(item.id): {"state": "SUCCEEDED"} for item in succeeded_items})
    return _batch(db, at, items, state=state, error_code=error_code)


def _lease_blocked_run(db, batch, item, at):
    return _run(
        db,
        "REGENERATE_CONTENT",
        "FAILED",
        at=at,
        hospital=db.get(Hospital, item.hospital_id),
        parent=batch,
        payload=_item_payload(item),
        result={"items": {str(item.id): {"state": "FAILED"}}},
    )


def _generation_incident(db, item, state, run, *, sla_due_at=None) -> Incident:
    closed = state in ("RECOVERED", "ACKNOWLEDGED")
    incident = Incident(
        hospital_id=item.hospital_id,
        dedupe_key=f"heartbeat-test:{uuid.uuid4()}",
        incident_type="CONTENT_GENERATION_FAILED",
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type="CONTENT_GENERATION",
        source_id=str(item.id),
        operation_run_id=run.id,
        next_action="테스트",
        admin_path="/hospitals",
        first_seen_at=FAILED_AT,
        last_seen_at=run.completed_at or FAILED_AT,
        sla_due_at=sla_due_at,
        recovered_at=LATER if closed else None,
        acknowledged_at=LATER if state == "ACKNOWLEDGED" else None,
    )
    db.add(incident)
    db.flush()
    return incident


def _pending_attempt(next_retry_at=NOW + timedelta(hours=3)) -> dict:
    """A sample failure the 12·18·22·01·04·07 sweeps still own (`retry_is_due` budget)."""
    return {
        "context": "body",
        "reason": "GENERATION_REJECTED",
        "observed_at": RETRIED_AT.isoformat(),
        "first_observed_at": FAILED_AT.isoformat(),
        "attempt_period": "2031-03-04",
        "retry_class": "SAMPLE_RECOVERABLE",
        "exhausted_days": 0,
        "attempt_count": 1,
        "provider_attempt_count": 1,
        "guard_deferral_count": 0,
        "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
    }


def _exhausted_attempt() -> dict:
    return {
        **_pending_attempt(None),
        "retry_class": "OPERATOR_REQUIRED",
        "exhausted_days": 3,
    }


def _monthly_batch(db, at, period, state, *, hospitals=1, completed=None) -> OperationRun:
    """`_finish_monthly_report_batch_run`: SUCCEEDED only when every eligible hospital is done."""
    succeeded = hospitals if state == "SUCCEEDED" else 0
    failed = 0 if state == "SUCCEEDED" else hospitals
    return _run(
        db,
        "MONTHLY_REPORT_BATCH",
        state,
        at=at,
        payload={"source_type": "MONTHLY_REPORT_BATCH", "source_id": period},
        result={"status": state, "total_count": hospitals, "success_count": succeeded,
                "failure_count": failed},
        idempotency_key=f"monthly-report-batch:{uuid.uuid4()}",
        counts=(hospitals, succeeded, failed),
        completed=completed,
    )


# `_finish_monthly_operation_run`: stage → (run state, (total, success, failure)).
# `skipped_existing` is SUCCEEDED too, but built nothing (stage EXISTING, success 0).
_MONTHLY_STAGES = {
    "ARTIFACT_VALIDATED": ("SUCCEEDED", (1, 1, 0)),
    "EXISTING": ("SUCCEEDED", (1, 0, 0)),
    "BLOCKED": ("PARTIAL", (1, 0, 1)),
    "FAILED": ("FAILED", (1, 0, 1)),
}
_DEFAULT_MONTHLY_STAGE = {"SUCCEEDED": "ARTIFACT_VALIDATED", "PARTIAL": "BLOCKED", "FAILED": "FAILED"}


def _monthly_outcome(state, stage):
    stage = stage or _DEFAULT_MONTHLY_STAGE[state]
    run_state, counts = _MONTHLY_STAGES[stage]
    assert run_state == state, (state, stage)
    return stage, counts


def _scheduled_report_run(
    db, hospital, period, state, at, *, stage=None, completed=None
) -> OperationRun:
    year, month = (int(part) for part in period.split("-"))
    stage, counts = _monthly_outcome(state, stage)
    return _run(
        db,
        "SCHEDULED_MONTHLY_REPORT",
        state,
        at=at,
        hospital=hospital,
        payload={"source_type": "MONTHLY_SCHEDULE", "source_id": period},
        result={"stage": stage, "period_year": year, "period_month": month},
        idempotency_key=f"scheduled:{hospital.id}:{period}",
        counts=counts,
        completed=completed,
    )


def _coverage_recovery_run(
    db, hospital, period, state, at, *, stage=None, completed=None
) -> OperationRun:
    # 기간은 결과의 period_year/month가 아니라 커버리지 복구 키에서 읽히게 둔다.
    year, month = (int(part) for part in period.split("-"))
    stage, counts = _monthly_outcome(state, stage)
    return _run(
        db,
        "GENERATE_MONTHLY_REPORT",
        state,
        at=at,
        hospital=hospital,
        payload=build_request_payload(
            DispatchPayload("hospital", str(hospital.id), "reports",
                            (str(hospital.id), year, month, True, True))
        ),
        result={"stage": stage},
        idempotency_key=f"coverage-recovery:{hospital.id}:{period}",
        counts=counts,
        completed=completed,
    )


def _monthly_report(db, hospital, period, at):
    year, month = (int(part) for part in period.split("-"))
    db.add(MonthlyReport(hospital_id=hospital.id, period_year=year, period_month=month,
                         report_type="MONTHLY", created_at=at))
    db.flush()


# ── 생성: 같은 글의 근거가 있으면 빠진다 (RED) ────────────────────────────────────────


@pytest.mark.parametrize("evidence", ["later_generation_succeeded", "published_after"])
def test_a_regenerate_failure_resolved_by_the_same_item_later_is_not_unresolved(db, evidence):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(
        db, hospital, first_published_at=LATER if evidence == "published_after" else None
    )
    _attempt(db, item, "FAILED", FAILED_AT)
    if evidence == "later_generation_succeeded":
        # 다음 스윕이 새 배치 아래 같은 글을 다시 만들었다. 사고는 그 성공으로 옮겨 닫혔다.
        _, _, success = _attempt(db, item, "SUCCEEDED", RETRIED_AT)
        _generation_incident(db, item, "ACKNOWLEDGED", success)

    failed, unresolved = facts.delta()

    assert failed == 2  # 원시 실패 수는 그대로 센다.
    assert unresolved == 0


def test_a_failure_whose_automatic_retry_is_still_in_progress_is_not_unresolved(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital, attempt=_pending_attempt())
    _attempt(db, item, "FAILED", FAILED_AT)
    _, _, latest = _attempt(db, item, "FAILED", RETRIED_AT)
    # 재시도 정책이 이 글을 소유한다: 예산 안의 다음 시도 시각과 기한 안의 RETRYING 사고.
    _generation_incident(db, item, "RETRYING", latest, sla_due_at=NOW + timedelta(hours=3))

    failed, unresolved = facts.delta()

    assert failed == 4
    assert unresolved == 0


@pytest.mark.parametrize("batch_state", ["FAILED", "PARTIAL"])
def test_a_nightly_batch_whose_every_failed_item_was_later_generated_is_not_unresolved(
    db, batch_state
):
    facts = _Facts(db)
    hospital = _hospital(db)
    blocked = _item(db, hospital)
    succeeded = [_item(db, hospital)] if batch_state == "PARTIAL" else []
    batch = _lease_blocked_batch(
        db, FAILED_AT, [blocked], state=batch_state, succeeded_items=succeeded
    )
    _lease_blocked_run(db, batch, blocked, FAILED_AT)
    _, _, success = _attempt(db, blocked, "SUCCEEDED", RETRIED_AT)
    _generation_incident(db, blocked, "ACKNOWLEDGED", success)

    failed, unresolved = facts.delta()

    assert failed == 2  # 배치 + lease-active 시도 기록
    assert unresolved == 0


# ── 생성: 근거가 없으면 그대로 센다 ───────────────────────────────────────────────────


def test_a_nightly_batch_with_an_unexplained_failed_item_still_counts(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    recovered, unexplained = _item(db, hospital), _item(db, hospital)
    batch = _lease_blocked_batch(db, FAILED_AT, [recovered, unexplained])
    _lease_blocked_run(db, batch, recovered, FAILED_AT)
    _, _, success = _attempt(db, recovered, "SUCCEEDED", RETRIED_AT)
    _generation_incident(db, recovered, "ACKNOWLEDGED", success)
    # `unexplained`는 배치 기록에만 실패로 남았다(시도 실행·사고·재시도·이후 성공 없음).

    failed, unresolved = facts.delta()

    assert failed == 2
    # 설명되지 않은 글이 남은 배치는 센다. 회복된 글의 시도 기록은 빠진다.
    assert unresolved == 1


def test_a_batch_with_an_unexplained_failed_child_run_counts_the_batch_and_the_child(db):
    """설명 안 된 글이 남은 배치와 그 글의 시도 기록을 각자 센다(합쳐 하나로 세지 않는다)."""
    facts = _Facts(db)
    hospital = _hospital(db)
    recovered, unexplained = _item(db, hospital), _item(db, hospital)
    batch = _lease_blocked_batch(db, FAILED_AT, [recovered, unexplained])
    _lease_blocked_run(db, batch, recovered, FAILED_AT)
    _lease_blocked_run(db, batch, unexplained, FAILED_AT)
    _, _, success = _attempt(db, recovered, "SUCCEEDED", RETRIED_AT)
    _generation_incident(db, recovered, "ACKNOWLEDGED", success)

    failed, unresolved = facts.delta()

    assert failed == 3
    assert unresolved == 2


@pytest.mark.parametrize("shape", ["operator_required", "stale_retrying", "retrying_without_deadline"])
def test_an_exhausted_retry_is_not_in_progress(db, shape):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital, attempt=_exhausted_attempt())
    _attempt(db, item, "FAILED", FAILED_AT)
    _, _, latest = _attempt(db, item, "FAILED", RETRIED_AT)
    if shape == "operator_required":
        _generation_incident(db, item, "OPEN", latest)
    elif shape == "stale_retrying":
        # 기한이 지난 RETRYING은 운영센터 규칙(`requires_operator_action`)상 사람의 일이다.
        _generation_incident(db, item, "RETRYING", latest, sla_due_at=NOW - timedelta(days=2))
    else:
        # 기한 없는 RETRYING은 끝이 정해지지 않아 '진행 중'의 근거가 아니다.
        _generation_incident(db, item, "RETRYING", latest, sla_due_at=None)

    failed, unresolved = facts.delta()

    assert failed == 4
    # 사고가 직접 가리키는 마지막 시도 기록만 설명된다(기존 규칙).
    assert unresolved == 3


@pytest.mark.parametrize("shape", ["another_item", "another_hospital", "earlier_success"])
def test_another_target_or_an_earlier_success_is_not_recovery_proof(db, shape):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital)
    if shape == "earlier_success":
        _attempt(db, item, "SUCCEEDED", FAILED_AT - timedelta(hours=2))
    _attempt(db, item, "FAILED", FAILED_AT)
    if shape == "another_item":
        _attempt(db, _item(db, hospital), "SUCCEEDED", RETRIED_AT)
    elif shape == "another_hospital":
        _attempt(db, _item(db, _hospital(db)), "SUCCEEDED", RETRIED_AT)

    failed, unresolved = facts.delta()

    assert failed == 2
    assert unresolved == 2


def test_explicit_retry_lineage_still_resolves(db):
    """운영센터 재시도(parent_run_id=실패 실행, 같은 종류·병원)의 성공은 기존대로 근거다."""
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital)
    failed_run = _run(
        db, "REGENERATE_CONTENT", "FAILED", at=FAILED_AT, hospital=hospital,
        payload=_item_payload(item),
    )
    _run(
        db, "REGENERATE_CONTENT", "SUCCEEDED", at=RETRIED_AT, hospital=hospital,
        parent=failed_run, payload=_item_payload(item),
    )

    assert facts.delta() == (1, 0)


# ── 월간: 같은 병원·같은 기간의 근거 ───────────────────────────────────────────────────


def test_a_monthly_batch_whose_period_later_closed_is_not_unresolved(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    period = "2031-02"
    _monthly_batch(db, FAILED_AT, period, "FAILED")
    # 병원별 실행은 재사용돼 같은 기간에서 성공했고, 리포트가 생겼고, 다음 날 배치가 마감했다.
    _scheduled_report_run(db, hospital, period, "SUCCEEDED", RETRIED_AT)
    _monthly_report(db, hospital, period, RETRIED_AT)
    _monthly_batch(db, LATER, period, "SUCCEEDED")

    failed, unresolved = facts.delta()

    assert failed == 1
    assert unresolved == 0


def test_a_hospital_report_run_later_succeeded_for_the_same_period_is_not_unresolved(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    period = "2031-02"
    _scheduled_report_run(db, hospital, period, "FAILED", FAILED_AT)
    # 자동 커버리지 복구(다른 실행 종류)가 같은 병원·같은 기간의 리포트를 만들었다.
    _coverage_recovery_run(db, hospital, period, "SUCCEEDED", RETRIED_AT)
    _monthly_report(db, hospital, period, RETRIED_AT)

    failed, unresolved = facts.delta()

    assert failed == 1
    assert unresolved == 0


@pytest.mark.parametrize("shape", ["another_month", "earlier_success", "batch_still_partial"])
def test_another_month_or_an_earlier_close_is_not_monthly_batch_proof(db, shape):
    facts = _Facts(db)
    hospital = _hospital(db)
    period = "2031-02"
    if shape == "earlier_success":
        _monthly_batch(db, FAILED_AT - timedelta(days=1), period, "SUCCEEDED")
    _monthly_batch(db, FAILED_AT, period, "FAILED")
    if shape == "another_month":
        _scheduled_report_run(db, hospital, "2031-01", "SUCCEEDED", RETRIED_AT)
        _monthly_report(db, hospital, "2031-01", RETRIED_AT)
        _monthly_batch(db, LATER, "2031-01", "SUCCEEDED")
    elif shape == "batch_still_partial":
        # 다음 날 같은 기간 배치도 막힌 병원이 남아 PARTIAL이다 — 둘 다 센다.
        _monthly_batch(db, LATER, period, "PARTIAL")

    failed, unresolved = facts.delta()

    expected = 2 if shape == "batch_still_partial" else 1
    assert (failed, unresolved) == (expected, expected)


@pytest.mark.parametrize("shape", ["another_hospital", "another_month"])
def test_a_hospital_report_failure_needs_its_own_hospital_and_period(db, shape):
    facts = _Facts(db)
    hospital = _hospital(db)
    _scheduled_report_run(db, hospital, "2031-02", "FAILED", FAILED_AT)
    if shape == "another_hospital":
        other = _hospital(db)
        _coverage_recovery_run(db, other, "2031-02", "SUCCEEDED", RETRIED_AT)
        _monthly_report(db, other, "2031-02", RETRIED_AT)
    else:
        _coverage_recovery_run(db, hospital, "2031-01", "SUCCEEDED", RETRIED_AT)
        _monthly_report(db, hospital, "2031-01", RETRIED_AT)

    assert facts.delta() == (1, 1)


# ── 근거는 실제로 만든 성공, 앞뒤는 종료 시각으로 잰다 ─────────────────────────────────


def test_a_later_success_that_built_nothing_is_not_monthly_report_proof(db):
    """`skipped_existing`도 SUCCEEDED지만 아무것도 만들지 않았다(stage EXISTING, 성공 0건)."""
    facts = _Facts(db)
    hospital = _hospital(db)
    period = "2031-02"
    # 정기 마감이 리포트를 만들었지만 검증을 통과하지 못했다(BLOCKED).
    _scheduled_report_run(db, hospital, period, "PARTIAL", FAILED_AT)
    _monthly_report(db, hospital, period, FAILED_AT)
    # rebuild 없는 수동 생성이 이미 있는 리포트를 보고 건너뛰었다.
    _coverage_recovery_run(db, hospital, period, "SUCCEEDED", RETRIED_AT, stage="EXISTING")

    assert facts.delta() == (1, 1)


def test_a_later_monthly_batch_that_covered_no_hospital_is_not_batch_proof(db):
    facts = _Facts(db)
    period = "2031-02"
    _monthly_batch(db, FAILED_AT, period, "FAILED")
    # 대상 병원이 0곳인 SUCCEEDED는 아무 병원도 끝냈다고 확인하지 않았다.
    _monthly_batch(db, LATER, period, "SUCCEEDED", hospitals=0)

    assert facts.delta() == (1, 1)


@pytest.mark.parametrize("family", ["monthly_report", "content_item"])
def test_a_failure_that_finished_after_the_other_success_still_counts(db, family):
    """제자리에서 다시 열린 실행: 요청은 먼저였지만 실패는 다른 실행의 성공 뒤에 끝났다."""
    facts = _Facts(db)
    hospital = _hospital(db)
    if family == "monthly_report":
        period = "2031-02"
        _scheduled_report_run(db, hospital, period, "FAILED", FAILED_AT, completed=LATER)
        _coverage_recovery_run(db, hospital, period, "SUCCEEDED", RETRIED_AT)
    else:
        item = _item(db, hospital)
        batch = _batch(db, FAILED_AT, {str(item.id): {"state": "RUNNING"}})
        _run(
            db, "GENERATE_CONTENT_ITEM", "FAILED", at=FAILED_AT, completed=LATER,
            hospital=hospital, parent=batch,
            payload=_item_payload(item, str(item.id), str(uuid.uuid4()), None),
        )
        _attempt(db, item, "SUCCEEDED", RETRIED_AT)

    assert facts.delta() == (1, 1)


def test_a_reopened_success_that_finished_after_the_failure_is_proof(db):
    """정기 마감 행은 제자리에서 다시 열린다 — 요청이 더 이르더라도 실패 뒤에 끝난 성공이 근거다."""
    facts = _Facts(db)
    hospital = _hospital(db)
    period = "2031-02"
    _scheduled_report_run(
        db, hospital, period, "SUCCEEDED", FAILED_AT - timedelta(hours=2), completed=LATER
    )
    _coverage_recovery_run(db, hospital, period, "FAILED", FAILED_AT)

    assert facts.delta() == (1, 0)


def test_a_monthly_batch_failure_that_finished_after_the_later_close_still_counts(db):
    facts = _Facts(db)
    period = "2031-02"
    # 같은 Celery 작업으로 다시 돈 배치 행: 요청은 처음 그대로, 실패는 다른 배치의 마감 뒤.
    _monthly_batch(db, FAILED_AT, period, "FAILED", completed=LATER)
    _monthly_batch(db, RETRIED_AT, period, "SUCCEEDED")

    assert facts.delta() == (1, 1)


# ── 배치: 설명은 기록된 실패 하나하나와 그 글의 본문 성공이어야 한다 ─────────────────────


def test_a_partial_batch_with_no_failed_item_listed_still_counts(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital)
    # 건너뛴 글만 있는 PARTIAL — 설명할 실패 목록이 없으니 근거도 없다.
    _batch(db, FAILED_AT, {str(item.id): {"state": "SKIPPED"}}, state="PARTIAL")

    assert facts.delta() == (1, 1)


def test_an_image_only_success_is_not_batch_proof(db):
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital)
    _lease_blocked_batch(db, FAILED_AT, [item])
    # 이미지 교체 성공은 본문이 생겼다는 근거가 아니다.
    _run(
        db, "REGENERATE_CONTENT_IMAGE", "SUCCEEDED", at=RETRIED_AT, hospital=hospital,
        payload=_item_payload(item),
    )

    assert facts.delta() == (1, 1)


def test_a_batch_ended_outside_its_recorder_still_counts(db):
    """신호·스윕이 끝낸 배치는 목록 밖의 글이 남았을 수 있다 — 기록된 실패가 설명돼도 센다."""
    facts = _Facts(db)
    hospital = _hospital(db)
    item = _item(db, hospital)
    _lease_blocked_batch(db, FAILED_AT, [item], error_code="TASK_FAILED")
    _attempt(db, item, "SUCCEEDED", RETRIED_AT)

    assert facts.delta() == (1, 1)
