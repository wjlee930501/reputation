"""Closed UNAVAILABLE reports use the real endpoint, PostgreSQL, and PDF bytes."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import func, select
from weasyprint import HTML

from app.api.admin import reports
from app.core.config import settings
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyDeliveryEvent,
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
)
from app.models.report import MonthlyReport
from app.schemas.report import ReportDeliveryRequest


def _unavailable_summary(*, sov_pct=None):
    return {
        "sov_pct": sov_pct,
        "change_pct": None,
        "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
        "observation_adequacy": {
            "status": "UNAVAILABLE",
            "lineage": "SLOTTED",
            "planned_slots": 5,
            "received_answers": 2,
            "confirmed_slots": 0,
            "ambiguous_slots": 1,
            "answer_failed_slots": 2,
            "judgment_failed_slots": 1,
            "pending_slots": 4,
            "platforms": [
                {
                    "platform": "chatgpt",
                    "planned_slots": 5,
                    "received_answers": 2,
                    "confirmed_slots": 0,
                    "ambiguous_slots": 1,
                    "answer_failed_slots": 2,
                    "judgment_failed_slots": 1,
                    "pending_slots": 4,
                    "confirmed_mentioned_count": 0,
                    "confirmed_sample_count": 0,
                }
            ],
        },
    }


async def _fixture(pg_async_session, tmp_path, monkeypatch, *, closed=True):
    hospital = Hospital(name="측정 불가 검증 의원", slug=f"unavailable-{uuid.uuid4().hex}")
    actor = AdminUser(
        email=f"owner-{uuid.uuid4().hex}@example.test",
        name="검증 담당자",
        role=ROLE_OWNER,
        password_hash="unused",
    )
    pg_async_session.add_all([hospital, actor])
    await pg_async_session.flush()
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=9,
        configured_platforms=["chatgpt"],
        platform_provenance={"measurement_protocol": {"policy_version": "task13"}},
        closes_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        closed_at=datetime(2026, 10, 8, tzinfo=timezone.utc) if closed else None,
    )
    pg_async_session.add(manifest)
    await pg_async_session.flush()

    pdf_path = tmp_path / "unavailable-doctor.pdf"
    HTML(
        string="""<html lang='ko'><style>@page { size: A4 }</style>
        <body><h1>측정 불가 검증 의원</h1><p>UNAVAILABLE · 언급률 미산출</p>
        <p>실패와 대기 결과를 미언급으로 세지 않았습니다.</p></body></html>"""
    ).write_pdf(pdf_path)
    pdf_bytes = pdf_path.read_bytes()
    assert len(PdfReader(pdf_path).pages) == 1
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    monkeypatch.setattr(settings, "REPORT_OUTPUT_DIR", str(tmp_path))

    report = MonthlyReport(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=9,
        report_type="MONTHLY",
        manifest_id=manifest.id,
        quality="DEGRADED",
        planned_count=0,
        success_count=0,
        failed_count=0,
        excluded_count=0,
        pdf_path=str(pdf_path),
        doctor_pdf_path=str(pdf_path),
        sov_summary=_unavailable_summary(),
        content_summary={"published_count": 0, "operations": {"delivery_blockers": []}},
        essence_summary={},
    )
    pg_async_session.add(report)
    await pg_async_session.flush()
    artifact = MonthlyReportArtifact(
        report_id=report.id,
        audience="DOCTOR",
        path=str(pdf_path),
        sha256=digest,
        byte_size=len(pdf_bytes),
        validated=True,
        validated_at=datetime.now(timezone.utc),
        validation_metadata={
            "validation_version": "doctor-pdf-v1",
            "validation_source": "SYSTEM",
            "page_count": 1,
            "page_size": "A4",
            "glyph_count": 20,
            "font_family": "Pretendard",
            "font_embedded": True,
            "korean_to_unicode": True,
            "link_count": 1,
            "expected_link_present": True,
            "required_text_present": True,
            "sha256": digest,
            "byte_size": len(pdf_bytes),
        },
    )
    pg_async_session.add(artifact)
    await pg_async_session.flush()
    return hospital, actor, manifest, report, artifact, pdf_path


async def _event_count(session, report_id):
    return await session.scalar(
        select(func.count()).select_from(MonthlyDeliveryEvent).where(
            MonthlyDeliveryEvent.report_id == report_id
        )
    )


@pytest.mark.asyncio
async def test_closed_unavailable_endpoint_appends_exactly_one_event(
    pg_async_session, tmp_path, monkeypatch
):
    hospital, actor, _manifest, report, artifact, _path = await _fixture(
        pg_async_session, tmp_path, monkeypatch
    )

    payload = await reports.mark_report_sent(
        hospital.id,
        report.id,
        ReportDeliveryRequest(
            artifact_sha256=artifact.sha256,
            recipient_label="가상 원장",
            channel="대면",
        ),
        db=pg_async_session,
        actor=actor,
    )

    assert payload["delivery_ready"] is True
    assert payload["sov_summary"]["sov_pct"] is None
    assert await _event_count(pg_async_session, report.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("open_period", "manifest_open"),
        ("artifact_mismatch", "artifact_mismatch"),
        ("fabricated_zero", "report_blocked"),
        ("wrong_bytes", "artifact_storage_mismatch"),
        ("malformed_counts", "coverage_incomplete"),
    ],
)
async def test_open_period_mismatch_zero_and_wrong_bytes_append_no_event(
    pg_async_session, tmp_path, monkeypatch, state, expected_code
):
    hospital, actor, _manifest, report, artifact, path = await _fixture(
        pg_async_session, tmp_path, monkeypatch, closed=state != "open_period"
    )
    supplied_digest = artifact.sha256
    if state == "artifact_mismatch":
        supplied_digest = "0" * 64
    elif state == "fabricated_zero":
        report.sov_summary = _unavailable_summary(sov_pct=0.0)
    elif state == "wrong_bytes":
        path.write_bytes(path.read_bytes() + b"tampered")
    elif state == "malformed_counts":
        malformed = _unavailable_summary()
        malformed["observation_adequacy"]["pending_slots"] = 2
        report.sov_summary = malformed
    await pg_async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await reports.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256=supplied_digest,
                recipient_label="가상 원장",
                channel="대면",
            ),
            db=pg_async_session,
            actor=actor,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == expected_code
    assert await _event_count(pg_async_session, report.id) == 0


@pytest.mark.asyncio
async def test_cross_tenant_report_identity_appends_no_event(
    pg_async_session, tmp_path, monkeypatch
):
    hospital, actor, _manifest, report, artifact, _path = await _fixture(
        pg_async_session, tmp_path, monkeypatch
    )
    other = Hospital(name="다른 병원", slug=f"other-{uuid.uuid4().hex}")
    pg_async_session.add(other)
    await pg_async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await reports.mark_report_sent(
            other.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256=artifact.sha256,
                recipient_label="가상 원장",
                channel="대면",
            ),
            db=pg_async_session,
            actor=actor,
        )

    assert exc.value.status_code == 404
    assert await _event_count(pg_async_session, report.id) == 0
