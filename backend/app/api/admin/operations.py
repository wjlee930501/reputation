"""Admin API — v1.0 operations control plane.

Queue operations use two append-only audit events: a durable request before
broker dispatch and a confirmed event only after ``apply_async`` succeeds.
This keeps the audit trail truthful without pretending that a pre-dispatch
row proves broker acceptance.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_owner_account
from app.api.admin.domain import verify_domain_for_hospital
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval, ReportDeliveryEventType
from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, AIQueryVariant
from app.schemas.operations import (
    AttentionHospital,
    AttentionQueueResponse,
    AttentionReportHospital,
    AttentionReports,
    CostGuardDailyLimitRequest,
    CostGuardDailyLimitResponse,
    CostGuardKillSwitchRequest,
    CostGuardKillSwitchResponse,
    CostGuardStatusResponse,
)
from app.services import cost_guard
from app.services.audit_log import default_actor, write_audit_log
from app.services.incident_safety import sanitize_operator_text
from app.services.monthly_delivery_projection import (
    latest_delivery_event_subquery,
    latest_monthly_report_subquery,
)
from app.services.monthly_events import MonthlyRunStage
from app.services.monthly_period import (
    MonthlyPeriodError,
    prior_month_to_close,
    reporting_period,
    require_closed_period,
)
from app.services.operation_run_payloads import UnsafeDispatchPayload, parse_stored_dispatch
from app.services.operation_runs import (
    DispatchTask,
    OperationCommand,
    OperationDispatch,
    OperationQueueUnavailable,
    dispatch_operation,
)
from app.services.post_publish_review_policy import (
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)
from app.services.v0_claim import latest_active_v0_run, v0_claim_is_alive
from app.workers.tasks import (
    build_aeo_site,
    generate_content_image,
    generate_monthly_report_for_hospital,
    regenerate_content_item,
    run_sov_for_hospital,
    trigger_v0_report,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Operations"])

# 비용 가드는 병원 단위가 아닌 전역 제어 평면이라 별도 prefix를 쓴다.
cost_guard_router = APIRouter(prefix="/admin/operations", tags=["Admin — Cost Guard"])

# 공개 후 24시간이 지나도록 사람이 확인하지 않은 콘텐츠를 "밀렸다"고 본다.
# 08:00 자동 발행 → 그날 업무 시간 안에 후행 확인이 정상 흐름이므로, 하루가 통째로
# 지났다는 것은 그 흐름에서 빠졌다는 뜻이다.
POST_PUBLISH_REVIEW_OVERDUE_HOURS = 24
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]
RequiredIdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


class GenerationClaimReleaseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_claimed_at: AwareDatetime
    reason: str = Field(min_length=3, max_length=200)


class MonthlyReportRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    state: str
    stage: str
    period_year: int
    period_month: int
    report_id: uuid.UUID | None
    report_version: int | None
    supersedes_report_id: uuid.UUID | None
    requested_at: datetime
    completed_at: datetime | None


class MonthlyReportBuildRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


_MONTHLY_REBUILD_AUDIT_ACTION = "generate_monthly_report_rebuild_requested"
_MONTHLY_REBUILD_AUDIT_TARGET = "monthly_report_rebuild_request"


async def _prepare_monthly_rebuild_audit(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    idempotency_key: str,
    year: int | None,
    month: int | None,
    reason: str,
) -> bool:
    """Lock the hospital and stage a reason audit before durable dispatch.

    ``dispatch_operation`` commits the staged audit together with the new run before
    contacting the broker.  The operation payload consequently stays limited to
    machine dispatch facts while the operator's redacted reason remains in the
    append-only audit trail.
    """

    await db.execute(
        select(Hospital.id).where(Hospital.id == hospital_id).with_for_update()
    )
    request_fingerprint = hashlib.sha256(idempotency_key.encode()).hexdigest()
    existing = await db.scalar(
        select(AdminAuditLog)
        .where(
            AdminAuditLog.hospital_id == hospital_id,
            AdminAuditLog.action == _MONTHLY_REBUILD_AUDIT_ACTION,
            AdminAuditLog.target_type == _MONTHLY_REBUILD_AUDIT_TARGET,
            AdminAuditLog.target_id == request_fingerprint,
        )
        .order_by(AdminAuditLog.created_at.desc())
    )
    expected = {"period_year": year, "period_month": month, "reason": reason}
    if existing is not None:
        if existing.detail != expected:
            raise HTTPException(
                status_code=409,
                detail="같은 요청 키가 다른 월 또는 다른 사유에 이미 사용됐습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
            )
        return False

    await write_audit_log(
        db,
        action=_MONTHLY_REBUILD_AUDIT_ACTION,
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type=_MONTHLY_REBUILD_AUDIT_TARGET,
        target_id=request_fingerprint,
        detail=expected,
    )
    return True


async def _enqueue_with_truthful_audit(
    db: AsyncSession,
    *,
    action: str,
    hospital_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID | str,
    task: DispatchTask,
    args: list[JSONValue],
    queue: str,
    idempotency_key: str | None = None,
) -> OperationDispatch:
    """Durably record request, dispatch, then record broker acceptance."""
    try:
        return await dispatch_operation(
            db,
            OperationCommand(
                operation_type=action.upper(),
                hospital_id=hospital_id,
                requested_by_id=None,
                idempotency_key=idempotency_key,
                audit_actor=default_actor(),
                target_type=target_type,
                target_id=str(target_id),
                queue=queue,
                task_args=tuple(args),
            ),
            task,
        )
    except OperationQueueUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": str(exc),
                "operation_run_id": str(exc.run_id),
                "operation_state": "FAILED",
            },
        ) from exc


@cost_guard_router.get("/attention", response_model=AttentionQueueResponse)
async def get_attention_queue(db: AsyncSession = Depends(get_db)):
    """자동 검수를 통과한 공개 콘텐츠 중 정기 품질 표본만 병원 횡단 집계한다.

    발행 차단은 자동 검수와 예외 큐가 담당한다. 정상 발행 전건을 다시 사람이 확인하면
    자동화가 사람 승인 큐로 되돌아가지 않도록 월간 시퀀스 첫 글과 자동 보완·공개 후 수정
    신호가 있는 글만 드리프트 감시 표본으로 둔다.

    조건은 순수 컬럼 술어(PUBLISHED · 미확인 · 공개시각 존재)라 집계 1회로 끝난다 —
    발행 가능 여부 재계산 같은 무거운 판정은 여기서 하지 않는다.
    """
    overdue_before = datetime.now(UTC) - timedelta(hours=POST_PUBLISH_REVIEW_OVERDUE_HOURS)

    rows = (
        await db.execute(
            select(
                Hospital.id,
                Hospital.name,
                func.count(ContentItem.id).label("unreviewed_count"),
                func.count(
                    case((ContentItem.published_at < overdue_before, 1))
                ).label("overdue_count"),
                func.min(ContentItem.published_at).label("oldest_published_at"),
            )
            .join(ContentItem, ContentItem.hospital_id == Hospital.id)
            .where(
                publicly_operational_hospital_predicate(),
                human_post_publish_review_predicate(),
            )
            .group_by(Hospital.id, Hospital.name)
            # 오래 방치된 병원이 위로 — 큐의 정렬 기준은 심각도가 아니라 경과 시간이다.
            .order_by(func.min(ContentItem.published_at).asc())
        )
    ).all()

    hospitals = [
        AttentionHospital(
            hospital_id=row.id,
            hospital_name=row.name,
            unreviewed_count=row.unreviewed_count,
            overdue_count=row.overdue_count,
            oldest_published_at=row.oldest_published_at,
        )
        for row in rows
    ]
    return AttentionQueueResponse(
        unreviewed_total=sum(h.unreviewed_count for h in hospitals),
        overdue_total=sum(h.overdue_count for h in hospitals),
        overdue_hours=POST_PUBLISH_REVIEW_OVERDUE_HOURS,
        hospitals=hospitals,
        reports=await _previous_month_report_gaps(db),
    )


async def _previous_month_report_gaps(db: AsyncSession) -> AttentionReports:
    """지난달 원장 보고가 빠진 병원.

    월말 배치가 어느 병원에서 실패하면 Slack 한 줄이 전부였고, 그 병원은 다음 달
    마지막 날까지 리포트가 빈 채로 남았다. 만들어졌더라도 원장에게 전달되지 않으면
    운영 실패는 마찬가지다 — 두 경우를 같이 본다.

    이번 달이 아니라 **지난달**을 본다. 이번 달 리포트는 월말에 생기므로 그 전에
    '없음'으로 표시하면 매일 거짓 경보가 된다.
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    year = now_kst.year if now_kst.month > 1 else now_kst.year - 1
    month = now_kst.month - 1 if now_kst.month > 1 else 12
    period = reporting_period(year, month)
    eligible_hospital_ids = (
        select(HospitalServiceInterval.hospital_id)
        .where(
            HospitalServiceInterval.started_at < period.ends_at,
            or_(
                HospitalServiceInterval.ended_at.is_(None),
                HospitalServiceInterval.ended_at > period.starts_at,
            ),
        )
        .distinct()
    )
    latest_report = latest_monthly_report_subquery(year, month)
    latest_delivery = latest_delivery_event_subquery()

    rows = (
        await db.execute(
            select(Hospital.id, Hospital.name, MonthlyReport.id, latest_delivery.c.event_type)
            .outerjoin(
                latest_report,
                and_(latest_report.c.hospital_id == Hospital.id, latest_report.c.rn == 1),
            )
            .outerjoin(
                MonthlyReport,
                and_(
                    MonthlyReport.id == latest_report.c.report_id,
                    MonthlyReport.period_year == year,
                    MonthlyReport.period_month == month,
                    MonthlyReport.report_type == "MONTHLY",
                ),
            )
            .outerjoin(
                latest_delivery,
                and_(
                    latest_delivery.c.report_id == MonthlyReport.id,
                    latest_delivery.c.rn == 1,
                ),
            )
            .where(
                # 지난달 서비스 구간을 우선하되, 아직 운영 전인 병원은 기대 대상이 아니다.
                # 지난달 서비스 후 중지된 병원은 누락 감시를 유지한다.
                Hospital.id.in_(eligible_hospital_ids),
                Hospital.status.in_((HospitalStatus.ACTIVE, HospitalStatus.PAUSED)),
                or_(
                    MonthlyReport.id.is_(None),
                    and_(
                        latest_delivery.c.report_id.is_(None),
                        MonthlyReport.sent_at.is_(None),
                    ),
                    latest_delivery.c.event_type == ReportDeliveryEventType.RESCINDED.value,
                ),
            )
            .order_by(Hospital.name.asc())
        )
    ).all()

    missing: list[AttentionReportHospital] = []
    undelivered: list[AttentionReportHospital] = []
    for hospital_id, hospital_name, report_id, _event_type in rows:
        if report_id is None:
            missing.append(
                AttentionReportHospital(hospital_id=hospital_id, hospital_name=hospital_name)
            )
        else:
            undelivered.append(
                AttentionReportHospital(
                    hospital_id=hospital_id, hospital_name=hospital_name, report_id=report_id
                )
            )

    return AttentionReports(
        period_year=year,
        period_month=month,
        missing=missing,
        undelivered=undelivered,
    )


