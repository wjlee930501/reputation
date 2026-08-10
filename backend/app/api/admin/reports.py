"""
Admin API — 리포트 조회
GET  /admin/hospitals/{hospital_id}/reports              — 리포트 목록 (최신순)
GET  /admin/hospitals/{hospital_id}/reports/{report_id}  — 리포트 상세
GET  /admin/hospitals/{hospital_id}/reports/{report_id}/download — PDF signed URL
POST /admin/hospitals/{hospital_id}/reports/{report_id}/mark-sent — 원장 전달 완료 기록
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account, require_owner_account
from app.core.config import settings
from app.core.database import get_db
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyDeliveryEvent,
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
    ReportArtifactState,
    ReportDeliveryEventType,
)
from app.models.report import MonthlyReport
from app.schemas.report import (
    ReportDeliveryCorrectionRequest,
    ReportDeliveryRequest,
    ReportDeliveryRescindRequest,
    ReportListResponse,
    ReportResponse,
)
from app.services.audit_log import write_audit_log
from app.services.essence_readiness import EssenceReadiness, get_essence_readiness
from app.services.gcs_utils import get_signed_url
from app.services.report_artifact_validation import parse_doctor_artifact_metadata
from app.services.report_review_evidence import build_report_review_evidence

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
DOCTOR_ARTIFACT_STATE_LABELS = {
    ReportArtifactState.MISSING: "원장 전달용 PDF가 없습니다",
    ReportArtifactState.INVALID: "원장 전달용 PDF를 다시 만들어야 합니다",
    ReportArtifactState.VALID: "원장 전달용 PDF 검증 완료",
}


def _report_type_label(report_type: str | None) -> str | None:
    if report_type is None:
        return None
    return REPORT_TYPE_DISPLAY_LABELS.get(report_type) or report_type


@dataclass(frozen=True, slots=True)
class DeliveryGate:
    ready: bool
    code: str | None
    message: str | None


def _screening_status(r: MonthlyReport, *, delivered: bool | None = None) -> str:
    if delivered if delivered is not None else bool(r.sent_at):
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


def _serialize_display(r: MonthlyReport, *, delivered: bool | None = None) -> dict:
    screening_status = _screening_status(r, delivered=delivered)
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


def _artifact_state(
    report: MonthlyReport, artifact: MonthlyReportArtifact | None
) -> ReportArtifactState:
    if artifact is None:
        return ReportArtifactState.MISSING
    metadata = parse_doctor_artifact_metadata(artifact.validation_metadata)
    valid = (
        artifact.report_id == report.id
        and artifact.audience == "DOCTOR"
        and artifact.path == report.doctor_pdf_path
        and artifact.validated is True
        and len(artifact.sha256) == 64
        and all(character in "0123456789abcdef" for character in artifact.sha256)
        and artifact.byte_size > 0
        and metadata is not None
        and metadata.sha256 == artifact.sha256
        and metadata.byte_size == artifact.byte_size
    )
    return ReportArtifactState.VALID if valid else ReportArtifactState.INVALID


def _delivery_gate(
    report: MonthlyReport,
    manifest: MonthlyMeasurementManifest | None,
    artifact: MonthlyReportArtifact | None,
) -> DeliveryGate:
    """Derive customer readiness only from server-owned persisted facts."""
    if report.report_type == "MONTHLY":
        counts_complete = (
            report.planned_count > 0
            and report.success_count == report.planned_count
            and report.failed_count == 0
        )
        if report.quality != "COMPLETE" or not counts_complete or manifest is None:
            return DeliveryGate(False, "coverage_incomplete", "월간 측정 커버리지가 완전하지 않습니다.")
        manifest_matches = (
            manifest.id == report.manifest_id
            and manifest.hospital_id == report.hospital_id
            and manifest.period_year == report.period_year
            and manifest.period_month == report.period_month
        )
        if not manifest_matches:
            return DeliveryGate(
                False,
                "manifest_mismatch",
                "이번 달 필수 측정 결과가 이 병원과 보고 기간에 연결되지 않았습니다.",
            )
        if manifest.closed_at is None:
            return DeliveryGate(
                False,
                "manifest_open",
                "이번 달 필수 측정 집계가 아직 끝나지 않았습니다.",
            )

    state = _artifact_state(report, artifact)
    if state is ReportArtifactState.MISSING:
        return DeliveryGate(False, "doctor_artifact_missing", "검증된 원장 보고용 PDF가 없습니다.")
    if state is ReportArtifactState.INVALID:
        return DeliveryGate(False, "doctor_artifact_invalid", "원장 보고용 PDF 검증 정보가 유효하지 않습니다.")

    blockers = _report_delivery_blockers(report)
    if blockers:
        return DeliveryGate(False, "report_blocked", blockers[0])
    return DeliveryGate(True, None, None)


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


@router.get(
    "/{hospital_id}/reports",
    response_model=list[ReportListResponse],
)
async def list_reports(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """리포트 목록 (최신순)"""
    await _get_hospital_or_404(db, hospital_id)

    result = await db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.hospital_id == hospital_id)
        .order_by(MonthlyReport.created_at.desc())
    )
    reports = result.scalars().all()
    return [await _serialize_report(db, r) for r in reports]


@router.get("/{hospital_id}/reports/{report_id}", response_model=ReportResponse)
async def get_report(
    hospital_id: uuid.UUID, report_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """리포트 상세"""
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return await _serialize_report(db, r, full=True)


def _content_disposition(ascii_name: str, display_name: str) -> str:
    """Content-Disposition — ASCII 파일명 + RFC 5987 UTF-8 파일명.

    HTTP 헤더 값은 latin-1로 인코딩되므로 한글 파일명을 filename에 그대로 넣으면
    응답 생성 단계에서 UnicodeEncodeError가 난다. 한글은 filename*로만 보낸다.
    """
    quoted = quote(display_name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


@router.get("/{hospital_id}/reports/{report_id}/download")
async def download_report(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    audience: str = Query(default="ae", pattern="^(ae|doctor)$"),
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    """PDF 다운로드 — GCS signed URL로 리다이렉트 (1시간 만료).

    `audience=doctor`는 원장에게 그대로 전달하는 1페이지 판본이다. 같은 데이터를
    다른 편집으로 렌더한 별도 파일이라 AE용과 경로가 다르다.
    """
    await _get_hospital_or_404(db, hospital_id)

    r = await db.get(MonthlyReport, report_id)
    if not r or r.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Report not found")

    is_doctor = audience == "doctor"
    pdf_path = r.doctor_pdf_path if is_doctor else r.pdf_path
    if is_doctor:
        await _assert_delivery_actor(db, r.hospital_id, actor)
        artifact = await _get_doctor_artifact(db, r.id)
        await _assert_customer_ready(db, r, await _get_manifest(db, r.manifest_id), artifact)
    if not pdf_path:
        raise HTTPException(
            status_code=404,
            detail=(
                "원장 보고용 리포트가 아직 만들어지지 않았습니다."
                if is_doctor
                else "PDF 경로가 없습니다."
            ),
        )

    # HTTP 헤더는 latin-1만 담을 수 있어 파일명에 한글을 그대로 넣으면 응답이 500이 된다.
    # ASCII 이름을 filename에 두고, 한글 이름은 RFC 5987 filename*으로 함께 보낸다.
    suffix = "-doctor" if is_doctor else ""
    display_suffix = "-원장보고" if is_doctor else ""
    stem = f"report-{r.period_year}-{r.period_month:02d}"
    download_name = f"{stem}{suffix}.pdf"
    disposition = _content_disposition(download_name, f"{stem}{display_suffix}.pdf")

    if pdf_path.startswith("gs://"):
        signed_url = get_signed_url(
            pdf_path,
            expiration_hours=1,
            response_disposition=disposition,
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
                "Content-Disposition": disposition,
            },
        )

    local_path = _safe_local_report_path(pdf_path)
    if not local_path:
        raise HTTPException(
            status_code=404,
            detail="PDF 파일을 찾을 수 없습니다.",
        )

    return FileResponse(
        path=str(local_path),
        filename=download_name,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer"},
    )


@router.post("/{hospital_id}/reports/{report_id}/mark-sent", response_model=ReportResponse)
async def mark_report_sent(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    body: ReportDeliveryRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    """Append a delivery or re-delivery event bound to the validated doctor PDF."""
    await _get_hospital_or_404(db, hospital_id)
    report = await _get_locked_report_or_404(db, hospital_id, report_id)
    await _assert_delivery_actor(db, report.hospital_id, actor)
    manifest = await _get_manifest(db, report.manifest_id)
    artifact = await _get_doctor_artifact(db, report.id)
    await _assert_customer_ready(db, report, manifest, artifact)
    if artifact is None or body.artifact_sha256 != artifact.sha256:
        raise _delivery_conflict(
            "artifact_mismatch", "다운로드한 원장 보고용 PDF와 현재 검증본이 일치하지 않습니다."
        )

    events = await _get_delivery_events(db, report.id)
    effective = _effective_delivery_event(events)
    if effective is not None and effective.event_type != ReportDeliveryEventType.RESCINDED:
        raise _delivery_conflict("already_delivered", "이미 유효한 전달 기록이 있습니다.")
    event_type = (
        ReportDeliveryEventType.REDELIVERED
        if effective is not None and effective.event_type == ReportDeliveryEventType.RESCINDED
        else ReportDeliveryEventType.DELIVERED
    )
    now = datetime.now(timezone.utc)
    event = _new_delivery_event(
        report=report,
        artifact=artifact,
        event_type=event_type,
        actor=actor,
        recipient=body.recipient_label,
        channel=body.channel,
        note=body.note,
        reason=None,
        now=now,
    )
    db.add(event)
    if report.sent_at is None:
        report.sent_at = now
    await _audit_delivery(db, report, actor, event)
    await db.commit()
    await db.refresh(report)
    return await _serialize_report(db, report, full=True)


@router.post(
    "/{hospital_id}/reports/{report_id}/correct-delivery", response_model=ReportResponse
)
async def correct_report_delivery(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    body: ReportDeliveryCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    """Append an OWNER-authorized correction without rewriting delivery history."""
    if actor.role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail={"code": "DELIVERY_OWNER_REQUIRED"})
    await _get_hospital_or_404(db, hospital_id)
    report = await _get_locked_report_or_404(db, hospital_id, report_id)
    manifest = await _get_manifest(db, report.manifest_id)
    artifact = await _get_doctor_artifact(db, report.id)
    await _assert_customer_ready(db, report, manifest, artifact)
    if artifact is None or body.artifact_sha256 != artifact.sha256:
        raise _delivery_conflict("artifact_mismatch", "현재 검증된 원장 보고용 PDF가 아닙니다.")
    effective = _effective_delivery_event(await _get_delivery_events(db, report.id))
    if effective is None or effective.event_type == ReportDeliveryEventType.RESCINDED:
        raise _delivery_conflict("delivery_not_effective", "수정할 유효 전달 기록이 없습니다.")
    event = _new_delivery_event(
        report=report,
        artifact=artifact,
        event_type=ReportDeliveryEventType.CORRECTED,
        actor=actor,
        recipient=body.recipient_label,
        channel=body.channel,
        note=body.note,
        reason=body.reason,
        now=datetime.now(timezone.utc),
    )
    db.add(event)
    await _audit_delivery(db, report, actor, event)
    await db.commit()
    return await _serialize_report(db, report, full=True)


@router.post(
    "/{hospital_id}/reports/{report_id}/rescind-delivery", response_model=ReportResponse
)
async def rescind_report_delivery(
    hospital_id: uuid.UUID,
    report_id: uuid.UUID,
    body: ReportDeliveryRescindRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    """Append an OWNER-authorized rescission; sent_at remains compatibility history."""
    if actor.role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail={"code": "DELIVERY_OWNER_REQUIRED"})
    await _get_hospital_or_404(db, hospital_id)
    report = await _get_locked_report_or_404(db, hospital_id, report_id)
    effective = _effective_delivery_event(await _get_delivery_events(db, report.id))
    if effective is None or effective.event_type == ReportDeliveryEventType.RESCINDED:
        raise _delivery_conflict("delivery_not_effective", "철회할 유효 전달 기록이 없습니다.")
    artifact = await db.get(MonthlyReportArtifact, effective.artifact_id)
    if artifact is None:
        raise _delivery_conflict("doctor_artifact_missing", "전달에 연결된 원장 PDF가 없습니다.")
    metadata = effective.metadata_json if isinstance(effective.metadata_json, dict) else {}
    event = _new_delivery_event(
        report=report,
        artifact=artifact,
        event_type=ReportDeliveryEventType.RESCINDED,
        actor=actor,
        recipient=effective.recipient,
        channel=str(metadata.get("channel") or ""),
        note=None,
        reason=body.reason,
        now=datetime.now(timezone.utc),
    )
    db.add(event)
    await _audit_delivery(db, report, actor, event)
    await db.commit()
    return await _serialize_report(db, report, full=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


async def _get_locked_report_or_404(
    db: AsyncSession, hospital_id: uuid.UUID, report_id: uuid.UUID
) -> MonthlyReport:
    result = await db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.id == report_id, MonthlyReport.hospital_id == hospital_id)
        .with_for_update()
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


async def _get_manifest(
    db: AsyncSession, manifest_id: uuid.UUID | None
) -> MonthlyMeasurementManifest | None:
    return await db.get(MonthlyMeasurementManifest, manifest_id) if manifest_id else None


async def _get_doctor_artifact(
    db: AsyncSession, report_id: uuid.UUID
) -> MonthlyReportArtifact | None:
    result = await db.execute(
        select(MonthlyReportArtifact).where(
            MonthlyReportArtifact.report_id == report_id,
            MonthlyReportArtifact.audience == "DOCTOR",
        )
    )
    return result.scalar_one_or_none()


async def _get_delivery_events(
    db: AsyncSession, report_id: uuid.UUID
) -> list[MonthlyDeliveryEvent]:
    result = await db.execute(
        select(MonthlyDeliveryEvent)
        .where(MonthlyDeliveryEvent.report_id == report_id)
        .order_by(MonthlyDeliveryEvent.created_at, MonthlyDeliveryEvent.id)
    )
    return list(result.scalars().all())


def _effective_delivery_event(
    events: list[MonthlyDeliveryEvent],
) -> MonthlyDeliveryEvent | None:
    return events[-1] if events else None


async def _assert_delivery_actor(
    db: AsyncSession, hospital_id: uuid.UUID, actor: AdminUser
) -> None:
    if actor.role == ROLE_OWNER:
        return
    if actor.role != ROLE_OPERATOR:
        raise HTTPException(status_code=403, detail={"code": "DELIVERY_ROLE_FORBIDDEN"})
    result = await db.execute(
        select(HospitalHandoff).where(HospitalHandoff.hospital_id == hospital_id)
    )
    handoff = result.scalar_one_or_none()
    if handoff is None or handoff.ae_owner_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "DELIVERY_NOT_ASSIGNED"})


def _delivery_conflict(code: str, message: str, blockers: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": code, "message": message, "blockers": blockers or [message]},
    )


async def _assert_customer_ready(
    db: AsyncSession,
    report: MonthlyReport,
    manifest: MonthlyMeasurementManifest | None,
    artifact: MonthlyReportArtifact | None,
) -> None:
    gate = _delivery_gate(report, manifest, artifact)
    if not gate.ready:
        raise _delivery_conflict(gate.code or "report_blocked", gate.message or "전달할 수 없습니다.")
    if report.report_type == "MONTHLY":
        readiness = await get_essence_readiness(db, report.hospital_id)
        blockers = _current_essence_delivery_blockers(report, readiness)
        if blockers:
            raise _delivery_conflict("current_readiness_blocked", blockers[0], blockers)


def _new_delivery_event(
    *,
    report: MonthlyReport,
    artifact: MonthlyReportArtifact,
    event_type: ReportDeliveryEventType,
    actor: AdminUser,
    recipient: str | None,
    channel: str,
    note: str | None,
    reason: str | None,
    now: datetime,
) -> MonthlyDeliveryEvent:
    return MonthlyDeliveryEvent(
        report_id=report.id,
        artifact_id=artifact.id,
        event_type=event_type.value,
        actor_id=actor.id,
        recipient=recipient,
        metadata_json={
            "artifact_sha256": artifact.sha256,
            "artifact_path_hash": sha256(artifact.path.encode("utf-8")).hexdigest(),
            "channel": channel,
            "operator": actor.email,
            "note": note,
            "reason": reason,
        },
        created_at=now,
    )


async def _audit_delivery(
    db: AsyncSession,
    report: MonthlyReport,
    actor: AdminUser,
    event: MonthlyDeliveryEvent,
) -> None:
    await write_audit_log(
        db,
        action=f"report_delivery_{event.event_type.lower()}",
        hospital_id=report.hospital_id,
        actor=actor.email,
        target_type="monthly_report",
        target_id=report.id,
        detail={"event_type": event.event_type, "artifact_id": str(event.artifact_id)},
    )


def _serialize(
    r: MonthlyReport,
    full: bool = False,
    *,
    manifest: MonthlyMeasurementManifest | None = None,
    artifact: MonthlyReportArtifact | None = None,
    events: list[MonthlyDeliveryEvent] | None = None,
    current_blockers: list[str] | None = None,
    review_evidence: dict[str, object] | None = None,
) -> dict:
    gate = _delivery_gate(r, manifest, artifact)
    delivery_events = events or []
    effective = _effective_delivery_event(delivery_events)
    delivered = (
        effective.event_type != ReportDeliveryEventType.RESCINDED
        if effective is not None
        else bool(r.sent_at)
    )
    delivery_blockers = [] if gate.ready else [gate.message or "전달할 수 없습니다."]
    delivery_blockers.extend(current_blockers or [])
    ready = gate.ready and not current_blockers
    artifact_state = _artifact_state(r, artifact)
    artifact_metadata = (
        parse_doctor_artifact_metadata(artifact.validation_metadata)
        if artifact_state is ReportArtifactState.VALID and artifact is not None
        else None
    )

    def serialize_event(event: MonthlyDeliveryEvent) -> dict:
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "artifact_id": str(event.artifact_id) if event.artifact_id else None,
            "artifact_sha256": metadata.get("artifact_sha256"),
            "artifact_path_hash": metadata.get("artifact_path_hash"),
            "recipient_label": event.recipient,
            "channel": metadata.get("channel"),
            "operator": metadata.get("operator"),
            "note": metadata.get("note"),
            "reason": metadata.get("reason"),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    d = {
        "id": str(r.id),
        "hospital_id": str(r.hospital_id),
        "period_year": r.period_year,
        "period_month": r.period_month,
        "report_type": r.report_type,
        "display": _serialize_display(r, delivered=delivered),
        "has_pdf": r.pdf_path is not None,
        "has_doctor_pdf": artifact_state is ReportArtifactState.VALID,
        "doctor_artifact_state": artifact_state.value,
        "doctor_artifact_sha256": artifact.sha256
        if artifact_state is ReportArtifactState.VALID and artifact is not None
        else None,
        "download_url": f"/api/admin/hospitals/{r.hospital_id}/reports/{r.id}/download"
        if r.pdf_path
        else None,
        "sov_summary": r.sov_summary if full else None,
        "content_summary": r.content_summary if full else None,
        "essence_summary": r.essence_summary if full else None,
        "delivery_ready": ready,
        "customer_ready": ready,
        "delivery_blockers": delivery_blockers,
        "effective_delivery": serialize_event(effective) if effective is not None else None,
        "delivery_history": [serialize_event(event) for event in delivery_events] if full else [],
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
    }
    if full:
        d["doctor_artifact"] = {
            "state": artifact_state.value,
            "state_label": DOCTOR_ARTIFACT_STATE_LABELS[artifact_state],
            "sha256": artifact.sha256 if artifact_metadata is not None and artifact else None,
            "byte_size": artifact.byte_size if artifact_metadata is not None and artifact else None,
            "page_count": artifact_metadata.page_count if artifact_metadata else None,
            "validated_at": (
                artifact.validated_at.isoformat()
                if artifact_metadata is not None and artifact and artifact.validated_at
                else None
            ),
            "validation_version": (
                artifact_metadata.validation_version if artifact_metadata else None
            ),
        }
        d["review_evidence"] = review_evidence
    return d


async def _serialize_report(
    db: AsyncSession, report: MonthlyReport, *, full: bool = False
) -> dict:
    manifest = await _get_manifest(db, report.manifest_id)
    artifact = await _get_doctor_artifact(db, report.id)
    events = await _get_delivery_events(db, report.id)
    gate = _delivery_gate(report, manifest, artifact)
    current_blockers: list[str] = []
    if gate.ready and report.report_type == "MONTHLY":
        readiness = await get_essence_readiness(db, report.hospital_id)
        current_blockers = _current_essence_delivery_blockers(report, readiness)
    review_evidence = await build_report_review_evidence(db, report) if full else None
    return _serialize(
        report,
        full,
        manifest=manifest,
        artifact=artifact,
        events=events,
        current_blockers=current_blockers,
        review_evidence=review_evidence,
    )
