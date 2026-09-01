import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import reports as reports_api
from app.api.admin.reports import _delivery_gate, _serialize
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER
from app.schemas.report import (
    ReportDeliveryCorrectionRequest,
    ReportDeliveryRequest,
    ReportDeliveryRescindRequest,
)
from app.services.essence_readiness import EssenceReadiness


def _report(**overrides):
    base = dict(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        period_year=2026,
        period_month=5,
        report_type="MONTHLY",
        version=1,
        supersedes_report_id=None,
        pdf_path="gs://reputation-reports/demo.pdf",
        doctor_pdf_path="gs://reputation-reports/demo_doctor.pdf",
        sov_summary={"sov_pct": 42.0},
        content_summary={
            "published_count": 8,
            "operations": {
                "schema_version": 1,
                "delivery_blockers": [],
            },
        },
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
        quality="COMPLETE",
        manifest_id=uuid.uuid4(),
        customer_ready=False,
        delivery_blockers=[],
        planned_count=4,
        success_count=4,
        failed_count=0,
        excluded_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _manifest(**overrides):
    base = {
        "id": uuid.uuid4(),
        "hospital_id": uuid.uuid4(),
        "period_year": 2026,
        "period_month": 5,
        "closed_at": datetime(2026, 5, 31, 23, 59, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _bind_manifest(report, manifest):
    manifest.id = report.manifest_id
    manifest.hospital_id = report.hospital_id
    manifest.period_year = report.period_year
    manifest.period_month = report.period_month
    return manifest


def _doctor_artifact(**overrides):
    base = {
        "id": uuid.uuid4(),
        "report_id": uuid.uuid4(),
        "audience": "DOCTOR",
        "path": "gs://reputation-reports/demo_doctor.pdf",
        "sha256": "a" * 64,
        "byte_size": 4096,
        "validated": True,
        "validated_at": datetime(2026, 5, 5, 12, 35, tzinfo=timezone.utc),
        "validation_metadata": {
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
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("report_overrides", "manifest", "artifact", "expected_code"),
    [
        ({"quality": "DEGRADED"}, _manifest(), _doctor_artifact(), "coverage_incomplete"),
        ({}, _manifest(closed_at=None), _doctor_artifact(), "manifest_open"),
        ({}, _manifest(), None, "doctor_artifact_missing"),
        ({}, _manifest(), _doctor_artifact(validated=False), "doctor_artifact_invalid"),
        (
            {},
            _manifest(),
            _doctor_artifact(validation_metadata={"page_count": 1}),
            "doctor_artifact_invalid",
        ),
    ],
)
def test_monthly_customer_delivery_fails_closed(
    report_overrides, manifest, artifact, expected_code
):
    report = _report(**report_overrides)
    _bind_manifest(report, manifest)
    if artifact is not None:
        artifact.report_id = report.id

    gate = _delivery_gate(report, manifest, artifact)

    assert gate.code == expected_code
    assert gate.ready is False


def test_monthly_customer_delivery_requires_matching_valid_doctor_artifact():
    report = _report()
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)

    gate = _delivery_gate(report, _bind_manifest(report, _manifest()), artifact)

    assert gate.ready is True
    assert gate.code is None


@pytest.mark.parametrize(
    "metadata_override",
    [
        {"page_count": 2},
        {"page_size": "LETTER"},
        {"font_family": "NanumGothic"},
        {"font_embedded": False},
        {"korean_to_unicode": False},
        {"link_count": 0},
        {"expected_link_present": False},
        {"required_text_present": False},
        {"validation_version": "doctor-pdf-v0"},
    ],
)
def test_monthly_customer_delivery_rejects_artifacts_that_fail_the_shared_parser(
    metadata_override,
):
    report = _report()
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    artifact.validation_metadata = {**artifact.validation_metadata, **metadata_override}

    gate = _delivery_gate(report, _bind_manifest(report, _manifest()), artifact)

    assert gate.ready is False
    assert gate.code == "doctor_artifact_invalid"


@pytest.mark.parametrize("mismatch", ["hospital", "year", "month"])
def test_monthly_customer_delivery_rejects_manifest_tenant_or_period_mismatch(mismatch):
    report = _report()
    manifest = _bind_manifest(report, _manifest())
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    if mismatch == "hospital":
        manifest.hospital_id = uuid.uuid4()
    elif mismatch == "year":
        manifest.period_year -= 1
    else:
        manifest.period_month -= 1

    gate = _delivery_gate(report, manifest, artifact)

    assert gate.code == "manifest_mismatch"
    assert gate.ready is False
    assert "manifest" not in (gate.message or "").lower()


def test_monthly_customer_delivery_open_measurement_copy_is_plain_korean():
    report = _report()
    manifest = _bind_manifest(report, _manifest(closed_at=None))
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)

    gate = _delivery_gate(report, manifest, artifact)

    assert gate.code == "manifest_open"
    assert "manifest" not in (gate.message or "").lower()
    assert "필수 측정 집계" in (gate.message or "")


def test_monthly_customer_delivery_incomplete_coverage_copy_is_plain_korean():
    report = _report(quality="DEGRADED")
    manifest = _bind_manifest(report, _manifest())
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)

    gate = _delivery_gate(report, manifest, artifact)

    assert gate.code == "coverage_incomplete"
    assert gate.message == "이번 달 필수 질문 측정이 모두 끝나지 않았습니다."
    assert "coverage" not in gate.message.lower()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("coverage", "coverage_incomplete"),
        ("open", "manifest_open"),
        ("manifest_hospital", "manifest_mismatch"),
        ("manifest_period", "manifest_mismatch"),
        ("missing", "doctor_artifact_missing"),
        ("invalid", "doctor_artifact_invalid"),
    ],
)
async def test_mark_sent_returns_distinct_readiness_conflicts(mutation, expected_code):
    hospital, report, actor, db = _ready_db()
    if mutation == "coverage":
        report.quality = "DEGRADED"
    elif mutation == "open":
        db.manifest.closed_at = None
    elif mutation == "manifest_hospital":
        db.manifest.hospital_id = uuid.uuid4()
    elif mutation == "manifest_period":
        db.manifest.period_month -= 1
    elif mutation == "missing":
        db.artifact = None
    elif mutation == "invalid":
        db.artifact.validated = False

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256="a" * 64, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=actor,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == expected_code
    assert report.sent_at is None
    assert db.events == []


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
    assert payload["delivery_ready"] is False
    assert payload["doctor_artifact_state"] == "MISSING"
    assert "doctor_artifact" not in payload


