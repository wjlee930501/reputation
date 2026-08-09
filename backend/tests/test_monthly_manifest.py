import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.monthly_manifest import (
    ManifestCellSpec,
    ManifestError,
    apply_manifest_to_report,
    exclude_cell,
    freeze_monthly_manifest,
    summarize_manifest,
)


class FakeSession:
    def __init__(self) -> None:
        self.objects: list[object] = []

    def add(self, value: object) -> None:
        self.objects.append(value)

    def add_all(self, values: list[object]) -> None:
        self.objects.extend(values)

    def flush(self) -> None:
        return None


def _spec(index: int, platform: str = "chatgpt") -> ManifestCellSpec:
    return ManifestCellSpec(
        query_key=f"variant:{index}",
        query_text=f"질문 {index}",
        platform=platform,
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
    )


def test_one_success_nine_failures_is_degraded_and_not_customer_ready() -> None:
    # Given
    cells = [SimpleNamespace(state="SUCCESS", exclusion_reason=None)] + [
        SimpleNamespace(state="FAILED", exclusion_reason=None) for _ in range(9)
    ]

    # When
    summary = summarize_manifest(cells)

    # Then
    assert summary.quality == "DEGRADED"
    assert (summary.planned_count, summary.success_count, summary.failed_count) == (10, 1, 9)
    assert summary.customer_ready is False
    assert summary.blockers == ("MANIFEST_CELL_FAILURES", "DOCTOR_ARTIFACT_UNVALIDATED")


def test_missing_or_empty_manifest_is_blocked() -> None:
    # Given / When / Then
    assert summarize_manifest([]).quality == "BLOCKED"
    with pytest.raises(ManifestError, match="manifest is required"):
        apply_manifest_to_report(SimpleNamespace(), None)


def test_complete_manifest_still_requires_doctor_artifact() -> None:
    # Given
    cells = [SimpleNamespace(state="SUCCESS", exclusion_reason=None) for _ in range(4)]

    # When
    summary = summarize_manifest(cells)

    # Then
    assert summary.quality == "COMPLETE"
    assert summary.customer_ready is False
    assert summary.blockers == ("DOCTOR_ARTIFACT_UNVALIDATED",)


def test_freeze_reuses_exact_manifest_and_rejects_changed_cells() -> None:
    # Given
    session = FakeSession()
    specs = [_spec(1), _spec(2, "gemini")]

    # When
    manifest = freeze_monthly_manifest(
        session, uuid.uuid4(), 2026, 7, specs, gemini_configured=True
    )
    reused = freeze_monthly_manifest(
        session,
        manifest.hospital_id,
        2026,
        7,
        specs,
        gemini_configured=True,
        existing=manifest,
    )

    # Then
    assert reused is manifest
    with pytest.raises(ManifestError, match="immutable"):
        freeze_monthly_manifest(
            session,
            manifest.hospital_id,
            2026,
            7,
            specs[:1],
            gemini_configured=True,
            existing=manifest,
        )


@pytest.mark.parametrize("role", ["OPERATOR", "VIEWER"])
def test_exclusion_is_owner_only(role: str) -> None:
    cell = SimpleNamespace(state="FAILED", attempts=[], manifest=SimpleNamespace(closed_at=None))
    with pytest.raises(ManifestError, match="OWNER"):
        exclude_cell(cell, role=role, reason="LEGAL_REMOVAL", actor_id=uuid.uuid4())


@pytest.mark.parametrize("reason", ["PROVIDER_FAILURE", "CONFIG_FAILURE", "COST_FAILURE"])
def test_operational_failures_cannot_shrink_denominator(reason: str) -> None:
    cell = SimpleNamespace(state="FAILED", attempts=[], manifest=SimpleNamespace(closed_at=None))
    with pytest.raises(ManifestError, match="reason"):
        exclude_cell(cell, role="OWNER", reason=reason, actor_id=uuid.uuid4())


def test_exclusion_must_precede_attempt_and_close() -> None:
    attempted = SimpleNamespace(
        state="FAILED", attempts=[object()], manifest=SimpleNamespace(closed_at=None)
    )
    closed = SimpleNamespace(
        state="FAILED", attempts=[], manifest=SimpleNamespace(closed_at=datetime.now(timezone.utc))
    )
    with pytest.raises(ManifestError, match="attempt"):
        exclude_cell(attempted, role="OWNER", reason="DUPLICATE_TARGET", actor_id=uuid.uuid4())
    with pytest.raises(ManifestError, match="closed"):
        exclude_cell(closed, role="OWNER", reason="LEGAL_REMOVAL", actor_id=uuid.uuid4())
