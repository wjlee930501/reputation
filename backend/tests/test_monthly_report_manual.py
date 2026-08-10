"""병원별 월간 리포트 수동 생성.

월말 배치가 실패하면 그 병원은 다음 달 마지막 날까지 리포트가 비고, 종전 복구 경로는
`make monthly-report`(전체 병원 · 마지막 날에만 동작)뿐이었다. 여기서 검증하는 축은
**어느 달을 만드는가** — 배치 실패는 대개 달이 바뀐 뒤 발견되므로 기본값이 '지난달'이
아니면 복구 자체가 엉뚱한 달을 만든다.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import arrow
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.admin import operations
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.workers import tasks

_POSTGRES_URL = os.getenv(
    "TASK16_DATABASE_URL",
    "postgresql://reputation:reputation@localhost:5434/reputation_test",
)


class FakeSession:
    def __init__(self, hospital=None):
        self.hospital = hospital
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, _pk):
        return self.hospital

    def rollback(self):
        self.rolled_back = True


class FakeHospital:
    id = uuid.uuid4()
    name = "장편한외과의원"


@pytest.fixture
def captured_anchor(monkeypatch):
    """_build_monthly_report_for_hospital에 넘어간 anchor를 가로챈다."""
    seen: dict = {}

    def fake_build(_db, hospital, anchor):
        seen["hospital"] = hospital
        seen["anchor"] = anchor
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", fake_build)
    return seen


def _use_session(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)


def test_defaults_to_previous_month(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 3, 4, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id))

    anchor = captured_anchor["anchor"]
    assert (anchor.year, anchor.month) == (2026, 2)
    # 리포트 본문이 anchor.ceil("month")로 기간을 잡으므로 월말이어야 한다.
    assert anchor.day == 28
    assert result == {"status": "created", "year": 2026, "month": 2}


def test_explicit_period_is_honoured(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2025, 12)

    anchor = captured_anchor["anchor"]
    assert (anchor.year, anchor.month, anchor.day) == (2025, 12, 31)
    assert result["year"] == 2025
    assert result["month"] == 12


def test_january_default_rolls_back_to_previous_december(monkeypatch, captured_anchor):
    """연도 경계 — 1월에 지난달을 만들면 전년 12월이어야 한다."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 1, 2, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id))

    assert (result["year"], result["month"]) == (2025, 12)


def test_partial_period_is_rejected(monkeypatch, captured_anchor):
    """잘못된 요청은 예외가 아니라 상태로 돌려준다 — autoretry가 헛돌지 않게."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))

    assert tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, None) == {
        "status": "invalid_period"
    }
    assert tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), None, 2) == {
        "status": "invalid_period"
    }

    assert "anchor" not in captured_anchor


@pytest.mark.parametrize("offset_months", [0, 1, 6])
def test_current_and_future_months_are_rejected(monkeypatch, captured_anchor, offset_months):
    """이번 달 이후를 미리 만들면 빈 리포트 행이 월말 배치를 dedupe로 영구 차단한다."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    now = arrow.get(2026, 8, 4, tzinfo="Asia/Seoul")
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: now)
    target = now.shift(months=offset_months)

    result = tasks.generate_monthly_report_for_hospital(
        str(FakeHospital.id), target.year, target.month
    )

    assert result == {"status": "invalid_period"}
    assert "anchor" not in captured_anchor


def test_previous_month_is_still_allowed(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 8, 4, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, 7)

    assert result["status"] == "created"
    assert (captured_anchor["anchor"].year, captured_anchor["anchor"].month) == (2026, 7)


def test_existing_report_is_not_overwritten(monkeypatch):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", lambda *_a: "skipped_existing")

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, 2)

    assert result["status"] == "skipped_existing"


def test_explicit_rebuild_requests_a_new_version(monkeypatch):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    seen: dict = {}

    def fake_build(_db, _hospital, _anchor, *, rebuild=False):
        seen["rebuild"] = rebuild
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", fake_build)

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, 2, rebuild=True)

    assert result["status"] == "created"
    assert seen == {"rebuild": True}


