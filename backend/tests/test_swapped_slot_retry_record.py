"""07:45·08:00 게이트가 자동 복구가 소유한 빈 슬롯의 시도 기록을 덮지 않는다.

#180은 저장 원인이 `TOPIC_SWAPPED`인 기록만 지켰다. 교체된 새 주제를 스윕이 쓰다가
공급자 시간 초과처럼 재시도 가능한 원인으로 실패하면, 그 기록(ENVIRONMENT_RECOVERABLE·
다음 시도 시각)을 게이트가 빈 슬롯의 증상 `CONTENT_NOT_GENERATED`(OPERATOR_REQUIRED·
기한 없음)로 덮었다. 그러면 로더가 다시 집지 않고(`_generation_attempt_is_unchanged`),
`CONTENT_NOT_GENERATED`는 교체 후보 코드가 아니며 교체 이력도 있어 다시 교체되지 않는다.

교체 이력이 없는 슬롯도 같은 기전으로 멈춘다 — 덮인 `CONTENT_NOT_GENERATED`는 이력과
무관하게 교체 후보(`SAMPLE_BODY_CODES`)가 아니다. 그래서 가드는 교체 이력을 보지 않는다.

실제 코드: 교체 기록(`_record_topic_swapped_attempt`), 복구 스윕 로더
(`_load_nightly_generation_batch` + `_generation_retry_is_eligible`), 워커
(`_run_generation_item`), 07:45 `_page_morning_stored_publication_gates`, 08:00
`_auto_publish_one`. 공급자(작가)·이미지·인시던트 저장만 가짜다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import arrow
import pytest

from app.workers import tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass, recovery_is_abandoned
from app.workers.topic_swap_fallback import TOPIC_SWAPPED_REASON
from tests.test_topic_swap_fallback import (
    _RECORD_ATTEMPT,
    _SWAPPED_AT,
    _approved_philosophy,
    _AutoPublishDB,
    _claims_at,
    _FakeDB,
    _freeze,
    _GateDB,
    _generate_once,
    _kst,
    _patch_generation,
    _swapped_slot,
    _WorkerDB,
)

_WRITTEN_AT = _kst(2026, 9, 16, 7, 0, 4)  # 교체 pass 뒤 같은 07:00 스윕의 글 단위 태스크
_NEXT_SWEEP = _kst(2026, 9, 16, 12, 0)  # 환경 원인의 다음 적격 스윕(4회 예산이 남아 있다)
_TOMORROW_SWEEP = _kst(2026, 9, 17, 1, 0)  # 표본 원인의 하루 예산이 소진된 뒤의 첫 스윕

# (작가가 던지는 오류, 워커가 저장하는 원인, 재시도 분류, 게이트가 보고하는 코드, 다음 스윕)
_RETRYABLE_FAILURES = [
    pytest.param(
        TimeoutError("provider timed out"),
        "PROVIDER_TIMEOUT",
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE,
        "CONTENT_NOT_GENERATED",
        _NEXT_SWEEP,
        id="PROVIDER_TIMEOUT",
    ),
    pytest.param(
        ConnectionError("provider 503"),
        "PROVIDER_UNAVAILABLE",
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE,
        "CONTENT_NOT_GENERATED",
        _NEXT_SWEEP,
        id="PROVIDER_UNAVAILABLE",
    ),
    pytest.param(
        RuntimeError("unexpected provider payload"),
        "GENERATION_FAILED",
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE,
        "CONTENT_NOT_GENERATED",
        _NEXT_SWEEP,
        id="GENERATION_FAILED",
    ),
    # 본문 표본 실패. 게이트가 저장 원인을 그대로 보고하므로(`_STORED_EMPTY_CONTENT_BLOCK_CODES`)
    # 종전에도 덮이지 않았다 — 같은 계약의 대조군이다.
    pytest.param(
        ValueError("GEO hard-fail: references is empty for FAQ"),
        "GENERATION_REJECTED",
        GenerationRetryClass.SAMPLE_RECOVERABLE,
        "GENERATION_REJECTED",
        _TOMORROW_SWEEP,
        id="GENERATION_REJECTED",
    ),
]


def _fail_writer_with(monkeypatch, writer_calls: list, error: BaseException) -> None:
    async def failing_writer(*, hospital, item, existing_titles, philosophy, approved_brief):
        writer_calls.append(item.id)
        raise error

    monkeypatch.setattr(tasks, "_generate_with_auto_review", failing_writer)


def _write_and_fail(monkeypatch, item, philosophy, error, *, swap: bool) -> tuple[dict, list]:
    """(교체 →) 같은 07:00 스윕 로더가 집고 → 워커의 작가 호출이 ``error``로 실패한다."""

    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)
    _fail_writer_with(monkeypatch, writer_calls, error)
    if swap:
        _freeze(monkeypatch, _SWAPPED_AT)
        _RECORD_ATTEMPT(_FakeDB([]), item, now=_SWAPPED_AT)
        assert item.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == TOPIC_SWAPPED_REASON
    assert _claims_at(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 3)) is True
    state, _code, _message = _generate_once(monkeypatch, item, _WRITTEN_AT)
    assert state == tasks.GenerationItemState.FAILED
    assert writer_calls == [item.id]
    # 글 단위 태스크가 끝나며 claim을 놓는다 — 07:45는 살아 있는 claim을 건너뛴다(#180).
    item.generation_claim_token = None
    item.generation_claimed_at = None
    return dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY]), writer_calls


def _run_gates(monkeypatch, item, philosophy) -> tuple[list[str], list[str]]:
    """실제 07:45 게이트와 실제 08:00 발행기. 보고된 인시던트·차단·요약 코드를 잡는다."""

    incidents: list[str] = []
    digests: list[str] = []

    async def capture_incident(**kwargs):
        incidents.append(kwargs["code"])

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda _db, _day, _batch, outcomes: digests.extend(row["code"] for row in outcomes),
    )

    gate_at = _kst(2026, 9, 16, 7, 45)
    _freeze(monkeypatch, gate_at)
    assert tasks._page_morning_stored_publication_gates(
        _GateDB(item), now_kst=arrow.get(gate_at)
    ) == 1

    publish_at = _kst(2026, 9, 16, 8, 0)
    _freeze(monkeypatch, publish_at)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_kw: arrow.get(publish_at))
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _AutoPublishDB(item, item.hospital))
    outcome = tasks._auto_publish_one(item.id)
    assert outcome is not None and outcome["kind"] == "blocked"
    return incidents + [outcome["code"]], digests


def _morning_digest(code: str) -> list[str]:
    return [code] if code == "CONTENT_NOT_GENERATED" else []


def _stored(item) -> dict:
    return dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])


def _assert_sweep_writes_it_again(monkeypatch, item, philosophy, next_sweep: datetime) -> None:
    """저장된 기한에 로더·워커가 다시 집어 새 주제를 쓴다."""

    assert datetime.fromisoformat(_stored(item)["next_retry_at"]) == next_sweep.astimezone(UTC)
    _freeze(monkeypatch, next_sweep - timedelta(seconds=1))
    assert tasks.retry_is_due(_stored(item)) is False
    _freeze(monkeypatch, next_sweep)
    assert tasks.retry_is_due(_stored(item)) is True
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is False
    assert _claims_at(monkeypatch, item, next_sweep) is True

    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)
    state, code, _message = _generate_once(monkeypatch, item, next_sweep + timedelta(seconds=1))
    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert writer_calls == [item.id]


# ── 재현: 교체된 슬롯의 재시도 가능한 실패를 게이트가 덮지 않는다 ───────────────────


@pytest.mark.parametrize(
    ("error", "reason", "retry_class", "reported_code", "next_sweep"), _RETRYABLE_FAILURES
)
def test_the_gates_keep_a_retryable_failure_on_a_swapped_slot(
    monkeypatch, error, reason, retry_class, reported_code, next_sweep
):
    """교체 → 새 주제 작성이 재시도 가능한 원인으로 실패 → 07:45 → 08:00 → 다음 스윕이 쓴다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    failed, _writer_calls = _write_and_fail(monkeypatch, item, philosophy, error, swap=True)
    assert failed["reason"] == reason
    assert failed["retry_class"] == retry_class.value
    assert failed["next_retry_at"] == next_sweep.astimezone(UTC).isoformat()

    reported, digested = _run_gates(monkeypatch, item, philosophy)

    # 기록은 한 글자도 바뀌지 않는다 — 원인·분류·기한·계수 모두.
    assert _stored(item) == failed
    # 보고 경로는 종전 그대로다. 07:45 요약은 CONTENT_NOT_GENERATED만 싣고, 본문 표본
    # 거절은 주간 롤업이 소유한다(`generation_block_digest_due`).
    assert reported == [reported_code, reported_code]
    assert digested == _morning_digest(reported_code)

    _assert_sweep_writes_it_again(monkeypatch, item, philosophy, next_sweep)


