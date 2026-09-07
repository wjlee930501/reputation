"""전역 비용 가드레일 + 킬스위치.

콘텐츠 생성(Claude)·이미지 생성(gpt-image/Imagen)·SoV 측정(GPT-4o/Gemini)은 병원 수에
비례해 무제한 확장되므로, 카테고리별 일일/월간 호출 상한과 즉시 차단용 킬스위치로 지출
폭주를 막는다.

설계 원칙:
- Redis 카운터로 카테고리(content|image|sov)별 일일/월간 호출 수를 집계한다.
- 하드 상한 도달 시 이후 호출을 차단(allowed=False)하고 운영자에게 1회 알린다.
- 소프트 임계(하드 상한의 80%) 최초 도달 시 1회 조기 경고한다.
- 킬스위치가 켜지면 카테고리 불문 전부 차단한다.
- **Redis 장애 시 fail-open**: 가드는 비용 보호 장치일 뿐이므로, Redis가 죽었다고 해서
  콘텐츠/이미지/측정 파이프라인 전체를 멈추면 안 된다(가용성 우선). 장애 시 allowed=True를
  돌려주되 warning 로그로 흔적을 남긴다.

시간 기준은 운영 캘린더(Asia/Seoul)를 따른다 — 야간 생성(23:00 KST)과 월말 리포트가 모두
KST 기준이므로 일/월 경계도 KST로 맞춰야 집계가 직관적이다.
"""
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import redis.asyncio as redis_async
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident
from app.services.notification_contracts import NotificationIntent, SlackMessage
from app.services.notification_outbox import enqueue_notification

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 카운터 보존 기간 — 경계 직후에도 직전 기간 값을 잠깐 조회할 수 있게 여유를 둔다.
_DAILY_TTL_SECONDS = 2 * 24 * 60 * 60       # 2일
_MONTHLY_TTL_SECONDS = 40 * 24 * 60 * 60    # 40일
_RESERVATION_TTL_SECONDS = 45 * 24 * 60 * 60

_SOFT_RATIO = 0.8  # 하드 상한의 80% 도달 시 조기 경고

KILL_SWITCH_KEY = "cost_guard:kill_switch"

# 일일 상한 임시 상향의 배수 한도. 야간 생성이 일일 상한에 걸렸을 때 개발자 재배포
# 없이 AE가 그날치를 푸는 것이 목적이라, **월간 상한은 건드리지 않는다** — 월간이
# 실제 예산 천장이고, 하루치 상향이 그 천장을 넘어 지출을 늘릴 수는 없다.
# 상향분은 그날 키에만 저장되므로 다음 날 자동으로 원복된다.
MAX_DAILY_LIMIT_MULTIPLIER = 2

CATEGORIES: tuple[str, ...] = ("content", "image", "sov", "leadgen")

_CATEGORY_LABELS = {
    "content": "콘텐츠 생성(Claude)",
    "image": "이미지 생성",
    "sov": "AI 답변 언급률 측정",
    # 1단(리드마그넷)은 2단 운영 서비스와 예산을 공유하지 않는다(설계 §0). 같은 'sov'
    # 카테고리에 넣으면 무료 진단 폭주가 계약 병원의 월간 측정을 차단하게 된다.
    "leadgen": "무료 진단 측정(리드마그넷)",
}

_RESERVE_BUDGET_SCRIPT = """
local daily = tonumber(redis.call('GET', KEYS[1]) or '0')
local monthly = tonumber(redis.call('GET', KEYS[2]) or '0')
local count = tonumber(ARGV[1])
local daily_limit = tonumber(ARGV[2])
local monthly_limit = tonumber(ARGV[3])

if monthly_limit > 0 and monthly + count > monthly_limit then
  return {0, 'monthly', daily, monthly}
end
if daily_limit > 0 and daily + count > daily_limit then
  return {0, 'daily', daily, monthly}
end

local new_daily = redis.call('INCRBY', KEYS[1], count)
local new_monthly = redis.call('INCRBY', KEYS[2], count)
if daily == 0 then redis.call('EXPIRE', KEYS[1], ARGV[4]) end
if monthly == 0 then redis.call('EXPIRE', KEYS[2], ARGV[5]) end
return {1, '', new_daily, new_monthly}
"""

_RELEASE_BUDGET_SCRIPT = """
local count = tonumber(ARGV[1])
for _, key in ipairs(KEYS) do
  local current = tonumber(redis.call('GET', key) or '0')
  local released = math.min(current, count)
  if released > 0 then redis.call('DECRBY', key, released) end
end
return 1
"""

