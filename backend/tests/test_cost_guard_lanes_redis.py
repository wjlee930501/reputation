"""content 비용 lane(Full-Automatic W1) — 실제 Redis에서 Lua 판정·증가·정산을 본다(COST_GUARD_REDIS_URL 필요)."""

import uuid
from datetime import date, datetime

import pytest
import redis
import redis.asyncio as redis_async

from app.services import cost_guard
from tests.db_env import require_redis_url

# 다른 테스트·운영 키와 겹치지 않는 먼 기간.
NOW = datetime(2031, 1, 15, 12, 0, tzinfo=cost_guard._KST)
DAY = "20310115"
MONTH = "203101"
HOSPITAL_A = str(uuid.uuid4())
HOSPITAL_B = str(uuid.uuid4())


def _keys(client: redis.Redis) -> list:
    return list(client.scan_iter(match=f"cost_guard:content:*{DAY}*")) + list(
        client.scan_iter(match=f"cost_guard:content:*{MONTH}")
    )


@pytest.fixture
def lane_env(monkeypatch):
    url = require_redis_url("COST_GUARD_REDIS_URL")
    sync = redis.Redis.from_url(url)
    for key in _keys(sync):
        sync.delete(key)
    monkeypatch.setattr(cost_guard, "_now", lambda: NOW)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_LANES_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_DAILY_CONTENT_CALLS", 200)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_MONTHLY_CONTENT_CALLS", 10_000)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_SCHEDULE_RESERVE", 150)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_NON_SCHEDULE_MONTHLY_PAUSE_RATIO", 0.85)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_ADHOC_DAILY", 3)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_ADHOC_MONTHLY", 400)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_NON_SCHEDULE_HOSPITAL_DAILY", 30)

    async def _no_alert(*_args, **_kwargs):
        return None

    # 카테고리 경보는 이 파일의 관심 밖이다(내구 알림은 DB를 쓴다).
    monkeypatch.setattr(cost_guard, "_evaluate_scope_alert", _no_alert)
    monkeypatch.setattr(cost_guard, "_best_effort_alert", _no_alert)
    yield url, sync
    for key in _keys(sync):
        sync.delete(key)
    sync.close()


async def _reserve(client, lane, *, count=1, hospital_id=None):
    return await cost_guard.reserve(
        "content",
        count=count,
        reserved_at=NOW,
        lane=lane,
        hospital_id=hospital_id,
        redis_client=client,
    )


def _int(sync: redis.Redis, key: str) -> int:
    return int(sync.get(key) or 0)


async def test_schedule_reserve_keeps_the_last_150_calls_for_on_schedule_generation(lane_env):
    url, sync = lane_env
    client = redis_async.from_url(url)
    try:
        # 일정 외 lane은 일 상한 200 − 예약 150 = 50까지만.
        for _ in range(5):
            assert (await _reserve(client, cost_guard.LANE_BACKGROUND, count=10)).allowed
        blocked = await _reserve(client, cost_guard.LANE_BACKGROUND)
        assert blocked.allowed is False
        assert "예정 글 생성 몫" in blocked.reason
        # 예정 글 생성은 남은 150을 다 쓴다.
        for _ in range(15):
            assert (await _reserve(client, cost_guard.LANE_SCHEDULE, count=10)).allowed
        assert (await _reserve(client, cost_guard.LANE_SCHEDULE)).allowed is False
        assert _int(sync, cost_guard._daily_key("content", DAY)) == 200
        assert _int(sync, cost_guard._lane_daily_key("schedule", DAY)) == 150
        assert _int(sync, cost_guard._lane_daily_key("background", DAY)) == 50
    finally:
        await client.aclose()


async def test_lane_cap_blocks_only_that_lane(lane_env):
    url, sync = lane_env
    client = redis_async.from_url(url)
    try:
        for _ in range(3):
            assert (await _reserve(client, cost_guard.LANE_ADHOC)).allowed
        blocked = await _reserve(client, cost_guard.LANE_ADHOC)
        assert blocked.allowed is False and "일일 상한(3건)" in blocked.reason
        assert (await _reserve(client, cost_guard.LANE_EVIDENCE)).allowed
        # 막힌 시도는 어느 카운터도 올리지 않는다.
        assert _int(sync, cost_guard._daily_key("content", DAY)) == 4
        assert _int(sync, cost_guard._lane_daily_key("adhoc", DAY)) == 3
    finally:
        await client.aclose()


