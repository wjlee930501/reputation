"""무료 진단 접수 API — 실제 Postgres로 검증한다 (설계 T-5 · T-6 · T-10 · T-11).

접수의 핵심 동작 셋(자리 배정 · 이중 잠금 · 고아 리드 방지)은 전부 DB 제약이 걸린
뒤에야 관측된다. fake DB로는 제약이 한 번도 실행되지 않아, 잠금 인덱스를 통째로
지워도 테스트가 초록으로 남는다.
"""
import itertools
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.public import diagnosis as diagnosis_api
from app.core.config import settings
from app.models.lead import LEAD_SOURCE_AI_DIAGNOSIS, SalesLead
from app.models.lead_diagnosis import ExecutionStatus, LeadDiagnosis, LeadReportToken
from app.services.lead_report_token import hash_report_token

# slowapi @limiter.limit 우회 — 라우트를 FastAPI 요청 라이프사이클 밖에서 직접 호출한다.
create_diagnosis = diagnosis_api.create_diagnosis.__wrapped__
get_slot_availability = diagnosis_api.get_slot_availability


class FakeRequest:
    def __init__(self, ip: str = "127.0.0.1"):
        self.headers = SimpleNamespace(get=lambda key, default=None: default)
        self.client = SimpleNamespace(host=ip)


# 전화번호는 숫자여야 잠금 키가 만들어진다 — hex를 쓰면 형식 검증에 걸린다.
_phone_sequence = itertools.count(1)


def _payload(**overrides):
    unique = uuid.uuid4().hex[:8]
    serial = next(_phone_sequence)
    base = dict(
        clinic_name=f"장편한외과의원{unique}",
        clinic_type="외과",
        region_keyword="수서역",
        clinic_phone=f"02-{serial // 10000 % 1000:03d}-{serial % 10000:04d}",
        core_keywords=["대장내시경", "치질"],
        contact_name="홍길동",
        contact="010-1234-5678",
        email=f"{unique}@example.com",
        privacy=True,
        source_path="/ai-diagnosis",
    )
    base.update(overrides)
    return diagnosis_api.DiagnosisRequest(**base)


async def _post(session, **overrides):
    return await create_diagnosis(FakeRequest(), _payload(**overrides), session)


@pytest.mark.asyncio
class TestHappyPath:
    async def test_accepted_application_creates_lead_diagnosis_and_token(self, pg_async_session):
        result = await _post(pg_async_session)

        assert result["ok"] is True
        assert result["slot_no"] == 1
        assert result["query_count"] == 3

        diagnosis = (
            await pg_async_session.execute(
                select(LeadDiagnosis).where(LeadDiagnosis.id == uuid.UUID(result["diagnosis_id"]))
            )
        ).scalar_one()
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value
        assert len(diagnosis.queries) == 3
        assert diagnosis.repeat_count == settings.LEADGEN_REPEAT_COUNT

        lead = (
            await pg_async_session.execute(
                select(SalesLead).where(SalesLead.id == diagnosis.lead_id)
            )
        ).scalar_one()
        assert lead.source == LEAD_SOURCE_AI_DIAGNOSIS
        assert lead.question is None
        # 동의 버전은 클라이언트가 아니라 서버 ENV에서 온다.
        assert lead.consent_version == settings.LEAD_CONSENT_VERSION

    async def test_status_url_token_is_stored_only_as_a_hash(self, pg_async_session):
        """DB가 유출돼도 열람 토큰 원문이 나오면 안 된다."""
        result = await _post(pg_async_session)
        raw = result["status_url"].rsplit("/", 1)[-1]

        stored = (
            await pg_async_session.execute(
                select(LeadReportToken).where(
                    LeadReportToken.diagnosis_id == uuid.UUID(result["diagnosis_id"])
                )
            )
        ).scalar_one()
        assert stored.token_hash != raw
        assert stored.token_hash == hash_report_token(raw)
        assert stored.revoked_at is None

    async def test_hospital_name_never_appears_in_the_generated_queries(self, pg_async_session):
        """자기 이름을 물으면 언급은 보장되고 측정은 무의미해진다 (PRD F1-1)."""
        result = await _post(pg_async_session, clinic_name="수서연세내과의원")
        diagnosis = (
            await pg_async_session.execute(
                select(LeadDiagnosis).where(LeadDiagnosis.id == uuid.UUID(result["diagnosis_id"]))
            )
        ).scalar_one()
        for query in diagnosis.queries:
            assert "수서연세내과의원" not in query["text"]


