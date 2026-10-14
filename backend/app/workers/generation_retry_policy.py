"""Retry classes and due times consumed by scheduled content sweeps."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.core.config import settings

KST = ZoneInfo("Asia/Seoul")
OVERNIGHT_RECOVERY_HOURS = (1, 4, 7)
DAYTIME_RECOVERY_HOURS = (12, 18, 22)
RECOVERY_SWEEP_HOURS = (*OVERNIGHT_RECOVERY_HOURS, *DAYTIME_RECOVERY_HOURS, 23)
# 23:00 야간 배치가 보는 창은 `[내일, 오늘+lookahead]`, 복구 스윕이 보는 창은
# `[오늘-7일, 오늘+lookahead]`(뒤로는 발행 catch-up과 같은 7일, 앞으로는 야간 배치와 같은
# lookahead)다. 두 창을 모르면 기한이 "다음 스윕 시각"이 되어 그 스윕이 실제로는 집지
# 않는 슬롯까지 재시도 중이라고 말하게 된다. lookahead는 생성 리드 타임(D-3)이며
# `NIGHTLY_GENERATION_LOOKAHEAD_DAYS` 하나가 정한다(`generation_lookahead_days`).
NIGHTLY_SWEEP_HOUR = 23
# `post_publish_review_policy.AUTO_PUBLISH_CATCHUP_DAYS`와 같은 값이어야 한다.
# 이 모듈은 모델을 import하지 않는 잎이라 상수만 복제하고 테스트로 묶어 둔다.
RECOVERY_SWEEP_CATCHUP_DAYS = 7
# 후보 스윕을 찾는 최대 지평. 여기서 못 찾으면 어떤 스윕도 그 슬롯을 집지 않는다.
RECOVERY_DEADLINE_HORIZON_DAYS = 14
# catch-up 창보다 오래된 슬롯은 22:30 백로그 복구(`content_backlog_recovery.reconcile`,
# `core/celery_app.py`의 `stranded-content-recovery`)가 미래 날짜로 옮긴다. 그 실행이
# 끝날 여유 1시간을 더한 시각이 그 슬롯의 다음 판정 시각이다.
BACKLOG_RECOVERY_HOUR = 22
BACKLOG_RECOVERY_MINUTE = 30
BACKLOG_RECOVERY_GRACE = timedelta(hours=1)
ENVIRONMENT_ATTEMPT_BUDGET = 4
# 같은 후보의 독립 검수 장애가 이어지면 다음 재검수를 물린다. n번째 연속 실패 뒤에는
# `관측 + min(1시간·2^(n-1), 24시간)` 이전에 다시 사지 않는다. 계수(`review_unavailable_total`)는
# KST 날을 넘어 누적되므로 아래의 일일 초기화가 이 물러서기를 지우지 않는다.
REVIEW_UNAVAILABLE_CODE = "CONTENT_AI_REVIEW_UNAVAILABLE"
# 하루 4회(`ENVIRONMENT_ATTEMPT_BUDGET`) 예산이 다음 KST 일에 다시 열리는 환경 원인. 공급자
# 장애는 몇 시간~하루면 풀리는데 평생 4회로 끝내면 하루짜리 장애가 사람의 일로 굳는다.
# 원고 미생성(CONTENT_NOT_GENERATED)은 게이트가 예산 없이 남긴 증상이다 — 유실된 배달을
# 다음 스윕이 다시 집게 한다.
DAILY_RESET_ENVIRONMENT_CODES = frozenset(
    {
        REVIEW_UNAVAILABLE_CODE,
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_FAILED",
        "CONTENT_NOT_GENERATED",
    }
)
# 일일 초기화 코드의 끝. 처음 관측한 KST 날부터 이 일수가 지나면 어떤 스윕도 다시 사지 않고
# 사람의 일이다(표본 실패의 `SAMPLE_EXHAUSTED_DAY_LIMIT`와 같은 3일). 검수 장애는 후보별 연속
# 실패 한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`)가 따로 끝을 소유하므로 뺀다.
ENVIRONMENT_EXHAUSTED_DAY_LIMIT = 3
ENVIRONMENT_SINCE_KEY = "environment_since"
WINDOWED_ENVIRONMENT_CODES = DAILY_RESET_ENVIRONMENT_CODES - {REVIEW_UNAVAILABLE_CODE}
REVIEW_UNAVAILABLE_BACKOFF_BASE = timedelta(hours=1)
REVIEW_UNAVAILABLE_BACKOFF_MAX = timedelta(hours=24)

# 표본(확률적) 실패 예산. LLM은 같은 입력에서도 매번 다른 출력을 낸다 — 한 번의 거절을
# "입력 변경 필요"로 굳히면 배포 전까지 슬롯이 비어 있게 된다. 대신 KST 하루 단위로
# 초기화되는 유한 예산을 주고, 소진된 날이 3일 쌓이면 그때 사람의 일로 올린다.
SAMPLE_BODY_DAILY_BUDGET = 2
SAMPLE_IMAGE_DAILY_BUDGET = 4
SAMPLE_EXHAUSTED_DAY_LIMIT = 3
# 임박 슬롯(예정일이 오늘·내일이거나 이미 지난 슬롯)의 본문 표본 예산은 KST 하루 한 세션 더 받는다.
# 발행까지 남은 스윕이 적어 하루 2세션으로는 같은 날 회복할 기회가 없다. 주제당·평생 상한
# (`generation_attempt_state`의 작가 6·검수 6·전송 36)은 그대로라 지출 끝은 바뀌지 않는다.
SAMPLE_BODY_URGENT_EXTRA = 1
URGENT_SLOT_LEAD_DAYS = 1
# 저장된 본문을 작가가 고칠 수 있는 코드(_AUTOMATIC_BODY_REPAIR_CODES)의 하루 세션 예산.
# 재시도 클래스와 분리해 계수한다 — 무조건 시도 기록을 지우면 결정적으로 고칠 수 없는
# 글이 하루 4회 유료 재생성을 영원히 반복한다.
BODY_REPAIR_DAILY_BUDGET = 2
# 저장된 본문을 작가가 스스로 고치는 코드. 이 코드들의 복구를 소유한 것은 재시도 클래스가
# 아니라 수리 세션 예산이므로, 기한·RETRYING·종착 판정이 모두 예산 상태를 본다.
BODY_REPAIR_STATE_KEY = "automatic_body_repair"
BODY_REPAIR_CODES = frozenset(
    {
        "FAQ_FIELDS_MISSING",
        "MISSING_REFERENCES",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "CONTENT_AI_REVIEW_STALE",
    }
)

# 사람이 정하는 참고자료 보류의 표시(`MISSING_REFERENCES` 시도 기록). 진료비·병원 선택 글은
# 공신력 있는 문서가 본질적으로 없어 작가가 다시 써도 풀리지 않는다 — 수리 세션 예산이 아니라
# 저장된 `OPERATOR_REQUIRED`가 이 보류를 소유한다(`reference_requirement.references_left_to_operator`).
OPERATOR_DECIDES_KEY = "operator_decides"

# 독립 검수 지적의 최소 교정 패스가 글(주제)당 상한을 다 쓴 표시(`CONTENT_AI_HARD_FINDING` 시도
# 기록). 이 표시가 있는 기록은 주제 교체 후보다 — 모델이 HARD로 단정한 지적이라도 교정을 이미
# 거쳤으므로 승인 자료 변경을 기다리지 않고 같은 슬롯·예정일로 다른 질문을 한 번 답하게 한다
# (`topic_swap_fallback.exhausted_body_sample_reason`). 교체 상한까지 쓰면 사람의 일이다.
AUTO_CORRECTION_EXHAUSTED_KEY = "auto_correction_exhausted"

# 모델이 HARD로 단정한 차단(`INPUT_CHANGE_REQUIRED`)인데 이번 실행에서 교정·재작성 어느 것도
# 맡지 못해 돈을 쓰지 않고 끝난 표시. 같은 입력으로 다시 집어도 같은 결과뿐이라 종착으로
# 본다 — 입력(지문)·사람 편집·승인된 병원 사실이 바뀌면 호출부가 다시 집는다. 쓰기와 읽기
# 모두 `CONTENT_HARD_FINDING_IDLE_TERMINAL`(기본 꺼짐) 뒤에 있다.
HARD_FINDING_IDLE_KEY = "hard_finding_idle"


# 결정적 생성 거절의 기계 라벨(2026-10-13 E5). 운영자 문구(`generation_run_control`)와 측정
# (`content_generation_review`의 `generation_rejections`)이 같은 판정을 쓴다. **순서가 판정이다** —
# 참고자료 거절 문구는 "GEO hard-fail: references is empty"라 `references`가 `geo`보다 먼저다.
GENERATION_REJECTION_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("truncated", ("truncated",)),
    ("price", ("unverified fixed price or coverage",)),
    ("references", ("references is empty",)),
    ("geo", ("geo hard-fail", "citable reference")),
    ("seo", ("seo hard-fail",)),
    ("forbidden", ("forbidden medical expressions",)),
    ("faq", ("faq output requires", "faq question must")),
    ("must_use", ("must_use messages missing verbatim",)),
    ("exam_title", ("exam title with treatment wording",)),
)
GENERATION_REJECTION_OTHER = "other"


def generation_rejection_kind(error: object) -> str:
    """결정적 거절 하나의 기계 라벨. 모르는 사유는 `other`다."""

    detail = str(error).casefold()
    for kind, markers in GENERATION_REJECTION_KINDS:
        if any(marker in detail for marker in markers):
            return kind
    return GENERATION_REJECTION_OTHER


class GenerationRetryClass(StrEnum):
    INPUT_CHANGE_REQUIRED = "INPUT_CHANGE_REQUIRED"
    ENVIRONMENT_RECOVERABLE = "ENVIRONMENT_RECOVERABLE"
    SAMPLE_RECOVERABLE = "SAMPLE_RECOVERABLE"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


_ENVIRONMENT_CODES = frozenset(
    {
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        "GENERATION_FAILED",
        "CONTENT_NOT_GENERATED",
    }
)
# 이미지 표본 실패. 하루 4회(야간 스윕 수)까지 재시도하고 다음 KST 일에 초기화된다.
SAMPLE_IMAGE_CODES = frozenset(
    {
        "IMAGE_GENERATION_FAILED",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
# 생성 예산 소진의 시도 기록 코드(`generation_run_control.classify_generation_failure`가 쓴다).
# 이 모듈은 다른 생성 모듈을 import하지 않는 잎이라 정본 상수를 여기 둔다.
GENERATION_BUDGET_EXHAUSTED_CODE = "GENERATION_BUDGET_EXHAUSTED"
# 본문 표본 실패. 작가 세션은 비싸므로 하루 2회로 제한한다.
SAMPLE_BODY_CODES = frozenset(
    {"GENERATION_REJECTED", "CONTENT_AI_HARD_FINDING", GENERATION_BUDGET_EXHAUSTED_CODE}
)
_SAMPLE_CODES = SAMPLE_IMAGE_CODES | SAMPLE_BODY_CODES
_INPUT_CODES = frozenset(
    {
        "MISSING_APPROVED_ESSENCE",
        "FORBIDDEN_EXPRESSION",
        "CONTENT_AI_REVIEW_STALE",
    }
)


def environment_attempt_period(observed: datetime | None = None) -> str:
    """Return the KST day that owns a bounded environment retry budget."""
    return (observed or datetime.now(UTC)).astimezone(KST).date().isoformat()


def stored_attempt_period(attempt: dict) -> str | None:
    raw_period = attempt.get("attempt_period")
    if isinstance(raw_period, str) and raw_period:
        return raw_period
    raw_observed = attempt.get("observed_at")
    if not isinstance(raw_observed, str):
        return None
    try:
        observed = datetime.fromisoformat(raw_observed)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return environment_attempt_period(observed)


def environment_window_start(attempt: Mapping) -> datetime | None:
    """일일 초기화 환경 원인의 3일 창이 시작된 시각. 창이 없는 원인이면 `None`.

    시작은 그 환경 장애 연속 구간을 처음 관측한 시각(`environment_since`)이다. 그 키가 없는
    레거시 기록은 마지막 관측일(`observed_at`)부터 3일 창을 받는다 — 같은 문맥의 옛 표본 실패
    시각을 물려받을 수 있는 `first_observed_at`으로 보면 이미 닫힌 창으로 읽힌다. 읽는 쪽
    (`environment_window_last_day`)과 잇는 쪽(`tasks._remember_generation_attempt`)이 함께 쓴다.
    """

    if attempt.get("reason") not in WINDOWED_ENVIRONMENT_CODES:
        return None
    for key in (ENVIRONMENT_SINCE_KEY, "observed_at"):
        raw = attempt.get(key)
        if not isinstance(raw, str):
            continue
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return started
    return None


def environment_window_last_day(attempt: Mapping) -> date | None:
    """일일 초기화 환경 원인이 자동 재시도를 살 수 있는 마지막 KST 날. 상한이 없으면 `None`."""

    started = environment_window_start(attempt)
    if started is None:
        return None
    return started.astimezone(KST).date() + timedelta(days=ENVIRONMENT_EXHAUSTED_DAY_LIMIT - 1)


def environment_window_closed(attempt: Mapping, now: datetime | None = None) -> bool:
    """일일 초기화 환경 원인의 3일 창이 끝났는가."""

    last_day = environment_window_last_day(attempt)
    if last_day is None:
        return False
    return (now or datetime.now(UTC)).astimezone(KST).date() > last_day


def review_unavailable_backoff(failures: int) -> timedelta:
    """n번째 연속 검수 장애 뒤 다음 재검수까지의 최소 간격."""

    if failures < 1:
        return timedelta(0)
    # 2^5시간이 이미 상한(24시간)을 넘는다 — 지수를 묶어 큰 n에서도 넘치지 않게 한다.
    return min(
        REVIEW_UNAVAILABLE_BACKOFF_BASE * 2 ** min(failures - 1, 5),
        REVIEW_UNAVAILABLE_BACKOFF_MAX,
    )


def review_unavailable_not_before(attempt: Mapping) -> datetime | None:
    """검수 장애 기록이 다음 재검수를 허락하는 가장 이른 시각. 계수가 없는 기록은 `None`."""

    if attempt.get("reason") != REVIEW_UNAVAILABLE_CODE:
        return None
    try:
        failures = int(attempt.get("review_unavailable_total") or 0)
    except (TypeError, ValueError):
        return None
    raw_observed = attempt.get("observed_at")
    if failures < 1 or not isinstance(raw_observed, str):
        return None
    try:
        observed = datetime.fromisoformat(raw_observed)
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return observed + review_unavailable_backoff(failures)


def has_model_declared_hard_finding(review: object) -> bool:
    """Return whether the stored review itself declared a HARD finding.

    확신도 부족으로 붙는 합성 finding은 UNCERTAIN이다. 모델이 HARD로 단정한 사실·
    의료 안전 지적만 입력(승인 자료) 변경을 요구하는 종착으로 취급한다.
    """

    if not isinstance(review, Mapping):
        return False
    findings = review.get("findings")
    if not isinstance(findings, list):
        return False
    return any(
        isinstance(finding, Mapping)
        and str(finding.get("severity") or "").upper() == "HARD"
        for finding in findings
    )


def generation_lookahead_days() -> int:
    """생성 스윕이 앞으로 내다보는 일수(리드 타임). 야간 배치·복구 스윕·기한 계산이 같은 값을 쓴다."""

    return max(int(settings.NIGHTLY_GENERATION_LOOKAHEAD_DAYS), 1)


def slot_is_urgent(scheduled_date: date | None, observed: datetime | None = None) -> bool:
    """예정일이 오늘(KST)+1일 이내이거나 이미 지났는가. 예정일을 모르면 임박이 아니다."""

    if scheduled_date is None:
        return False
    today = (observed or datetime.now(UTC)).astimezone(KST).date()
    return scheduled_date <= today + timedelta(days=URGENT_SLOT_LEAD_DAYS)


def sample_daily_budget(
    reason: object,
    *,
    scheduled_date: date | None = None,
    observed: datetime | None = None,
) -> int:
    """한 슬롯이 KST 하루에 살 수 있는 표본 시도 수. 판정의 정본은 이 함수 하나다.

    이미지 후보는 싸고 유한하다. 작가 세션은 비싸므로 하루 2회이고, 임박 슬롯
    (`slot_is_urgent`)만 한 세션 더 받는다. 임박 여부는 KST 하루 안에서 바뀌지 않으므로 같은
    날의 계수·소진 판정이 서로 어긋나지 않는다.
    """

    if str(reason or "") in SAMPLE_IMAGE_CODES:
        return SAMPLE_IMAGE_DAILY_BUDGET
    if slot_is_urgent(scheduled_date, observed):
        return SAMPLE_BODY_DAILY_BUDGET + SAMPLE_BODY_URGENT_EXTRA
    return SAMPLE_BODY_DAILY_BUDGET


def retry_class_for(
    code: str, *, model_declared_hard: bool = False
) -> GenerationRetryClass:
    if code in _ENVIRONMENT_CODES:
        return GenerationRetryClass.ENVIRONMENT_RECOVERABLE
    if code == "CONTENT_AI_HARD_FINDING" and model_declared_hard:
        # 모델이 HARD로 단정한 사실·안전 지적은 삭제형 재작성까지 마친 뒤의 종착이다.
        return GenerationRetryClass.INPUT_CHANGE_REQUIRED
    if code in _SAMPLE_CODES:
        return GenerationRetryClass.SAMPLE_RECOVERABLE
    if code in _INPUT_CODES:
        return GenerationRetryClass.INPUT_CHANGE_REQUIRED
    return GenerationRetryClass.OPERATOR_REQUIRED


def next_recovery_sweep(now: datetime | None = None) -> datetime:
    observed = (now or datetime.now(UTC)).astimezone(KST)
    for hour in RECOVERY_SWEEP_HOURS:
        candidate = observed.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > observed:
            return candidate.astimezone(UTC)
    return (observed + timedelta(days=1)).replace(
        hour=RECOVERY_SWEEP_HOURS[0], minute=0, second=0, microsecond=0
    ).astimezone(UTC)


def _sample_budget_is_spent(
    attempt: dict, observed: datetime, scheduled_date: date | None = None
) -> bool:
    """Whether today's KST sample budget for this slot is already spent."""

    if stored_attempt_period(attempt) != environment_attempt_period(observed):
        return False
    try:
        count = int(attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0)
    except (TypeError, ValueError):
        return True
    return count >= sample_daily_budget(
        attempt.get("reason"), scheduled_date=scheduled_date, observed=observed
    )