# 예약 ID가 있으면 같은 논리 작업의 재전달/API-worker 중첩 진입도 한 번만 예약한다.
# receipt에 원래 KST 일/월 key를 저장하므로 자정·월말 뒤 정산도 새 기간을 건드리지 않는다.
_RESERVE_WITH_RECEIPT_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 1 then
  local existing_category = redis.call('HGET', KEYS[3], 'category') or ''
  local existing_count = tonumber(redis.call('HGET', KEYS[3], 'reserved_units') or '-1')
  if existing_category ~= ARGV[6] or existing_count ~= tonumber(ARGV[1]) then
    return {-1, 'conflict', 0, 0, ARGV[7], ARGV[8]}
  end
  local original_daily_key = redis.call('HGET', KEYS[3], 'daily_key')
  local original_monthly_key = redis.call('HGET', KEYS[3], 'monthly_key')
  local existing_consumed = redis.call('HGET', KEYS[3], 'consumed_units') or ''
  local existing_released = redis.call('HGET', KEYS[3], 'released_units') or ''
  local duplicate_status = 2
  if existing_consumed ~= '' then duplicate_status = 3 end
  return {duplicate_status, '', tonumber(redis.call('GET', original_daily_key) or '0'),
    tonumber(redis.call('GET', original_monthly_key) or '0'),
    redis.call('HGET', KEYS[3], 'daily_period'),
    redis.call('HGET', KEYS[3], 'monthly_period'), existing_consumed, existing_released}
end

local daily = tonumber(redis.call('GET', KEYS[1]) or '0')
local monthly = tonumber(redis.call('GET', KEYS[2]) or '0')
local count = tonumber(ARGV[1])
local daily_limit = tonumber(ARGV[2])
local monthly_limit = tonumber(ARGV[3])

if monthly_limit > 0 and monthly + count > monthly_limit then
  return {0, 'monthly', daily, monthly, ARGV[7], ARGV[8]}
end
if daily_limit > 0 and daily + count > daily_limit then
  return {0, 'daily', daily, monthly, ARGV[7], ARGV[8]}
end

local new_daily = redis.call('INCRBY', KEYS[1], count)
local new_monthly = redis.call('INCRBY', KEYS[2], count)
if daily == 0 then redis.call('EXPIRE', KEYS[1], ARGV[4]) end
if monthly == 0 then redis.call('EXPIRE', KEYS[2], ARGV[5]) end
redis.call('HSET', KEYS[3],
  'category', ARGV[6],
  'daily_period', ARGV[7],
  'monthly_period', ARGV[8],
  'daily_key', KEYS[1],
  'monthly_key', KEYS[2],
  'reserved_units', count,
  'consumed_units', '',
  'released_units', '')
redis.call('EXPIRE', KEYS[3], ARGV[9])
return {1, '', new_daily, new_monthly, ARGV[7], ARGV[8]}
"""

_SETTLE_RESERVATION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return {0, 'missing'} end
local category = redis.call('HGET', KEYS[1], 'category') or ''
local daily_period = redis.call('HGET', KEYS[1], 'daily_period') or ''
local monthly_period = redis.call('HGET', KEYS[1], 'monthly_period') or ''
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved_units') or '-1')
if category ~= ARGV[1] or daily_period ~= ARGV[2] or monthly_period ~= ARGV[3]
  or reserved ~= tonumber(ARGV[4]) then
  return {-1, 'receipt_mismatch'}
end
local prior = redis.call('HGET', KEYS[1], 'consumed_units')
local consumed = tonumber(ARGV[5])
if prior and prior ~= '' then
  local released = tonumber(redis.call('HGET', KEYS[1], 'released_units') or '0')
  if tonumber(prior) == consumed then return {2, 'duplicate', tonumber(prior), released} end
  return {3, 'already_settled', tonumber(prior), released}
end
local released = reserved - consumed
local daily_key = redis.call('HGET', KEYS[1], 'daily_key')
local monthly_key = redis.call('HGET', KEYS[1], 'monthly_key')
for _, key in ipairs({daily_key, monthly_key}) do
  local current = tonumber(redis.call('GET', key) or '0')
  local decrement = math.min(current, released)
  if decrement > 0 then redis.call('DECRBY', key, decrement) end
end
redis.call('HSET', KEYS[1], 'consumed_units', consumed, 'released_units', released)
return {1, 'settled', consumed, released}
"""

_UNLIMITED_REMAINING = 2**63 - 1


@dataclass(frozen=True)
class CostGuardDecision:
    allowed: bool
    reason: str | None = None
    receipt: "ReservationReceipt | None" = None


@dataclass(frozen=True)
class ReservationReceipt:
    """One durable, idempotently settleable reservation in its original KST periods."""

    id: str
    category: str
    daily_period: str
    monthly_period: str
    reserved_units: int
    consumed_units: int | None = None
    released_units: int | None = None


def _reservation_key(reservation_id: str) -> str:
    return f"cost_guard:reservation:{reservation_id}"


_redis_client: redis_async.Redis | None = None


