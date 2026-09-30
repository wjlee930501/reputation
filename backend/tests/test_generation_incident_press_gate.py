"""원고 미생성 조치의 '“작업 다시 시도”를 눌러도 원고를 만들지 않는다'에는 시한이 있다(#187 3차 차단).

3차 문구의 시각 없는 비해제형("지금은 “작업 다시 시도”를 눌러도 원고를 만들지 않으니")은 렌더
시점에만 참이었다. D-7 빈 슬롯이 22:00 스윕에서 HARD 표본 실패(저장 기한 23:30, 백로그 판정 시각)
→ 23:00:30에 다시 그린 문구가 그것을 말했고, 23:45나 다음 날 12:00에 누르면 실제로는 작가를 한 번
불렀다. 그 글은 다음 날 발행기 catch-up 창에서 빠지고 22:30 백로그가 날짜를 옮기면 미래 글이라,
문구가 다시 그려지지 않아 거짓인 채로 하루 넘게 남는다.

이제 그 단정은 누름 게이트가 풀리는 시각까지만 말한다(`operator_retry_opens_at`). 이 시각은 복구
약속이 아니다 — 누른 실행의 같은 원인 억제(`tasks._generation_attempt_is_unchanged`)가 풀리는
시각이다. 게이트가 풀리지 않는 기록이면 '눌러도 안 된다'는 말 자체를 하지 않는다.

모든 누름은 실제 `regenerate_content_item`(Admin이 만든 실행)을 돌리고 작가 호출만 가짜로 센다.
"""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_operator_retry_gate_record import (
    _press_retry,
    _slot_with_writer,
    _spend_sample_budget_today,
    _swap_a_slot_scheduled_tomorrow,
)
from test_topic_swap_fallback import _kst, _WorkerDB

from app.workers import generation_incident_control, tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.test_generation_incident_copy import _freeze, _open

KST = ZoneInfo("Asia/Seoul")
_X = date(2026, 9, 16)
_PRESS_NOW = "“작업 다시 시도”를 눌러 지금 바로 다시 시도할 수 있습니다"
_PRESS_ANYTIME = "“작업 다시 시도”를 누르세요"
_OPENS_AT = re.compile(r"(\d\d/\d\d \d\d:\d\d) KST 전에는 “작업 다시 시도”를 눌러도 원고를 만들지 않고")
_DUE_THEN_WAIT = re.compile(
    r"자동 복구가 (\d\d/\d\d \d\d:\d\d) KST에 이 글의 원고 생성을 다시 시도합니다\. 그 전에는 “작업 다시 "
    r"시도”를 눌러도 원고를 만들지 않으니"
)
_UNSCHEDULED = "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다."
_OPENS_THEN = "그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 수 있습니다."


def _remember(monkeypatch, item, philosophy, moment, reason) -> dict:
    _freeze(monkeypatch, moment)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason)
    return dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])


def _gate_keeps(monkeypatch, item, philosophy, moment) -> None:
    """시간별 발행기·아침 게이트가 스윕 소유 기록을 원고 미생성으로 덮지 않는다(#182)."""

    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    _freeze(monkeypatch, moment)
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY] == record


def _action(monkeypatch, item, moment) -> str:
    _freeze(monkeypatch, moment)
    request, _incident = asyncio.run(_open(monkeypatch, item, "CONTENT_NOT_GENERATED"))
    return request.next_action


def _writes(monkeypatch, item, writer_calls, moment) -> int:
    """``moment``에 “작업 다시 시도”를 누른 실제 실행의 작가 호출 수. 누르기 전 상태로 되돌린다."""

    saved = copy.deepcopy(vars(item))
    before = len(writer_calls)
    try:
        _press_retry(monkeypatch, item, moment, operator=True)
        return len(writer_calls) - before
    finally:
        vars(item).clear()
        vars(item).update(saved)


def _kst_display(text: str) -> datetime:
    return datetime.strptime(f"2026/{text}", "%Y/%m/%d %H:%M").replace(tzinfo=KST)