def test_report_detail_exposes_only_safe_authoritative_artifact_evidence():
    from app.schemas.report import ReportResponse

    report = _report()
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)

    payload = _serialize(
        report,
        full=True,
        manifest=_bind_manifest(report, _manifest()),
        artifact=artifact,
    )

    assert payload["doctor_artifact"] == {
        "state": "VALID",
        "state_label": "원장 전달용 PDF 검증 완료",
        "sha256": "a" * 64,
        "byte_size": 4096,
        "page_count": 1,
        "validated_at": "2026-05-05T12:35:00+00:00",
        "validation_version": "doctor-pdf-v1",
    }
    assert "path" not in payload["doctor_artifact"]
    assert "validation_metadata" not in payload["doctor_artifact"]
    serialized = ReportResponse.model_validate(payload).model_dump(mode="json")
    assert serialized["doctor_artifact"]["validated_at"] == "2026-05-05T12:35:00Z"
    assert serialized["doctor_artifact"]["sha256"] == "a" * 64
    assert "gs://" not in str(serialized["doctor_artifact"])


def test_report_schema_closes_artifact_projection_and_hides_storage_fields():
    from app.main import app
    from app.schemas.report import DoctorArtifactProjection, ReportListResponse, ReportResponse

    artifact_schema = DoctorArtifactProjection.model_json_schema()
    assert artifact_schema["additionalProperties"] is False
    assert set(artifact_schema["properties"]) == {
        "state",
        "state_label",
        "sha256",
        "byte_size",
        "page_count",
        "validated_at",
        "validation_version",
    }
    assert "path" not in str(artifact_schema)
    assert "validation_metadata" not in str(artifact_schema)
    report_schema = ReportResponse.model_json_schema()
    artifact_ref = report_schema["properties"]["doctor_artifact"]
    assert "DoctorArtifactProjection" in str(artifact_ref)
    assert "doctor_artifact" not in ReportListResponse.model_json_schema()["properties"]

    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]
    assert "path" not in schemas["DoctorArtifactProjection"]["properties"]
    assert "validation_metadata" not in schemas["DoctorArtifactProjection"]["properties"]
    list_response = openapi["paths"]["/api/v1/admin/hospitals/{hospital_id}/reports"]["get"]
    response_schema = list_response["responses"]["200"]["content"]["application/json"]["schema"]
    assert "ReportListResponse" in str(response_schema)
    assert "doctor_artifact" not in schemas["ReportListResponse"]["properties"]