def _client() -> redis_async.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_async.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def _limits(category: str) -> tuple[int, int]:
    """카테고리별 (일일 상한, 월간 상한)."""
    table = {
        "content": (
            settings.COST_GUARD_DAILY_CONTENT_CALLS,
            settings.COST_GUARD_MONTHLY_CONTENT_CALLS,
        ),
        "image": (
            settings.COST_GUARD_DAILY_IMAGE_CALLS,
            settings.COST_GUARD_MONTHLY_IMAGE_CALLS,
        ),
        "sov": (
            settings.COST_GUARD_DAILY_SOV_QUERIES,
            settings.COST_GUARD_MONTHLY_SOV_QUERIES,
        ),
        "leadgen": (
            settings.COST_GUARD_DAILY_LEADGEN_CALLS,
            settings.COST_GUARD_MONTHLY_LEADGEN_CALLS,
        ),
    }
    return table[category]


def _now() -> datetime:
    return datetime.now(_KST)


def _daily_period(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _monthly_period(now: datetime) -> str:
    return now.strftime("%Y%m")


def _daily_key(category: str, period: str) -> str:
    return f"cost_guard:{category}:daily:{period}"


def _monthly_key(category: str, period: str) -> str:
    return f"cost_guard:{category}:monthly:{period}"


def _daily_override_key(category: str, period: str) -> str:
    return f"cost_guard:{category}:daily_override:{period}"


async def _effective_daily_limit(
    client: redis_async.Redis, category: str, period: str, configured: int
) -> int:
    """오늘치 상향이 걸려 있으면 그 값을, 아니면 설정값을 쓴다.

    configured가 0이면 이미 무제한이라 상향 개념이 없다. 상향값은 항상
    [configured, configured * MAX_DAILY_LIMIT_MULTIPLIER] 범위로 재한정한다 —
    저장 시점 검증과 별개로, 오래된/조작된 키가 상한을 무력화하지 못하게 한다.
    """
    if configured <= 0:
        return configured
    raw = await client.get(_daily_override_key(category, period))
    if raw is None:
        return configured
    try:
        override = int(raw)
    except (TypeError, ValueError):
        return configured
    return max(configured, min(override, configured * MAX_DAILY_LIMIT_MULTIPLIER))


def validate_daily_limit_override(category: str, limit: int | None) -> None:
    """상향 요청이 허용 범위인지 검사한다 (Redis 접근 없음).

    호출자가 감사 로그를 커밋하기 **전에** 검증할 수 있도록 분리했다 — 순서 규약이
    write_audit_log → commit → 외부 부수효과라, 검증을 부수효과 안에 두면 잘못된 요청에도
    감사 row가 남는다. limit이 None이면 해제 요청이라 범위 검사가 필요 없다.
    """
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")
    if limit is None:
        return
    configured, _monthly = _limits(category)
    if configured <= 0:
        raise ValueError("이 항목은 일일 상한이 설정돼 있지 않아 상향할 수 없습니다.")
    ceiling = configured * MAX_DAILY_LIMIT_MULTIPLIER
    if limit <= configured:
        raise ValueError(f"현재 일일 상한({configured}건)보다 큰 값이어야 합니다.")
    if limit > ceiling:
        raise ValueError(f"일일 상한은 기본값의 {MAX_DAILY_LIMIT_MULTIPLIER}배({ceiling}건)까지만 올릴 수 있습니다.")


async def set_daily_limit_override(
    category: str,
    limit: int,
    *,
    redis_client: redis_async.Redis | None = None,
) -> int:
    """오늘 하루만 적용되는 일일 상한을 설정하고, 실제 적용된 값을 반환한다."""
    validate_daily_limit_override(category, limit)

    client = redis_client or _client()
    period = _daily_period(_now())
    await client.set(_daily_override_key(category, period), limit, ex=_DAILY_TTL_SECONDS)
    return limit


async def clear_daily_limit_override(
    category: str,
    *,
    redis_client: redis_async.Redis | None = None,
) -> None:
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")
    client = redis_client or _client()
    await client.delete(_daily_override_key(category, _daily_period(_now())))


def _actual_daily_key(category: str, period: str) -> str:
    return f"cost_guard:{category}:actual:daily:{period}"


def _actual_monthly_key(category: str, period: str) -> str:
    return f"cost_guard:{category}:actual:monthly:{period}"


def _ttl_for_scope(scope: str) -> int:
    return _DAILY_TTL_SECONDS if scope == "daily" else _MONTHLY_TTL_SECONDS


async def _incr_with_ttl(client: redis_async.Redis, key: str, amount: int, ttl: int) -> int:
    """카운터를 amount만큼 증가시키고, 새로 생성된 경우에만 TTL을 건다.

    이미 존재하는 키의 TTL을 매번 갱신하면 카운터가 만료되지 않아 기간이 넘어가도
    리셋되지 않으므로, 최초 생성(반환값 == amount)일 때만 EXPIRE 한다.
    """
    new_value = int(await client.incrby(key, amount))
    if new_value == amount:
        await client.expire(key, ttl)
    return new_value


async def _claim_flag(client: redis_async.Redis, key: str, scope: str) -> bool:
    """알림 중복 방지 플래그를 선점(NX)한다. 최초 1회만 True.

    TTL을 기간(일/월)에 맞춰 걸어, 기간이 넘어가면 경고를 다시 보낼 수 있게 한다.
    """
    result = await client.set(key, "1", nx=True, ex=_ttl_for_scope(scope))
    return bool(result)


async def _is_kill_switch_active(client: redis_async.Redis) -> bool:
    return bool(await client.exists(KILL_SWITCH_KEY))


async def check_and_increment(
    category: str,
    *,
    count: int = 1,
    redis_client: redis_async.Redis | None = None,
) -> CostGuardDecision:
    """카테고리 호출 예산을 확인하고, 허용 시 카운터를 count만큼 증가시킨다.

    - 킬스위치 활성 또는 일/월 하드 상한 도달 시 allowed=False (증가하지 않음).
    - 하드 상한 도달 시 1회, 소프트 임계(80%) 최초 도달 시 1회 운영자에게 Slack 경고.
    - Redis 장애 시 fail-open(allowed=True) — 비용 보호가 파이프라인 가용성을 해치지 않게.

    count는 한 번에 여러 호출을 예약할 때(예: AI 언급률 측정의 실제 호출 개수) 사용한다.
    """
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")
    if count < 0:
        raise ValueError("cost_guard count must be non-negative")
    if not settings.COST_GUARD_ENABLED:
        return CostGuardDecision(True, None)

    client = redis_client or _client()
    label = _CATEGORY_LABELS[category]

    try:
        if await _is_kill_switch_active(client):
            return CostGuardDecision(False, "비용 가드 킬스위치가 활성화되어 모든 자동 호출이 차단됐습니다.")

        # 캐시된 답변 뒤 판정처럼 이 함수가 예약할 단위는 0이어도, 이후 유료 호출이
        # 존재할 수 있다. 그래서 kill switch 확인 뒤에만 0건 no-op을 허용한다.
        if count == 0:
            return CostGuardDecision(True, None)

        now = _now()
        daily_period = _daily_period(now)
        monthly_period = _monthly_period(now)
        daily_limit, monthly_limit = _limits(category)
        # 오늘치 임시 상향이 있으면 그것으로 판정한다. 월간 상한은 상향 대상이 아니라
        # 하루치를 올려도 이번 달 총지출 천장은 그대로다.
        daily_limit = await _effective_daily_limit(client, category, daily_period, daily_limit)
        daily_key = _daily_key(category, daily_period)
        monthly_key = _monthly_key(category, monthly_period)

        reservation = await client.eval(
            _RESERVE_BUDGET_SCRIPT,
            2,
            daily_key,
            monthly_key,
            count,
            daily_limit,
            monthly_limit,
            _DAILY_TTL_SECONDS,
            _MONTHLY_TTL_SECONDS,
        )
        allowed = bool(int(reservation[0]))
        blocked_scope_raw = reservation[1]
        blocked_scope = (
            blocked_scope_raw.decode() if isinstance(blocked_scope_raw, bytes) else str(blocked_scope_raw)
        )
        new_daily = int(reservation[2])
        new_monthly = int(reservation[3])

        # 한 Lua 연산 안에서 current + count를 검사하고 두 카운터를 함께 예약한다.
        if not allowed and blocked_scope == "monthly":
            await _best_effort_alert(
                client, category, "monthly", monthly_period, new_monthly, monthly_limit, hard=True
            )
            return CostGuardDecision(
                False, f"{label} 월간 호출 상한({monthly_limit}건)에 도달했습니다."
            )
        if not allowed and blocked_scope == "daily":
            await _best_effort_alert(
                client, category, "daily", daily_period, new_daily, daily_limit, hard=True
            )
            return CostGuardDecision(
                False, f"{label} 일일 호출 상한({daily_limit}건)에 도달했습니다."
            )

        # 알림은 결정에 영향을 주지 않도록 증가 이후 best-effort로만 발송한다.
        await _evaluate_scope_alert(client, category, "monthly", monthly_period, new_monthly, monthly_limit)
        await _evaluate_scope_alert(client, category, "daily", daily_period, new_daily, daily_limit)

        return CostGuardDecision(True, None)

    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # fail-open: Redis 장애가 콘텐츠/이미지/측정 파이프라인을 멈추게 하지 않는다(가용성 우선).
        logger.warning(
            "cost_guard fail-open (redis unavailable): category=%s error=%s",
            category,
            exc.__class__.__name__,
        )
        return CostGuardDecision(True, None)


async def reserve(
    category: str,
    *,
    count: int = 1,
    reservation_id: str | uuid.UUID | None = None,
    reserved_at: datetime | None = None,
    redis_client: redis_async.Redis | None = None,
) -> CostGuardDecision:
    """Reserve paid-call units once and return an immutable settlement receipt.

    ``reservation_id`` should identify one logical execution attempt. Reusing it with the
    same category/count returns the original receipt without incrementing counters. Reusing
    it for different inputs is rejected. A zero-unit request still evaluates the kill switch,
    which protects cached workflows that perform a paid judgment after answer lookup.

    Redis remains explicitly fail-open. In that case ``allowed`` is true and ``receipt`` is
    absent, so callers can continue without pretending that a durable reservation exists.
    """
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")
    if count < 0:
        raise ValueError("cost_guard count must be non-negative")
    if not settings.COST_GUARD_ENABLED:
        return CostGuardDecision(True, None)

    client = redis_client or _client()
    label = _CATEGORY_LABELS[category]
    try:
        if await _is_kill_switch_active(client):
            return CostGuardDecision(
                False, "비용 가드 킬스위치가 활성화되어 모든 자동 호출이 차단됐습니다."
            )
        if count == 0:
            return CostGuardDecision(True, None)

        now = (reserved_at or _now()).astimezone(_KST)
        daily_period = _daily_period(now)
        monthly_period = _monthly_period(now)
        daily_limit, monthly_limit = _limits(category)
        daily_limit = await _effective_daily_limit(
            client, category, daily_period, daily_limit
        )
        receipt_id = str(reservation_id or uuid.uuid4())
        if not receipt_id or len(receipt_id) > 200:
            raise ValueError("reservation_id must contain 1..200 characters")

        result = await client.eval(
            _RESERVE_WITH_RECEIPT_SCRIPT,
            3,
            _daily_key(category, daily_period),
            _monthly_key(category, monthly_period),
            _reservation_key(receipt_id),
            count,
            daily_limit,
            monthly_limit,
            _DAILY_TTL_SECONDS,
            _MONTHLY_TTL_SECONDS,
            category,
            daily_period,
            monthly_period,
            _RESERVATION_TTL_SECONDS,
        )
        status = int(result[0])
        scope_raw = result[1]
        scope = scope_raw.decode() if isinstance(scope_raw, bytes) else str(scope_raw)
        daily_value = int(result[2])
        monthly_value = int(result[3])
        original_daily_period = result[4]
        original_monthly_period = result[5]
        if isinstance(original_daily_period, bytes):
            original_daily_period = original_daily_period.decode()
        if isinstance(original_monthly_period, bytes):
            original_monthly_period = original_monthly_period.decode()
        if status == -1:
            raise ValueError(
                f"reservation_id already belongs to different inputs: {receipt_id}"
            )
        if status == 0:
            value = monthly_value if scope == "monthly" else daily_value
            limit = monthly_limit if scope == "monthly" else daily_limit
            period = monthly_period if scope == "monthly" else daily_period
            await _best_effort_alert(
                client, category, scope, period, value, limit, hard=True
            )
            scope_label = "월간" if scope == "monthly" else "일일"
            return CostGuardDecision(
                False, f"{label} {scope_label} 호출 상한({limit}건)에 도달했습니다."
            )

        receipt = ReservationReceipt(
            id=receipt_id,
            category=category,
            daily_period=str(original_daily_period),
            monthly_period=str(original_monthly_period),
            reserved_units=count,
        )
        if status == 3:
            consumed_raw = result[6]
            released_raw = result[7]
            consumed_units = int(consumed_raw) if str(consumed_raw) else None
            released_units = int(released_raw) if str(released_raw) else None
            settled_receipt = replace(
                receipt,
                consumed_units=consumed_units,
                released_units=released_units,
            )
            return CostGuardDecision(
                False,
                "동일한 비용 예약은 이미 정산됐습니다. 새 실행 시도 ID가 필요합니다.",
                settled_receipt,
            )
        if status == 1:
            await _evaluate_scope_alert(
                client, category, "monthly", monthly_period, monthly_value, monthly_limit
            )
            await _evaluate_scope_alert(
                client, category, "daily", daily_period, daily_value, daily_limit
            )
        return CostGuardDecision(True, None, receipt)
    except ValueError:
        raise
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        logger.warning(
            "cost_guard reservation fail-open (redis unavailable): category=%s error=%s",
            category,
            exc.__class__.__name__,
        )
        return CostGuardDecision(True, None)


async def settle_reservation(
    receipt: ReservationReceipt | None,
    *,
    consumed_units: int,
    redis_client: redis_async.Redis | None = None,
) -> ReservationReceipt | None:
    """Settle a receipt once against its original KST day/month counters.

    Returns the receipt with consumed/released units for a new or duplicate identical
    settlement. Missing/expired receipts and Redis failures return ``None`` without
    disrupting the completed business operation.
    """
    if receipt is None or not settings.COST_GUARD_ENABLED:
        return None
    if consumed_units < 0 or consumed_units > receipt.reserved_units:
        raise ValueError("consumed_units must be between zero and reserved_units")
    client = redis_client or _client()
    try:
        result = await client.eval(
            _SETTLE_RESERVATION_SCRIPT,
            1,
            _reservation_key(receipt.id),
            receipt.category,
            receipt.daily_period,
            receipt.monthly_period,
            receipt.reserved_units,
            consumed_units,
        )
        status = int(result[0])
        if status == -1:
            reason = result[1]
            if isinstance(reason, bytes):
                reason = reason.decode()
            logger.warning(
                "cost_guard reservation settlement rejected: id=%s reason=%s",
                receipt.id,
                reason,
            )
            return None
        if status == 0:
            logger.warning("cost_guard reservation receipt missing: id=%s", receipt.id)
            return None
        actual_consumed = int(result[2])
        actual_released = int(result[3])
        return replace(
            receipt,
            consumed_units=actual_consumed,
            released_units=actual_released,
        )
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        logger.warning(
            "cost_guard reservation settlement skipped: id=%s error=%s",
            receipt.id,
            exc.__class__.__name__,
        )
        return None


async def remaining_units(
    category: str,
    *,
    redis_client: redis_async.Redis | None = None,
) -> tuple[int, int]:
    """현재 기간의 (일일, 월간) 예약 가능 수를 반환한다.

    재디스패치 판단용이므로 Redis를 읽지 못하면 fail-closed로 ``(0, 0)``을
    반환한다. 설정상 상한이 없는 범위는 충분히 큰 값으로 표현한다.
    """
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")
    if not settings.COST_GUARD_ENABLED:
        return (_UNLIMITED_REMAINING, _UNLIMITED_REMAINING)

    client = redis_client or _client()
    try:
        if await _is_kill_switch_active(client):
            return (0, 0)
        now = _now()
        daily_period = _daily_period(now)
        monthly_period = _monthly_period(now)
        daily_limit, monthly_limit = _limits(category)
        daily_limit = await _effective_daily_limit(
            client, category, daily_period, daily_limit
        )
        daily_raw = await client.get(_daily_key(category, daily_period))
        monthly_raw = await client.get(_monthly_key(category, monthly_period))
        daily_used = int(daily_raw or 0)
        monthly_used = int(monthly_raw or 0)
        daily_remaining = (
            _UNLIMITED_REMAINING
            if daily_limit <= 0
            else max(daily_limit - daily_used, 0)
        )
        monthly_remaining = (
            _UNLIMITED_REMAINING
            if monthly_limit <= 0
            else max(monthly_limit - monthly_used, 0)
        )
        return daily_remaining, monthly_remaining
    except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        logger.warning(
            "cost_guard remaining capacity unavailable: category=%s error=%s",
            category,
            exc.__class__.__name__,
        )
        return (0, 0)


async def release_reservation(
    category: str,
    count: int,
    *,
    redis_client: redis_async.Redis | None = None,
) -> None:
    """사용하지 않은 예약을 일일·월간 카운터에서 원자적으로 반환한다."""
    if count <= 0 or not settings.COST_GUARD_ENABLED:
        return
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")

    client = redis_client or _client()
    now = _now()
    try:
        await client.eval(
            _RELEASE_BUDGET_SCRIPT,
            2,
            _daily_key(category, _daily_period(now)),
            _monthly_key(category, _monthly_period(now)),
            count,
        )
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # 반환 실패가 원래 예외를 가리거나 태스크 정리를 막으면 안 된다.
        logger.warning(
            "cost_guard reservation release skipped: category=%s count=%s error=%s",
            category,
            count,
            exc.__class__.__name__,
        )


async def record_provider_call(
    category: str,
    *,
    count: int = 1,
    redis_client: redis_async.Redis | None = None,
) -> None:
    """**실제로 발생한** 외부 공급자 호출 수를 예약과 별개로 집계한다(차단하지 않음).

    예약(check_and_increment)은 "논리적 작업 1건" 단위로 태스크 계층에서 한 번 일어나는데,
    실제 호출은 서비스 계층 tenacity `stop_after_attempt(3)` 안에서 최대 3회(이미지 경로는
    gpt-image 3회 실패 후 Imagen 폴백 3회까지) 발생한다. 그래서 예약 카운터만 보면
    실제 지출을 1/3~1/6로 과소 계상하게 되고, 상한이 사실상 그 배수만큼 열린다.

    이 카운터는 그 괴리를 **관측 가능**하게 만드는 용도다. 차단·알림은 하지 않는다 —
    실제 호출 시점에서 되돌릴 수 있는 것이 없고, 재시도 때문에 파이프라인이 멈추면
    가용성만 잃는다. 운영자는 get_usage_snapshot의 reserved 대비 actual 값으로
    재시도 증폭 실태를 보고 상한을 조정한다.

    호출 규약: 실제 공급자 요청 **직전**(재시도마다 1회) 호출한다.
    """
    if count <= 0:
        return
    if not settings.COST_GUARD_ENABLED:
        return
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")

    client = redis_client or _client()
    now = _now()
    try:
        await _incr_with_ttl(
            client, _actual_daily_key(category, _daily_period(now)), count, _DAILY_TTL_SECONDS
        )
        await _incr_with_ttl(
            client,
            _actual_monthly_key(category, _monthly_period(now)),
            count,
            _MONTHLY_TTL_SECONDS,
        )
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # 관측용 카운터가 실제 호출을 막으면 안 된다 — 예약 경로와 같은 fail-open.
        logger.warning(
            "cost_guard actual-usage record skipped (redis unavailable): category=%s error=%s",
            category,
            exc.__class__.__name__,
        )


async def _evaluate_scope_alert(
    client: redis_async.Redis,
    category: str,
    scope: str,
    period: str,
    new_value: int,
    limit: int,
) -> None:
    if limit <= 0:
        return
    if new_value >= limit:
        await _best_effort_alert(client, category, scope, period, new_value, limit, hard=True)
    elif new_value >= int(limit * _SOFT_RATIO):
        await _best_effort_alert(client, category, scope, period, new_value, limit, hard=False)


async def _best_effort_alert(
    client: redis_async.Redis,
    category: str,
    scope: str,
    period: str,
    value: int,
    limit: int,
    *,
    hard: bool,
) -> None:
    """Record one durable cost alert. Alert failures never affect the guard decision."""
    kind = "hard" if hard else "soft"
    flag_key = f"cost_guard:{category}:{scope}:{kind}_alerted:{period}"
    try:
        if await client.exists(flag_key):
            return
        if not await _enqueue_durable_cost_alert(
            category,
            scope,
            period,
            value,
            limit,
            hard=hard,
        ):
            return
        # Claim only after the durable DB projection exists. If the DB is down,
        # the next guard observation retries instead of suppressing the alert
        # for the rest of the day/month.
        await _claim_flag(client, flag_key, scope)
    except Exception:  # noqa: BLE001 — 알림 실패는 가드 결정에 영향 주지 않는다.
        logger.warning("cost_guard alert projection failed: category=%s scope=%s", category, scope)


async def _enqueue_durable_cost_alert(
    category: str,
    scope: str,
    period: str,
    value: int,
    limit: int,
    *,
    hard: bool,
) -> bool:
    """Persist a cost alert through the shared Incident/NotificationOutbox control plane."""

    try:
        sessionmaker = get_async_sessionmaker()
        async with sessionmaker() as db:
            await _enqueue_durable_cost_alert_in_session(
                db,
                category,
                scope,
                period,
                value,
                limit,
                hard=hard,
                now=datetime.now(UTC),
            )
            await db.commit()
            return True
    except Exception:  # noqa: BLE001 — 비용 가드 결정은 DB/알림 장애와 독립이어야 한다.
        logger.warning(
            "cost_guard durable alert skipped: category=%s scope=%s kind=%s",
            category,
            scope,
            "hard" if hard else "soft",
        )
        return False


async def _enqueue_durable_cost_alert_in_session(
    db: AsyncSession,
    category: str,
    scope: str,
    period: str,
    value: int,
    limit: int,
    *,
    hard: bool,
    now: datetime,
) -> None:
    kind = "hard" if hard else "soft"
    incident: Incident | None = None
    if hard:
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="cost_guard",
                object_type="budget_scope",
                object_id=_cost_alert_identity(category, scope, period, kind),
                fingerprint=IncidentFingerprint.COST_BLOCKED,
                incident_type="COST_GUARD_LIMIT_REACHED",
                severity=IncidentSeverity.HIGH,
                customer_impact=(
                    "해당 비용 범위가 리셋되거나 Admin에서 상한을 조정하기 전까지 "
                    "자동 AI 호출이 차단됩니다."
                ),
                source_type="COST_GUARD",
                next_action=(
                    "운영센터의 비용 가드 사용량을 확인하고, 필요한 경우 킬스위치나 "
                    "일일 상한을 조정한 뒤 차단된 작업을 재시도하세요."
                ),
                admin_path="/operations",
                source_id=_cost_alert_identity(category, scope, period, kind),
                safe_error_code="COST_GUARD_LIMIT_REACHED",
                safe_error_message=_cost_alert_safe_message(category, scope, period, value, limit),
            ),
            actor="cost-guard",
            reason="cost guard hard limit reached",
            now=now,
        )
    await enqueue_notification(
        db,
        _build_cost_alert_intent(
            category,
            scope,
            period,
            value,
            limit,
            hard=hard,
            incident=incident,
        ),
        now=now,
    )


