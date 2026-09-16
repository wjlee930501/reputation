"""Service boundaries keep durable worker contracts and report semantics intact."""

import ast
import importlib
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.measurement_manifest_policy import (
    manifest_cell_slots_resolved,
    manifest_slot_repeat_count,
    pending_weekly_manifest_specs,
    selected_weekly_manifest_is_resolved,
)
from app.services.monthly_publication_facts import (
    contract_publication_timing_counts,
    first_publication_at,
    observed_contract_publications,
)
from app.workers import tasks

MODULES = (
    "content_review_feedback",
    "content_generation_review",
    "v0_measurement_snapshot",
    "measurement_selection",
    "measurement_manifest_policy",
    "monthly_publication_facts",
)


@pytest.mark.parametrize("module_name", MODULES)
def test_services_do_not_import_task_registry_or_own_transactions(module_name: str) -> None:
    module = importlib.import_module(f"app.services.{module_name}")
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app.workers")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback", "apply_async", "delay"}
            elif isinstance(node.func, ast.Name):
                assert node.func.id not in {"SyncSessionLocal", "get_async_sessionmaker"}


@pytest.mark.parametrize(
    "module_name", [name for name in MODULES if name != "content_generation_review"]
)
def test_worker_exports_remain_aliases_not_duplicate_implementations(module_name: str) -> None:
    module = importlib.import_module(f"app.services.{module_name}")
    exported = 0
    for name, value in vars(module).items():
        if (
            name.startswith("_")
            or not inspect.isfunction(value)
            or value.__module__ != module.__name__
        ):
            continue
        if name == "build_measurement_specs":  # Has an explicit DB adapter instead.
            continue
        assert getattr(tasks, "_" + name) is value
        exported += 1
    assert exported > 0


@pytest.mark.parametrize(
    "repeat,expected", [(True, None), (False, None), ("2", None), (0, None), (-1, None), (2, 2)]
)
def test_manifest_repeat_count_rejects_boolean_and_coercible_nonintegers(
    repeat: object, expected: int | None
) -> None:
    manifest = SimpleNamespace(platform_provenance={"observation_slots": {"repeat_count": repeat}})
    assert manifest_slot_repeat_count(manifest) == expected


def slot(number: int, judgment: str = "CONFIRMED") -> SimpleNamespace:
    return SimpleNamespace(
        repeat_no=number,
        judgment_status=judgment,
        answer_status="RECEIVED",
        answer_attempt_count=1,
        judgment_attempt_count=1,
    )


@pytest.mark.parametrize(
    "slots,expected",
    [
        ([slot(1), slot(2)], True),
        ([slot(1), slot(1)], False),
        ([slot(1), slot(2, "AMBIGUOUS")], True),
        ([slot(1), slot(2, "PENDING")], False),
        ([slot(1)], False),
    ],
)
def test_manifest_resolution_requires_exact_repeat_set_and_terminal_judgments(
    slots: list, expected: bool
) -> None:
    assert manifest_cell_slots_resolved(SimpleNamespace(observation_slots=slots), 2) is expected


def test_resume_does_not_widen_the_selected_measurement_cohort() -> None:
    cells = [
        SimpleNamespace(
            query_matrix_id=UUID(int=index),
            query_text=f"query-{index}",
            platform="chatgpt",
            query_target_id=None,
            query_variant_id=None,
            state="FAILED",
            observation_slots=[],
        )
        for index in (1, 2)
    ]
    manifest = SimpleNamespace(
        cells=cells, platform_provenance={"observation_slots": {"repeat_count": 2}}
    )
    selected = [{"query_id": cells[0].query_matrix_id, "platform": "chatgpt", "target_id": None}]
    pending = pending_weekly_manifest_specs(manifest, selected)
    assert [spec["query_id"] for spec in pending] == [cells[0].query_matrix_id]
    assert not selected_weekly_manifest_is_resolved(manifest, selected)
    cells[0].observation_slots = [slot(1), slot(2)]
    assert selected_weekly_manifest_is_resolved(manifest, selected)
    assert pending_weekly_manifest_specs(manifest, selected) == []
    assert cells[1].observation_slots == []  # Unselected work stays untouched.


def test_first_publication_survives_withdrawal_and_republication() -> None:
    original = datetime(2026, 8, 20, tzinfo=timezone.utc)
    item = SimpleNamespace(
        first_published_at=original, published_at=original + timedelta(days=20), status="REJECTED"
    )
    assert first_publication_at(item) == original
    assert observed_contract_publications([item], original) == [item]


def test_report_observation_cutoff_excludes_future_and_never_published_items() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    past = SimpleNamespace(first_published_at=now - timedelta(seconds=1), published_at=None)
    exact = SimpleNamespace(first_published_at=now, published_at=None)
    future = SimpleNamespace(first_published_at=now + timedelta(seconds=1), published_at=None)
    never = SimpleNamespace(first_published_at=None, published_at=None)
    assert observed_contract_publications(iter([past, exact, future, never]), now) == [past, exact]


def test_contract_window_is_start_inclusive_end_exclusive_and_keeps_legacy_fallback() -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    times = [
        start - timedelta(seconds=1),
        start,
        end - timedelta(seconds=1),
        end,
        end + timedelta(seconds=1),
    ]
    items = [SimpleNamespace(first_published_at=None, published_at=point) for point in times]
    assert contract_publication_timing_counts(iter(items), start, end) == (1, 2)
    assert first_publication_at(items[1]) == start
