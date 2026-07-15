"""
Admin API — 리포트 조회
GET  /admin/hospitals/{hospital_id}/reports              — 리포트 목록 (최신순)
GET  /admin/hospitals/{hospital_id}/reports/{report_id}  — 리포트 상세
GET  /admin/hospitals/{hospital_id}/reports/{report_id}/download — PDF signed URL
POST /admin/hospitals/{hospital_id}/reports/{report_id}/mark-sent — 원장 전달 완료 기록
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.schemas.report import ReportResponse
from app.services.audit_log import default_actor, write_audit_log
from app.services.essence_readiness import EssenceReadiness, get_essence_readiness
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
    if str(r.pdf_path).startswith("gs://") or _safe_local_report_path(str(r.pdf_path)) is not None:
        return "READY"
    return "LINK_PENDING"


def _safe_local_report_path(pdf_path: str) -> Path | None:
    try:
        report_root = Path(settings.REPORT_OUTPUT_DIR).resolve(strict=False)
        candidate = Path(pdf_path).resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    try:
        candidate.relative_to(report_root)
    except ValueError:
        return None

    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


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


def _report_delivery_blockers(r: MonthlyReport) -> list[str]:
    """Return operator-facing reasons a report must not be marked delivered.

    V0 is intentionally available before source/Essence onboarding. Monthly
    reports, however, are customer deliverables and must contain a complete,
    current, medically screened operating snapshot.
    """
    blockers: list[str] = []
    if _pdf_status(r) != "READY":
        blockers.append("PDF 다운로드 파일이 준비되지 않았습니다.")

    sov_summary = r.sov_summary if isinstance(r.sov_summary, dict) else {}
    if sov_summary.get("sov_pct") is None:
        blockers.append("AI 언급률 요약이 없습니다.")

    if r.report_type != "MONTHLY":
        return blockers

    content_summary = r.content_summary if isinstance(r.content_summary, dict) else {}
    if "published_count" not in content_summary:
        blockers.append("월간 콘텐츠 발행 요약이 없습니다.")

    essence = r.essence_summary if isinstance(r.essence_summary, dict) else {}
    if not essence.get("approved_philosophy_exists"):
        blockers.append("승인된 콘텐츠 운영 기준이 없습니다.")
    if essence.get("source_stale"):
        blockers.append("리포트의 콘텐츠 운영 기준이 현재 자료와 일치하지 않습니다.")

    source_count = essence.get("source_count")
    processed_count = essence.get("processed_source_count")
    if not isinstance(source_count, int) or source_count < 1:
        blockers.append("리포트에 반영된 온보딩 자료가 없습니다.")
    elif processed_count != source_count:
        blockers.append("처리되지 않은 온보딩 자료가 남아 있습니다.")

    if (essence.get("needs_review_content_count") or 0) > 0:
        blockers.append("운영 기준 재검수가 필요한 콘텐츠가 남아 있습니다.")
    if (essence.get("missing_philosophy_content_count") or 0) > 0:
        blockers.append("승인된 운영 기준 없이 생성된 콘텐츠가 남아 있습니다.")
    if essence.get("medical_risk_findings"):
        blockers.append("의료광고 리스크 표현이 발견된 콘텐츠가 있습니다.")
    return blockers


def _current_essence_delivery_blockers(
    r: MonthlyReport,
    readiness: EssenceReadiness,
) -> list[str]:
    """Validate the stored report snapshot against current source truth."""
    if r.report_type != "MONTHLY":
        return []

    blockers: list[str] = []
    if readiness.current is None:
        blockers.append(
            "현재 병원 자료와 일치하는 승인된 콘텐츠 운영 기준이 없습니다. 리포트를 다시 생성해 주세요."
        )
    if readiness.has_unprocessed_sources:
        blockers.append("현재 처리되지 않은 온보딩 자료가 남아 있습니다.")

    essence = r.essence_summary if isinstance(r.essence_summary, dict) else {}
    stored_version = essence.get("philosophy_version")
    current_version = readiness.current.version if readiness.current is not None else None
    if current_version is not None and stored_version != current_version:
        blockers.append(
            "리포트 생성 후 콘텐츠 운영 기준 버전이 변경되었습니다. 리포트를 다시 생성해 주세요."
        )
    return blockers


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
async def get_report(
    hospital_id: uuid.UUID, report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """리포트 상세"""
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return _serialize(r, full=True)


@router.get("/{hospital_id}/reports/{report_id}/download")
async def download_report(
    hospital_id: uuid.UUID, report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """PDF 다운로드 — GCS signed URL로 리다이렉트 (1시간 만료)"""
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")

    if not r.pdf_path:
        raise HTTPException(status_code=404, detail="PDF 경로가 없습니다.")

    if r.pdf_path.startswith("gs://"):
        download_name = f"report-{r.period_year}-{r.period_month:02d}.pdf"
        signed_url = get_signed_url(
            r.pdf_path,
            expiration_hours=1,
            response_disposition=f'attachment; filename="{download_name}"',
        )
        if not signed_url:
            raise HTTPException(
                status_code=503,
                detail="PDF URL 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
            )
        return RedirectResponse(
            url=signed_url,
            status_code=302,
            headers={
                "Cache-Control": "no-store, private",
                "Referrer-Policy": "no-referrer",
                "Content-Disposition": f'attachment; filename="{download_name}"',
            },
        )

    local_path = _safe_local_report_path(r.pdf_path)
    if not local_path:
        raise HTTPException(
            status_code=404,
            detail="PDF 파일을 찾을 수 없습니다.",
        )

    return FileResponse(
        path=str(local_path),
        filename=f"report-{r.period_year}-{r.period_month:02d}.pdf",
        media_type="application/pdf",
        headers={"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer"},
    )


@router.post("/{hospital_id}/reports/{report_id}/mark-sent", response_model=ReportResponse)
async def mark_report_sent(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """원장 보고 완료 기록 (A4) — sent_at=now 설정, 멱등.

    이미 전달 완료된 리포트에 다시 호출하면 기존 sent_at을 유지한 채 200을 반환한다.
    """
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")

    if r.sent_at is None:
        blockers = _report_delivery_blockers(r)
        if not blockers and r.report_type == "MONTHLY":
            readiness = await get_essence_readiness(db, hospital_id)
            blockers.extend(_current_essence_delivery_blockers(r, readiness))
        if blockers:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPORT_NOT_READY",
                    "message": "원장 전달 전 필수 검수를 완료해 주세요.",
                    "blockers": blockers,
                },
            )
        r.sent_at = datetime.now(timezone.utc)
        await write_audit_log(
            db,
            action="mark_report_sent",
            hospital_id=hospital_id,
            actor=default_actor(),
            target_type="monthly_report",
            target_id=report_id,
            detail={
                "report_type": r.report_type,
                "period_year": r.period_year,
                "period_month": r.period_month,
                "sent_at": r.sent_at.isoformat(),
            },
        )
        await db.commit()
        await db.refresh(r)

    return _serialize(r, full=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _serialize(r: MonthlyReport, full: bool = False) -> dict:
    delivery_blockers = _report_delivery_blockers(r)
    d = {
        "id": str(r.id),
        "hospital_id": str(r.hospital_id),
        "period_year": r.period_year,
        "period_month": r.period_month,
        "report_type": r.report_type,
        "display": _serialize_display(r),
        "has_pdf": r.pdf_path is not None,
        "download_url": f"/api/admin/hospitals/{r.hospital_id}/reports/{r.id}/download"
        if r.pdf_path
        else None,
        "sov_summary": r.sov_summary if full else None,
        "content_summary": r.content_summary if full else None,
        "essence_summary": r.essence_summary if full else None,
        "delivery_ready": not delivery_blockers,
        "delivery_blockers": delivery_blockers,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
    }
    return d
