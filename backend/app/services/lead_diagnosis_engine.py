"""무료 진단 측정 실행 (설계 §4-4 · §2-6).

한 진단 = 질의 3개 × 플랫폼 2개 × 반복 3회 = **18 측정**으로 고정이다.
고정이어야 원가(§6)와 SLA(§7)가 계산 가능하다.

세 단계로 나눈다. 네트워크 단계에서 DB 세션을 건드리지 않기 위해서다 —
AsyncSession은 동시 사용이 안전하지 않은데, 측정은 동시에 던져야 15분 안에 끝난다.

  1. 읽기   캐시 조회 (DB, 순차)
  2. 측정   캐시 미적중분 공급자 호출 + 전 건 판정 (네트워크, 동시)
  3. 쓰기   결과 행 + 캐시 적재 + 상태 확정 (DB, 단일 커밋)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead_diagnosis import (
    AnswerSource,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
    MentionVerdict,
)
from app.services import cost_guard, lead_query_cache, notifier, sov_engine

logger = logging.getLogger(__name__)

# 서로 다른 공급자 경로를 동일 질문·동일 반복 수로 교차 관찰한다. 소비자 앱 시장
# 점유율이나 개인화 화면을 재현한다는 뜻이 아니다(PRD §2).
PLATFORMS: tuple[str, ...] = ("chatgpt", "gemini")

MEASUREMENT_SUCCESS = "SUCCESS"
MEASUREMENT_FAILED = "FAILED"

# 플랫폼당 허용하는 미확정(실패 + 판정 보류) 최대 건수. 계획 9건 기준 하한 8건.
# 이 값을 올리면 결측이 숫자를 흔드는 폭이 커진다 — resolve_execution_status 참고.
MAX_UNCONFIRMED_PER_PLATFORM = 1


def min_confirmed_for(planned_count: int) -> int:
    """계획 건수에 대한 확정 하한. 계획이 1건뿐이면 하한도 1이다(0으로 내리지 않는다)."""
    return max(1, planned_count - MAX_UNCONFIRMED_PER_PLATFORM)


@dataclass
class _Measurement:
    """측정 1회의 계획과 결과."""

    platform: str
    query_slot: int
    query_text: str
    repeat_no: int
    requested_model: str

    answer_source: str = AnswerSource.LIVE.value
    measured_at: datetime | None = None
    raw_response: str = ""
    answer_model: str | None = None
    source_urls: list = field(default_factory=list)
    search_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    is_mentioned: bool | None = None
    mention_verdict: str | None = None
    measurement_status: str = MEASUREMENT_FAILED
    failure_reason: str | None = None

    # 캐시에 새로 적재할 대상인지 — 캐시에서 읽은 것을 다시 쓰지 않기 위해.
    cache_on_write: bool = False


def _model_for(diagnosis: LeadDiagnosis, platform: str) -> str:
    models = diagnosis.requested_models or {}
    return models.get("openai" if platform == "chatgpt" else "gemini") or ""


def plan_measurements(diagnosis: LeadDiagnosis) -> list[_Measurement]:
    """계획된 18건. 실제 호출 여부와 무관하게 먼저 전부 만든다 —
    계획과 결과를 같은 구조로 다뤄야 '몇 건이 빠졌는지'를 셀 수 있다."""
    planned: list[_Measurement] = []
    for query in diagnosis.queries or []:
        for platform in PLATFORMS:
            for repeat_no in range(1, diagnosis.repeat_count + 1):
                planned.append(
                    _Measurement(
                        platform=platform,
                        query_slot=int(query["slot"]),
                        query_text=query["text"],
                        repeat_no=repeat_no,
                        requested_model=_model_for(diagnosis, platform),
                    )
                )
    return planned


async def _load_cached(db: AsyncSession, diagnosis: LeadDiagnosis, planned: list[_Measurement]):
    """1단계 — 캐시 적중분을 채운다. 부분 적중이면 부분만 채운다."""
    seen: set[tuple[int, str]] = set()
    for measurement in planned:
        key = (measurement.query_slot, measurement.platform)
        if key in seen:
            continue
        seen.add(key)

        cached = await lead_query_cache.get_cached_answers(
            db,
            query_text=measurement.query_text,
            platform=measurement.platform,
            requested_model=measurement.requested_model,
            repeat_count=diagnosis.repeat_count,
        )
        if not cached:
            continue
        for sibling in planned:
            if (sibling.query_slot, sibling.platform) != key:
                continue
            hit = cached.get(sibling.repeat_no)
            if hit is None:
                continue
            sibling.answer_source = AnswerSource.CACHED.value
            sibling.raw_response = hit.raw_response
            sibling.answer_model = hit.answer_model
            sibling.source_urls = hit.source_urls or []
            # 캐시 적중분도 원본 측정의 메타데이터를 그대로 들고 온다. 여기서 빠뜨리면
            # 캐시 적중률이 높은 진단일수록 "검색 사용 N/9"가 비어 보여, 측정 조건을
            # 설명해야 할 때 정작 캐시가 잘 든 건들이 설명 불가가 된다.
            sibling.search_calls = hit.search_calls
            sibling.input_tokens = hit.input_tokens
            sibling.output_tokens = hit.output_tokens
            # **원본 측정 시각을 그대로 들고 온다.** 오늘로 찍으면 7일 전 답변을
            # 오늘 측정한 것처럼 파는 것이 된다 (설계 §2-6, T-15).
            sibling.measured_at = hit.measured_at


async def _measure_one(diagnosis: LeadDiagnosis, measurement: _Measurement) -> None:
    """2단계 — 답변 확보(캐시 미적중 시에만 호출) + 판정(항상)."""
    if not measurement.raw_response:
        answer = await sov_engine.fetch_answer(
            measurement.query_text,
            measurement.platform,
            pool=sov_engine.POOL_LEADGEN,
            # 접수 시점에 고정한 모델. 실행 시점 전역 설정과 다르면 호출하지 않는다 —
            # 캐시 키와 리포트 표기가 실제 호출 모델과 어긋나면 안 된다.
            requested_model=measurement.requested_model,
        )
        measurement.measured_at = datetime.now(timezone.utc)
        measurement.source_urls = answer.get("source_urls") or []
        if answer.get("measurement_status") != MEASUREMENT_SUCCESS:
            measurement.measurement_status = MEASUREMENT_FAILED
            measurement.failure_reason = answer.get("failure_reason")
            return
        measurement.raw_response = answer["text"]
        measurement.answer_model = answer.get("answer_model")
        measurement.search_calls = answer.get("search_calls")
        measurement.input_tokens = answer.get("input_tokens")
        measurement.output_tokens = answer.get("output_tokens")
        # 답변 자체는 병원과 무관하므로, 판정이 실패해도 이 답변은 캐시할 값어치가 있다.
        measurement.cache_on_write = True

    try:
        parsed = await sov_engine.judge_mention(
            diagnosis.subject_hospital_name,
            measurement.raw_response,
            diagnosis.subject_region,
        )
    except Exception as exc:  # noqa: BLE001
        # 응답 수신과 언급 판정 성공은 별개다. 판정 실패를 '미언급 0%'로 넣지 않는다 —
        # 그렇게 하면 도구 장애가 병원 성과처럼 보인다.
        logger.warning("lead diagnosis judge failed: %s", exc)
        measurement.measurement_status = MEASUREMENT_FAILED
        measurement.failure_reason = "mention_parse_failed"
        return

    # 판정 3값을 그대로 들고 온다. AMBIGUOUS의 is_mentioned는 None이고, 집계는
    # 이 None을 분자에서도 분모에서도 뺀다 — 확정하지 못한 것을 세지 않기 위해서다.
    measurement.mention_verdict = parsed["verdict"]
    measurement.is_mentioned = parsed.get("is_mentioned")
    # 측정(답변 수신 + 판정 수행)은 성공했다. 판정이 '확정 불가'인 것은 측정 실패가
    # 아니라 판정 결과이므로 여기서 FAILED로 접지 않는다 — 섞으면 공급자 장애와
    # 이름 모호성을 같은 칸에 넣게 되고, 어느 쪽이 문제인지 영영 못 가른다.
    measurement.measurement_status = MEASUREMENT_SUCCESS
    measurement.failure_reason = None


def is_confirmed(measurement: _Measurement) -> bool:
    """분모에 들어갈 자격 — 답변을 받았고 **판정까지 확정**된 측정.

    AMBIGUOUS는 측정은 성공했지만 확정되지 않았으므로 분모가 아니다.
    """
    return (
        measurement.measurement_status == MEASUREMENT_SUCCESS
        and measurement.mention_verdict != MentionVerdict.AMBIGUOUS.value
    )


def confirmed_per_platform(planned: list[_Measurement]) -> dict[str, int]:
    counts: dict[str, int] = {platform: 0 for platform in PLATFORMS}
    for measurement in planned:
        if is_confirmed(measurement):
            counts[measurement.platform] = counts.get(measurement.platform, 0) + 1
    return counts


def resolve_execution_status(diagnosis: LeadDiagnosis, planned: list[_Measurement]) -> str:
    """§4-4 — **확정 판정 개수**로 판정한다.

    ## 왜 플랫폼당 하한이 필요한가

    이전에는 플랫폼당 확정 1건만 있어도 `PARTIAL`이었고 `PARTIAL`은 리포트가 나갔다.
    실패는 분모에서 빠지므로, 18건 중 2건만 확정되고 그 2건에 병원명이 나오면
    리포트에 **100%** 가 찍힌다. 공급자 장애가 몰린 날의 결측이 그대로 '성과'로
    인쇄되는 구조였다 — 그리고 결측은 무작위가 아니므로 이 오차는 양방향이 아니다.

    그래서 플랫폼당 계획 9건 중 **8건 이상 확정**을 요구한다. 8/9는 11.1% 한 칸까지만
    결측을 허용한다는 뜻이다. 7/9부터는 최대 22.2%가 사라져 숫자가 결측에 좌우된다.
    9/9(무결측)가 아닌 이유는 SLA와의 절충이다 — 이 완화는 리포트에 실패·보류 건수를
    같은 크기로 표기한다는 전제 위에서만 정당하다(F3-5).

    합산으로 갈음하지 않는다. 9/9와 7/9를 더해 16/18로 통과시키면 취약한 플랫폼이
    튼튼한 플랫폼 뒤에 숨는다.
    """
    if not planned:
        return ExecutionStatus.FAILED.value

    planned_per_platform: dict[str, int] = {platform: 0 for platform in PLATFORMS}
    for measurement in planned:
        planned_per_platform[measurement.platform] = (
            planned_per_platform.get(measurement.platform, 0) + 1
        )
    confirmed = confirmed_per_platform(planned)

    for platform, planned_count in planned_per_platform.items():
        floor = min_confirmed_for(planned_count)
        if confirmed.get(platform, 0) < floor:
            return ExecutionStatus.FAILED.value

    if sum(confirmed.values()) == len(planned):
        return ExecutionStatus.SUCCEEDED.value
    return ExecutionStatus.PARTIAL.value


async def _notify_budget_blocked(
    diagnosis: LeadDiagnosis, live_calls: int, reason: str | None
) -> None:
    """예산 차단은 자동 복구 대상이 아니다 — 상한을 올릴지 사과할지는 사람이 정한다."""
    try:
        await notifier.notify_ops_alert(
            title="무료 진단 측정이 호출 예산으로 차단됨",
            message=(
                f"진단 `{diagnosis.id}` ({diagnosis.subject_hospital_name})의 측정을 "
                f"시작하지 못했습니다.\n"
                f"필요 호출: {live_calls}건\n"
                f"사유: {reason or '알 수 없음'}\n"
                "**신청자는 리포트를 받지 못합니다.** 상한을 조정하거나 신청자에게 안내가 "
                "필요합니다. 상한 조정 후에는 Admin에서 재실행해 주세요."
            ),
        )
    except Exception:  # noqa: BLE001 — 알림 실패가 상태 확정을 되돌리지 않는다.
        logger.warning("lead diagnosis budget-block alert delivery failed")


async def run_diagnosis_measurements(db: AsyncSession, diagnosis: LeadDiagnosis) -> dict:
    """진단 1건의 측정을 끝내고 `execution_status`를 확정한다.

    호출부(폴러)가 이미 `RUNNING`으로 claim한 상태여야 한다 — claim 없이 부르면
    같은 진단이 두 워커에서 동시에 측정된다.
    """
    planned = plan_measurements(diagnosis)
    if not planned:
        diagnosis.execution_status = ExecutionStatus.FAILED.value
        diagnosis.error = "측정할 질의가 없습니다."
        diagnosis.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"planned": 0, "succeeded": 0, "cached": 0, "status": diagnosis.execution_status}

    # 접수 시점에 고정한 측정 정책과 실행 시점 정책이 다르면 재지 않는다 — 모델 핀과
    # 같은 규약이다. API 배포와 워커 배포 사이에 정책이 바뀌면, 리포트가 공개하는
    # 조건(접수 스냅샷)과 실제 측정 조건이 어긋난 숫자를 팔게 된다.
    # 스냅샷이 없는 진단(도입 이전 접수)은 검사 대상이 아니다.
    snapshot = diagnosis.measurement_config
    # **실행 조건**만 본다. 질의 설계가 바뀐 것은 실행을 막을 이유가 되지 않는다 —
    # 접수 시점에 질의 원문이 이미 저장됐으므로 그 질의로 재면 된다. 여기서 질의
    # 설계까지 보면 생성기 배포가 대기 중인 진단을 전부 죽인다.
    if snapshot and not sov_engine.same_execution_policy(
        snapshot, sov_engine.measurement_protocol()
    ):
        diagnosis.execution_status = ExecutionStatus.FAILED.value
        diagnosis.error = (
            f"측정 정책 불일치: 접수 {snapshot.get('policy_version')} ≠ "
            f"실행 {sov_engine.MEASUREMENT_POLICY_VERSION}. 접수 시점 조건으로 잴 수 없어 "
            "중단했습니다."
        )
        diagnosis.finished_at = datetime.now(timezone.utc)
        diagnosis.running_since = None
        await db.commit()
        return {
            "planned": len(planned),
            "succeeded": 0,
            "cached": 0,
            "status": diagnosis.execution_status,
            "blocked": "policy_drift",
        }

    await _load_cached(db, diagnosis, planned)

    # ── 호출 예산 예약 (설계 §6).
    # **선착순 자리 수는 호출 상한이 아니다.** 자리 20개는 접수를 20건으로 묶지만, 측정
    # 재시도(최대 3회)까지 겹치면 하루 공급자 호출은 1,000건을 넘을 수 있다. 자리 카운터는
    # 그것을 세지 않는다.
    #
    # 예약 단위는 **캐시 미적중분**이다 — 캐시에서 온 답변은 돈을 쓰지 않으므로, 공유 캐시의
    # 절감이 예산에도 그대로 반영된다. 판정 호출(콜당 0.26원, 답변 모델의 1/370)은 세지 않는다.
    live_calls = sum(1 for m in planned if not m.raw_response)
    decision = await cost_guard.check_and_increment("leadgen", count=live_calls)
    if not decision.allowed:
        # 예산 소진은 측정 실패가 아니다. 그런데 여기서 조용히 물러나면 신청자는 리포트를
        # 못 받고 아무도 그 이유를 모른다 — FAILED로 종결하고 사람을 부른다.
        diagnosis.execution_status = ExecutionStatus.FAILED.value
        diagnosis.error = f"호출 예산 초과로 측정을 중단했습니다: {decision.reason}"
        diagnosis.finished_at = datetime.now(timezone.utc)
        diagnosis.running_since = None
        await db.commit()
        await _notify_budget_blocked(diagnosis, live_calls, decision.reason)
        return {
            "planned": len(planned),
            "succeeded": 0,
            "cached": sum(1 for m in planned if m.answer_source == AnswerSource.CACHED.value),
            "status": diagnosis.execution_status,
            "blocked": "cost_guard",
        }

    await asyncio.gather(*(_measure_one(diagnosis, m) for m in planned))

    for measurement in planned:
        db.add(
            LeadDiagnosisResult(
                diagnosis_id=diagnosis.id,
                platform=measurement.platform,
                query_slot=measurement.query_slot,
                repeat_no=measurement.repeat_no,
                attempt_no=diagnosis.execution_attempts or 1,
                query_text=measurement.query_text[:500],
                requested_model=measurement.requested_model,
                answer_model=measurement.answer_model,
                is_mentioned=measurement.is_mentioned,
                mention_verdict=measurement.mention_verdict,
                measurement_status=measurement.measurement_status,
                failure_reason=measurement.failure_reason,
                raw_response=measurement.raw_response or "",
                source_urls=measurement.source_urls or None,
                search_calls=measurement.search_calls,
                input_tokens=measurement.input_tokens,
                output_tokens=measurement.output_tokens,
                answer_source=measurement.answer_source,
                measured_at=measurement.measured_at or datetime.now(timezone.utc),
            )
        )

    for measurement in planned:
        if not measurement.cache_on_write:
            continue
        await lead_query_cache.store_answer(
            db,
            query_text=measurement.query_text,
            platform=measurement.platform,
            requested_model=measurement.requested_model,
            repeat_no=measurement.repeat_no,
            answer_model=measurement.answer_model,
            raw_response=measurement.raw_response,
            source_urls=measurement.source_urls,
            search_calls=measurement.search_calls,
            input_tokens=measurement.input_tokens,
            output_tokens=measurement.output_tokens,
            measured_at=measurement.measured_at,
        )

    diagnosis.execution_status = resolve_execution_status(diagnosis, planned)
    diagnosis.finished_at = datetime.now(timezone.utc)
    diagnosis.running_since = None
    if diagnosis.execution_status == ExecutionStatus.FAILED.value:
        failures = [m.failure_reason for m in planned if m.failure_reason]
        confirmed = confirmed_per_platform(planned)
        detail = ", ".join(f"{platform} 확정 {count}건" for platform, count in confirmed.items())
        diagnosis.error = (
            f"확정 판정이 하한에 미달했습니다 ({detail}). "
            f"측정 실패 {len(failures)}/{len(planned)}건"
            + (f": {failures[0]}" if failures else "")
        )
    else:
        diagnosis.error = None

    await db.commit()

    succeeded = sum(1 for m in planned if m.measurement_status == MEASUREMENT_SUCCESS)
    confirmed = sum(1 for m in planned if is_confirmed(m))
    ambiguous = succeeded - confirmed
    cached = sum(1 for m in planned if m.answer_source == AnswerSource.CACHED.value)
    logger.info(
        "lead diagnosis %s measured: %s/%s success (%s confirmed, %s ambiguous), "
        "%s from cache, status=%s",
        diagnosis.id,
        succeeded,
        len(planned),
        confirmed,
        ambiguous,
        cached,
        diagnosis.execution_status,
    )
    return {
        "planned": len(planned),
        "succeeded": succeeded,
        "confirmed": confirmed,
        "ambiguous": ambiguous,
        "cached": cached,
        "status": diagnosis.execution_status,
    }
