"""경쟁 병원 반영 체인 — 경쟁사 재판정(REJUDGE_COMPETITORS) → 경쟁 현황 부분 재생성(COMPETITOR_REFRESH).

한 번의 Admin 행동(`POST /admin/hospitals/{id}/reports/competitors/refresh`)이 이 태스크 하나를 큐에 넣고,
태스크가 끝나면 **같은 체인**(자식 OperationRun, `parent_run_id` = 이 실행)으로 월간 보고서 템플릿 갱신을
`competitor_refresh=True`로 부른다. 재판정 셀 일부가 미확정(null)으로 남아도 재생성을 막지 않는다 —
보고서는 확정 판정만 세고 미확정은 0이 아니라 '없음'으로 다룬다. 재판정 자체가 비용 보류로 아무것도
묻지 못했을 때만 재생성을 부르지 않는다(반영할 새 판정이 없다).

실행 수명은 공통 claim(`AuthenticatedTask.before_start` → `track_operation_prerun`)이 소유한다 — 이 모듈은
그 claim의 버전·시도 수를 돌리지 않고, 끝낼 때만 같은 울타리(task_id·lease_owner·claim 버전)로 종결한다.
부모를 **먼저** 종결한 뒤 자식을 큐에 넣는다 — 자식의 숫자 보존 판정이 진행 중인 부모를
`OPERATION_IN_FLIGHT`로 보지 않게 하기 위해서다(자식 쪽도 자기 부모 실행을 명시적으로 제외한다).

이 재판정은 새 유료 측정 단계가 아니다(`services/competitor_rejudge.py` 모듈 설명) — 저장된 답변 원문에
대한 경쟁사 판정만 다시 하므로 마감(1일 07:00 KST)이 지난 달도 대상이다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import arrow
from sqlalchemy import select, update

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services import operation_run_payloads
from app.services.competitor_rejudge import (
    REJUDGE_DISPATCH_PURPOSE,
    RejudgeOutcome,
    execute_rejudge,
)
from app.services.monthly_period import MonthlyPeriodError, require_closed_period
from app.workers.dispatch_auth import require_dispatch
from app.workers.dispatch_envelope import build_dispatch_headers
from app.workers.generation_run_control import explicit_run_context

logger = logging.getLogger(__name__)

REPORT_REFRESH_STAGE = "COMPETITOR_REFRESH"
STAGE_REPORT_REFRESH_QUEUED = "REPORT_REFRESH_QUEUED"
STAGE_COST_BLOCKED = "COST_BLOCKED"
# 자식 보고서 실행의 인자 — `generate_monthly_report_for_hospital(hospital, year, month, rebuild=True,
# automatic_recovery=False, template_only=True, allow_recovery_pending=False, competitor_refresh=True)`.
REPORT_REFRESH_TASK_FLAGS: tuple[Any, ...] = (True, False, True, False, True)
_OPEN_STATES = (OperationRunState.REQUESTED, OperationRunState.QUEUED, OperationRunState.RUNNING)


def _operation_run_id(task) -> uuid.UUID | None:
    headers = getattr(getattr(task, "request", None), "headers", None)
    raw = headers.get("operation_run_id") if isinstance(headers, Mapping) else None
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _note_progress(db, run_id: uuid.UUID | None, summary: dict[str, Any]) -> None:
    """진행 요약만 적는다 — 상태·버전·시도 수는 공통 claim이 소유하므로 손대지 않는다."""
    if run_id is None:
        return
    run = db.get(OperationRun, run_id)
    if run is None:
        return
    run.result_summary = {**(run.result_summary if isinstance(run.result_summary, dict) else {}), **summary}
    db.commit()


def _finish(
    db,
    task,
    run_id: uuid.UUID | None,
    *,
    state: OperationRunState,
    summary: dict[str, Any],
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
) -> bool:
    """실행을 종결한다. 공통 claim 문맥이 있으면 그 울타리(task_id·lease·버전)로만 쓴다 — 다른 사본이 가져간
    실행은 건드리지 않는다. 문맥이 없으면(직접 호출·테스트) 열린 상태만 보고 쓴다. 종결했으면 True."""
    if run_id is None:
        return False
    run = db.get(OperationRun, run_id)
    if run is None:
        return False
    merged = {**(run.result_summary if isinstance(run.result_summary, dict) else {}), **summary}
    context = explicit_run_context(task) if task is not None else None
    statement = (
        update(OperationRun)
        .where(OperationRun.id == run_id)
        .values(
            state=state,
            completed_at=datetime.now(UTC),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            total_count=1,
            success_count=int(state == OperationRunState.SUCCEEDED),
            failure_count=int(state in (OperationRunState.FAILED, OperationRunState.PARTIAL)),
            skipped_count=0,
            result_summary=merged,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    )
    if context is not None and context.run_id == run_id:
        statement = statement.where(
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.task_id == context.worker_id,
            OperationRun.lease_owner == context.worker_id,
            OperationRun.version == context.version,
        )
    else:
        statement = statement.where(OperationRun.state.in_(_OPEN_STATES))
    finished = db.execute(statement).scalar_one_or_none()
    db.commit()
    db.expire_all()
    if finished is None:
        logger.info("competitor rejudge run not finished by this delivery (fenced): run_id=%s", run_id)
    return finished is not None


def dispatch_competitor_report_refresh(
    db,
    hospital: Hospital,
    year: int,
    month: int,
    *,
    parent_run_id: uuid.UUID | None,
    rejudge_summary: dict[str, Any],
) -> OperationRun:
    """재판정 결과를 실을 템플릿 갱신(COMPETITOR_REFRESH)을 자식 실행으로 큐에 넣는다. 같은 부모에 한 번만."""
    from app.workers.tasks import (
        _mark_weekly_sov_operation_queued,
        generate_monthly_report_for_hospital,
    )

    idempotency_key = (
        f"competitor-refresh:{parent_run_id}:report"
        if parent_run_id is not None
        else f"competitor-refresh:{hospital.id}:{year:04d}-{month:02d}:{uuid.uuid4()}"
    )
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    hospital_id = str(hospital.id)
    task_args = (hospital_id, year, month, *REPORT_REFRESH_TASK_FLAGS)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        parent_run_id=parent_run_id,
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={
            **operation_run_payloads.build_request_payload(
                operation_run_payloads.DispatchPayload("hospital", hospital_id, "reports", task_args)
            ),
            "competitor_refresh": {
                "rejudge_run_id": str(parent_run_id) if parent_run_id is not None else None,
                **rejudge_summary,
            },
        },
        result_summary={"period_year": year, "period_month": month, "mode": REPORT_REFRESH_STAGE},
        version=1,
    )
    db.add(run)
    db.commit()
    try:
        generate_monthly_report_for_hospital.apply_async(
            args=list(task_args),
            queue="reports",
            headers={
                **build_dispatch_headers("generate-monthly-report", hospital_id),
                "operation_run_id": str(run.id),
            },
            task_id=run.task_id,
        )
    except Exception:  # noqa: BLE001 — REQUESTED 실행은 자율 복구가 재배달한다.
        logger.exception(
            "competitor report refresh dispatch failed; reconciler will retry",
            extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
        )
        return run
    _mark_weekly_sov_operation_queued(db, run.id, datetime.now(UTC))
    return run


@celery_app.task(
    name="app.workers.competitor_refresh.rejudge_month_competitors",
    bind=True,
    # 공급자·DB 일시 장애는 다시 돈다 — 기록 단위로 커밋하므로 재시도가 이미 판정한 이름을 다시 사지 않는다.
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
    soft_time_limit=1500,
    time_limit=1800,
)
def rejudge_month_competitors(self, hospital_id: str, year: int, month: int) -> dict[str, Any]:
    """지난달 저장 답변의 경쟁사 판정을 지금 등록 경쟁사로 보충하고, 보고서 경쟁 현황 갱신을 체인으로 부른다."""
    require_dispatch(self, REJUDGE_DISPATCH_PURPOSE, hospital_id, args=[hospital_id, year, month], kwargs={})
    run_id = _operation_run_id(self)
    now = arrow.now("Asia/Seoul")
    period = {"period_year": year, "period_month": month}
    with SyncSessionLocal() as db:
        hospital = db.get(Hospital, uuid.UUID(str(hospital_id)))
        if hospital is None:
            _finish(
                db, self, run_id, state=OperationRunState.FAILED,
                summary={"stage": "FAILED", **period}, safe_error_code="HOSPITAL_NOT_FOUND",
                safe_error_message="병원을 찾지 못해 경쟁 병원 반영을 시작하지 않았습니다.",
            )
            return {"status": "hospital_not_found"}
        try:
            require_closed_period(int(year), int(month), now=now.datetime)
        except MonthlyPeriodError as exc:
            _finish(
                db, self, run_id, state=OperationRunState.FAILED,
                summary={"stage": "FAILED", **period},
                safe_error_code="PERIOD_NOT_CLOSED", safe_error_message=str(exc),
            )
            return {"status": "period_not_closed"}
        _note_progress(db, run_id, {"stage": "RUNNING", **period})
        try:
            outcome: RejudgeOutcome = execute_rejudge(db, hospital, int(year), int(month), run_id=run_id)
        except Exception:
            db.rollback()
            if self.request.retries >= self.max_retries:
                _finish(
                    db, self, run_id, state=OperationRunState.FAILED,
                    summary={"stage": "FAILED", **period},
                    safe_error_code="COMPETITOR_REJUDGE_FAILED",
                    safe_error_message="경쟁 병원 판정을 끝내지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
                )
            raise
        summary = {**period, "rejudge": outcome.summary()}
        if outcome.cost_blocked:
            _finish(
                db, self, run_id, state=OperationRunState.PARTIAL,
                summary={**summary, "stage": STAGE_COST_BLOCKED, "report_run_id": None},
                safe_error_code="COMPETITOR_REJUDGE_COST_BLOCKED",
                safe_error_message="비용 한도로 경쟁 병원 판정을 보류했습니다. 한도가 열린 뒤 다시 실행해 주세요.",
            )
            return {"status": "cost_blocked", **outcome.summary()}
        # 부모를 먼저 종결한다 — 자식의 숫자 보존 판정이 진행 중인 재판정을 기다리지 않게. 이 배달이 실행의
        # 주인이 아니면(울타리 불일치) 자식도 만들지 않는다 — 주인 사본이 만든다.
        owned = _finish(
            db, self, run_id, state=OperationRunState.SUCCEEDED,
            summary={**summary, "stage": STAGE_REPORT_REFRESH_QUEUED, "report_run_id": None},
        )
        if run_id is not None and not owned:
            return {"status": "stale_delivery", **outcome.summary()}
        report_run = dispatch_competitor_report_refresh(
            db, hospital, int(year), int(month),
            parent_run_id=run_id, rejudge_summary=outcome.summary(),
        )
        _note_progress(db, run_id, {"report_run_id": str(report_run.id)})
    return {"status": "report_refresh_queued", "report_run_id": str(report_run.id), **outcome.summary()}
