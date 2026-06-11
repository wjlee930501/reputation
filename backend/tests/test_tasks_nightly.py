"""P1-3/R1 — 야간 생성 catch-up window + cap 절단 감지 + 아침 누락 경보 윈도우."""
from datetime import date
from types import SimpleNamespace

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


class _FakeSyncDB:
    """첫 execute는 batch 조회, 두 번째 execute는 overflow count 조회."""

    def __init__(self, items, total_count):
        self._results = [_Result(items=items), _Result(scalar=total_count)]
        self.execute_calls = 0

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result


def _items(n):
    return [SimpleNamespace(id=i) for i in range(n)]


def test_load_nightly_generation_batch_without_truncation():
    db = _FakeSyncDB(_items(3), total_count=3)

    items, truncated = tasks._load_nightly_generation_batch(db, date(2026, 6, 10), date(2026, 6, 11))

    assert len(items) == 3
    assert truncated == 0
    assert db.execute_calls == 1  # overflow count 조회 불필요


def test_load_nightly_generation_batch_detects_cap_truncation():
    cap = tasks.NIGHTLY_GENERATION_CAP
    db = _FakeSyncDB(_items(cap + 1), total_count=cap + 7)

    items, truncated = tasks._load_nightly_generation_batch(db, date(2026, 6, 10), date(2026, 6, 11))

    assert len(items) == cap
    assert truncated == 7  # 정확한 잔여 건수 보고
    assert db.execute_calls == 2


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
