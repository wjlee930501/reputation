"""파기 cascade (설계 T-12 · §6-4 · 개인정보보호법 제21조).

`sales_leads` 행만 익명화하고 진단 산출물을 남기면 **파기가 거짓말이 된다.**
리포트 PDF는 GCS에 남고, 열람 토큰은 살아 있고, AI 답변 원문도 그대로다.
"""
import itertools
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
    LeadReportArtifact,
    LeadReportToken,
    ReportStatus,
)
from app.services import lead_privacy, lead_report, lead_report_token

_slot_sequence = itertools.count(700)


@pytest.fixture
def sync_session(pg_engine):
    """파기 배치는 동기 세션(SyncSessionLocal)에서 돈다 — 같은 방식으로 시험한다."""
    connection = pg_engine.connect()
    trans = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _seed(session, tmp_path, *, expired=True):
    now = datetime.now(timezone.utc)
    lead = SalesLead(
        clinic_name="파기테스트의원",
        clinic_type="내과",
        contact="010-1234-5678",
        contact_name="홍길동",
        clinic_phone="02-111-2222",
        email="doctor@example.com",
        privacy=True,
        source="AI_DIAGNOSIS",
        retain_until=now - timedelta(days=1) if expired else now + timedelta(days=30),
    )
    session.add(lead)
    session.flush()

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash="email-hash-keepme",
        subject_phone_hash="phone-hash-keepme",
        subject_hospital_name="파기테스트의원",
        subject_region="수서역",
        slot_date=date(2026, 9, 1),
        slot_no=next(_slot_sequence),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        execution_status=ExecutionStatus.SUCCEEDED.value,
        report_status=ReportStatus.READY.value,
    )
    session.add(diagnosis)
    session.flush()

    session.add(
        LeadDiagnosisResult(
            diagnosis_id=diagnosis.id,
            platform="chatgpt",
            query_slot=1,
            repeat_no=1,
            attempt_no=1,
            query_text="수서역 근처 내과 병원 추천해줘",
            requested_model="m",
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="파기테스트의원과 경쟁의원을 추천드립니다",
            measured_at=now,
        )
    )
    _, token_hash = lead_report_token.issue_report_token(diagnosis.id)
    session.add(
        LeadReportToken(
            diagnosis_id=diagnosis.id,
            token_hash=token_hash,
            expires_at=now + timedelta(days=30),
        )
    )

    pdf_path = tmp_path / f"{diagnosis.id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 report")
    session.add(
        LeadReportArtifact(
            diagnosis_id=diagnosis.id,
            version=1,
            storage_uri=str(pdf_path),
            content_hash="hash",
            byte_size=pdf_path.stat().st_size,
            template_version="lead-v1",
        )
    )
    session.flush()
    return lead, diagnosis, pdf_path


class TestPurgeCascade:
    def test_everything_identifiable_is_gone(self, sync_session, tmp_path):
        lead, diagnosis, pdf_path = _seed(sync_session, tmp_path)
        now = datetime.now(timezone.utc)

        assert lead_privacy.anonymize_lead(lead, now) is True
        lead_privacy.purge_lead_diagnosis_artifacts(sync_session, lead.id, now)
        sync_session.flush()

        assert lead.email == "[purged]"
        assert lead.contact_name == "[purged]"
        assert lead.clinic_phone == "[purged]"
        assert lead.clinic_name == "[purged]"
        assert lead.consent_ip is None

        assert not pdf_path.exists(), "GCS/로컬 산출물이 남아 있다 — 파기가 거짓말이 된다"

        artifact = sync_session.execute(
            select(LeadReportArtifact).where(LeadReportArtifact.diagnosis_id == diagnosis.id)
        ).scalar_one()
        assert artifact.purged_at is not None
        # 행은 남긴다 — 삭제했다는 사실 자체가 증거다.
        assert artifact.storage_uri

        token = sync_session.execute(
            select(LeadReportToken).where(LeadReportToken.diagnosis_id == diagnosis.id)
        ).scalar_one()
        assert token.revoked_at is not None

        result = sync_session.execute(
            select(LeadDiagnosisResult).where(
                LeadDiagnosisResult.diagnosis_id == diagnosis.id
            )
        ).scalar_one()
        assert result.raw_response == ""

        sync_session.refresh(diagnosis)
        assert diagnosis.report_status == ReportStatus.PURGED.value

    def test_lock_hashes_survive_the_purge(self, sync_session, tmp_path):
        """보관기간이 지났다고 두 번째 무료 진단을 주는 것이 아니다 (PRD F1-6).

        해시는 개인정보가 아니고(원문은 지웠다), 잠금은 영구다.
        """
        lead, diagnosis, _ = _seed(sync_session, tmp_path)
        now = datetime.now(timezone.utc)
        lead_privacy.anonymize_lead(lead, now)
        lead_privacy.purge_lead_diagnosis_artifacts(sync_session, lead.id, now)
        sync_session.flush()

        sync_session.refresh(diagnosis)
        assert diagnosis.applicant_email_hash == "email-hash-keepme"
        assert diagnosis.subject_phone_hash == "phone-hash-keepme"

    def test_purge_is_idempotent(self, sync_session, tmp_path):
        """매일 04:00에 반복 실행된다 — 두 번째 실행이 깨지면 그날의 파기가 통째로 멈춘다."""
        lead, _, _ = _seed(sync_session, tmp_path)
        now = datetime.now(timezone.utc)
        lead_privacy.anonymize_lead(lead, now)
        lead_privacy.purge_lead_diagnosis_artifacts(sync_session, lead.id, now)
        sync_session.flush()

        assert lead_privacy.anonymize_lead(lead, now) is False
        again = lead_privacy.purge_lead_diagnosis_artifacts(sync_session, lead.id, now)
        assert again["artifacts"] == 0

    def test_storage_deletion_failure_blocks_the_db_change(self, sync_session, tmp_path,
                                                           monkeypatch):
        """순서가 뒤집히면 purged_at은 찍혔는데 파일은 사는, 가장 나쁜 상태가 된다."""
        lead, diagnosis, pdf_path = _seed(sync_session, tmp_path)

        def boom(uri):
            raise RuntimeError("GCS unavailable")

        monkeypatch.setattr(lead_report, "delete_report_pdf", boom)

        with pytest.raises(RuntimeError):
            lead_privacy.purge_lead_diagnosis_artifacts(
                sync_session, lead.id, datetime.now(timezone.utc)
            )

        artifact = sync_session.execute(
            select(LeadReportArtifact).where(LeadReportArtifact.diagnosis_id == diagnosis.id)
        ).scalar_one()
        assert artifact.purged_at is None
        assert pdf_path.exists()