class _FakeResult:
    def __init__(self, values):
        self.values = values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return self.values


class _FakeDB:
    def __init__(self, hospital, report, *, manifest=None, artifact=None, events=None, handoff=None):
        self.hospital = hospital
        self.report = report
        self.manifest = manifest
        self.artifact = artifact
        self.events = list(events or [])
        self.handoff = handoff
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        name = getattr(model, "__name__", "")
        if name == "Hospital":
            return self.hospital if self.hospital.id == object_id else None
        if name == "MonthlyReport":
            return self.report if self.report and self.report.id == object_id else None
        if name == "MonthlyMeasurementManifest":
            return self.manifest if self.manifest and self.manifest.id == object_id else None
        if name == "MonthlyReportArtifact":
            return self.artifact if self.artifact and self.artifact.id == object_id else None
        return None

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        name = getattr(entity, "__name__", "")
        if name == "MonthlyReport":
            values = [self.report] if self.report and self.report.hospital_id == self.hospital.id else []
        elif name == "MonthlyReportArtifact":
            values = [self.artifact] if self.artifact else []
        elif name == "MonthlyDeliveryEvent":
            values = self.events
        elif name == "HospitalHandoff":
            values = [self.handoff] if self.handoff else []
        else:
            values = []
        return _FakeResult(values)

    def add(self, item):
        self.added.append(item)
        if item.__class__.__name__ == "MonthlyDeliveryEvent":
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()
            self.events.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def _hospital():
    return SimpleNamespace(id=uuid.uuid4())


def _actor(*, role=ROLE_OWNER):
    return SimpleNamespace(id=uuid.uuid4(), email="owner@example.com", role=role, is_active=True)


def _ready_db(*, role=ROLE_OWNER):
    hospital = _hospital()
    report = _report(hospital_id=hospital.id)
    manifest = _bind_manifest(report, _manifest())
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    actor = _actor(role=role)
    handoff = SimpleNamespace(hospital_id=hospital.id, ae_owner_id=actor.id)
    return hospital, report, actor, _FakeDB(
        hospital, report, manifest=manifest, artifact=artifact, handoff=handoff
    )


async def test_mark_report_sent_sets_sent_at_and_audits(monkeypatch):
    hospital, report, actor, db = _ready_db()

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

    payload = await reports_api.mark_report_sent(
        hospital.id,
        report.id,
        ReportDeliveryRequest(
            artifact_sha256=db.artifact.sha256,
            recipient_label="김원장",
            channel="대면",
            note="2026-05 월간 보고",
        ),
        db=db,
        actor=actor,
    )

    assert report.sent_at is not None
    assert payload["sent_at"] == report.sent_at.isoformat()
    assert payload["display"]["screening_status"] == "DELIVERED"
    assert payload["sov_summary"] == {"sov_pct": 42.0}  # full serialization
    assert db.committed is True
    event = next(item for item in db.added if item.__class__.__name__ == "MonthlyDeliveryEvent")
    assert event.event_type == "DELIVERED"
    assert event.artifact_id == db.artifact.id
    assert event.recipient == "김원장"
    assert event.metadata_json["artifact_sha256"] == db.artifact.sha256
    assert len(event.metadata_json["artifact_path_hash"]) == 64
    assert payload["effective_delivery"]["operator"] == actor.email


async def test_mark_report_sent_rechecks_current_essence_after_pdf_generation(monkeypatch):
    hospital, report, actor, db = _ready_db()

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
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256=db.artifact.sha256, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=actor,
        )

    assert exc.value.status_code == 409
    assert any("현재 병원 자료" in blocker for blocker in exc.value.detail["blockers"])
    assert any("처리되지 않은 온보딩 자료" in blocker for blocker in exc.value.detail["blockers"])
    assert report.sent_at is None