def _sweep_window(candidate: datetime) -> tuple[date, date]:
    """Return the scheduled-date window one sweep at ``candidate`` actually claims."""

    day = candidate.date()
    lookahead = generation_lookahead_days()
    if candidate.hour == NIGHTLY_SWEEP_HOUR:
        return day + timedelta(days=1), day + timedelta(days=lookahead)
    # 복구 스윕의 앞쪽 끝은 야간 배치와 같다. 22:30 백로그 복구가 오래된 슬롯을 **미래**
    # 날짜로 옮기므로, 앞쪽을 `오늘`에서 끊으면 방금 구조한 바로 그 슬롯을 복구 스윕이
    # 다시 집지 못한다.
    return (
        day - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS),
        day + timedelta(days=lookahead),
    )


def sweep_claims_slot(moment: datetime, scheduled_date: date | None) -> bool:
    """``moment``가 생성 스윕 시각이고 그 스윕의 창에 ``scheduled_date``가 드는가.

    `next_recovery_deadline`의 백로그 복구 판정 시각(22:30 + 1시간)은 원고 생성 시도가 아니다 —
    그 시각을 "다시 시도합니다"로 말하지 않게 문구 쪽이 구분할 때 쓴다.
    """

    if scheduled_date is None:
        return False
    observed = moment.astimezone(KST)
    if observed.hour not in RECOVERY_SWEEP_HOURS or observed != observed.replace(
        minute=0, second=0, microsecond=0
    ):
        return False
    window_start, window_end = _sweep_window(observed)
    return window_start <= scheduled_date <= window_end


