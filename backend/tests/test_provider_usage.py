"""Provider usage normalization and attribution contracts."""

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app.models.usage import HospitalUsageEvent, ProviderUsageEvent
from app.services import provider_usage


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


class FakeRecoveryRedis:
    def __init__(self, key, payload):
        self.index = [key]
        self.scores = {key: 0.0}
        self.store = {key: json.dumps(payload)}

    async def zrangebyscore(self, _key, _minimum, maximum, *, start, num):
        eligible = [key for key in self.index if self.scores[key] <= float(maximum)]
        return eligible[start : start + num]

    async def get(self, key):
        return self.store.get(key)

    async def zrem(self, _index, key):
        if key in self.index:
            self.index.remove(key)
        self.scores.pop(key, None)

    async def zadd(self, _index, mapping):
        for key, score in mapping.items():
            if key not in self.index:
                self.index.append(key)
            self.scores[key] = float(score)

    async def delete(self, key):
        self.store.pop(key, None)


def test_unknown_usage_is_distinct_from_reported_zero():
    unknown = provider_usage.normalize_usage("openai", None)
    zero = provider_usage.normalize_usage(
        "openai", {"input_tokens": 0, "output_tokens": 0}
    )

    assert unknown == {
        "usage_known": False,
        "input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }
    assert zero["usage_known"] is True
    assert zero["input_tokens"] == zero["output_tokens"] == 0


def test_openai_cache_and_reasoning_metadata_are_normalized():
    usage = SimpleNamespace(
        input_tokens=120,
        output_tokens=30,
        input_tokens_details=SimpleNamespace(cached_tokens=80),
        output_tokens_details=SimpleNamespace(reasoning_tokens=12),
    )

    normalized = provider_usage.normalize_usage("openai", usage)

    assert normalized == {
        "usage_known": True,
        "input_tokens": 120,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": 80,
        "output_tokens": 30,
        "reasoning_tokens": 12,
    }


def test_explicit_zero_cache_and_reasoning_are_not_replaced_by_nested_values():
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "reasoning_tokens": 0,
        "input_tokens_details": {"cached_tokens": 9},
        "output_tokens_details": {"reasoning_tokens": 4},
    }

    normalized = provider_usage.normalize_usage("openai", usage)

    assert normalized["cache_read_input_tokens"] == 0
    assert normalized["reasoning_tokens"] == 0


def test_anthropic_and_gemini_cache_fields_keep_their_original_meaning():
    anthropic = provider_usage.normalize_usage(
        "anthropic",
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 150,
            "output_tokens": 20,
        },
    )
    gemini = provider_usage.normalize_usage(
        "gemini",
        {
            "prompt_token_count": 11,
            "candidates_token_count": 22,
            "thoughts_token_count": 7,
            "cached_content_token_count": 8,
        },
    )

    assert anthropic["cache_creation_input_tokens"] == 200
    assert anthropic["cache_read_input_tokens"] == 150
    assert gemini["cache_creation_input_tokens"] is None
    assert gemini["cache_read_input_tokens"] == 8
    assert gemini["reasoning_tokens"] == 7


async def test_http_retry_attempts_are_separate_but_success_usage_is_not_duplicated():
    db = FakeDB()
    logical_call_id = str(uuid.uuid4())

    assert await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="SOV_JUDGMENT",
        cost_category="leadgen",
        lead_id=uuid.uuid4(),
        logical_call_id=logical_call_id,
        http_attempt=1,
        usage_known=False,
        metadata={"http_status": 429},
        db=db,
    )
    assert await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="SOV_JUDGMENT",
        cost_category="leadgen",
        lead_id=uuid.uuid4(),
        logical_call_id=logical_call_id,
        http_attempt=2,
        provider_request_id="req-success",
        usage={"input_tokens": 40, "output_tokens": 5},
        db=db,
    )

    events = [event for event in db.added if isinstance(event, ProviderUsageEvent)]
    assert [event.http_attempt for event in events] == [1, 2]
    assert [event.usage_known for event in events] == [False, True]
    assert events[0].input_tokens is None and events[0].output_tokens is None
    assert events[1].input_tokens == 40 and events[1].output_tokens == 5


