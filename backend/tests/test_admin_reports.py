import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import reports as reports_api
from app.api.admin.reports import _serialize
from app.services.essence_readiness import EssenceReadiness


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
            "source_stale": False,
            "generated_content_count": 8,
            "aligned_content_count": 8,
            "needs_review_content_count": 0,
            "missing_philosophy_content_count": 0,
            "medical_risk_findings": [],
            "recommended_actions": [],
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
    assert (
        payload["download_url"]
        == f"/api/admin/hospitals/{report.hospital_id}/reports/{report.id}/download"
    )
    assert payload["sov_summary"] is None
    assert payload["content_summary"] is None
    assert payload["essence_summary"] is None
    assert payload["delivery_ready"] is True
    assert payload["delivery_blockers"] == []


class _FakeDB:
    def __init__(self, hospital, report):
        self.hospital = hospital
        self.report = report
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        name = getattr(model, "__name__", "")
        if name == "Hospital":
            return self.hospital if self.hospital.id == object_id else None
        if name == "MonthlyReport":
            return self.report if self.report and self.report.id == object_id else None
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def _hospital():
    return SimpleNamespace(id=uuid.uuid4())


async def test_mark_report_sent_sets_sent_at_and_audits(monkeypatch):
    """A4 — sent_at 기록 + audit log + 상세 응답 반환."""
    hospital = _hospital()
    report = _report(hospital_id=hospital.id, sent_at=None)
    db = _FakeDB(hospital, report)

    async def _fresh_essence(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(
            approved=philosophy,
            current=philosophy,
            processed_source_count=4,
            required_source_count=4,
            current_snapshot_hash="snapshot",
        )

    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh_essence)

    payload = await reports_api.mark_report_sent(hospital.id, report.id, db=db)

    assert report.sent_at is not None
    assert payload["sent_at"] == report.sent_at.isoformat()
    assert payload["display"]["screening_status"] == "DELIVERED"
    assert payload["sov_summary"] == {"sov_pct": 42.0}  # full serialization
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].action == "mark_report_sent"


async def test_mark_report_sent_rechecks_current_essence_after_pdf_generation(monkeypatch):
    hospital = _hospital()
    report = _report(hospital_id=hospital.id, sent_at=None)
    db = _FakeDB(hospital, report)

    async def _stale_essence(db, hospital_id):
        del db, hospital_id
        return EssenceReadiness(
            approved=SimpleNamespace(version=3),
            current=None,
            processed_source_count=4,
            required_source_count=5,
            current_snapshot_hash="new-snapshot",
        )

    monkeypatch.setattr(reports_api, "get_essence_readiness", _stale_essence)

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(hospital.id, report.id, db=db)

    assert exc.value.status_code == 409
    assert any("현재 병원 자료" in blocker for blocker in exc.value.detail["blockers"])
    assert any("처리되지 않은 온보딩 자료" in blocker for blocker in exc.value.detail["blockers"])
    assert report.sent_at is None


async def test_mark_report_sent_is_idempotent():
    hospital = _hospital()
    original_sent_at = datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc)
    report = _report(hospital_id=hospital.id, sent_at=original_sent_at)
    db = _FakeDB(hospital, report)

    payload = await reports_api.mark_report_sent(hospital.id, report.id, db=db)

    assert report.sent_at == original_sent_at  # 기존 기록 유지
    assert payload["sent_at"] == original_sent_at.isoformat()
    assert db.committed is False  # 변경 없음 → 커밋/감사 로그 없음
    assert db.added == []


async def test_mark_report_sent_404_for_foreign_report():
    hospital = _hospital()
    report = _report(hospital_id=uuid.uuid4(), sent_at=None)  # 다른 병원의 리포트
    db = _FakeDB(hospital, report)

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(hospital.id, report.id, db=db)

    assert exc.value.status_code == 404


async def test_download_report_rejects_local_path_outside_report_output_dir(tmp_path):
    hospital = _hospital()
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"%PDF-1.4\n% not a real report\n")
    report = _report(hospital_id=hospital.id, pdf_path=str(outside_pdf))
    db = _FakeDB(hospital, report)

    with pytest.raises(HTTPException) as exc:
        await reports_api.download_report(hospital.id, report.id, db=db)

    assert exc.value.status_code == 404


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
    assert payload["essence_summary"]["aligned_content_count"] == 8
    assert payload["essence_summary"]["needs_review_content_count"] == 0
    assert payload["essence_summary"]["missing_philosophy_content_count"] == 0
    assert payload["essence_summary"]["medical_risk_findings"] == []
    assert payload["essence_summary"]["recommended_actions"] == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"pdf_path": None}, "PDF 다운로드 파일"),
        ({"sov_summary": None}, "AI 언급률 요약"),
        ({"content_summary": None}, "월간 콘텐츠 발행 요약"),
        ({"essence_summary": {"approved_philosophy_exists": False}}, "승인된 콘텐츠 운영 기준"),
        (
            {"essence_summary": {"approved_philosophy_exists": True, "source_stale": True}},
            "현재 자료와 일치하지 않습니다",
        ),
    ],
)
async def test_mark_report_sent_blocks_incomplete_delivery(overrides, expected):
    hospital = _hospital()
    report = _report(hospital_id=hospital.id, sent_at=None, **overrides)
    db = _FakeDB(hospital, report)

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(hospital.id, report.id, db=db)

    assert exc.value.status_code == 409
    assert any(expected in blocker for blocker in exc.value.detail["blockers"])
    assert report.sent_at is None
    assert db.committed is False


async def test_download_report_uses_one_hour_signed_url(monkeypatch):
    hospital = _hospital()
    report = _report(hospital_id=hospital.id)
    db = _FakeDB(hospital, report)
    calls = []

    def fake_signed_url(path, expiration_hours=24, response_disposition=None):
        calls.append((path, expiration_hours, response_disposition))
        return "https://storage.example/report.pdf"

    monkeypatch.setattr(reports_api, "get_signed_url", fake_signed_url)
    response = await reports_api.download_report(hospital.id, report.id, db=db)

    assert calls == [
        (report.pdf_path, 1, 'attachment; filename="report-2026-05.pdf"'),
    ]
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "report-2026-05.pdf" in response.headers["content-disposition"]