def _candidate_sweeps(observed: datetime):
    """Yield the scheduled sweep datetimes after ``observed``, in time order."""

    for offset in range(RECOVERY_DEADLINE_HORIZON_DAYS + 1):
        day = observed + timedelta(days=offset)
        for hour in sorted(RECOVERY_SWEEP_HOURS):
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > observed:
                yield candidate


def _next_sweep_catchup_start(observed: datetime) -> date:
    """The oldest scheduled date any *future* catch-up sweep can still claim."""

    return (
        observed.astimezone(KST).date()
        + timedelta(days=1)
        - timedelta(days=RECOVERY_SWEEP_CATCHUP_DAYS)
    )


def _next_backlog_recovery_deadline(observed: datetime) -> datetime:
    """Return when the 22:30 backlog recovery will have had its turn."""

    run = observed.replace(
        hour=BACKLOG_RECOVERY_HOUR,
        minute=BACKLOG_RECOVERY_MINUTE,
        second=0,
        microsecond=0,
    )
    if run <= observed:
        run += timedelta(days=1)
    return (run + BACKLOG_RECOVERY_GRACE).astimezone(UTC)


def next_repair_session_date(
    repair_state: Mapping | None, now: datetime | None = None
) -> date | None:
    """Return the first KST date that can still buy one automatic repair session.

    오늘 예산이 남았으면 오늘, 오늘 것만 소진됐으면 내일, 소진된 날이 상한만큼 쌓였으면
    `None`(사람의 일)이다.
    """

    observed = (now or datetime.now(UTC)).astimezone(KST)
    today = observed.date()
    if repair_session_is_available(repair_state, observed):
        return today
    try:
        exhausted_days = int((repair_state or {}).get("exhausted_days") or 0)
    except (TypeError, ValueError):
        return None
    if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
        return None
    return today + timedelta(days=1)