async def test_hospital_attempt_dual_writes_legacy_aggregate_without_leadgen_mix():
    hospital_id = uuid.uuid4()
    db = FakeDB()
    assert await provider_usage.record_attempt(
        provider="anthropic",
        model="claude-test",
        workflow="CONTENT_WRITE",
        cost_category="content",
        hospital_id=hospital_id,
        usage={"input_tokens": 10, "output_tokens": 4},
        cache_status="cache_hit",
        db=db,
    )

    provider_event = next(event for event in db.added if isinstance(event, ProviderUsageEvent))
    legacy_event = next(event for event in db.added if isinstance(event, HospitalUsageEvent))
    assert provider_event.hospital_id == legacy_event.hospital_id == hospital_id
    assert provider_event.cache_status == "hit"
    assert legacy_event.kind == "content"
    assert (legacy_event.input_tokens, legacy_event.output_tokens) == (10, 4)

    lead_db = FakeDB()
    assert await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="LEAD_ANSWER",
        cost_category="leadgen",
        lead_id=uuid.uuid4(),
        usage_known=False,
        db=lead_db,
    )
    assert len(lead_db.added) == 1
    assert isinstance(lead_db.added[0], ProviderUsageEvent)


async def test_explicit_known_zero_and_safe_metadata_are_preserved():
    db = FakeDB()
    assert await provider_usage.record_attempt(
        provider="google",
        model="gemini-test",
        workflow="IMAGE_REVIEW",
        cost_category="content",
        usage_known=True,
        input_tokens=0,
        output_tokens=0,
        cache_status=False,
        metadata={"when": uuid.UUID(int=0), "nested": {"values": (1, 2)}},
        db=db,
    )
    event = db.added[0]
    assert event.usage_known is True
    assert event.input_tokens == event.output_tokens == 0
    assert event.cache_status == "miss"
    assert event.metadata_json["when"] == str(uuid.UUID(int=0))
    assert event.metadata_json["nested"]["values"] == [1, 2]


async def test_empty_usage_object_cannot_be_marked_known_without_metered_fields():
    db = FakeDB()
    assert await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="SOV_JUDGMENT",
        cost_category="leadgen",
        usage=SimpleNamespace(),
        usage_known=True,
        db=db,
    )

    event = db.added[0]
    assert event.usage_known is False
    assert event.input_tokens is None
    assert event.output_tokens is None


async def test_anthropic_legacy_aggregate_includes_distinct_cache_buckets_once():
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="anthropic",
        model="claude-test",
        workflow="CONTENT_WRITE",
        cost_category="content",
        hospital_id=uuid.uuid4(),
        usage={
            "input_tokens": 10,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 40,
            "output_tokens": 5,
        },
        db=db,
    )
    event = next(value for value in db.added if isinstance(value, ProviderUsageEvent))
    legacy = next(value for value in db.added if isinstance(value, HospitalUsageEvent))

    assert event.input_tokens == 10
    assert event.cache_creation_input_tokens == 100
    assert event.cache_read_input_tokens == 40
    assert legacy.input_tokens == 150
    assert event.metadata_json["input_tokens_semantics"].endswith(
        "excludes_cache_creation_and_read"
    )


async def test_profile_autofill_keeps_the_legacy_onboarding_bucket():
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="anthropic",
        model="claude-test",
        workflow="PROFILE_AUTOFILL",
        cost_category="content",
        hospital_id=uuid.uuid4(),
        usage={"input_tokens": 4, "output_tokens": 2},
        db=db,
    )

    legacy = next(value for value in db.added if isinstance(value, HospitalUsageEvent))
    assert legacy.kind == "onboarding"
    assert (legacy.input_tokens, legacy.output_tokens) == (4, 2)