def test_unknown_hospital_reports_instead_of_raising(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(None))

    result = tasks.generate_monthly_report_for_hospital(str(uuid.uuid4()))

    assert result == {"status": "hospital_not_found"}
    assert "anchor" not in captured_anchor


def _run_failing_attempt(monkeypatch, retries: int) -> tuple[FakeSession, list[dict]]:
    session = FakeSession(FakeHospital())
    _use_session(monkeypatch, session)
    alerts: list[dict] = []

    async def fake_ops_alert(**kwargs):
        alerts.append(kwargs)
        return True

    def boom(*_a):
        raise RuntimeError("pdf renderer down")

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", boom)
    monkeypatch.setattr(tasks.notifier, "notify_ops_alert", fake_ops_alert)

    task = tasks.generate_monthly_report_for_hospital
    task.push_request(retries=retries)
    try:
        with pytest.raises(RuntimeError):
            task(str(FakeHospital.id), 2026, 2)
    finally:
        task.pop_request()
    return session, alerts


def test_final_failure_rolls_back_without_legacy_direct_slack(monkeypatch):
    session, alerts = _run_failing_attempt(
        monkeypatch, tasks.generate_monthly_report_for_hospital.max_retries
    )

    assert session.rolled_back is True
    assert alerts == []


def test_intermediate_failure_stays_silent(monkeypatch):
    """재시도가 남았는데 매번 알리면 일시 장애 한 번에 Slack이 여러 번 울린다."""
    session, alerts = _run_failing_attempt(monkeypatch, 0)

    assert session.rolled_back is True
    assert alerts == []


@pytest.mark.asyncio
async def test_rebuild_requires_reason_and_idempotency_key() -> None:
    with pytest.raises(HTTPException) as missing_reason:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(),
            year=2025,
            month=12,
            rebuild=True,
            payload=None,
            db=AsyncMock(),
            idempotency_key="rebuild-key",
        )
    assert missing_reason.value.status_code == 400
    assert "이유" in str(missing_reason.value.detail)

    with pytest.raises(HTTPException) as missing_key:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(),
            year=2025,
            month=12,
            rebuild=True,
            payload=operations.MonthlyReportBuildRequest(reason="늦게 확인된 자료 반영"),
            db=AsyncMock(), idempotency_key=None,
        )
    assert missing_key.value.status_code == 400
    assert "요청 키" in str(missing_key.value.detail)


@pytest.mark.asyncio
async def test_rebuild_reason_is_sanitized_audited_and_replayed_safely(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED,
        idempotency_key="rebuild-key",
        request_payload={
            "source_type": "hospital",
            "source_id": str(hospital_id),
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id), 2025, 12, True],
            },
        },
    )
    db = AsyncMock()
    monkeypatch.setattr(
        operations,
        "_get_hospital_or_404",
        AsyncMock(return_value=SimpleNamespace(id=hospital_id)),
    )
    order: list[str] = []
    prepared_reasons: list[str] = []

    async def prepare(*_args, **kwargs):
        order.append("reason_audit_staged")
        prepared_reasons.append(kwargs["reason"])
        return True

    async def enqueue(*_args, **_kwargs):
        order.append("durable_dispatch")
        return SimpleNamespace(run=run, replayed=False)

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", enqueue)

    response = await operations.generate_monthly_report_operation(
        hospital_id,
        year=2025,
        month=12,
        rebuild=True,
        payload=operations.MonthlyReportBuildRequest(
            reason="자료 010-1234-5678 ae@example.com secret=raw-value 반영"
        ),
        db=db,
        idempotency_key="rebuild-key",
    )

    assert response["idempotent_replay"] is False
    assert order == ["reason_audit_staged", "durable_dispatch"]
    assert prepared_reasons == [
        "자료 [phone redacted] [email redacted] secret=[redacted] 반영"
    ]
    serialized_payload = str(run.request_payload)
    assert "010-1234-5678" not in serialized_payload
    assert "ae@example.com" not in serialized_payload
    assert "raw-value" not in serialized_payload
    assert "rebuild_reason" not in run.request_payload


