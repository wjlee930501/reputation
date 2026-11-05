"""Admin API — v1.0 operations control plane.

Queue operations use two append-only audit events: a durable request before
broker dispatch and a confirmed event only after ``apply_async`` succeeds.
This keeps the audit trail truthful without pretending that a pre-dispatch
row proves broker acceptance.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.admin.accounts import require_active_account, require_owner_account
from app.api.admin.domain import verify_domain_for_hospital
from app.core.database import SyncSessionLocal, get_db
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
    ContentYieldHospital,
    ContentYieldResponse,
    ContentYieldWeek,
    CostGuardDailyLimitRequest,
    CostGuardDailyLimitResponse,
    CostGuardKillSwitchRequest,
    CostGuardKillSwitchResponse,
    CostGuardStatusResponse,
    LegacyBudgetReplacementRequest,
)
from app.services import cost_guard
from app.services import operation_run_keys as run_keys
from app.services.audit_log import default_actor, verified_request_actor, write_audit_log
from app.services.competitor_rejudge import (
    REJUDGE_OPERATION_TYPE,
    REJUDGE_QUEUE,
    build_rejudge_plan,
    registered_competitor_names,
)
from app.services.content_visibility import assess_sampled_visibility, visibility_load_only
from app.services.content_yield import compute_content_yield_async, kst_week_start
from app.services.doctor_report_ae_edit import (
    ae_edit_fact_problems,
    base_view_of,
    editable_fields,
    editable_summary,
    field_changes,
    merge_ae_fields,
    normalize_ae_fields,
    stored_ae_overrides,
    validate_ae_fields,
)
from app.services.doctor_report_v4 import (
    CaseCandidate,
    DoctorReportV4,
    apply_ae_overrides,
    compare_facts_v4,
    stored_facts_v4,
)
from app.services.hospital_lifecycle import missing_profile_requirement_keys
from app.services.incident_safety import sanitize_operator_text
from app.services.legacy_budget_replacement import (
    LEGACY_BUDGET_REPLACED_ACTION,
    legacy_incident_open_stmt,
)
from app.services.monthly_delivery_projection import (
    latest_delivery_event_subquery,
    latest_monthly_report_subquery,
)
from app.services.monthly_events import MonthlyRunStage
from app.services.monthly_period import (
    MonthlyPeriodError,
    is_monthly_recovery_window,
    reporting_period,
    require_closed_period,
    scheduled_report_period,
)
from app.services.monthly_remasure_gate import ManualRemasureLocked, authorize_manual_remasure
from app.services.monthly_report_snapshot import report_render_inputs, restore_doctor_view
from app.services.operation_run_keys import normalize_operation_key
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
from app.workers.competitor_refresh import rejudge_month_competitors
from app.workers.generation_attempt_state import (
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    budget_generation_attempt,
    legacy_budget_is_replaceable,
    read_generation_budget,
    replace_legacy_budget_and_release,
)
from app.workers.tasks import (
    build_aeo_site,
    generate_content_image,
    generate_monthly_report_for_hospital,
    load_ae_case_candidates,
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
    template_only: bool = False,
    allow_recovery_pending: bool = False,
    mode: str | None = None,
    mode_detail: dict[str, JSONValue] | None = None,
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
    expected: dict[str, JSONValue] = {"period_year": year, "period_month": month, "reason": reason}
    if template_only:
        # 템플릿 갱신은 같은 사유라도 일반 재생성과 다른 요청이다. 키를 섞어 쓰면 409.
        expected["mode"] = "TEMPLATE_REFRESH"
    if mode is not None:
        expected["mode"] = mode
    if allow_recovery_pending:
        expected["allow_recovery_pending"] = True
    if mode_detail:
        # AE 수정 판: 같은 템플릿 갱신 인자라도 수정 값(필드별 전/후)이 다르면 다른 요청이다 — 감사에 그대로 남는다.
        expected.update(mode_detail)
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
    request_payload_extra: dict[str, JSONValue] | None = None,
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
                request_payload_extra=request_payload_extra,
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

    SQL 술어(PUBLISHED · 미확인 · 공개시각 존재)만으로는 "공개 중"을 말할 수 없다. 공개
    페이지는 저장된 글을 다시 판정해 숨기므로, 그 글을 '공개 후 확인 필요'로 세면 AE는
    확인을 누를 때마다 409로 거절당하고 행은 큐에 남아 기한만 넘긴다(H-01). 표본은 위
    조건으로 이미 병원당 소수라, 각 행을 공개 표면과 같은 판정 함수로 다시 본다. 표본
    크기는 검수 표본 정책(월 시퀀스 1편 + 공개 후 수정, 미확인)이 묶고 AE가 확인할수록
    줄어들며, 그 행에서도 판정에 쓰는 컬럼만 싣는다.
    """
    overdue_before = datetime.now(UTC) - timedelta(hours=POST_PUBLISH_REVIEW_OVERDUE_HOURS)

    rows = (
        await db.execute(
            select(ContentItem, Hospital.id, Hospital.name)
            .options(visibility_load_only())
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                publicly_operational_hospital_predicate(),
                human_post_publish_review_predicate(),
            )
        )
    ).all()
    visibility = await assess_sampled_visibility(db, [row[0] for row in rows])

    samples: dict[uuid.UUID, dict] = {}
    for item, hospital_id, hospital_name in rows:
        bucket = samples.setdefault(
            hospital_id,
            {"name": hospital_name, "published_at": [], "withheld": 0},
        )
        if visibility[item.id].visible:
            bucket["published_at"].append(item.published_at)
        else:
            bucket["withheld"] += 1

    hospitals = [
        AttentionHospital(
            hospital_id=hospital_id,
            hospital_name=bucket["name"],
            unreviewed_count=len(bucket["published_at"]),
            overdue_count=sum(1 for at in bucket["published_at"] if at < overdue_before),
            oldest_published_at=min(bucket["published_at"], default=None),
            withheld_count=bucket["withheld"],
        )
        for hospital_id, bucket in samples.items()
    ]
    # 오래 방치된 병원이 위로 — 큐의 정렬 기준은 심각도가 아니라 경과 시간이다.
    # 확인 대상이 하나도 없고 공개 보류만 남은 병원은 비교할 경과 시간이 없어 맨 뒤에 둔다.
    hospitals.sort(
        key=lambda h: (h.oldest_published_at is None, h.oldest_published_at or datetime.min)
    )
    return AttentionQueueResponse(
        unreviewed_total=sum(h.unreviewed_count for h in hospitals),
        overdue_total=sum(h.overdue_count for h in hospitals),
        overdue_hours=POST_PUBLISH_REVIEW_OVERDUE_HOURS,
        withheld_total=sum(h.withheld_count for h in hospitals),
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


@cost_guard_router.get("/content-yield", response_model=ContentYieldResponse)
async def get_content_yield(
    weeks: int = Query(4, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    """주별·병원별 계약 예정 대비 실제 발행. 읽기 전용 집계다.

    월요일 주간 요약이 Slack에 한 줄로 보내는 것과 **같은 사실**을 같은 함수로
    계산한다. 여기서는 여러 주를 나란히 두어 버전업 전후를 비교할 수 있게 한다.
    가장 최근(진행 중)인 주가 맨 앞이며, 그 주는 아직 예정일이 남아 있어 수율이
    낮게 보이는 것이 정상이다.
    """

    current_week_start = kst_week_start(datetime.now(ZoneInfo("Asia/Seoul")).date())
    payload: list[ContentYieldWeek] = []
    for index in range(weeks):
        week_start = current_week_start - timedelta(days=7 * index)
        week_end = week_start + timedelta(days=7)
        facts = await compute_content_yield_async(
            db, period_start=week_start, period_end=week_end
        )
        active = [fact for fact in facts if fact.due or fact.published]
        payload.append(
            ContentYieldWeek(
                week_start=week_start,
                week_end=week_end - timedelta(days=1),
                due_total=sum(fact.due for fact in active),
                published_total=sum(fact.published for fact in active),
                hospitals=[
                    ContentYieldHospital(
                        hospital_id=fact.hospital_id,
                        hospital_name=fact.hospital_name,
                        due=fact.due,
                        published=fact.published,
                        published_with_reused_image=fact.published_with_reused_image,
                        retrying=fact.retrying,
                        topic_swapped=fact.topic_swapped,
                        operator_required=fact.operator_required,
                        blocked=fact.blocked,
                        blocked_by_cause=fact.blocked_by_cause,
                    )
                    for fact in sorted(active, key=lambda fact: fact.hospital_name)
                ],
            )
        )
    return ContentYieldResponse(weeks=payload)


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
    # 프로필이 미완인 병원의 V0는 워커도 시작하지 않는다. 큐에 넣으면 유료 측정 없이
    # 실패만 남으므로, 남은 필수 항목을 알려주고 여기서 거절한다.
    if not hospital.profile_complete:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_INCOMPLETE",
                "message": (
                    "병원 기본 정보가 완료되면 초기 진단이 자동으로 시작됩니다. "
                    "먼저 필수 항목을 채워 주세요."
                ),
                "missing": missing_profile_requirement_keys(hospital),
            },
        )
    # 태스크는 v0_report_done인 병원을 조용히 건너뛴다(중복 리포트·중복 측정 비용 방지).
    # 그대로 큐에 넣으면 화면은 "등록했습니다"라고 알리는데 아무 일도 일어나지 않아,
    # AE가 리포트를 기다리다 놓친다. 큐에 넣기 전에 사실대로 거절한다.
    # V0는 공개 라이프사이클과 독립적으로 돌아가므로 ACTIVE·PAUSED 병원도 실행 중일
    # 수 있다. ANALYZING 상태만 보면 라이브 병원에서 같은 유료 측정을 중복 큐잉한다.
    active = await latest_active_v0_run(db, hospital.id)
    alive = await v0_claim_is_alive(db, hospital.id)
    if alive or active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "이 병원은 이미 초기 진단을 백그라운드에서 만들고 있습니다. "
                    "다른 설정을 계속 진행하고 결과는 진행 상태에서 확인하세요."
                ),
                "operation_run_id": str(active.id) if active is not None else None,
                "operation_state": str(active.state) if active is not None else "RUNNING",
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
    measurement_mode: Literal["weekly", "monthly"] = "weekly",
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
    task_args: list[JSONValue] = [str(hospital.id)]
    operation_key = idempotency_key
    request_payload_extra: dict[str, JSONValue] | None = None
    detail = "AI 언급률 측정이 큐에 등록되었습니다."
    if measurement_mode == "monthly":
        now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
        try:
            period = scheduled_report_period(now_kst)
        except MonthlyPeriodError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not is_monthly_recovery_window(
            now_kst, period.year, period.month
        ):
            raise HTTPException(
                status_code=409,
                detail="지난달 월간 측정 복구는 매월 1일 00시 15분부터 07시까지 실행할 수 있습니다.",
            )
        if not hospital.monthly_sov_cohort:
            raise HTTPException(
                status_code=409,
                detail="이 병원은 월간 측정 코호트가 아니어서 주간 측정을 사용해야 합니다.",
            )
        if idempotency_key is None:
            raise HTTPException(
                status_code=400,
                detail="중복 측정을 막는 요청 키가 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
            )
        request_fingerprint = hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        try:
            authorization = await authorize_manual_remasure(
                db,
                hospital_id=hospital.id,
                year=period.year,
                month=period.month,
                request_fingerprint=request_fingerprint,
            )
        except ManualRemasureLocked as exc:
            decision = exc.decision
            raise HTTPException(
                status_code=409,
                detail={
                    "code": decision.code,
                    "message": decision.message,
                    "stalled_since": (
                        decision.stalled_since.isoformat() if decision.stalled_since else None
                    ),
                    "remaining_work": decision.remaining_work,
                },
            ) from exc
        operation_key = authorization.operation_key
        request_payload_extra = authorization.request_payload_extra
        task_args = [str(hospital.id), "monthly", period.year, period.month]
        detail = (
            f"{period.year}년 {period.month}월 월간 AI 언급률 재측정이 큐에 등록되었습니다."
        )
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="run_sov",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=run_sov_for_hospital,
        args=task_args,
        queue="sov",
        idempotency_key=operation_key,
        request_payload_extra=request_payload_extra,
    )
    return {
        "detail": detail,
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
    template_only: bool = Query(default=False),
    allow_recovery_pending: bool = Query(default=False),
    payload: MonthlyReportBuildRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    """월간 리포트를 병원 단위로 다시 만든다.

    월말 배치가 실패하면 그 병원은 다음 달 마지막 날까지 리포트가 비어 있었고, 복구
    경로가 `make monthly-report`(전체 병원·마지막 날 한정)뿐이었다. year/month를 주지
    않으면 지난달을 만든다 — 배치 실패는 대개 달이 바뀐 뒤에 발견된다.
    이미 있는 리포트는 덮어쓰지 않는다.

    `rebuild=true&template_only=true`는 저장된 숫자를 그대로 두고 원장·AE PDF의 문구와
    디자인만 새 버전으로 다시 그린다(새 템플릿 배포 뒤 지난달 리포트 갱신용). 숫자 판정이
    어긋나면 워커가 새 버전을 만들지 않는다. 측정 복구 창(1일 00:15~07:00 KST)에 측정이 덜 끝난 병원은
    워커가 막고, `allow_recovery_pending=true`(템플릿 갱신 전용)를 함께 주면 운영자 지정으로
    지금 저장된 숫자 그대로 갱신한다.
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
            else scheduled_report_period(now_kst)
        )
    except MonthlyPeriodError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    year, month = period.year, period.month
    template_only = template_only is True
    if template_only and not rebuild:
        raise HTTPException(
            status_code=400,
            detail="문구·디자인만 다시 만들기는 새 버전 만들기와 함께 요청해 주세요.",
        )
    allow_recovery_pending = allow_recovery_pending is True
    if allow_recovery_pending and not template_only:
        raise HTTPException(
            status_code=400,
            detail="측정 미완료 병원 허용은 문구·디자인만 다시 만들기에서만 쓸 수 있습니다.",
        )
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
    task_args: list[JSONValue] = [
        str(hospital.id),
        year,
        month,
        *([True] if rebuild else []),
        *([False, True] if template_only else []),
        *([True] if allow_recovery_pending else []),
    ]
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
            template_only=template_only,
            allow_recovery_pending=allow_recovery_pending,
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


# ── 경쟁 병원 반영: 경쟁사 재판정 → 경쟁 현황 부분 재생성 ─────────────────────────


class CompetitorRefreshRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


COMPETITOR_REFRESH_MODE = "COMPETITOR_REFRESH"
COMPETITOR_REFRESH_DEFAULT_REASON = "경쟁 병원 등록 반영(측정 숫자 변경 없음)"
_COMPETITOR_REFRESH_ACTIVE_STATES = (
    OperationRunState.REQUESTED,
    OperationRunState.QUEUED,
    OperationRunState.RUNNING,
)


def _stored_run_period(run: OperationRun) -> tuple[int, int] | None:
    """실행이 가리키는 연월 — 결과 요약, 없으면 저장된 디스패치 인자. 모르면 None(지난달로 짐작하지 않는다)."""
    summary = run.result_summary if isinstance(run.result_summary, dict) else {}
    year, month = summary.get("period_year"), summary.get("period_month")
    if isinstance(year, int) and isinstance(month, int):
        return year, month
    try:
        dispatch = parse_stored_dispatch(run.request_payload.get("_dispatch"))
    except UnsafeDispatchPayload:
        return None
    if dispatch is not None and len(dispatch.task_args) >= 3:
        year, month = dispatch.task_args[1:3]
        if isinstance(year, int) and isinstance(month, int):
            return year, month
    return None


def _competitor_report_run_view(run: OperationRun | None) -> dict[str, JSONValue] | None:
    if run is None:
        return None
    summary = run.result_summary if isinstance(run.result_summary, dict) else {}
    version = summary.get("report_version")
    report_id = _monthly_run_uuid(summary, "report_id")
    return {
        "run_id": str(run.id),
        "state": str(run.state),
        "stage": _monthly_run_stage(run).value,
        "report_id": str(report_id) if report_id is not None else None,
        "report_version": version if isinstance(version, int) else None,
        "safe_error_code": run.safe_error_code,
        "requested_at": run.requested_at.isoformat() if run.requested_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


async def _competitor_refresh_run_view(db: AsyncSession, run: OperationRun | None) -> dict[str, JSONValue] | None:
    if run is None:
        return None
    summary = run.result_summary if isinstance(run.result_summary, dict) else {}
    rejudge = summary.get("rejudge") if isinstance(summary.get("rejudge"), dict) else {}
    report_run = (
        await db.execute(
            select(OperationRun)
            .where(
                OperationRun.parent_run_id == run.id,
                OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            )
            .order_by(OperationRun.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "run_id": str(run.id),
        "state": str(run.state),
        "stage": summary.get("stage") if isinstance(summary.get("stage"), str) else None,
        "safe_error_code": run.safe_error_code,
        "failure_code": summary.get("failure_code") if isinstance(summary.get("failure_code"), str) else None,
        "requested_at": run.requested_at.isoformat() if run.requested_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "records_total": rejudge.get("records_total"),
        "pending_records": rejudge.get("pending_records"),
        "judged_records": rejudge.get("judged_records"),
        "unresolved_records": rejudge.get("unresolved_records"),
        "judged_names": rejudge.get("judged_names"),
        "unresolved_names": rejudge.get("unresolved_names"),
        "cost_blocked": rejudge.get("cost_blocked"),
        "report_run": _competitor_report_run_view(report_run),
    }


async def _competitor_chain_active(db: AsyncSession, run: OperationRun) -> bool:
    """재판정 실행이 열려 있거나, 끝났는데 그 자식 보고서 실행이 아직 열려 있으면 체인이 진행 중이다."""
    if run.state in _COMPETITOR_REFRESH_ACTIVE_STATES:
        return True
    if run.state != OperationRunState.SUCCEEDED:
        return False
    child_state = (
        await db.execute(
            select(OperationRun.state)
            .where(
                OperationRun.parent_run_id == run.id,
                OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            )
            .order_by(OperationRun.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return child_state in _COMPETITOR_REFRESH_ACTIVE_STATES


async def _competitor_refresh_runs(
    db: AsyncSession, hospital_id: uuid.UUID, year: int, month: int
) -> list[OperationRun]:
    runs = (
        await db.execute(
            select(OperationRun)
            .where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == REJUDGE_OPERATION_TYPE,
            )
            .order_by(OperationRun.requested_at.desc())
            .limit(50)
        )
    ).scalars()
    return [run for run in runs if _stored_run_period(run) == (year, month)]


@router.get("/{hospital_id}/reports/competitors/refresh")
async def competitor_refresh_status(
    hospital_id: uuid.UUID,
    year: int = Query(ge=2000, le=2200),
    month: int = Query(ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    """경쟁 병원 반영의 현재 상태 — 등록 경쟁사, 그 달 보고서가 쓴 답변 수와 아직 판정 없는 답변 수, 최근 실행.

    읽기 전용이다(공급자 호출·쓰기 없음). Admin 보고서 탭이 버튼을 그리기 전과 실행 중 진행을 볼 때 부른다.
    """
    hospital = await _get_hospital_or_404(db, hospital_id)
    plan = await db.run_sync(lambda session: build_rejudge_plan(session, hospital, year, month))
    runs = await _competitor_refresh_runs(db, hospital.id, year, month)
    return {
        "hospital_id": str(hospital.id),
        "period_year": year,
        "period_month": month,
        **plan.summary(),
        "latest_run": await _competitor_refresh_run_view(db, runs[0] if runs else None),
        "redeliver_required": True,
    }


@router.post("/{hospital_id}/reports/competitors/refresh")
async def request_competitor_refresh(
    hospital_id: uuid.UUID,
    year: int = Query(ge=2000, le=2200),
    month: int = Query(ge=1, le=12),
    payload: CompetitorRefreshRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    idempotency_key: IdempotencyKeyHeader = None,
):
    """경쟁 병원을 뒤늦게 등록한 뒤 지난달 보고서의 경쟁 현황만 다시 만든다.

    한 번의 요청이 체인 하나를 큐에 넣는다 — (1) 그 달 보고서가 쓴 저장 답변마다 아직 판정이 없는 등록 경쟁사만
    측정과 같은 판정으로 다시 묻고(`REJUDGE_COMPETITORS`, 답변을 새로 사지 않으므로 마감 뒤에도 된다),
    (2) 끝나면 같은 체인의 자식 실행이 숫자는 그대로 두고 경쟁 현황만 다시 계산한 새 버전을 만든다
    (`COMPETITOR_REFRESH` 템플릿 갱신). 새 버전은 이전 버전·전달 기록을 보존하며 **다시 전달해야 원장에게
    닿는다**. 등록 경쟁사는 병원 정보 탭에서 고친다(이 요청은 입력을 받지 않는다).
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    try:
        period = require_closed_period(year, month, now=now_kst)
    except MonthlyPeriodError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if idempotency_key is None:
        raise HTTPException(
            status_code=400,
            detail="중복 요청을 막는 요청 키가 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
        )
    hospital = await _get_hospital_or_404(db, hospital_id)
    registered = registered_competitor_names(hospital)
    if not registered:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_REGISTERED_COMPETITORS",
                "message": "등록된 경쟁 병원이 없습니다. 병원 정보 탭에서 경쟁 병원을 먼저 등록해 주세요.",
            },
        )
    plan = await db.run_sync(
        lambda session: build_rejudge_plan(session, hospital, period.year, period.month)
    )
    if plan.report_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_REPORT",
                "message": "이 달의 보고서가 아직 없습니다. 먼저 보고서를 만든 뒤 경쟁 병원을 반영해 주세요.",
            },
        )
    if plan.records_total == 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_FROZEN_RECORDS",
                "message": "이 보고서가 점수에 쓴 저장 답변이 없어 경쟁 병원을 반영할 수 없습니다.",
            },
        )
    reason = sanitize_operator_text(payload.reason if payload is not None else None, limit=200)
    if reason is None or len(reason) < 3:
        reason = COMPETITOR_REFRESH_DEFAULT_REASON
    # 병원 행 잠금(사유 감사 준비) **안에서** 진행 중인 체인을 본다 — 키가 다른 두 요청이 같은 달에 유료 판정 체인을
    # 겹쳐 시작하지 않게. 먼저 커밋한 요청의 실행 행을 나중 요청이 잠금을 받은 뒤 보게 된다.
    audit_created = await _prepare_monthly_rebuild_audit(
        db,
        hospital_id=hospital.id,
        idempotency_key=idempotency_key,
        year=period.year,
        month=period.month,
        reason=reason,
        mode=COMPETITOR_REFRESH_MODE,
    )
    normalized_key = run_keys.normalize_operation_key(idempotency_key)
    for run in await _competitor_refresh_runs(db, hospital.id, period.year, period.month):
        if run.idempotency_key == normalized_key or not await _competitor_chain_active(db, run):
            continue
        active_run_id = str(run.id)  # rollback이 ORM 속성을 만료시키므로 먼저 읽는다
        await db.rollback()  # 준비해 둔 사유 감사도 함께 버린다
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMPETITOR_REFRESH_IN_FLIGHT",
                "message": "이 달의 경쟁 병원 반영이 이미 진행 중입니다. 끝난 뒤 다시 확인해 주세요.",
                "operation_run_id": active_run_id,
            },
        )
    task_args: list[JSONValue] = [str(hospital.id), period.year, period.month]
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="rejudge_competitors",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=rejudge_month_competitors,
        args=task_args,
        queue=REJUDGE_QUEUE,
        idempotency_key=idempotency_key,
        request_payload_extra={
            "period_year": period.year,
            "period_month": period.month,
            "registered_competitors": list(registered),
            "records_total": plan.records_total,
            "pending_records": plan.pending_records,
        },
    )
    if dispatch.replayed:
        if audit_created:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="기존 요청에는 사유 기록이 없습니다. 새 요청 키로 다시 시도해 주세요.",
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
                detail="같은 요청 키가 다른 월에 이미 사용됐습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
            )
    return {
        "detail": (
            "경쟁 병원 반영을 요청했습니다. 저장된 답변에 등록 경쟁사 판정을 보충한 뒤 경쟁 현황만 다시 계산한 "
            "새 버전이 만들어집니다. 새 버전은 다시 전달해야 원장님께 반영됩니다."
        ),
        "hospital_id": str(hospital.id),
        "operation_run_id": str(dispatch.run.id),
        "operation_state": dispatch.run.state,
        "task_id": dispatch.run.task_id,
        "idempotent_replay": dispatch.replayed,
        **plan.summary(),
        "redeliver_required": True,
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
    body: LegacyBudgetReplacementRequest | None = Body(default=None),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital.id:
        raise HTTPException(status_code=404, detail="Content not found")
    if item.status in (
        ContentStatus.PUBLISHED,
        ContentStatus.CANCELLED,
        ContentStatus.WITHHELD,
    ):
        raise HTTPException(
            status_code=409,
            detail="Published, cancelled or withheld content cannot be regenerated",
        )
    # 한 번도 시도하지 않은 슬롯은 지출 0의 새 예산이다 — 감사된 교체 대상이 아니다.
    attempt = budget_generation_attempt(item)
    try:
        budget = read_generation_budget(attempt)
    except GenerationBudgetUnknown:
        budget = None
    if budget is not None and budget.reset_record is not None and body is not None:
        raise HTTPException(status_code=409, detail="Legacy generation budget was already replaced")
    # 지출을 정말 모르는 슬롯과, `LEGACY_SPEND_UNKNOWN` 실패 기록이 계수 0을 남겨 KNOWN으로
    # 읽히는 슬롯(게이트가 기록을 덮었으면 열린 사고가 근거) 모두 감사된 교체 대상이다.
    legacy_incident_open = bool(
        await db.scalar(legacy_incident_open_stmt(content_id, hospital.id))
    )
    legacy_unknown = legacy_budget_is_replaceable(
        attempt, legacy_incident_open=legacy_incident_open
    )
    if legacy_unknown:
        actor = verified_request_actor()
        if actor is None:
            raise HTTPException(status_code=401, detail="Verified Admin actor is required")
        if body is None or idempotency_key is None:
            raise HTTPException(
                status_code=409,
                detail="Legacy spend is unknown; reason and Idempotency-Key are required",
            )
        locked = await db.execute(
            select(ContentItem)
            .where(ContentItem.id == content_id, ContentItem.hospital_id == hospital.id)
            .with_for_update()
        )
        item = locked.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Content not found")
        try:
            replaced_summary, reset_record = replace_legacy_budget_and_release(
                item.essence_check_summary,
                actor=actor,
                reason=body.reason,
                idempotency_key=idempotency_key,
                legacy_incident_open=legacy_incident_open,
            )
        except GenerationBudgetExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        item.essence_check_summary = replaced_summary
        await write_audit_log(
            db,
            action=LEGACY_BUDGET_REPLACED_ACTION,
            hospital_id=hospital.id,
            actor=actor,
            target_type="content_item",
            target_id=content_id,
            detail=reset_record,
        )
        await db.commit()
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
        request_payload_extra=(
            {"reason": body.reason, "legacy_budget_replaced": legacy_unknown}
            if body is not None
            else None
        ),
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
    if item.status in (
        ContentStatus.PUBLISHED,
        ContentStatus.CANCELLED,
        ContentStatus.WITHHELD,
    ):
        raise HTTPException(
            status_code=409,
            detail="Published, cancelled or withheld content image cannot be regenerated",
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
            # 토큰도 함께 지운다. 시각만 지우면 풀어 준 줄 안 워커의 토큰 가드 write-back이
            # 여전히 맞아, 새 소유자가 잡기 전의 슬롯에 늦은 결과를 쓸 수 있다.
            .values(generation_claimed_at=None, generation_claim_token=None)
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


# ── AE 수정 판(2026-11-05) — 원장용 v4 보고서의 서술 문구·진료 항목 라벨만 고쳐 새 버전 ──────────────────


class ReportAeEditRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=200)
    # 경로 → 값. 값 null은 그 경로의 기존 수정을 지운다(원문으로). 문법은 `doctor_report_v4.parse_ae_path`.
    fields: dict[str, Any]


AE_EDIT_AUDIT_MODE = "AE_EDIT"
_AE_EDIT_DEFAULT_REASON = "AE 수정 판(문구·라벨만, 숫자 변경 없음)"


@dataclass(frozen=True, slots=True)
class _AeEditContext:
    hospital: Hospital
    report: MonthlyReport
    base: DoctorReportV4
    render_inputs: Any
    stored: dict[str, Any] | None


async def _load_ae_edit_context(db: AsyncSession, hospital_id: uuid.UUID, report_id: uuid.UUID) -> _AeEditContext:
    hospital = await _get_hospital_or_404(db, hospital_id)
    report = await db.get(MonthlyReport, report_id)
    if report is None or report.hospital_id != hospital_id or report.report_type != "MONTHLY":
        raise HTTPException(status_code=404, detail="월간 보고서를 찾을 수 없습니다.")
    latest = await db.scalar(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital_id,
            MonthlyReport.period_year == report.period_year,
            MonthlyReport.period_month == report.period_month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    )
    if latest is None or latest.id != report.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AE_EDIT_NOT_LATEST",
                "message": "이 달의 최신 버전만 고칠 수 있습니다. 최신 버전을 열어 다시 시도해 주세요.",
                "latest_report_id": str(latest.id) if latest is not None else None,
            },
        )
    render_inputs = report_render_inputs(report.content_summary)
    base = base_view_of(render_inputs) if render_inputs is not None else None
    if base is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AE_EDIT_REQUIRES_V4",
                "message": "새 형식(v4) 보고서만 고칠 수 있습니다. 먼저 '문구·디자인만 다시 만들기'로 새 형식 버전을 만들어 주세요.",
            },
        )
    return _AeEditContext(hospital, report, base, render_inputs, stored_ae_overrides(render_inputs))


