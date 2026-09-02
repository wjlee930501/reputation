import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin.operations_center_query_common import OperationsFilters, SlaFilter
from app.api.admin.operations_center_serializers import sla_state


class _EmptyResult:
    def all(self):
        return []


class _RecordingDb:
    def __init__(self):
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _EmptyResult()

    async def scalar(self, _statement):
        return 0


def _delivery_facts():
    hospital_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    report_id = uuid.uuid4()
    report = SimpleNamespace(
        id=report_id,
        hospital_id=hospital_id,
        period_year=2026,
        period_month=8,
        report_type="MONTHLY",
        manifest_id=manifest_id,
        quality="COMPLETE",
        planned_count=2,
        success_count=2,
        failed_count=0,
        pdf_path="gs://reports/internal.pdf",
        doctor_pdf_path="gs://reports/doctor.pdf",
        sov_summary={"sov_pct": 10.0},
        content_summary={"published_count": 1, "operations": {"delivery_blockers": []}},
        essence_summary={
            "approved_philosophy_exists": True,
            "source_stale": False,
            "source_count": 1,
            "processed_source_count": 1,
            "needs_review_content_count": 0,
            "missing_philosophy_content_count": 0,
            "medical_risk_findings": [],
        },
    )
    manifest = SimpleNamespace(
        id=manifest_id,
        hospital_id=hospital_id,
        period_year=2026,
        period_month=8,
        closed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    artifact = SimpleNamespace(
        report_id=report_id,
        audience="DOCTOR",
        path=report.doctor_pdf_path,
        sha256="a" * 64,
        byte_size=4096,
        validated=True,
        validation_metadata={
            "validation_version": "doctor-pdf-v1",
            "validation_source": "SYSTEM",
            "page_count": 1,
            "page_size": "A4",
            "glyph_count": 840,
            "font_family": "Pretendard",
            "font_embedded": True,
            "korean_to_unicode": True,
            "link_count": 1,
            "expected_link_present": True,
            "required_text_present": True,
            "sha256": "a" * 64,
            "byte_size": 4096,
        },
    )
    return report, manifest, artifact


def test_report_queue_state_uses_delivery_gate_instead_of_sent_at_only():
    report, manifest, artifact = _delivery_facts()

    assert report_queries._report_queue_state(report, manifest, artifact)[0] == (
        "DELIVERY_PENDING"
    )

    report.quality = "DEGRADED"
    assert report_queries._report_queue_state(report, manifest, artifact)[0] == (
        "COVERAGE_INCOMPLETE"
    )
    report.quality = "COMPLETE"

    assert report_queries._report_queue_state(report, manifest, None)[0] == (
        "DOCTOR_ARTIFACT_MISSING"
    )
    artifact.path = "gs://reports/wrong.pdf"
    assert report_queries._report_queue_state(report, manifest, artifact)[0] == (
        "DOCTOR_ARTIFACT_INVALID"
    )


def test_incomplete_report_queue_action_never_offers_report_regeneration():
    control = report_queries._report_action(
        uuid.uuid4(), state="COVERAGE_INCOMPLETE", year=2026, month=8
    )

    assert control.kind == "OPEN_REPORT"
    assert control.method == "GET"


def test_previous_period_exposes_the_fifteen_minute_monthly_close():
    now = datetime(2026, 7, 31, 15, 5, tzinfo=UTC)

    year, month, _starts_at, ends_at, closes_at = report_queries._previous_period(now)

    assert (year, month) == (2026, 7)
    assert closes_at == ends_at + timedelta(minutes=15)
    assert sla_state(closes_at, now) == "DUE"
    assert sla_state(closes_at, closes_at + timedelta(minutes=1)) == "OVERDUE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "accepted", "rejected"),
    [
        (datetime(2026, 7, 31, 15, 5, tzinfo=UTC), SlaFilter.DUE, SlaFilter.OVERDUE),
        (datetime(2026, 7, 31, 15, 16, tzinfo=UTC), SlaFilter.OVERDUE, SlaFilter.DUE),
    ],
)
async def test_report_sla_filter_matches_the_close_time_state(now, accepted, rejected):
    accepted_db = _RecordingDb()
    await report_queries.load_reports_queue(
        accepted_db,
        OperationsFilters(sla=accepted),
        page=1,
        page_size=10,
        overview=False,
        now=now,
    )
    assert accepted_db.execute_calls == 1

    rejected_db = _RecordingDb()
    result = await report_queries.load_reports_queue(
        rejected_db,
        OperationsFilters(sla=rejected),
        page=1,
        page_size=10,
        overview=False,
        now=now,
    )
    assert result == (0, [])
    assert rejected_db.execute_calls == 0