async def test_persistence_failure_is_deferred_without_becoming_business_failure(monkeypatch):
    deferred = []

    async def fail_persist(_event):
        return False

    async def capture_defer(event):
        deferred.append(event)
        return True

    monkeypatch.setattr(provider_usage, "_persist", fail_persist)
    monkeypatch.setattr(provider_usage, "_defer", capture_defer)

    recorded = await provider_usage.record_attempt(
        provider="openai",
        model="gpt-test",
        workflow="CONTENT_REVIEW",
        cost_category="content",
        hospital_id=uuid.uuid4(),
        usage_known=False,
    )

    assert recorded is False
    assert len(deferred) == 1
    assert deferred[0].idempotency_key.startswith("provider-attempt:")


async def test_recovery_spool_replays_original_attempt_identity(monkeypatch):
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="gemini",
        model="gemini-test",
        workflow="SOV_ANSWER",
        cost_category="sov",
        hospital_id=uuid.uuid4(),
        logical_call_id="answer-7",
        http_attempt=2,
        usage={"prompt_token_count": 12, "candidates_token_count": 3},
        idempotency_key="stable-attempt",
        db=db,
    )
    event = next(value for value in db.added if isinstance(value, ProviderUsageEvent))
    key = f"provider_usage:recovery:event:{event.id}"
    redis = FakeRecoveryRedis(key, provider_usage._event_payload(event))
    persisted = []

    async def capture_persist(replayed):
        persisted.append(replayed)
        return True

    monkeypatch.setattr(provider_usage, "_persist", capture_persist)
    result = await provider_usage.replay_deferred_attempts(redis_client=redis)

    assert result == {"recovered": 1, "missing": 0, "failed": 0}
    assert len(persisted) == 1
    assert persisted[0].id == event.id
    assert persisted[0].idempotency_key == "stable-attempt"
    assert persisted[0].http_attempt == 2
    assert redis.index == [] and redis.store == {}


async def test_recovery_failure_is_rotated_so_newer_healthy_rows_are_not_starved(
    monkeypatch,
):
    db = FakeDB()
    for idempotency_key in ("poison", "healthy"):
        await provider_usage.record_attempt(
            provider="openai",
            model="gpt-test",
            workflow="SOV_ANSWER",
            cost_category="leadgen",
            usage_known=False,
            idempotency_key=idempotency_key,
            db=db,
        )
    events = [value for value in db.added if isinstance(value, ProviderUsageEvent)]
    poison_key = f"provider_usage:recovery:event:{events[0].id}"
    healthy_key = f"provider_usage:recovery:event:{events[1].id}"
    redis = FakeRecoveryRedis(poison_key, provider_usage._event_payload(events[0]))
    redis.index.append(healthy_key)
    redis.scores[healthy_key] = 0.0
    redis.store[healthy_key] = json.dumps(provider_usage._event_payload(events[1]))

    async def persist_by_identity(event):
        return event.idempotency_key == "healthy"

    monkeypatch.setattr(provider_usage, "_persist", persist_by_identity)

    first = await provider_usage.replay_deferred_attempts(limit=1, redis_client=redis)
    second = await provider_usage.replay_deferred_attempts(limit=1, redis_client=redis)

    assert first == {"recovered": 0, "missing": 0, "failed": 1}
    assert second == {"recovered": 1, "missing": 0, "failed": 0}
    assert poison_key in redis.index
    assert redis.scores[poison_key] > time.time()
    assert healthy_key not in redis.index


class FakeSpoolRedis:
    """_defer가 쓰는 transaction pipeline과 카운터만 흉내 낸다."""

    def __init__(self, existing: int):
        self.index_size = existing
        self.counters: dict[str, int] = {}
        self.values: dict[str, object] = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def incrby(self, key, amount):
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops: list[str] = []

    def set(self, key, value, ex=None):
        self.ops.append("set")
        return self

    def zadd(self, _key, mapping):
        self.ops.append("zadd")
        self.redis.index_size += len(mapping)
        return self

    def zremrangebyrank(self, _key, _start, stop):
        self.ops.append("zrem")
        return self

    def get(self, key):
        self.ops.append(("get", key))
        return self

    def delete(self, key):
        self.ops.append(("delete", key))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op == "zrem":
                keep = provider_usage._RECOVERY_MAX_ITEMS
                removed = max(0, self.redis.index_size - keep)
                self.redis.index_size -= removed
                results.append(removed)
            elif isinstance(op, tuple) and op[0] == "get":
                results.append(self.redis.counters.get(op[1]))
            elif isinstance(op, tuple) and op[0] == "delete":
                results.append(1 if self.redis.counters.pop(op[1], None) is not None else 0)
            else:
                results.append(True)
        return results


