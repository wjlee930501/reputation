"""Admin API — v1.0 operations control plane.

순서 규약: write_audit_log → db.commit() → external side-effect (apply_async).
이 순서를 어기면 큐는 실행되지만 audit row가 사라지는 정합성 결손이 발생함.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.domain import _normalize_dns_name, _resolve_cname, missing_live_prerequisites
from app.core.config import settings
from app.core.database import get_db
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.services.audit_log import default_actor, write_audit_log
from app.workers.tasks import build_aeo_site, regenerate_content_item, run_sov_for_hospital, trigger_v0_report

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Operations"])


@router.post("/{hospital_id}/operations/trigger-v0-report")
async def trigger_v0_report_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    await write_audit_log(
        db,
        action="trigger_v0_report",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital.id,
        detail={"queued": True},
    )
    await db.commit()
    trigger_v0_report.apply_async(args=[str(hospital.id)], queue="reports")
    return {"detail": "V0 report queued", "hospital_id": str(hospital.id)}


@router.post("/{hospital_id}/operations/run-sov")
async def run_sov_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
        raise HTTPException(status_code=409, detail="AI 언급률 측정은 ACTIVE 또는 PENDING_DOMAIN 상태에서 실행할 수 있습니다.")
    await write_audit_log(
        db,
        action="run_sov",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital.id,
        detail={"queued": True},
    )
    await db.commit()
    run_sov_for_hospital.apply_async(args=[str(hospital.id)], queue="sov")
    return {"detail": "AI 언급률 측정이 큐에 등록되었습니다.", "hospital_id": str(hospital.id)}


@router.post("/{hospital_id}/operations/rebuild-site")
async def rebuild_site_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    await write_audit_log(
        db,
        action="rebuild_site",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital.id,
        detail={"queued": True},
    )
    await db.commit()
    build_aeo_site.apply_async(args=[str(hospital.id)], queue="default")
    return {"detail": "Site rebuild queued", "hospital_id": str(hospital.id)}


@router.post("/{hospital_id}/operations/verify-domain")
async def verify_domain_operation(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    if not hospital.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다.")

    cname_value = await _resolve_cname(hospital.aeo_domain)
    verified = _normalize_dns_name(cname_value) == _normalize_dns_name(settings.CNAME_TARGET)
    previous_status = hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    previous_site_live = bool(hospital.site_live)
    if verified:
        missing_prerequisites = missing_live_prerequisites(hospital)
        if missing_prerequisites:
            raise HTTPException(
                status_code=409,
                detail=f"도메인 DNS는 확인됐지만 LIVE 전환 전 단계가 남아 있습니다: {', '.join(missing_prerequisites)}",
            )
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
            "verified": verified,
            "cname_value": cname_value,
            "expected_cname": settings.CNAME_TARGET,
            "previous_status": previous_status,
            "previous_site_live": previous_site_live,
            "new_status": hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status),
            "new_site_live": bool(hospital.site_live),
        },
    )
    await db.commit()
    return {
        "domain": hospital.aeo_domain,
        "verified": verified,
        "cname_value": cname_value,
        "expected_cname": settings.CNAME_TARGET,
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
    if item.status == ContentStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="Published content cannot be regenerated")
    await write_audit_log(
        db,
        action="regenerate_content",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="content_item",
        target_id=content_id,
        detail={"queued": True},
    )
    await db.commit()
    regenerate_content_item.apply_async(args=[str(content_id)], queue="content")
    return {"detail": "Content regeneration queued", "content_id": str(content_id)}


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
