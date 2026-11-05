"""경쟁 병원 반영 체인 — 경쟁사 재판정(REJUDGE_COMPETITORS) → 경쟁 현황 부분 재생성(COMPETITOR_REFRESH).

한 번의 Admin 행동(`POST /admin/hospitals/{id}/reports/competitors/refresh`)이 이 태스크 하나를 큐에 넣고,
태스크가 끝나면 **같은 체인**(자식 OperationRun, `parent_run_id` = 이 실행)으로 월간 보고서 템플릿 갱신을
`competitor_refresh=True`로 부른다. 재판정 셀 일부가 미확정(null)으로 남아도 재생성을 막지 않는다 —
보고서는 확정 판정만 세고 미확정은 0이 아니라 '없음'으로 다룬다. 재판정 자체가 비용 보류로 아무것도
묻지 못했을 때만 재생성을 부르지 않는다(반영할 새 판정이 없다).

실행 수명은 공통 claim(`AuthenticatedTask.before_start` → `track_operation_prerun`)이 소유한다 — 이 모듈은
그 claim의 버전·시도 수를 돌리지 않고, 끝낼 때는 **열린 상태**(REQUESTED·QUEUED·RUNNING)만 울타리로 종결한다.
같은 병원의 재판정은 세션 advisory lock(고정 커넥션, 기다리지 않는 try-lock)으로 직렬화한다 — 락을 못 잡은 사본은
`Ignore`로 조용히 물러나 실행 기록·사고를 남기지 않고(재시도도 소모하지 않고), 주인 사본만 판정을 사고 종결한다.
claim 버전까지 울타리에 넣지 않는 이유: 같은 task id로 다시 배달된 사본이 살아 있는 lease를 다시 claim하면
버전이 바뀌어, 일을 끝낸 주인 사본의 종결이 거절되고 자식 체인이 사라진다. 부모 종결과 자식 REQUESTED 행은
한 트랜잭션에 쓴 뒤 발행한다 — 끝난 부모에 자식이 없는 창이 없고, 자식의 숫자 보존 판정은 자기 부모를
`OPERATION_IN_FLIGHT`에서 뺀다.

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
from celery.exceptions import Ignore
from sqlalchemy import select, update

from app.core.celery_app import celery_app
from app.core.database import SyncSessionPinnedConnection
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services import operation_run_payloads
from app.services.competitor_rejudge import (
    REJUDGE_DISPATCH_PURPOSE,
    RejudgeOutcome,
    execute_rejudge,
)
from app.services.monthly_period import MonthlyPeriodError, require_closed_period
from app.utils.db_locks import (
    release_hospital_advisory_session_lock_sync,
    try_acquire_hospital_advisory_session_lock_sync,
)
from app.workers.dispatch_auth import require_dispatch
from app.workers.dispatch_envelope import build_dispatch_headers

logger = logging.getLogger(__name__)

REPORT_REFRESH_STAGE = "COMPETITOR_REFRESH"
STAGE_REPORT_REFRESH_QUEUED = "REPORT_REFRESH_QUEUED"
STAGE_COST_BLOCKED = "COST_BLOCKED"
# 자식 보고서 실행의 인자 — `generate_monthly_report_for_hospital(hospital, year, month, rebuild=True,
# automatic_recovery=False, template_only=True, allow_recovery_pending=False, competitor_refresh=True)`.
REPORT_REFRESH_TASK_FLAGS: tuple[Any, ...] = (True, False, True, False, True)
# 재판정 직렬화 락의 이름공간 — 프로필 저장·V0가 쓰는 병원 공용 키와 분리한다(짧은 PATCH가 유일한 배달을 물러나게 하지 않게).
LOCK_NAMESPACE = "competitor-rejudge"
_TERMINAL_STATES = (
    OperationRunState.SUCCEEDED,
    OperationRunState.PARTIAL,
    OperationRunState.FAILED,
    OperationRunState.CANCELLED,
)
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
    commit: bool = True,
) -> bool:
    """실행을 종결한다. 울타리는 열린 상태(REQUESTED·QUEUED·RUNNING)다 — 이미 끝난 실행은 건드리지 않는다. 종결했으면 True.
    `commit=False`면 호출자가 같은 트랜잭션에 다른 행(자식 실행)을 함께 묶어 커밋한다. 동시 실행은 세션 락이 막는다."""
    del task  # 울타리에 claim 버전을 쓰지 않는다(모듈 설명 참조)
    if run_id is None:
        return False
    run = db.get(OperationRun, run_id)
    if run is None:
        return False
    merged = {**(run.result_summary if isinstance(run.result_summary, dict) else {}), **summary}
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
        .where(OperationRun.state.in_(_OPEN_STATES))
    )
    finished = db.execute(statement).scalar_one_or_none()
    if commit:
        db.commit()
        db.expire_all()
    if finished is None:
        logger.info("competitor rejudge run already terminal; not finished by this delivery: run_id=%s", run_id)
    return finished is not None


def _release_session_lock(db, hospital_uuid: uuid.UUID) -> None:
    """세션 락을 반드시 푼 채 커넥션을 돌려준다(V0 `release_v0_session_lock`과 같은 이유) — 락 구간의 SQL 오류로 세션이
    pending-rollback이면 unlock 자체가 실패하고, 세션 락은 롤백으로 풀리지 않아 풀에 돌아간 커넥션이 그 병원의 모든
    쓰기를 막는다. 먼저 롤백하고, 해제를 확인하지 못하면 커넥션을 폐기한다."""
    try:
        db.rollback()
        if release_hospital_advisory_session_lock_sync(db, hospital_uuid, namespace=LOCK_NAMESPACE) is False:
            logger.error("competitor rejudge session lock for %s was not held by this connection; discarding it", hospital_uuid)
            db.invalidate()
        return
    except Exception:
        logger.exception("failed to release competitor rejudge session lock for %s", hospital_uuid)
    try:
        db.invalidate()
    except Exception:
        logger.exception("failed to discard the competitor rejudge lock connection for %s", hospital_uuid)


def _existing_child_run(db, idempotency_key: str) -> OperationRun | None:
    return db.execute(
        select(OperationRun).where(
            OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def _child_idempotency_key(hospital: Hospital, year: int, month: int, parent_run_id: uuid.UUID | None) -> str:
    return (
        f"competitor-refresh:{parent_run_id}:report"
        if parent_run_id is not None
        else f"competitor-refresh:{hospital.id}:{year:04d}-{month:02d}:{uuid.uuid4()}"
    )


def _stage_child_run(
    db,
    hospital: Hospital,
    year: int,
    month: int,
    *,
    parent_run_id: uuid.UUID | None,
    rejudge_summary: dict[str, Any],
) -> tuple[OperationRun, bool]:
    """자식 실행 행을 REQUESTED로 **세션에만** 올린다(커밋은 호출자). 같은 부모의 자식이 있으면 그것을 돌려준다."""
    idempotency_key = _child_idempotency_key(hospital, year, month, parent_run_id)
    existing = _existing_child_run(db, idempotency_key)
    if existing is not None:
        return existing, False
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
    db.flush()
    return run, True


def _publish_child_run(db, run: OperationRun) -> OperationRun:
    """커밋된 REQUESTED 자식을 브로커에 싣는다. 실패해도 행은 남아 자율 복구가 재배달한다."""
    from app.workers.tasks import (
        _mark_weekly_sov_operation_queued,
        generate_monthly_report_for_hospital,
    )

    if run.state != OperationRunState.REQUESTED:
        return run
    try:
        dispatch = operation_run_payloads.parse_stored_dispatch(run.request_payload.get("_dispatch"))
        generate_monthly_report_for_hospital.apply_async(
            args=list(dispatch.task_args),
            queue="reports",
            headers={
                **build_dispatch_headers("generate-monthly-report", str(run.hospital_id)),
                "operation_run_id": str(run.id),
            },
            task_id=run.task_id,
        )
    except Exception:  # noqa: BLE001 — REQUESTED 실행은 자율 복구가 재배달한다.
        logger.exception(
            "competitor report refresh dispatch failed; reconciler will retry",
            extra={"hospital_id": str(run.hospital_id), "operation_run_id": str(run.id)},
        )
        return run
    _mark_weekly_sov_operation_queued(db, run.id, datetime.now(UTC))
    return run


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
    run, _created = _stage_child_run(
        db, hospital, year, month, parent_run_id=parent_run_id, rejudge_summary=rejudge_summary
    )
    db.commit()
    return _publish_child_run(db, run)


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
    # 세션 락은 커넥션에 붙는다 — 기록 단위 커밋이 커넥션을 풀에 돌려주지 않도록 커넥션을 고정한다(V0와 같은 이유).
    with SyncSessionPinnedConnection() as db:
        hospital = db.get(Hospital, uuid.UUID(str(hospital_id)))
        if hospital is None:
            _finish(
                db, self, run_id, state=OperationRunState.FAILED,
                summary={"stage": "FAILED", "failure_code": "HOSPITAL_NOT_FOUND", **period},
                safe_error_message="병원을 찾지 못해 경쟁 병원 반영을 시작하지 않았습니다.",
            )
            return {"status": "hospital_not_found"}
        try:
            require_closed_period(int(year), int(month), now=now.datetime)
        except MonthlyPeriodError as exc:
            _finish(
                db, self, run_id, state=OperationRunState.FAILED,
                summary={"stage": "FAILED", "failure_code": "PERIOD_NOT_CLOSED", **period},
                safe_error_message=str(exc),
            )
            return {"status": "period_not_closed"}
        hospital_uuid = hospital.id
        # 같은 병원의 재판정은 한 번에 하나만 돈다 — 같은 task id로 다시 배달된 사본이 살아 있는 lease를 다시 claim해
        # 같은 이름의 판정을 두 번 사지 않도록 세션 advisory lock(전용 이름공간)으로 직렬화한다. 기다리지 않는다:
        # 못 잡은 사본은 조용히 물러난다(Ignore — 실패도 재시도 소모도 아니다). 주인 사본이 끝내고 자식을 큐에 넣는다.
        if not try_acquire_hospital_advisory_session_lock_sync(db, hospital_uuid, namespace=LOCK_NAMESPACE):
            logger.info("competitor rejudge already running for hospital=%s; this delivery steps aside", hospital_uuid)
            raise Ignore()
        report_run_id: str | None = None
        try:
            # 락을 잡은 뒤 실행 상태를 다시 읽는다 — 먼저 끝난 사본이 있으면 진행 기록도 유료 판정도 하지 않는다.
            current = db.get(OperationRun, run_id, populate_existing=True) if run_id is not None else None
            if current is not None and current.state in _TERMINAL_STATES:
                return {"status": "already_finished", "report_run_id": (current.result_summary or {}).get("report_run_id")}
            _note_progress(db, run_id, {"stage": "RUNNING", **period})
            try:
                outcome: RejudgeOutcome = execute_rejudge(db, hospital, int(year), int(month), run_id=run_id)
            except Exception:
                db.rollback()
                if self.request.retries >= self.max_retries:
                    # 원인 코드는 요약에만 남긴다 — 종결 사고의 식별자는 원인과 무관해야 뒤의 성공한 실행이 그 사고를 닫는다.
                    _finish(
                        db, self, run_id, state=OperationRunState.FAILED,
                        summary={"stage": "FAILED", "failure_code": "COMPETITOR_REJUDGE_FAILED", **period},
                        safe_error_message="경쟁 병원 판정을 끝내지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
                    )
                raise
            summary = {**period, "rejudge": outcome.summary()}
            if outcome.cost_blocked:
                _finish(
                    db, self, run_id, state=OperationRunState.PARTIAL,
                    summary={
                        **summary, "stage": STAGE_COST_BLOCKED, "report_run_id": None,
                        "failure_code": "COMPETITOR_REJUDGE_COST_BLOCKED",
                    },
                    safe_error_message="비용 한도로 경쟁 병원 판정을 보류했습니다. 한도가 열린 뒤 다시 실행해 주세요.",
                )
                return {"status": "cost_blocked", **outcome.summary()}
            # 부모 종결과 자식 REQUESTED 행을 **한 트랜잭션**에 쓴다 — 그 사이에 죽어도 '끝난 부모에 자식 없음'이
            # 남지 않고, API는 커밋 직후부터 열린 자식을 진행 중 체인으로 본다. 이 배달이 실행의 주인이 아니면
            # (울타리 불일치) 자식 행도 버린다 — 주인 사본이 만든다.
            report_run, _created = _stage_child_run(
                db, hospital, int(year), int(month), parent_run_id=run_id, rejudge_summary=outcome.summary(),
            )
            owned = _finish(
                db, self, run_id, state=OperationRunState.SUCCEEDED,
                summary={**summary, "stage": STAGE_REPORT_REFRESH_QUEUED, "report_run_id": str(report_run.id)},
                commit=False,
            )
            if run_id is not None and not owned:
                db.rollback()
                return {"status": "already_finished", **outcome.summary()}
            report_run_id = str(report_run.id)  # 세션이 닫힌 뒤에도 쓸 수 있게 스칼라로 들고 간다
            db.commit()
            db.expire_all()
            _publish_child_run(db, db.get(OperationRun, report_run.id))
        finally:
            _release_session_lock(db, hospital_uuid)
    return {"status": "report_refresh_queued", "report_run_id": report_run_id, **outcome.summary()}