def _ae_edit_receipt(run: OperationRun, *, report_id: uuid.UUID, changes: list, replayed: bool) -> dict[str, Any]:
    return {
        "detail": "AE 수정 판 생성을 요청했습니다. 새 버전이 만들어지면 다시 전달해야 원장님께 반영됩니다.",
        "hospital_id": str(run.hospital_id),
        "report_id": str(report_id),
        "operation_run_id": str(run.id),
        "operation_state": run.state,
        "task_id": run.task_id,
        "idempotent_replay": replayed,
        "changes": changes,
        "redelivery_required": True,
    }


async def _find_ae_edit_replay(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    idempotency_key: str,
    fields: Mapping[str, Any],
) -> dict[str, Any] | None:
    """같은 요청 키로 이미 접수한 AE 수정 판이 있으면 그 영수증(같은 판·같은 수정 값일 때). 다른 값이면 409."""
    key = normalize_operation_key(idempotency_key)
    if key is None:
        return None
    run = await db.scalar(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            OperationRun.idempotency_key == key,
        )
    )
    if run is None:
        return None
    stored = run.request_payload.get("ae_edit") if isinstance(run.request_payload, dict) else None
    if (
        not isinstance(stored, dict)
        or str(stored.get("report_id") or "") != str(report_id)
        or stored.get("fields") != {path: fields.get(path) for path in fields}
    ):
        raise HTTPException(
            status_code=409,
            detail="같은 요청 키가 다른 판이나 다른 수정 값에 이미 사용됐습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
        )
    return _ae_edit_receipt(run, report_id=report_id, changes=[], replayed=True)


def _load_ae_candidates_sync(hospital_id: uuid.UUID, report_id: uuid.UUID) -> list[CaseCandidate]:
    with SyncSessionLocal() as session:
        hospital = session.get(Hospital, hospital_id)
        report = session.get(MonthlyReport, report_id)
        if hospital is None or report is None:
            return []
        try:
            return load_ae_case_candidates(session, hospital, report)
        finally:
            session.rollback()


def _candidate_dict(candidate: CaseCandidate) -> dict[str, Any]:
    case = candidate.case
    return {
        "record_id": candidate.record_id,
        "keyword": case.keyword,
        "platform": case.platform,
        "measured_on": case.measured_on,
        "excerpt": case.excerpt,
        "highlight": case.highlight,
        "insight": case.insight,
    }


def _ae_edit_preview_payload(
    context: _AeEditContext,
    *,
    requested: Mapping[str, Any] | None,
    candidates: Sequence[CaseCandidate],
) -> dict[str, Any]:
    """쓰기 없이 수정 값을 pristine 뷰에 적용한 결과와 거절 사유. 요청 값이 없으면 저장된 수정 값 그대로를 미리본다."""
    base = context.base
    stored_fields = dict(context.stored["fields"]) if context.stored is not None else {}
    merged = merge_ae_fields(stored_fields, requested or {})
    problems = validate_ae_fields(merged, base, case_candidates=candidates)
    preview = None
    fact_problems: list[str] = []
    if not problems:
        normalized = normalize_ae_fields(merged, base, case_candidates=candidates)
        edited = apply_ae_overrides(base, normalized, case_candidates=candidates) if normalized else base
        fact_problems = ae_edit_fact_problems(base, edited)
        fact_problems.extend(
            compare_facts_v4(
                stored_facts_v4(context.report.sov_summary or {}, context.report.content_summary or {}),
                edited.facts(),
            )
        )
        preview = editable_summary(edited)
        merged = normalized
    current = restore_doctor_view(context.render_inputs.doctor_view)
    return {
        "report_id": str(context.report.id),
        "report_version": context.report.version,
        "template_version": base.template_version,
        "editable": editable_summary(base),
        "original_fields": editable_fields(base),
        "current": editable_summary(current) if isinstance(current, DoctorReportV4) else None,
        "overrides": stored_fields,
        "requested": dict(requested) if requested is not None else None,
        "merged": merged,
        "preview": preview,
        "problems": [problem.as_dict() for problem in problems]
        + [{"path": "", "code": "NUMBERS_CHANGED", "message": line} for line in fact_problems],
        "numbers_preserved": not problems and not fact_problems,
        "candidates": {
            "found": [_candidate_dict(c) for c in candidates if c.found],
            "missing": [_candidate_dict(c) for c in candidates if not c.found],
        },
        "edited_by": (context.stored or {}).get("edited_by"),
        "edited_at": (context.stored or {}).get("edited_at"),
        "history": list((context.stored or {}).get("history") or []),
        "dropped": getattr(context.render_inputs, "ae_overrides_dropped", None),
        "redelivery_required": True,
        "redelivery_note": "새 버전은 다시 전달해야 원장님께 반영됩니다. 기존 전달 기록은 그대로 남습니다.",
    }