def repair_recovery_remains(
    repair_state: Mapping | None, now: datetime | None = None
) -> bool:
    """Whether automatic body repair still owns this blocker (today or later)."""

    return next_repair_session_date(repair_state, now) is not None


def _earliest_eligible_date(
    attempt: dict,
    observed: datetime,
    repair_state: Mapping | None = None,
    scheduled_date: date | None = None,
) -> date | None:
    """Return the first KST date whose budget can still buy one attempt.

    ``None``은 "남은 예산이 없다"는 뜻이다(종착). 오늘 예산만 소진된 경우에는 내일을
    돌려준다 — 예산 때문에 건너뛴 슬롯의 기한을 스윕마다 미루지 않기 위해, 저장 시점에
    실제로 시도할 수 있는 첫 날을 한 번만 정한다.
    """

    retry_class = attempt.get("retry_class")
    today = observed.astimezone(KST).date()
    if (
        attempt.get("reason") in BODY_REPAIR_CODES
        and retry_class != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
        and not attempt.get(OPERATOR_DECIDES_KEY)
    ):
        # 이 코드들의 복구는 재시도 클래스가 아니라 수리 세션 예산이 소유한다
        # (`retry_class_for`의 기본값은 다른 용도로 그대로 둔다). 다만 승인된 입력 자체가
        # 틀렸다는 판정(INPUT_CHANGE_REQUIRED)과 사람이 정하는 참고자료 보류는 작가 세션으로
        # 고칠 수 없는 종착이다.
        return next_repair_session_date(repair_state, observed)
    if retry_class == GenerationRetryClass.SAMPLE_RECOVERABLE.value:
        try:
            exhausted_days = int(attempt.get("exhausted_days") or 0)
        except (TypeError, ValueError):
            return None
        if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
            return None
        if _sample_budget_is_spent(attempt, observed, scheduled_date):
            return today + timedelta(days=1)
        return today
    if retry_class != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
        return None
    try:
        count = int(
            attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0
        )
    except (TypeError, ValueError):
        return None
    if count < ENVIRONMENT_ATTEMPT_BUDGET:
        earliest = today
    elif attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES:
        # 소진된 예산이 어제 것이면 오늘 이미 초기화됐다. 하루를 더 미루면 새 예산의
        # 하루를 통째로 버린다.
        if stored_attempt_period(attempt) != environment_attempt_period(observed):
            earliest = today
        else:
            earliest = today + timedelta(days=1)
    else:
        return None
    last_day = environment_window_last_day(attempt)
    if last_day is not None and earliest > last_day:
        return None
    return earliest