@pytest.mark.asyncio
async def test_rebuild_reason_is_staged_before_a_dispatch_failure(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    order: list[str] = []
    monkeypatch.setattr(
        operations,
        "_get_hospital_or_404",
        AsyncMock(return_value=SimpleNamespace(id=hospital_id)),
    )

    async def prepare(*_args, **_kwargs):
        order.append("reason_audit_staged")
        return True

    async def fail_dispatch(*_args, **_kwargs):
        order.append("dispatch_failed")
        raise RuntimeError("simulated dispatch interruption")

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", fail_dispatch)

    with pytest.raises(RuntimeError, match="simulated dispatch interruption"):
        await operations.generate_monthly_report_operation(
            hospital_id,
            year=2025,
            month=12,
            rebuild=True,
            payload=operations.MonthlyReportBuildRequest(reason="늦게 확인된 자료 반영"),
            db=AsyncMock(),
            idempotency_key="rebuild-key",
        )
    assert order == ["reason_audit_staged", "dispatch_failed"]


@pytest.mark.asyncio
async def test_rebuild_audit_is_staged_without_committing_before_dispatch(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    db = AsyncMock()
    db.scalar.return_value = None
    audit = AsyncMock()
    monkeypatch.setattr(operations, "write_audit_log", audit)

    created = await operations._prepare_monthly_rebuild_audit(
        db,
        hospital_id=hospital_id,
        idempotency_key="stable-rebuild-key",
        year=2025,
        month=12,
        reason="자료 [phone redacted] [email redacted] secret=[redacted] 반영",
    )

    assert created is True
    assert audit.await_args.kwargs["detail"] == {
        "period_year": 2025,
        "period_month": 12,
        "reason": "자료 [phone redacted] [email redacted] secret=[redacted] 반영",
    }
    assert audit.await_args.kwargs["target_id"] != "stable-rebuild-key"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_replay_compares_append_only_reason_audit(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    expected = {
        "period_year": 2025,
        "period_month": 12,
        "reason": "늦게 확인된 자료 반영",
    }
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(detail=expected)
    monkeypatch.setattr(operations, "write_audit_log", AsyncMock())

    assert await operations._prepare_monthly_rebuild_audit(
        db,
        hospital_id=hospital_id,
        idempotency_key="same-key",
        year=2025,
        month=12,
        reason="늦게 확인된 자료 반영",
    ) is False

    with pytest.raises(HTTPException) as conflict:
        await operations._prepare_monthly_rebuild_audit(
            db,
            hospital_id=hospital_id,
            idempotency_key="same-key",
            year=2025,
            month=12,
            reason="다른 자료 반영",
        )
    assert conflict.value.status_code == 409


@pytest.fixture
def monthly_pg_session():
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_rebuild_run_links_new_report_version_and_records_validation_pending(
    monthly_pg_session: Session,
) -> None:
    # Given: a prior report and a queued rebuild run for the same month
    hospital = Hospital(name="월간 복구 검증 의원", slug=f"monthly-run-{uuid.uuid4().hex}")
    monthly_pg_session.add(hospital)
    monthly_pg_session.flush()
    previous = MonthlyReport(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=7,
        report_type="MONTHLY",
        version=1,
        quality="COMPLETE",
        planned_count=20,
        success_count=20,
        failed_count=0,
    )
    monthly_pg_session.add(previous)
    monthly_pg_session.flush()
    rebuilt = MonthlyReport(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=7,
        report_type="MONTHLY",
        version=2,
        supersedes_report_id=previous.id,
        quality="COMPLETE",
        planned_count=20,
        success_count=20,
        failed_count=0,
    )
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.RUNNING,
        attempt_count=1,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"source_type": "hospital", "source_id": str(hospital.id)},
    )
    monthly_pg_session.add_all((rebuilt, run))
    monthly_pg_session.commit()

    # When: the worker records the completed rebuild
    tasks._finish_monthly_operation_run(
        monthly_pg_session, run.id, hospital.id, 2026, 7, "created"
    )

    # Then: the run is durable, version-linked, and explicitly not customer-ready
    monthly_pg_session.refresh(run)
    assert run.state == OperationRunState.SUCCEEDED
    assert run.result_summary is not None
    assert run.result_summary["stage"] == "ARTIFACT_VALIDATION_PENDING"
    assert run.result_summary["milestones"] == [
        "COVERAGE_COMPLETE",
        "ARTIFACT_VALIDATION_PENDING",
    ]
    assert run.result_summary["report_version"] == 2
    assert run.result_summary["supersedes_report_id"] == str(previous.id)
    assert "CUSTOMER_READY" not in str(run.result_summary)


def test_manual_run_moves_from_queue_to_running_before_report_build(
    monthly_pg_session: Session,
) -> None:
    hospital = Hospital(name="수동 작업 상태 의원", slug=f"manual-running-{uuid.uuid4().hex}")
    monthly_pg_session.add(hospital)
    monthly_pg_session.flush()
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED,
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"source_type": "hospital"},
    )
    monthly_pg_session.add(run)
    monthly_pg_session.commit()

    tasks._mark_monthly_operation_run_running(monthly_pg_session, run.id, 2026, 7)

    monthly_pg_session.refresh(run)
    assert run.state == OperationRunState.RUNNING
    assert run.attempt_count == 1
    assert run.started_at is not None
    assert run.heartbeat_at is not None
    assert run.result_summary == {
        "stage": "RUNNING",
        "period_year": 2026,
        "period_month": 7,
    }


