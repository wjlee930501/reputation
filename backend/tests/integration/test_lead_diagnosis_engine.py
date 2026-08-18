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
from app.services import (
    cost_guard,
    lead_diagnosis_engine,
    lead_query_cache,
    sov_engine,
)
from app.services.query_mapper import build_lead_diagnosis_queries

_slot_sequence = itertools.count(1)


async def _noop_alert(**_kwargs):
    return None


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


def _verdict_for(mentioned: bool) -> dict:
    return {
        "verdict": "MATCHED" if mentioned else "NOT_MATCHED",
        "is_mentioned": mentioned,
        "matched_text": None,
        "mention_rank": 1 if mentioned else None,
        "sentiment": "neutral",
        "mention_context": None,
    }


async def _judge_matched(hospital_name, response_text, region=""):
    return _verdict_for(hospital_name in response_text)


async def _judge_ambiguous(hospital_name, response_text, region=""):
    """동명 기관 가능성으로 확정하지 못한 판정 — 분모에서 빠진다."""
    return {
        "verdict": "AMBIGUOUS",
        "is_mentioned": None,
        "matched_text": None,
        "mention_rank": None,
        "sentiment": None,
        "mention_context": None,
    }


@pytest.fixture
def spy(monkeypatch):
    spy = _ProviderSpy()
    monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
    monkeypatch.setattr(sov_engine, "judge_mention", _judge_matched)
    return spy


@pytest.fixture(autouse=True)
def allow_leadgen_budget_by_default(monkeypatch):
    """이 파일은 측정 엔진·공유 캐시 계약을 검증한다.

    실제 Redis 비용 가드 상태가 남아 있으면 로컬/CI 환경의 사용량에 따라 측정이 시작 전
    차단되고, provider spy나 캐시 행이 전혀 만들어지지 않는다. 비용 가드 자체는 별도
    테스트가 담당하므로 여기서는 기본 허용으로 고정하고, 예산 예약/차단을 보는 테스트만
    각자 `_GuardSpy`로 덮어쓴다.
    """

    async def _allow(category, *, count=1, redis_client=None):
        return cost_guard.CostGuardDecision(True, None)

    monkeypatch.setattr(cost_guard, "check_and_increment", _allow)


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


class _GuardSpy:
    """비용 가드 예약을 세고 정해진 결정을 돌려준다."""

    def __init__(self, *, allowed=True, reason=None):
        self.reservations: list[tuple[str, int]] = []
        self.allowed = allowed
        self.reason = reason

    async def check_and_increment(self, category, *, count=1, redis_client=None):
        self.reservations.append((category, count))
        return cost_guard.CostGuardDecision(self.allowed, self.reason)

    @property
    def leadgen_count(self):
        return sum(count for category, count in self.reservations if category == "leadgen")