@pytest.mark.asyncio
class TestDualLock:
    async def test_second_application_with_the_same_email_is_rejected(self, pg_async_session):
        email = f"{uuid.uuid4().hex[:8]}@example.com"
        await _post(pg_async_session, email=email)
        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, email=email)
        assert exc.value.status_code == 409

    async def test_second_application_with_the_same_phone_is_rejected(self, pg_async_session):
        phone = "02-987-6543"
        await _post(pg_async_session, clinic_phone=phone)
        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, clinic_phone=phone)
        assert exc.value.status_code == 409

    async def test_reformatted_phone_number_does_not_unlock(self, pg_async_session):
        """표기만 바꾼 재신청이 통하면 잠금이 무의미해진다."""
        await _post(pg_async_session, clinic_phone="02-987-6543")
        for variant in ("0298 76543", "+82 2 987 6543", "(02)987-6543"):
            with pytest.raises(HTTPException) as exc:
                await _post(pg_async_session, clinic_phone=variant)
            assert exc.value.status_code == 409, variant

    async def test_plus_tagged_email_does_not_unlock(self, pg_async_session):
        local = uuid.uuid4().hex[:8]
        await _post(pg_async_session, email=f"{local}@example.com")
        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, email=f"{local}+2@example.com")
        assert exc.value.status_code == 409

    async def test_rejection_does_not_leak_the_existing_status_url(self, pg_async_session):
        """남의 병원 대표번호를 아는 사람이 재신청으로 그 병원 리포트 링크를 얻으면 안 된다."""
        phone = "02-111-2222"
        first = await _post(pg_async_session, clinic_phone=phone)
        raw = first["status_url"].rsplit("/", 1)[-1]

        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, clinic_phone=phone)
        assert raw not in str(exc.value.detail)
        assert "status_url" not in str(exc.value.detail)

    async def test_rejected_application_leaves_no_orphan_lead(self, pg_async_session):
        """리드만 남고 진단이 없으면 Admin 리드 목록이 유령 행으로 오염된다."""
        email = f"{uuid.uuid4().hex[:8]}@example.com"
        await _post(pg_async_session, email=email)
        before = int(
            await pg_async_session.scalar(select(func.count()).select_from(SalesLead)) or 0
        )
        with pytest.raises(HTTPException):
            await _post(pg_async_session, email=email)
        after = int(
            await pg_async_session.scalar(select(func.count()).select_from(SalesLead)) or 0
        )
        assert after == before


@pytest.mark.asyncio
class TestDailySlots:
    async def test_slot_numbers_increase_and_remaining_decreases(self, pg_async_session):
        for expected_slot in (1, 2, 3):
            result = await _post(pg_async_session)
            assert result["slot_no"] == expected_slot
            assert result["remaining_slots"] == settings.LEADGEN_DAILY_SLOTS - expected_slot

    async def test_application_is_refused_once_the_day_is_full(
        self, pg_async_session, monkeypatch
    ):
        """다 차면 대기열에 쌓지 않고 거부한다 — 선착순 마감이 곧 희소성 메시지다."""
        monkeypatch.setattr(settings, "LEADGEN_DAILY_SLOTS", 2)
        await _post(pg_async_session)
        await _post(pg_async_session)

        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session)
        assert exc.value.status_code == 429
        assert "내일" in exc.value.detail

    async def test_slot_endpoint_reports_the_real_count(self, pg_async_session, monkeypatch):
        """랜딩의 "남은 자리"는 실제 카운터다 — 희소성을 연출하려고 꾸미지 않는다."""
        monkeypatch.setattr(settings, "LEADGEN_DAILY_SLOTS", 5)
        before = await get_slot_availability(pg_async_session)
        assert before["used"] == 0
        assert before["remaining"] == 5

        await _post(pg_async_session)

        after = await get_slot_availability(pg_async_session)
        assert after["used"] == 1
        assert after["remaining"] == 4
        assert after["total"] == 5


@pytest.mark.asyncio
class TestAbuseDefenses:
    async def test_honeypot_returns_success_without_storing_anything(self, pg_async_session):
        before = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadDiagnosis)) or 0
        )
        result = await create_diagnosis(
            FakeRequest(), _payload(website="http://spam.example"), pg_async_session
        )
        assert result["ok"] is True
        assert result["diagnosis_id"] is None
        after = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadDiagnosis)) or 0
        )
        assert after == before

    async def test_keyword_containing_the_hospital_name_is_rejected(self, pg_async_session):
        """키워드 칸으로 병원명을 질의에 실어 언급을 유도하는 우회 (PRD F1-4)."""
        with pytest.raises(HTTPException) as exc:
            await _post(
                pg_async_session,
                clinic_name="장편한외과의원",
                core_keywords=["장편한외과의원 대장내시경"],
            )
        assert exc.value.status_code == 400

    async def test_spacing_variants_of_the_hospital_name_are_also_rejected(self, pg_async_session):
        with pytest.raises(HTTPException):
            await _post(
                pg_async_session,
                clinic_name="장편한 외과의원",
                core_keywords=["장편한외과의원"],
            )

    async def test_missing_privacy_consent_is_rejected(self, pg_async_session):
        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, privacy=False)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("bad_phone", ["없음", "1234", "abc"])
    async def test_unusable_phone_numbers_are_rejected(self, pg_async_session, bad_phone):
        """대표번호가 잠금의 병원 측 키다 — 형식이 깨지면 잠금 자체가 성립하지 않는다."""
        with pytest.raises(HTTPException) as exc:
            await _post(pg_async_session, clinic_phone=bad_phone)
        assert exc.value.status_code == 400

    async def test_patient_sensitive_text_is_rejected(self, pg_async_session):
        with pytest.raises(Exception):
            await _post(pg_async_session, contact_name="환자 홍길동 900101-1234567")
