"""운영센터 “작업 다시 시도”가 게이트의 원고 없음 기록에 막히지 않는다.

07:45·08:00 게이트는 빈 슬롯을 CONTENT_NOT_GENERATED(OPERATOR_REQUIRED, 기한 없음)로
기록한다. 예산을 쓰지 않은 증상 기록이지만, 같은 생성 context에서는 워커의 동일 원인
억제가 그 기록을 그대로 읽어 운영자가 누른 재시도까지 작가 호출 0회로 끝냈다
(PR #179 2차 리뷰 B1). 이제 Admin이 만든 실행(`regenerate_content_item`의 explicit run)에서
본문이 없고 저장 원인이 CONTENT_NOT_GENERATED이거나 저장 분류가 ENVIRONMENT_RECOVERABLE일
때만 억제를 풀고, 기록에 남은 계수는 그대로 둔다. 게이트가 CONTENT_NOT_GENERATED로 덮은
기록은 원인이 바뀌어 계수가 이미 0에서 다시 시작한 것이라(`_remember_generation_attempt`)
그 경우 남는 계수는 0이다. 게이트가 덮지 않고 지킨 환경 실패 기록만 실제 계수를 잇는다.

환경 실패는 #182의 게이트 가드와 합쳐져 생긴 경우다. 게이트가 스윕이 소유한
PROVIDER_TIMEOUT 등을 CONTENT_NOT_GENERATED로 덮지 않고 남기므로, 원인 문자열만 보면
운영자 재시도가 다시 작가 0회로 끝난다. 표본 실패(SAMPLE_RECOVERABLE, 주제 교체 기록
포함)는 하루 예산이 소유하므로 운영자 재시도에도 그대로 억제한다. 자동 경로와 그 밖의
원인·분류도 그대로 억제한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from test_topic_swap_fallback import (
    _RECORD_ATTEMPT,
    _approved_philosophy,
    _FakeDB,
    _freeze,
    _generate_once,
    _kst,
    _patch_generation,
    _swapped_slot,
    _WorkerDB,
)

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import OperationRunState
from app.workers import tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY, GENERATION_LADDER_KEYS
from app.workers.generation_retry_policy import GenerationRetryClass
from app.workers.nightly_generation_batch import (
    GENERATION_WRITE_BACK_STATUSES,
    generation_claim_is_active,
)

_GATE_AT = _kst(2026, 9, 17, 7, 45)
_SAME_DAY = _GATE_AT + timedelta(minutes=30)
_NEXT_DAY = _GATE_AT + timedelta(days=1, hours=2)


class _TaskDB(_WorkerDB):
    """`regenerate_content_item`의 세션 — 대상 글과 병원만 돌려준다."""

    def __init__(self, item) -> None:
        self._rows = {ContentItem: item, Hospital: item.hospital}

    def get(self, model, _key):
        return self._rows.get(model)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _gate_recorded_slot(monkeypatch):
    """게이트가 CONTENT_NOT_GENERATED를 기록한 빈 슬롯과 작가 호출·시점 기록."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    item.topic_swap_history = []
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)
    seen_at_writer: list = []
    writer = tasks._generate_with_auto_review

    async def snapshot_then_write(**kwargs):
        summary = item.essence_check_summary or {}
        seen_at_writer.append(summary.get(GENERATION_ATTEMPT_KEY))
        return await writer(**kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", snapshot_then_write)
    _freeze(monkeypatch, _GATE_AT)
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")
    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    assert record["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    return item, record, writer_calls, seen_at_writer


def _install_item_lease(monkeypatch, item) -> list:
    """글 단위 lease(`claim_generation_lease`·토큰 해제)를 이 글 하나에 대해 흉내 낸다.

    살아 있는 claim의 판정은 실제 술어(`generation_claim_is_active`)를 그대로 쓴다. 해제는
    자기 토큰만 푼다 — 토큰 없는 해제는 여기서 실패한다. 실제 UPDATE 술어는
    `tests/integration/test_operator_regenerate_lease_postgres.py`가 본다. 반환값은
    해제 요청의 (토큰, 푼 행 수) 목록이다.
    """

    released: list = []

    def claim(_db, _item_id, *, now=None):
        observed_at = now or tasks.datetime.now(timezone.utc)
        if item.status not in GENERATION_WRITE_BACK_STATUSES or generation_claim_is_active(
            item, now=observed_at
        ):
            return None
        token = uuid.uuid4()
        item.generation_claimed_at = observed_at
        item.generation_claim_token = token
        return item, token

    def release(_db, _item_ids, *, expected_claimed_at=None, expected_claim_token=None):
        assert expected_claim_token is not None, "운영자 재생성은 자기 토큰으로만 푼다"
        rows = int(item.generation_claim_token == expected_claim_token)
        if rows:
            item.generation_claimed_at = None
            item.generation_claim_token = None
        released.append((expected_claim_token, rows))
        return rows

    monkeypatch.setattr(tasks, "claim_generation_lease", claim)
    monkeypatch.setattr(tasks, "release_unfinished_claims", release)
    return released


def _press_retry(monkeypatch, item, moment, *, operator: bool) -> list:
    """`regenerate_content_item`을 실제로 돌린다.

    operator=True는 운영센터 RETRY_RUN·Admin 재생성이 만든 실행이다 — 워커는
    `operation_run_id` 헤더와 claim 버전을 받고(`explicit_run_context`), 그 실행이 이 글을
    허가하는지(`explicit_run_matches`)는 별도 테스트가 고정한다. operator=False는 실행 없이
    서명만 된 배포(스케줄 저장 직후 생성·일회성 스크립트)다.
    """

    _freeze(monkeypatch, moment)
    _install_item_lease(monkeypatch, item)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _TaskDB(item))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(tasks, "explicit_run_matches", lambda *_args, **_kwargs: True)
    finished: list = []

    def finish(_db, task, _item_id, state, **kwargs):
        finished.append((state, kwargs.get("safe_error_code")))
        return uuid.uuid4() if tasks.explicit_run_context(task) is not None else None

    monkeypatch.setattr(tasks, "finish_explicit_run", finish)
    headers = {"operation_run_id": str(uuid.uuid4())} if operator else {}
    task = tasks.regenerate_content_item
    task.push_request(
        id="worker-1",
        headers=headers,
        operation_run_claim_version=2 if operator else None,
    )
    try:
        assert (tasks.explicit_run_context(task) is not None) is operator
        task.run(str(item.id))
    finally:
        task.pop_request()
    return finished


def _ladder(record: dict) -> dict:
    return {key: record[key] for key in GENERATION_LADDER_KEYS if key in record}


@pytest.mark.parametrize("moment", [_SAME_DAY, _NEXT_DAY], ids=["same_day", "next_day"])
def test_operator_retry_writes_the_gate_recorded_empty_slot_once(monkeypatch, moment):
    item, record, writer_calls, seen_at_writer = _gate_recorded_slot(monkeypatch)

    finished = _press_retry(monkeypatch, item, moment, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    # 작가가 불릴 때의 기록: 억제(원인·분류·관측 시각)만 빠지고 게이트 기록의 사다리는
    # 그대로다. 게이트가 예산 없이 새로 쓴 기록이라 그 계수는 0이다.
    assert seen_at_writer == [_ladder(record)]
    released = seen_at_writer[0]
    assert {"reason", "retry_class", "observed_at", "next_retry_at"}.isdisjoint(released)
    for key in (
        "context",
        "attempt_period",
        "exhausted_days",
        "attempt_count",
        "provider_attempt_count",
        "guard_deferral_count",
        "first_observed_at",
    ):
        assert released[key] == record[key], key
    assert released["attempt_period"] == "2026-09-17"  # 다음 날에도 게이트 날의 기간이다


@pytest.mark.parametrize("moment", [_SAME_DAY, _NEXT_DAY], ids=["same_day", "next_day"])
def test_dispatch_without_an_operator_run_keeps_the_suppression(monkeypatch, moment):
    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)

    finished = _press_retry(monkeypatch, item, moment, operator=False)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, "CONTENT_NOT_GENERATED")]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record