async def test_spool_truncation_is_counted_and_logged(monkeypatch, caplog):
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="openai", model="m", workflow="SOV_ANSWER", cost_category="sov",
        hospital_id=uuid.uuid4(), logical_call_id="c", http_attempt=1, usage=None,
        idempotency_key="k-trunc", db=db,
    )
    event = next(value for value in db.added if isinstance(value, ProviderUsageEvent))
    redis = FakeSpoolRedis(existing=provider_usage._RECOVERY_MAX_ITEMS)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_client(_injected=None):
        yield redis

    monkeypatch.setattr(provider_usage, "_recovery_redis_client", fake_client)

    assert await provider_usage._defer(event) is True
    assert redis.counters[provider_usage._RECOVERY_DROPPED_KEY] == 1
    assert any(
        r.levelname == "ERROR" and r.getMessage().startswith("provider_usage_spool_truncated")
        for r in caplog.records
    )


class DroppedCounterRig:
    """실제 Redis에 테스트 전용 키 이름으로 유실 카운터·인수 키를 두고, 시계와 사고 기록을 조종한다."""

    def __init__(self, monkeypatch):
        import redis.asyncio as redis_async

        from tests.db_env import require_redis_url

        self.url = require_redis_url("INTEGRATION_REDIS_URL")
        self.client = redis_async.from_url(self.url)
        suffix = uuid.uuid4().hex
        self.dropped_key = f"test:provider_usage:dropped:{suffix}"
        self.inflight_key = f"test:provider_usage:dropped:inflight:{suffix}"
        monkeypatch.setattr(provider_usage, "_RECOVERY_DROPPED_KEY", self.dropped_key)
        monkeypatch.setattr(provider_usage, "_RECOVERY_DROPPED_INFLIGHT_KEY", self.inflight_key)
        self.now = [1_800_000_000.0]
        monkeypatch.setattr(provider_usage, "_wall_clock", lambda: self.now[0])
        self.opened: list[int | None] = []
        self.fail_next = 0
        monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", self._open)

    async def _open(self, dropped, *, now, unreadable_key=None):
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("db down")
        self.opened.append(dropped)

    def advance(self, seconds: float) -> None:
        self.now[0] += seconds

    async def drain(self) -> int:
        return await provider_usage.report_dropped_usage(redis_client=self.client)

    async def keys(self) -> list[str]:
        found = await self.client.keys(f"{self.dropped_key}*")
        found += await self.client.keys(f"{self.inflight_key}*")
        return sorted({k.decode() for k in found})

    async def inflight_values(self) -> list[int]:
        index = provider_usage._inflight_index_key()
        members = [m.decode() for m in await self.client.zrange(index, 0, -1)]
        return sorted([int(await self.client.get(m)) for m in members])

    async def close(self):
        for key in await self.keys():
            await self.client.delete(key)
        await self.client.aclose()


@pytest.fixture
async def dropped_rig(monkeypatch):
    rig = DroppedCounterRig(monkeypatch)
    try:
        yield rig
    finally:
        await rig.close()


async def test_dropped_usage_opens_one_developer_incident_and_resets_the_counter(dropped_rig):
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 7)

    assert await dropped_rig.drain() == 7
    assert dropped_rig.opened == [7]
    assert await dropped_rig.keys() == []
    # 다음 drain에는 할 일이 없다.
    assert await dropped_rig.drain() == 0
    assert dropped_rig.opened == [7]


