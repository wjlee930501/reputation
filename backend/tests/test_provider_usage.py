"""Provider usage normalization and attribution contracts."""

import json
import time
import uuid
from types import SimpleNamespace

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