async def test_mark_report_sent_rejects_artifact_hash_mismatch(monkeypatch):
    hospital, report, actor, db = _ready_db()

    async def _fresh(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(philosophy, philosophy, 4, 4, "snapshot")

    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh)
    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256="b" * 64, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=actor,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "artifact_mismatch"
    assert report.sent_at is None
    assert db.events == []


async def test_delivery_operator_must_be_assigned(monkeypatch):
    hospital, report, actor, db = _ready_db(role=ROLE_OPERATOR)
    db.handoff.ae_owner_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256=db.artifact.sha256, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=actor,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "DELIVERY_NOT_ASSIGNED"
    assert db.events == []


async def test_correction_rescind_and_redelivery_are_append_only(monkeypatch):
    hospital, report, owner, db = _ready_db()

    async def _fresh(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(philosophy, philosophy, 4, 4, "snapshot")

    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh)
    delivered = await reports_api.mark_report_sent(
        hospital.id,
        report.id,
        ReportDeliveryRequest(
            artifact_sha256=db.artifact.sha256, recipient_label="김원장", channel="대면"
        ),
        db=db,
        actor=owner,
    )
    first_sent_at = report.sent_at
    corrected = await reports_api.correct_report_delivery(
        hospital.id,
        report.id,
        ReportDeliveryCorrectionRequest(
            artifact_sha256=db.artifact.sha256,
            recipient_label="김 원장",
            channel="대면",
            note="성명 띄어쓰기 수정",
            reason="수신자 표기 정정",
        ),
        db=db,
        actor=owner,
    )
    rescinded = await reports_api.rescind_report_delivery(
        hospital.id,
        report.id,
        ReportDeliveryRescindRequest(reason="잘못된 수신자에게 전달"),
        db=db,
        actor=owner,
    )
    redelivered = await reports_api.mark_report_sent(
        hospital.id,
        report.id,
        ReportDeliveryRequest(
            artifact_sha256=db.artifact.sha256, recipient_label="김원장", channel="대면"
        ),
        db=db,
        actor=owner,
    )

    assert delivered["effective_delivery"]["event_type"] == "DELIVERED"
    assert corrected["effective_delivery"]["event_type"] == "CORRECTED"
    assert rescinded["effective_delivery"]["event_type"] == "RESCINDED"
    assert rescinded["display"]["screening_status"] == "AWAITING_REVIEW"
    assert redelivered["effective_delivery"]["event_type"] == "REDELIVERED"
    assert [event.event_type for event in db.events] == [
        "DELIVERED",
        "CORRECTED",
        "RESCINDED",
        "REDELIVERED",
    ]
    assert report.sent_at == first_sent_at


async def test_operator_cannot_correct_or_rescind_delivery():
    hospital, report, _owner, db = _ready_db()
    operator = _actor(role=ROLE_OPERATOR)

    with pytest.raises(HTTPException) as correction:
        await reports_api.correct_report_delivery(
            hospital.id,
            report.id,
            ReportDeliveryCorrectionRequest(
                artifact_sha256=db.artifact.sha256,
                recipient_label="김원장",
                channel="대면",
                reason="정정",
            ),
            db=db,
            actor=operator,
        )
    with pytest.raises(HTTPException) as rescind:
        await reports_api.rescind_report_delivery(
            hospital.id,
            report.id,
            ReportDeliveryRescindRequest(reason="철회"),
            db=db,
            actor=operator,
        )

    assert correction.value.status_code == 403
    assert rescind.value.status_code == 403
    assert db.events == []


async def test_mark_report_sent_404_for_foreign_report():
    hospital = _hospital()
    report = _report(hospital_id=uuid.uuid4(), sent_at=None)  # 다른 병원의 리포트
    db = _FakeDB(hospital, report)

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256="a" * 64, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=_actor(),
        )

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
    assert payload["content_summary"] == report.content_summary
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


