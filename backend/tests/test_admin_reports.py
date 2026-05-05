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
            "approved_philosophy_exists": True,
            "philosophy_version": 3,
            "approved_at": "2026-05-05T12:00:00+00:00",
            "source_count": 4,
            "processed_source_count": 4,
            "generated_content_count": 8,
            "aligned_content_count": 7,
            "needs_review_content_count": 1,
            "missing_philosophy_content_count": 0,
            "medical_risk_findings": [{"content_id": "demo", "risk": "과장 표현"}],
            "recommended_actions": ["재검토 필요 콘텐츠 1건을 수정하세요."],
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
    assert payload["display"] == {
        "report_type_label": "월간 리포트",
        "screening_status": "AWAITING_REVIEW",
        "screening_status_label": "검수 대기",
        "pdf_status": "READY",
        "pdf_status_label": "다운로드 가능",
    }
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
    assert payload["display"]["report_type_label"] == "월간 리포트"
    assert payload["display"]["screening_status_label"] == "검수 대기"
    assert payload["essence_summary"]["approved_philosophy_exists"] is True
    assert payload["essence_summary"]["philosophy_version"] == 3
    assert payload["essence_summary"]["source_count"] == 4
    assert payload["essence_summary"]["processed_source_count"] == 4
    assert payload["essence_summary"]["aligned_content_count"] == 7
    assert payload["essence_summary"]["needs_review_content_count"] == 1
    assert payload["essence_summary"]["missing_philosophy_content_count"] == 0
    assert payload["essence_summary"]["medical_risk_findings"] == [{"content_id": "demo", "risk": "과장 표현"}]
    assert payload["essence_summary"]["recommended_actions"] == ["재검토 필요 콘텐츠 1건을 수정하세요."]
