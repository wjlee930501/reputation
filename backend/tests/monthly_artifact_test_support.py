"""Shared typed fixtures for Task24 monthly doctor-artifact PostgreSQL tests."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.monthly_control import MonthlyMeasurementManifest
from app.models.report import MonthlyReport
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.report_artifact_validation import (
    DoctorArtifactMetadata,
    PublishedDoctorPdf,
)


def complete_cells() -> tuple[ManifestCellInput, ...]:
    measured_at = datetime(2026, 7, 31, tzinfo=timezone.utc)
    return tuple(
        ManifestCellInput(
            query_key=f"{platform}:local:recovery",
            query_text="지역 병원 추천",
            platform=platform,
            query_intent="LOCAL",
            state="SUCCESS",
            query_matrix_id=None,
            query_target_id=None,
            query_variant_id=None,
            query_intent_source="FROZEN",
            attempts=tuple(
                CellAttempt(
                    record_id=uuid.uuid4(),
                    measured_at=measured_at,
                    succeeded=True,
                    is_mentioned=index < 5,
                )
                for index in range(10)
            ),
            planned_repeat_count=10,
            received_answer_count=10,
            confirmed_slot_count=10,
        )
        for platform in ("chatgpt", "gemini")
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
        "platforms": [
            {
                "platform": platform,
                "mention_rate": 50.0,
                "mentioned_attempts": 5,
                "attempts_used": 10,
            }
            for platform in ("chatgpt", "gemini")
        ],
        "queries": [],
        "segments": {},
        "observation_adequacy": {
            "status": "COMPLETE",
            "planned_slots": 20,
            "received_answers": 20,
            "confirmed_slots": 20,
            "ambiguous_slots": 0,
            "answer_failed_slots": 0,
            "judgment_failed_slots": 0,
            "pending_slots": 0,
            "pending_semantics": "INCLUDES_FAILURES",
            "platforms": [
                {
                    "platform": "chatgpt",
                    "planned_slots": 10,
                    "received_answers": 10,
                    "confirmed_slots": 10,
                    "ambiguous_slots": 0,
                    "answer_failed_slots": 0,
                    "judgment_failed_slots": 0,
                    "pending_slots": 0,
                },
                {
                    "platform": "gemini",
                    "planned_slots": 10,
                    "received_answers": 10,
                    "confirmed_slots": 10,
                    "ambiguous_slots": 0,
                    "answer_failed_slots": 0,
                    "judgment_failed_slots": 0,
                    "pending_slots": 0,
                },
            ],
        },
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
        sov_pct_all_cells=47.0,
        comparison=SimpleNamespace(
            prior_sov_pct=None,
            change_pct=None,
            reason="NO_PRIOR_MANIFEST",
            status="NON_COMPARABLE",
        ),
        comparison_cell_keys=frozenset(),
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


def published(report_id: uuid.UUID, *, page_count: int = 1) -> PublishedDoctorPdf:
    digest = report_id.hex * 2
    byte_size = 4096
    metadata = DoctorArtifactMetadata(
        validation_version="doctor-pdf-v1",
        validation_source="SYSTEM",
        page_count=page_count,
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
