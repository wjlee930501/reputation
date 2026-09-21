"""Stored bytes, not metadata alone, authorize report delivery."""
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services.report_file_integrity import ReportFileUnavailable, read_verified_report

DATA = b"%PDF-1.7\nverified fictional report\n%%EOF"
DIGEST = hashlib.sha256(DATA).hexdigest()


@pytest.mark.parametrize("state", ["healthy", "missing", "same_size", "truncated", "oversized"])
def test_local_verified_snapshot(tmp_path, monkeypatch, state):
    monkeypatch.setattr(settings, "REPORT_OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "report.pdf"
    if state != "missing":
        payload = {"healthy": DATA, "same_size": b"x" * len(DATA),
                   "truncated": DATA[:-1], "oversized": DATA + b"x"}[state]
        path.write_bytes(payload)
    if state == "healthy":
        snapshot = read_verified_report(str(path), DIGEST, len(DATA))
        path.write_bytes(b"x" * len(DATA))
        assert snapshot == DATA
    else:
        with pytest.raises(ReportFileUnavailable):
            read_verified_report(str(path), DIGEST, len(DATA))


@pytest.mark.parametrize("state", ["healthy", "missing", "same_size", "generation_changed"])
def test_cloud_snapshot_is_generation_pinned(monkeypatch, state):
    from google.cloud import storage
    blob = MagicMock(size=len(DATA), generation=42)
    blob.download_as_bytes.return_value = DATA if state != "same_size" else b"x" * len(DATA)
    if state == "missing":
        blob.reload.side_effect = FileNotFoundError("missing fixture")
    if state == "generation_changed":
        blob.download_as_bytes.side_effect = RuntimeError("precondition failed")
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    monkeypatch.setattr(storage, "Client", lambda: client)
    monkeypatch.setattr(settings, "GCS_REPORTS_BUCKET", "fixture-reports")
    path = "gs://fixture-reports/clinic/report.pdf"
    if state == "healthy":
        assert read_verified_report(path, DIGEST, len(DATA)) == DATA
        assert blob.download_as_bytes.call_args.kwargs["if_generation_match"] == 42
        assert blob.download_as_bytes.call_args.kwargs["end"] == len(DATA)
    else:
        with pytest.raises(ReportFileUnavailable):
            read_verified_report(path, DIGEST, len(DATA))


def test_local_path_cannot_escape_report_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_OUTPUT_DIR", str(tmp_path / "allowed"))
    path = tmp_path / "outside.pdf"
    path.write_bytes(DATA)
    with pytest.raises(ReportFileUnavailable):
        read_verified_report(str(path), DIGEST, len(DATA))


@pytest.fixture
def ready_report(tmp_path, monkeypatch):
    from test_admin_reports import _ready_db

    from app.api.admin import reports
    from app.services.essence_readiness import EssenceReadiness
    hospital, report, actor, db = _ready_db()
    path = tmp_path / "doctor.pdf"
    path.write_bytes(DATA)
    monkeypatch.setattr(settings, "REPORT_OUTPUT_DIR", str(tmp_path))
    report.doctor_pdf_path = str(path)
    db.artifact.path = str(path)
    db.artifact.sha256 = DIGEST
    db.artifact.byte_size = len(DATA)
    db.artifact.validation_metadata.update(sha256=DIGEST, byte_size=len(DATA))
    async def fresh(*args, **kwargs):
        p = SimpleNamespace(version=3)
        return EssenceReadiness(p, p, 4, 4, "snapshot")
    monkeypatch.setattr(reports, "get_essence_readiness", fresh)
    return hospital, report, actor, db, path


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "same_size"])
async def test_doctor_download_rejects_unverified_stored_bytes(ready_report, state):
    from fastapi import HTTPException

    from app.api.admin import reports
    hospital, report, actor, db, path = ready_report
    path.unlink() if state == "missing" else path.write_bytes(b"x" * len(DATA))
    with pytest.raises(HTTPException) as exc:
        await reports.download_report(hospital.id, report.id, audience="doctor", db=db, actor=actor)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "artifact_storage_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "same_size"])
async def test_mark_sent_rejects_unverified_stored_bytes(ready_report, state):
    from fastapi import HTTPException

    from app.api.admin import reports
    from app.schemas.report import ReportDeliveryRequest
    hospital, report, actor, db, path = ready_report
    path.unlink() if state == "missing" else path.write_bytes(b"x" * len(DATA))
    with pytest.raises(HTTPException) as exc:
        await reports.mark_report_sent(hospital.id, report.id,
            ReportDeliveryRequest(artifact_sha256=DIGEST, recipient_label="가상 원장", channel="대면"),
            db=db, actor=actor)
    assert exc.value.detail["code"] == "artifact_storage_mismatch"
    assert report.sent_at is None
    assert not any(type(row).__name__ == "MonthlyDeliveryEvent" for row in db.added)


@pytest.mark.asyncio
async def test_doctor_download_serves_exact_verified_bytes(ready_report):
    from app.api.admin import reports
    hospital, report, actor, db, _ = ready_report
    response = await reports.download_report(hospital.id, report.id, audience="doctor", db=db, actor=actor)
    assert response.status_code == 200
    assert response.body == DATA


@pytest.mark.asyncio
async def test_download_reads_storage_off_the_event_loop(ready_report, monkeypatch):
    import threading

    from app.api.admin import reports
    hospital, report, actor, db, _ = ready_report
    loop_thread = threading.get_ident()
    reader = reports.read_verified_report
    observed = []
    def tracked(*args):
        observed.append(threading.get_ident())
        return reader(*args)
    monkeypatch.setattr(reports, "read_verified_report", tracked)
    response = await reports.download_report(hospital.id, report.id, audience="doctor", db=db, actor=actor)
    assert response.body == DATA
    assert observed and all(thread != loop_thread for thread in observed)


@pytest.mark.asyncio
async def test_historical_delivery_still_checks_storage(ready_report):
    from datetime import datetime, timezone

    from fastapi import HTTPException

    from app.api.admin import reports
    hospital, report, actor, db, path = ready_report
    report.sent_at = datetime.now(timezone.utc)
    path.write_bytes(b"x" * len(DATA))
    with pytest.raises(HTTPException) as exc:
        await reports.download_report(hospital.id, report.id, audience="doctor", db=db, actor=actor)
    assert exc.value.detail["code"] == "artifact_storage_mismatch"
