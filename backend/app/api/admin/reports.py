"""
Admin API — 리포트 조회
GET /admin/hospitals/{hospital_id}/reports              — 리포트 목록 (최신순)
GET /admin/hospitals/{hospital_id}/reports/{report_id}  — 리포트 상세
GET /admin/hospitals/{hospital_id}/reports/{report_id}/download — PDF signed URL
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.schemas.report import ReportResponse
from app.services.gcs_utils import get_signed_url

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Reports"])


@router.get("/{hospital_id}/reports", response_model=list[ReportResponse])
async def list_reports(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """리포트 목록 (최신순)"""
    await _get_hospital_or_404(db, hospital_id)

    result = await db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.hospital_id == hospital_id)
        .order_by(MonthlyReport.created_at.desc())
    )
    reports = result.scalars().all()
    return [_serialize(r) for r in reports]


@router.get("/{hospital_id}/reports/{report_id}", response_model=ReportResponse)
async def get_report(hospital_id: uuid.UUID, report_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """리포트 상세"""
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return _serialize(r, full=True)


@router.get("/{hospital_id}/reports/{report_id}/download")
async def download_report(hospital_id: uuid.UUID, report_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """PDF 다운로드 — GCS signed URL로 리다이렉트 (1시간 만료)"""
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")

    if not r.pdf_path:
        raise HTTPException(status_code=404, detail="PDF 경로가 없습니다.")

    if not r.pdf_path.startswith("gs://"):
        local_path = Path(r.pdf_path)
        if local_path.exists() and local_path.is_file():
            return FileResponse(
                path=str(local_path),
                filename=local_path.name,
                media_type="application/pdf",
            )

    signed_url = get_signed_url(r.pdf_path)
    if not signed_url:
        raise HTTPException(
            status_code=503,
            detail="PDF URL 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        )

    return RedirectResponse(url=signed_url, status_code=302)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _serialize(r: MonthlyReport, full: bool = False) -> dict:
    d = {
        "id": str(r.id),
        "hospital_id": str(r.hospital_id),
        "period_year": r.period_year,
        "period_month": r.period_month,
        "report_type": r.report_type,
        "has_pdf": r.pdf_path is not None,
        "download_url": f"/api/admin/hospitals/{r.hospital_id}/reports/{r.id}/download" if r.pdf_path else None,
        "sov_summary": r.sov_summary if full else None,
        "content_summary": r.content_summary if full else None,
        "essence_summary": r.essence_summary if full else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
    }
    return d
