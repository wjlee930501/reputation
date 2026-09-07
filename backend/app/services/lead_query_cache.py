"""질의 단위 공유 캐시 (설계 §2-6).

**질의에 병원 이름이 들어가지 않으므로**(PRD F1-1) `수서역 근처 내과 병원 추천해줘`의
AI 답변은 신청 병원이 누구든 동일하다. 병원마다 달라지는 것은 그 답변에서 이 병원
이름이 나왔는지 보는 판정 단계뿐이고, 판정은 콜당 0.26원이다(답변 모델의 1/370).

  첫 번째 병원 (수서역·내과)  1,499원
  같은 질의를 쓰는 두 번째    약 5원   ← 판정 18콜만 다시

캐시 키에 **모델과 프롬프트 버전이 들어간다.** 이것이 빠지면 모델을 바꿔도(luna 이전
같은) 옛 답변이 살아남아, PRD §2-1의 "핀 고정 + 의도적 이전"이 캐시 뒤에서 조용히
깨진다 — 언급률 변화가 플랫폼 탓인지 우리 도구 탓인지 영원히 분리할 수 없게 된다.

**개인정보가 아니다.** 질의에 신청자 이름·연락처를 절대 넣지 않으므로(PRD §6)
파기 파이프라인의 대상이 아니고 TTL로만 관리한다.
"""
import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.lead_diagnosis import LeadQueryAnswer
from app.services import sov_engine

logger = logging.getLogger(__name__)

_FLIGHT_LEASE_SECONDS = 90
_FLIGHT_RESULT_SECONDS = 600
_FLIGHT_FAILURE_SECONDS = 20
_FLIGHT_POLL_SECONDS = 0.1

_ACQUIRE_FLIGHT_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then
  return 2
end
if redis.call('SETNX', KEYS[1], ARGV[1]) == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

_RENEW_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class SingleFlightAnswer:
    answer: dict
    owner: bool


def _flight_keys(cache_key: str, repeat_no: int) -> tuple[str, str]:
    identity = f"{cache_key}:{repeat_no}"
    return f"lead-query-flight:lease:{identity}", f"lead-query-flight:result:{identity}"


async def _renew_flight_lease(client, lease_key: str, token: str) -> None:
    """Keep a live owner from expiring while provider latency is high."""
    try:
        while True:
            await asyncio.sleep(_FLIGHT_LEASE_SECONDS / 3)
            renewed = await client.eval(
                _RENEW_LEASE_SCRIPT, 1, lease_key, token, _FLIGHT_LEASE_SECONDS
            )
            if not renewed:
                return
    except (OSError, RedisError, RuntimeError, TimeoutError):
        # The owner still returns its purchased answer. Redis fail-open can reduce sharing,
        # but must never turn one successful provider call into a workflow failure.
        return


async def singleflight_fetch_answer(
    *,
    query_text: str,
    platform: str,
    requested_model: str,
    repeat_no: int,
    fetch: Callable[[], Awaitable[dict]],
    redis_client: redis_async.Redis | None = None,
) -> SingleFlightAnswer:
    """Run one provider fetch per shared cache identity across worker processes.

    A short renewable Redis lease elects the owner. The completed result is handed directly
    to waiters so they do not race the database commit. If an owner process dies, its lease
    expires and exactly one waiter takes over. Redis failure keeps the documented fail-open
    behavior and performs the fetch locally.
    """
    from app.services import cost_guard

    key = query_cache_key(
        query_text=query_text,
        platform=platform,
        requested_model=requested_model,
    )
    lease_key, result_key = _flight_keys(key, repeat_no)
    token = uuid.uuid4().hex
    client = redis_client or cost_guard._client()  # shared configured async Redis client

    waiting_on_owner = False
    while True:
        if waiting_on_owner:
            try:
                cached_payload = await client.get(result_key)
                if cached_payload is not None:
                    if isinstance(cached_payload, bytes):
                        cached_payload = cached_payload.decode()
                    return SingleFlightAnswer(json.loads(cached_payload), owner=False)
                if not await client.exists(lease_key):
                    # The owner crashed before publishing. Return to election; one waiter wins.
                    waiting_on_owner = False
                    continue
            except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                logger.warning("lead query single-flight fail-open: %s", type(exc).__name__)
                return SingleFlightAnswer(await fetch(), owner=True)

        try:
            acquired = await client.eval(
                _ACQUIRE_FLIGHT_SCRIPT,
                2,
                lease_key,
                result_key,
                token,
                _FLIGHT_LEASE_SECONDS,
            )
        except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
            logger.warning("lead query single-flight fail-open: %s", type(exc).__name__)
            return SingleFlightAnswer(await fetch(), owner=True)

        if int(acquired) == 2:
            # The prior owner finished its paid call but the caller may have crashed before
            # committing the DB cache/checkpoint. Treat this short-lived handoff as a valid
            # answer for the exact same query/model/protocol/repeat identity.
            try:
                cached_payload = await client.get(result_key)
                if cached_payload is None:
                    continue
                if isinstance(cached_payload, bytes):
                    cached_payload = cached_payload.decode()
                return SingleFlightAnswer(json.loads(cached_payload), owner=False)
            except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                logger.warning("lead query single-flight handoff skipped: %s", type(exc).__name__)
                return SingleFlightAnswer(await fetch(), owner=True)

        if int(acquired) == 1:
            renewer = asyncio.create_task(_renew_flight_lease(client, lease_key, token))
            try:
                # Provider exceptions propagate. Treating them as Redis failures would execute
                # `fetch` twice and purchase the same answer twice in one logical attempt.
                answer = await fetch()
                ttl = (
                    _FLIGHT_RESULT_SECONDS
                    if answer.get("measurement_status") == "SUCCESS"
                    else _FLIGHT_FAILURE_SECONDS
                )
                try:
                    await client.set(
                        result_key,
                        json.dumps(answer, ensure_ascii=False, separators=(",", ":")),
                        ex=ttl,
                    )
                except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
                    logger.warning(
                        "lead query single-flight result publish skipped: %s",
                        type(exc).__name__,
                    )
                return SingleFlightAnswer(answer, owner=True)
            finally:
                renewer.cancel()
                with suppress(asyncio.CancelledError):
                    await renewer
                try:
                    await client.eval(_RELEASE_LEASE_SCRIPT, 1, lease_key, token)
                except (OSError, RedisError, RuntimeError, TimeoutError):
                    pass

        # A live owner is working. Poll the result; when an owner crashes the lease
        # disappears and this loop elects one successor without human intervention.
        waiting_on_owner = True
        await asyncio.sleep(_FLIGHT_POLL_SECONDS)