async def test_incident_failure_keeps_the_taken_over_count_for_a_later_drain(dropped_rig):
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 3)
    dropped_rig.fail_next = 1

    assert await dropped_rig.drain() == 0
    # 인수한 수는 이 drain의 인수 키에 남는다(카운터로 되돌리지 않는다). 새 유실은 섞이지 않는다.
    assert await dropped_rig.inflight_values() == [3]
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 2)

    # 5분이 지나기 전에는 남은 인수 키를 건드리지 않는다(주인이 아직 커밋 중일 수 있다).
    assert await dropped_rig.drain() == 2
    dropped_rig.advance(301)
    assert await dropped_rig.drain() == 3
    assert dropped_rig.opened == [2, 3]
    assert await dropped_rig.keys() == []


async def test_a_worker_killed_after_takeover_does_not_lose_the_count(dropped_rig, monkeypatch):
    """인수 뒤 사고 커밋 전에 강제 종료돼도(예외가 아닌 종료) 5분 뒤 다음 drain이 인수 키를 처리한다."""
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 5)

    class Killed(BaseException):
        pass

    async def killed(_dropped, *, now, unreadable_key=None):
        raise Killed

    original = provider_usage._open_spool_truncation_incident
    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", killed)
    with pytest.raises(Killed):
        await dropped_rig.drain()
    assert await dropped_rig.inflight_values() == [5]

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", original)
    dropped_rig.advance(301)
    assert await dropped_rig.drain() == 5
    assert dropped_rig.opened == [5] and await dropped_rig.keys() == []


async def test_a_worker_killed_after_commit_only_touches_the_same_daily_incident_again(
    dropped_rig, monkeypatch
):
    """사고 커밋 뒤 인수 키 삭제 전에 죽으면 다음 drain이 같은 수로 같은 날 사고를 한 번 더 touch한다(새 사고 아님)."""
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 4)

    class Killed(BaseException):
        pass

    async def killed_forget(_client, _key):
        raise Killed

    original = provider_usage._forget_inflight
    monkeypatch.setattr(provider_usage, "_forget_inflight", killed_forget)
    with pytest.raises(Killed):
        await dropped_rig.drain()
    monkeypatch.setattr(provider_usage, "_forget_inflight", original)

    dropped_rig.advance(301)
    assert await dropped_rig.drain() == 4
    assert dropped_rig.opened == [4, 4]
    assert await dropped_rig.keys() == []


async def test_a_late_drain_cannot_delete_another_drains_takeover(dropped_rig, monkeypatch):
    """검토가 재현한 순서: A·B가 같은 3건을 다루고 → A 커밋·삭제 → C가 새 7건 인수 → 늦은 B가 끝나고
    → C의 DB 커밋 실패. 예전에는 늦은 B가 공유 인수 키(=C의 7건)를 지웠다. 이제 각자 자기 키만 지운다.
    """
    import asyncio

    gates = {name: asyncio.Event() for name in ("A", "B", "C")}
    entered = {name: asyncio.Event() for name in ("A", "B", "C")}
    order = iter(["A", "B", "C", "D"])
    fail = {"C"}

    async def gated_open(dropped, *, now, unreadable_key=None):
        name = next(order)
        if name in gates:
            entered[name].set()
            await gates[name].wait()
        if name in fail:
            raise RuntimeError("db down")
        dropped_rig.opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", gated_open)
    await dropped_rig.client.incrby(dropped_rig.dropped_key, 3)

    task_a = asyncio.create_task(dropped_rig.drain())
    await entered["A"].wait()  # A가 3건을 인수하고 커밋 중이다(오래 걸린다).
    dropped_rig.advance(301)
    task_b = asyncio.create_task(dropped_rig.drain())
    await entered["B"].wait()  # A가 5분 넘게 멈춰 있어 B가 같은 3건을 다시 인수했다.
    gates["A"].set()
    assert await task_a == 3  # A 커밋 → A는 자기(이미 옮겨진) 키만 지운다.

    await dropped_rig.client.incrby(dropped_rig.dropped_key, 7)
    task_c = asyncio.create_task(dropped_rig.drain())
    await entered["C"].wait()  # C가 새 7건을 인수했다.
    gates["B"].set()
    assert await task_b == 3  # 늦은 B가 끝난다 — C의 인수 키는 그대로다.
    assert await dropped_rig.inflight_values() == [7]
    gates["C"].set()
    assert await task_c == 0  # C의 DB 커밋 실패 — 7건은 인수 키에 남는다.
    assert await dropped_rig.inflight_values() == [7]

    dropped_rig.advance(301)
    assert await dropped_rig.drain() == 7  # D가 남은 7건을 다시 인수해 처리한다.
    assert dropped_rig.opened == [3, 3, 7]  # 같은 3건은 같은 날 사고를 두 번 touch할 뿐, 7건은 잃지 않는다.
    assert await dropped_rig.keys() == []