@pytest.mark.parametrize("moment", [_SAME_DAY, _NEXT_DAY], ids=["same_day", "next_day"])
def test_recovery_sweep_keeps_the_suppression(monkeypatch, moment):
    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)

    state, code, _message = _generate_once(monkeypatch, item, moment)

    assert (state, code) == (tasks.GenerationItemState.SKIPPED, "CONTENT_NOT_GENERATED")
    assert writer_calls == []
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record


@pytest.mark.parametrize(
    "reason",
    [
        "GENERATION_REJECTED",
        "MISSING_APPROVED_ESSENCE",
        "COST_BLOCKED",
        "TOPIC_SWAPPED",
        "MISSING_REFERENCES",
    ],
)
def test_operator_retry_keeps_the_suppression_for_other_stored_reasons(monkeypatch, reason):
    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    # 같은 context·같은 OPERATOR_REQUIRED 기록에서 원인만 다르다.
    stored = {**record, "reason": reason}
    item.essence_check_summary = {GENERATION_ATTEMPT_KEY: stored}

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, reason)]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == stored


def test_operator_retry_does_not_release_a_record_on_a_written_body(monkeypatch):
    """본문이 있는 글의 CONTENT_NOT_GENERATED 기록(제목만 빈 경우 등)은 풀지 않는다."""

    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    item.body = "이미 작성된 본문입니다."
    # 현재 기준보다 오래된 본문이라 저장 본문 경로가 아니라 동일 원인 억제 판정으로 간다.
    item.content_philosophy_id = uuid.uuid4()
    stored = dict(record)
    item.essence_check_summary = {GENERATION_ATTEMPT_KEY: stored}

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, "CONTENT_NOT_GENERATED")]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == stored


