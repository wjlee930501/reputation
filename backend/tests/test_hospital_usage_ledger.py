"""병원별 사용량 원장 — 귀속·창(오늘/이번 달)·비용 가드와의 분리를 고정한다.

이 원장은 관측 전용이다. 전역 cost_guard가 "우리 전체가 얼마나 썼는가"를 막는 장치라면,
원장은 "그 지출이 어느 병원 앞으로 나갔는가"만 기록한다. 그래서 여기서 지키는 것은 셋이다.

1. **병원 스코프** — A의 화면에 B의 호출이 한 건도 섞이면 안 된다.
2. **KST 창** — cost_guard가 KST 일/월로 세므로 원장도 같은 경계여야 한다. UTC로 자르면
   같은 호출이 두 화면에서 다른 날에 잡힌다.
3. **가드 불간섭** — 원장 기록이 cost_guard 예약 카운터(check_and_increment)를 건드리면
   관측하는 행위 자체가 상한을 소모하게 된다.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.admin import hospitals as hospitals_api
from app.services import hospital_usage, sov_engine
from app.services.hospital_usage import LEDGER_KINDS, aggregate_usage, record_usage

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 27, 10, 0, tzinfo=KST)


def _event(hospital_id, kind, *, created_at, input_tokens=0, output_tokens=0):
    return SimpleNamespace(
        hospital_id=hospital_id,
        kind=kind,
        created_at=created_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _one_param(params: dict, prefix: str):
    """컴파일된 바인드 파라미터에서 where 절 값 하나를 꺼낸다."""
    matches = [v for k, v in params.items() if k.startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one {prefix} predicate, got {matches}"
    return matches[0]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeUsageDB:
    """where 절을 실제로 적용하는 세션 대역.

    집계 쿼리에서 병원 스코프나 기간 하한이 빠지면 파라미터가 없어 즉시 실패한다 —
    "전 병원 합계를 한 병원 화면에 보여주는" 회귀를 대역 단계에서 잡기 위한 것이다.
    """

    def __init__(self, events, hospital=None):
        self.events = events
        self.hospital = hospital
        self.added = []

    async def get(self, _model, object_id):
        if self.hospital is not None and self.hospital.id == object_id:
            return self.hospital
        return None

    def add(self, item):
        self.added.append(item)

    async def execute(self, statement):
        params = statement.compile().params
        hospital_id = _one_param(params, "hospital_id")
        since = _one_param(params, "created_at")
        buckets: dict[str, list[int]] = {}
        for event in self.events:
            if event.hospital_id != hospital_id:
                continue
            if event.created_at < since:
                continue
            bucket = buckets.setdefault(event.kind, [0, 0, 0])
            bucket[0] += 1
            bucket[1] += event.input_tokens
            bucket[2] += event.output_tokens
        return _FakeResult([(kind, *totals) for kind, totals in buckets.items()])


# ── 병원 스코프 ─────────────────────────────────────────────────────────


async def test_aggregate_scopes_counts_and_tokens_to_one_hospital():
    """B의 호출과 토큰은 A의 집계에 한 건도 들어오지 않는다."""
    a, b = uuid.uuid4(), uuid.uuid4()
    db = FakeUsageDB([
        _event(a, "content", created_at=NOW, input_tokens=100, output_tokens=10),
        _event(a, "content", created_at=NOW, input_tokens=200, output_tokens=20),
        _event(b, "content", created_at=NOW, input_tokens=999, output_tokens=99),
        _event(b, "image", created_at=NOW),
    ])

    totals_a = await aggregate_usage(db, a, now=NOW)
    totals_b = await aggregate_usage(db, b, now=NOW)

    assert totals_a["content"]["monthly"] == {
        "count": 2, "input_tokens": 300, "output_tokens": 30,
    }
    assert totals_a["image"]["monthly"]["count"] == 0
    assert totals_b["content"]["monthly"] == {
        "count": 1, "input_tokens": 999, "output_tokens": 99,
    }
    assert totals_b["image"]["monthly"]["count"] == 1


async def test_every_kind_is_present_with_zeros_when_unused():
    """쓰지 않은 구분이 표에서 사라지면 '측정 안 함'과 '0회'를 구분할 수 없다."""
    hospital_id = uuid.uuid4()
    totals = await aggregate_usage(FakeUsageDB([]), hospital_id, now=NOW)

    assert set(totals) == set(LEDGER_KINDS)
    assert set(LEDGER_KINDS) == {"onboarding", "content", "image", "sov"}
    for kind in LEDGER_KINDS:
        for window in ("daily", "monthly"):
            assert totals[kind][window] == {"count": 0, "input_tokens": 0, "output_tokens": 0}


# ── KST 창 ──────────────────────────────────────────────────────────────


async def test_daily_and_monthly_windows_use_kst_boundaries():
    """어제(같은 달)는 이번 달에만, 지난달은 어느 창에도 잡히지 않는다."""
    hospital_id = uuid.uuid4()
    db = FakeUsageDB([
        _event(hospital_id, "sov", created_at=NOW - timedelta(hours=1),
               input_tokens=10, output_tokens=1),
        _event(hospital_id, "sov", created_at=NOW - timedelta(days=1),
               input_tokens=20, output_tokens=2),
        _event(hospital_id, "sov", created_at=datetime(2026, 7, 20, 9, 0, tzinfo=KST),
               input_tokens=40, output_tokens=4),
    ])

    totals = await aggregate_usage(db, hospital_id, now=NOW)

    assert totals["sov"]["daily"] == {"count": 1, "input_tokens": 10, "output_tokens": 1}
    assert totals["sov"]["monthly"] == {"count": 2, "input_tokens": 30, "output_tokens": 3}


async def test_month_boundary_follows_seoul_not_utc():
    """UTC로 7월 말이어도 KST로 8월 1일이면 이번 달이다 (cost_guard와 같은 경계)."""
    hospital_id = uuid.uuid4()
    # 2026-07-31T15:30Z == 2026-08-01T00:30 KST
    db = FakeUsageDB([
        _event(hospital_id, "image",
               created_at=datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)),
    ])

    totals = await aggregate_usage(db, hospital_id, now=NOW)

    assert totals["image"]["monthly"]["count"] == 1
    assert totals["image"]["daily"]["count"] == 0


def test_window_starts_are_midnight_seoul():
    day_start, month_start = hospital_usage._window_starts(
        datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)  # == 10:00 KST
    )
    assert (day_start.year, day_start.month, day_start.day) == (2026, 8, 27)
    assert (month_start.year, month_start.month, month_start.day) == (2026, 8, 1)
    assert day_start.hour == month_start.hour == 0
    assert day_start.tzinfo == KST and month_start.tzinfo == KST


# ── 기록은 파이프라인을 깨지 않는다 ──────────────────────────────────────


async def test_record_usage_skips_missing_hospital_id_without_raising():
    """귀속할 병원이 없으면 조용히 건너뛴다 — 원장 때문에 공급자 작업이 죽으면 안 된다."""
    db = FakeUsageDB([])
    await record_usage(hospital_id=None, kind="sov", input_tokens=5, output_tokens=5, db=db)
    assert db.added == []


async def test_record_usage_skips_unknown_kind_without_raising():
    db = FakeUsageDB([])
    await record_usage(hospital_id=uuid.uuid4(), kind="mystery", db=db)
    assert db.added == []


async def test_record_usage_does_not_touch_cost_guard_reservations(monkeypatch):
    """관측이 상한을 소모하면 '보기만 해도 예산이 준다' — 가드 API를 절대 부르지 않는다."""
    from app.services import cost_guard

    async def _forbidden(*_args, **_kwargs):
        raise AssertionError("usage ledger must not call cost_guard")

    monkeypatch.setattr(cost_guard, "check_and_increment", _forbidden)
    monkeypatch.setattr(cost_guard, "record_provider_call", _forbidden)

    db = FakeUsageDB([])
    await record_usage(hospital_id=uuid.uuid4(), kind="content", input_tokens=7, output_tokens=3, db=db)

    assert len(db.added) == 1
    assert db.added[0].kind == "content"
    assert (db.added[0].input_tokens, db.added[0].output_tokens) == (7, 3)


async def test_record_usage_stamps_created_at_in_python():
    """시각을 DB server_default에만 맡기면 flush 전 행에는 created_at이 없다.

    그 상태로는 오늘/이번 달 창이 시각 없는 행을 만나 집계에서 통째로 빠지거나 터진다.
    기록 시점에 파이썬에서 찍어 두면 어떤 세션에서도 창 경계가 성립한다.
    """
    before = datetime.now(KST)
    db = FakeUsageDB([])
    await record_usage(hospital_id=uuid.uuid4(), kind="sov", db=db)
    after = datetime.now(KST)

    created_at = db.added[0].created_at
    assert created_at is not None
    assert created_at.tzinfo is not None  # naive면 KST 창과 비교할 수 없다
    assert before <= created_at <= after


# ── sov 귀속 ────────────────────────────────────────────────────────────


@pytest.fixture
def recorded_usage(monkeypatch):
    calls: list[dict] = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(hospital_usage, "record_usage", _record)
    return calls


async def _record_sov_usage_with(pool: str, hospital_id, recorded):
    category_token = sov_engine._provider_cost_category.set(pool)
    hospital_token = sov_engine._provider_hospital_id.set(hospital_id)
    try:
        await sov_engine._record_sov_usage(11, 22)
    finally:
        sov_engine._provider_cost_category.reset(category_token)
        sov_engine._provider_hospital_id.reset(hospital_token)
    return recorded


async def test_leadgen_is_never_written_to_the_ledger(recorded_usage):
    """무료 진단은 원장에 한 건도 남지 않는다 — 병원이 정해져 있어도 마찬가지다.

    원장은 계약 병원의 운영 사용량만 본다. 리드마그넷 호출이 한 줄이라도 섞이면 그 표는
    "이 병원 앞으로 나간 운영 지출"이 아니게 된다. 무료 진단의 예산 집계는 cost_guard의
    leadgen 카테고리가 이미 따로 맡고 있으므로 여기서 다시 셀 이유도 없다.
    """
    assert "leadgen" not in LEDGER_KINDS

    # 귀속할 병원이 없는 익명 리드 진단
    assert await _record_sov_usage_with(sov_engine.POOL_LEADGEN, None, recorded_usage) == []

    # 병원이 이미 정해진 뒤 돌린 무료 진단도 남기지 않는다
    hospital_id = uuid.uuid4()
    assert await _record_sov_usage_with(sov_engine.POOL_LEADGEN, hospital_id, recorded_usage) == []

    # 원장 API로 직접 밀어 넣어도 거부한다 — 구분이 아니므로 unknown kind로 걸린다
    db = FakeUsageDB([])
    await record_usage(hospital_id=hospital_id, kind="leadgen", input_tokens=9, db=db)
    assert db.added == []


async def test_paid_sov_pool_records_the_sov_kind(recorded_usage):
    hospital_id = uuid.uuid4()
    calls = await _record_sov_usage_with(sov_engine.POOL_SOV, hospital_id, recorded_usage)
    assert [(c["hospital_id"], c["kind"]) for c in calls] == [(hospital_id, "sov")]


async def test_sov_usage_skips_when_hospital_is_unknown(recorded_usage):
    calls = await _record_sov_usage_with(sov_engine.POOL_SOV, None, recorded_usage)
    assert calls == []


# ── Admin API ───────────────────────────────────────────────────────────


async def test_admin_usage_endpoint_scopes_by_hospital_and_returns_both_windows():
    """엔드포인트는 프로덕션과 같은 '지금'으로 집계한다.

    그래서 고정 날짜로 심으면 오늘이 그 날이 아닌 순간 전부 창 밖으로 밀려난다.
    실제 now(KST) 기준으로 심어, 어느 날 돌려도 같은 경계를 검증한다.
    """
    a, b = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(KST)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    today = day_start + timedelta(minutes=1)
    earlier = day_start - timedelta(minutes=1)  # 어제 23:59 KST
    # 매달 1일에는 "이번 달이면서 오늘은 아닌" 시각이 존재하지 않는다.
    earlier_is_this_month = earlier >= month_start

    hospital = SimpleNamespace(id=a, name="테스트의원")
    db = FakeUsageDB(
        [
            _event(a, "content", created_at=today, input_tokens=100, output_tokens=10),
            _event(a, "content", created_at=earlier, input_tokens=50, output_tokens=5),
            # 지난달 — 어느 창에도 잡히면 안 된다.
            _event(a, "content", created_at=month_start - timedelta(days=1),
                   input_tokens=400, output_tokens=40),
            _event(b, "content", created_at=today, input_tokens=777, output_tokens=77),
        ],
        hospital=hospital,
    )

    response = await hospitals_api.get_hospital_usage(a, db=db)

    assert response.hospital_id == str(a)
    assert [k.kind for k in response.kinds] == list(LEDGER_KINDS)

    content = next(k for k in response.kinds if k.kind == "content")
    # B의 777도, 지난달 400도 섞이지 않는다.
    assert (content.daily.count, content.daily.input_tokens, content.daily.output_tokens) == (
        1, 100, 10,
    )
    expected_monthly = (2, 150, 15) if earlier_is_this_month else (1, 100, 10)
    assert (
        content.monthly.count, content.monthly.input_tokens, content.monthly.output_tokens
    ) == expected_monthly

    # 쓰지 않은 구분도 0으로 존재한다 — '측정 안 함'과 '0회'는 다르다.
    assert [k.kind for k in response.kinds] == ["onboarding", "content", "image", "sov"]
    for kind in ("onboarding", "image", "sov"):
        unused = next(k for k in response.kinds if k.kind == kind)
        assert unused.label
        assert (unused.daily.count, unused.monthly.count) == (0, 0)
