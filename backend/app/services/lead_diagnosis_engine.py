"""무료 진단 측정 실행 (설계 §4-4 · §2-6).

한 진단 = 질의 3개 × 플랫폼 2개 × 반복 3회 = **18 측정**으로 고정이다.
고정이어야 원가(§6)와 SLA(§7)가 계산 가능하다.

네트워크 호출은 동시로 실행하되, 완료 결과는 한 세션에서 순차로 커밋한다. 답변과 판정을
각각 체크포인트하므로 판정 장애나 worker 재시작이 이미 받은 답변을 다시 구매하지 않는다.

  1. 읽기   캐시와 이전 시도 체크포인트 조회 (DB, 순차)
  2. 답변   캐시 미적중 공급자 호출 (네트워크, 동시) → 건별 커밋
  3. 판정   미완료 판정 호출 (네트워크, 동시) → 건별 커밋
  4. 확정   전체 실행 상태와 비용 예약 정산
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead_diagnosis import (
    AnswerSource,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
)
from app.services import cost_guard, lead_query_cache, sov_engine
from app.services.incident_types import IncidentFingerprint
from app.services.ops_incident_alerts import open_ops_incident, recover_ops_incident

logger = logging.getLogger(__name__)

# 서로 다른 공급자 경로를 동일 질문·동일 반복 수로 교차 관찰한다. 소비자 앱 시장
# 점유율이나 개인화 화면을 재현한다는 뜻이 아니다(PRD §2).
PLATFORMS: tuple[str, ...] = ("chatgpt", "gemini")

MEASUREMENT_SUCCESS = "SUCCESS"
MEASUREMENT_FAILED = "FAILED"

# 플랫폼당 허용하는 미확정(실패 + 판정 보류) 최대 건수. 계획 9건 기준 하한 8건.
# 이 값을 올리면 결측이 숫자를 흔드는 폭이 커진다 — resolve_execution_status 참고.
MAX_UNCONFIRMED_PER_PLATFORM = 1
_KST = ZoneInfo("Asia/Seoul")
_COST_SWITCH_RECHECK = timedelta(minutes=15)


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
    judgment_input_fingerprint: str | None = None
    judgment_reused: bool = False
    provider_calls: int = 0

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


async def _load_prior_checkpoints(
    db: AsyncSession,
    diagnosis: LeadDiagnosis,
    planned: list[_Measurement],
) -> None:
    """Reuse received answers, and reuse judgments only with an exact stored fingerprint."""
    rows = list(
        (
            await db.execute(
                select(LeadDiagnosisResult)
                .where(LeadDiagnosisResult.diagnosis_id == diagnosis.id)
                .order_by(LeadDiagnosisResult.attempt_no.desc(), LeadDiagnosisResult.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[tuple[str, int, int], LeadDiagnosisResult] = {}
    for row in rows:
        latest.setdefault((row.platform, row.query_slot, row.repeat_no), row)

    for measurement in planned:
        row = latest.get((measurement.platform, measurement.query_slot, measurement.repeat_no))
        if (
            row is None
            or not (row.raw_response or "").strip()
            or row.query_text != measurement.query_text[:500]
            or row.requested_model != measurement.requested_model
        ):
            continue
        measurement.answer_source = row.answer_source
        measurement.measured_at = row.measured_at
        measurement.raw_response = row.raw_response
        measurement.answer_model = row.answer_model
        measurement.source_urls = row.source_urls or []
        measurement.search_calls = row.search_calls
        measurement.input_tokens = row.input_tokens
        measurement.output_tokens = row.output_tokens

        fingerprint = sov_engine.judgment_input_fingerprint(
            hospital_identity=str(diagnosis.id),
            hospital_name=diagnosis.subject_hospital_name,
            response_text=row.raw_response,
            region=diagnosis.subject_region,
            policy=diagnosis.measurement_config or sov_engine.measurement_protocol(),
        )
        if (
            row.judgment_input_fingerprint == fingerprint
            and row.measurement_status == MEASUREMENT_SUCCESS
            and row.mention_verdict in {"MATCHED", "NOT_MATCHED", "AMBIGUOUS"}
        ):
            measurement.is_mentioned = row.is_mentioned
            measurement.mention_verdict = row.mention_verdict
            measurement.measurement_status = MEASUREMENT_SUCCESS
            measurement.failure_reason = None
            measurement.judgment_input_fingerprint = fingerprint
            measurement.judgment_reused = True


async def _fetch_one(diagnosis: LeadDiagnosis, measurement: _Measurement) -> _Measurement:
    """Acquire only the answer stage; shared misses use a crash-recoverable single flight."""
    if measurement.raw_response:
        return measurement

    async def _fetch() -> dict:
        return await sov_engine.fetch_answer(
            measurement.query_text,
            measurement.platform,
            pool=sov_engine.POOL_LEADGEN,
            requested_model=measurement.requested_model,
            lead_id=diagnosis.lead_id,
            workflow="lead_diagnosis_answer",
            run_id=str(diagnosis.id),
            item_id=f"{measurement.platform}:{measurement.query_slot}:{measurement.repeat_no}",
            attempt_id=str(diagnosis.execution_attempts or 1),
        )

    flight = await lead_query_cache.singleflight_fetch_answer(
        query_text=measurement.query_text,
        platform=measurement.platform,
        requested_model=measurement.requested_model,
        repeat_no=measurement.repeat_no,
        fetch=_fetch,
    )
    answer = flight.answer
    measurement.provider_calls += int(answer.get("provider_calls", 1) if flight.owner else 0)
    measurement.measured_at = datetime.now(timezone.utc)
    measurement.source_urls = answer.get("source_urls") or []
    if answer.get("measurement_status") != MEASUREMENT_SUCCESS:
        measurement.measurement_status = MEASUREMENT_FAILED
        measurement.failure_reason = answer.get("failure_reason")
        return measurement
    measurement.raw_response = str(answer.get("raw_response") or answer.get("text") or "")
    measurement.answer_model = answer.get("answer_model")
    measurement.search_calls = answer.get("search_calls")
    measurement.input_tokens = answer.get("input_tokens")
    measurement.output_tokens = answer.get("output_tokens")
    measurement.answer_source = (
        AnswerSource.LIVE.value if flight.owner else AnswerSource.CACHED.value
    )
    # 답변은 병원과 무관하므로 판정 실패와 별개로 즉시 캐시/checkpoint할 가치가 있다.
    # A non-owner can be resuming the prior owner's Redis handoff after that worker died
    # before committing the shared DB cache. Persist every successful miss-path handoff;
    # store_answer isolates uniqueness conflicts when the original owner did commit.
    measurement.cache_on_write = True
    measurement.failure_reason = "mention_parse_pending"
    return measurement


async def _judge_one(diagnosis: LeadDiagnosis, measurement: _Measurement) -> _Measurement:
    """Run only the judgment stage; an exact prior judgment is terminal, including AMBIGUOUS."""
    if not measurement.raw_response or measurement.judgment_reused:
        return measurement

    fingerprint = sov_engine.judgment_input_fingerprint(
        hospital_identity=str(diagnosis.id),
        hospital_name=diagnosis.subject_hospital_name,
        response_text=measurement.raw_response,
        region=diagnosis.subject_region,
        policy=diagnosis.measurement_config or sov_engine.measurement_protocol(),
    )
    measurement.judgment_input_fingerprint = fingerprint
    # Keep the compatibility wrapper here so existing isolated tests can replace the judge,
    # while explicitly establishing leadgen attribution for cached as well as live answers.
    provider_calls = sov_engine.estimate_judgment_provider_calls(
        diagnosis.subject_hospital_name,
        measurement.raw_response,
    )
    try:
        with sov_engine.provider_execution_context(
            pool=sov_engine.POOL_LEADGEN,
            lead_id=diagnosis.lead_id,
            workflow="lead_diagnosis_judgment",
            run_id=str(diagnosis.id),
            item_id=f"{measurement.platform}:{measurement.query_slot}:{measurement.repeat_no}",
            attempt_id=str(diagnosis.execution_attempts or 1),
        ):
            async with sov_engine._get_semaphore(f"{sov_engine.POOL_LEADGEN}:openai-judge"):
                parsed = await sov_engine.judge_mention(
                    diagnosis.subject_hospital_name,
                    measurement.raw_response,
                    diagnosis.subject_region,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("lead diagnosis judge failed: %s", type(exc).__name__)
        measurement.provider_calls += provider_calls
        measurement.measurement_status = MEASUREMENT_FAILED
        measurement.failure_reason = "mention_parse_failed"
        return measurement

    measurement.provider_calls += provider_calls
    measurement.mention_verdict = parsed["verdict"]
    measurement.is_mentioned = parsed.get("is_mentioned")
    measurement.measurement_status = MEASUREMENT_SUCCESS
    measurement.failure_reason = None
    return measurement


def is_confirmed(measurement: _Measurement) -> bool:
    """분모에 들어갈 자격 — 답변을 받았고 **판정까지 확정**된 측정.

    AMBIGUOUS는 측정은 성공했지만 확정되지 않았으므로 분모가 아니다.
    """
    return sov_engine.record_is_confirmed(measurement)


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
    """Persist one deduplicated incident; the deferred row recovers without human polling."""
    try:
        await open_ops_incident(
            pipeline="lead_diagnosis",
            object_type="diagnosis",
            object_id=str(diagnosis.id),
            incident_type="LEAD_DIAGNOSIS_COST_BLOCKED",
            safe_error_code="COST_BLOCKED",
            problem="무료 진단 측정이 호출 예산 안전장치로 시작되지 않았습니다.",
            customer_impact="신청자에게 진단 리포트를 전달할 수 없습니다.",
            next_action="비용 안전장치가 허용하는 시점에 시스템이 진단을 자동 재개합니다.",
            source_type="LEAD_DIAGNOSIS",
            hospital_name=diagnosis.subject_hospital_name,
            actor="lead-diagnosis-worker",
            fingerprint=IncidentFingerprint.COST_BLOCKED,
            notify=False,
        )
    except Exception:  # noqa: BLE001 — 알림 실패가 상태 확정을 되돌리지 않는다.
        logger.warning("lead diagnosis budget-block alert delivery failed")


async def _recover_budget_incident(diagnosis: LeadDiagnosis) -> None:
    try:
        await recover_ops_incident(
            pipeline="lead_diagnosis",
            object_type="diagnosis",
            object_id=str(diagnosis.id),
            fingerprint=IncidentFingerprint.COST_BLOCKED,
            hospital_name=diagnosis.subject_hospital_name,
            actor="lead-diagnosis-worker",
            reason="deferred lead diagnosis resumed after cost guard allowed it",
            notify=False,
        )
    except Exception:  # noqa: BLE001 - incident state never rolls back completed work.
        logger.warning("lead diagnosis budget incident recovery skipped")


def cost_retry_at(reason: str | None, *, now: datetime | None = None) -> datetime:
    """Return the earliest automatic retry for the guard scope that blocked the call."""
    current = (now or datetime.now(timezone.utc)).astimezone(_KST)
    detail = reason or ""
    if "월간" in detail:
        if current.month == 12:
            boundary = current.replace(
                year=current.year + 1, month=1, day=1, hour=0, minute=1,
                second=0, microsecond=0,
            )
        else:
            boundary = current.replace(
                month=current.month + 1, day=1, hour=0, minute=1,
                second=0, microsecond=0,
            )
        return boundary.astimezone(timezone.utc)
    if "일일" in detail:
        boundary = (current + timedelta(days=1)).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        return boundary.astimezone(timezone.utc)
    return (current + _COST_SWITCH_RECHECK).astimezone(timezone.utc)


def _result_row(diagnosis: LeadDiagnosis, measurement: _Measurement) -> LeadDiagnosisResult:
    return LeadDiagnosisResult(
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
        judgment_input_fingerprint=measurement.judgment_input_fingerprint,
    )


def _sync_result_row(row: LeadDiagnosisResult, measurement: _Measurement) -> None:
    row.answer_model = measurement.answer_model
    row.is_mentioned = measurement.is_mentioned
    row.mention_verdict = measurement.mention_verdict
    row.measurement_status = measurement.measurement_status
    row.failure_reason = measurement.failure_reason
    row.raw_response = measurement.raw_response or ""
    row.source_urls = measurement.source_urls or None
    row.search_calls = measurement.search_calls
    row.input_tokens = measurement.input_tokens
    row.output_tokens = measurement.output_tokens
    row.answer_source = measurement.answer_source
    row.measured_at = measurement.measured_at or row.measured_at
    row.judgment_input_fingerprint = measurement.judgment_input_fingerprint


async def _checkpoint_answer(
    db: AsyncSession,
    diagnosis: LeadDiagnosis,
    measurement: _Measurement,
    rows: dict[tuple[str, int, int], LeadDiagnosisResult],
) -> None:
    key = (measurement.platform, measurement.query_slot, measurement.repeat_no)
    row = rows.get(key)
    if row is None:
        row = _result_row(diagnosis, measurement)
        rows[key] = row
        db.add(row)
    else:
        _sync_result_row(row, measurement)

    if measurement.cache_on_write and measurement.raw_response:
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
    # Each received answer is durable before its judgment starts. A worker death after this
    # commit resumes from the stored answer and never purchases it again.
    await db.commit()


async def _defer_cost_block(
    db: AsyncSession,
    diagnosis: LeadDiagnosis,
    planned: list[_Measurement],
    *,
    decision: cost_guard.CostGuardDecision,
    reserved_units: int,
    provider_calls_made: int,
) -> dict:
    # Restore the claim only when this run made no provider call at all. If the answer stage
    # spent calls before the judgment guard closed, those received answers are checkpointed
    # and the run legitimately consumed one bounded execution attempt.
    if provider_calls_made == 0:
        diagnosis.execution_attempts = max(0, int(diagnosis.execution_attempts or 0) - 1)
    diagnosis.execution_status = ExecutionStatus.PENDING.value
    diagnosis.error = f"비용 안전장치로 자동 재개 대기 중: {decision.reason}"
    diagnosis.finished_at = None
    diagnosis.running_since = None
    diagnosis.cost_deferred_until = cost_retry_at(decision.reason)
    diagnosis.cost_defer_reason = (decision.reason or "cost_guard")[:100]
    await db.commit()
    await _notify_budget_blocked(diagnosis, reserved_units, decision.reason)
    return {
        "planned": len(planned),
        "succeeded": 0,
        "cached": sum(1 for m in planned if m.answer_source == AnswerSource.CACHED.value),
        "status": diagnosis.execution_status,
        "blocked": "cost_guard",
        "deferred_until": diagnosis.cost_deferred_until.isoformat(),
    }


async def _cancel_unfinished(tasks: list[asyncio.Task]) -> None:
    """Stop sibling provider work when persistence can no longer checkpoint its result."""
    unfinished = [task for task in tasks if not task.done()]
    for task in unfinished:
        task.cancel()
    if unfinished:
        await asyncio.gather(*unfinished, return_exceptions=True)


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
    await _load_prior_checkpoints(db, diagnosis, planned)

    # ── 호출 예산 예약 (설계 §6).
    # **선착순 자리 수는 호출 상한이 아니다.** 자리 20개는 접수를 20건으로 묶지만, 측정
    # 재시도(최대 3회)까지 겹치면 하루 공급자 호출은 1,000건을 넘을 수 있다. 자리 카운터는
    # 그것을 세지 않는다.
    #
    # 답변과 판정은 별도 stage로 예약한다. 그래야 답변 체크포인트 뒤 판정만 재개할 때
    # 답변 예산을 다시 잡지 않고, 자사/경쟁사 prefilter가 실제로 열어 둔 판정 batch만
    # 예약할 수 있다. count=0도 kill switch를 검사한다.
    live_calls = sum(1 for m in planned if not m.raw_response)
    attempt_id = diagnosis.execution_attempts or 1
    answer_decision = await cost_guard.reserve(
        "leadgen",
        count=live_calls,
        reservation_id=f"lead-diagnosis:{diagnosis.id}:attempt:{attempt_id}:answer",
    )
    if not answer_decision.allowed:
        return await _defer_cost_block(
            db,
            diagnosis,
            planned,
            decision=answer_decision,
            reserved_units=live_calls,
            provider_calls_made=0,
        )

    current_rows = list(
        (
            await db.execute(
                select(LeadDiagnosisResult).where(
                    LeadDiagnosisResult.diagnosis_id == diagnosis.id,
                    LeadDiagnosisResult.attempt_no == (diagnosis.execution_attempts or 1),
                )
            )
        )
        .scalars()
        .all()
    )
    rows: dict[tuple[str, int, int], LeadDiagnosisResult] = {
        (row.platform, row.query_slot, row.repeat_no): row for row in current_rows
    }

    async def _fetch(measurement: _Measurement) -> _Measurement:
        return await _fetch_one(diagnosis, measurement)

    fetch_tasks = [asyncio.create_task(_fetch(measurement)) for measurement in planned]
    try:
        for completed in asyncio.as_completed(fetch_tasks):
            measurement = await completed
            await _checkpoint_answer(db, diagnosis, measurement, rows)
    except BaseException:
        # A failed commit cannot preserve later answers. Stop uncheckpointed calls instead of
        # leaving them alive on the Celery thread's reused event loop.
        await _cancel_unfinished(fetch_tasks)
        raise

    answer_calls = sum(measurement.provider_calls for measurement in planned)
    await cost_guard.settle_reservation(
        answer_decision.receipt,
        consumed_units=min(answer_calls, live_calls),
    )

    judgment_units = sum(
        sov_engine.estimate_judgment_provider_calls(
            diagnosis.subject_hospital_name,
            measurement.raw_response,
        )
        for measurement in planned
        if measurement.raw_response and not measurement.judgment_reused
    )
    judgment_decision = await cost_guard.reserve(
        "leadgen",
        count=judgment_units,
        reservation_id=f"lead-diagnosis:{diagnosis.id}:attempt:{attempt_id}:judgment",
    )
    if not judgment_decision.allowed:
        return await _defer_cost_block(
            db,
            diagnosis,
            planned,
            decision=judgment_decision,
            reserved_units=judgment_units,
            provider_calls_made=answer_calls,
        )

    if diagnosis.cost_defer_reason:
        diagnosis.cost_deferred_until = None
        diagnosis.cost_defer_reason = None
        await db.commit()
        await _recover_budget_incident(diagnosis)

    async def _judge(measurement: _Measurement) -> _Measurement:
        return await _judge_one(diagnosis, measurement)

    judge_tasks = [
        asyncio.create_task(_judge(measurement))
        for measurement in planned
        if measurement.raw_response and not measurement.judgment_reused
    ]
    try:
        for completed in asyncio.as_completed(judge_tasks):
            measurement = await completed
            row = rows[(measurement.platform, measurement.query_slot, measurement.repeat_no)]
            _sync_result_row(row, measurement)
            # Judgment outcome is its own durable checkpoint. AMBIGUOUS is a completed sampled
            # outcome, so later attempts preserve it rather than resampling until a preferred answer.
            await db.commit()
    except BaseException:
        await _cancel_unfinished(judge_tasks)
        raise

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

    judgment_calls = sum(measurement.provider_calls for measurement in planned) - answer_calls
    await cost_guard.settle_reservation(
        judgment_decision.receipt,
        consumed_units=min(judgment_calls, judgment_units),
    )

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