def test_scheduled_batch_is_partial_and_preserves_successful_hospital(
    monthly_pg_session: Session, monkeypatch
) -> None:
    # Given: two active hospitals where one report build fails
    success_hospital = Hospital(
        name="월간 성공 의원",
        slug=f"monthly-success-{uuid.uuid4().hex}",
        status="ACTIVE",
    )
    failed_hospital = Hospital(
        name="월간 실패 의원",
        slug=f"monthly-failed-{uuid.uuid4().hex}",
        status="ACTIVE",
    )
    monthly_pg_session.add_all((success_hospital, failed_hospital))
    monthly_pg_session.commit()

    class SessionContext:
        def __enter__(self):
            return monthly_pg_session

        def __exit__(self, *_exc):
            return False

    def fake_build(db, hospital, now):
        if hospital.id == failed_hospital.id:
            raise RuntimeError("renderer unavailable")
        db.add(
            MonthlyReport(
                hospital_id=hospital.id,
                period_year=now.year,
                period_month=now.month,
                report_type="MONTHLY",
                version=1,
                quality="COMPLETE",
                planned_count=20,
                success_count=20,
                failed_count=0,
            )
        )
        db.commit()
        return "created"

    monkeypatch.setattr(tasks, "SyncSessionLocal", SessionContext)
    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", fake_build)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: arrow.get(2026, 7, 31, 21, tzinfo="Asia/Seoul"),
    )

    # When: the monthly batch processes both hospitals
    result = tasks.run_monthly_reports()

    # Then: the batch is partial, the success remains committed, and each hospital has truth
    assert result == {
        "status": "PARTIAL",
        "total_count": 2,
        "success_count": 1,
        "failure_count": 1,
    }
    runs = list(
        monthly_pg_session.execute(
            select(OperationRun).where(
                OperationRun.hospital_id.in_((success_hospital.id, failed_hospital.id)),
                OperationRun.operation_type == "SCHEDULED_MONTHLY_REPORT",
            )
        ).scalars()
    )
    assert {run.hospital_id: run.state for run in runs} == {
        success_hospital.id: OperationRunState.SUCCEEDED,
        failed_hospital.id: OperationRunState.FAILED,
    }
    assert monthly_pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == success_hospital.id)
    ).scalar_one().version == 1


