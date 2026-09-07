"""Normalized, best-effort usage ledger for individual provider HTTP attempts."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import date, datetime

import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_sessionmaker
from app.models.usage import (
    HospitalUsageEvent,
    HospitalUsageKind,
    ProviderCacheStatus,
    ProviderUsageEvent,
)
from app.services.cost_guard import CATEGORIES

logger = logging.getLogger(__name__)

_RECOVERY_INDEX_KEY = "provider_usage:recovery:index"
_RECOVERY_TTL_SECONDS = 7 * 24 * 60 * 60
_RECOVERY_MAX_ITEMS = 10_000
_RECOVERY_RETRY_DELAY_SECONDS = 60
_recovery_redis: redis_async.Redis | None = None

_HOSPITAL_KIND_BY_CATEGORY = {
    "content": HospitalUsageKind.CONTENT.value,
    "image": HospitalUsageKind.IMAGE.value,
    "sov": HospitalUsageKind.SOV.value,
}
_CACHE_ALIASES = {
    "hit": ProviderCacheStatus.HIT.value,
    "cached": ProviderCacheStatus.HIT.value,
    "cache_hit": ProviderCacheStatus.HIT.value,
    "read": ProviderCacheStatus.HIT.value,
    "miss": ProviderCacheStatus.MISS.value,
    "cache_miss": ProviderCacheStatus.MISS.value,
    "write": ProviderCacheStatus.WRITE.value,
    "created": ProviderCacheStatus.WRITE.value,
    "creation": ProviderCacheStatus.WRITE.value,
    "bypass": ProviderCacheStatus.BYPASS.value,
    "disabled": ProviderCacheStatus.BYPASS.value,
    "unknown": ProviderCacheStatus.UNKNOWN.value,
}


def _read(value: object | None, *names: str) -> object | None:
    if value is None:
        return None
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _unit(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, normalized)


def _first_present(*values: object | None) -> object | None:
    return next((value for value in values if value is not None), None)


def _uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _json_safe(value: object, *, depth: int = 0) -> object:
    """Bound caller metadata to plain JSON values without serializing SDK response bodies."""
    if depth >= 5:
        return str(value)[:500]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, str) else value[:2000]
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _json_safe(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:100]]
    return str(value)[:500]


def _redis() -> redis_async.Redis:
    global _recovery_redis
    if _recovery_redis is None:
        from app.core.config import settings

        _recovery_redis = redis_async.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
    return _recovery_redis


def _event_payload(event: ProviderUsageEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "provider": event.provider,
        "model": event.model,
        "workflow": event.workflow,
        "cost_category": event.cost_category,
        "hospital_id": str(event.hospital_id) if event.hospital_id else None,
        "lead_id": str(event.lead_id) if event.lead_id else None,
        "run_id": event.run_id,
        "item_id": event.item_id,
        "attempt_id": event.attempt_id,
        "logical_call_id": event.logical_call_id,
        "http_attempt": event.http_attempt,
        "provider_request_id": event.provider_request_id,
        "idempotency_key": event.idempotency_key,
        "cache_status": event.cache_status,
        "usage_known": event.usage_known,
        "input_tokens": event.input_tokens,
        "cache_creation_input_tokens": event.cache_creation_input_tokens,
        "cache_read_input_tokens": event.cache_read_input_tokens,
        "output_tokens": event.output_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "search_units": event.search_units,
        "image_units": event.image_units,
        "metadata": event.metadata_json,
        "created_at": event.created_at.isoformat(),
    }


def _event_from_payload(payload: Mapping[str, object]) -> ProviderUsageEvent:
    return ProviderUsageEvent(
        id=uuid.UUID(str(payload["id"])),
        provider=str(payload["provider"]),
        model=str(payload["model"]) if payload.get("model") else None,
        workflow=str(payload["workflow"]),
        cost_category=str(payload["cost_category"]),
        hospital_id=_uuid(payload.get("hospital_id")),
        lead_id=_uuid(payload.get("lead_id")),
        run_id=str(payload["run_id"]) if payload.get("run_id") else None,
        item_id=str(payload["item_id"]) if payload.get("item_id") else None,
        attempt_id=str(payload["attempt_id"]) if payload.get("attempt_id") else None,
        logical_call_id=(
            str(payload["logical_call_id"]) if payload.get("logical_call_id") else None
        ),
        http_attempt=int(payload["http_attempt"]),
        provider_request_id=(
            str(payload["provider_request_id"])
            if payload.get("provider_request_id") else None
        ),
        idempotency_key=str(payload["idempotency_key"]),
        cache_status=str(payload["cache_status"]),
        usage_known=bool(payload["usage_known"]),
        input_tokens=_unit(payload.get("input_tokens")),
        cache_creation_input_tokens=_unit(payload.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_unit(payload.get("cache_read_input_tokens")),
        output_tokens=_unit(payload.get("output_tokens")),
        reasoning_tokens=_unit(payload.get("reasoning_tokens")),
        search_units=_unit(payload.get("search_units")),
        image_units=_unit(payload.get("image_units")),
        metadata_json=dict(payload.get("metadata") or {}),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
    )


def _legacy_event(event: ProviderUsageEvent) -> HospitalUsageEvent | None:
    legacy_kind = (
        HospitalUsageKind.ONBOARDING.value
        if event.workflow == "PROFILE_AUTOFILL"
        else _HOSPITAL_KIND_BY_CATEGORY.get(event.cost_category)
    )
    if event.hospital_id is None or legacy_kind is None:
        return None
    # The old aggregate has non-null counters only. Unknown units therefore remain legacy
    # zero for response compatibility, while ProviderUsageEvent preserves unknown as NULL.
    legacy_input_tokens = int(event.input_tokens or 0)
    if event.provider == "anthropic":
        # Anthropic reports non-cache input and cache create/read buckets separately. The
        # existing hospital aggregate historically displayed their sum; preserve that API.
        legacy_input_tokens += int(event.cache_creation_input_tokens or 0)
        legacy_input_tokens += int(event.cache_read_input_tokens or 0)
    return HospitalUsageEvent(
        hospital_id=event.hospital_id,
        kind=legacy_kind,
        input_tokens=legacy_input_tokens,
        output_tokens=int(event.output_tokens or 0),
        created_at=event.created_at,
    )


async def _persist(event: ProviderUsageEvent) -> bool:
    legacy_event = _legacy_event(event)
    try:
        sessionmaker = get_async_sessionmaker()
        async with sessionmaker() as session:
            session.add(event)
            if legacy_event is not None:
                session.add(legacy_event)
            await session.commit()
        return True
    except IntegrityError:
        if not event.idempotency_key:
            return False
        try:
            sessionmaker = get_async_sessionmaker()
            async with sessionmaker() as session:
                duplicate = await session.scalar(
                    select(ProviderUsageEvent.id).where(
                        ProviderUsageEvent.idempotency_key == event.idempotency_key
                    )
                )
            return duplicate is not None
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


async def _defer(event: ProviderUsageEvent) -> bool:
    client = _redis()
    key = f"provider_usage:recovery:event:{event.id}"
    try:
        pipe = client.pipeline(transaction=True)
        pipe.set(key, json.dumps(_event_payload(event)), ex=_RECOVERY_TTL_SECONDS)
        pipe.zadd(_RECOVERY_INDEX_KEY, {key: event.created_at.timestamp()})
        pipe.zremrangebyrank(_RECOVERY_INDEX_KEY, 0, -_RECOVERY_MAX_ITEMS - 1)
        await pipe.execute()
        return True
    except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError):
        logger.warning(
            "provider usage recovery spool unavailable: idempotency_key=%s",
            event.idempotency_key,
        )
        return False


async def replay_deferred_attempts(
    *, limit: int = 100, redis_client: redis_async.Redis | None = None
) -> dict[str, int]:
    """Replay the bounded Redis observation spool; safe for periodic reconciliation."""
    if limit < 1 or limit > 1_000:
        raise ValueError("limit must be between 1 and 1000")
    client = redis_client or _redis()
    recovered = missing = failed = 0
    try:
        # Scores are next-attempt timestamps. Failed rows move into the future so one
        # permanently invalid FK/payload cannot occupy the first batch and starve newer,
        # healthy observations. Payload TTL still bounds the total retry lifetime.
        keys = await client.zrangebyscore(
            _RECOVERY_INDEX_KEY,
            "-inf",
            time.time(),
            start=0,
            num=limit,
        )
        for raw_key in keys:
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            raw_payload = await client.get(key)
            if raw_payload is None:
                await client.zrem(_RECOVERY_INDEX_KEY, key)
                missing += 1
                continue
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode()
            try:
                payload = json.loads(raw_payload)
                if not isinstance(payload, Mapping):
                    raise TypeError("provider usage recovery payload must be an object")
                event = _event_from_payload(payload)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                await client.delete(key)
                await client.zrem(_RECOVERY_INDEX_KEY, key)
                missing += 1
                continue
            if await _persist(event):
                await client.delete(key)
                await client.zrem(_RECOVERY_INDEX_KEY, key)
                recovered += 1
            else:
                await client.zadd(
                    _RECOVERY_INDEX_KEY,
                    {key: time.time() + _RECOVERY_RETRY_DELAY_SECONDS},
                )
                failed += 1
        return {"recovered": recovered, "missing": missing, "failed": failed}
    except (OSError, RedisError, RuntimeError, TimeoutError):
        logger.warning("provider usage recovery replay skipped: redis unavailable")
        return {"recovered": recovered, "missing": missing, "failed": failed + 1}


def normalize_cache_status(
    value: str | bool | None,
    *,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> str:
    if isinstance(value, bool):
        return ProviderCacheStatus.HIT.value if value else ProviderCacheStatus.MISS.value
    if value is not None:
        return _CACHE_ALIASES.get(
            str(value).strip().lower(), ProviderCacheStatus.UNKNOWN.value
        )
    if (cache_read_input_tokens or 0) > 0:
        return ProviderCacheStatus.HIT.value
    if (cache_creation_input_tokens or 0) > 0:
        return ProviderCacheStatus.WRITE.value
    return ProviderCacheStatus.UNKNOWN.value


def normalize_usage(provider: str, usage: object | None) -> dict[str, int | bool | None]:
    """Normalize OpenAI, Anthropic, and Gemini usage fields without turning missing into zero."""
    provider_name = provider.strip().lower()
    input_details = _read(usage, "input_tokens_details", "prompt_tokens_details")
    output_details = _read(usage, "output_tokens_details", "completion_tokens_details")

    if provider_name in {"google", "gemini", "vertex", "vertex_ai"}:
        input_tokens = _unit(_read(usage, "prompt_token_count", "prompt_tokens"))
        output_tokens = _unit(_read(usage, "candidates_token_count", "candidates_tokens"))
        reasoning_tokens = _unit(_read(usage, "thoughts_token_count", "thought_tokens"))
        cache_read = _unit(_read(usage, "cached_content_token_count", "cached_tokens"))
        cache_creation = _unit(_read(usage, "cache_creation_input_tokens"))
    else:
        input_tokens = _unit(_read(usage, "input_tokens", "prompt_tokens"))
        output_tokens = _unit(_read(usage, "output_tokens", "completion_tokens"))
        reasoning_tokens = _unit(
            _first_present(
                _read(usage, "reasoning_tokens"),
                _read(output_details, "reasoning_tokens"),
            )
        )
        cache_creation = _unit(
            _read(usage, "cache_creation_input_tokens", "cache_creation_tokens")
        )
        cache_read = _unit(
            _first_present(
                _read(usage, "cache_read_input_tokens", "cache_read_tokens"),
                _read(input_details, "cached_tokens"),
            )
        )

    values = (input_tokens, cache_creation, cache_read, output_tokens, reasoning_tokens)
    return {
        "usage_known": any(value is not None for value in values),
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


async def record_attempt(
    *,
    provider: str,
    model: str | None,
    workflow: str,
    cost_category: str,
    hospital_id: uuid.UUID | str | None = None,
    lead_id: uuid.UUID | str | None = None,
    run_id: object | None = None,
    item_id: object | None = None,
    attempt_id: object | None = None,
    logical_call_id: object | None = None,
    http_attempt: int = 1,
    provider_request_id: str | None = None,
    usage: object | None = None,
    usage_known: bool | None = None,
    input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    search_units: int | None = None,
    image_units: int | None = None,
    cache_status: str | bool | None = None,
    metadata: Mapping[str, object] | None = None,
    db: AsyncSession | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """Append one actual HTTP attempt and optionally its legacy hospital aggregate row.

    Persistence is observational: failures are logged and return false, but never fail the
    provider workflow. Passing a stable idempotency key lets a missed observation be retried.
    """
    if not provider.strip() or not workflow.strip() or cost_category not in CATEGORIES:
        logger.warning(
            "provider usage skipped: invalid identity provider=%r workflow=%r category=%r",
            provider,
            workflow,
            cost_category,
        )
        return False
    if http_attempt < 1:
        logger.warning("provider usage skipped: invalid http_attempt=%s", http_attempt)
        return False
    try:
        normalized_hospital_id = _uuid(hospital_id)
        normalized_lead_id = _uuid(lead_id)
    except (TypeError, ValueError):
        logger.warning("provider usage skipped: invalid hospital_id or lead_id")
        return False
    if normalized_hospital_id is not None and normalized_lead_id is not None:
        logger.warning("provider usage skipped: hospital_id and lead_id are mutually exclusive")
        return False

    normalized = normalize_usage(provider, usage)
    explicit = {
        "input_tokens": _unit(input_tokens),
        "cache_creation_input_tokens": _unit(cache_creation_input_tokens),
        "cache_read_input_tokens": _unit(cache_read_input_tokens),
        "output_tokens": _unit(output_tokens),
        "reasoning_tokens": _unit(reasoning_tokens),
    }
    for key, value in explicit.items():
        if value is not None:
            normalized[key] = value
    normalized_search = _unit(search_units)
    normalized_images = _unit(image_units)
    inferred_known = (
        bool(normalized["usage_known"])
        or any(value is not None for value in explicit.values())
        or normalized_search is not None
        or normalized_images is not None
    )
    # ``usage_known`` is an assertion about the fields, not a substitute for them. An
    # empty SDK usage object (or an accidental explicit True) must remain distinguishable
    # from a provider-reported zero, which is represented by an actual numeric zero.
    normalized_known = (
        inferred_known if usage_known is None else bool(usage_known) and inferred_known
    )
    normalized_cache = normalize_cache_status(
        cache_status,
        cache_creation_input_tokens=normalized["cache_creation_input_tokens"],
        cache_read_input_tokens=normalized["cache_read_input_tokens"],
    )
    metadata_json = dict(_json_safe(metadata or {}))
    metadata_json.setdefault("normalization_schema", 1)
    normalized_provider = provider.strip().lower()
    metadata_json.setdefault("usage_provider", normalized_provider)
    metadata_json.setdefault(
        "input_tokens_semantics",
        (
            "provider_reported_excludes_cache_creation_and_read"
            if normalized_provider == "anthropic"
            else "provider_reported_total_including_cached_input"
        ),
    )
    metadata_json.setdefault(
        "reasoning_tokens_semantics",
        (
            "separate_from_candidates_output_tokens"
            if normalized_provider in {"google", "gemini", "vertex", "vertex_ai"}
            else "subset_of_output_tokens"
        ),
    )
    provider_total = _unit(_read(usage, "total_tokens", "total_token_count"))
    tool_input = _unit(_read(usage, "tool_use_prompt_token_count"))
    if provider_total is not None:
        metadata_json.setdefault("provider_reported_total_tokens", provider_total)
    if tool_input is not None:
        metadata_json.setdefault("provider_tool_input_tokens", tool_input)

    event_id = uuid.uuid4()
    event = ProviderUsageEvent(
        id=event_id,
        provider=normalized_provider,
        model=model.strip() if model else None,
        workflow=workflow.strip().upper(),
        cost_category=cost_category,
        hospital_id=normalized_hospital_id,
        lead_id=normalized_lead_id,
        run_id=str(run_id) if run_id is not None else None,
        item_id=str(item_id) if item_id is not None else None,
        attempt_id=str(attempt_id) if attempt_id is not None else None,
        logical_call_id=str(logical_call_id) if logical_call_id is not None else None,
        http_attempt=http_attempt,
        provider_request_id=provider_request_id,
        idempotency_key=idempotency_key or f"provider-attempt:{event_id}",
        cache_status=normalized_cache,
        usage_known=normalized_known,
        input_tokens=normalized["input_tokens"],
        cache_creation_input_tokens=normalized["cache_creation_input_tokens"],
        cache_read_input_tokens=normalized["cache_read_input_tokens"],
        output_tokens=normalized["output_tokens"],
        reasoning_tokens=normalized["reasoning_tokens"],
        search_units=normalized_search,
        image_units=normalized_images,
        metadata_json=metadata_json,
        created_at=datetime.now().astimezone(),
    )
    # A lightweight collector is useful in unit tests. Production callers should omit db:
    # `_persist` always owns a separate transaction, so telemetry cannot poison domain state.
    if db is not None and not isinstance(db, AsyncSession):
        db.add(event)
        legacy_event = _legacy_event(event)
        if legacy_event is not None:
            db.add(legacy_event)
        return True
    if await _persist(event):
        return True
    await _defer(event)
    logger.warning(
        "provider usage persistence deferred: provider=%s workflow=%s idempotency_key=%s",
        provider,
        workflow,
        event.idempotency_key,
    )
    return False