def next_recovery_deadline(
    attempt: dict,
    *,
    scheduled_date: date | None,
    now: datetime | None = None,
    repair_state: Mapping | None = None,
) -> datetime | None:
    """Return when a sweep will actually pick this slot up again, or ``None``.

    스윕마다 창이 다르므로 "다음 스윕 시각"은 기한이 아니다. 후보 시각을 시간순으로
    열거하고, 그 스윕의 창에 이 슬롯의 예정일이 드는 첫 시각을 고른다. 어떤 후보도
    집지 않거나 재시도 예산이 끝났으면 `None`(사람의 일)이다.

    catch-up 창보다 오래된 슬롯은 예외다. 스윕이 아니라 22:30 백로그 복구가 소유하므로
    그 실행 뒤(+1시간)를 기한으로 준다. 복구가 날짜를 옮기면 새 예정일로 시도·기한이
    다시 서고, 옮기지 못하면 그 시각에 사람에게 보이는 것이 옳은 신호다 — 기한 없는
    RETRYING으로 영원히 숨지 않는다.
    """

    if scheduled_date is None:
        return None
    observed = (now or datetime.now(UTC)).astimezone(KST)
    earliest_date = _earliest_eligible_date(attempt, observed, repair_state, scheduled_date)
    if earliest_date is None:
        return None
    not_before = review_unavailable_not_before(attempt)
    last_day = environment_window_last_day(attempt)
    for candidate in _candidate_sweeps(observed):
        if candidate.date() < earliest_date:
            continue
        if last_day is not None and candidate.date() > last_day:
            # 3일 창 밖의 스윕은 이 기록을 집지 않는다(`retry_is_due`). catch-up 창보다 오래된
            # 슬롯의 백로그 복구 판정은 아래에서 그대로 준다.
            break
        if not_before is not None and candidate < not_before:
            continue
        window_start, window_end = _sweep_window(candidate)
        if window_start <= scheduled_date <= window_end:
            return candidate.astimezone(UTC)
    # 기준은 오늘의 catch-up 창이 아니라 **다음 스윕이 쓸 창**이다. 오늘 01·04·07이
    # 집었던 창의 첫날(오늘-7)은 내일 창에서 빠지므로, 그날 예정된 슬롯이 마지막 스윕
    # 뒤에 실패하면 어떤 후보 스윕도 다시 담지 못한다. 기준을 오늘 창으로 두면 그 하루가
    # 스윕에도 백로그 복구에도 속하지 않아 `next_retry_at`이 `None`으로 굳고, 표본 실패는
    # 소진 일수가 더 쌓이지 않아 주제 교체로 넘어갈 계단조차 열리지 않는다.
    if scheduled_date < _next_sweep_catchup_start(observed):
        return _next_backlog_recovery_deadline(observed)
    return None


