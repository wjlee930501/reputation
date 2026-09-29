"""운영센터 “작업 다시 시도”가 게이트의 원고 없음 기록에 막히지 않는다.

07:45·08:00 게이트는 빈 슬롯을 CONTENT_NOT_GENERATED(OPERATOR_REQUIRED, 기한 없음)로
기록한다. 예산을 쓰지 않은 증상 기록이지만, 같은 생성 context에서는 워커의 동일 원인
억제가 그 기록을 그대로 읽어 운영자가 누른 재시도까지 작가 호출 0회로 끝냈다
(PR #179 2차 리뷰 B1). 이제 Admin이 만든 실행(`regenerate_content_item`의 explicit run)에서
본문이 없고 저장 원인이 정확히 CONTENT_NOT_GENERATED일 때만 억제를 풀고 예산 계수는 남긴다.
자동 경로와 다른 원인은 그대로 억제한다.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from test_topic_swap_fallback import (
    _approved_philosophy,
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


def _press_retry(monkeypatch, item, moment, *, operator: bool) -> list:
    """`regenerate_content_item`을 실제로 돌린다.

    operator=True는 운영센터 RETRY_RUN·Admin 재생성이 만든 실행이다 — 워커는
    `operation_run_id` 헤더와 claim 버전을 받고(`explicit_run_context`), 그 실행이 이 글을
    허가하는지(`explicit_run_matches`)는 별도 테스트가 고정한다. operator=False는 실행 없이
    서명만 된 배포(스케줄 저장 직후 생성·일회성 스크립트)다.
    """

    _freeze(monkeypatch, moment)
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
    # 작가가 불릴 때의 기록: 억제(원인·분류·관측 시각)만 빠지고 예산 사다리는 그대로다.
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