@router.get("/{hospital_id}/reports/{report_id}/ae-edit/preview")
async def preview_report_ae_edit(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    overrides: str | None = Query(default=None, max_length=20000),
    db: AsyncSession = Depends(get_db),
):
    """AE 수정 판 미리보기(쓰기 없음) — 편집 가능 필드의 원문·현재 값·후보 사례와, `overrides`(JSON 경로→값)를
    적용했을 때의 결과와 거절 사유(금지 표현·내부 표식·새 숫자·화이트리스트 밖·숫자 불일치)를 돌려준다."""
    requested: dict[str, Any] | None = None
    if overrides is not None and overrides.strip():
        try:
            parsed = json.loads(overrides)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="수정 값(JSON)을 읽을 수 없습니다.") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="수정 값은 경로 → 값의 객체여야 합니다.")
        requested = parsed
    context = await _load_ae_edit_context(db, hospital_id, report_id)
    candidates = await run_in_threadpool(_load_ae_candidates_sync, hospital_id, report_id)
    return _ae_edit_preview_payload(context, requested=requested, candidates=candidates)


@router.post("/{hospital_id}/reports/{report_id}/ae-edit")
async def request_report_ae_edit(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    payload: ReportAeEditRequest,
    idempotency_key: RequiredIdempotencyKeyHeader,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    """AE 수정 판 — 검증을 통과한 수정 값으로 템플릿 갱신과 같은 OperationRun(`GENERATE_MONTHLY_REPORT`,
    `template_only`)을 큐에 넣는다. 워커가 숫자 보존 판정·원장 PDF 검증을 다시 하고 PASS일 때만 새 버전을 만든다.
    걸리면 422로 거절하고 아무것도 쓰지 않는다. 새 버전은 다시 전달해야 반영된다.

    사람 계정만 받는다(`require_active_account` — 시스템 actor는 403). 같은 요청 키의 기존 실행이 있으면 최신
    버전 검사보다 먼저 그 실행을 돌려준다 — 응답을 잃은 재요청이 새 버전이 이미 생긴 뒤 409로 떨어지지 않게."""
    replay = await _find_ae_edit_replay(db, hospital_id, report_id, idempotency_key, payload.fields)
    if replay is not None:
        return replay
    context = await _load_ae_edit_context(db, hospital_id, report_id)
    candidates = await run_in_threadpool(_load_ae_candidates_sync, hospital_id, report_id)
    preview = _ae_edit_preview_payload(context, requested=payload.fields, candidates=candidates)
    if preview["problems"]:
        raise HTTPException(
            status_code=422,
            detail={"code": "AE_EDIT_REJECTED", "message": "수정 값을 받을 수 없습니다.", "problems": preview["problems"]},
        )
    stored_fields = dict(context.stored["fields"]) if context.stored is not None else {}
    merged = preview["merged"]
    changes = field_changes(context.base, stored_fields, merged)
    if not changes:
        raise HTTPException(
            status_code=422,
            detail={"code": "AE_EDIT_NO_CHANGES", "message": "바뀐 내용이 없습니다. 원문과 같은 값은 수정으로 저장하지 않습니다."},
        )
    reason = sanitize_operator_text(payload.reason, limit=200) or _AE_EDIT_DEFAULT_REASON
    if len(reason) < 3:
        raise HTTPException(status_code=400, detail="새 버전을 만드는 이유를 3자 이상 입력해 주세요.")
    year, month = context.report.period_year, context.report.period_month
    requested_at = datetime.now(UTC).isoformat()
    ae_edit = {
        "report_id": str(context.report.id),
        "fields": {path: payload.fields.get(path) for path in payload.fields},
        "edited_by": actor.email,
        "requested_at": requested_at,
    }
    audit_created = await _prepare_monthly_rebuild_audit(
        db,
        hospital_id=context.hospital.id,
        idempotency_key=idempotency_key,
        year=year,
        month=month,
        reason=reason,
        template_only=True,
        mode_detail={"mode": AE_EDIT_AUDIT_MODE, "report_id": str(context.report.id), "changes": changes},
    )
    task_args: list[JSONValue] = [str(context.hospital.id), year, month, True, False, True]
    dispatch = await _enqueue_with_truthful_audit(
        db,
        action="generate_monthly_report",
        hospital_id=context.hospital.id,
        target_type="hospital",
        target_id=context.hospital.id,
        task=generate_monthly_report_for_hospital,
        args=task_args,
        queue="reports",
        idempotency_key=idempotency_key,
        request_payload_extra={"ae_edit": ae_edit},
    )
    if dispatch.replayed:
        if audit_created:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="기존 요청에는 수정 사유 기록이 없습니다. 새 요청 키로 다시 시도해 주세요.",
            )
        stored_request = dispatch.run.request_payload.get("ae_edit") if isinstance(dispatch.run.request_payload, dict) else None
        if not isinstance(stored_request, dict) or stored_request.get("fields") != ae_edit["fields"]:
            raise HTTPException(
                status_code=409,
                detail="같은 요청 키가 다른 수정 값에 이미 사용됐습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
            )
    return _ae_edit_receipt(dispatch.run, report_id=context.report.id, changes=changes, replayed=dispatch.replayed)