@cost_guard_router.get("/cost-guard", response_model=CostGuardStatusResponse)
async def get_cost_guard_status():
    """카테고리별 일/월 사용량 + 상한 + 킬스위치 상태 조회."""
    return await cost_guard.get_usage_snapshot()


@cost_guard_router.post("/cost-guard/kill-switch", response_model=CostGuardKillSwitchResponse)
async def set_cost_guard_kill_switch(
    payload: CostGuardKillSwitchRequest,
    db: AsyncSession = Depends(get_db),
):
    """비용 가드 킬스위치 토글. 감사 로그 기록 후 Redis 상태를 변경한다.

    순서 규약(write_audit_log → commit → 외부 부수효과)을 지켜, 감사 row가 durable해진
    뒤에만 실제 킬스위치를 반영한다.
    """
    await write_audit_log(
        db,
        action="cost_guard_kill_switch",
        actor=default_actor(),
        target_type="cost_guard",
        target_id="kill_switch",
        detail={"enabled": payload.enabled},
    )
    await db.commit()
    await cost_guard.set_kill_switch(payload.enabled)
    return CostGuardKillSwitchResponse(kill_switch_active=payload.enabled)


@cost_guard_router.post("/cost-guard/daily-limit", response_model=CostGuardDailyLimitResponse)
async def set_cost_guard_daily_limit(
    payload: CostGuardDailyLimitRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    """오늘 하루치 일일 상한을 올리거나(limit) 원복한다(limit=None).

    야간 생성이 일일 상한에 걸리면 종전에는 환경변수 변경 + 재배포가 유일한 복구
    경로였다. 지출을 늘리는 조작이므로 소유자만 할 수 있고, 월간 상한은 바꾸지 않아
    이번 달 예산 천장은 그대로다. 상향은 오늘 키에만 저장돼 다음 날 자동 원복된다.
    """
    # 값 검증을 먼저 한다 — 잘못된 요청으로 감사 로그만 남는 것을 피한다.
    try:
        cost_guard.validate_daily_limit_override(payload.category, payload.limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    before_snapshot = await cost_guard.get_usage_snapshot()
    before = next(
        (c for c in before_snapshot["categories"] if c["category"] == payload.category),
        None,
    )

    # 순서 규약: write_audit_log → commit → 외부 부수효과(Redis).
    # 뒤집으면 Redis만 바뀌고 감사 커밋이 실패했을 때 "누가 상한을 올렸는지" 기록이 없다.
    await write_audit_log(
        db,
        action="cost_guard_daily_limit",
        actor=actor.email,
        target_type="cost_guard",
        target_id=payload.category,
        detail={
            "category": payload.category,
            "previous_limit": int(before["daily_limit"]) if before else None,
            "requested_limit": payload.limit,
            "reason": sanitize_operator_text(payload.reason, limit=200),
            "change": "RESTORE" if payload.limit is None else "RAISE",
        },
    )
    await db.commit()

    try:
        if payload.limit is None:
            await cost_guard.clear_daily_limit_override(payload.category)
        else:
            await cost_guard.set_daily_limit_override(payload.category, payload.limit)
    except ValueError as exc:  # pragma: no cover — 위에서 이미 검증했다
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    snapshot = await cost_guard.get_usage_snapshot()
    current = next(
        (c for c in snapshot["categories"] if c["category"] == payload.category),
        None,
    )
    return CostGuardDailyLimitResponse(
        category=payload.category,
        daily_limit=int(current["daily_limit"]) if current else 0,
        daily_limit_default=int(current["daily_limit_default"]) if current else 0,
    )


@router.post("/{hospital_id}/operations/trigger-v0-report")
async def trigger_v0_report_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    # 태스크는 v0_report_done인 병원을 조용히 건너뛴다(중복 리포트·중복 측정 비용 방지).
    # 그대로 큐에 넣으면 화면은 "등록했습니다"라고 알리는데 아무 일도 일어나지 않아,
    # AE가 리포트를 기다리다 놓친다. 큐에 넣기 전에 사실대로 거절한다.
    if hospital.status == HospitalStatus.ANALYZING:
        active = await latest_active_v0_run(db, hospital.id)
        alive = await v0_claim_is_alive(db, hospital.id)
        if alive or active is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "이 병원은 이미 초기 진단을 만들고 있습니다. "
                        "진행 상태에서 결과를 확인하세요."
                    ),
                    "operation_run_id": str(active.id) if active is not None else None,
                    "operation_state": (
                        active.state.value if active is not None else "ANALYZING"
                    ),
                },
            )
    if hospital.v0_report_done:
        raise HTTPException(
            status_code=409,
            detail=(
                "이 병원은 이미 초기 진단 리포트가 생성돼 있어 다시 만들지 않습니다. "
                "최신 수치가 필요하면 'AI 언급률 측정'을 실행하고 리포트 화면에서 확인해 주세요."
            ),
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="trigger_v0_report",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=trigger_v0_report,
        args=[str(hospital.id)],
        queue="reports",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "V0 report queued",
        "hospital_id": str(hospital.id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


@router.post("/{hospital_id}/operations/run-sov")
async def run_sov_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
        raise HTTPException(
            status_code=409,
            detail="AI 언급률 측정은 ACTIVE 또는 PENDING_DOMAIN 상태에서 실행할 수 있습니다.",
        )
    if not await _has_active_query_variant(db, hospital.id):
        raise HTTPException(
            status_code=409,
            detail="활성 문구가 있는 환자 질문 타깃이 없어 AI 언급률 측정을 실행할 수 없습니다.",
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="run_sov",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=run_sov_for_hospital,
        args=[str(hospital.id)],
        queue="sov",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "AI 언급률 측정이 큐에 등록되었습니다.",
        "hospital_id": str(hospital.id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


@router.post("/{hospital_id}/operations/rebuild-site")
async def rebuild_site_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="rebuild_site",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=build_aeo_site,
        args=[str(hospital.id)],
        queue="default",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "Site rebuild queued",
        "hospital_id": str(hospital.id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


def _monthly_run_period(run: OperationRun) -> tuple[int, int]:
    summary = run.result_summary if isinstance(run.result_summary, dict) else {}
    year = summary.get("period_year")
    month = summary.get("period_month")
    if isinstance(year, int) and isinstance(month, int):
        return year, month
    try:
        dispatch = parse_stored_dispatch(run.request_payload.get("_dispatch"))
    except UnsafeDispatchPayload:
        dispatch = None
    if dispatch is not None and len(dispatch.task_args) >= 3:
        requested_year, requested_month = dispatch.task_args[1:3]
        if isinstance(requested_year, int) and isinstance(requested_month, int):
            return requested_year, requested_month
    previous = datetime.now(ZoneInfo("Asia/Seoul")).replace(day=1) - timedelta(days=1)
    return previous.year, previous.month


def _monthly_run_stage(run: OperationRun) -> MonthlyRunStage:
    summary = run.result_summary if isinstance(run.result_summary, dict) else {}
    raw = summary.get("stage")
    if isinstance(raw, str):
        try:
            return MonthlyRunStage(raw)
        except ValueError:
            pass
    if run.state in (OperationRunState.REQUESTED, OperationRunState.QUEUED):
        return MonthlyRunStage.QUEUED
    if run.state == OperationRunState.RUNNING:
        return MonthlyRunStage.RUNNING
    return MonthlyRunStage.FAILED


def _monthly_run_uuid(summary: dict[str, JSONValue], field: str) -> uuid.UUID | None:
    raw = summary.get(field)
    try:
        return uuid.UUID(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


@router.get(
    "/{hospital_id}/operations/monthly-report-runs",
    response_model=list[MonthlyReportRunResponse],
)
async def list_monthly_report_runs(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[MonthlyReportRunResponse]:
    await _get_hospital_or_404(db, hospital_id)
    runs = list(
        (
            await db.execute(
                select(OperationRun)
                .where(
                    OperationRun.hospital_id == hospital_id,
                    OperationRun.operation_type.in_(
                        ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT")
                    ),
                )
                .order_by(OperationRun.requested_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    response: list[MonthlyReportRunResponse] = []
    for run in runs:
        summary = run.result_summary if isinstance(run.result_summary, dict) else {}
        year, month = _monthly_run_period(run)
        version = summary.get("report_version")
        response.append(
            MonthlyReportRunResponse(
                run_id=run.id,
                parent_run_id=run.parent_run_id,
                state=run.state,
                stage=_monthly_run_stage(run).value,
                period_year=year,
                period_month=month,
                report_id=_monthly_run_uuid(summary, "report_id"),
                report_version=version if isinstance(version, int) else None,
                supersedes_report_id=_monthly_run_uuid(summary, "supersedes_report_id"),
                requested_at=run.requested_at,
                completed_at=run.completed_at,
            )
        )
    return response


@router.post("/{hospital_id}/operations/generate-monthly-report")
async def generate_monthly_report_operation(
    hospital_id: uuid.UUID,
    year: int | None = Query(default=None, ge=2000, le=2200),
    month: int | None = Query(default=None, ge=1, le=12),
    rebuild: bool = Query(default=False),
    payload: MonthlyReportBuildRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    """월간 리포트를 병원 단위로 다시 만든다.

    월말 배치가 실패하면 그 병원은 다음 달 마지막 날까지 리포트가 비어 있었고, 복구
    경로가 `make monthly-report`(전체 병원·마지막 날 한정)뿐이었다. year/month를 주지
    않으면 지난달을 만든다 — 배치 실패는 대개 달이 바뀐 뒤에 발견된다.
    이미 있는 리포트는 덮어쓰지 않는다.
    """
    if (year is None) != (month is None):
        raise HTTPException(
            status_code=400, detail="연도와 월은 함께 지정해야 합니다."
        )
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    try:
        period = (
            require_closed_period(year, month, now=now_kst)
            if year is not None and month is not None
            else prior_month_to_close(now_kst)
        )
    except MonthlyPeriodError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    year, month = period.year, period.month
    rebuild_reason = sanitize_operator_text(payload.reason if payload is not None else None, limit=200)
    if rebuild and (rebuild_reason is None or len(rebuild_reason) < 3):
        raise HTTPException(
            status_code=400,
            detail="새 버전을 만드는 이유를 3자 이상 입력해 주세요.",
        )
    if rebuild and idempotency_key is None:
        raise HTTPException(
            status_code=400,
            detail="중복 요청을 막는 요청 키가 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
        )
    hospital = await _get_hospital_or_404(db, hospital_id)
    task_args: list[JSONValue] = [str(hospital.id), year, month, *([True] if rebuild else [])]
    rebuild_audit_created = False
    if rebuild:
        assert idempotency_key is not None
        assert rebuild_reason is not None
        rebuild_audit_created = await _prepare_monthly_rebuild_audit(
            db,
            hospital_id=hospital.id,
            idempotency_key=idempotency_key,
            year=year,
            month=month,
            reason=rebuild_reason,
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="generate_monthly_report",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=generate_monthly_report_for_hospital,
        args=task_args,
        queue="reports",
        idempotency_key=idempotency_key,
    )
    if dispatch.replayed:
        if rebuild and rebuild_audit_created:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="기존 요청에는 재생성 사유 기록이 없습니다. 새 요청 키로 다시 시도해 주세요.",
            )
        try:
            stored = parse_stored_dispatch(dispatch.run.request_payload.get("_dispatch"))
        except UnsafeDispatchPayload as exc:
            raise HTTPException(
                status_code=409,
                detail="기존 요청 내용을 확인할 수 없습니다. 개발팀에 작업 ID를 알려 주세요.",
            ) from exc
        if stored.task_args != tuple(task_args):
            raise HTTPException(
                status_code=409,
                detail="같은 요청 키가 다른 월 또는 다른 사유에 이미 사용됐습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
            )
    return {
        "detail": "월간 리포트 생성을 요청했습니다. 아래 작업 기록에서 진행 상황을 확인해 주세요.",
        "hospital_id": str(hospital.id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


@router.post("/{hospital_id}/operations/verify-domain")
async def verify_domain_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Dashboard adapter over the canonical domain verification transaction."""

    result = await verify_domain_for_hospital(hospital_id, db)
    return {
        "domain": result.domain,
        "verified": result.verified,
        "dns_verified": result.dns_verified,
        "cname_value": result.cname_value,
        "address_values": result.address_values,
        "expected_cname": result.expected_cname,
        "expected_addresses": result.expected_addresses,
        "verification_method": result.verification_method,
        "certificate_ready": result.certificate_ready,
        "certificate_phase": result.certificate_phase,
        "message": result.message,
    }


@router.post("/{hospital_id}/content/{content_id}/regenerate")
async def regenerate_content_operation(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital.id:
        raise HTTPException(status_code=404, detail="Content not found")
    if item.status in (ContentStatus.PUBLISHED, ContentStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail="Published or cancelled content cannot be regenerated",
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="regenerate_content",
        hospital_id=hospital.id,
        target_type="content_item",
        target_id=content_id,
        task=regenerate_content_item,
        args=[str(content_id)],
        queue="content",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "Content regeneration queued",
        "content_id": str(content_id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


@router.post("/{hospital_id}/content/{content_id}/regenerate-image")
async def regenerate_content_image_operation(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital.id:
        raise HTTPException(status_code=404, detail="Content not found")
    if item.status in (ContentStatus.PUBLISHED, ContentStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail="Published or cancelled content image cannot be regenerated",
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="regenerate_content_image",
        hospital_id=hospital.id,
        target_type="content_item",
        target_id=content_id,
        task=generate_content_image,
        args=[str(content_id)],
        queue="content",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "Content image generation queued",
        "content_id": str(content_id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
    }


@router.post("/{hospital_id}/content/{content_id}/generation-claim/release")
async def force_release_generation_claim(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    payload: GenerationClaimReleaseRequest,
    idempotency_key: RequiredIdempotencyKeyHeader,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
) -> dict[str, object]:
    """Release only the exact stale generation lease an OWNER inspected."""
    await _get_hospital_or_404(db, hospital_id)
    item = await db.get(ContentItem, content_id)
    if item is None or item.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Content not found")
    operation_type = "FORCE_RELEASE_GENERATION_CLAIM"
    existing = await db.scalar(
        select(OperationRun).where(
            OperationRun.requested_by_id == actor.id,
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == operation_type,
            OperationRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        source_id = existing.request_payload.get("source_id")
        if source_id != str(content_id):
            raise HTTPException(
                status_code=409,
                detail="같은 요청 키가 다른 콘텐츠 복구 작업에 이미 사용됐습니다.",
            )
        return _claim_release_response(existing, replayed=True)

    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type=operation_type,
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        requested_by_id=actor.id,
        request_payload={
            "source_type": "content_item",
            "source_id": str(content_id),
            "expected_claimed_at": payload.expected_claimed_at.isoformat(),
        },
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        version=1,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        replay = await db.scalar(
            select(OperationRun).where(
                OperationRun.requested_by_id == actor.id,
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == operation_type,
                OperationRun.idempotency_key == idempotency_key,
            )
        )
        if replay is None:
            raise
        return _claim_release_response(replay, replayed=True)

    released = (
        await db.execute(
            update(ContentItem)
            .where(
                ContentItem.id == content_id,
                ContentItem.hospital_id == hospital_id,
                ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.REJECTED)),
                ContentItem.body.is_(None),
                ContentItem.generation_claimed_at == payload.expected_claimed_at,
            )
            .values(generation_claimed_at=None)
            .returning(ContentItem.id)
        )
    ).scalar_one_or_none()
    succeeded = released is not None
    run.state = OperationRunState.SUCCEEDED if succeeded else OperationRunState.FAILED
    run.success_count = int(succeeded)
    run.failure_count = int(not succeeded)
    run.completed_at = datetime.now(UTC)
    run.result_summary = {"released": succeeded}
    run.safe_error_code = None if succeeded else "GENERATION_CLAIM_VERSION_CONFLICT"
    run.safe_error_message = (
        None if succeeded else "claim 상태가 이미 변경됐습니다. 최신 상태를 다시 확인해 주세요."
    )
    run.version += 1
    await write_audit_log(
        db,
        action="force_release_generation_claim",
        hospital_id=hospital_id,
        actor=actor.email,
        target_type="content_item",
        target_id=content_id,
        detail={
            "operation_run_id": str(run.id),
            "released": succeeded,
            "reason": sanitize_operator_text(payload.reason, limit=200),
            "idempotency_key_present": True,
        },
    )
    await db.commit()
    if not succeeded:
        raise HTTPException(
            status_code=409,
            detail={
                "code": run.safe_error_code,
                "message": run.safe_error_message,
                "operation_run_id": str(run.id),
            },
        )
    return _claim_release_response(run, replayed=False)


def _claim_release_response(run: OperationRun, *, replayed: bool) -> dict[str, object]:
    released = bool(run.result_summary and run.result_summary.get("released"))
    if run.state == OperationRunState.FAILED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": run.safe_error_code,
                "message": run.safe_error_message,
                "operation_run_id": str(run.id),
            },
        )
    return {
        "content_id": str(run.request_payload.get("source_id")),
        "released": released,
        "operation_run_id": str(run.id),
        "operation_state": run.state,
        "idempotent_replay": replayed,
    }


@router.get("/{hospital_id}/operations/audit-logs")
async def list_audit_logs(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    await _get_hospital_or_404(db, hospital_id)
    result = await db.execute(
        select(AdminAuditLog)
        .where(AdminAuditLog.hospital_id == hospital_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(limit)
    )
    return [_serialize_audit_log(row) for row in result.scalars().all()]


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _has_active_query_variant(db: AsyncSession, hospital_id: uuid.UUID) -> bool:
    # 일부 단위 테스트/레거시 어댑터는 get()만 제공한다. 실제 AsyncSession에서는 아래
    # 존재 확인으로 빈 측정 run·비용 차감을 사전에 막는다.
    if not hasattr(db, "execute"):
        return True
    result = await db.execute(
        select(AIQueryVariant.id)
        .join(AIQueryTarget, AIQueryTarget.id == AIQueryVariant.query_target_id)
        .where(
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status == "ACTIVE",
            AIQueryVariant.is_active.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _serialize_audit_log(log: AdminAuditLog) -> dict:
    return {
        "id": str(log.id),
        "hospital_id": str(log.hospital_id) if log.hospital_id else None,
        "actor": log.actor,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "detail": log.detail,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }
