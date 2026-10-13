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

    # 유실 카운터 인수(report_dropped_usage)가 쓰는 명령 — Redis 의미 그대로.
    async def get(self, key):
        value = self.counters.get(key)
        return None if value is None else str(value).encode()

    async def delete(self, key):
        return 1 if self.counters.pop(key, None) is not None else 0

    async def renamenx(self, source, destination):
        if source not in self.counters:
            raise provider_usage.ResponseError("no such key")
        if destination in self.counters:
            return False
        self.counters[destination] = self.counters.pop(source)
        return True


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


async def test_dropped_usage_opens_one_developer_incident_and_resets_the_counter(monkeypatch):
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 7
    opened: list[int] = []

    async def fake_open(dropped, *, now):
        opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", fake_open)

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 7
    assert opened == [7]
    assert provider_usage._RECOVERY_DROPPED_KEY not in redis.counters
    # 다음 drain에는 할 일이 없다.
    assert await provider_usage.report_dropped_usage(redis_client=redis) == 0
    assert opened == [7]


async def test_incident_failure_keeps_the_taken_over_count_for_the_next_drain(monkeypatch):
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 3
    opened: list[int] = []
    outcomes = [RuntimeError("db down"), None, None]

    async def flaky(dropped, *, now):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", flaky)

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 0
    # 인수한 수는 인수 키에 남는다(카운터로 되돌리지 않는다). 그 사이 새 유실이 쌓여도 섞이지 않는다.
    assert redis.counters == {provider_usage._RECOVERY_DROPPED_INFLIGHT_KEY: 3}
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 2

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 3
    assert await provider_usage.report_dropped_usage(redis_client=redis) == 2
    assert opened == [3, 2]
    assert redis.counters == {}


async def test_a_worker_killed_after_takeover_does_not_lose_the_count(monkeypatch):
    """인수 뒤 사고 커밋 전에 강제 종료돼도(예외가 아닌 종료) 다음 drain이 인수 키를 처리한다."""
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 5

    class Killed(BaseException):
        pass

    async def killed(_dropped, *, now):
        raise Killed

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", killed)
    with pytest.raises(Killed):
        await provider_usage.report_dropped_usage(redis_client=redis)
    assert redis.counters == {provider_usage._RECOVERY_DROPPED_INFLIGHT_KEY: 5}

    opened: list[int] = []

    async def fine(dropped, *, now):
        opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", fine)
    assert await provider_usage.report_dropped_usage(redis_client=redis) == 5
    assert opened == [5] and redis.counters == {}


async def test_a_worker_killed_after_commit_only_touches_the_same_daily_incident_again(monkeypatch):
    """사고 커밋 뒤 인수 키 삭제 전에 죽으면 다음 drain이 같은 수로 같은 날 사고를 한 번 더 touch한다(새 사고 아님)."""
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 4
    opened: list[int] = []

    async def committed(dropped, *, now):
        opened.append(dropped)

    class Killed(BaseException):
        pass

    async def killed_delete(_key):
        raise Killed

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", committed)
    original_delete = redis.delete
    redis.delete = killed_delete
    with pytest.raises(Killed):
        await provider_usage.report_dropped_usage(redis_client=redis)
    redis.delete = original_delete

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 4
    assert opened == [4, 4]
    assert redis.counters == {}


async def test_dropped_counter_takeover_uses_real_redis_semantics(monkeypatch):
    """RENAMENX가 남은 인수 키를 덮어쓰지 않는다 — 실제 Redis로 확인한다."""
    import redis.asyncio as redis_async

    from tests.db_env import require_redis_url

    client = redis_async.from_url(require_redis_url("INTEGRATION_REDIS_URL"))
    suffix = uuid.uuid4().hex
    dropped_key = f"test:provider_usage:dropped:{suffix}"
    inflight_key = f"test:provider_usage:dropped:inflight:{suffix}"
    monkeypatch.setattr(provider_usage, "_RECOVERY_DROPPED_KEY", dropped_key)
    monkeypatch.setattr(provider_usage, "_RECOVERY_DROPPED_INFLIGHT_KEY", inflight_key)
    opened: list[int] = []
    fail = [True]

    async def open_incident(dropped, *, now):
        if fail[0]:
            raise RuntimeError("db down")
        opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", open_incident)
    try:
        await client.incrby(dropped_key, 6)
        assert await provider_usage.report_dropped_usage(redis_client=client) == 0
        assert int(await client.get(inflight_key)) == 6
        await client.incrby(dropped_key, 1)
        fail[0] = False
        assert await provider_usage.report_dropped_usage(redis_client=client) == 6
        assert await provider_usage.report_dropped_usage(redis_client=client) == 1
        assert await provider_usage.report_dropped_usage(redis_client=client) == 0
        assert opened == [6, 1]
        assert await client.exists(dropped_key, inflight_key) == 0
    finally:
        await client.delete(dropped_key, inflight_key)
        await client.aclose()


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