async def test_concurrent_drains_report_every_dropped_record_exactly_once(dropped_rig):
    import asyncio

    await dropped_rig.client.incrby(dropped_rig.dropped_key, 10)
    results = await asyncio.gather(*(dropped_rig.drain() for _ in range(5)))

    assert sum(results) == 10
    assert dropped_rig.opened == [10]
    assert await dropped_rig.keys() == []


@pytest.mark.parametrize("shape", ["legacy_json", "token_text", "token_hash"])
async def test_an_unreadable_inflight_key_is_quarantined_and_never_blocks_new_counts(
    dropped_rig, caplog, shape
):
    rig = dropped_rig
    if shape == "legacy_json":
        # 옛 형식(토큰 없는 공유 인수 키)에 다른 스키마의 값이 남았다.
        leftover = rig.inflight_key
        await rig.client.set(leftover, json.dumps({"count": 3, "taken_at": 1}))
    else:
        leftover = f"{rig.inflight_key}:1:{uuid.uuid4().hex}"
        if shape == "token_text":
            await rig.client.set(leftover, "not-a-number")
        else:
            await rig.client.hset(leftover, "count", "3")
        await rig.client.zadd(provider_usage._inflight_index_key(), {leftover: 1})
    await rig.client.incrby(rig.dropped_key, 2)

    assert await rig.drain() == 2
    # 읽을 수 없는 키는 수를 모르는 사고(None)로 알리고, 새 유실 2건은 같은 drain에서 처리한다.
    assert sorted(rig.opened, key=lambda v: -1 if v is None else v) == [None, 2]
    assert any("unreadable" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    remaining = await rig.keys()
    assert len(remaining) == 1 and ":quarantine:" in remaining[0]
    assert await rig.client.ttl(remaining[0]) > 0
    assert await rig.client.zcard(provider_usage._inflight_index_key()) == 0
    # 다음 drain은 격리된 키를 다시 보지 않는다.
    assert await rig.drain() == 0
    assert len(rig.opened) == 2


async def test_counter_failure_does_not_turn_a_stored_spool_into_a_failure(monkeypatch, caplog):
    # spool 저장은 이미 성공했다 — 유실 카운터만 실패해도 기록은 spool에 있다(정상 경로로 되돌리지 않는다).
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="openai", model="m", workflow="SOV_ANSWER", cost_category="sov",
        hospital_id=uuid.uuid4(), logical_call_id="c", http_attempt=1, usage=None,
        idempotency_key="k-trunc-counter", db=db,
    )
    event = next(value for value in db.added if isinstance(value, ProviderUsageEvent))
    redis = FakeSpoolRedis(existing=provider_usage._RECOVERY_MAX_ITEMS)

    async def broken_incrby(_key, _amount):
        raise provider_usage.RedisError("counter down")

    redis.incrby = broken_incrby

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_client(_injected=None):
        yield redis

    monkeypatch.setattr(provider_usage, "_recovery_redis_client", fake_client)

    assert await provider_usage._defer(event) is True
    messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(m.startswith("provider_usage_spool_truncated_count_lost") for m in messages)
    assert not any("recovery spool unavailable" in r.getMessage() for r in caplog.records)
