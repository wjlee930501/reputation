"""수율 집계의 판정 규칙 — DB 없이 고정한다.

SQL 경계는 tests/integration/test_content_yield_postgres.py가 본다. 여기서는 행을
사실로 접는 순수 규칙만 본다: 무엇이 분모이고 무엇이 분자인지, 그리고 `RETRYING`을
사람의 일로 세지 않는다는 계약이다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.content import ContentStatus
from app.services.content_yield import fold_yield_rows, kst_week_start
from app.workers.generation_retry_policy import GenerationRetryClass

KST = timezone(timedelta(hours=9))
WEEK_START = date(2026, 9, 7)
WEEK_END = date(2026, 9, 14)
HOSPITAL_ID = uuid.uuid4()
HOSPITALS = [(HOSPITAL_ID, "수율의원")]


def _row(
    *,
    scheduled_date: date = WEEK_START,
    status=ContentStatus.DRAFT,
    first_published_at: datetime | None = None,
    reused_from: uuid.UUID | None = None,
    summary: dict | None = None,
    hospital_id: uuid.UUID = HOSPITAL_ID,
) -> SimpleNamespace:
    return SimpleNamespace(
        hospital_id=hospital_id,
        scheduled_date=scheduled_date,
        status=status,
        first_published_at=first_published_at,
        image_reused_from_content_id=reused_from,
        essence_check_summary=summary,
    )


def _attempt(reason: str, retry_class: str, **extra) -> dict:
    return {"generation_attempt": {"reason": reason, "retry_class": retry_class, **extra}}


def _fold(rows) -> SimpleNamespace:
    facts = fold_yield_rows(
        HOSPITALS, rows, period_start=WEEK_START, period_end=WEEK_END
    )
    return facts[0]


def test_cancelled_slots_leave_the_contract_denominator() -> None:
    fact = _fold(
        [
            _row(),
            _row(scheduled_date=WEEK_START + timedelta(days=1), status=ContentStatus.CANCELLED),
        ]
    )

    assert fact.due == 1


def test_retrying_is_not_operator_work() -> None:
    fact = _fold(
        [
            _row(
                summary=_attempt(
                    "GENERATION_REJECTED", GenerationRetryClass.SAMPLE_RECOVERABLE.value
                )
            ),
            _row(
                scheduled_date=WEEK_START + timedelta(days=1),
                summary=_attempt(
                    "CONTENT_IMAGE_NOT_READY",
                    GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
                ),
            ),
        ]
    )

    assert (fact.retrying, fact.operator_required, fact.blocked) == (2, 0, 2)


def test_exhausted_sample_budget_becomes_operator_work() -> None:
    fact = _fold(
        [
            _row(
                summary=_attempt(
                    "GENERATION_REJECTED",
                    GenerationRetryClass.SAMPLE_RECOVERABLE.value,
                    exhausted_days=3,
                )
            )
        ]
    )

    assert (fact.retrying, fact.operator_required) == (0, 1)


def test_published_slots_are_never_counted_as_blocked() -> None:
    fact = _fold(
        [
            _row(
                status=ContentStatus.PUBLISHED,
                first_published_at=datetime(2026, 9, 7, 12, tzinfo=KST),
                # 발행 전에 남아 있던 시도 기록은 차단이 아니다.
                summary=_attempt(
                    "IMAGE_GENERATION_FAILED",
                    GenerationRetryClass.SAMPLE_RECOVERABLE.value,
                ),
            )
        ]
    )

    assert (fact.due, fact.published, fact.blocked) == (1, 1, 0)


def test_reused_image_publication_is_counted_from_either_marker() -> None:
    fact = _fold(
        [
            _row(
                status=ContentStatus.PUBLISHED,
                first_published_at=datetime(2026, 9, 7, 12, tzinfo=KST),
                reused_from=uuid.uuid4(),
            ),
            _row(
                scheduled_date=WEEK_START + timedelta(days=1),
                status=ContentStatus.PUBLISHED,
                first_published_at=datetime(2026, 9, 8, 12, tzinfo=KST),
                summary={"image_reuse": {"source_content_id": str(uuid.uuid4())}},
            ),
        ]
    )

    assert (fact.published, fact.published_with_reused_image) == (2, 2)


def test_causes_are_operator_copy_not_enum_codes() -> None:
    fact = _fold(
        [
            _row(
                summary=_attempt(
                    "GENERATION_REJECTED", GenerationRetryClass.SAMPLE_RECOVERABLE.value
                )
            ),
            _row(
                scheduled_date=WEEK_START + timedelta(days=1),
                summary=_attempt(
                    "GENERATION_REJECTED", GenerationRetryClass.SAMPLE_RECOVERABLE.value
                ),
            ),
        ]
    )

    assert list(fact.blocked_by_cause.values()) == [2]
    assert all("GENERATION" not in cause for cause in fact.blocked_by_cause)


def test_rows_of_other_hospitals_and_malformed_summaries_are_ignored() -> None:
    fact = _fold(
        [
            _row(hospital_id=uuid.uuid4()),
            _row(summary={"generation_attempt": "부서진 값"}),
            _row(
                scheduled_date=WEEK_START + timedelta(days=1),
                summary=_attempt(
                    "GENERATION_REJECTED",
                    GenerationRetryClass.SAMPLE_RECOVERABLE.value,
                    exhausted_days="이상한 값",
                ),
            ),
        ]
    )

    assert (fact.due, fact.blocked, fact.retrying) == (2, 1, 1)


def test_period_must_move_forward() -> None:
    with pytest.raises(ValueError):
        fold_yield_rows(HOSPITALS, [], period_start=WEEK_END, period_end=WEEK_START)


def test_week_start_is_the_kst_monday() -> None:
    assert kst_week_start(date(2026, 9, 13)) == WEEK_START
    assert kst_week_start(WEEK_START) == WEEK_START