def sample_budget_spent(
    previous: Mapping | None,
    reason: str,
    observed: datetime,
    *,
    scheduled_date: date | None = None,
) -> tuple[int, int]:
    """Return the (daily count, exhausted days) after spending one sample attempt.

    같은 지문 안에서만 누적한다. 날이 바뀌면 하루 예산은 초기화되고, 그날의 예산을
    막 소진한 순간에만 `exhausted_days`가 1 오른다(하루에 두 번 오르지 않는다). 그날의
    예산은 `sample_daily_budget`이 정한다(임박 슬롯은 한 세션 더).
    """

    stored = dict(previous or {})
    period = environment_attempt_period(observed)
    same_period = stored_attempt_period(stored) == period
    try:
        count = int(stored.get("provider_attempt_count") or 0) if same_period else 0
    except (TypeError, ValueError):
        count = 0
    try:
        exhausted_days = int(stored.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        exhausted_days = 0
    count += 1
    if count == sample_daily_budget(reason, scheduled_date=scheduled_date, observed=observed):
        exhausted_days += 1
    return count, exhausted_days


def repair_session_is_available(
    state: Mapping | None,
    now: datetime | None = None,
    *,
    daily_budget: int = BODY_REPAIR_DAILY_BUDGET,
) -> bool:
    """Return whether one more automatic body-repair session fits today's budget."""

    if not isinstance(state, Mapping):
        return True
    try:
        if int(state.get("exhausted_days") or 0) >= SAMPLE_EXHAUSTED_DAY_LIMIT:
            return False
    except (TypeError, ValueError):
        return False
    if state.get("period") != environment_attempt_period(now):
        return True
    try:
        return int(state.get("count") or 0) < daily_budget
    except (TypeError, ValueError):
        return False


def spend_repair_session(
    state: Mapping | None,
    now: datetime | None = None,
    *,
    daily_budget: int = BODY_REPAIR_DAILY_BUDGET,
) -> dict[str, object]:
    """Persistable counters after one automatic body-repair session is spent."""

    observed = now or datetime.now(UTC)
    period = environment_attempt_period(observed)
    stored = dict(state) if isinstance(state, Mapping) else {}
    try:
        count = int(stored.get("count") or 0) if stored.get("period") == period else 0
    except (TypeError, ValueError):
        count = 0
    try:
        exhausted_days = int(stored.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        exhausted_days = 0
    count += 1
    if count == daily_budget:
        exhausted_days += 1
    first_observed_at = stored.get("first_observed_at")
    return {
        "period": period,
        "count": count,
        "exhausted_days": exhausted_days,
        "first_observed_at": (
            first_observed_at
            if isinstance(first_observed_at, str)
            else observed.isoformat()
        ),
    }


def _sample_retry_is_due(
    attempt: dict, observed: datetime, scheduled_date: date | None = None
) -> bool:
    try:
        exhausted_days = int(attempt.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        return False
    if exhausted_days >= SAMPLE_EXHAUSTED_DAY_LIMIT:
        # 3일 소진은 OPERATOR_REQUIRED 전이와 같은 종착이다.
        return False
    if stored_attempt_period(attempt) != environment_attempt_period(observed):
        # 새 KST 일에는 예산이 초기화된다. 다만 저장된 다음 시도 시각은 그대로 지킨다 —
        # 날이 바뀌었다는 이유로 앞당기면 그 시각을 약속한 인시던트 기한과 어긋난다.
        return _due_time_reached(attempt, observed)
    if _sample_budget_is_spent(attempt, observed, scheduled_date):
        return False
    return _due_time_reached(attempt, observed)


def _due_time_reached(attempt: dict, observed: datetime) -> bool:
    raw_due = attempt.get("next_retry_at")
    if raw_due is None and "next_retry_at" in attempt:
        # 어떤 스윕도 이 슬롯을 집지 않는다고 판정해 저장한 결정이다. 시각만으로
        # 되살리면 창 밖의 슬롯을 매 스윕이 다시 사게 된다. (키가 아예 없는 레거시
        # 기록은 종전대로 시각 제한 없이 한 번 더 시도한다.)
        return False
    if not isinstance(raw_due, str):
        return True
    try:
        due = datetime.fromisoformat(raw_due)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return observed >= due


def _sweep_window_was_abandoned(attempt: Mapping) -> bool:
    """저장된 기록이 "어떤 스윕도 이 슬롯을 다시 집지 않는다"로 굳었는가.

    `next_retry_at`을 명시적으로 `None`으로 저장한 것이 그 결정이다. 키가 아예 없는
    레거시 기록은 결정을 내린 적이 없으므로 여기 해당하지 않는다.
    """

    return "next_retry_at" in attempt and attempt.get("next_retry_at") is None


def retry_is_due(
    attempt: dict,
    now: datetime | None = None,
    *,
    scheduled_date: date | None = None,
) -> bool:
    """저장된 기록의 다음 시도가 지금 됐는가. ``scheduled_date``는 표본 하루 예산의 임박 판정에만 쓴다
    (모르면 기본 예산 — 더 적게 산다)."""

    observed = now or datetime.now(UTC)
    retry_class = attempt.get("retry_class")
    if retry_class == GenerationRetryClass.SAMPLE_RECOVERABLE.value:
        return _sample_retry_is_due(attempt, observed, scheduled_date)
    if retry_class != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
        return False
    if environment_window_closed(attempt, observed):
        # 3일 창이 끝났다. 아래의 일일 초기화 되살리기보다 앞선다.
        return False
    legacy_count = (
        0 if attempt.get("reason") == "COST_BLOCKED" else attempt.get("attempt_count")
    )
    try:
        count = int(attempt.get("provider_attempt_count", legacy_count) or 0)
    except (TypeError, ValueError):
        return False
    budget_reset_due = (
        attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES
        and stored_attempt_period(attempt) != environment_attempt_period(observed)
    )
    # 일일 초기화 원인(검수 장애·공급자 장애·원고 미생성)은 다음 KST 날에 새 하루 예산을 받아
    # 이미 기한이 된 슬롯이 영영 비지 않는다. 공급자 장애·원고 미생성의 끝은 위의 3일 창
    # (`environment_window_closed`), 검수 장애의 끝은 후보별 연속 실패 한도다. 그 밖의 환경
    # 원인(COST_BLOCKED 등)은 유한한 H-08 예산 그대로다.
    if count >= ENVIRONMENT_ATTEMPT_BUDGET and not budget_reset_due:
        return False
    not_before = review_unavailable_not_before(attempt)
    if not_before is not None and observed < not_before:
        # 검수 장애의 물러서기는 일일 초기화보다 앞선다 — 새 날이라고 더 일찍 사지 않는다.
        return False
    if budget_reset_due and _sweep_window_was_abandoned(attempt):
        # 어제 저장한 "집을 스윕이 없다"는 예측보다, 오늘의 새 예산이 나중에 내려진
        # 결정이다. 게다가 이 판정을 묻는 것은 이미 이 행을 claim한 스윕이므로 창
        # 밖이라는 전제 자체가 틀렸다. 그 예측을 그대로 지키면 하루짜리 검수 장애가
        # 영구 차단으로 굳는다 — 검수를 다시 사지 않으니 저장된 판정도 영원히 그대로다.
        return True
    return _due_time_reached(attempt, observed)


def attempt_is_terminal(attempt: Mapping | None) -> bool:
    """저장된 기록이 "사람 또는 입력 변경만 풀 수 있다"로 굳은 종착인가.

    스윕이 이 기록의 슬롯을 다시 집어 봐야 작가·검수 공급자 호출은 이미 예산이 끝났고,
    OperationRun·인시던트 쓰기만 매 스윕(하루 7번) 반복된다. 판정 범위는 **재집기가 아무것도
    풀지 못하는 코드**로만 좁힌다:

    - 본문 표본 실패(`SAMPLE_BODY_CODES`)가 `OPERATOR_REQUIRED`까지 소진된 것 — 주제 교체는
      별도 pass(`topic_swap_fallback`)가 소유한다.
    - 사람이 정하는 참고자료 보류(`OPERATOR_DECIDES_KEY`).
    - 승인된 Essence가 없는 `MISSING_APPROVED_ESSENCE`(입력 지문이 바뀌어야 풀린다).

    `INPUT_CHANGE_REQUIRED`인 `CONTENT_AI_HARD_FINDING`은 뺀다 — 최소 교정 패스가 아직 그 지적을
    풀 수 있는 단계다. 단 직전 실행이 아무것도 맡지 못하고 끝났다는 표시(`HARD_FINDING_IDLE_KEY`)가
    있고 `CONTENT_HARD_FINDING_IDLE_TERMINAL`이 켜져 있으면 종착이다. 그 밖에 본문 수리 코드
    (`BODY_REPAIR_CODES`)·이미지 코드는 재집기가
    실제로 일을 한다. 입력이 그대로인지는 호출부가 지문(`context`)으로 따로 확인한다.
    """

    if not isinstance(attempt, Mapping):
        return False
    reason = attempt.get("reason")
    retry_class = attempt.get("retry_class")
    if retry_class == GenerationRetryClass.OPERATOR_REQUIRED.value:
        return reason in SAMPLE_BODY_CODES or (
            reason == "MISSING_REFERENCES" and bool(attempt.get(OPERATOR_DECIDES_KEY))
        )
    if retry_class != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value:
        return False
    return reason == "MISSING_APPROVED_ESSENCE" or (
        reason == "CONTENT_AI_HARD_FINDING"
        and bool(attempt.get(HARD_FINDING_IDLE_KEY))
        and settings.CONTENT_HARD_FINDING_IDLE_TERMINAL
    )


def recovery_is_abandoned(attempt: Mapping | None, now: datetime | None = None) -> bool:
    """예약 복구가 이 기록을 더 집지 않는가. 인시던트가 RETRYING을 말할 자격의 기준."""

    if not isinstance(attempt, Mapping) or not _sweep_window_was_abandoned(attempt):
        return False
    return not retry_is_due(dict(attempt), now)