async def test_oversized_first_reservation_of_the_day_is_not_stuck_forever(lane_env, monkeypatch):
    url, sync = lane_env
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_NON_SCHEDULE_HOSPITAL_DAILY", 4)
    client = redis_async.from_url(url)
    try:
        # 상한(adhoc 3·병원 4)보다 큰 한 건도 그날 첫 예약이면 들어간다(예약 천장 50 안).
        assert (await _reserve(client, cost_guard.LANE_ADHOC, count=9, hospital_id=HOSPITAL_A)).allowed
        assert (await _reserve(client, cost_guard.LANE_ADHOC, hospital_id=HOSPITAL_B)).allowed is False
        assert (
            await _reserve(client, cost_guard.LANE_EVIDENCE, hospital_id=HOSPITAL_A)
        ).allowed is False
    finally:
        await client.aclose()


async def test_monthly_pause_stops_non_schedule_lanes_at_85_percent(lane_env):
    url, sync = lane_env
    sync.set(cost_guard._monthly_key("content", MONTH), 8_500)
    client = redis_async.from_url(url)
    try:
        paused = await _reserve(client, cost_guard.LANE_POST_PUBLISH)
        assert paused.allowed is False and "85%" in paused.reason
        assert (await _reserve(client, cost_guard.LANE_SCHEDULE)).allowed
        assert _int(sync, cost_guard._monthly_key("content", MONTH)) == 8_501
    finally:
        await client.aclose()


async def test_per_hospital_non_schedule_cap_spans_lanes(lane_env, monkeypatch):
    url, sync = lane_env
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_NON_SCHEDULE_HOSPITAL_DAILY", 4)
    client = redis_async.from_url(url)
    try:
        assert (await _reserve(client, cost_guard.LANE_EVIDENCE, count=3, hospital_id=HOSPITAL_A)).allowed
        assert (await _reserve(client, cost_guard.LANE_POST_PUBLISH, hospital_id=HOSPITAL_A)).allowed
        blocked = await _reserve(client, cost_guard.LANE_POST_PUBLISH, hospital_id=HOSPITAL_A)
        assert blocked.allowed is False and "이 병원" in blocked.reason
        assert (await _reserve(client, cost_guard.LANE_POST_PUBLISH, hospital_id=HOSPITAL_B)).allowed
        # 예정 글 생성은 병원 상한을 받지 않고 병원 카운터도 올리지 않는다.
        assert (await _reserve(client, cost_guard.LANE_SCHEDULE, hospital_id=HOSPITAL_A)).allowed
        assert _int(sync, cost_guard._hospital_daily_key(HOSPITAL_A, DAY)) == 4
    finally:
        await client.aclose()


async def test_settlement_returns_unused_units_to_lane_and_hospital_counters(lane_env):
    url, sync = lane_env
    client = redis_async.from_url(url)
    try:
        decision = await _reserve(
            client, cost_guard.LANE_EVIDENCE, count=6, hospital_id=HOSPITAL_A
        )
        assert decision.allowed and decision.receipt is not None
        settled = await cost_guard.settle_reservation(
            decision.receipt, consumed_units=2, redis_client=client
        )
        assert settled is not None and settled.released_units == 4
        assert _int(sync, cost_guard._daily_key("content", DAY)) == 2
        assert _int(sync, cost_guard._lane_daily_key("evidence", DAY)) == 2
        assert _int(sync, cost_guard._lane_monthly_key("evidence", MONTH)) == 2
        assert _int(sync, cost_guard._hospital_daily_key(HOSPITAL_A, DAY)) == 2
    finally:
        sync.delete(cost_guard._reservation_key(decision.receipt.id))
        await client.aclose()


async def test_context_lane_applies_to_check_and_increment(lane_env):
    url, sync = lane_env
    client = redis_async.from_url(url)
    try:
        with cost_guard.cost_lane(cost_guard.LANE_ADHOC, hospital_id=HOSPITAL_B) as scope:
            assert scope.hospital_id == HOSPITAL_B
            for _ in range(3):
                assert (await cost_guard.check_and_increment("content", redis_client=client)).allowed
            assert (
                await cost_guard.check_and_increment("content", redis_client=client)
            ).allowed is False
        # 맥락 밖은 기본 lane(background)이다.
        assert (await cost_guard.check_and_increment("content", redis_client=client)).allowed
        assert _int(sync, cost_guard._lane_daily_key("adhoc", DAY)) == 3
        assert _int(sync, cost_guard._lane_daily_key("background", DAY)) == 1
        assert _int(sync, cost_guard._hospital_daily_key(HOSPITAL_B, DAY)) == 3
        # 다른 카테고리는 lane을 쓰지 않는다.
        assert not list(sync.scan_iter(match=f"cost_guard:image:lane:*{DAY}"))
    finally:
        await client.aclose()