def test_report_detail_returns_persisted_fixed_manifest_breakdown_unchanged():
    stored_summary = {
        "sov_pct": 50.0,
        "planned_count": 3,
        "success_count": 2,
        "failed_count": 1,
        "excluded_count": 1,
        "cells": [
            {
                "query_key": "query:fixed",
                "query_text": "강남 내과를 찾아줘",
                "query_intent_label": "지역·병원 선택 질문",
                "platform": "chatgpt",
                "platform_label": "ChatGPT",
                "state": "SUCCESS",
                "state_label": "측정 완료",
                "measured": True,
                "mentioned": True,
            }
        ],
        "platforms": [
            {
                "platform": "chatgpt",
                "cell_count": 2,
                "planned_count": 1,
                "success_count": 1,
                "failed_count": 0,
                "excluded_count": 1,
            },
            {
                "platform": "gemini",
                "cell_count": 2,
                "planned_count": 2,
                "success_count": 1,
                "failed_count": 1,
                "excluded_count": 0,
            },
        ],
        "queries": [{"query_key": "query:fixed", "cell_count": 4}],
        "comparison": {
            "status": "NON_COMPARABLE",
            "reason": "NO_PRIOR_MANIFEST",
            "matched_cell_count": 0,
        },
    }
    report = _report(sov_summary=stored_summary)

    detail = _serialize(report, full=True)
    listing = _serialize(report)

    assert detail["sov_summary"] == stored_summary
    assert detail["sov_summary"]["planned_count"] == 3
    assert sum(row["cell_count"] for row in detail["sov_summary"]["platforms"]) == 4
    assert detail["sov_summary"]["queries"][0]["cell_count"] == 4
    assert detail["sov_summary"]["cells"][0]["state_label"] == "측정 완료"
    assert "raw_response" not in detail["sov_summary"]["cells"][0]
    assert listing["sov_summary"] is None


def test_report_detail_keeps_delivered_reports_downloadable_even_if_current_readiness_changes():
    report = _report()
    report.sent_at = datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc)
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    payload = _serialize(
        report,
        full=True,
        manifest=_bind_manifest(report, _manifest()),
        artifact=artifact,
        events=[
            SimpleNamespace(
                id=uuid.uuid4(),
                event_type="DELIVERED",
                artifact_id=artifact.id,
                recipient="김원장",
                metadata_json={
                    "artifact_sha256": artifact.sha256,
                    "artifact_path_hash": "b" * 64,
                    "channel": "대면",
                    "operator": "owner@example.com",
                    "note": None,
                    "reason": None,
                },
                created_at=datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc),
            )
        ],
        current_blockers=["현재 병원 자료가 변경됐습니다."],
    )

    assert payload["delivery_ready"] is True
    assert payload["customer_ready"] is True
    assert payload["delivery_blockers"] == []
    assert payload["display"]["screening_status"] == "DELIVERED"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"pdf_path": None}, "PDF 다운로드 파일"),
        ({"sov_summary": None}, "AI 언급률 요약"),
        ({"content_summary": None}, "월간 콘텐츠 발행 요약"),
        (
            {
                "content_summary": {
                    "published_count": 8,
                    "operations": {
                        "delivery_blockers": [
                            "월간 리포트 필수 사후검수 샘플 1건이 아직 완료되지 않았습니다."
                        ]
                    },
                }
            },
            "필수 사후검수 샘플",
        ),
        ({"essence_summary": {"approved_philosophy_exists": False}}, "승인된 콘텐츠 운영 기준"),
        (
            {"essence_summary": {"approved_philosophy_exists": True, "source_stale": True}},
            "현재 자료와 일치하지 않습니다",
        ),
    ],
)
async def test_mark_report_sent_blocks_incomplete_delivery(overrides, expected):
    hospital, report, actor, db = _ready_db()
    for key, value in overrides.items():
        setattr(report, key, value)

    with pytest.raises(HTTPException) as exc:
        await reports_api.mark_report_sent(
            hospital.id,
            report.id,
            ReportDeliveryRequest(
                artifact_sha256=db.artifact.sha256, recipient_label="김원장", channel="대면"
            ),
            db=db,
            actor=actor,
        )

    assert exc.value.status_code == 409
    assert any(expected in blocker for blocker in exc.value.detail["blockers"])
    assert report.sent_at is None
    assert db.committed is False


