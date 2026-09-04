import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin.operations_center_query_common import OperationsFilters, SlaFilter


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


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _RowsDb:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _RowsResult(self.rows)


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


def test_report_queue_preserves_manifest_gate_distinctions():
    report, manifest, artifact = _delivery_facts()

    manifest.hospital_id = uuid.uuid4()
    assert report_queries._report_queue_state(report, manifest, artifact)[0] == (
        "MANIFEST_MISMATCH"
    )

    manifest.hospital_id = report.hospital_id
    manifest.closed_at = None
    assert report_queries._report_queue_state(report, manifest, artifact)[0] == (
        "MANIFEST_OPEN"
    )


def test_incomplete_report_queue_action_offers_real_prior_month_remasure():
    control = report_queries._report_action(
        uuid.uuid4(),
        state="COVERAGE_INCOMPLETE",
        year=2026,
        month=8,
        monthly_recovery_open=True,
    )

    assert control.kind == "RUN_SOV"
    assert control.method == "POST"
    assert control.path.endswith("/operations/run-sov?measurement_mode=monthly")
    assert control.requires_idempotency_key is True


def test_incomplete_report_action_is_not_a_dead_remasure_outside_recovery_window():
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


@pytest.mark.asyncio
async def test_report_rows_have_no_staff_sla_deadline():
    hospital = SimpleNamespace(id=uuid.uuid4(), name="월간 보고 병원")
    db = _RowsDb([(hospital, None, None, None, None, None)])

    total, rows = await report_queries.load_reports_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=10,
        overview=False,
        now=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert total == 1
    assert rows[0].queue.value == "REPORTS"
    assert rows[0].sla_due_at is None
    assert rows[0].sla_state == "NONE"
    assert rows[0].days_since_close == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sla", [None, SlaFilter.NONE])
async def test_report_queue_accepts_unfiltered_and_no_deadline_sla_filters(sla):
    db = _RecordingDb()

    await report_queries.load_reports_queue(
        db,
        OperationsFilters(sla=sla),
        page=1,
        page_size=10,
        overview=False,
        now=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert db.execute_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sla", [SlaFilter.DUE, SlaFilter.OVERDUE])
async def test_report_queue_is_empty_for_deadline_sla_filters(sla):
    db = _RecordingDb()

    result = await report_queries.load_reports_queue(
        db,
        OperationsFilters(sla=sla),
        page=1,
        page_size=10,
        overview=False,
        now=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert result == (0, [])
    assert db.execute_calls == 0
