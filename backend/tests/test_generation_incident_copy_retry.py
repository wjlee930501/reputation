"""원고 없는 슬롯의 인시던트 조치가 “작업 다시 시도”에 대해 하는 말은 실제 결과와 같다(#182 위).

#182 뒤에는 게이트가 스윕이 아직 소유한 환경·표본 실패 기록을 원고 미생성으로 덮지 않고
(`tasks._record_gate_blocker_decision`), 운영자 재시도는 원고 미생성 기록과 환경 실패 기록의
억제를 푼다(`tasks.regenerate_content_item`). 조치 문구의 가지마다 실제 게이트·인시던트
경로로 문구를 만들고, 같은 슬롯에서 실제 `regenerate_content_item`을 눌러 작가 호출 수가 문구와
맞는지 확인한다 — 누르라고 하면 한 번 쓰고, 눌러도 안 된다고 하면 0회다.

문구의 판정(`operator_retry_releases`)과 태스크의 해제 조건은 코드로 공유하지 않는다(태스크의 그
구간은 생성 lease 작업이 따로 고친다). 대신 저장 기록의 분류·원인마다 실제 태스크가 억제를
풀었는지와 판정이 같은지를 확인한다.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC

import pytest
from test_operator_retry_gate_record import (
    _FAILED_AT,
    _SLOT_GATE_AT,
    _gate,
    _press_retry,
    _slot_with_writer,
    _spend_sample_budget_today,
    _swap_a_slot_scheduled_tomorrow,
)
from test_topic_swap_fallback import _kst, _WorkerDB

from app.models.operations import OperationRunState
from app.services.notification_copy import display_time
from app.workers import generation_incident_control, generation_retry_policy, tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_incident_control import (
    CONTENT_NOT_GENERATED_OPERATOR_ACTION,
    ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION,
    operator_retry_releases,
)
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.test_generation_incident_copy import _freeze, _open

_PRESS = _kst(2026, 9, 16, 7, 50)
_PRESS_ANYTIME = "“작업 다시 시도”를 누르세요"
_PRESS_NOW = "“작업 다시 시도”를 눌러 지금 바로 만들 수도 있습니다"
_PRESS_DOES_NOTHING = "그 전에는 “작업 다시 시도”를 눌러도 원고를 만들지 않으니"


def _remember(monkeypatch, item, philosophy, moment, reason) -> dict:
    _freeze(monkeypatch, moment)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason)
    return dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])


def _action(monkeypatch, item, code, moment) -> str:
    """실제 `open_generation_incident`가 고른 조치. 워커의 `_run_async`와 루프가 겹치지 않게 따로 돈다."""

    _freeze(monkeypatch, moment)
    request, _incident = asyncio.run(_open(monkeypatch, item, code))
    return request.next_action


def test_a_gate_recorded_slot_says_press_and_pressing_writes_it(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _gate(monkeypatch, item, philosophy)  # 기록이 없던 슬롯 — 게이트가 원고 미생성을 남긴다
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == "CONTENT_NOT_GENERATED"

    action = _action(monkeypatch, item, "CONTENT_NOT_GENERATED", _SLOT_GATE_AT)

    assert action == CONTENT_NOT_GENERATED_OPERATOR_ACTION
    assert _PRESS_ANYTIME in action
    assert operator_retry_releases(item)
    assert _press_retry(monkeypatch, item, _PRESS, operator=True) == [
        (OperationRunState.SUCCEEDED, None)
    ]
    assert writer_calls == [item.id]


@pytest.mark.parametrize("code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"])
def test_an_exhausted_provider_failure_says_press_now_and_pressing_writes_it(
    monkeypatch, code
):
    """예산 소진은 07:45 전이어도 누르면 된다 — 게이트를 기다리라고 하지 않는다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    for moment in (_kst(2026, 9, 15, 23), _kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _FAILED_AT):
        record = _remember(monkeypatch, item, philosophy, moment, code)
    assert record["next_retry_at"] is None
    assert generation_retry_policy.recovery_is_abandoned(record)

    action = _action(monkeypatch, item, code, _FAILED_AT)

    assert action == ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION
    assert _PRESS_ANYTIME in action
    assert "07시 45분" not in action and "기다리" not in action
    before_gate = _kst(2026, 9, 16, 7, 10)
    assert _press_retry(monkeypatch, item, before_gate, operator=True) == [
        (OperationRunState.SUCCEEDED, None)
    ]
    assert writer_calls == [item.id]