# ── 대조군 ───────────────────────────────────────────────────────────────────


def test_a_slot_without_swap_history_keeps_its_retryable_failure_too(monkeypatch):
    """(a) 교체 이력이 없어도 같다 — 덮인 CONTENT_NOT_GENERATED는 교체 후보가 아니다."""

    from app.workers import topic_swap_fallback

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    item.topic_swap_history = None
    failed, _writer_calls = _write_and_fail(
        monkeypatch, item, philosophy, TimeoutError("provider timed out"), swap=False
    )
    assert failed["reason"] == "PROVIDER_TIMEOUT"

    reported, digested = _run_gates(monkeypatch, item, philosophy)

    assert _stored(item) == failed
    assert reported == ["CONTENT_NOT_GENERATED", "CONTENT_NOT_GENERATED"]
    assert digested == ["CONTENT_NOT_GENERATED"]
    _assert_sweep_writes_it_again(monkeypatch, item, philosophy, _NEXT_SWEEP)

    # 종전처럼 덮였다면 이 슬롯을 되살릴 경로가 없다: 교체 후보도 아니다.
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            "reason": "CONTENT_NOT_GENERATED",
            "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
        }
    }
    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None


@pytest.mark.parametrize(
    ("stored", "gate_code"),
    [
        # 새 주제의 3일 표본 예산이 끝났다(빈 슬롯에 남은 종착 기록).
        (
            {
                "reason": "CONTENT_AI_HARD_FINDING",
                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
                "exhausted_days": 3,
            },
            "CONTENT_NOT_GENERATED",
        ),
        (
            {
                "reason": "CONTENT_AI_HARD_FINDING",
                "retry_class": GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
            },
            "CONTENT_NOT_GENERATED",
        ),
        # 저장 원인을 그대로 보고하는 종착은 종전에도 같은 코드라 덮이지 않는다.
        (
            {
                "reason": "GENERATION_REJECTED",
                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
                "exhausted_days": 3,
            },
            "GENERATION_REJECTED",
        ),
    ],
    ids=["exhausted_sample", "input_change", "exhausted_rejection"],
)
def test_a_terminal_record_on_a_swapped_slot_is_handled_as_before(
    monkeypatch, stored, gate_code
):
    """(b) 자동 복구가 끝난 기록은 종전처럼 게이트 코드가 정본이 된다(또는 같은 코드면 그대로)."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            **stored,
            "context": tasks._generation_attempt_context(item, philosophy),
            "attempt_period": "2026-09-16",
        }
    }

    reported, digested = _run_gates(monkeypatch, item, philosophy)

    after = _stored(item)
    assert reported == [gate_code, gate_code]
    assert digested == _morning_digest(gate_code)
    assert after["reason"] == gate_code
    if gate_code == "CONTENT_NOT_GENERATED":
        assert after["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
        assert after.get("next_retry_at") is None
        _freeze(monkeypatch, _TOMORROW_SWEEP)
        assert tasks.retry_is_due(after) is False
    else:
        assert after["retry_class"] == stored["retry_class"]


def test_the_topic_swapped_record_is_still_kept(monkeypatch):
    """(c) #180의 가드는 그대로다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    _freeze(monkeypatch, _SWAPPED_AT)
    _RECORD_ATTEMPT(_FakeDB([]), item, now=_SWAPPED_AT)
    swapped = _stored(item)

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    assert _stored(item) == swapped


