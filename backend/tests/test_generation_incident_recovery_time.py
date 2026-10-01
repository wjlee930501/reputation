"""원고 미생성 조치 문구가 말하는 다음 자동 복구 시각은 그 슬롯을 실제로 집는 스윕의 시각이다(#187 3차 B').

저장된 다음 시도 시각이 지난 기록은 "다음 스윕 시각"을 말했다. 그러나 23:00 야간 배치는 내일·모레
글만 보므로, 22시가 지나 다시 그려진 오늘 글의 문구가 '23:00에 다시 시도합니다'라고 했고 실제
다음 시도는 다음 날 01:00이었다. 시간별 발행기(8~23시)가 매시 문구를 다시 쓰므로 이 어긋남은
매일 보인다.

기대 시각은 문구를 만드는 함수(`next_recovery_deadline`)와 무관한 오라클에서 나온다 — celery
beat에 실제로 등록된 생성 스윕의 발화 시각, 그 태스크 본문이 실제로 쓰는 예정일 창, 그리고
로더가 claim 전에 쓰는 실제 적격 술어(`tasks._generation_retry_is_eligible`)다. 스윕 일정이나
창이 바뀌어 문구의 시각과 어긋나면 이 테스트가 실패한다. 실제 SQL로 같은 사실을 보는 것은
`tests/integration/test_announced_recovery_time_postgres.py`다.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest

from app.core.celery_app import celery_app
from app.services.notification_copy import display_time
from app.workers import tasks
from app.workers.generation_incident_control import (
    CONTENT_NOT_GENERATED_SCHEDULED_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_EARLY_OPEN_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION,
    CONTENT_NOT_GENERATED_UNSCHEDULED_ACTION,
    CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION,
    CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION,
    announced_recovery_time,
)
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.test_generation_incident_copy import _freeze, _open, _remember, _slot

KST = ZoneInfo("Asia/Seoul")
_D = date(2026, 9, 16)
_PHILOSOPHY = SimpleNamespace(id="p1")  # `_remember`가 시도 지문에 넣는 운영 기준과 같은 id
_NIGHTLY_TASK = "app.workers.tasks.nightly_content_generation"
_RECOVERY_TASK = "app.workers.tasks.overnight_content_generation_recovery"
_GENERATING_TASKS = {_NIGHTLY_TASK: "nightly_content_generation", _RECOVERY_TASK: "overnight_content_generation_recovery"}
_ORACLE_HORIZON_DAYS = 20
_KST_TIME = re.compile(r"\d\d/\d\d \d\d:\d\d KST")


def _at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=KST)


# ── 오라클: 실제 beat 일정 × 실제 태스크 창 × 실제 claim 술어 ────────────────────


def _beat_generation_fires(after: datetime) -> list[tuple[datetime, str]]:
    """beat가 생성 스윕 태스크를 부르는 시각들(``after`` 뒤, 시간순)."""

    fires: list[tuple[datetime, str]] = []
    entries = [
        entry for entry in celery_app.conf.beat_schedule.values() if entry["task"] in _GENERATING_TASKS
    ]
    assert {entry["task"] for entry in entries} == set(_GENERATING_TASKS)
    start = after.astimezone(KST).date()
    for entry in entries:
        schedule = entry["schedule"]
        # 요일·날짜·월 제한이 없는 매일 일정이어야 아래 열거가 맞다.
        assert len(schedule.day_of_week) == 7
        assert len(schedule.day_of_month) == 31
        assert len(schedule.month_of_year) == 12
        for offset in range(_ORACLE_HORIZON_DAYS + 1):
            day = start + timedelta(days=offset)
            for hour in schedule.hour:
                for minute in schedule.minute:
                    fire = _at(day, hour, minute)
                    if fire > after:
                        fires.append((fire, entry["task"]))
    return sorted(fires)


def _task_window(monkeypatch, task_name: str, fire: datetime) -> tuple[date, date]:
    """그 시각에 실제 태스크 본문이 로더에 넘기는 예정일 창."""

    captured: dict[str, tuple[date, date]] = {}

    def capture(_db, _recorder, window_start, window_end, **_kwargs):
        captured["window"] = (window_start, window_end)
        return 0

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks.arrow, "now", lambda tz=None: arrow.get(fire).to(tz or "UTC"))
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(tasks, "swap_exhausted_topics", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks,
        "GenerationBatchRecorder",
        lambda *_args, **_kwargs: SimpleNamespace(finish=lambda: None),
    )
    monkeypatch.setattr(tasks, "_dispatch_generation_batch", capture)
    monkeypatch.setattr(tasks, "_page_morning_stored_publication_gates", lambda *_a, **_k: None)
    getattr(tasks, _GENERATING_TASKS[task_name]).run()
    return captured["window"]


def _sweep_claims(monkeypatch, item, fire: datetime, task_name: str) -> bool:
    window_start, window_end = _task_window(monkeypatch, task_name, fire)
    if not window_start <= item.scheduled_date <= window_end:
        return False
    # 본문 없는 행은 SQL 술어(`_needs_generation_recovery`)를 통과한다 — 남은 것은 로더가 claim
    # 전에 쓰는 적격 술어다(워커의 SKIPPED 판정과 같은 규칙).
    _freeze(monkeypatch, fire.astimezone(UTC))
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda _db, _hid: _PHILOSOPHY)
    return tasks._generation_retry_is_eligible(object())(item)


def _oracle(monkeypatch, item, render: datetime) -> datetime | None:
    """``render`` 뒤에 이 슬롯을 실제로 claim하는 첫 생성 스윕 시각, 없으면 None."""

    try:
        for fire, task_name in _beat_generation_fires(render):
            if _sweep_claims(monkeypatch, item, fire, task_name):
                return fire.astimezone(UTC)
        return None
    finally:
        _freeze(monkeypatch, render.astimezone(UTC))


# ── 기록 만들기(실제 `_remember_generation_attempt`) ─────────────────────────────


def _failed_slot(monkeypatch, *, scheduled: date, record: str, failed_at: datetime):
    item = _slot()
    item.scheduled_date = scheduled
    if record == "ENV":
        _remember(monkeypatch, item, failed_at, "PROVIDER_TIMEOUT")
    elif record == "GENERATION_FAILED":
        _remember(monkeypatch, item, failed_at, "GENERATION_FAILED")
    elif record == "SAMPLE":
        _remember(monkeypatch, item, failed_at, "GENERATION_REJECTED")
    elif record == "SAMPLE_SPENT":  # 오늘 표본 예산(2회)을 다 썼다 — 다음 날 첫 스윕
        _remember(monkeypatch, item, failed_at - timedelta(minutes=30), "GENERATION_REJECTED")
        _remember(monkeypatch, item, failed_at, "GENERATION_REJECTED")
    else:  # pragma: no cover - 매개변수 오타
        raise AssertionError(record)
    stored = tasks._stored_generation_attempt(item)
    assert isinstance(stored["next_retry_at"], str)
    return item


def _stored_deadline(item) -> datetime:
    return datetime.fromisoformat(tasks._stored_generation_attempt(item)["next_retry_at"])


_TIMED = (
    CONTENT_NOT_GENERATED_SCHEDULED_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_EARLY_OPEN_ACTION,
)
_UNTIMED = (
    CONTENT_NOT_GENERATED_UNSCHEDULED_ACTION,
    CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION,
    CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION,
)


async def _assert_copy_names(monkeypatch, item, render: datetime, expected: datetime | None) -> str:
    """문구가 말하는 자동 복구 시각은 ``expected``뿐이다. 누름 게이트 시각(`{opens}`)은 복구 약속이 아니다."""

    _freeze(monkeypatch, render.astimezone(UTC))
    request, _incident = await _open(monkeypatch, item, "CONTENT_NOT_GENERATED")
    action = request.next_action
    times = _KST_TIME.findall(action)
    opens = times[-1] if times else None
    if expected is None:
        assert action in {template.format(opens=opens) for template in _UNTIMED}
        assert not action.startswith("자동 복구가 ")
        assert "다시 시도합니다" not in action
    else:
        assert times[0] == display_time(expected)
        assert action in {
            template.format(due=display_time(expected), opens=opens) for template in _TIMED
        }
    return action


# ── 08·12·18·22·23시 직전·직후 × 오늘·내일·지난 예정일 × 표본·환경 ─────────────────

_RENDERS = []
for _hour in (8, 12, 18, 22, 23):
    _RENDERS.append(pytest.param(_at(_D, _hour - 1, 59, 30), id=f"{_hour - 1:02d}:59:30"))
    _RENDERS.append(pytest.param(_at(_D, _hour, 0, 30), id=f"{_hour:02d}:00:30"))


@pytest.mark.asyncio
@pytest.mark.parametrize("render", _RENDERS)
@pytest.mark.parametrize(
    "offset", [0, 1, -3], ids=["today", "tomorrow", "past-scheduled"]
)
@pytest.mark.parametrize("record", ["ENV", "GENERATION_FAILED", "SAMPLE", "SAMPLE_SPENT"])
async def test_the_announced_time_is_the_first_sweep_that_claims_the_slot(
    monkeypatch, render, offset, record
):
    item = _failed_slot(
        monkeypatch, scheduled=_D + timedelta(days=offset), record=record, failed_at=_at(_D, 6)
    )
    expected = _oracle(monkeypatch, item, render)
    assert expected is not None  # 이 행렬은 모두 스윕이 다시 집는 슬롯이다

    assert announced_recovery_time(item, render.astimezone(UTC)) == expected
    await _assert_copy_names(monkeypatch, item, render, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["ENV", "GENERATION_FAILED"])
async def test_the_reviewer_probe_names_the_next_day_one_oclock_sweep(monkeypatch, record):
    """18:00 실패(다음 시도 22:00) → 22:00:30에 다시 그린 문구. 23:00 야간 배치는 오늘 글을 보지 않는다."""

    item = _failed_slot(monkeypatch, scheduled=_D, record=record, failed_at=_at(_D, 18))
    assert _stored_deadline(item) == _at(_D, 22)
    render = _at(_D, 22, 0, 30)

    expected = _oracle(monkeypatch, item, render)

    assert expected == _at(_D + timedelta(days=1), 1)
    assert announced_recovery_time(item, render.astimezone(UTC)) == expected
    action = await _assert_copy_names(monkeypatch, item, render, expected)
    assert "09/17 01:00 KST" in action
    assert "09/16 23:00 KST" not in action


# ── 어떤 스윕도 집지 않으면 시각을 말하지 않는다 ────────────────────────────────


def _past_due_record(**fields) -> dict:
    return {
        "context": tasks._generation_attempt_context(_slot(), _PHILOSOPHY),
        "attempt_period": _D.isoformat(),
        "next_retry_at": _at(_D, 7).astimezone(UTC).isoformat(),
        **fields,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record", "releasable"),
    [
        pytest.param(
            _past_due_record(
                reason="PROVIDER_TIMEOUT",
                retry_class=GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
                attempt_count=4,
                provider_attempt_count=4,
            ),
            True,
            id="ENV-budget-spent",
        ),
        pytest.param(
            _past_due_record(
                reason="TOPIC_SWAPPED",
                retry_class=GenerationRetryClass.SAMPLE_RECOVERABLE.value,
                provider_attempt_count=2,
                exhausted_days=3,
            ),
            False,
            id="SAMPLE-days-spent",
        ),
    ],
)
async def test_a_record_no_sweep_will_claim_names_no_time(monkeypatch, record, releasable):
    item = _slot()
    item.essence_check_summary = {"generation_attempt": dict(record)}
    render = _at(_D, 12, 0, 30)

    assert _oracle(monkeypatch, item, render) is None
    assert announced_recovery_time(item, render.astimezone(UTC)) is None
    action = await _assert_copy_names(monkeypatch, item, render, None)
    # 비해제형도 '지금은 눌러도 안 된다'고 하지 않는다 — 누른 실행이 풀리는 시각이 없다(#187 3차 차단).
    assert action == (
        CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION
        if releasable
        else CONTENT_NOT_GENERATED_UNSCHEDULED_CLOSED_ACTION
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "render",
    [_at(_D, 22, 0, 30), _at(_D, 23, 0, 30)],
    ids=["22:00:30", "23:00:30"],
)
async def test_a_slot_leaving_the_catchup_window_names_no_time(monkeypatch, render):
    """예정일이 오늘-7인 글 — 22시 스윕 뒤에는 어떤 생성 스윕도 이 글을 다시 집지 않는다.

    다음 판정은 22:30 백로그 복구(+1시간)지만 그것은 원고 생성 시도가 아니다. 그 시각을 '다시
    시도합니다'로 말하지 않는다.
    """

    item = _failed_slot(
        monkeypatch, scheduled=_D - timedelta(days=7), record="ENV", failed_at=_at(_D, 6)
    )

    assert _oracle(monkeypatch, item, render) is None
    assert announced_recovery_time(item, render.astimezone(UTC)) is None
    action = await _assert_copy_names(monkeypatch, item, render, None)
    assert action == CONTENT_NOT_GENERATED_UNSCHEDULED_RELEASABLE_ACTION


@pytest.mark.asyncio
async def test_a_stored_backlog_deadline_is_not_announced_as_an_attempt(monkeypatch):
    """저장된 기한이 아직 오지 않았어도 그것이 백로그 복구 판정 시각이면 원고 생성 시각이 아니다."""

    failed_at = _at(_D, 22, 10)
    item = _failed_slot(
        monkeypatch, scheduled=_D - timedelta(days=7), record="ENV", failed_at=failed_at
    )
    render = _at(_D, 22, 15)
    assert _stored_deadline(item) > render  # 저장된 기한(백로그 복구 뒤)은 아직 미래다

    assert _oracle(monkeypatch, item, render) is None
    assert announced_recovery_time(item, render.astimezone(UTC)) is None
    await _assert_copy_names(monkeypatch, item, render, None)


@pytest.mark.asyncio
async def test_a_future_stored_time_is_kept_when_an_earlier_sweep_would_not_claim(monkeypatch):
    """저장된 다음 시도 시각이 아직 오지 않았으면 그 시각이 약속이다 — 그 전 스윕은 기한 전이라 집지 않는다."""

    item = _slot()
    item.essence_check_summary = {
        "generation_attempt": _past_due_record(
            reason="PROVIDER_TIMEOUT",
            retry_class=GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
            attempt_count=1,
            provider_attempt_count=1,
            next_retry_at=_at(_D, 18).astimezone(UTC).isoformat(),
        )
    }
    render = _at(_D, 7, 59, 30)

    expected = _oracle(monkeypatch, item, render)

    assert expected == _at(_D, 18)  # 12:00 스윕은 기한 전이라 집지 않는다
    assert announced_recovery_time(item, render.astimezone(UTC)) == expected
    await _assert_copy_names(monkeypatch, item, render, expected)


# ── 예정일이 바뀐 뒤의 저장 시각(#187 3차: 22:30 백로그 이동·일정 변경, 리뷰 뮤턴트 n8) ─────────
# 백로그 복구(`content_backlog_recovery.reconcile`)와 일정 변경은 시도 기록을 두고 예정일만 바꾼다.
# 저장된 시각이 아직 오지 않았어도 그 시각의 스윕 창에 새 예정일이 없으면 그 스윕은 집지 않는다 —
# 그 시각 뒤 새 예정일을 창에 담는 첫 생성 스윕이 실제 시각이다.


def _moved(monkeypatch, *, scheduled: date, record: str, failed_at: datetime, moved_to: date):
    item = _failed_slot(monkeypatch, scheduled=scheduled, record=record, failed_at=failed_at)
    item.scheduled_date = moved_to
    return item


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["ENV", "SAMPLE"])
@pytest.mark.parametrize(
    ("scheduled", "failed_at", "stored", "moved_to", "render", "expected"),
    [
        # X 15:00 운영자 재시도가 창 밖(X-8) 글에서 실패 → 저장 23:30(백로그 판정) → 22:30 이동.
        pytest.param(
            _D - timedelta(days=8),
            _at(_D, 15),
            _at(_D, 23, 30),
            _D + timedelta(days=1),
            _at(_D, 22, 45),
            _at(_D + timedelta(days=1), 1),
            id="backlog-moved-to-tomorrow",
        ),
        pytest.param(
            _D - timedelta(days=8),
            _at(_D, 15),
            _at(_D, 23, 30),
            _D + timedelta(days=3),
            _at(_D, 22, 45),
            _at(_D + timedelta(days=1), 1),
            id="backlog-moved-past-the-nightly-window",
        ),
        # X+1 글이 X 22:00 스윕에서 실패 → 저장 23:00(야간 배치) → 예정일을 X로 당김.
        pytest.param(
            _D + timedelta(days=1),
            _at(_D, 22, 0, 4),
            _at(_D, 23),
            _D,
            _at(_D, 22, 10),
            _at(_D + timedelta(days=1), 1),
            id="pulled-into-today",
        ),
        # 같은 저장 23:00, 예정일을 창 안(X+2)으로 옮기면 그 시각이 그대로 실제 시각이다.
        pytest.param(
            _D + timedelta(days=1),
            _at(_D, 22, 0, 4),
            _at(_D, 23),
            _D + timedelta(days=2),
            _at(_D, 22, 10),
            _at(_D, 23),
            id="moved-inside-the-window",
        ),
    ],
)
async def test_a_stored_time_recorded_before_the_date_changed_names_the_sweep_that_claims_the_new_date(
    monkeypatch, record, scheduled, failed_at, stored, moved_to, render, expected
):
    item = _moved(
        monkeypatch, scheduled=scheduled, record=record, failed_at=failed_at, moved_to=moved_to
    )
    assert _stored_deadline(item) == stored
    assert stored > render  # 저장된 시각은 아직 미래다

    assert _oracle(monkeypatch, item, render) == expected
    assert announced_recovery_time(item, render.astimezone(UTC)) == expected
    await _assert_copy_names(monkeypatch, item, render, expected)


def test_the_oracle_itself_sees_both_generating_beat_entries():
    """오라클이 비어 있으면 모든 기대값이 None이 되어 테스트가 헛돈다."""

    fires = _beat_generation_fires(_at(_D, 0))
    hours = {fire.hour for fire, _task in fires if fire.date() == _D}
    assert {1, 4, 7, 12, 18, 22, 23} <= hours
    assert {task for _fire, task in fires} == set(_GENERATING_TASKS)