def test_operator_retry_keeps_an_operator_decides_reference_block(monkeypatch):
    """#177의 참고자료 결정 기록은 운영자 재시도로도 우회되지 않는다(원인 문자열로 고정)."""

    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    stored = {
        **record,
        "reason": "MISSING_REFERENCES",
        "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
        "operator_decides": True,
    }
    item.essence_check_summary = {GENERATION_ATTEMPT_KEY: stored}

    finished = _press_retry(monkeypatch, item, _NEXT_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, "MISSING_REFERENCES")]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == stored


# ── #182 가드와의 결합: 게이트가 덮지 않고 남긴 환경 실패 기록 ─────────────────────

_FAILED_AT = _kst(2026, 9, 16, 7, 0, 4)  # 예정일 07:00 스윕의 글 단위 태스크가 실패한다
_SLOT_GATE_AT = _kst(2026, 9, 16, 7, 45)  # 같은 날 07:45 게이트
_OPERATOR_PRESSES = [
    pytest.param(_kst(2026, 9, 16, 7, 50), id="0750"),
    pytest.param(_kst(2026, 9, 16, 8, 5), id="0805"),  # 08:00 발행기가 막힌 뒤
]
_SWAP_HISTORY = [
    pytest.param(True, id="swapped"),
    pytest.param(False, id="not_swapped"),
]


def _slot_with_writer(monkeypatch, *, swapped: bool):
    """빈 슬롯과 작가 호출·작가 호출 시점의 시도 기록."""

    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    if not swapped:
        item.topic_swap_history = None
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)
    seen_at_writer: list = []
    writer = tasks._generate_with_auto_review

    async def snapshot_then_write(**kwargs):
        summary = item.essence_check_summary or {}
        seen_at_writer.append(summary.get(GENERATION_ATTEMPT_KEY))
        return await writer(**kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", snapshot_then_write)
    return philosophy, item, writer_calls, seen_at_writer


def _gate(monkeypatch, item, philosophy, code: str = "CONTENT_NOT_GENERATED") -> None:
    _freeze(monkeypatch, _SLOT_GATE_AT)
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, code)


def _gate_kept_environment_failure(monkeypatch, *, swapped: bool):
    """실제 `_remember_generation_attempt`가 남긴 PROVIDER_TIMEOUT을 07:45 게이트가 지킨다."""

    philosophy, item, writer_calls, seen_at_writer = _slot_with_writer(monkeypatch, swapped=swapped)
    _freeze(monkeypatch, _FAILED_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "PROVIDER_TIMEOUT")
    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert record["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert record["provider_attempt_count"] == 1
    # 다음 시도 시각은 운영자가 누르는 두 시각보다 뒤다 — 기한으로는 억제가 풀리지 않는다.
    assert datetime.fromisoformat(record["next_retry_at"]) > _kst(2026, 9, 16, 8, 5)

    _gate(monkeypatch, item, philosophy)

    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record
    return item, record, writer_calls, seen_at_writer


@pytest.mark.parametrize("moment", _OPERATOR_PRESSES)
@pytest.mark.parametrize("swapped", _SWAP_HISTORY)
def test_operator_retry_writes_a_gate_kept_environment_failure_once(monkeypatch, swapped, moment):
    """환경 실패 → 07:45 게이트가 기록을 지킴 → 운영자 재시도가 작가를 정확히 한 번 부른다.

    #182 단독으로는 게이트가 기록을 지키고, #179 단독으로는 운영자 재시도가
    CONTENT_NOT_GENERATED만 풀었다. 둘을 합치면 남은 PROVIDER_TIMEOUT(기한 미도래)이
    운영자 재시도를 같은 원인으로 건너뛰게 만들었다(작가 0회).
    """

    item, record, writer_calls, seen_at_writer = _gate_kept_environment_failure(
        monkeypatch, swapped=swapped
    )

    finished = _press_retry(monkeypatch, item, moment, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    # 작가가 불릴 때의 기록: 억제만 빠지고 예산 사다리(환경 실패 1회 포함)는 그대로다.
    assert seen_at_writer == [_ladder(record)]
    released = seen_at_writer[0]
    assert {"reason", "retry_class", "observed_at", "next_retry_at"}.isdisjoint(released)
    assert released["provider_attempt_count"] == 1
    assert released["attempt_period"] == "2026-09-16"
    assert released["first_observed_at"] == record["first_observed_at"]
    assert (item.topic_swap_history is not None) is swapped  # 교체 이력은 건드리지 않는다

    # 첫 재시도가 원고를 썼다. 바로 이어 한 번 더 눌러도 작가를 다시 사지 않는다.
    assert (item.body or "").strip()
    again = _press_retry(monkeypatch, item, moment + timedelta(minutes=1), operator=True)

    assert writer_calls == [item.id]
    # 저장 본문이 현재 기준으로 이미 쓰였으므로 작가 없이 성공으로 끝난다.
    assert again == [(OperationRunState.SUCCEEDED, None)]


@pytest.mark.parametrize("moment", _OPERATOR_PRESSES)
@pytest.mark.parametrize("swapped", _SWAP_HISTORY)
def test_dispatch_without_an_operator_run_keeps_a_gate_kept_environment_failure(
    monkeypatch, swapped, moment
):
    item, record, writer_calls, _seen = _gate_kept_environment_failure(monkeypatch, swapped=swapped)

    finished = _press_retry(monkeypatch, item, moment, operator=False)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, "PROVIDER_TIMEOUT")]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record


