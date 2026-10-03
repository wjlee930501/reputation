"""생성 문맥이 바뀐 빈 슬롯을 발행기 게이트가 지금 문맥의 사람 몫으로 굳히지 않는다.

운영 회귀(2026-10-02): #199가 gate catalog를 `2026-10-02.1`로 올려 진료비·병원 선택 빈 슬롯
4건(작가 0회)을 다시 후보로 만들었다. 스윕보다 먼저 09:00 매시 발행기의 게이트가 그 슬롯에
`CONTENT_NOT_GENERATED`(OPERATOR_REQUIRED·기한 없음)를 새 문맥으로 저장했고, 로더
(`_generation_retry_is_eligible` → `_generation_attempt_is_unchanged`)는 그 기록을 "같은 문맥·
기한 없음"으로 읽어 이후 모든 스윕에서 조용히 뺐다.

이제 게이트는 저장된 기록이 다른 생성 문맥의 것이면 기록하지 않는다 — 로더가 그 슬롯을 한 번
더 집는다. 기록이 없는 빈 슬롯과 같은 문맥의 실제 실패(공급자 시도 > 0)는 종전 규칙 그대로다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from test_topic_swap_fallback import (
    _approved_philosophy,
    _freeze,
    _kst,
    _swapped_slot,
    _WorkerDB,
)

from app.workers import tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass

_OLD_CATALOG = "2026-09-30.1"
_NEW_CATALOG = "2026-10-02.1"
_OLD_GATE_AT = _kst(2026, 9, 16, 8, 0)  # 옛 코드의 08:00 발행기
_HOURLY_GATE_AT = _kst(2026, 9, 16, 9, 0)  # 배포 뒤 첫 매시 발행기
_NEXT_SWEEP_AT = _kst(2026, 9, 16, 12, 0)


def _empty_slot(monkeypatch):
    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    item.topic_swap_history = []
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda _db, _hospital_id: philosophy)
    return philosophy, item


def _record(item) -> dict:
    return dict((item.essence_check_summary or {}).get(GENERATION_ATTEMPT_KEY) or {})


def _remember(monkeypatch, item, philosophy, moment, reason, **kwargs) -> dict:
    _freeze(monkeypatch, moment)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason, **kwargs)
    return _record(item)


def _gate(monkeypatch, item, philosophy, moment) -> None:
    _freeze(monkeypatch, moment)
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")


def _loader_takes(monkeypatch, item, moment) -> bool:
    _freeze(monkeypatch, moment)
    return tasks._generation_retry_is_eligible(_WorkerDB())(item)


def _old_context_record(monkeypatch, item, philosophy, reason: str, **kwargs) -> dict:
    monkeypatch.setattr(tasks, "GENERATION_GATE_CATALOG_VERSION", _OLD_CATALOG)
    record = _remember(monkeypatch, item, philosophy, _OLD_GATE_AT, reason, **kwargs)
    monkeypatch.setattr(tasks, "GENERATION_GATE_CATALOG_VERSION", _NEW_CATALOG)
    assert f"gate_catalog={_OLD_CATALOG};" in record["context"]
    return record


@pytest.mark.parametrize(
    ("reason", "kwargs"),
    [
        # 10/2 08:00(옛 코드) 발행기가 남긴 참고자료 보류 — 작가 0회.
        pytest.param("MISSING_REFERENCES", {"count_attempt": False}, id="old_0800_gate_record"),
        pytest.param(
            "MISSING_REFERENCES",
            {"count_attempt": False, "operator_decides": True},
            id="old_operator_decides_hold",
        ),
    ],
)
def test_hourly_gate_keeps_a_stale_context_record_and_the_sweep_takes_the_slot(
    monkeypatch, reason, kwargs
):
    """10/2 사례: 옛 문맥 기록 → catalog 갱신 배포 → 09:00 게이트 → 12:00 스윕이 집는다."""

    philosophy, item = _empty_slot(monkeypatch)
    old = _old_context_record(monkeypatch, item, philosophy, reason, **kwargs)
    assert old["provider_attempt_count"] == 0

    _gate(monkeypatch, item, philosophy, _HOURLY_GATE_AT)

    assert _record(item) == old  # 지금 문맥의 기한 없는 OPERATOR_REQUIRED로 덮지 않았다
    assert _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)
    # 매시 발행기가 다시 돌아도 같은 판정이다.
    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 16, 10, 0))
    assert _record(item) == old
    assert _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)


def test_stale_context_record_from_real_attempts_is_also_left_for_the_sweep(monkeypatch):
    """옛 문맥에서 예산을 다 쓴 실패도 문맥이 바뀌면 로더가 한 번 더 집는 종전 계약 그대로다."""

    philosophy, item = _empty_slot(monkeypatch)
    monkeypatch.setattr(tasks, "GENERATION_GATE_CATALOG_VERSION", _OLD_CATALOG)
    for moment in (_kst(2026, 9, 15, 23), _kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _kst(2026, 9, 16, 7)):
        old = _remember(monkeypatch, item, philosophy, moment, "PROVIDER_TIMEOUT")
    assert old["next_retry_at"] is None and old["provider_attempt_count"] > 0
    monkeypatch.setattr(tasks, "GENERATION_GATE_CATALOG_VERSION", _NEW_CATALOG)

    _gate(monkeypatch, item, philosophy, _HOURLY_GATE_AT)

    assert _record(item) == old
    assert _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)


def test_a_slot_without_any_record_is_still_recorded_by_the_gate(monkeypatch):
    """기록이 없는 빈 슬롯은 종전대로 게이트가 원고 미생성(사람의 몫)으로 남긴다."""

    philosophy, item = _empty_slot(monkeypatch)
    assert _loader_takes(monkeypatch, item, _OLD_GATE_AT)

    _gate(monkeypatch, item, philosophy, _HOURLY_GATE_AT)

    record = _record(item)
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    assert record["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert not _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)


def test_an_already_frozen_current_context_record_is_not_released_by_this_deploy(monkeypatch):
    """운영에 이미 저장된 4건의 모양(지금 문맥·작가 0회·기한 없음)은 배포만으로 풀리지 않는다.

    문맥(gate catalog)을 바꾸지 않았으므로 로더는 그대로 뺀다 — 운영센터 “작업 다시 시도”가
    이 기록의 억제를 푸는 경로다(`test_operator_retry_gate_record.py`).
    """

    philosophy, item = _empty_slot(monkeypatch)
    frozen = _remember(
        monkeypatch, item, philosophy, _HOURLY_GATE_AT, "CONTENT_NOT_GENERATED", count_attempt=False
    )
    assert f"gate_catalog={tasks.GENERATION_GATE_CATALOG_VERSION};" in frozen["context"]
    assert frozen["provider_attempt_count"] == 0
    assert "next_retry_at" not in frozen

    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 16, 10, 0))

    assert _record(item) == frozen
    assert not _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)


def test_a_current_context_provider_failure_still_waits_for_its_deadline(monkeypatch):
    """같은 문맥의 실제 실패(공급자 시도 1회)는 기한 전에는 빼고, 기한이 되면 집는다(회귀)."""

    philosophy, item = _empty_slot(monkeypatch)
    failed = _remember(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 0, 4), "PROVIDER_TIMEOUT")
    assert failed["provider_attempt_count"] == 1
    assert failed["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    due = datetime.fromisoformat(failed["next_retry_at"])

    _gate(monkeypatch, item, philosophy, _HOURLY_GATE_AT)

    assert _record(item) == failed
    assert not _loader_takes(monkeypatch, item, due - timedelta(minutes=1))
    assert _loader_takes(monkeypatch, item, due)


def test_a_current_context_exhausted_failure_stays_excluded(monkeypatch):
    """같은 문맥에서 예산을 다 쓴 실패는 게이트가 종전대로 덮고 로더는 계속 뺀다(회귀)."""

    philosophy, item = _empty_slot(monkeypatch)
    for moment in (_kst(2026, 9, 15, 23), _kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _kst(2026, 9, 16, 7)):
        exhausted = _remember(monkeypatch, item, philosophy, moment, "PROVIDER_TIMEOUT")
    assert exhausted["provider_attempt_count"] > 0
    assert exhausted["next_retry_at"] is None
    assert not _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)

    _gate(monkeypatch, item, philosophy, _HOURLY_GATE_AT)

    assert _record(item)["reason"] == "CONTENT_NOT_GENERATED"
    assert not _loader_takes(monkeypatch, item, _NEXT_SWEEP_AT)
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 17, 1, 0))
