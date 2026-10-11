"""공급자 장애 예산은 하루 단위로 다시 열리고 3일 뒤에 사람의 일이 된다.

종전에는 PROVIDER_TIMEOUT·PROVIDER_UNAVAILABLE·GENERATION_FAILED의 4회 예산이 슬롯 평생
한 번이라, 하루짜리 OpenRouter 장애가 끝나도 그 슬롯을 어떤 스윕도 다시 쓰지 않았다(감사 F9).
이제 검수 장애(CONTENT_AI_REVIEW_UNAVAILABLE)처럼 KST 날마다 4회가 다시 열리고, 같은 환경
장애 구간을 처음 관측한 날부터 3일(`ENVIRONMENT_EXHAUSTED_DAY_LIMIT`)이 지나면 끝난다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from test_gate_record_stale_context import _empty_slot, _loader_takes, _remember
from test_topic_swap_fallback import _kst

from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    ENVIRONMENT_SINCE_KEY,
    GenerationRetryClass,
    environment_window_closed,
    next_recovery_deadline,
    recovery_is_abandoned,
    retry_is_due,
)

_SLOT = date(2026, 9, 16)
_DAY1 = (_kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _kst(2026, 9, 16, 7), _kst(2026, 9, 16, 12))


def _env(reason: str, count: int, *, period: str, since: datetime, **extra) -> dict:
    return {
        "reason": reason,
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": count,
        "attempt_period": period,
        ENVIRONMENT_SINCE_KEY: since.isoformat(),
        **extra,
    }


@pytest.mark.parametrize("reason", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "GENERATION_FAILED"])
def test_a_spent_provider_budget_reopens_the_next_kst_day(reason):
    spent = _env(reason, ENVIRONMENT_ATTEMPT_BUDGET, period="2026-09-16", since=_kst(2026, 9, 16, 1))

    due = next_recovery_deadline(spent, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5))

    assert due == _kst(2026, 9, 17, 1).astimezone(UTC)


def test_the_window_ends_after_three_kst_days():
    since = _kst(2026, 9, 14, 1)
    spent_on_last_day = _env(
        "PROVIDER_TIMEOUT", ENVIRONMENT_ATTEMPT_BUDGET, period="2026-09-16", since=since
    )

    assert (
        next_recovery_deadline(spent_on_last_day, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5))
        is None
    )
    # 마지막 날에 예산이 남았으면 그날 안의 스윕까지만 준다.
    one_left = _env("PROVIDER_TIMEOUT", 1, period="2026-09-16", since=since)
    assert next_recovery_deadline(
        one_left, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5)
    ) == _kst(2026, 9, 16, 18).astimezone(UTC)
    assert next_recovery_deadline(one_left, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 22, 5)) is None


def test_a_closed_window_is_not_revived_by_the_daily_reset():
    abandoned = _env(
        "PROVIDER_UNAVAILABLE",
        ENVIRONMENT_ATTEMPT_BUDGET,
        period="2026-09-16",
        since=_kst(2026, 9, 14, 1),
        next_retry_at=None,
    )

    assert environment_window_closed(abandoned, _kst(2026, 9, 17, 1)) is True
    assert retry_is_due(abandoned, _kst(2026, 9, 17, 1)) is False
    assert recovery_is_abandoned(abandoned, _kst(2026, 9, 17, 1)) is True


def test_the_review_outage_keeps_its_own_cap_and_has_no_three_day_window():
    review = _env(
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        ENVIRONMENT_ATTEMPT_BUDGET,
        period="2026-09-16",
        since=_kst(2026, 9, 1, 1),
    )

    assert environment_window_closed(review, _kst(2026, 9, 17, 1)) is False
    assert next_recovery_deadline(
        review, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5)
    ) == _kst(2026, 9, 17, 1).astimezone(UTC)


def test_the_worker_counts_per_day_and_carries_the_window_start(monkeypatch):
    philosophy, item = _empty_slot(monkeypatch)
    for moment in _DAY1:
        day1 = _remember(monkeypatch, item, philosophy, moment, "PROVIDER_TIMEOUT")
    assert day1["provider_attempt_count"] == ENVIRONMENT_ATTEMPT_BUDGET
    assert day1["next_retry_at"] == _kst(2026, 9, 17, 1).astimezone(UTC).isoformat()
    assert day1[ENVIRONMENT_SINCE_KEY] == _DAY1[0].astimezone(UTC).isoformat()

    # 다음 날 첫 실패는 1회차다. 원인이 바뀌어도 창의 시작은 그대로 잇는다.
    day2 = _remember(monkeypatch, item, philosophy, _kst(2026, 9, 17, 1), "GENERATION_FAILED")
    assert day2["provider_attempt_count"] == 1
    assert day2[ENVIRONMENT_SINCE_KEY] == day1[ENVIRONMENT_SINCE_KEY]
    assert _loader_takes(monkeypatch, item, _kst(2026, 9, 17, 4))


def test_the_worker_hands_over_after_three_days(monkeypatch):
    philosophy, item = _empty_slot(monkeypatch)
    for day in (16, 17, 18):
        for hour in (1, 4, 7, 12):
            last = _remember(monkeypatch, item, philosophy, _kst(2026, 9, day, hour), "PROVIDER_TIMEOUT")

    assert last["next_retry_at"] is None
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 19, 1))
    assert recovery_is_abandoned(last, _kst(2026, 9, 19, 1)) is True
