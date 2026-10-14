"""비-Critical 일일 갱신 — 교체·평생 상한을 다 쓴 비-Critical 슬롯을 사람에게 넘기지 않는다.

대표 원칙(2026-10-14): Critical이 아닌 글은 예정일에 빠짐없이 나가야 하며, 어떤 경로도 집지 않는
미발행 글은 버그다. 누락 방지 5(PR #268)는 비-Critical 원인에도 주제 교체 두 번을 줬지만, 슬롯 평생
지출 상한(주제 3 × 작가 6)을 다 쓰면 더 살 수 없어 `OPERATOR_REQUIRED`에서 멈췄다(2026-10-15 나머지).

이 모듈은 그 끝을 **일일 갱신**으로 바꾼다:

- 비-Critical 슬롯의 사다리(표본 3일 소진·생성 예산 소진·저장 본문 수리 소진)가 교체를 다 쓴 뒤
  끝나면 시도 기록은 `OPERATOR_REQUIRED`가 아니라 갱신 대기(`DAILY_RENEWAL_KEY`, 자동 복구 소유,
  다음 시도 = 다음 갱신 시각)다(`renewal_wait_fields`). 사람의 사고를 열지 않는다 — 생성 사고는
  RETRYING(조용한 기록)이고, 늦은 슬롯은 기존 일일 SLO 요약 줄이 보여 준다.
- KST 하루에 한 번 갱신 단계(`topic_swap_fallback.renew_noncritical_slots`, 교체 pass 바로 뒤·로더 앞)가
  예산 원장에 새 구간(`generation_attempt_state.open_renewal_segment`)을 연다 — 작가 세션
  `CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS`(기본 1)개, 세션당 정상 세션 한도(작가 3·검수 3). 평생 계수는 지우지 않고
  구간 뒤의 획득만 센다. 쓰지 않은 측정 질문 후보가 있으면 그 주제로, 없으면 같은 주제를 누적 거절
  사유와 함께 다시 쓴다.
- 지출의 끝: 구간은 하루 한 번, 병원별 하루 상한은 W1 schedule lane의 갱신 상한
  (`COST_GUARD_CONTENT_RENEWAL_HOSPITAL_DAILY`, `cost_guard.cost_lane(..., hospital_daily_cap=)`).

Critical 원인(교정 상한을 다 쓴 사실·안전 차단, 모델이 단정한 Critical HARD·금지 표현 등 입력 변경
종착)은 종전 사다리 그대로 `OPERATOR_REQUIRED`·`INPUT_CHANGE_REQUIRED`에서 끝난다. 사람이 정하는
참고자료 보류(`operator_decides`)와 이미지 원인(자기 폴백이 있다)도 대상이 아니다.

로더(claim 전)·워커·정체 감지기는 같은 술어(`renewal_due`)를 쓴다 — 갱신을 기다리는 슬롯은 갱신 단계가
구간을 열기 전에는 집지 않는다. 이 모듈은 잎이다(워커 본체를 import하지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.core.config import settings
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    TOPIC_SWAP_LIMIT,
    GenerationBudgetUnknown,
    budget_generation_attempt,
    read_generation_attempt,
    read_generation_budget,
    renewal_segment,
    renewal_segment_spent,
    writer_purchase_blocked,
)
from app.workers.generation_retry_policy import (
    AUTO_CORRECTION_EXHAUSTED_KEY,
    BODY_REPAIR_STATE_KEY,
    GENERATION_BUDGET_EXHAUSTED_CODE,
    KST,
    OPERATOR_DECIDES_KEY,
    SAMPLE_BODY_CODES,
    WINDOWED_ENVIRONMENT_CODES,
    GenerationRetryClass,
    environment_attempt_period,
    environment_window_closed,
    has_model_declared_hard_finding,
    next_recovery_deadline,
    recovery_is_abandoned,
    repair_recovery_remains,
)

# 시도 기록의 갱신 대기 표시. 이 표시가 있는 기록은 갱신 단계가 소유한다.
DAILY_RENEWAL_KEY = "daily_renewal"
# 원고 미생성 게이트가 갱신 대상이 아닌 종착(Critical·입력 변경·사람 보류)을 덮을 때 남기는 표시 —
# 증상 기록이 원인을 지워도 일일 갱신이 그 슬롯을 되살리지 않는다(`tasks._record_gate_blocker_decision`).
RENEWAL_EXCLUDED_KEY = "renewal_excluded"
# 갱신 구간을 연 직후의 시도 기록 원인 — 실패가 아니라 "오늘 새 예산으로 다시 쓴다"는 자동 복구 상태다.
DAILY_RENEWAL_REASON = "DAILY_RENEWAL"
# 저장 본문 수리 세션 예산(3일)을 다 쓴 뒤 주제 교체 계단을 타는 코드(`topic_swap_fallback`이 다시 내보낸다).
BODY_REPAIR_SWAP_CODES = frozenset({"MISSING_REFERENCES", "FAQ_FIELDS_MISSING", "ESSENCE_NOT_ALIGNED"})
# 갱신 구간이 열린 슬롯에서만 갱신 대기로 바꾸는 환경 원인. 이 원인들의 일일 예산·3일 창은 갱신 구간
# 하나의 지출을 다시 쪼갤 뿐이라, 구간이 열린 뒤에는 다음 갱신까지 기다린다(하루 한 세션).
_RENEWAL_MODE_ENVIRONMENT_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_FAILED",
        "CONTENT_NOT_GENERATED",
        "COST_BLOCKED",
    }
)
# 갱신 대상 원인(허용 목록). 이미지 원인(자기 폴백)·검수 장애(D37 선발행 — 공개될 수 있는 본문을 지우지
# 않는다)·레거시 지출 불명(D22)·자동 복구의 중간 기록(주제 교체·갱신 시작)은 들지 않는다.
_RENEWABLE_REASONS = frozenset(
    {*SAMPLE_BODY_CODES, *BODY_REPAIR_SWAP_CODES, *_RENEWAL_MODE_ENVIRONMENT_CODES}
)


def renewal_sessions() -> int:
    """하루 갱신 구간의 작가 세션 수. 0이면 일일 갱신이 꺼져 종전 사다리(사람의 일)다."""

    return max(int(settings.CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS), 0)


def topic_swap_limit(*, critical: bool = True) -> int:
    """같은 슬롯의 자동 주제 교체 상한.

    원인과 무관하게 `CONTENT_AUTO_TOPIC_SWAP_MAX`(기본 2)까지다. 2026-10-14 O5는 Critical이 아닌 원인을
    한 번으로 묶었지만, 같은 날 누락 방지 5(대표 원칙 — Critical이 아닌 글은 예정일에 빠짐없이
    나가야 한다)로 Critical과 같은 두 번을 준다. 예산 기록이 받아 주는 상한(`TOPIC_SWAP_LIMIT`)을
    넘지 않는다. `critical` 인자는 호출부 호환을 위해 남긴다.
    """

    del critical
    return max(0, min(int(settings.CONTENT_AUTO_TOPIC_SWAP_MAX), TOPIC_SWAP_LIMIT))


def critical_swap_reason(attempt: Mapping[str, Any]) -> bool:
    """이 시도 기록이 Critical 원인의 교체 요청인가 — 최소 교정 상한을 다 쓴 사실·안전 차단."""

    return attempt.get("reason") == "CONTENT_AI_HARD_FINDING" and bool(
        attempt.get(AUTO_CORRECTION_EXHAUSTED_KEY)
    )


def topic_swap_budget_left(item: Any, *, critical: bool = False) -> bool:
    """이 슬롯이 자동 주제 교체를 한 번 더 쓸 수 있는가 — 교체 상한의 단일 판정.

    `critical`은 지금 교체를 요청하는 원인이 Critical인가다(`critical_swap_reason`).
    """

    limit = topic_swap_limit(critical=critical)
    if regular_swap_count(getattr(item, "topic_swap_history", None)) >= limit:
        return False
    try:
        budget = read_generation_budget(budget_generation_attempt(item))
    except GenerationBudgetUnknown:
        return False
    return budget.topic_swaps < limit and len(budget.topics) < limit + 1


def regular_swap_count(history: Any) -> int:
    """교체 예산을 쓴 정규 교체 수 — 일일 갱신의 주제 변경(`daily_renewal` 항목)은 세지 않는다."""

    if not isinstance(history, list):
        return 0
    return sum(
        1 for entry in history if not (isinstance(entry, dict) and entry.get("daily_renewal") is True)
    )


def in_renewal_mode(item: Any) -> bool:
    """예산 원장에 일일 갱신 구간이 열린 적이 있는가 — 그 뒤 본문 지출은 구간 상한만 본다."""

    return renewal_segment(budget_generation_attempt(item)) is not None


def _today(now: datetime) -> str:
    return environment_attempt_period(now)


def record_is_renewable(attempt: Mapping[str, Any]) -> bool:
    """이 기록의 원인이 비-Critical 갱신 대상인가(원인·분류만 본다). 갱신 대기 기록도 대상이다."""

    if attempt.get(DAILY_RENEWAL_KEY) and attempt.get("reason") == DAILY_RENEWAL_REASON:
        return True
    return _renewable_record(attempt)


def _renewable_record(attempt: Mapping[str, Any]) -> bool:
    """이 기록의 원인이 비-Critical 갱신 대상인가(원인·분류만 본다)."""

    if attempt.get(RENEWAL_EXCLUDED_KEY):
        return False
    if attempt.get("reason") not in _RENEWABLE_REASONS:
        return False
    if attempt.get(OPERATOR_DECIDES_KEY) or critical_swap_reason(attempt):
        return False
    return attempt.get("retry_class") != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value


def _repair_state(item: Any) -> Mapping[str, Any] | None:
    summary = getattr(item, "essence_check_summary", None)
    state = summary.get(BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
    return state if isinstance(state, Mapping) else None


def ladder_spent(item: Any, attempt: Mapping[str, Any]) -> bool:
    """비-Critical 사다리의 끝인가 — 그대로 두면 사람의 일(`OPERATOR_REQUIRED`)이나 공회전이다.

    교체가 남았으면 교체 pass가 먼저 돈다(같은 pass에서 갱신 단계는 그 뒤다). 여기서는 사다리의 끝 모양만 본다.
    """

    reason = attempt.get("reason")
    retry_class = attempt.get("retry_class")
    if retry_class == GenerationRetryClass.OPERATOR_REQUIRED.value and reason in SAMPLE_BODY_CODES:
        return True
    if reason == GENERATION_BUDGET_EXHAUSTED_CODE:
        # 지금 작가 결과 하나도 예약할 수 없는 슬롯이다(주제당·평생·구간·전송 상한). 살 수 있으면 아직
        # 표본 사다리다(검수만 소진 등 — 소진 일수 뒤 교체·갱신).
        return writer_purchase_blocked(dict(attempt))
    if reason in BODY_REPAIR_SWAP_CODES:
        return not repair_recovery_remains(_repair_state(item))
    if reason in WINDOWED_ENVIRONMENT_CODES:
        # W3 환경 장애의 3일 창이 닫혔거나 어떤 스윕도 다시 집지 않는다고 굳은 빈 슬롯(게이트가 그 위에
        # 쓴 원고 미생성 OPERATOR_REQUIRED 포함) — 사람이 아니라 갱신이 다음 날 다시 쓴다.
        return (
            retry_class == GenerationRetryClass.OPERATOR_REQUIRED.value
            or environment_window_closed(attempt)
            or recovery_is_abandoned(attempt)
        )
    return False


def stored_critical_verdict(item: Any) -> bool:
    """저장된 독립 검수가 Critical HARD를 단정했는가 — 그 판정은 Critical 계단이 소유한다."""

    summary = getattr(item, "essence_check_summary", None)
    review = summary.get("ai_review") if isinstance(summary, dict) else None
    return has_model_declared_hard_finding(review, item)


def renewal_candidate(item: Any) -> bool:
    """일일 갱신이 소유한 비-Critical 슬롯인가(오늘 구간을 이미 열었는지와 무관)."""

    if renewal_sessions() <= 0:
        return False
    if getattr(item, "first_published_at", None) or getattr(item, "human_edited_at", None):
        return False
    if getattr(item, "body", None) and stored_critical_verdict(item):
        # 지금 본문에 Critical 판정이 있으면 갱신이 비우지 않는다 — 교정·교체·사람 계단이 맡는다.
        return False
    attempt = read_generation_attempt(item)
    if not _renewable_record(attempt):
        return False
    try:
        read_generation_budget(budget_generation_attempt(item))
    except GenerationBudgetUnknown:
        # 지출을 모르는 슬롯은 D22 교체(정체 감지기 S0)가 먼저다.
        return False
    if attempt.get(DAILY_RENEWAL_KEY):
        return True
    return ladder_spent(item, attempt)


def renewal_due(item: Any, now: datetime | None = None) -> bool:
    """오늘(KST) 갱신 구간을 아직 열지 않은 갱신 대상인가 — 로더·워커·정체 감지기의 공통 술어.

    참이면 갱신 단계만 이 슬롯을 집는다(구간을 연 뒤의 시도 기록은 지금 기한이라 로더가 집는다).
    """

    if not renewal_candidate(item):
        return False
    segment = renewal_segment(budget_generation_attempt(item))
    # 날짜는 앞으로만 간다 — 자정을 넘긴 늦은 판정이 어제 날짜로 다시 열지 않는다.
    return segment is None or str(segment.get("period")) < _today(now or datetime.now(UTC))


def renewal_waiting(item: Any, now: datetime | None = None) -> bool:
    """오늘 구간을 쓴 뒤 다음 갱신을 기다리는 슬롯인가 — 본문이 있어도 그 시각 전에는 집지 않는다.

    구간 안의 비-Critical 실패는 갱신 대기(`renewal_wait_fields`)로 남고, 그 기한 전의 재집기는 구간
    상한에 막혀 아무것도 사지 못한 채 실행·사고 쓰기만 늘린다.
    """

    if not renewal_candidate(item):
        return False
    attempt = read_generation_attempt(item)
    if attempt.get("reason") == DAILY_RENEWAL_REASON:
        return False
    raw = attempt.get("next_retry_at")
    if not isinstance(raw, str):
        return False
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) < due


def renewal_holds(item: Any, now: datetime | None = None) -> bool:
    """로더(claim 전)·워커가 이 슬롯을 집지 않는가 — 갱신 단계가 구간을 열기 전이거나 다음 갱신 대기."""

    return renewal_due(item, now) or renewal_waiting(item, now)


def next_renewal_deadline(
    scheduled_date: Any, now: datetime, *, opened_today: bool
) -> datetime:
    """갱신 대기 기록의 다음 시도 시각 — 다음 갱신 구간을 쓸 첫 생성 스윕.

    오늘 구간을 이미 열었으면 내일부터, 아니면 오늘 남은 스윕부터 이 예정일을 집는 첫 스윕이다
    (`next_recovery_deadline`과 같은 창 규칙). 어떤 스윕도 집지 않는 예정일이면 다음 KST 자정이다 —
    `None`(어떤 스윕도 집지 않는다는 결정)을 쓰면 갱신 대기가 기한 없는 사람의 일로 읽힌다.
    """

    probe = {
        "reason": "GENERATION_REJECTED",
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "exhausted_days": 0,
        "attempt_period": _today(now),
        "provider_attempt_count": 99 if opened_today else 0,
    }
    deadline = next_recovery_deadline(probe, scheduled_date=scheduled_date, now=now)
    if deadline is not None:
        return deadline
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(), tzinfo=KST).astimezone(UTC)


def renewal_wait_fields(
    item: Any,
    attempt: Mapping[str, Any],
    *,
    now: datetime,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """새 시도 기록을 갱신 대기로 바꿀 값. 대상이 아니면 ``None``.

    `tasks._remember_generation_attempt`가 기록을 저장하기 직전에 부른다. 갱신 구간이 열린 슬롯의
    비-Critical 실패는 모두 다음 갱신을 기다린다(하루 한 세션). 아직 구간이 없으면 교체를 다 쓴 뒤
    사다리가 끝난 경우에만 바꾼다 — 교체가 남았으면 종전처럼 `OPERATOR_REQUIRED`로 두어 교체 pass가 집는다.
    """

    if renewal_sessions() <= 0:
        return None
    if getattr(item, "first_published_at", None) or getattr(item, "human_edited_at", None):
        return None
    if not _renewable_record(attempt):
        return None
    if getattr(item, "body", None) and stored_critical_verdict(item):
        return None
    if (
        attempt.get("reason") == "CONTENT_NOT_GENERATED"
        and previous
        and previous.get("reason")
        and not (previous.get(DAILY_RENEWAL_KEY) or _renewable_record(previous))
    ):
        # 원고 미생성은 증상이다. 덮는 기록이 Critical·입력 변경 종착이면 그 원인을 따른다.
        return None
    segment = renewal_segment(dict(attempt))
    if segment is None:
        if not ladder_spent(item, attempt):
            return None
        if attempt.get("reason") not in WINDOWED_ENVIRONMENT_CODES and topic_swap_budget_left(item):
            # 교체가 남은 본문 사다리는 종전처럼 OPERATOR_REQUIRED로 두어 교체 pass가 집는다. 환경 장애는
            # 교체 계단을 타지 않으므로 창이 닫히면 곧바로 갱신을 기다린다(사람의 사고를 열지 않는다).
            return None
    # 오늘 구간이 열렸고 세션 하나를 더 살 수 없으면 내일, 아니면(구간 없음·세션 2 이상 설정에서 남은
    # 세션) 오늘 남은 스윕이다.
    opened_today = (
        segment is not None
        and str(segment.get("period")) >= _today(now)
        and renewal_segment_spent(dict(attempt))
    )
    deadline = next_renewal_deadline(
        getattr(item, "scheduled_date", None), now, opened_today=opened_today
    )
    return {
        DAILY_RENEWAL_KEY: True,
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "exhausted_days": 0,
        "next_retry_at": deadline.isoformat(),
    }


def operator_release_renewal_wait(attempt: Mapping[str, Any]) -> bool:
    """운영자 “작업 다시 시도”가 이 갱신 대기 기록의 억제를 푸는가.

    환경 원인(공급자 장애·원고 미생성·비용 보류)이 갱신 대기로 바뀐 기록은 종전 환경 실패 기록처럼
    운영자 재시도가 억제를 푼다(다음 갱신 시각 전이어도). 해제는 억제만 걷고 예산 원장은 그대로라,
    살 수 있는 예산(정상 상한 또는 열린 갱신 구간)이 없으면 공급자 호출 없이 다시 갱신 대기로 끝난다.
    표본 실패에서 온 갱신 대기는 종전 표본 기록처럼 풀지 않는다.
    """

    return bool(attempt.get(DAILY_RENEWAL_KEY)) and (
        attempt.get("reason") in _RENEWAL_MODE_ENVIRONMENT_CODES
    )


def renewal_owns_blocker(code: str, item: Any) -> bool:
    """이 생성 차단을 일일 갱신이 소유하는가 — 사고는 사람의 일이 아니라 RETRYING이다."""

    if item is None:
        return False
    attempt = read_generation_attempt(item)
    if not attempt.get(DAILY_RENEWAL_KEY):
        return False
    # 같은 원인, 또는 기록을 다시 쓰지 않는 증상·예산·환경 보고(원고 미생성 게이트, 본문이 있는 실행의
    # 예외 — 구간 상한 소진 등)다. Critical 실패는 기록을 갱신 표시 없이 다시 쓰므로 여기 오지 않는다.
    return (
        attempt.get("reason") == code
        or code == GENERATION_BUDGET_EXHAUSTED_CODE
        or code in _RENEWAL_MODE_ENVIRONMENT_CODES
    )


def stored_renewal_deadline(item: Any) -> datetime | None:
    """갱신 대기 기록이 저장한 다음 시도 시각."""

    raw = read_generation_attempt(item).get("next_retry_at")
    if not isinstance(raw, str):
        return None
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return due if due.tzinfo is not None else due.replace(tzinfo=UTC)


__all__ = [
    "BODY_REPAIR_SWAP_CODES",
    "DAILY_RENEWAL_KEY",
    "DAILY_RENEWAL_REASON",
    "GENERATION_ATTEMPT_KEY",
    "critical_swap_reason",
    "in_renewal_mode",
    "ladder_spent",
    "next_renewal_deadline",
    "renewal_candidate",
    "renewal_due",
    "renewal_holds",
    "renewal_owns_blocker",
    "renewal_waiting",
    "RENEWAL_EXCLUDED_KEY",
    "record_is_renewable",
    "regular_swap_count",
    "operator_release_renewal_wait",
    "renewal_sessions",
    "renewal_wait_fields",
    "stored_renewal_deadline",
    "topic_swap_budget_left",
    "topic_swap_limit",
]