def test_scheduled_replay_keeps_a_prior_failure_visible(
    monthly_pg_session: Session, monkeypatch
) -> None:
    hospital = Hospital(
        name="월간 재실행 실패 의원",
        slug=f"monthly-replay-failed-{uuid.uuid4().hex}",
        status="ACTIVE",
    )
    monthly_pg_session.add(hospital)
    monthly_pg_session.flush()
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="SCHEDULED_MONTHLY_REPORT",
        state=OperationRunState.FAILED,
        idempotency_key=f"scheduled:{hospital.id}:2026-07",
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        request_payload={"source_type": "MONTHLY_SCHEDULE", "source_id": "2026-07"},
        result_summary={"stage": "FAILED", "period_year": 2026, "period_month": 7},
    )
    monthly_pg_session.add(run)
    monthly_pg_session.commit()

    class SessionContext:
        def __enter__(self):
            return monthly_pg_session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", SessionContext)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: arrow.get(2026, 7, 31, 21, tzinfo="Asia/Seoul"),
    )

    result = tasks.run_monthly_reports()

    assert result == {
        "status": "FAILED",
        "total_count": 1,
        "success_count": 0,
        "failure_count": 1,
    }
    monthly_pg_session.refresh(run)
    assert run.state == OperationRunState.FAILED
    assert run.result_summary["stage"] == "FAILED"


def test_stale_scheduled_run_is_terminalized_with_a_manual_recovery_action(
    monthly_pg_session: Session,
) -> None:
    hospital = Hospital(name="월간 중단 의원", slug=f"monthly-stale-{uuid.uuid4().hex}")
    monthly_pg_session.add(hospital)
    monthly_pg_session.flush()
    stale_at = datetime.now(timezone.utc) - timedelta(hours=2)
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="SCHEDULED_MONTHLY_REPORT",
        state=OperationRunState.RUNNING,
        idempotency_key=f"scheduled:{hospital.id}:2026-07",
        attempt_count=1,
        heartbeat_at=stale_at,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"source_type": "MONTHLY_SCHEDULE", "source_id": "2026-07"},
        result_summary={"stage": "RUNNING", "period_year": 2026, "period_month": 7},
    )
    monthly_pg_session.add(run)
    monthly_pg_session.commit()

    run_id, replayed = tasks._start_scheduled_monthly_operation_run(
        monthly_pg_session,
        hospital,
        arrow.get(2026, 7, 31, 21, tzinfo="Asia/Seoul"),
    )

    monthly_pg_session.refresh(run)
    assert (run_id, replayed) == (run.id, True)
    assert run.state == OperationRunState.FAILED
    assert run.result_summary["stage"] == "FAILED"
    assert run.safe_error_code == "MONTHLY_REPORT_RUN_INTERRUPTED"
    assert "다시 만들기" in (run.safe_error_message or "")


def test_failed_rebuild_does_not_misreport_the_preserved_prior_version_as_success(
    monthly_pg_session: Session,
) -> None:
    # Given: an old successful report and a new rebuild run that failed
    hospital = Hospital(name="재생성 실패 의원", slug=f"failed-rebuild-{uuid.uuid4().hex}")
    monthly_pg_session.add(hospital)
    monthly_pg_session.flush()
    previous = MonthlyReport(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=6,
        report_type="MONTHLY",
        version=1,
        quality="COMPLETE",
        planned_count=20,
        success_count=20,
        failed_count=0,
    )
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.RUNNING,
        attempt_count=1,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"source_type": "hospital", "source_id": str(hospital.id)},
    )
    monthly_pg_session.add_all((previous, run))
    monthly_pg_session.commit()

    # When: the rebuild is marked failed
    tasks._fail_monthly_operation_run(monthly_pg_session, run.id, hospital.id, 2026, 6)

    # Then: the old report stays intact but the new attempt remains visibly failed
    monthly_pg_session.refresh(run)
    assert run.state == OperationRunState.FAILED
    assert run.result_summary is not None
    assert run.result_summary["stage"] == "FAILED"
    assert run.result_summary["report_id"] == str(previous.id)
    assert run.safe_error_message == "월간 리포트를 만들지 못했습니다. 다시 만들기를 시도해 주세요."
