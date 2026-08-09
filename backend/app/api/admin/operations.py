"""Admin API — v1.0 operations control plane.

Queue operations use two append-only audit events: a durable request before
broker dispatch and a confirmed event only after ``apply_async`` succeeds.
This keeps the audit trail truthful without pretending that a pre-dispatch
row proves broker acceptance.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_owner_account
from app.api.admin.domain import (
    check_domain_dns,
    domain_dns_strategy_for_hospital,
    ensure_verified_domain_certificate,
)
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
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
from app.services.hospital_lifecycle import activation_gate_error, evaluate_activation_gate
from app.services.incident_safety import sanitize_operator_text
from app.services.operation_runs import (
    DispatchTask,
    OperationCommand,
    OperationDispatch,
    OperationQueueUnavailable,
    dispatch_operation,
)
from app.services.service_intervals import ServiceIntervalProvenance, open_service_interval
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
    """공개됐지만 아직 사람이 확인하지 않은 콘텐츠를 병원 횡단으로 집계한다.

    08:00 자동 발행은 사람 승인 없이 공개된다. 그래서 운영의 병목은 "발행 전 승인"이
    아니라 **이미 공개된 것 중 아직 아무도 안 본 것**이고, 그 노출 시간이 곧 위험이다.
    지금까지 이 상태는 병원 상세 화면에 들어가야만 보여서, 병원이 늘면 AE가 매일
    전 병원을 순회해야 확인할 수 있었다.

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
                ContentItem.status == ContentStatus.PUBLISHED,
                ContentItem.post_publish_reviewed_at.is_(None),
                ContentItem.published_at.is_not(None),
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
    period_start = datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Seoul"))

    rows = (
        await db.execute(
            select(Hospital.id, Hospital.name, MonthlyReport.id, MonthlyReport.sent_at)
            .outerjoin(
                MonthlyReport,
                (MonthlyReport.hospital_id == Hospital.id)
                & (MonthlyReport.period_year == year)
                & (MonthlyReport.period_month == month)
                & (MonthlyReport.report_type == "MONTHLY"),
            )
            .where(
                Hospital.status == HospitalStatus.ACTIVE,
                # 그 달에 아직 존재하지 않던 병원은 리포트가 없는 게 정상이다.
                Hospital.created_at < period_start,
            )
            .order_by(Hospital.name.asc())
        )
    ).all()

    missing: list[AttentionReportHospital] = []
    undelivered: list[AttentionReportHospital] = []
    for hospital_id, hospital_name, report_id, sent_at in rows:
        if report_id is None:
            missing.append(
                AttentionReportHospital(hospital_id=hospital_id, hospital_name=hospital_name)
            )
        elif sent_at is None:
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

    # 순서 규약: write_audit_log → commit → 외부 부수효과(Redis).
    # 뒤집으면 Redis만 바뀌고 감사 커밋이 실패했을 때 "누가 상한을 올렸는지" 기록이 없다.
    await write_audit_log(
        db,
        action="cost_guard_daily_limit",
        actor=actor.email,
        target_type="cost_guard",
        target_id=payload.category,
        detail={"category": payload.category, "limit": payload.limit},
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
    if hospital.v0_report_done:
        raise HTTPException(
            status_code=409,
            detail=(
                "이 병원은 이미 V0 리포트가 생성돼 있어 다시 만들지 않습니다. "
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


@router.post("/{hospital_id}/operations/generate-monthly-report")
async def generate_monthly_report_operation(
    hospital_id: uuid.UUID,
    year: int | None = Query(default=None, ge=2000, le=2200),
    month: int | None = Query(default=None, ge=1, le=12),
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
    if year is not None and month is not None:
        # 이번 달·다음 달을 미리 만들면 아직 쌓이지 않은 데이터로 빈 리포트가 생기고,
        # 그 행 때문에 정작 월말 배치가 dedupe로 건너뛰어 진짜 리포트가 영영 생기지 않는다.
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        if (year, month) >= (now_kst.year, now_kst.month):
            raise HTTPException(
                status_code=400,
                detail="이번 달과 그 이후는 만들 수 없습니다. 월말 자동 생성이 끝난 지난달까지만 가능합니다.",
            )
    hospital = await _get_hospital_or_404(db, hospital_id)
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="generate_monthly_report",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=generate_monthly_report_for_hospital,
        args=[str(hospital.id), year, month],
        queue="reports",
        idempotency_key=idempotency_key,
    )
    return {
        "detail": "월간 리포트 생성을 요청했습니다. 완료되면 Slack으로 알려드립니다.",
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
    hospital = await _get_hospital_or_404(db, hospital_id)
    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다.")

    dns_check = await check_domain_dns(
        hospital.aeo_domain, domain_dns_strategy_for_hospital(hospital)
    )
    certificate = None
    serving_ready = False
    previous_status = (
        hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    )
    previous_site_live = bool(hospital.site_live)
    if dns_check.verified:
        gate = await evaluate_activation_gate(db, hospital)
        if not gate["ready"]:
            raise HTTPException(
                status_code=409,
                detail=activation_gate_error(gate),
            )
        certificate = await ensure_verified_domain_certificate(hospital.aeo_domain)
        serving_ready = certificate is None or certificate.ready
        if serving_ready:
            hospital.site_live = True
            hospital.status = HospitalStatus.ACTIVE
            await open_service_interval(
                db, hospital.id, ServiceIntervalProvenance.ACTIVATION
            )

    await write_audit_log(
        db,
        action="verify_domain",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="domain",
        target_id=hospital.aeo_domain,
        detail={
            "verified": serving_ready,
            "dns_verified": dns_check.verified,
            "cname_value": dns_check.cname_value,
            "address_values": dns_check.address_values,
            "expected_cname": dns_check.expected_cname,
            "expected_addresses": dns_check.expected_addresses,
            "verification_method": dns_check.verification_method,
            "certificate_ready": serving_ready,
            "certificate_phase": certificate.phase if certificate else None,
            "certificate_error_code": certificate.error_code if certificate else None,
            "previous_status": previous_status,
            "previous_site_live": previous_site_live,
            "new_status": hospital.status.value
            if hasattr(hospital.status, "value")
            else str(hospital.status),
            "new_site_live": bool(hospital.site_live),
            "activation_gate": gate if dns_check.verified else None,
        },
    )
    await db.commit()
    return {
        "domain": hospital.aeo_domain,
        "verified": serving_ready,
        "dns_verified": dns_check.verified,
        "cname_value": dns_check.cname_value,
        "address_values": dns_check.address_values,
        "expected_cname": dns_check.expected_cname,
        "expected_addresses": dns_check.expected_addresses,
        "verification_method": dns_check.verification_method,
        "certificate_ready": serving_ready,
        "certificate_phase": certificate.phase if certificate else None,
        "message": (
            "공개 도메인 상태가 확인되었습니다."
            if serving_ready
            else certificate.message
            if certificate is not None
            else "DNS 설정이 아직 확인되지 않았습니다."
        ),
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
