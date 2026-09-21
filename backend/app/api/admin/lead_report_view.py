"""Authenticated Admin access to a free-diagnosis report artifact."""

import logging
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.admin.accounts import require_active_account
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.lead_diagnosis import LeadDiagnosis, LeadReportArtifact, ReportStatus
from app.services.audit_log import write_audit_log
from app.services.report_file_integrity import ReportFileUnavailable, read_verified_report

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{lead_id}/diagnoses/{diagnosis_id}/report")
async def view_lead_diagnosis_report(
    lead_id: uuid.UUID,
    diagnosis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> Response:
    diagnosis = (
        await db.execute(
            select(LeadDiagnosis).where(
                LeadDiagnosis.id == diagnosis_id,
                LeadDiagnosis.lead_id == lead_id,
            )
        )
    ).scalar_one_or_none()
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="무료 진단을 찾을 수 없습니다.")
    if diagnosis.report_status == ReportStatus.PURGED.value:
        raise HTTPException(status_code=410, detail="보관 기간이 지나 삭제된 리포트입니다.")
    if diagnosis.report_status != ReportStatus.READY.value:
        raise HTTPException(
            status_code=409,
            detail="리포트가 아직 준비되지 않았습니다. 잠시 후 다시 확인해 주세요.",
        )

    artifact = (
        await db.execute(
            select(LeadReportArtifact)
            .where(
                LeadReportArtifact.diagnosis_id == diagnosis.id,
                LeadReportArtifact.purged_at.is_(None),
            )
            .order_by(LeadReportArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(
            status_code=409,
            detail="리포트 파일 연결이 아직 끝나지 않았습니다. 잠시 후 다시 확인해 주세요.",
        )

    try:
        data = await run_in_threadpool(
            read_verified_report, artifact.storage_uri, artifact.content_hash, artifact.byte_size
        )
    except ReportFileUnavailable as exc:
        raise HTTPException(status_code=409, detail="저장된 리포트 파일이 검증본과 다릅니다. 다시 생성한 뒤 확인해 주세요.") from exc

    await write_audit_log(
        db,
        action="view_lead_diagnosis_report",
        actor=actor.email,
        target_type="lead_diagnosis",
        target_id=diagnosis.id,
        detail={"artifact_version": artifact.version},
    )
    await db.commit()

    filename = f"{diagnosis.subject_hospital_name}_AI노출진단.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store, private",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
        },
    )
