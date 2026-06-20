"""P1-3/R1 — 야간 생성 catch-up window + cap 절단 감지 + 아침 누락 경보 윈도우."""
from datetime import date
from types import SimpleNamespace

import arrow
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.content import ContentItem
from app.models.hospital import HospitalStatus
from app.workers import tasks


def test_nightly_generation_stmt_covers_window_bounds():
    """야간 배치가 며칠 누락돼도 catch-up window 안의 슬롯을 다시 집어야 한다."""
    window_start = date(2026, 6, 3)
    tomorrow = date(2026, 6, 11)

    stmt = tasks._nightly_generation_stmt(window_start, tomorrow)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "scheduled_date >= '2026-06-03'" in sql
    assert "scheduled_date <= '2026-06-11'" in sql
    assert "body IS NULL" in sql
    # cap+1로 읽어 절단 발생을 감지한다
    assert f"LIMIT {tasks.NIGHTLY_GENERATION_CAP + 1}" in sql


def test_generation_catchup_window_is_seven_days():
    """야간 catch-up과 아침 누락 경보가 공유하는 윈도우 (R1) — 경보 문구의 약속과 결합."""
    assert tasks.GENERATION_CATCHUP_DAYS == 7


def test_nightly_generation_orders_carried_over_items_first():
    """전월 이월(carried_over_from) 슬롯이 cap 안에서 가장 먼저 생성돼야 한다."""
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    order_clause = sql.split("ORDER BY", 1)[1]
    assert "carried_over_from IS NOT NULL DESC" in order_clause
    # 이월 우선 정렬이 발행 예정일 정렬보다 앞선다.
    assert order_clause.index("carried_over_from") < order_clause.index("scheduled_date")


def test_nightly_generation_stmt_uses_row_level_claiming():
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
    assert "generation_claimed_at IS NULL" in sql


def test_content_item_schedule_slots_have_db_uniqueness():
    indexes = {index.name: index for index in ContentItem.__table__.indexes}
    slot_index = indexes.get("uq_content_items_schedule_slot")

    assert slot_index is not None
    assert slot_index.unique is True


def test_v0_report_requires_at_least_one_successful_measurement():
    with pytest.raises(RuntimeError, match="zero successful"):
        tasks._ensure_v0_has_successful_measurements(success_count=0, failure_count=5)


def test_monthly_report_failures_are_raised_for_celery_autoretry():
    with pytest.raises(RuntimeError, match="월간 리포트 실패"):
        tasks._raise_if_monthly_report_failures([("장편한외과의원", RuntimeError("pdf boom"))])


class _NestedSlotTransaction:
    def __init__(self, db):
        self._db = db

    def __enter__(self):
        self._db._staged = []
        return self

    def __exit__(self, exc_type, *_exc):
        if exc_type is None:
            self._db.persisted.extend(self._db._staged)
        self._db._staged = None
        return False


class _MonthlySlotDB:
    def __init__(self, schedules):
        self._results = [_Result(items=schedules), _Result(scalar=None), _Result(scalar=None)]
        self.execute_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.persisted = []
        self._staged = None

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def begin_nested(self):
        return _NestedSlotTransaction(self)

    def add(self, item):
        assert self._staged is not None
        self._staged.append(item)

    def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 2:
            raise IntegrityError("insert", {}, Exception("duplicate slot"))

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1
        self.persisted.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_monthly_slot_generation_keeps_prior_success_when_later_schedule_conflicts(monkeypatch):
    hospitals = [
        SimpleNamespace(id="h1", name="첫번째의원", status=HospitalStatus.ACTIVE),
        SimpleNamespace(id="h2", name="두번째의원", status=HospitalStatus.ACTIVE),
    ]
    schedules = [
        SimpleNamespace(id="s1", hospital=hospitals[0], plan="PLAN_4", publish_days=[0]),
        SimpleNamespace(id="s2", hospital=hospitals[1], plan="PLAN_4", publish_days=[0]),
    ]
    db = _MonthlySlotDB(schedules)

    monkeypatch.setattr(tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(2026, 6, 25, tzinfo="Asia/Seoul"))
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.workers.monthly_slots.generate_monthly_slots",
        lambda *_args, **_kwargs: [(date(2026, 7, 1), "FAQ", 1, 1)],
    )

    tasks.monthly_slot_generation()

    assert db.rollback_calls == 0
    assert db.commit_calls == 1
    assert len(db.persisted) == 1
    assert db.persisted[0].hospital_id == "h1"


