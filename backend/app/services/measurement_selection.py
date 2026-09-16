"""Stable weekly sampling and caps, with query persistence supplied by the caller.

Monthly target selection deliberately preserves its existing uncapped path.
Ordering, platform aliases, and fallback behavior are compatibility contracts.
"""

import uuid
from collections.abc import Callable
from datetime import date

from app.models.sov import AIQueryTarget, AIQueryVariant, QueryMatrix
from app.services import sov_engine

_MEASUREMENT_WEEK_EPOCH = date(2026, 1, 5)  # 2026-W02 월요일 (임의의 고정 기준점)


def is_even_measurement_week(today: date) -> bool:
    """격주 측정에서 이번 주가 '측정하는 주'인가."""
    return ((today - _MEASUREMENT_WEEK_EPOCH).days // 7) % 2 == 0


def priority_included(priority: str | None, is_even_week: bool, is_month_start: bool) -> bool:
    """priority 기반 주간 측정 게이팅 규칙.

    HIGH: 매주 포함 / LOW: 월초(첫째 주)만 / 그 외(NORMAL 등): 짝수 주차만.
    QueryMatrix.priority와 AIQueryTarget.priority에 동일 규칙을 적용해 스로틀링을 단일화한다.
    """
    normalized = str(priority or "NORMAL").upper()
    if normalized == "HIGH":
        return True
    if normalized == "LOW":
        return is_month_start
    return is_even_week


def apply_high_priority_cap(specs: list[dict], cap: int) -> tuple[list[dict], int]:
    """HIGH 우선순위 spec을 상한까지만 유지하고 초과분은 잘라낸다 (결정론적: 앞에서부터 유지).

    Returns: (유지된 specs, 잘린 HIGH spec 개수).
    """
    if cap < 0:
        return specs, 0
    kept: list[dict] = []
    high_seen = 0
    dropped = 0
    for spec in specs:
        if str(spec.get("priority") or "NORMAL").upper() == "HIGH":
            if high_seen >= cap:
                dropped += 1
                continue
            high_seen += 1
        kept.append(spec)
    return kept, dropped


def apply_total_spec_cap(specs: list[dict], cap: int) -> list[dict]:
    """Bound HIGH/NORMAL/LOW combined while preserving deterministic priority order."""

    if cap < 0:
        return specs
    return specs[:cap]


def normalize_platform(platform: str) -> str:
    value = (platform or "CHATGPT").strip().lower()
    if value in {"gemini", "google"}:
        return "gemini"
    return "chatgpt"


def build_measurement_specs(
    *,
    resolve_variant_query: Callable[[AIQueryVariant], QueryMatrix],
    gemini_enabled: bool,
    query_targets: list[AIQueryTarget],
    fallback_queries: list[QueryMatrix],
    is_even_week: bool = True,
    is_month_start: bool = True,
    high_priority_cap: int,
    total_spec_cap: int,
    measurement_mode: str = "weekly",
) -> tuple[list[dict], int]:
    """주간 측정 spec 목록을 만든다.

    target/variant 유래 spec도 fallback 쿼리와 동일하게 target.priority 기준으로 게이팅한다
    (V0 후 target 자동 시드로 인해 스로틀링이 죽는 문제 방지). 마지막에 HIGH 상한과
    전체 상한을 적용해 NORMAL/LOW도 무제한 확장되지 않게 한다.
    Returns: (specs, 잘린 HIGH spec 개수).
    """
    specs: list[dict] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    priority_rank = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
    sorted_targets = sorted(
        query_targets,
        key=lambda target: (
            priority_rank.get(str(getattr(target, "priority", "NORMAL")).upper(), 9),
            str(getattr(target, "target_month", "") or ""),
            str(getattr(target, "name", "") or ""),
            str(getattr(target, "id", "")),
        ),
    )
    for target in sorted_targets:
        target_priority = str(getattr(target, "priority", "NORMAL") or "NORMAL").upper()
        if measurement_mode != "monthly" and not priority_included(
            target_priority, is_even_week, is_month_start
        ):
            continue
        active_variants = sorted(
            [variant for variant in target.variants if variant.is_active],
            key=lambda variant: (
                normalize_platform(variant.platform),
                str(variant.query_text),
                str(variant.id),
            ),
        )
        for variant in active_variants:
            platform = normalize_platform(variant.platform)
            if platform == "gemini" and not gemini_enabled:
                continue
            query = resolve_variant_query(variant)
            query_intent = str(getattr(query, "query_intent", "LOCAL") or "LOCAL").upper()
            if query_intent == sov_engine.QUERY_INTENT_INFO:
                continue
            key = (query.id, platform)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "query_id": query.id,
                    "query_text": variant.query_text,
                    "platform": platform,
                    "target_id": target.id,
                    "variant_id": variant.id,
                    "priority": target_priority,
                    "query_intent": query_intent,
                }
            )

    if specs:
        if measurement_mode == "monthly":
            return specs, 0
        capped, trimmed_high = apply_high_priority_cap(specs, high_priority_cap)
        return apply_total_spec_cap(capped, total_spec_cap), trimmed_high

    platforms = ["chatgpt"]
    if gemini_enabled:
        platforms.append("gemini")
    sorted_fallback_queries = sorted(
        fallback_queries,
        key=lambda query: (
            priority_rank.get(str(getattr(query, "priority", "NORMAL") or "NORMAL").upper(), 9),
            str(getattr(query, "query_text", "") or ""),
            str(getattr(query, "id", "") or ""),
        ),
    )
    for query in sorted_fallback_queries:
        query_intent = str(getattr(query, "query_intent", "LOCAL") or "LOCAL").upper()
        if query_intent == sov_engine.QUERY_INTENT_INFO:
            continue
        for platform in platforms:
            specs.append(
                {
                    "query_id": query.id,
                    "query_text": query.query_text,
                    "platform": platform,
                    "target_id": None,
                    "variant_id": None,
                    "priority": str(getattr(query, "priority", "NORMAL") or "NORMAL").upper(),
                    "query_intent": query_intent,
                }
            )
    capped, trimmed_high = apply_high_priority_cap(specs, high_priority_cap)
    return apply_total_spec_cap(capped, total_spec_cap), trimmed_high
