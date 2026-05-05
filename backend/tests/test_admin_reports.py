import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.admin.reports import _serialize


def _report(**overrides):
    base = dict(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        period_year=2026,
        period_month=5,
        report_type="MONTHLY",
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={"sov_pct": 42.0},
        content_summary={"published_count": 8},
        essence_summary={
            "approved_philosophy": {"exists": True, "version": 3},
            "source_assets": {"reviewed_count": 4},
            "content_alignment": {"needs_review_count": 1},
            "medical_ad_risks": {"risk_count": 2},
        },
        created_at=datetime(2026, 5, 5, 12, 30, tzinfo=timezone.utc),
        sent_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_report_list_hides_internal_summaries_but_keeps_pdf_contract():
    report = _report()

    payload = _serialize(report)

    assert payload["id"] == str(report.id)
    assert payload["hospital_id"] == str(report.hospital_id)
    assert payload["has_pdf"] is True
    assert payload["download_url"] == f"/api/admin/hospitals/{report.hospital_id}/reports/{report.id}/download"
    assert payload["sov_summary"] is None
    assert payload["content_summary"] is None
    assert payload["essence_summary"] is None


def test_report_detail_serializes_essence_summary_for_pre_pdf_review():
    report = _report()

    payload = _serialize(report, full=True)

    assert payload["sov_summary"] == {"sov_pct": 42.0}
    assert payload["content_summary"] == {"published_count": 8}
    assert payload["essence_summary"] == report.essence_summary
    assert payload["essence_summary"]["approved_philosophy"]["exists"] is True
    assert payload["essence_summary"]["source_assets"]["reviewed_count"] == 4
    assert payload["essence_summary"]["content_alignment"]["needs_review_count"] == 1
    assert payload["essence_summary"]["medical_ad_risks"]["risk_count"] == 2
