"""리포트 생성 + 열람 표면 (설계 §6-3 · §6-4).

토큰이 유일한 열쇠이므로, 여기서 새면 남의 병원 진단 결과가 그대로 노출된다.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.public import diagnosis as diagnosis_api
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    ExecutionStatus,
    LeadDiagnosis,
    LeadReportArtifact,
    LeadReportToken,
    ReportStatus,
)
from app.services import lead_report_token

get_status = diagnosis_api.get_diagnosis_status
get_report = diagnosis_api.get_diagnosis_report

_slot_sequence = itertools.count(300)


async def _seed(
    session,
    *,
    execution_status=ExecutionStatus.SUCCEEDED.value,
    report_status=ReportStatus.READY.value,
    with_artifact=True,
    expires_in_days=30,
    revoked=False,
    tmp_path=None,
):
    lead = SalesLead(
        clinic_name="리포트테스트의원",
        clinic_type="내과",
        contact="010-0000-0000",
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add(lead)
    await session.flush()

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name="리포트테스트의원",
        subject_region="수서역",
        slot_date=date(2026, 8, 25),
        slot_no=next(_slot_sequence),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        execution_status=execution_status,
        report_status=report_status,
    )
    session.add(diagnosis)
    await session.flush()

    raw, token_hash = lead_report_token.issue_report_token(diagnosis.id)
    session.add(
        LeadReportToken(
            diagnosis_id=diagnosis.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
            revoked_at=datetime.now(timezone.utc) if revoked else None,
        )
    )

    if with_artifact and tmp_path is not None:
        pdf_path = tmp_path / f"{diagnosis.id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.7 fake report bytes")
        session.add(
            LeadReportArtifact(
                diagnosis_id=diagnosis.id,
                version=1,
                storage_uri=str(pdf_path),
                content_hash="deadbeef",
                byte_size=pdf_path.stat().st_size,
                template_version="lead-v1",
            )
        )
    await session.flush()
    return diagnosis, raw


@pytest.mark.asyncio
class TestStatusEndpoint:
    async def test_measuring_phase_before_execution_finishes(self, pg_async_session):
        _, raw = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.RUNNING.value,
            report_status=ReportStatus.PENDING.value,
            with_artifact=False,
        )
        body = await get_status(raw, pg_async_session)
        assert body["phase"] == "MEASURING"
        assert body["report_ready"] is False

    async def test_ready_phase_when_the_report_exists(self, pg_async_session, tmp_path):
        _, raw = await _seed(pg_async_session, tmp_path=tmp_path)
        body = await get_status(raw, pg_async_session)
        assert body["phase"] == "READY"
        assert body["report_ready"] is True

    async def test_internal_status_names_are_not_exposed(self, pg_async_session):
        """'PARTIAL'·'BUILDING'은 신청자에게 의미가 없고, 내부 모델 변경이 공개 계약을 흔든다."""
        _, raw = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.PARTIAL.value,
            report_status=ReportStatus.PENDING.value,
            with_artifact=False,
        )
        body = await get_status(raw, pg_async_session)
        assert body["phase"] == "BUILDING_REPORT"
        assert "PARTIAL" not in str(body)

    async def test_failed_execution_does_not_leak_the_internal_error(self, pg_async_session):
        diagnosis, raw = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.FAILED.value,
            report_status=ReportStatus.PENDING.value,
            with_artifact=False,
        )
        diagnosis.error = "OpenAIError: invalid api key sk-secret"
        await pg_async_session.flush()

        body = await get_status(raw, pg_async_session)
        assert body["phase"] == "FAILED"
        assert "sk-secret" not in str(body)


@pytest.mark.asyncio
class TestTokenGate:
    async def test_unknown_token_is_404(self, pg_async_session):
        with pytest.raises(HTTPException) as exc:
            await get_status("not-a-real-token", pg_async_session)
        assert exc.value.status_code == 404

    async def test_expired_token_is_indistinguishable_from_unknown(self, pg_async_session):
        """구분해 알려주면 토큰 존재 여부를 확인하는 오라클이 된다."""
        _, raw = await _seed(pg_async_session, expires_in_days=-1, with_artifact=False)
        with pytest.raises(HTTPException) as exc:
            await get_status(raw, pg_async_session)
        assert exc.value.status_code == 404
        assert exc.value.detail == "유효하지 않은 링크입니다."

    async def test_revoked_token_is_rejected(self, pg_async_session, tmp_path):
        """파기 시 토큰을 회수한다 — 회수가 실제로 통해야 파기가 성립한다."""
        _, raw = await _seed(pg_async_session, revoked=True, tmp_path=tmp_path)
        with pytest.raises(HTTPException) as exc:
            await get_report(raw, pg_async_session)
        assert exc.value.status_code == 404

    async def test_access_is_counted(self, pg_async_session, tmp_path):
        diagnosis, raw = await _seed(pg_async_session, tmp_path=tmp_path)
        await get_status(raw, pg_async_session)
        await get_status(raw, pg_async_session)

        token = (
            await pg_async_session.execute(
                select(LeadReportToken).where(LeadReportToken.diagnosis_id == diagnosis.id)
            )
        ).scalar_one()
        assert token.access_count == 2
        assert token.last_accessed_at is not None

    async def test_a_token_only_opens_its_own_diagnosis(self, pg_async_session, tmp_path):
        """토큰이 유일한 열쇠다 — 하나가 다른 진단을 열면 남의 결과가 그대로 노출된다."""
        first, first_raw = await _seed(pg_async_session, tmp_path=tmp_path)
        second, second_raw = await _seed(pg_async_session, tmp_path=tmp_path)

        # 두 산출물의 내용을 구분 가능하게 바꾼다.
        for diagnosis, marker in ((first, b"FIRST-REPORT"), (second, b"SECOND-REPORT")):
            artifact = (
                await pg_async_session.execute(
                    select(LeadReportArtifact).where(
                        LeadReportArtifact.diagnosis_id == diagnosis.id
                    )
                )
            ).scalar_one()
            (tmp_path / f"{diagnosis.id}.pdf").write_bytes(b"%PDF-1.7 " + marker)
            assert artifact.storage_uri.endswith(f"{diagnosis.id}.pdf")

        assert b"FIRST-REPORT" in (await get_report(first_raw, pg_async_session)).body
        assert b"SECOND-REPORT" in (await get_report(second_raw, pg_async_session)).body


@pytest.mark.asyncio
class TestReportDelivery:
    async def test_ready_report_is_served_with_no_store_headers(self, pg_async_session, tmp_path):
        """링크가 전달돼도 검색엔진에 잡히거나 referrer로 새어나가면 안 된다 (F5-4)."""
        _, raw = await _seed(pg_async_session, tmp_path=tmp_path)
        response = await get_report(raw, pg_async_session)

        assert response.status_code == 200
        assert response.media_type == "application/pdf"
        assert response.body.startswith(b"%PDF")
        assert response.headers["cache-control"] == "no-store"
        assert "noindex" in response.headers["x-robots-tag"]
        assert response.headers["referrer-policy"] == "no-referrer"

    async def test_report_not_ready_is_409_not_an_empty_pdf(self, pg_async_session):
        """빈 응답을 200으로 주면 원장이 '내용 없는 리포트'를 받는다."""
        _, raw = await _seed(
            pg_async_session, report_status=ReportStatus.PENDING.value, with_artifact=False
        )
        with pytest.raises(HTTPException) as exc:
            await get_report(raw, pg_async_session)
        assert exc.value.status_code == 409

    async def test_ready_without_an_artifact_is_409_not_500(self, pg_async_session):
        """READY인데 산출물이 없다 = 상태와 파일이 어긋난 상태. 조용히 빈 응답을 주지 않는다."""
        _, raw = await _seed(pg_async_session, with_artifact=False)
        with pytest.raises(HTTPException) as exc:
            await get_report(raw, pg_async_session)
        assert exc.value.status_code == 409

    async def test_purged_report_is_410_gone_not_404(self, pg_async_session, tmp_path):
        """404는 '없었다'는 뜻이다. 있었고 우리가 지웠다는 것이 사실이므로 410이다."""
        _, raw = await _seed(
            pg_async_session, report_status=ReportStatus.PURGED.value, tmp_path=tmp_path
        )
        with pytest.raises(HTTPException) as exc:
            await get_report(raw, pg_async_session)
        assert exc.value.status_code == 410

    async def test_the_newest_unpurged_version_is_served(self, pg_async_session, tmp_path):
        """재생성 시 토큰은 그대로 두고 version만 올린다 — 이미 보낸 메일이 죽으면 안 된다."""
        diagnosis, raw = await _seed(pg_async_session, tmp_path=tmp_path)
        newer = tmp_path / "v2.pdf"
        newer.write_bytes(b"%PDF-1.7 second version")
        pg_async_session.add(
            LeadReportArtifact(
                diagnosis_id=diagnosis.id,
                version=2,
                storage_uri=str(newer),
                content_hash="cafe",
                byte_size=newer.stat().st_size,
                template_version="lead-v1",
            )
        )
        await pg_async_session.flush()

        response = await get_report(raw, pg_async_session)
        assert b"second version" in response.body

    async def test_purged_artifact_rows_are_skipped(self, pg_async_session, tmp_path):
        diagnosis, raw = await _seed(pg_async_session, tmp_path=tmp_path)
        purged = tmp_path / "v2-purged.pdf"
        purged.write_bytes(b"%PDF-1.7 deleted version")
        pg_async_session.add(
            LeadReportArtifact(
                diagnosis_id=diagnosis.id,
                version=2,
                storage_uri=str(purged),
                content_hash="cafe",
                byte_size=purged.stat().st_size,
                template_version="lead-v1",
                purged_at=datetime.now(timezone.utc),
            )
        )
        await pg_async_session.flush()

        response = await get_report(raw, pg_async_session)
        assert b"fake report bytes" in response.body
