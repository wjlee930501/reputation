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
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import redis.asyncio as redis_async
from redis.exceptions import RedisError

from app.core.config import settings
from app.services import notifier

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 카운터 보존 기간 — 경계 직후에도 직전 기간 값을 잠깐 조회할 수 있게 여유를 둔다.
_DAILY_TTL_SECONDS = 2 * 24 * 60 * 60       # 2일
_MONTHLY_TTL_SECONDS = 40 * 24 * 60 * 60    # 40일

_SOFT_RATIO = 0.8  # 하드 상한의 80% 도달 시 조기 경고

KILL_SWITCH_KEY = "cost_guard:kill_switch"

# 일일 상한 임시 상향의 배수 한도. 야간 생성이 일일 상한에 걸렸을 때 개발자 재배포
# 없이 AE가 그날치를 푸는 것이 목적이라, **월간 상한은 건드리지 않는다** — 월간이
# 실제 예산 천장이고, 하루치 상향이 그 천장을 넘어 지출을 늘릴 수는 없다.
# 상향분은 그날 키에만 저장되므로 다음 날 자동으로 원복된다.
MAX_DAILY_LIMIT_MULTIPLIER = 3

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


@dataclass(frozen=True)
class CostGuardDecision:
    allowed: bool
    reason: str | None = None


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
    if count < 0:
        raise ValueError("cost_guard count must be non-negative")
    if count == 0:
        return CostGuardDecision(True, None)
    if not settings.COST_GUARD_ENABLED:
        return CostGuardDecision(True, None)
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"unknown cost_guard category: {category}")

    client = redis_client or _client()
    label = _CATEGORY_LABELS[category]

    try:
        if await _is_kill_switch_active(client):
            return CostGuardDecision(False, "비용 가드 킬스위치가 활성화되어 모든 자동 호출이 차단됐습니다.")

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
    """Slack 경고를 중복 없이 1회 발송한다. 알림 실패가 가드 결정을 바꾸지 않도록 격리."""
    kind = "hard" if hard else "soft"
    flag_key = f"cost_guard:{category}:{scope}:{kind}_alerted:{period}"
    scope_label = "일일" if scope == "daily" else "월간"
    label = _CATEGORY_LABELS[category]
    try:
        if not await _claim_flag(client, flag_key, scope):
            return
        if hard:
            await notifier.notify_ops_alert(
                title=f"비용 가드 {scope_label} 상한 도달 — {label}",
                message=(
                    f"카테고리: *{label}*\n"
                    f"{scope_label} 사용량이 상한에 도달했습니다: {value}/{limit}건\n"
                    f"이후 {scope_label} 자동 호출은 기간이 리셋될 때까지 차단됩니다. "
                    f"긴급 시 킬스위치/상한을 Admin에서 조정해 주세요."
                ),
            )
        else:
            await notifier.notify_ops_alert(
                title=f"비용 가드 {scope_label} 소프트 경고(80%) — {label}",
                message=(
                    f"카테고리: *{label}*\n"
                    f"{scope_label} 사용량이 상한의 80%를 넘었습니다: {value}/{limit}건\n"
                    f"현재 추세라면 곧 상한에 도달합니다. 사용량을 확인해 주세요."
                ),
            )
    except Exception:  # noqa: BLE001 — 알림 실패는 가드 결정에 영향 주지 않는다.
        logger.warning("cost_guard alert delivery failed: category=%s scope=%s", category, scope)


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
