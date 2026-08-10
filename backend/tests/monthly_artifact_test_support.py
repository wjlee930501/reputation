"""Shared typed fixtures for Task24 monthly doctor-artifact PostgreSQL tests."""

import uuid
from types import SimpleNamespace

from app.models.monthly_control import MonthlyMeasurementManifest
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import (
    DoctorArtifactMetadata,
    PublishedDoctorPdf,
)


def monthly_sov() -> SimpleNamespace:
    payload = {
        "sov_pct": 47.0,
        "prev_sov_pct": None,
        "change_pct": None,
        "planned_count": 20,
        "success_count": 20,
        "failed_count": 0,
        "excluded_count": 0,
        "query_intent_snapshot": "FROZEN",
        "cells": [],
        "platforms": [],
        "queries": [],
        "segments": {},
        "comparison": {
            "status": "NON_COMPARABLE",
            "reason": "NO_PRIOR_MANIFEST",
            "current_sov_pct": None,
            "prior_sov_pct": None,
            "change_pct": None,
            "matched_cell_count": 0,
            "current_unmatched_cell_count": 20,
            "prior_unmatched_cell_count": 0,
            "problem": "지난달에 같은 기준으로 확인한 결과가 없습니다.",
            "customer_impact": "전월 대비 증감 숫자는 표시하지 않습니다.",
            "next_action": "이번 달 현재 수치만 전달해 주세요.",
        },
    }
    return SimpleNamespace(
        sov_pct=47.0,
        comparison=SimpleNamespace(prior_sov_pct=None, change_pct=None),
        to_payload=lambda: payload,
    )


def apply_complete(report: MonthlyReport, manifest: MonthlyMeasurementManifest) -> None:
    report.manifest_id = manifest.id
    report.quality = "COMPLETE"
    report.planned_count = 20
    report.success_count = 20
    report.failed_count = 0
    report.excluded_count = 0
    report.customer_ready = False
    report.delivery_blockers = ["DOCTOR_ARTIFACT_UNVALIDATED"]


def published(report_id: uuid.UUID) -> PublishedDoctorPdf:
    digest = report_id.hex * 2
    byte_size = 4096
    metadata = DoctorArtifactMetadata(
        validation_version="doctor-pdf-v1",
        validation_source="SYSTEM",
        page_count=1,
        page_size="A4",
        glyph_count=840,
        font_family="Pretendard",
        font_embedded=True,
        korean_to_unicode=True,
        link_count=1,
        expected_link_present=True,
        required_text_present=True,
        sha256=digest,
        byte_size=byte_size,
    )
    return PublishedDoctorPdf(
        path=f"gs://qa-private/monthly/{report_id}.pdf",
        sha256=digest,
        byte_size=byte_size,
        metadata=metadata,
    )
