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

# 측정 대상 플랫폼. 국내 점유 합산 84% (PRD §2).
PLATFORMS: tuple[str, ...] = ("chatgpt", "gemini")

MEASUREMENT_SUCCESS = "SUCCESS"
MEASUREMENT_FAILED = "FAILED"


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
        # 답변 자체는 병원과 무관하므로, 판정이 실패해도 이 답변은 캐시할 값어치가 있다.
        measurement.cache_on_write = True

    try:
        parsed = await sov_engine.judge_mention(
            diagnosis.subject_hospital_name, measurement.raw_response
        )
    except Exception as exc:  # noqa: BLE001
        # 응답 수신과 언급 판정 성공은 별개다. 판정 실패를 '미언급 0%'로 넣지 않는다 —
        # 그렇게 하면 도구 장애가 병원 성과처럼 보인다.
        logger.warning("lead diagnosis judge failed: %s", exc)
        measurement.measurement_status = MEASUREMENT_FAILED
        measurement.failure_reason = "mention_parse_failed"
        return

    measurement.is_mentioned = bool(parsed.get("is_mentioned"))
    measurement.mention_verdict = (
        MentionVerdict.MATCHED.value
        if measurement.is_mentioned
        else MentionVerdict.NOT_MATCHED.value
    )
    measurement.measurement_status = MEASUREMENT_SUCCESS
    measurement.failure_reason = None


def resolve_execution_status(diagnosis: LeadDiagnosis, planned: list[_Measurement]) -> str:
    """§4-4 — 성공 **개수**로 판정한다.

    'PARTIAL'을 느낌으로 두면 리포트 생성 게이트가 흔들린다. 어느 한 플랫폼이라도
    성공 0이면 `FAILED`이고 리포트가 만들어지지 않는다 — 분모가 0인 플랫폼 칸을
    인쇄할 방법이 없기 때문이다(PRD F3-5는 플랫폼별 분모 표기를 요구한다).
    """
    if not planned:
        return ExecutionStatus.FAILED.value

    per_platform: dict[str, int] = {platform: 0 for platform in PLATFORMS}
    for measurement in planned:
        if measurement.measurement_status == MEASUREMENT_SUCCESS:
            per_platform[measurement.platform] = per_platform.get(measurement.platform, 0) + 1

    if any(count == 0 for count in per_platform.values()):
        return ExecutionStatus.FAILED.value
    if sum(per_platform.values()) == len(planned):
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
        diagnosis.error = f"측정 실패 {len(failures)}/{len(planned)}건: {failures[0] if failures else '알 수 없음'}"
    else:
        diagnosis.error = None

    await db.commit()

    succeeded = sum(1 for m in planned if m.measurement_status == MEASUREMENT_SUCCESS)
    cached = sum(1 for m in planned if m.answer_source == AnswerSource.CACHED.value)
    logger.info(
        "lead diagnosis %s measured: %s/%s success, %s from cache, status=%s",
        diagnosis.id,
        succeeded,
        len(planned),
        cached,
        diagnosis.execution_status,
    )
    return {
        "planned": len(planned),
        "succeeded": succeeded,
        "cached": cached,
        "status": diagnosis.execution_status,
    }
