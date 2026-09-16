"""Sampling policy is deterministic without a database or provider configuration."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.services.measurement_selection import (
    apply_high_priority_cap,
    apply_total_spec_cap,
    build_measurement_specs,
    is_even_measurement_week,
    normalize_platform,
    priority_included,
)


def variant(index: int, platform: str = "chatgpt", *, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(int=index), platform=platform, query_text=f"query-{index}", is_active=active
    )


def target(index: int, priority: str = "HIGH", variants: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(int=index),
        name=f"target-{index}",
        priority=priority,
        target_month="2026-09",
        variants=variants or [variant(index)],
    )


def select(**overrides: Any) -> tuple[list[dict], int]:
    arguments = dict(
        resolve_variant_query=lambda v: SimpleNamespace(id=v.id, query_intent="LOCAL"),
        gemini_enabled=True,
        query_targets=[],
        fallback_queries=[],
        high_priority_cap=20,
        total_spec_cap=30,
    )
    arguments.update(overrides)
    return build_measurement_specs(**arguments)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GOOGLE", "gemini"),
        (" Gemini ", "gemini"),
        ("CHATGPT", "chatgpt"),
        ("", "chatgpt"),
        ("legacy", "chatgpt"),
    ],
)
def test_platform_aliases_preserve_legacy_normalization(raw: str, expected: str) -> None:
    assert normalize_platform(raw) == expected


@pytest.mark.parametrize(
    "priority,even,start,expected",
    [
        ("HIGH", False, False, True),
        ("LOW", False, True, True),
        ("LOW", True, False, False),
        ("NORMAL", False, True, False),
        ("NORMAL", True, False, True),
        (None, True, False, True),
        ("unknown", False, False, False),
    ],
)
def test_weekly_priority_gates(
    priority: str | None, even: bool, start: bool, expected: bool
) -> None:
    assert priority_included(priority, even, start) is expected


@pytest.mark.parametrize(
    "cap,ids,dropped",
    [(-1, [1, 2, 3, 4], 0), (0, [2, 4], 2), (1, [1, 2, 4], 1), (9, [1, 2, 3, 4], 0)],
)
def test_priority_caps_preserve_order_and_do_not_mutate_input(
    cap: int, ids: list[int], dropped: int
) -> None:
    specs = [
        {"id": 1, "priority": "HIGH"},
        {"id": 2, "priority": "NORMAL"},
        {"id": 3, "priority": "high"},
        {"id": 4, "priority": "LOW"},
    ]
    before = deepcopy(specs)
    kept, count = apply_high_priority_cap(specs, cap)
    assert [item["id"] for item in kept] == ids
    assert count == dropped and specs == before
    assert all(any(item is original for original in specs) for item in kept)
    if cap < 0:
        assert kept is specs


@pytest.mark.parametrize("cap,count", [(-1, 3), (0, 0), (1, 1), (10, 3)])
def test_total_cap_has_explicit_unlimited_zero_and_positive_semantics(cap: int, count: int) -> None:
    specs = [{"id": index} for index in range(3)]
    assert len(apply_total_spec_cap(specs, cap)) == count
    assert len(specs) == 3


def test_monthly_target_measurements_keep_all_variants_without_weekly_caps() -> None:
    specs, trimmed = select(
        query_targets=[target(1, "LOW", [variant(1), variant(2, "google")])],
        measurement_mode="monthly",
        high_priority_cap=0,
        total_spec_cap=0,
        is_even_week=False,
        is_month_start=False,
    )
    assert len(specs) == 2 and trimmed == 0
    assert [spec["platform"] for spec in specs] == ["chatgpt", "gemini"]


def test_weekly_gated_targets_never_resolve_or_purchase_queries() -> None:
    resolver = Mock()
    specs, _ = select(
        query_targets=[target(1, "NORMAL"), target(2, "LOW")],
        is_even_week=False,
        is_month_start=False,
        resolve_variant_query=resolver,
    )
    assert specs == []
    resolver.assert_not_called()


def test_inactive_and_disabled_gemini_variants_do_not_touch_the_resolver() -> None:
    enabled = variant(1)
    resolver = Mock(return_value=SimpleNamespace(id=enabled.id, query_intent="LOCAL"))
    specs, _ = select(
        query_targets=[
            target(1, variants=[enabled, variant(2, "gemini"), variant(3, active=False)])
        ],
        gemini_enabled=False,
        resolve_variant_query=resolver,
    )
    assert len(specs) == 1 and specs[0]["variant_id"] == enabled.id
    resolver.assert_called_once_with(enabled)


def test_selection_is_stable_after_input_order_changes() -> None:
    first, second = target(1, "NORMAL"), target(2, "HIGH")
    a, _ = select(query_targets=[first, second], total_spec_cap=1)
    b, _ = select(query_targets=[second, first], total_spec_cap=1)
    assert a == b and a[0]["target_id"] == second.id


def test_same_query_platform_is_deduplicated_without_dropping_other_platform() -> None:
    resolver = Mock(return_value=SimpleNamespace(id=UUID(int=99), query_intent="LOCAL"))
    specs, _ = select(
        query_targets=[target(1, variants=[variant(1), variant(2), variant(3, "gemini")])],
        resolve_variant_query=resolver,
    )
    assert len(specs) == 2
    assert {spec["platform"] for spec in specs} == {"chatgpt", "gemini"}


def test_info_queries_are_excluded_and_fallback_preserves_its_existing_cap() -> None:
    info = SimpleNamespace(id=UUID(int=1), query_text="info", query_intent="INFO", priority="HIGH")
    local = SimpleNamespace(
        id=UUID(int=2), query_text="local", query_intent="LOCAL", priority="NORMAL"
    )
    resolver = Mock()
    specs, _ = select(
        fallback_queries=[info, local], resolve_variant_query=resolver, total_spec_cap=1
    )
    assert len(specs) == 1 and specs[0]["query_id"] == local.id
    assert specs[0]["target_id"] is None
    resolver.assert_not_called()


def test_week_53_does_not_create_two_consecutive_skipped_weeks() -> None:
    monday = date(2026, 12, 21)
    values = [is_even_measurement_week(monday + timedelta(weeks=i)) for i in range(5)]
    assert all(left != right for left, right in zip(values, values[1:]))