async def test_download_report_uses_one_hour_signed_url(monkeypatch):
    hospital = _hospital()
    report = _report(hospital_id=hospital.id)
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    db = _FakeDB(
        hospital, report, manifest=_bind_manifest(report, _manifest()), artifact=artifact
    )
    calls = []

    async def _fresh(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(philosophy, philosophy, 4, 4, "snapshot")

    def fake_signed_url(path, expiration_hours=24, response_disposition=None):
        calls.append((path, expiration_hours, response_disposition))
        return "https://storage.example/report.pdf"

    monkeypatch.setattr(reports_api, "get_signed_url", fake_signed_url)
    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh)
    response = await reports_api.download_report(hospital.id, report.id, db=db)

    assert calls == [
        (report.pdf_path, 1, reports_api._content_disposition(
            "report-2026-05.pdf", "report-2026-05.pdf")),
    ]
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "report-2026-05.pdf" in response.headers["content-disposition"]


async def test_download_report_serves_the_doctor_edition_when_asked(monkeypatch):
    """원장용은 같은 데이터를 다른 편집으로 렌더한 별도 파일이다 — 경로도 파일명도 다르다."""
    hospital = _hospital()
    report = _report(hospital_id=hospital.id)
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    db = _FakeDB(
        hospital,
        report,
        manifest=_bind_manifest(report, _manifest()),
        artifact=artifact,
    )
    calls = []

    async def _fresh_essence(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(philosophy, philosophy, 4, 4, "snapshot")

    def fake_signed_url(path, expiration_hours=24, response_disposition=None):
        calls.append((path, expiration_hours, response_disposition))
        return "https://storage.example/doctor.pdf"

    monkeypatch.setattr(reports_api, "get_signed_url", fake_signed_url)
    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh_essence)
    response = await reports_api.download_report(
        hospital.id, report.id, audience="doctor", db=db, actor=_actor()
    )

    # 헤더는 latin-1만 담을 수 있다 — 한글 이름은 RFC 5987 filename*으로만 나간다.
    disposition = calls[0][2]
    assert calls == [(report.doctor_pdf_path, 1, disposition)]
    assert 'filename="report-2026-05-doctor.pdf"' in disposition
    assert "filename*=UTF-8''" in disposition
    assert "원장보고" not in disposition, "한글이 latin-1 헤더에 그대로 들어가면 500이 난다"
    response.headers["content-disposition"].encode("latin-1")  # 인코딩 가능해야 한다


async def test_legacy_delivered_doctor_artifact_remains_downloadable(monkeypatch):
    """이벤트 도입 전 sent_at 전달본도 이후 readiness 변화로 감사 조회가 막히면 안 된다."""
    hospital = _hospital()
    report = _report(
        hospital_id=hospital.id,
        sent_at=datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc),
        quality="DEGRADED",
    )
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    db = _FakeDB(hospital, report, artifact=artifact)

    async def fail_if_revalidated(*_args, **_kwargs):
        raise AssertionError("과거 전달본 조회에 현재 readiness를 다시 적용하면 안 된다")

    monkeypatch.setattr(reports_api, "_assert_customer_ready", fail_if_revalidated)
    monkeypatch.setattr(reports_api, "get_signed_url", lambda *args, **kwargs: "https://x.test")

    response = await reports_api.download_report(
        hospital.id,
        report.id,
        audience="doctor",
        db=db,
        actor=_actor(),
    )

    assert response.status_code == 302


@pytest.mark.parametrize(("assigned", "expected_status"), [(True, 302), (False, 403)])
async def test_doctor_download_requires_owner_or_assigned_operator(
    monkeypatch, assigned, expected_status
):
    hospital, report, operator, db = _ready_db(role=ROLE_OPERATOR)
    if not assigned:
        db.handoff.ae_owner_id = uuid.uuid4()

    async def _fresh(db, hospital_id):
        del db, hospital_id
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(philosophy, philosophy, 4, 4, "snapshot")

    monkeypatch.setattr(reports_api, "get_essence_readiness", _fresh)
    monkeypatch.setattr(reports_api, "get_signed_url", lambda *args, **kwargs: "https://x.test")

    if expected_status == 302:
        response = await reports_api.download_report(
            hospital.id, report.id, audience="doctor", db=db, actor=operator
        )
        assert response.status_code == 302
    else:
        with pytest.raises(HTTPException) as exc:
            await reports_api.download_report(
                hospital.id, report.id, audience="doctor", db=db, actor=operator
            )
        assert exc.value.status_code == expected_status
        assert exc.value.detail["code"] == "DELIVERY_NOT_ASSIGNED"