def _cost_alert_identity(category: str, scope: str, period: str, kind: str) -> str:
    return f"{category}:{scope}:{period}:{kind}"


def _cost_alert_safe_message(
    category: str, scope: str, period: str, value: int, limit: int
) -> str:
    scope_label = "daily" if scope == "daily" else "monthly"
    return f"category={category} scope={scope_label} period={period} usage={value}/{limit}"


def _build_cost_alert_intent(
    category: str,
    scope: str,
    period: str,
    value: int,
    limit: int,
    *,
    hard: bool,
    incident: Incident | None,
) -> NotificationIntent:
    kind = "hard" if hard else "soft"
    scope_label = "일일" if scope == "daily" else "월간"
    label = _CATEGORY_LABELS[category]
    title = (
        f"비용 가드 {scope_label} 상한 도달 - {label}"
        if hard
        else f"비용 가드 {scope_label} 소프트 경고(80%) - {label}"
    )
    context = (
        f"{scope_label} 사용량이 상한에 도달했습니다: {value}/{limit}건"
        if hard
        else f"{scope_label} 사용량이 상한의 80%를 넘었습니다: {value}/{limit}건"
    )
    next_action = (
        f"이후 {scope_label} 자동 호출은 기간이 리셋될 때까지 차단됩니다. "
        "운영센터에서 사용량과 상한을 확인해 주세요."
        if hard
        else "현재 추세라면 곧 상한에 도달합니다. 운영센터에서 사용량을 확인해 주세요."
    )
    admin_url = urljoin(settings.ADMIN_BASE_URL.rstrip("/") + "/", "operations")
    message = SlackMessage(
        fallback_text=f"{title}: {context}",
        blocks=(
            {
                "type": "header",
                "block_id": "cost_guard_header",
                "text": {"type": "plain_text", "text": title},
            },
            {
                "type": "section",
                "block_id": "cost_guard_context",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*카테고리*\n{label}\n"
                        f"*범위*\n{scope_label} · {period}\n"
                        f"*상태*\n{context}\n"
                        f"*지금 할 일*\n{next_action}"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": "cost_guard_action",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "운영센터에서 확인"},
                        "url": admin_url,
                    },
                ],
            },
        ),
        admin_url=admin_url,
    )
    return NotificationIntent(
        dedupe_key=f"COST_GUARD_ALERT:{category}:{scope}:{period}:{kind}",
        notification_type="COST_GUARD_LIMIT_REACHED" if hard else "COST_GUARD_SOFT_WARNING",
        message=message,
        incident_id=incident.id if incident is not None else None,
        max_attempts=3,
    )


