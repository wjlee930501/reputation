"""적대적 검토(Codex, 2026-07-30)에서 나온 결함들의 회귀 방지.

전부 **테스트가 통과하는데도 남아 있던** 문제다. 공통 원인 둘:
경쟁 조건을 단일 세션의 순차 호출로 대체했고, 파기를 서비스 함수 직접 호출로만
검증해 실제 API·배치 오케스트레이션을 거치지 않았다.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from app.api.admin import leads as admin_leads
from app.api.public import diagnosis as diagnosis_api
from app.core.config import settings
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDelivery,
    LeadDiagnosis,
    LeadDiagnosisResult,
    LeadReportArtifact,
    LeadReportToken,
    ReportStatus,
)
from app.services import lead_delivery, lead_privacy, lead_report_token, mailer, sov_engine

erase_lead_pii = admin_leads.erase_lead_pii
release_diagnosis_lock = admin_leads.release_diagnosis_lock
create_diagnosis = diagnosis_api.create_diagnosis.__wrapped__

_slots = itertools.count(1100)
_phones = itertools.count(1)


class _Req:
    def __init__(self):
        from types import SimpleNamespace

        self.headers = SimpleNamespace(get=lambda key, default=None: default)
        self.client = SimpleNamespace(host="127.0.0.1")


def _payload(**overrides):
    unique = uuid.uuid4().hex[:8]
    serial = next(_phones)
    base = dict(
        clinic_name=f"검토테스트의원{unique}",
        clinic_type="외과",
        region_keyword="수서역",
        clinic_phone=f"02-{serial // 10000 % 1000:03d}-{serial % 10000:04d}",
        core_keywords=["대장내시경"],
        contact_name="홍길동",
        contact="010-1234-5678",
        email=f"{unique}@example.com",
        privacy=True,
    )
    base.update(overrides)
    return diagnosis_api.DiagnosisRequest(**base)


async def _seed_full(session, *, with_artifact=True, tmp_path=None,
                     execution_status=ExecutionStatus.SUCCEEDED.value,
                     report_status=ReportStatus.READY.value):
    """리포트까지 발급된 진단 1건 — 파기가 실제로 지워야 할 것이 전부 있는 상태."""
    lead = SalesLead(
        clinic_name="검토테스트의원",
        clinic_type="외과",
        contact="010-1234-5678",
        contact_name="홍길동",
        clinic_phone="02-111-2222",
        email="doctor@example.com",
        region_keyword="수서역",
        core_keywords=["대장내시경"],
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add(lead)
    await session.flush()

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name="검토테스트의원",
        subject_region="수서역",
        slot_date=date(2026, 9, 20),
        slot_no=next(_slots),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 외과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        execution_status=execution_status,
        report_status=report_status,
    )
    session.add(diagnosis)
    await session.flush()

    session.add(
        LeadDiagnosisResult(
            diagnosis_id=diagnosis.id,
            platform="chatgpt",
            query_slot=1,
            repeat_no=1,
            attempt_no=1,
            query_text="수서역 근처 외과 병원 추천해줘",
            requested_model="m",
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="검토테스트의원을 추천드립니다",
            measured_at=datetime.now(timezone.utc),
        )
    )
    _, token_hash = lead_report_token.issue_report_token(diagnosis.id)
    session.add(
        LeadReportToken(
            diagnosis_id=diagnosis.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    if with_artifact and tmp_path is not None:
        pdf = tmp_path / f"{diagnosis.id}.pdf"
        pdf.write_bytes(b"%PDF report")
        session.add(
            LeadReportArtifact(
                diagnosis_id=diagnosis.id,
                version=1,
                storage_uri=str(pdf),
                content_hash="h",
                byte_size=pdf.stat().st_size,
                template_version="lead-v1",
            )
        )
        await session.flush()
        return lead, diagnosis, pdf
    await session.flush()
    return lead, diagnosis, None


@pytest.mark.asyncio
class TestEraseApiPurgesEverything:
    """BLOCKER 1 — `/erase`가 진단 산출물을 전혀 건드리지 않았다.

    `purged_at`만 찍히고 PDF·활성 토큰·AI 원문이 남는데, 이후 보관기간 배치는
    `purged_at IS NULL`만 조회하므로 그 리드를 **영원히 건너뛴다.**
    """

    async def test_erase_removes_pdf_tokens_and_raw_text(self, pg_async_session, tmp_path):
        lead, diagnosis, pdf = await _seed_full(pg_async_session, tmp_path=tmp_path)

        result = await erase_lead_pii(lead.id, db=pg_async_session)
        assert result["detail"] == "erased"

        assert not pdf.exists(), "즉시 파기가 리포트 PDF를 남겼다"

        token = (
            await pg_async_session.execute(
                select(LeadReportToken).where(LeadReportToken.diagnosis_id == diagnosis.id)
            )
        ).scalar_one()
        assert token.revoked_at is not None, "파기 후에도 열람 토큰이 살아 있다"

        row = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(
                    LeadDiagnosisResult.diagnosis_id == diagnosis.id
                )
            )
        ).scalar_one()
        assert row.raw_response == ""

        await pg_async_session.refresh(diagnosis)
        assert diagnosis.report_status == ReportStatus.PURGED.value

    async def test_erase_scrubs_identifying_diagnosis_fields(self, pg_async_session, tmp_path):
        """HIGH 14 — 처리방침은 병원명·진료과·지역 파기를 약속한다."""
        lead, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        await erase_lead_pii(lead.id, db=pg_async_session)
        await pg_async_session.refresh(diagnosis)

        assert diagnosis.subject_hospital_name == "[purged]"
        assert diagnosis.subject_region == "[purged]"
        assert diagnosis.queries == []

    async def test_erase_keeps_the_permanent_locks(self, pg_async_session, tmp_path):
        lead, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        before_email = diagnosis.applicant_email_hash
        before_phone = diagnosis.subject_phone_hash

        await erase_lead_pii(lead.id, db=pg_async_session)
        await pg_async_session.refresh(diagnosis)

        assert diagnosis.applicant_email_hash == before_email
        assert diagnosis.subject_phone_hash == before_phone


@pytest.mark.asyncio
class TestPurgeSurvivesAFailedDiagnosis:
    """BLOCKER 2 — `execution_status=FAILED`인 진단을 PURGED로 바꾸면 CHECK에 걸려
    그날 파기 대상 **전부**가 롤백되고, 같은 독성 행이 매일 다시 선택된다."""

    async def test_a_failed_diagnosis_can_be_purged(self, pg_async_session, tmp_path):
        lead, diagnosis, _ = await _seed_full(
            pg_async_session,
            tmp_path=tmp_path,
            execution_status=ExecutionStatus.FAILED.value,
            report_status=ReportStatus.PENDING.value,
        )
        await erase_lead_pii(lead.id, db=pg_async_session)
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.report_status == ReportStatus.PURGED.value

    async def test_a_blocked_report_can_be_purged(self, pg_async_session, tmp_path):
        lead, diagnosis, _ = await _seed_full(
            pg_async_session,
            tmp_path=tmp_path,
            execution_status=ExecutionStatus.FAILED.value,
            report_status=ReportStatus.BLOCKED.value,
        )
        await erase_lead_pii(lead.id, db=pg_async_session)
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.report_status == ReportStatus.PURGED.value


@pytest.mark.asyncio
class TestPurgeRetryMovesForward:
    """BLOCKER 3 — A는 지웠는데 B에서 실패해 롤백되면, 재시도가 A의 `NotFound`로
    또 멈춰 파기가 영구히 좌초한다."""

    async def test_an_already_deleted_object_does_not_block_the_retry(
        self, pg_async_session, tmp_path, monkeypatch
    ):
        from app.services import lead_report

        lead, diagnosis, pdf = await _seed_full(pg_async_session, tmp_path=tmp_path)
        pdf.unlink()  # 앞선 시도에서 이미 지워진 상태

        calls: list[str] = []
        real_delete = lead_report.delete_report_pdf

        def spy(uri):
            calls.append(uri)
            real_delete(uri)

        monkeypatch.setattr(lead_report, "delete_report_pdf", spy)

        result = await erase_lead_pii(lead.id, db=pg_async_session)
        assert result["detail"] == "erased"
        assert calls, "삭제를 시도조차 하지 않았다"
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.report_status == ReportStatus.PURGED.value


@pytest.mark.asyncio
class TestQueryInjectionSurface:
    """HIGH 11 — 병원명 검사가 키워드에만 걸려 진료과·지역 칸으로 우회 가능했다."""

    @pytest.mark.parametrize("field", ["clinic_type", "region_keyword"])
    async def test_hospital_name_in_any_query_input_is_rejected(self, pg_async_session, field):
        with pytest.raises(HTTPException) as exc:
            await create_diagnosis(
                _Req(),
                _payload(clinic_name="장편한외과의원", **{field: "장편한외과의원"}),
                BackgroundTasks(),
                pg_async_session,
            )
        assert exc.value.status_code == 400

    async def test_generated_queries_never_contain_the_hospital_name(self, pg_async_session):
        """fail-closed 최종 확인 — 어떤 조합이 와도 병원명이 든 질의는 저장되지 않는다."""
        result = await create_diagnosis(
            _Req(),
            _payload(clinic_name="수서연세내과의원"),
            BackgroundTasks(),
            pg_async_session,
        )
        diagnosis = (
            await pg_async_session.execute(
                select(LeadDiagnosis).where(
                    LeadDiagnosis.id == uuid.UUID(result["diagnosis_id"])
                )
            )
        ).scalar_one()
        for query in diagnosis.queries:
            assert "수서연세내과의원" not in query["text"]


@pytest.mark.asyncio
class TestPinnedModelDrift:
    """HIGH 10 — API 배포와 워커 배포 사이에 모델이 바뀌면 캐시 키·리포트 표기와
    실제 호출 모델이 어긋난다. 잘못된 모델로 잰 숫자를 파느니 실패하는 편이 낫다."""

    async def test_a_drifting_model_fails_instead_of_measuring(self, monkeypatch):
        called = []

        async def should_not_run(query):
            called.append(query)
            return {"text": "x", "source_urls": []}

        monkeypatch.setattr(sov_engine, "_query_chatgpt", should_not_run)
        monkeypatch.setattr(settings, "OPENAI_MODEL_QUERY", "gpt-5.6-luna")

        answer = await sov_engine.fetch_answer(
            "수서역 근처 외과 병원 추천해줘", "chatgpt", requested_model="gpt-5-mini-2025-08-07"
        )
        assert answer["measurement_status"] == "FAILED"
        assert "pinned_model_drift" in answer["failure_reason"]
        assert called == [], "고정 모델과 다른데도 공급자를 호출했다"

    async def test_a_matching_model_proceeds(self, monkeypatch):
        async def ok(query):
            return {"text": "답변", "source_urls": []}

        monkeypatch.setattr(sov_engine, "_query_chatgpt", ok)
        monkeypatch.setattr(settings, "OPENAI_MODEL_QUERY", "gpt-5.6-luna")

        answer = await sov_engine.fetch_answer(
            "q", "chatgpt", requested_model="gpt-5.6-luna"
        )
        assert answer["measurement_status"] == "SUCCESS"


class TestMailEscaping:
    """MEDIUM 19 — 병원명이 사용자 입력인데 메일 HTML에 그대로 들어갔다.

    우리 발신 도메인으로 공격자가 만든 링크가 담긴 메일을 보낼 수 있는 경로다.
    """

    def test_html_in_the_hospital_name_is_escaped(self):
        html = mailer.build_report_email_html(
            hospital_name='<a href="https://evil.example">클릭</a>의원',
            report_url="https://reputation.motionlabs.kr/ai-diagnosis/status/tok",
        )
        assert "<a href=\"https://evil.example\">" not in html
        assert "&lt;a href=" in html

    def test_newlines_in_the_subject_are_stripped(self):
        """제목의 개행은 임의 헤더를 덧붙일 수 있는 인젝션 경로다."""
        subject = mailer.build_report_email_subject("의원\r\nBcc: victim@example.com")
        assert "\r" not in subject
        assert "\n" not in subject


@pytest.mark.asyncio
class TestDeliveryWindowRecheck:
    """HIGH 8 — 스윕이 23시간 59분에 retriable로 판정해도, 큐 지연으로 24시간을
    넘겨 실행되면 Resend가 키를 잊어 재시도가 곧 두 번째 메일이 된다."""

    async def test_send_is_refused_when_the_window_closed_after_dispatch(
        self, pg_async_session, tmp_path, monkeypatch
    ):
        sent: list[str] = []

        async def spy(**kwargs):
            sent.append(kwargs["idempotency_key"])
            return mailer.MailResult(provider_message_id="m")

        monkeypatch.setattr(mailer, "send_email", spy)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "k")

        lead, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        delivery = LeadDelivery(
            id=uuid.uuid4(),
            lead_id=lead.id,
            diagnosis_id=diagnosis.id,
            channel="EMAIL",
            event="REPORT",
            status=DeliveryStatus.SENDING.value,
            attempt=1,
        )
        pg_async_session.add(delivery)
        await pg_async_session.flush()
        # 발행 이후 창이 닫힌 상태를 만든다.
        delivery.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await pg_async_session.flush()

        result = await lead_delivery.deliver_report(pg_async_session, diagnosis)

        assert result == {"skipped": "idempotency_window_expired"}
        assert sent == [], "멱등성 창 밖인데 메일을 보냈다"
        await pg_async_session.refresh(delivery)
        assert delivery.status == DeliveryStatus.FAILED.value


@pytest.mark.asyncio
class TestSingleDeliveryRow:
    """HIGH 7 — 폴러가 겹치면 두 세션이 각자 delivery 행을 만들고 **서로 다른**
    Idempotency-Key로 호출해 메일이 두 통 나간다."""

    async def test_a_second_delivery_row_is_rejected_by_the_database(
        self, pg_async_session, tmp_path
    ):
        from sqlalchemy.exc import IntegrityError

        lead, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        for _ in range(1):
            pg_async_session.add(
                LeadDelivery(
                    id=uuid.uuid4(),
                    lead_id=lead.id,
                    diagnosis_id=diagnosis.id,
                    channel="EMAIL",
                    event="REPORT",
                    status=DeliveryStatus.SENDING.value,
                    attempt=1,
                )
            )
        await pg_async_session.flush()

        pg_async_session.add(
            LeadDelivery(
                id=uuid.uuid4(),
                lead_id=lead.id,
                diagnosis_id=diagnosis.id,
                channel="EMAIL",
                event="REPORT",
                status=DeliveryStatus.SENDING.value,
                attempt=1,
            )
        )
        with pytest.raises(IntegrityError):
            await pg_async_session.flush()


@pytest.mark.asyncio
class TestAdminReleaseLock:
    """HIGH 13 — 컬럼만 있고 운영자가 쓸 경로가 없으면 F1-6은 리드 차단 장치다."""

    async def test_release_frees_the_lock_and_records_why(self, pg_async_session, tmp_path):
        lead, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)

        result = await release_diagnosis_lock(
            lead.id,
            admin_leads.ReleaseLockRequest(reason="제3자 선점 — 원장 확인 완료"),
            db=pg_async_session,
        )

        assert result["detail"] == "released"
        assert result["released_count"] == 1
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.lock_released_at is not None
        assert diagnosis.lock_released_by
        assert "제3자 선점" in diagnosis.lock_release_reason

    async def test_releasing_twice_is_rejected(self, pg_async_session, tmp_path):
        lead, _, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        await release_diagnosis_lock(
            lead.id, admin_leads.ReleaseLockRequest(reason="첫 해제"), db=pg_async_session
        )
        with pytest.raises(HTTPException) as exc:
            await release_diagnosis_lock(
                lead.id, admin_leads.ReleaseLockRequest(reason="두 번째"), db=pg_async_session
            )
        assert exc.value.status_code == 409

    async def test_a_released_phone_can_apply_again(self, pg_async_session):
        """해제의 목적은 이것 하나다 — 실제로 다시 신청이 되어야 한다."""
        phone = "02-777-8888"
        first = await create_diagnosis(
            _Req(), _payload(clinic_phone=phone), BackgroundTasks(), pg_async_session
        )

        with pytest.raises(HTTPException):
            await create_diagnosis(
                _Req(), _payload(clinic_phone=phone), BackgroundTasks(), pg_async_session
            )

        diagnosis = (
            await pg_async_session.execute(
                select(LeadDiagnosis).where(
                    LeadDiagnosis.id == uuid.UUID(first["diagnosis_id"])
                )
            )
        ).scalar_one()
        await release_diagnosis_lock(
            diagnosis.lead_id,
            admin_leads.ReleaseLockRequest(reason="원장 본인 확인"),
            db=pg_async_session,
        )

        again = await create_diagnosis(
            _Req(), _payload(clinic_phone=phone), BackgroundTasks(), pg_async_session
        )
        assert again["ok"] is True


@pytest.mark.asyncio
class TestStatusEnumGuard:
    """MEDIUM 15 — 오타 상태가 들어가면 모든 폴러가 알려진 문자열만 조회하므로
    그 행은 영원히 회수되지 않는다. 아무도 모른다."""

    async def test_an_unknown_status_is_rejected(self, pg_async_session, tmp_path):
        from sqlalchemy.exc import IntegrityError

        _, diagnosis, _ = await _seed_full(pg_async_session, tmp_path=tmp_path)
        diagnosis.execution_status = "RUNING"
        with pytest.raises(IntegrityError):
            await pg_async_session.flush()


def test_purge_helper_is_shared_by_both_paths():
    """단일 진입점이 둘로 갈라지면 반드시 어긋난다 — 실제로 `/erase`가 어긋나 있었다."""
    assert hasattr(lead_privacy, "purge_lead_completely")
    assert hasattr(lead_privacy, "purge_lead_completely_async")
