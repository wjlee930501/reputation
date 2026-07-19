"""Admin API — v1.0 operations control plane.

Queue operations use two append-only audit events: a durable request before
broker dispatch and a confirmed event only after ``apply_async`` succeeds.
This keeps the audit trail truthful without pretending that a pre-dispatch
row proves broker acceptance.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.domain import (
    check_domain_dns,
    domain_dns_strategy_for_hospital,
    ensure_verified_domain_certificate,
    missing_live_prerequisites,
)
from app.core.database import get_db
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.sov import AIQueryTarget, AIQueryVariant
from app.schemas.operations import (
    CostGuardKillSwitchRequest,
    CostGuardKillSwitchResponse,
    CostGuardStatusResponse,
)
from app.services import cost_guard
from app.services.audit_log import default_actor, write_audit_log
from app.workers.tasks import (
    build_aeo_site,
    generate_content_image,
    regenerate_content_item,
    run_sov_for_hospital,
    trigger_v0_report,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Operations"])

# 비용 가드는 병원 단위가 아닌 전역 제어 평면이라 별도 prefix를 쓴다.
cost_guard_router = APIRouter(prefix="/admin/operations", tags=["Admin — Cost Guard"])


async def _enqueue_with_truthful_audit(
    db: AsyncSession,
    *,
    action: str,
    hospital_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID | str,
    task: Any,
    args: list[str],
    queue: str,
) -> str | None:
    """Durably record request, dispatch, then record broker acceptance."""
    actor = default_actor()
    await write_audit_log(
        db,
        action=f"{action}_requested",
        hospital_id=hospital_id,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        detail={"queued": False, "queue": queue},
    )
    await db.commit()

    try:
        result = task.apply_async(args=args, queue=queue)
    except Exception as exc:
        await write_audit_log(
            db,
            action=f"{action}_queue_failed",
            hospital_id=hospital_id,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            detail={"queued": False, "queue": queue, "error_type": type(exc).__name__},
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="작업 큐 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc

    task_id = str(result.id) if getattr(result, "id", None) else None
    await write_audit_log(
        db,
        action=action,
        hospital_id=hospital_id,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        detail={"queued": True, "queue": queue, "task_id": task_id},
    )
    await db.commit()
    return task_id


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


@router.post("/{hospital_id}/operations/trigger-v0-report")
async def trigger_v0_report_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    await _enqueue_with_truthful_audit(
        db,
        action="trigger_v0_report",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=trigger_v0_report,
        args=[str(hospital.id)],
        queue="reports",
    )
    return {"detail": "V0 report queued", "hospital_id": str(hospital.id)}


@router.post("/{hospital_id}/operations/run-sov")
async def run_sov_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
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
    await _enqueue_with_truthful_audit(
        db,
        action="run_sov",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=run_sov_for_hospital,
        args=[str(hospital.id)],
        queue="sov",
    )
    return {"detail": "AI 언급률 측정이 큐에 등록되었습니다.", "hospital_id": str(hospital.id)}


@router.post("/{hospital_id}/operations/rebuild-site")
async def rebuild_site_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    await _enqueue_with_truthful_audit(
        db,
        action="rebuild_site",
        hospital_id=hospital.id,
        target_type="hospital",
        target_id=hospital.id,
        task=build_aeo_site,
        args=[str(hospital.id)],
        queue="default",
    )
    return {"detail": "Site rebuild queued", "hospital_id": str(hospital.id)}


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
        missing_prerequisites = missing_live_prerequisites(hospital)
        if missing_prerequisites:
            raise HTTPException(
                status_code=409,
                detail=f"도메인 DNS는 확인됐지만 LIVE 전환 전 단계가 남아 있습니다: {', '.join(missing_prerequisites)}",
            )
        certificate = await ensure_verified_domain_certificate(hospital.aeo_domain)
        serving_ready = certificate is None or certificate.ready
        if serving_ready:
            hospital.site_live = True
            hospital.status = HospitalStatus.ACTIVE

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
    await _enqueue_with_truthful_audit(
        db,
        action="regenerate_content",
        hospital_id=hospital.id,
        target_type="content_item",
        target_id=content_id,
        task=regenerate_content_item,
        args=[str(content_id)],
        queue="content",
    )
    return {"detail": "Content regeneration queued", "content_id": str(content_id)}


@router.post("/{hospital_id}/content/{content_id}/regenerate-image")
async def regenerate_content_image_operation(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
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
    await _enqueue_with_truthful_audit(
        db,
        action="regenerate_content_image",
        hospital_id=hospital.id,
        target_type="content_item",
        target_id=content_id,
        task=generate_content_image,
        args=[str(content_id)],
        queue="content",
    )
    return {"detail": "Content image generation queued", "content_id": str(content_id)}


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
