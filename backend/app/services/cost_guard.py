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
from dataclasses import dataclass
from datetime import datetime
import logging
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

CATEGORIES: tuple[str, ...] = ("content", "image", "sov")

_CATEGORY_LABELS = {
    "content": "콘텐츠 생성(Claude)",
    "image": "이미지 생성",
    "sov": "AI 답변 언급률 측정",
}


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

    count는 한 번에 여러 호출을 예약할 때(예: SoV run 단위 spec 개수) 사용한다.
    """
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
        daily_key = _daily_key(category, daily_period)
        monthly_key = _monthly_key(category, monthly_period)

        current_daily = int(await client.get(daily_key) or 0)
        current_monthly = int(await client.get(monthly_key) or 0)

        # 하드 상한 도달 → 차단(증가 금지). 월간을 일간보다 우선 판정한다.
        if monthly_limit > 0 and current_monthly >= monthly_limit:
            await _best_effort_alert(
                client, category, "monthly", monthly_period, current_monthly, monthly_limit, hard=True
            )
            return CostGuardDecision(
                False, f"{label} 월간 호출 상한({monthly_limit}건)에 도달했습니다."
            )
        if daily_limit > 0 and current_daily >= daily_limit:
            await _best_effort_alert(
                client, category, "daily", daily_period, current_daily, daily_limit, hard=True
            )
            return CostGuardDecision(
                False, f"{label} 일일 호출 상한({daily_limit}건)에 도달했습니다."
            )

        # 허용 → 카운터 증가
        new_daily = await _incr_with_ttl(client, daily_key, count, _DAILY_TTL_SECONDS)
        new_monthly = await _incr_with_ttl(client, monthly_key, count, _MONTHLY_TTL_SECONDS)

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


async def get_usage_snapshot(*, redis_client: redis_async.Redis | None = None) -> dict:
    """운영 표면용 — 카테고리별 일일/월간 사용량 + 상한 + 킬스위치 상태."""
    client = redis_client or _client()
    now = _now()
    daily_period = _daily_period(now)
    monthly_period = _monthly_period(now)

    kill_switch_active = False
    categories: list[dict] = []
    try:
        kill_switch_active = await _is_kill_switch_active(client)
        for category in CATEGORIES:
            daily_limit, monthly_limit = _limits(category)
            daily_used = int(await client.get(_daily_key(category, daily_period)) or 0)
            monthly_used = int(await client.get(_monthly_key(category, monthly_period)) or 0)
            categories.append(
                {
                    "category": category,
                    "label": _CATEGORY_LABELS[category],
                    "daily_used": daily_used,
                    "daily_limit": daily_limit,
                    "monthly_used": monthly_used,
                    "monthly_limit": monthly_limit,
                }
            )
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # 대시보드 조회 실패가 500으로 번지지 않게 — 상한만이라도 표기한다(사용량 0 표시).
        logger.warning("cost_guard snapshot degraded (redis unavailable): %s", exc.__class__.__name__)
        if not categories:
            for category in CATEGORIES:
                daily_limit, monthly_limit = _limits(category)
                categories.append(
                    {
                        "category": category,
                        "label": _CATEGORY_LABELS[category],
                        "daily_used": 0,
                        "daily_limit": daily_limit,
                        "monthly_used": 0,
                        "monthly_limit": monthly_limit,
                    }
                )

    return {
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