@pytest.mark.asyncio
class TestCallBudget:
    """**선착순 자리 수는 호출 상한이 아니다.**

    자리 20개는 접수를 20건으로 묶지만, 측정 재시도까지 겹치면 하루 공급자 호출은
    1,000건을 넘을 수 있다. 자리 카운터는 그것을 세지 않는다.
    """

    async def test_reservation_counts_the_provider_calls_not_the_diagnosis(
        self, pg_async_session, spy, monkeypatch
    ):
        guard = _GuardSpy()
        monkeypatch.setattr(cost_guard, "check_and_increment", guard.check_and_increment)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)

        # 진단 1건이 아니라 답변 호출 18건을 예약해야 한다.
        assert guard.leadgen_count == 18

    async def test_cache_hits_are_not_charged(self, pg_async_session, spy, monkeypatch):
        """캐시에서 온 답변은 돈을 쓰지 않는다 — 예산에도 그렇게 반영돼야 한다.

        그러지 않으면 공유 캐시가 원가를 줄여도 상한은 그대로 닫혀, 절감이 자리 수를
        늘리는 데 전혀 쓰이지 못한다.
        """
        first = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, first)

        guard = _GuardSpy()
        monkeypatch.setattr(cost_guard, "check_and_increment", guard.check_and_increment)
        second = await _seed_diagnosis(pg_async_session, hospital_name="같은질의의원")
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, second)

        assert guard.leadgen_count == 0

    async def test_blocked_budget_stops_before_any_provider_call(
        self, pg_async_session, spy, monkeypatch
    ):
        """차단은 호출 **전에** 일어나야 한다 — 후에 막으면 돈은 이미 나갔다."""
        guard = _GuardSpy(allowed=False, reason="일일 호출 상한(500건)에 도달했습니다.")
        monkeypatch.setattr(cost_guard, "check_and_increment", guard.check_and_increment)
        alerts: list[dict] = []

        async def _capture(**kwargs):
            alerts.append(kwargs)

        monkeypatch.setattr(lead_diagnosis_engine, "open_ops_incident", _capture)

        diagnosis = await _seed_diagnosis(pg_async_session)
        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, diagnosis
        )

        assert spy.calls == []
        assert result["blocked"] == "cost_guard"
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value
        assert "예산" in (diagnosis.error or "")
        # 조용히 실패하면 신청자는 리포트를 못 받고 아무도 이유를 모른다.
        assert len(alerts) == 1
        assert alerts[0].get("notify", True) is True

    async def test_blocked_budget_writes_no_measurement_rows(
        self, pg_async_session, spy, monkeypatch
    ):
        """0건 측정을 행으로 남기면 리포트가 분모 0으로 만들어질 여지가 생긴다."""
        guard = _GuardSpy(allowed=False, reason="상한 도달")
        monkeypatch.setattr(cost_guard, "check_and_increment", guard.check_and_increment)
        monkeypatch.setattr(lead_diagnosis_engine, "open_ops_incident", _noop_alert)

        diagnosis = await _seed_diagnosis(pg_async_session)
        await lead_diagnosis_engine.run_diagnosis_measurements(pg_async_session, diagnosis)

        rows = int(
            await pg_async_session.scalar(
                select(func.count())
                .select_from(LeadDiagnosisResult)
                .where(LeadDiagnosisResult.diagnosis_id == diagnosis.id)
            )
        )
        assert rows == 0

    async def test_leadgen_budget_is_separate_from_the_operations_sov_budget(self):
        """1단 폭주가 계약 병원의 월간 측정을 차단하면 안 된다(설계 §0의 두 단 분리)."""
        assert "leadgen" in cost_guard.CATEGORIES
        assert "sov" in cost_guard.CATEGORIES
        leadgen_limits = cost_guard._limits("leadgen")
        sov_limits = cost_guard._limits("sov")
        assert leadgen_limits != sov_limits


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

    async def test_one_missing_measurement_per_platform_is_still_partial(
        self, pg_async_session, monkeypatch
    ):
        """플랫폼당 결측 1건까지는 리포트를 만든다 — 하한(8/9)이 허용하는 범위다."""
        failed_by_platform: dict[str, int] = {}

        async def fetch(query_text, platform, *, pool=None, requested_model=None):
            if failed_by_platform.get(platform, 0) < 1:
                failed_by_platform[platform] = failed_by_platform.get(platform, 0) + 1
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

    async def test_scattered_failures_below_the_floor_are_failed(
        self, pg_async_session, monkeypatch
    ):
        """결측이 하한을 넘으면 FAILED다.

        예전에는 플랫폼당 성공 1건만 있어도 PARTIAL이라 리포트가 나갔다. 실패는 분모에서
        빠지므로, 18건 중 2건만 성공하고 그 2건에 병원명이 나오면 **100%** 가 인쇄된다.
        결측이 그대로 '성과'가 되는 구조였다.
        """
        flaky = itertools.count()

        async def fetch(query_text, platform, *, pool=None, requested_model=None):
            # 3번째 호출마다 실패 — 플랫폼당 3건씩 빠져 하한(8/9) 미달.
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
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value
        assert diagnosis.error

    async def test_ambiguous_verdicts_do_not_count_as_confirmed(
        self, pg_async_session, monkeypatch
    ):
        """판정 보류는 측정 성공이지만 분모가 아니다.

        전부 보류면 확정 0건이므로 리포트를 만들 수 없다 — 분모 없는 비율을 인쇄하느니
        측정 불충분으로 막는다.
        """
        spy = _ProviderSpy()
        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)
        monkeypatch.setattr(sov_engine, "judge_mention", _judge_ambiguous)

        diagnosis = await _seed_diagnosis(pg_async_session)
        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, diagnosis
        )

        assert result["succeeded"] == 18      # 답변 수신·판정 수행은 전부 성공
        assert result["confirmed"] == 0       # 그러나 확정된 판정은 0건
        assert result["ambiguous"] == 18
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value

        rows = (
            await pg_async_session.execute(
                select(LeadDiagnosisResult).where(
                    LeadDiagnosisResult.diagnosis_id == diagnosis.id
                )
            )
        ).scalars().all()
        # 보류를 False로 접으면 조용한 하향 편향이 된다. None이어야 한다.
        assert all(r.is_mentioned is None for r in rows)
        assert all(r.mention_verdict == "AMBIGUOUS" for r in rows)

    async def test_policy_drift_blocks_measurement_before_any_provider_call(
        self, pg_async_session, spy
    ):
        """접수 시점 정책과 실행 시점 정책이 다르면 재지 않는다 — 모델 핀과 같은 규약.

        리포트가 공개하는 조건은 접수 스냅샷이므로, 다른 조건으로 잰 숫자를 그 스냅샷
        아래 인쇄하면 재현성 계약이 깨진다.
        """
        diagnosis = await _seed_diagnosis(pg_async_session)
        diagnosis.measurement_config = {
            **sov_engine.measurement_protocol(),
            "policy_version": "v1",
            "openai_tool_choice": "required",
        }
        await pg_async_session.flush()

        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, diagnosis
        )

        assert result["blocked"] == "policy_drift"
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value
        assert "측정 정책 불일치" in (diagnosis.error or "")
        assert spy.calls == []   # 돈을 쓰기 전에 막았다

    async def test_matching_policy_snapshot_measures_normally(self, pg_async_session, spy):
        diagnosis = await _seed_diagnosis(pg_async_session)
        diagnosis.measurement_config = sov_engine.measurement_protocol()
        await pg_async_session.flush()

        result = await lead_diagnosis_engine.run_diagnosis_measurements(
            pg_async_session, diagnosis
        )

        assert result["status"] == ExecutionStatus.SUCCEEDED.value

    async def test_judge_failure_is_not_recorded_as_a_zero_mention(
        self, pg_async_session, monkeypatch
    ):
        """판정 실패를 '미언급'으로 접으면 도구 장애가 병원 성과처럼 보인다."""
        spy = _ProviderSpy()
        monkeypatch.setattr(sov_engine, "fetch_answer", spy.fetch_answer)

        async def broken_judge(hospital_name, response_text, region=""):
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

        async def judge(hospital_name, response_text, region=""):
            judged.append(hospital_name)
            return _verdict_for(hospital_name in response_text)

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