def test_lane_usage_fact_reads_counters(lane_env):
    url, sync = lane_env
    sync.set(cost_guard._daily_key("content", DAY), 60)
    sync.set(cost_guard._lane_daily_key("post_publish", DAY), 12)
    facts = cost_guard.content_lane_usage_sync(redis_url=url, now=NOW)
    assert facts["availability"] == "AVAILABLE"
    assert facts["schedule_reserve"] == 150
    assert facts["category_daily_used"] == 60
    assert facts["non_schedule_paused"] is True  # 60 ≥ 200 − 150
    assert facts["lanes"]["post_publish"]["daily_used"] == 12
    assert set(facts["lanes"]) == set(cost_guard.CONTENT_LANES)


def test_generation_lane_follows_due_date() -> None:
    today = date(2026, 10, 14)
    assert cost_guard.generation_cost_lane(date(2026, 10, 10), today=today) == "schedule"
    assert cost_guard.generation_cost_lane(date(2026, 10, 17), today=today) == "schedule"
    assert cost_guard.generation_cost_lane(date(2026, 10, 18), today=today) == "background"
    assert cost_guard.generation_cost_lane(None, today=today) == "background"


def test_unknown_lane_is_rejected() -> None:
    with pytest.raises(ValueError):
        with cost_guard.cost_lane("nightly"):
            pass


async def test_daily_renewal_scope_caps_one_hospital_inside_the_schedule_lane(lane_env, monkeypatch):
    """비-Critical 일일 갱신(2026-10-15): schedule lane이되 병원별 하루 갱신 상한을 받는다.

    일반 예정 글 생성(갱신 상한 없는 schedule)은 병원 상한을 받지 않고 갱신 카운터도 올리지 않는다.
    """

    url, sync = lane_env
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_CONTENT_RENEWAL_HOSPITAL_DAILY", 4)
    client = redis_async.from_url(url)
    renewal_key = cost_guard._renewal_hospital_daily_key(HOSPITAL_A, DAY)
    try:
        with cost_guard.cost_lane(
            cost_guard.LANE_SCHEDULE, hospital_id=HOSPITAL_A, hospital_daily_cap=4
        ):
            for _ in range(4):
                assert (await cost_guard.check_and_increment("content", redis_client=client)).allowed
            blocked = await cost_guard.check_and_increment("content", redis_client=client)
            assert blocked.allowed is False and "일일 재시도" in blocked.reason
            # 영수증 예약도 같은 상한을 받는다.
            assert (await _reserve(client, None, hospital_id=HOSPITAL_A)).allowed is False
        assert _int(sync, renewal_key) == 4
        # 다른 병원의 갱신은 막히지 않는다.
        with cost_guard.cost_lane(
            cost_guard.LANE_SCHEDULE, hospital_id=HOSPITAL_B, hospital_daily_cap=4
        ):
            assert (await cost_guard.check_and_increment("content", redis_client=client)).allowed
        # 같은 병원의 일반 예정 글 생성은 상한 밖이다(갱신 카운터 불변).
        with cost_guard.cost_lane(cost_guard.LANE_SCHEDULE, hospital_id=HOSPITAL_A):
            assert (await cost_guard.check_and_increment("content", redis_client=client)).allowed
        assert _int(sync, renewal_key) == 4
        assert _int(sync, cost_guard._lane_daily_key("schedule", DAY)) == 6
        # 일정 외 병원 카운터와 섞이지 않는다.
        assert _int(sync, cost_guard._hospital_daily_key(HOSPITAL_A, DAY)) == 0
    finally:
        await client.aclose()


async def test_daily_renewal_settlement_returns_units_to_the_renewal_counter(lane_env):
    url, sync = lane_env
    client = redis_async.from_url(url)
    try:
        with cost_guard.cost_lane(
            cost_guard.LANE_SCHEDULE, hospital_id=HOSPITAL_A, hospital_daily_cap=30
        ):
            decision = await _reserve(client, None, count=6)
        assert decision.allowed and decision.receipt is not None
        settled = await cost_guard.settle_reservation(
            decision.receipt, consumed_units=2, redis_client=client
        )
        assert settled is not None and settled.released_units == 4
        assert _int(sync, cost_guard._renewal_hospital_daily_key(HOSPITAL_A, DAY)) == 2
    finally:
        sync.delete(cost_guard._reservation_key(decision.receipt.id))
        await client.aclose()