def test_morning_missed_stmt_bounds_and_filters():
    """R1 — 누락 경보는 catch-up 윈도우 내, ACTIVE 병원, 승인된 운영 기준 보유만 본다."""
    today = date(2026, 6, 10)

    stmt = tasks._morning_missed_stmt(today)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "scheduled_date <= '2026-06-10'" in sql
    # 야간 catch-up 윈도우와 동일한 하한 — 윈도우 밖 슬롯은 영원히 재경보되지 않는다.
    assert "scheduled_date >= '2026-06-03'" in sql
    assert "body IS NULL" in sql
    assert "JOIN hospitals" in sql
    assert "'ACTIVE'" in sql
    # 운영 기준 미승인 병원은 전용 '생성 차단' 알림이 커버하므로 제외.
    assert "IN (SELECT" in sql
    assert "'APPROVED'" in sql


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, items=None, scalar=None):
        self._items = items
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._items)

    def scalar_one(self):
        return self._scalar

    def scalar(self):
        return self._scalar


class _FakeSyncDB:
    """첫 execute는 batch 조회, 두 번째 execute는 overflow count 조회."""

    def __init__(self, items, total_count):
        self._results = [_Result(items=items), _Result(scalar=total_count)]
        self.execute_calls = 0
        self.commit_calls = 0

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def commit(self):
        self.commit_calls += 1


def _items(n):
    return [SimpleNamespace(id=i) for i in range(n)]


def test_load_nightly_generation_batch_without_truncation():
    db = _FakeSyncDB(_items(3), total_count=3)

    items, truncated = tasks._load_nightly_generation_batch(db, date(2026, 6, 10), date(2026, 6, 11))

    assert len(items) == 3
    assert truncated == 0
    assert db.execute_calls == 1  # overflow count 조회 불필요
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


def test_load_nightly_generation_batch_detects_cap_truncation():
    cap = tasks.NIGHTLY_GENERATION_CAP
    db = _FakeSyncDB(_items(cap + 1), total_count=cap + 7)

    items, truncated = tasks._load_nightly_generation_batch(db, date(2026, 6, 10), date(2026, 6, 11))

    assert len(items) == cap
    assert truncated == 7  # 정확한 잔여 건수 보고
    assert db.execute_calls == 2
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


# ── R1c — 아침 알림 done-key: 누락 경보 실패가 초안 알림 dedupe를 막지 않는다 ──


class _MorningDB:
    """첫 execute = 초안 완료 조회, 두 번째 execute = 생성 누락 조회."""

    def __init__(self, draft_items, missed_items):
        self._results = [_Result(items=draft_items), _Result(items=missed_items)]
        self.execute_calls = 0

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _draft_item():
    hospital = SimpleNamespace(id="h1", name="테스트의원")
    return SimpleNamespace(
        id="c1",
        hospital_id="h1",
        hospital=hospital,
        sequence_no=1,
        total_count=8,
        content_type=SimpleNamespace(value="FAQ"),
        scheduled_date=date(2026, 6, 10),
        carried_over_from=None,
    )


def _missed_item():
    hospital = SimpleNamespace(id="h2", name="누락의원")
    return SimpleNamespace(
        id="c2",
        hospital_id="h2",
        hospital=hospital,
        scheduled_date=date(2026, 6, 8),
    )


def _run_morning(monkeypatch, *, draft_sent: bool, missed_sent: bool):
    db = _MorningDB([_draft_item()], [_missed_item()])
    marked: list[str] = []

    async def fake_draft_ready(**_kw):
        return draft_sent

    async def fake_generation_missed(**_kw):
        return missed_sent

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "_already_done", lambda _key: False)
    monkeypatch.setattr(tasks, "_mark_done", lambda key, **_kw: marked.append(key))
    monkeypatch.setattr(tasks.notifier, "notify_content_draft_ready", fake_draft_ready)
    monkeypatch.setattr(tasks.notifier, "notify_content_generation_missed", fake_generation_missed)

    tasks.morning_content_notification()
    return marked


def test_morning_marks_done_even_when_missed_alert_fails(monkeypatch):
    """누락 경보 Slack 실패가 done-key를 막으면 같은 날 재트리거 때 초안 알림이 전부 중복된다 (R1c)."""
    marked = _run_morning(monkeypatch, draft_sent=True, missed_sent=False)
    assert len(marked) == 1


def test_morning_does_not_mark_done_when_draft_alert_fails(monkeypatch):
    """초안 알림 자체가 실패하면 claim-after-success 유지 — 재트리거가 다시 시도해야 한다."""
    marked = _run_morning(monkeypatch, draft_sent=False, missed_sent=True)
    assert marked == []