def _claimed_opening(action: str, render: datetime) -> datetime | None:
    """문구가 '누르면 원고 생성을 다시 시도한다'고 말하는 첫 시각. 그런 말이 없으면 None."""

    if _PRESS_NOW in action or _PRESS_ANYTIME in action:
        return render
    for pattern in (_OPENS_AT, _DUE_THEN_WAIT):
        match = pattern.search(action)
        if match:
            return _kst_display(match[1])
    # 시한을 말할 수 없으면 '지금은/눌러도 안 된다'는 단정도 하지 않는다.
    assert "눌러도" not in action and "지금" not in action, action
    return None


# ── 리뷰 재현: D-7 빈 슬롯, 22:00 HARD 표본 실패, 23:00:30 렌더 ───────────────────

_SWEEP_FAILED_AT = _kst(2026, 9, 16, 22, 0, 4)
_RENDER_2300 = _kst(2026, 9, 16, 23, 0, 30)
_REVIEWER_ACTION = (
    "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다. 09/16 23:30 KST "
    "전에는 “작업 다시 시도”를 눌러도 원고를 만들지 않고, 그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 "
    "수 있습니다."
)


def _reviewer_slot(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    item.scheduled_date = _X - timedelta(days=7)
    record = _remember(monkeypatch, item, philosophy, _SWEEP_FAILED_AT, "CONTENT_AI_HARD_FINDING")
    assert record["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    # 어떤 생성 스윕도 이 글을 다시 집지 않는다 — 저장값은 22:30 백로그 복구의 판정 시각이다.
    assert generation_incident_control._stored_retry_deadline(record) == _kst(2026, 9, 16, 23, 30)
    _gate_keeps(monkeypatch, item, philosophy, _RENDER_2300)
    return item, writer_calls


@pytest.mark.parametrize(
    ("press", "writes"),
    [
        pytest.param(_kst(2026, 9, 16, 23, 10), 0, id="2310"),
        pytest.param(_kst(2026, 9, 16, 23, 45), 1, id="2345"),
        pytest.param(_kst(2026, 9, 17, 12, 0), 1, id="next-day-1200"),
    ],
)
def test_the_reviewer_probe_bounds_the_no_write_claim_by_the_press_gate(monkeypatch, press, writes):
    item, writer_calls = _reviewer_slot(monkeypatch)

    action = _action(monkeypatch, item, _RENDER_2300)

    assert action == _REVIEWER_ACTION
    assert "지금은" not in action
    assert _claimed_opening(action, _RENDER_2300) == _kst(2026, 9, 16, 23, 30)
    assert _writes(monkeypatch, item, writer_calls, press) == writes


def test_the_press_gate_is_not_announced_as_a_recovery(monkeypatch):
    """게이트 시각은 '자동 복구가 … 다시 시도합니다'로 말하지 않는다 — 23:30은 원고 생성 시도가 아니다."""

    item, _writer_calls = _reviewer_slot(monkeypatch)

    action = _action(monkeypatch, item, _RENDER_2300)

    assert action.startswith(_UNSCHEDULED)
    assert "다시 시도합니다" not in action
    assert generation_incident_control.announced_recovery_time(item, _RENDER_2300.astimezone(UTC)) is None


def test_the_press_gate_is_the_stored_time_when_the_budget_allows_it(monkeypatch):
    item, _writer_calls = _reviewer_slot(monkeypatch)

    opens = generation_incident_control.operator_retry_opens_at(item, _RENDER_2300.astimezone(UTC))

    assert opens == _kst(2026, 9, 16, 23, 30)
    assert not generation_incident_control.operator_retry_writes_now(item, opens - timedelta(seconds=1))
    assert generation_incident_control.operator_retry_writes_now(item, opens)


# ── 게이트가 풀리지 않는 기록 ────────────────────────────────────────────────────

_CLOSED_ACTION = (
    "예약된 자동 복구가 이 글의 원고 생성을 다시 시도할 시각이 정해져 있지 않습니다. 운영 센터에서 "
    "이 글의 상태를 확인하세요."
)


def _days_spent_slot(monkeypatch):
    """표본 소진 일수가 상한에 닿은 교체 기록 — 기한이 지나도 누른 실행이 같은 원인으로 건너뛴다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=True)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            "reason": "TOPIC_SWAPPED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "context": tasks._generation_attempt_context(item, philosophy),
            "attempt_period": _X.isoformat(),
            "provider_attempt_count": 2,
            "exhausted_days": 3,
            "next_retry_at": _kst(2026, 9, 16, 7).astimezone(UTC).isoformat(),
        }
    }
    return item, writer_calls


def test_a_record_the_press_gate_never_opens_for_makes_no_now_claim(monkeypatch):
    item, writer_calls = _days_spent_slot(monkeypatch)
    render = _kst(2026, 9, 16, 12, 0, 30)

    action = _action(monkeypatch, item, render)

    assert action == _CLOSED_ACTION
    assert generation_incident_control.operator_retry_opens_at(item, render.astimezone(UTC)) is None
    assert _claimed_opening(action, render) is None
    for press in (render + timedelta(minutes=10), _kst(2026, 9, 17, 12), _kst(2026, 9, 23, 12)):
        assert _writes(monkeypatch, item, writer_calls, press) == 0


# ── 날짜가 바뀐 뒤의 저장 시각(22:30 백로그 이동·일정 변경) ─────────────────────────


def _moved_slot(monkeypatch):
    """X 15:00 운영자 재시도가 X-8 글에서 표본 실패(저장 기한 X 23:30) → 22:30 백로그가 X+1로 옮김."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    item.scheduled_date = _X - timedelta(days=8)
    record = _remember(monkeypatch, item, philosophy, _kst(2026, 9, 16, 15), "GENERATION_REJECTED")
    assert generation_incident_control._stored_retry_deadline(record) == _kst(2026, 9, 16, 23, 30)
    item.scheduled_date = _X + timedelta(days=1)  # `content_backlog_recovery.reconcile`는 기록을 두고 날짜만 옮긴다
    return item, writer_calls


def _rescheduled_slot(monkeypatch):
    """X+1 글이 X 22:00 스윕에서 표본 실패(저장 기한 X 23:00 야간 배치) → 예정일을 X로 당김."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    item.scheduled_date = _X + timedelta(days=1)
    record = _remember(monkeypatch, item, philosophy, _SWEEP_FAILED_AT, "GENERATION_REJECTED")
    assert generation_incident_control._stored_retry_deadline(record) == _kst(2026, 9, 16, 23)
    item.scheduled_date = _X
    return item, writer_calls


@pytest.mark.parametrize(
    ("make", "render", "announced", "opens"),
    [
        pytest.param(
            _moved_slot, _kst(2026, 9, 16, 22, 45), "09/17 01:00", "09/16 23:30", id="backlog-moved"
        ),
        pytest.param(
            _rescheduled_slot, _kst(2026, 9, 16, 22, 10), "09/17 01:00", "09/16 23:00", id="rescheduled"
        ),
    ],
)
def test_a_moved_slot_names_the_real_pickup_and_bounds_the_press_claim_separately(
    monkeypatch, make, render, announced, opens
):
    item, writer_calls = make(monkeypatch)

    action = _action(monkeypatch, item, render)

    assert action == (
        f"자동 복구가 {announced} KST에 이 글의 원고 생성을 다시 시도합니다. {opens} KST 전에는 “작업 "
        "다시 시도”를 눌러도 원고를 만들지 않고, 그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 수 있습니다."
    )
    gate = _kst_display(opens)
    assert _writes(monkeypatch, item, writer_calls, gate - timedelta(minutes=1)) == 0
    assert _writes(monkeypatch, item, writer_calls, gate) == 1


# ── 하루 예산이 여는 게이트(KST 자정) ─────────────────────────────────────────────


def _spent_with_a_passed_time(monkeypatch):
    """오늘 표본 예산(2회)을 다 쓴 기록인데 저장 시각은 이미 지났다(옛 규칙이 남긴 기록 등).

    기한은 됐어도 오늘은 예산이 없어 누른 실행도 건너뛴다. 게이트는 기록 날짜가 바뀌는 자정에 풀린다.
    """

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _spend_sample_budget_today(item, philosophy, monkeypatch)
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    record["next_retry_at"] = _kst(2026, 9, 16, 7).astimezone(UTC).isoformat()
    return item, writer_calls


def test_a_spent_budget_opens_the_press_gate_at_midnight(monkeypatch):
    item, writer_calls = _spent_with_a_passed_time(monkeypatch)
    render = _kst(2026, 9, 16, 12, 0, 30)

    action = _action(monkeypatch, item, render)

    assert action == (
        "자동 복구가 09/17 01:00 KST에 이 글의 원고 생성을 다시 시도합니다. 09/17 00:00 KST 전에는 “작업 "
        "다시 시도”를 눌러도 원고를 만들지 않고, 그 뒤에는 원인이 풀렸으면 눌러 다시 시도할 수 있습니다."
    )
    assert _writes(monkeypatch, item, writer_calls, _kst(2026, 9, 16, 23, 59)) == 0
    assert _writes(monkeypatch, item, writer_calls, _kst(2026, 9, 17, 0, 0)) == 1


# ── 문구가 말한 게이트 시각 ⇔ 실제 누름이 처음 작가를 부르는 시각 ─────────────────────


def _gate_recorded(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")
    return item, writer_calls


def _environment(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _remember(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 0, 4), "PROVIDER_TIMEOUT")
    return item, writer_calls


def _sample_spent(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _spend_sample_budget_today(item, philosophy, monkeypatch)
    return item, writer_calls


def _sample_left(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _remember(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 0, 4), "CONTENT_AI_HARD_FINDING")
    return item, writer_calls


def _swapped(monkeypatch):
    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=True)
    _swap_a_slot_scheduled_tomorrow(item, philosophy, monkeypatch)
    return item, writer_calls


def _reviewer(monkeypatch):
    return _reviewer_slot(monkeypatch)


_GRID = [
    pytest.param(_gate_recorded, _kst(2026, 9, 16, 7, 45), id="CNG-0745"),
    pytest.param(_environment, _kst(2026, 9, 16, 7, 45), id="ENV-0745"),
    pytest.param(_sample_spent, _kst(2026, 9, 16, 7, 45), id="SAMPLE-spent-0745"),
    pytest.param(_sample_spent, _kst(2026, 9, 16, 23, 30), id="SAMPLE-spent-2330"),
    pytest.param(_sample_left, _kst(2026, 9, 16, 7, 45), id="SAMPLE-left-0745"),
    pytest.param(_sample_left, _kst(2026, 9, 16, 12, 0, 30), id="SAMPLE-left-1200"),
    pytest.param(_swapped, _kst(2026, 9, 16, 7, 45), id="SWAPPED-0745"),
    pytest.param(_swapped, _kst(2026, 9, 16, 22, 10), id="SWAPPED-2210"),
    pytest.param(_reviewer, _kst(2026, 9, 16, 22, 10), id="D-7-HARD-2210"),
    pytest.param(_reviewer, _RENDER_2300, id="D-7-HARD-2300"),
    pytest.param(_moved_slot, _kst(2026, 9, 16, 22, 45), id="moved-2245"),
    pytest.param(_rescheduled_slot, _kst(2026, 9, 16, 22, 10), id="rescheduled-2210"),
    pytest.param(_days_spent_slot, _kst(2026, 9, 16, 12, 0, 30), id="days-spent-1200"),
    pytest.param(_spent_with_a_passed_time, _kst(2026, 9, 16, 12, 0, 30), id="spent-passed-1200"),
]
_SCAN_STEP = timedelta(minutes=30)
_SCAN_HORIZON = timedelta(hours=36)


@pytest.mark.parametrize(("make", "render"), _GRID)
def test_the_rendered_press_gate_is_when_pressing_first_writes(monkeypatch, make, render):
    """문구가 말한 시각 전의 누름은 0회, 그 시각의 누름은 1회다. 30분 간격 훑기의 첫 쓰기도 그 뒤다."""

    item, writer_calls = make(monkeypatch)
    action = _action(monkeypatch, item, render)
    claimed = _claimed_opening(action, render)

    first = None
    moment = render
    while moment <= render + _SCAN_HORIZON:
        if _writes(monkeypatch, item, writer_calls, moment):
            first = moment
            break
        moment += _SCAN_STEP
    if claimed is None:
        assert first is None
        return
    assert first is not None and claimed <= first < claimed + _SCAN_STEP
    if claimed > render:
        assert _writes(monkeypatch, item, writer_calls, claimed - timedelta(seconds=1)) == 0
    assert _writes(monkeypatch, item, writer_calls, claimed) == 1