@pytest.mark.parametrize(
    ("reason", "retry_class"),
    [
        ("PROVIDER_TIMEOUT", GenerationRetryClass.ENVIRONMENT_RECOVERABLE),
        # 교체 기록 밖의 표본 실패도 자동 복구가 소유한다(3일 사다리가 교체 계단으로 이어진다).
        ("CONTENT_AI_HARD_FINDING", GenerationRetryClass.SAMPLE_RECOVERABLE),
    ],
)
def test_the_empty_slot_symptom_keeps_either_recoverable_class(
    monkeypatch, reason, retry_class
):
    """분류가 판정 기준이다 — 원인 코드 목록이 아니라 재시도 정책의 두 복구 분류."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    _freeze(monkeypatch, _WRITTEN_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason)
    recorded = _stored(item)
    assert recorded["retry_class"] == retry_class.value
    assert recorded["next_retry_at"] is not None

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    assert _stored(item) == recorded


@pytest.mark.parametrize("swapped", [True, False], ids=["swapped", "not_swapped"])
def test_an_exhausted_environment_budget_is_handled_as_before(monkeypatch, swapped):
    """(b') 4회 예산을 다 써 어떤 스윕도 집지 않는 기록은 소유자가 없다 — 종전처럼 덮는다.

    분류는 여전히 ENVIRONMENT_RECOVERABLE이지만 `next_retry_at=None`으로 굳었다
    (`recovery_is_abandoned`). 그대로 두면 운영자가 부른 재시도도 같은 원인으로 건너뛴다.
    """

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    if not swapped:
        item.topic_swap_history = None
    for hour in (1, 4, 7):
        _freeze(monkeypatch, _kst(2026, 9, 15, hour, 0))
        tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "PROVIDER_TIMEOUT")
    _freeze(monkeypatch, _WRITTEN_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "PROVIDER_TIMEOUT")
    exhausted = _stored(item)
    assert exhausted["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert exhausted["provider_attempt_count"] == 4
    assert exhausted["next_retry_at"] is None

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    after = _stored(item)
    assert after["reason"] == "CONTENT_NOT_GENERATED"
    assert after["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value


@pytest.mark.parametrize("swapped", [True, False], ids=["swapped", "not_swapped"])
@pytest.mark.parametrize(
    "gate_code", ["CONTENT_AUTHORITY_CHANGED", "CONTENT_IMAGE_NOT_READY"]
)
def test_other_gate_codes_still_become_the_record(monkeypatch, gate_code, swapped):
    """(d) 빈 슬롯의 증상이 아닌 게이트 코드는 종전처럼 재시도 가능한 기록도 대신한다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    if not swapped:
        item.topic_swap_history = None
    _freeze(monkeypatch, _WRITTEN_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "PROVIDER_TIMEOUT")
    assert _stored(item)["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, gate_code)

    assert _stored(item)["reason"] == gate_code


def test_a_written_slot_is_reported_and_recorded_by_its_own_blocker(monkeypatch):
    """(e) 본문이 있는 슬롯은 게이트 코드가 CONTENT_NOT_GENERATED가 아니라 가드 밖이다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    _patch_generation(monkeypatch, philosophy, item, fail=False)
    assert _claims_at(monkeypatch, item, _kst(2026, 9, 16, 7, 0, 3)) is True
    state, _code, _message = _generate_once(monkeypatch, item, _WRITTEN_AT)
    assert state == tasks.GenerationItemState.SUCCEEDED
    item.generation_claim_token = None
    item.generation_claimed_at = None
    # 대표 이미지가 빠졌고, 앞선 시도가 남긴 재시도 가능한 기록이 있다.
    item.image_url = None
    item.image_content_hash = None
    item.image_policy_verified_at = None
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "PROVIDER_TIMEOUT")

    assessment = tasks.assess_content_publication(item, philosophy)
    code, _message = tasks._publication_block_details(item, assessment)
    assert code != "CONTENT_NOT_GENERATED"

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, code)

    assert _stored(item)["reason"] == code


# ── `next_retry_at` 키가 없는 2026-09-07 이전 기록 ──────────────────────────────

_LEGACY_ENV = {
    "reason": "PROVIDER_TIMEOUT",
    "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
}
_LEGACY_SAMPLE = {
    "reason": "CONTENT_AI_HARD_FINDING",
    "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
}


def _legacy_slot(monkeypatch, record: dict):
    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            **record,
            "context": tasks._generation_attempt_context(item, philosophy),
            "attempt_period": "2026-09-16",
            "exhausted_days": 0,
        }
    }
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    return philosophy, item, _stored(item)


@pytest.mark.parametrize(
    "record",
    [
        # 환경 예산 4회를 다 썼다 — 키가 없으니 "집을 스윕이 없다"는 결정도 없다.
        pytest.param({**_LEGACY_ENV, "provider_attempt_count": 4}, id="environment_exhausted"),
        # 오늘의 본문 표본 예산 2회를 다 썼다.
        pytest.param({**_LEGACY_SAMPLE, "provider_attempt_count": 2}, id="sample_spent_today"),
    ],
)
def test_a_legacy_record_without_a_schedule_that_is_not_due_is_overwritten(monkeypatch, record):
    """키가 없고 지금 기한도 아닌 레거시 기록은 소유자가 없다 — 종전처럼 게이트 코드가 대신한다.

    `recovery_is_abandoned`는 `next_retry_at=None`을 명시한 기록만 알아본다. 이 기록을
    지키면 로더는 기한 미도래로 거르고(`_generation_attempt_is_unchanged`), 표본 실패는
    운영자 재시도도 억제되어 어느 쪽도 이 슬롯을 쓰지 않는다.
    """

    philosophy, item, legacy = _legacy_slot(monkeypatch, record)
    assert "next_retry_at" not in legacy
    assert tasks.retry_is_due(legacy) is False
    assert recovery_is_abandoned(legacy) is False  # 종전 가드가 지키던 이유
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is True

    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    after = _stored(item)
    assert after["reason"] == "CONTENT_NOT_GENERATED"
    assert after["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            {
                **_LEGACY_ENV,
                "provider_attempt_count": 1,
                "next_retry_at": _kst(2026, 9, 16, 12, 0).astimezone(UTC).isoformat(),
            },
            id="environment",
        ),
        pytest.param(
            {
                **_LEGACY_SAMPLE,
                "provider_attempt_count": 2,
                "next_retry_at": _TOMORROW_SWEEP.astimezone(UTC).isoformat(),
            },
            id="sample",
        ),
    ],
)
def test_a_record_with_a_stored_schedule_is_kept_before_it_is_due(monkeypatch, record):
    """같은 모양이라도 저장된 다음 시도 시각이 있으면 그 시각의 스윕이 소유한다."""

    philosophy, item, stored = _legacy_slot(monkeypatch, record)
    assert tasks.retry_is_due(stored) is False

    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    assert _stored(item) == stored


def test_a_legacy_record_that_is_already_due_is_kept_for_the_sweep(monkeypatch):
    """키가 없어도 예산이 남은 레거시 기록은 `_due_time_reached`가 기한으로 읽는다.

    로더가 곧바로 집는 슬롯이라 스윕이 소유한다 — 덮어 사람의 일로 만들지 않는다.
    """

    philosophy, item, legacy = _legacy_slot(
        monkeypatch, {**_LEGACY_ENV, "provider_attempt_count": 1}
    )
    assert "next_retry_at" not in legacy
    assert tasks.retry_is_due(legacy) is True

    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    assert _stored(item) == legacy
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is False


@pytest.mark.parametrize(
    ("reason", "retry_class"),
    [
        # 본문 수리 코드는 분류가 OPERATOR_REQUIRED여도 수리 예산의 기한을 문자열로 남긴다.
        ("MISSING_REFERENCES", GenerationRetryClass.OPERATOR_REQUIRED),
        ("FORBIDDEN_EXPRESSION", GenerationRetryClass.INPUT_CHANGE_REQUIRED),
    ],
)
def test_a_terminal_class_is_overwritten_even_with_a_stored_schedule(
    monkeypatch, reason, retry_class
):
    """다음 시도 시각이 있어도 판정 기준은 두 복구 분류다 — 종착 분류는 종전처럼 덮는다."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    _freeze(monkeypatch, _WRITTEN_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason)
    recorded = _stored(item)
    assert recorded["retry_class"] == retry_class.value
    if not isinstance(recorded.get("next_retry_at"), str):
        # 실제 기록이 기한을 남기지 않는 분류도 같은 모양(기한 문자열)에서 확인한다.
        recorded["next_retry_at"] = _NEXT_SWEEP.astimezone(UTC).isoformat()
        item.essence_check_summary = {GENERATION_ATTEMPT_KEY: recorded}

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")

    after = _stored(item)
    assert after["reason"] == "CONTENT_NOT_GENERATED"
    assert after["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
