"""무료 진단 측정 실행 + 질의 공유 캐시 (설계 T-2 · T-14 · T-15 · T-16).

공급자 호출만 가짜로 바꾸고 **DB는 실제 Postgres**를 쓴다. 캐시의 값어치는 전부
"두 번째 병원이 공급자를 부르지 않는다"에 있는데, 그건 실제 조회·삽입이 돌아야
관측된다.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    AnswerSource,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
    LeadQueryAnswer,
)
from app.services import lead_diagnosis_engine, lead_query_cache, sov_engine
from app.services.query_mapper import build_lead_diagnosis_queries

_slot_sequence = itertools.count(1)


class _ProviderSpy:
    """공급자 호출을 세고 정해진 답변을 돌려준다."""

    def __init__(self, *, text="수서역 근처에는 장편한외과의원이 있습니다.", fail_platforms=()):
        self.calls: list[tuple[str, str]] = []
        self.text = text
        self.fail_platforms = set(fail_platforms)

    async def fetch_answer(self, query_text, platform, *, pool=None, requested_model=None):
        self.calls.append((platform, query_text))
        if platform in self.fail_platforms:
            return {
                "text": "",
                "source_urls": [],
                "measurement_status": "FAILED",
                "failure_reason": "provider_query_failed:TimeoutError",
            }
        return {
            "text": self.text,
            "source_urls": ["https://example.com/a"],
            "answer_model": f"{platform}-model-x",
            "measurement_status": "SUCCESS",
            "failure_reason": None,
        }

    def count_for(self, platform):
        return sum(1 for p, _ in self.calls if p == platform)


async def _judge_matched(hospital_name, response_text):
    return {
        "is_mentioned": hospital_name in response_text,
        "mention_rank": 1,
        "sentiment": "neutral",
        "mention_context": None,
    }


@pytest.fixture
def spy(monkeypatch):
    spy = _ProviderSpy()
    monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
    monkeypatch.setattr(sov_engine, "judge_mention", _judge_matched)
    return spy


async def _seed_diagnosis(
    session, *, hospital_name="장편한외과의원", region="수서역", specialty="외과",
    keywords=("대장내시경", "치질"),
):
    lead = SalesLead(
        clinic_name=hospital_name,
        clinic_type=specialty,
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
        subject_hospital_name=hospital_name,
        subject_region=region,
        slot_date=date(2026, 8, 10),
        slot_no=next(_slot_sequence),
        queries=build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=list(keywords)
        ),
        requested_models={
            "openai": settings.OPENAI_MODEL_QUERY,
            "gemini": settings.GEMINI_MODEL,
            "judge": settings.OPENAI_MODEL_PARSE,
        },
        repeat_count=settings.LEADGEN_REPEAT_COUNT,
        execution_status=ExecutionStatus.RUNNING.value,
        execution_attempts=1,
    )
    session.add(diagnosis)
    await session.flush()
    return diagnosis


@pytest.mark.asyncio
class TestMeasurementVolume:
    async def test_a_diagnosis_is_always_eighteen_measurements(self, pg_async_session, spy):
        """질의 3 × 플랫폼 2 × 반복 3 = 18. 고정이어야 원가와 SLA가 계산 가능하다."""
        diagnosis = await _seed_diagnosis(pg_async_session)
        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, diagnosis
        )

        assert result["planned"] == 18
        assert result["succeeded"] == 18
        assert len(spy.calls) == 18

        rows = int(
            await pg_async_session.scalar(
                select(func.count())
                .select_from(LeadDiagnosisResult)
                .where(LeadDiagnosisResult.diagnosis_id == diagnosis.id)
            )
        )
        assert rows == 18

    async def test_both_platforms_get_the_same_queries(self, pg_async_session, spy):
        """플랫폼마다 다른 질의를 던지면 플랫폼 비교가 성립하지 않는다."""
        await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, await _seed_diagnosis(pg_async_session)
        )
        chatgpt = {q for p, q in spy.calls if p == "chatgpt"}
        gemini = {q for p, q in spy.calls if p == "gemini"}
        assert chatgpt == gemini


@pytest.mark.asyncio
class TestExecutionStatus:
    async def test_all_success_is_succeeded(self, pg_async_session, spy):
        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.SUCCEEDED.value

    async def test_one_dead_platform_is_failed_not_partial(
        self, pg_async_session, monkeypatch
    ):
        """어느 한 플랫폼이라도 성공 0이면 FAILED다 (설계 §4-4).

        PARTIAL로 접으면 분모가 0인 플랫폼 칸을 가진 리포트가 만들어져 원장에게 간다.
        PRD F3-5는 플랫폼별 분모 표기를 요구하므로 그 리포트는 인쇄할 수 없다.
        """
        spy = _ProviderSpy(fail_platforms={"gemini"})
        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
        monkeypatch.setattr(sov_engine, "judge_mention", _judge_matched)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)

        assert diagnosis.execution_status == ExecutionStatus.FAILED.value
        assert diagnosis.error

    async def test_scattered_failures_with_both_platforms_alive_is_partial(
        self, pg_async_session, monkeypatch
    ):
        """반복 일부가 실패해도 두 플랫폼에 데이터가 있으면 리포트를 만들 수 있다."""
        flaky = itertools.count()

        async def fetch(query_text, platform, *, pool=None, requested_model=None):
            # 3번째 호출마다 실패 — 두 플랫폼 모두 성공이 남는다.
            if next(flaky) % 3 == 0:
                return {
                    "text": "",
                    "source_urls": [],
                    "measurement_status": "FAILED",
                    "failure_reason": "empty_raw_response",
                }
            return {
                "text": "장편한외과의원이 있습니다.",
                "source_urls": [],
                "answer_model": "m",
                "measurement_status": "SUCCESS",
                "failure_reason": None,
            }

        monkeypatch.setattr(sov_engine, "fetch_answer", fetch)
        monkeypatch.setattr(sov_engine, "judge_mention", _judge_matched)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.PARTIAL.value

    async def test_judge_failure_is_not_recorded_as_a_zero_mention(
        self, pg_async_session, monkeypatch
    ):
        """판정 실패를 '미언급'으로 접으면 도구 장애가 병원 성과처럼 보인다."""
        spy = _ProviderSpy()
        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)

        async def broken_judge(hospital_name, response_text):
            raise RuntimeError("judge model down")

        monkeypatch.setattr(sov_engine, "judge_mention", broken_judge)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)

        rows = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(
                    LeadDiagnosisResult.diagnosis_id == diagnosis.id
                )
            )
        ).scalars().all()
        assert all(r.measurement_status == "FAILED" for r in rows)
        assert all(r.is_mentioned is None for r in rows)
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value


@pytest.mark.asyncio
class TestSharedQueryCache:
    async def test_second_hospital_with_the_same_queries_calls_no_provider(
        self, pg_async_session, spy
    ):
        """캐시의 값어치 전부가 여기 있다 — 1,499원이 약 5원이 된다 (설계 §2-6)."""
        first = await _seed_diagnosis(pg_async_session, hospital_name="가나의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        assert len(spy.calls) == 18

        spy.calls.clear()

        # 같은 지역·진료과·키워드 → 같은 질의. 병원명만 다르다(질의에 안 들어간다).
        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, second
        )

        assert spy.calls == []
        assert result["cached"] == 18
        assert second.execution_status == ExecutionStatus.SUCCEEDED.value

    async def test_cached_results_keep_the_original_measurement_time(
        self, pg_async_session, spy
    ):
        """7일 전 답변을 오늘 측정한 것처럼 팔지 않는다 (설계 T-15)."""
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)

        # 캐시의 측정 시각을 과거로 밀어둔다.
        past = datetime.now(timezone.utc) - timedelta(days=3)
        for answer in (
            await pg_async_session.execute(select(LeadQueryAnswer))
        ).scalars().all():
            answer.measured_at = past
        await pg_async_session.flush()

        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        rows = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(LeadDiagnosisResult.diagnosis_id == second.id)
            )
        ).scalars().all()
        assert rows
        for row in rows:
            assert row.answer_source == AnswerSource.CACHED.value
            assert abs((row.measured_at - past).total_seconds()) < 1

    async def test_judging_is_redone_for_every_hospital(self, pg_async_session, monkeypatch):
        """답변은 병원과 무관하지만 판정은 아니다 — 캐시가 판정까지 재사용하면 안 된다."""
        spy = _ProviderSpy(text="수서역에는 가나의원이 있습니다.")
        judged: list[str] = []

        async def judge(hospital_name, response_text):
            judged.append(hospital_name)
            return {
                "is_mentioned": hospital_name in response_text,
                "mention_rank": None,
                "sentiment": None,
                "mention_context": None,
            }

        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
        monkeypatch.setattr(sov_engine, "judge_mention", judge)

        first = await _seed_diagnosis(pg_async_session, hospital_name="가나의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        assert judged.count("가나의원") == 18
        assert judged.count("다라의원") == 18

        # 같은 답변인데 판정 결과가 갈린다 — 캐시가 판정을 오염시키지 않았다는 증거.
        first_rows = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(LeadDiagnosisResult.diagnosis_id == first.id)
            )
        ).scalars().all()
        second_rows = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(LeadDiagnosisResult.diagnosis_id == second.id)
            )
        ).scalars().all()
        assert all(r.is_mentioned for r in first_rows)
        assert not any(r.is_mentioned for r in second_rows)

    async def test_a_different_region_does_not_hit_the_cache(self, pg_async_session, spy):
        """'수서역'과 '성수동'을 같은 키로 묶으면 틀린 숫자를 팔게 된다."""
        first = await _seed_diagnosis(pg_async_session, region="수서역")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        spy.calls.clear()

        second = await _seed_diagnosis(pg_async_session, region="성수동")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)
        assert len(spy.calls) == 18

    async def test_changing_the_answer_model_invalidates_the_cache(
        self, pg_async_session, spy, monkeypatch
    ):
        """모델을 바꿔도 옛 답변이 살아남으면 §2-1의 핀 고정이 캐시 뒤에서 깨진다 (T-14)."""
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        spy.calls.clear()

        monkeypatch.setattr(settings, "OPENAI_MODEL_QUERY", "gpt-5.7-nova")
        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        # OpenAI 쪽만 다시 측정된다 — Gemini 모델은 그대로이므로 캐시가 유효하다.
        assert spy.count_for("chatgpt") == 9
        assert spy.count_for("gemini") == 0

    async def test_changing_the_system_prompt_invalidates_the_cache(
        self, pg_async_session, spy, monkeypatch
    ):
        """프롬프트를 빼면 잡음률이 27%→74%로 무너진다(PRD §1-2) — 조건이 다르면 다른 측정이다."""
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        spy.calls.clear()

        monkeypatch.setattr(sov_engine, "SYSTEM_PROMPT_SOV", "완전히 다른 지시문입니다.")
        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)
        assert len(spy.calls) == 18

    async def test_expired_answers_are_not_reused(self, pg_async_session, spy):
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)

        for answer in (
            await pg_async_session.execute(select(LeadQueryAnswer))
        ).scalars().all():
            answer.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await pg_async_session.flush()
        spy.calls.clear()

        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)
        assert len(spy.calls) == 18

    async def test_failed_measurements_are_never_cached(self, pg_async_session, monkeypatch):
        """실패를 캐시하면 한 번의 공급자 장애가 7일 동안 모든 신청자에게 전파된다."""
        spy = _ProviderSpy(fail_platforms={"chatgpt", "gemini"})
        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
        monkeypatch.setattr(sov_engine, "judge_mention", _judge_matched)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)

        cached = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadQueryAnswer))
        )
        assert cached == 0

    async def test_expired_entries_are_purged(self, pg_async_session, spy):
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        for answer in (
            await pg_async_session.execute(select(LeadQueryAnswer))
        ).scalars().all():
            answer.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await pg_async_session.flush()

        removed = await lead_query_cache.purge_expired(pg_async_session)
        assert removed > 0
        remaining = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadQueryAnswer))
        )
        assert remaining == 0


@pytest.mark.asyncio
class TestCacheConflictIsolation:
    """캐시 경합이 **측정 결과를 파괴하지 않아야** 한다.

    캐시 적재는 측정 결과 18행과 같은 트랜잭션에서 일어난다. 충돌 시 세션 전체를
    rollback하면 그 18행이 통째로 사라진다 — 그리고 이 경합은 드문 사고가 아니라
    **공유 캐시가 정확히 유도하는 상황**이다. 두 병원이 같은 질의를 동시에 측정하면
    반드시 한쪽이 진다.
    """

    async def test_a_cache_collision_does_not_discard_the_measurements(
        self, pg_async_session, spy, monkeypatch
    ):
        first = await _seed_diagnosis(pg_async_session, hospital_name="가나의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)

        # 조회는 비었는데 저장은 충돌하는 상태 = 두 진단이 동시에 같은 질의를 측정한 상황.
        async def empty_cache(*args, **kwargs):
            return {}

        monkeypatch.setattr(lead_query_cache, "get_cached_answers", empty_cache)

        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        rows = int(
            await pg_async_session.scalar(
                select(func.count())
                .select_from(LeadDiagnosisResult)
                .where(LeadDiagnosisResult.diagnosis_id == second.id)
            )
        )
        assert rows == 18, "캐시 충돌이 측정 결과를 삼켰다"
        assert second.execution_status == ExecutionStatus.SUCCEEDED.value

    async def test_the_losing_writer_leaves_exactly_one_cache_row(
        self, pg_async_session, spy, monkeypatch
    ):
        """경합에서 진 쪽이 물러나되, 이긴 쪽의 캐시는 그대로 남아야 한다."""
        first = await _seed_diagnosis(pg_async_session, hospital_name="가나의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)
        before = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadQueryAnswer))
        )

        async def empty_cache(*args, **kwargs):
            return {}

        monkeypatch.setattr(lead_query_cache, "get_cached_answers", empty_cache)
        second = await _seed_diagnosis(pg_async_session, hospital_name="다라의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        after = int(
            await pg_async_session.scalar(select(func.count()).select_from(LeadQueryAnswer))
        )
        assert after == before, "충돌한 캐시 행이 중복 저장됐다"
