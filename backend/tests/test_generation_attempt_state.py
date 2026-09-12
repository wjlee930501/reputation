"""저장된 이미지 시도 기록의 해석 — "오늘은 더 살 것이 없다"를 누가 판정하는가.

이 판정이 틀리면 두 가지가 잘못된다. 너무 일찍 소진이라고 하면 아직 성공할 수 있는
슬롯이 남의 이미지를 빌리고, 영영 소진이 아니라고 하면 본문이 완성된 글이 매일
`CONTENT_IMAGE_NOT_READY`로 막혀 계약 월을 넘긴다.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    image_attempts_exhausted_today,
    read_generation_attempt,
)
from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    environment_attempt_period,
)

_NOW = datetime(2026, 9, 12, 4, 30, tzinfo=timezone.utc)  # KST 13:30


def _item(attempt=None, summary=None):
    if summary is None:
        summary = {} if attempt is None else {GENERATION_ATTEMPT_KEY: attempt}
    return SimpleNamespace(essence_check_summary=summary)


def _attempt(reason: str, *, count: int, observed: datetime = _NOW, **extra):
    payload = {
        "reason": reason,
        "observed_at": observed.isoformat(),
        "attempt_period": environment_attempt_period(observed),
        "retry_class": "SAMPLE_RECOVERABLE",
        "attempt_count": count,
        "provider_attempt_count": count,
    }
    payload.update(extra)
    return payload


def test_missing_or_malformed_fragments_read_as_empty():
    assert read_generation_attempt(SimpleNamespace(essence_check_summary=None)) == {}
    assert read_generation_attempt(_item(summary={"generation_attempt": "nope"})) == {}
    assert read_generation_attempt(_item(summary={})) == {}


def test_unknown_extra_fields_are_preserved_and_never_raise():
    attempt = _attempt("IMAGE_GENERATION_FAILED", count=1, future_field={"a": 1})
    stored = read_generation_attempt(_item(attempt))

    assert stored["future_field"] == {"a": 1}
    # 반환값은 사본이다 — 호출부가 만져도 저장된 조각이 바뀌지 않는다.
    stored["reason"] = "CHANGED"
    assert read_generation_attempt(_item(attempt))["reason"] == "IMAGE_GENERATION_FAILED"


def test_no_attempt_means_the_sweeps_still_own_the_image():
    assert image_attempts_exhausted_today(_item(), now=_NOW) is False


def test_cost_blocked_never_counts_as_exhausted():
    """비용 가드 보류는 공급자를 한 번도 부르지 못한 상태다 — 예산을 쓴 적이 없다."""
    attempt = _attempt("COST_BLOCKED", count=ENVIRONMENT_ATTEMPT_BUDGET)

    assert image_attempts_exhausted_today(_item(attempt), now=_NOW) is False


def test_body_failures_are_not_image_failures():
    attempt = _attempt("GENERATION_REJECTED", count=ENVIRONMENT_ATTEMPT_BUDGET)

    assert image_attempts_exhausted_today(_item(attempt), now=_NOW) is False


def test_terminal_image_reasons_are_exhausted_regardless_of_the_counter():
    for reason in ("IMAGE_GENERATION_RETRIES_EXHAUSTED", "CONTENT_IMAGE_POLICY_REJECTED"):
        assert image_attempts_exhausted_today(_item(_attempt(reason, count=0)), now=_NOW) is True


def test_budget_is_exhausted_only_after_the_whole_days_allowance():
    under = _attempt("IMAGE_GENERATION_FAILED", count=ENVIRONMENT_ATTEMPT_BUDGET - 1)
    spent = _attempt("IMAGE_GENERATION_FAILED", count=ENVIRONMENT_ATTEMPT_BUDGET)

    assert image_attempts_exhausted_today(_item(under), now=_NOW) is False
    assert image_attempts_exhausted_today(_item(spent), now=_NOW) is True


def test_yesterdays_exhaustion_is_not_todays_exhaustion():
    """하루 예산은 KST 일 단위로 다시 열린다 — 어제 소진은 오늘 한 번 더 시도한다."""
    yesterday = _attempt(
        "IMAGE_GENERATION_FAILED",
        count=ENVIRONMENT_ATTEMPT_BUDGET,
        observed=_NOW - timedelta(days=1),
    )

    assert image_attempts_exhausted_today(_item(yesterday), now=_NOW) is False


def test_unreadable_counter_is_not_read_as_exhausted():
    attempt = _attempt("IMAGE_GENERATION_FAILED", count=0)
    attempt["provider_attempt_count"] = "many"

    assert image_attempts_exhausted_today(_item(attempt), now=_NOW) is False


def test_legacy_fragments_without_a_period_fall_back_to_the_observed_day():
    attempt = _attempt("IMAGE_GENERATION_FAILED", count=ENVIRONMENT_ATTEMPT_BUDGET)
    attempt.pop("attempt_period")

    assert image_attempts_exhausted_today(_item(attempt), now=_NOW) is True