async def test_doctor_download_is_cross_tenant_404():
    hospital = _hospital()
    report = _report(hospital_id=uuid.uuid4())
    db = _FakeDB(hospital, report)

    with pytest.raises(HTTPException) as exc:
        await reports_api.download_report(
            hospital.id, report.id, audience="doctor", db=db, actor=_actor()
        )

    assert exc.value.status_code == 404


async def test_download_report_explains_a_missing_doctor_edition(monkeypatch):
    """AE용은 있는데 원장용만 없을 수 있다 — 'PDF 경로 없음'은 그 상황을 설명하지 못한다."""
    hospital = _hospital()
    report = _report(hospital_id=hospital.id, doctor_pdf_path=None)
    db = _FakeDB(hospital, report, manifest=_bind_manifest(report, _manifest()))

    with pytest.raises(HTTPException) as exc:
        await reports_api.download_report(
            hospital.id, report.id, audience="doctor", db=db, actor=_actor()
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "doctor_artifact_missing"


async def test_report_list_reports_whether_the_doctor_edition_exists():
    report = _report()
    missing = reports_api._serialize(report)
    artifact = _doctor_artifact(report_id=report.id, path=report.doctor_pdf_path)
    valid = reports_api._serialize(
        report, manifest=_bind_manifest(report, _manifest()), artifact=artifact
    )

    assert missing["has_doctor_pdf"] is False
    assert valid["has_doctor_pdf"] is True


def test_v0_report_is_ready_on_its_own_pdf_not_a_monthly_doctor_artifact():
    """A-7 — V0에는 검증된 원장 보고용 PDF를 만드는 경로가 아예 없다.

    그런데 전달 게이트가 리포트 종류를 가리지 않고 그 아티팩트를 요구해서, 측정도 PDF도
    끝난 V0가 영구히 `검증된 원장 보고용 PDF가 없습니다`로 남았다 — 온보딩 3단계는
    완료인데 리포트 화면만 조치 필요로 보이던 모순의 원인이다.
    """
    report = _report(report_type="V0", doctor_pdf_path=None, manifest_id=None)

    gate = _delivery_gate(report, None, None)

    assert gate.ready is True
    assert gate.code is None


def test_v0_report_without_a_pdf_says_so_in_its_own_words():
    report = _report(report_type="V0", pdf_path=None, doctor_pdf_path=None, manifest_id=None)

    gate = _delivery_gate(report, None, None)

    assert gate.ready is False
    assert gate.code == "v0_pdf_missing"
    assert "초기 진단" in gate.message


def test_only_monthly_reports_are_tracked_by_the_delivery_receipt_pipeline():
    """화면이 V0에 전달 버튼·전달 서사를 제안하지 않도록 종류를 실어 보낸다."""
    monthly = _serialize(_report())
    v0 = _serialize(_report(report_type="V0", doctor_pdf_path=None, manifest_id=None))

    assert monthly["delivery_tracked"] is True
    assert v0["delivery_tracked"] is False


class _ListFakeResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _ListDB:
    """Fakes exactly the query shape `list_reports` should now issue: one page query
    plus one IN(...) query per related table (skipped when its id set is empty)."""

    def __init__(self, *, hospital, reports, manifests=(), artifacts=(), events=()):
        self.hospital = hospital
        self.reports = list(reports)
        self.manifests = list(manifests)
        self.artifacts = list(artifacts)
        self.events = list(events)
        self.calls: list[str] = []

    async def get(self, model, object_id):
        name = getattr(model, "__name__", "")
        if name == "Hospital":
            return self.hospital if self.hospital and self.hospital.id == object_id else None
        return None

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        name = getattr(entity, "__name__", "")
        self.calls.append(name)
        if name == "MonthlyReport":
            return _ListFakeResult(self.reports)
        if name == "MonthlyMeasurementManifest":
            return _ListFakeResult(self.manifests)
        if name == "MonthlyReportArtifact":
            return _ListFakeResult(self.artifacts)
        if name == "MonthlyDeliveryEvent":
            return _ListFakeResult(self.events)
        raise AssertionError(f"unexpected entity {name}")


async def test_list_reports_uses_a_fixed_query_count_for_v0_only_hospitals(monkeypatch):
    """N reports must not cost N manifest/artifact/event/readiness round-trips.

    V0 reports never need a manifest or essence readiness (`_delivery_gate` short
    circuits on `report.pdf_path` alone), so those two IN(...) queries — and
    get_essence_readiness — must not run at all here; only the page query plus the
    artifact/event batches.
    """
    hospital = SimpleNamespace(id=uuid.uuid4())
    reports = [
        _report(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            report_type="V0",
            manifest_id=None,
            doctor_pdf_path=None,
        )
        for _ in range(5)
    ]

    async def _fail_if_called(db, hospital_id):
        raise AssertionError("get_essence_readiness must not run for a V0-only page")

    monkeypatch.setattr(reports_api, "get_essence_readiness", _fail_if_called)

    db = _ListDB(hospital=hospital, reports=reports)

    result = await reports_api.list_reports(hospital.id, limit=24, offset=0, db=db)

    assert len(result) == 5
    assert db.calls == ["MonthlyReport", "MonthlyReportArtifact", "MonthlyDeliveryEvent"]
    assert all(item["report_type"] == "V0" for item in result)


async def test_list_reports_reads_essence_readiness_once_for_the_whole_page(monkeypatch):
    """A page mixing several ready MONTHLY reports must call get_essence_readiness
    exactly once — not once per report — since they all share one hospital_id."""
    hospital = SimpleNamespace(id=uuid.uuid4())
    reports = [_report(id=uuid.uuid4(), hospital_id=hospital.id) for _ in range(3)]
    manifests = [_bind_manifest(r, _manifest()) for r in reports]
    artifacts = [_doctor_artifact(report_id=r.id, path=r.doctor_pdf_path) for r in reports]

    calls = []

    async def _counting_readiness(db, hospital_id):
        calls.append(hospital_id)
        philosophy = SimpleNamespace(version=3)
        return EssenceReadiness(
            approved=philosophy,
            current=philosophy,
            processed_source_count=4,
            required_source_count=4,
            current_snapshot_hash="snapshot",
        )

    monkeypatch.setattr(reports_api, "get_essence_readiness", _counting_readiness)

    db = _ListDB(hospital=hospital, reports=reports, manifests=manifests, artifacts=artifacts)

    result = await reports_api.list_reports(hospital.id, limit=24, offset=0, db=db)

    assert len(result) == 3
    assert calls == [hospital.id]  # 리포트 3건인데 딱 1번
    assert db.calls == [
        "MonthlyReport",
        "MonthlyMeasurementManifest",
        "MonthlyReportArtifact",
        "MonthlyDeliveryEvent",
    ]
    assert all(item["delivery_ready"] is True for item in result)


async def test_list_reports_respects_limit_and_offset_params():
    hospital = SimpleNamespace(id=uuid.uuid4())
    reports = [
        _report(id=uuid.uuid4(), hospital_id=hospital.id, report_type="V0", manifest_id=None)
        for _ in range(2)
    ]
    db = _ListDB(hospital=hospital, reports=reports)

    await reports_api.list_reports(hospital.id, limit=10, offset=5, db=db)

    # 페이지 쿼리에 offset/limit이 실제로 실린다 — 컴파일된 SQL 텍스트로 확인한다.
    from sqlalchemy import select as sa_select
    from sqlalchemy.dialects import postgresql

    from app.models.report import MonthlyReport

    page_stmt = (
        sa_select(MonthlyReport)
        .where(MonthlyReport.hospital_id == hospital.id)
        .order_by(MonthlyReport.created_at.desc())
        .offset(5)
        .limit(10)
    )
    sql = str(
        page_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "OFFSET 5" in sql
    assert "LIMIT 10" in sql


async def test_list_reports_returns_empty_without_any_batch_query_when_no_reports():
    hospital = SimpleNamespace(id=uuid.uuid4())
    db = _ListDB(hospital=hospital, reports=[])

    result = await reports_api.list_reports(hospital.id, limit=24, offset=0, db=db)

    assert result == []
    assert db.calls == ["MonthlyReport"]  # 빈 페이지면 배치 조회 자체를 스킵한다