def _spend_sample_budget_today(item, philosophy, monkeypatch) -> str:
    """본문 표본 실패 2회로 오늘 예산을 다 쓴다(다음 시도는 내일)."""

    for hour in (1, 4):
        _freeze(monkeypatch, _kst(2026, 9, 16, hour, 0, 4))
        tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "GENERATION_REJECTED")
    return "GENERATION_REJECTED"


def _swap_a_slot_scheduled_tomorrow(item, philosophy, monkeypatch) -> str:
    """예정일 전날의 교체 — 오늘 예산을 소진으로 남기고 다음 시도는 내일 01시다."""

    item.scheduled_date = item.scheduled_date + timedelta(days=1)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 0, 2))
    _RECORD_ATTEMPT(_FakeDB([]), item, now=_kst(2026, 9, 16, 7, 0, 2))
    return "CONTENT_NOT_GENERATED"


@pytest.mark.parametrize("moment", _OPERATOR_PRESSES)
@pytest.mark.parametrize(
    "record_failure",
    [
        pytest.param(_spend_sample_budget_today, id="GENERATION_REJECTED"),
        pytest.param(_swap_a_slot_scheduled_tomorrow, id="TOPIC_SWAPPED"),
    ],
)
def test_operator_retry_keeps_a_sample_budget_suppression(monkeypatch, record_failure, moment):
    """표본 실패(주제 교체 기록 포함)는 하루 예산이 소유한다 — 운영자 재시도도 억제된다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=True)
    gate_code = record_failure(item, philosophy, monkeypatch)
    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert record["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert datetime.fromisoformat(record["next_retry_at"]) > _kst(2026, 9, 16, 8, 5)

    _gate(monkeypatch, item, philosophy, gate_code)
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record

    finished = _press_retry(monkeypatch, item, moment, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, record["reason"])]
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record


def test_a_legacy_sample_record_the_gate_overwrites_is_retried_by_the_operator(monkeypatch):
    """`next_retry_at` 키가 없는 2026-09-07 이전 기록(오늘 예산 소진)은 게이트가 종전처럼 덮는다.

    지켰다면 스윕도(기한 미도래) 운영자 재시도도(표본 실패 억제) 이 슬롯을 쓰지 못한다.
    덮인 CONTENT_NOT_GENERATED는 운영자 재시도가 푼다. 원인이 바뀌어 덮을 때 계수가 0에서
    다시 시작하므로(`_remember_generation_attempt`) 운영자 해제가 남기는 사다리도 그 0이다
    — 덮이기 전의 표본 계수(2회)가 아니다.
    """

    philosophy, item, writer_calls, seen_at_writer = _slot_with_writer(monkeypatch, swapped=True)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            "reason": "GENERATION_REJECTED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "context": tasks._generation_attempt_context(item, philosophy),
            "attempt_period": "2026-09-16",
            "provider_attempt_count": 2,
            "attempt_count": 2,
            "exhausted_days": 0,
        }
    }
    _freeze(monkeypatch, _SLOT_GATE_AT)
    assert tasks.retry_is_due(item.essence_check_summary[GENERATION_ATTEMPT_KEY]) is False

    _gate(monkeypatch, item, philosophy)

    overwritten = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert overwritten["reason"] == "CONTENT_NOT_GENERATED"
    assert overwritten["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert overwritten["provider_attempt_count"] == 0  # 원인이 바뀌어 계수가 리셋됐다

    finished = _press_retry(monkeypatch, item, _kst(2026, 9, 16, 7, 50), operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert seen_at_writer == [_ladder(overwritten)]