def prompt_version(*, platform: str | None = None) -> str:
    """측정 **프로토콜 전체**의 지문.

    수동으로 관리하는 버전 문자열이면 조건을 고치고 버전 올리는 것을 잊는다 —
    그 순간 캐시가 옛 조건의 답변을 새 조건의 결과라고 내놓는다. 조건 자체에서
    파생시켜 잊을 수 없게 만든다.

    지시문만 해싱하면 부족하다. 검색 강제 여부(`tool_choice`)가 바뀌면 같은 지시문으로도
    전혀 다른 답변이 나오는데, v1 캐시가 7일간 살아남아 v2 측정으로 팔린다.
    정책 전환이 캐시를 자동으로 무효화하도록 프로토콜 지문을 그대로 쓴다.
    """
    return sov_engine.protocol_fingerprint(platform=platform)


def query_cache_key(*, query_text: str, platform: str, requested_model: str) -> str:
    """정규화는 앞뒤 공백과 연속 공백까지만.

    '수서역'과 '수서동'을 같은 키로 묶지 않는다 — 실제로 답변이 다르므로 묶으면
    틀린 숫자를 팔게 된다.
    """
    normalized = " ".join((query_text or "").split())
    material = f"{normalized}|{platform}|{requested_model}|{prompt_version(platform=platform)}"
    return hashlib.sha256(material.encode()).hexdigest()


async def get_cached_answers(
    db: AsyncSession,
    *,
    query_text: str,
    platform: str,
    requested_model: str,
    repeat_count: int,
) -> dict[int, LeadQueryAnswer]:
    """만료되지 않은 답변을 회차별로. 부분 적중이면 부분만 돌려준다.

    반복 3회 중 2회만 캐시에 있으면 나머지 1회만 실제로 호출한다 — 전부 아니면 전무로
    두면 흔한 부분 적중에서 절감이 통째로 사라진다.
    """
    now = datetime.now(timezone.utc)
    key = query_cache_key(
        query_text=query_text, platform=platform, requested_model=requested_model
    )
    rows = (
        await db.execute(
            select(LeadQueryAnswer).where(
                LeadQueryAnswer.query_hash == key,
                LeadQueryAnswer.repeat_no <= repeat_count,
                LeadQueryAnswer.expires_at > now,
            )
        )
    ).scalars().all()
    return {row.repeat_no: row for row in rows}


async def store_answer(
    db: AsyncSession,
    *,
    query_text: str,
    platform: str,
    requested_model: str,
    repeat_no: int,
    answer_model: str | None,
    raw_response: str,
    source_urls: list | None,
    search_calls: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    measured_at: datetime | None = None,
) -> None:
    """성공한 측정만 캐시한다.

    실패 응답을 캐시하면 한 번의 공급자 장애가 7일 동안 모든 신청자에게 전파된다.
    호출부가 SUCCESS만 넘기도록 되어 있지만, 여기서도 빈 응답을 거른다.
    """
    if not (raw_response or "").strip():
        return

    now = measured_at or datetime.now(timezone.utc)
    key = query_cache_key(
        query_text=query_text, platform=platform, requested_model=requested_model
    )
    # **SAVEPOINT 안에서 넣는다.** 캐시 적재는 같은 트랜잭션에서 측정 결과 18행과 함께
    # 진행되는데, 여기서 세션 전체를 rollback하면 그 18행이 통째로 사라진다.
    # 그리고 이 경합은 드문 사고가 아니라 **공유 캐시가 정확히 유도하는 상황**이다 —
    # 두 병원이 같은 질의를 동시에 측정하면 반드시 한쪽이 진다.
    try:
        async with db.begin_nested():
            db.add(
                LeadQueryAnswer(
                    query_hash=key,
                    repeat_no=repeat_no,
                    query_text=query_text[:500],
                    platform=platform,
                    requested_model=requested_model,
                    answer_model=answer_model,
                    prompt_version=prompt_version(platform=platform),
                    raw_response=raw_response,
                    source_urls=source_urls,
                    search_calls=search_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    measured_at=now,
                    expires_at=now + timedelta(days=settings.LEADGEN_QUERY_CACHE_TTL_DAYS),
                )
            )
    except IntegrityError:
        # 다른 진단이 같은 질의를 먼저 넣었다. 캐시는 최적화이지 정합성이 아니므로
        # 경합에서 지는 쪽이 조용히 물러난다 — **SAVEPOINT만 되돌아간다.**
        logger.debug("lead query answer already cached: %s/%s", key[:12], repeat_no)


async def purge_expired(db: AsyncSession) -> int:
    """만료분 삭제. 되살리지 않는다 — 오래된 답변을 오늘 측정으로 파는 유혹을 없앤다."""
    result = await db.execute(
        delete(LeadQueryAnswer).where(LeadQueryAnswer.expires_at <= datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)
