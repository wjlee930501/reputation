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

REPORT_TYPE_DISPLAY_LABELS = {
    "V0": "V0 진단",
    "MONTHLY": "월간 리포트",
}
SCREENING_STATUS_DISPLAY = {
    "PDF_PENDING": {"label": "PDF 생성 중"},
    "AWAITING_REVIEW": {"label": "검수 대기"},
    "DELIVERED": {"label": "전달 완료"},
}
PDF_STATUS_LABELS = {
    "READY": "다운로드 가능",
    "LINK_PENDING": "링크 준비 중",
    "GENERATING": "생성 중",
}


def _report_type_label(report_type: str | None) -> str | None:
    if report_type is None:
        return None
    return REPORT_TYPE_DISPLAY_LABELS.get(report_type) or report_type


def _screening_status(r: MonthlyReport) -> str:
    if r.sent_at:
        return "DELIVERED"
    if not r.pdf_path:
        return "PDF_PENDING"
    return "AWAITING_REVIEW"


def _pdf_status(r: MonthlyReport) -> str:
    if not r.pdf_path:
        return "GENERATING"
    if str(r.pdf_path).startswith("gs://") or Path(str(r.pdf_path)).exists():
        return "READY"
    return "LINK_PENDING"


def _serialize_display(r: MonthlyReport) -> dict:
    screening_status = _screening_status(r)
    pdf_status = _pdf_status(r)
    return {
        "report_type_label": _report_type_label(r.report_type),
        "screening_status": screening_status,
        "screening_status_label": SCREENING_STATUS_DISPLAY[screening_status]["label"],
        "pdf_status": pdf_status,
        "pdf_status_label": PDF_STATUS_LABELS[pdf_status],
    }


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
        "display": _serialize_display(r),
        "has_pdf": r.pdf_path is not None,
        "download_url": f"/api/admin/hospitals/{r.hospital_id}/reports/{r.id}/download" if r.pdf_path else None,
        "sov_summary": r.sov_summary if full else None,
        "content_summary": r.content_summary if full else None,
        "essence_summary": r.essence_summary if full else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
    }
    return d