def _empty_category_usage(category: str) -> dict:
    daily_limit, monthly_limit = _limits(category)
    return {
        "category": category,
        "label": _CATEGORY_LABELS[category],
        "daily_used": None,
        "daily_limit": daily_limit,
        "daily_limit_default": daily_limit,
        "monthly_used": None,
        "monthly_limit": monthly_limit,
        "daily_actual": None,
        "monthly_actual": None,
    }


async def get_usage_snapshot(*, redis_client: redis_async.Redis | None = None) -> dict:
    """운영 표면용 — 카테고리별 일일/월간 사용량 + 상한 + 킬스위치 상태.

    *_used는 예약 단위, *_actual은 재시도를 포함한 실제 공급자 호출 수다. 둘이 벌어지면
    재시도 증폭 때문에 상한이 실제 지출을 못 막고 있다는 신호다(record_provider_call 참고).
    """
    client = redis_client or _client()
    now = _now()
    daily_period = _daily_period(now)
    monthly_period = _monthly_period(now)

    available = True
    kill_switch_active: bool | None = False
    categories: list[dict] = []
    try:
        kill_switch_active = await _is_kill_switch_active(client)
        for category in CATEGORIES:
            configured_daily, monthly_limit = _limits(category)
            daily_limit = await _effective_daily_limit(
                client, category, daily_period, configured_daily
            )
            daily_used = int(await client.get(_daily_key(category, daily_period)) or 0)
            monthly_used = int(await client.get(_monthly_key(category, monthly_period)) or 0)
            daily_actual = int(await client.get(_actual_daily_key(category, daily_period)) or 0)
            monthly_actual = int(
                await client.get(_actual_monthly_key(category, monthly_period)) or 0
            )
            categories.append(
                {
                    "category": category,
                    "label": _CATEGORY_LABELS[category],
                    "daily_used": daily_used,
                    "daily_limit": daily_limit,
                    # 화면이 "기본 X → 오늘만 Y"를 구분해 보여줄 수 있게 설정값을 함께 싣는다.
                    "daily_limit_default": configured_daily,
                    "monthly_used": monthly_used,
                    "monthly_limit": monthly_limit,
                    "daily_actual": daily_actual,
                    "monthly_actual": monthly_actual,
                }
            )
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # 관측 실패를 0건/정상으로 위장하지 않는다. 설정 상한만 남기고 실제 값은 명시적으로 비운다.
        logger.warning("cost_guard snapshot degraded (redis unavailable): %s", exc.__class__.__name__)
        available = False
        kill_switch_active = None
        categories = [_empty_category_usage(category) for category in CATEGORIES]

    return {
        "availability": "AVAILABLE" if available else "UNAVAILABLE",
        "enabled": settings.COST_GUARD_ENABLED,
        "kill_switch_active": kill_switch_active,
        "categories": categories,
    }


async def set_kill_switch(enabled: bool, *, redis_client: redis_async.Redis | None = None) -> None:
    """킬스위치를 켜거나 끈다. 켜지면 모든 카테고리가 차단된다(만료 없음)."""
    client = redis_client or _client()
    if enabled:
        await client.set(KILL_SWITCH_KEY, "1")
    else:
        await client.delete(KILL_SWITCH_KEY)