def test_a_gate_kept_environment_failure_names_the_sweep_and_the_button(monkeypatch):
    """예산이 남은 환경 실패는 게이트가 지킨다 — 스윕 시각과, 지금 눌러도 된다는 것을 함께 말한다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    record = _remember(monkeypatch, item, philosophy, _FAILED_AT, "PROVIDER_TIMEOUT")
    assert record["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    _gate(monkeypatch, item, philosophy)
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record  # 덮지 않았다

    action = _action(monkeypatch, item, "CONTENT_NOT_GENERATED", _SLOT_GATE_AT)

    due = generation_incident_control._stored_retry_deadline(record)
    assert due is not None and due.tzinfo is not None
    assert action == (
        f"자동 복구가 {display_time(due.astimezone(UTC))}에 이 글의 원고를 다시 만듭니다. 원인이 "
        "풀렸으면 운영센터에서 해당 항목의 “작업 다시 시도”를 눌러 지금 바로 만들 수도 있습니다."
    )
    assert _PRESS_NOW in action and _PRESS_DOES_NOTHING not in action
    assert _press_retry(monkeypatch, item, _PRESS, operator=True) == [
        (OperationRunState.SUCCEEDED, None)
    ]
    assert writer_calls == [item.id]


def test_a_gate_kept_sample_record_says_pressing_waits_and_pressing_writes_nothing(
    monkeypatch,
):
    """주제 교체 기록(표본 실패)은 하루 예산이 소유한다 — 기한 전에는 눌러도 쓰지 않는다고 말한다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=True)
    gate_code = _swap_a_slot_scheduled_tomorrow(item, philosophy, monkeypatch)
    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    assert record["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    _gate(monkeypatch, item, philosophy, gate_code)
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record

    action = _action(monkeypatch, item, gate_code, _SLOT_GATE_AT)

    assert _PRESS_DOES_NOTHING in action and _PRESS_NOW not in action
    assert not operator_retry_releases(item)
    assert _press_retry(monkeypatch, item, _PRESS, operator=True) == [
        (OperationRunState.FAILED, record["reason"])
    ]
    assert writer_calls == []


# ── 판정 ⇔ 실제 태스크의 해제 ─────────────────────────────────────────────────


def _gate_recorded(monkeypatch, item, philosophy) -> None:
    _gate(monkeypatch, item, philosophy)  # 기록 없는 빈 슬롯 → 원고 미생성(OPERATOR_REQUIRED)


def _failed(reason: str, times: int = 1):
    def record(monkeypatch, item, philosophy) -> None:
        moments = (_kst(2026, 9, 15, 23), _kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _FAILED_AT)
        for moment in moments[-times:]:
            _remember(monkeypatch, item, philosophy, moment, reason)

    return record


def _sample_spent(monkeypatch, item, philosophy) -> None:
    _spend_sample_budget_today(item, philosophy, monkeypatch)


def _swapped(monkeypatch, item, philosophy) -> None:
    _swap_a_slot_scheduled_tomorrow(item, philosophy, monkeypatch)


def _operator_decides(monkeypatch, item, philosophy) -> None:
    _gate_recorded(monkeypatch, item, philosophy)
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {**record, "reason": "MISSING_REFERENCES", "operator_decides": True}
    }


def _with_body(body: str, record_failure):
    def record(monkeypatch, item, philosophy) -> None:
        record_failure(monkeypatch, item, philosophy)
        item.body = body
        # 현재 기준보다 오래된 본문 — 저장 본문 경로가 아니라 동일 원인 억제 판정으로 간다.
        item.content_philosophy_id = uuid.uuid4()

    return record


_RECORDS = [
    pytest.param(_gate_recorded, id="CONTENT_NOT_GENERATED"),
    *(
        pytest.param(_failed(reason), id=f"ENV-{reason}")
        for reason in sorted(generation_retry_policy._ENVIRONMENT_CODES)
    ),
    pytest.param(_failed("PROVIDER_TIMEOUT", times=4), id="ENV-PROVIDER_TIMEOUT-exhausted"),
    pytest.param(_sample_spent, id="SAMPLE-GENERATION_REJECTED"),
    pytest.param(_failed("CONTENT_AI_HARD_FINDING"), id="SAMPLE-CONTENT_AI_HARD_FINDING"),
    pytest.param(_swapped, id="SAMPLE-TOPIC_SWAPPED"),
    pytest.param(_failed("MISSING_APPROVED_ESSENCE"), id="INPUT-MISSING_APPROVED_ESSENCE"),
    pytest.param(_operator_decides, id="OPERATOR-MISSING_REFERENCES-operator_decides"),
    pytest.param(_with_body("이미 작성된 본문입니다.", _gate_recorded), id="written-CNG"),
    pytest.param(_with_body("이미 작성된 본문입니다.", _failed("PROVIDER_TIMEOUT")), id="written-ENV"),
    pytest.param(_with_body(" \n\t ", _gate_recorded), id="whitespace-CNG"),
    pytest.param(_with_body(" \n\t ", _failed("PROVIDER_TIMEOUT")), id="whitespace-ENV"),
]


@pytest.mark.parametrize("record_failure", _RECORDS)
def test_the_copy_predicate_agrees_with_what_the_real_task_releases(monkeypatch, record_failure):
    """`operator_retry_releases`는 실제 `regenerate_content_item`이 억제를 푸는 바로 그 경우에만 참이다.

    “태스크가 풀었다”는 태스크 자신의 해제 구간이 `_release_generation_attempt_for_repair`를 불렀다는
    뜻이다 — 같은 함수를 부르는 저장 본문 수리 경로의 호출은 세지 않는다.
    """

    philosophy, item, _writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    record_failure(monkeypatch, item, philosophy)
    predicted = operator_retry_releases(item)

    released: list = []
    release = tasks._release_generation_attempt_for_repair

    def spy(db, target):
        if sys._getframe(1).f_code.co_name == "regenerate_content_item":
            released.append(target.id)
        return release(db, target)

    monkeypatch.setattr(tasks, "_release_generation_attempt_for_repair", spy)
    _press_retry(monkeypatch, item, _PRESS, operator=True)

    assert bool(released) is predicted
