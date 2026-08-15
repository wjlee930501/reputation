import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.monthly_control import MonthlyMeasurementCell
from app.models.sov import SovRecord
from app.services.monthly_manifest import (
    ManifestCellSpec,
    ManifestError,
    apply_manifest_to_report,
    close_manifest,
    exclude_cell,
    freeze_dispatch_manifest,
    freeze_monthly_manifest,
    link_attempt,
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


class ExistingManifestSession(FakeSession):
    def __init__(self, manifest) -> None:
        super().__init__()
        self.manifest = manifest

    def execute(self, _statement):
        manifest = self.manifest

        class Result:
            def scalar_one_or_none(self):
                return manifest

        return Result()


def _spec(index: int, platform: str = "chatgpt") -> ManifestCellSpec:
    return ManifestCellSpec(
        query_key=f"variant:{index}",
        query_text=f"질문 {index}",
        platform=platform,
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
        query_intent="LOCAL",
    )


def test_one_success_nine_failures_is_degraded_and_not_customer_ready() -> None:
    # Given
    cells = [SimpleNamespace(state="SUCCESS", exclusion_reason=None)] + [
        SimpleNamespace(state="FAILED", exclusion_reason=None) for _ in range(9)
    ]

    # When
    summary = summarize_manifest(cells, closed=True, configured_platforms=["chatgpt"])

    # Then
    assert summary.quality == "DEGRADED"
    assert (summary.planned_count, summary.success_count, summary.failed_count) == (10, 1, 9)
    assert summary.customer_ready is False
    assert summary.blockers == ("MANIFEST_CELL_FAILURES", "DOCTOR_ARTIFACT_UNVALIDATED")


def test_missing_or_empty_manifest_is_blocked() -> None:
    # Given / When / Then
    assert (
        summarize_manifest([], closed=True, configured_platforms=["chatgpt"]).quality == "BLOCKED"
    )
    with pytest.raises(ManifestError, match="manifest is required"):
        apply_manifest_to_report(SimpleNamespace(), None)


def test_complete_manifest_still_requires_doctor_artifact() -> None:
    # Given
    cells = [SimpleNamespace(state="SUCCESS", exclusion_reason=None) for _ in range(4)]

    # When
    summary = summarize_manifest(cells, closed=True, configured_platforms=["chatgpt"])

    # Then
    assert summary.quality == "COMPLETE"
    assert summary.customer_ready is False
    assert summary.blockers == ("DOCTOR_ARTIFACT_UNVALIDATED",)


def test_freeze_preserves_already_expanded_query_platform_cells_and_reuses_snapshot() -> None:
    # Given
    session = FakeSession()
    specs = [
        _spec(1, "chatgpt"),
        _spec(1, "gemini"),
        _spec(2, "chatgpt"),
    ]

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
    assert {(cell.query_key, cell.platform) for cell in manifest.cells} == {
        ("variant:1", "chatgpt"),
        ("variant:1", "gemini"),
        ("variant:2", "chatgpt"),
    }
    assert manifest.platform_provenance["query_intents"] == {
        "variant:1": "LOCAL",
        "variant:2": "LOCAL",
    }


def test_freeze_deduplicates_exact_query_platform_specs_without_cross_expanding() -> None:
    session = FakeSession()
    duplicate = _spec(1, "chatgpt")
    manifest = freeze_monthly_manifest(
        session,
        uuid.uuid4(),
        2026,
        7,
        [duplicate, duplicate, _spec(1, "gemini")],
        gemini_configured=True,
    )

    assert [(cell.query_key, cell.platform) for cell in manifest.cells] == [
        ("variant:1", "chatgpt"),
        ("variant:1", "gemini"),
    ]


def test_link_attempt_requires_confirmed_measurement_before_success() -> None:
    cell = MonthlyMeasurementCell(
        manifest_id=uuid.uuid4(),
        query_key="variant:ambiguous",
        query_text="강남 내과 추천",
        platform="chatgpt",
        state="FAILED",
    )
    ambiguous = SovRecord(
        hospital_id=uuid.uuid4(),
        query_id=uuid.uuid4(),
        ai_platform="chatgpt",
        raw_response="응답",
        measurement_status="SUCCESS",
        mention_verdict="AMBIGUOUS",
        is_mentioned=None,
    )
    confirmed = SovRecord(
        hospital_id=uuid.uuid4(),
        query_id=uuid.uuid4(),
        ai_platform="chatgpt",
        raw_response="응답",
        measurement_status="SUCCESS",
        mention_verdict="NOT_MATCHED",
        is_mentioned=False,
    )

    link_attempt(cell, ambiguous)
    assert cell.state == "FAILED"

    link_attempt(cell, confirmed)
    assert cell.state == "SUCCESS"


def test_configured_platform_without_planned_cell_is_blocked() -> None:
    cells = [SimpleNamespace(state="SUCCESS", platform="chatgpt")]
    summary = summarize_manifest(cells, closed=True, configured_platforms=["chatgpt", "gemini"])
    assert summary.quality == "BLOCKED"
    assert "CONFIGURED_PLATFORM_WITHOUT_CELLS" in summary.blockers


def test_open_manifest_cannot_have_final_quality_or_close_before_cutoff() -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    manifest = SimpleNamespace(
        closes_at=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc), closed_at=None
    )
    cells = [SimpleNamespace(state="SUCCESS", platform="chatgpt")]
    assert (
        summarize_manifest(cells, closed=False, configured_platforms=["chatgpt"]).quality
        == "BLOCKED"
    )
    with pytest.raises(ManifestError, match="cutoff"):
        close_manifest(manifest, now=now)
    close_manifest(manifest, now=manifest.closes_at)
    assert manifest.closed_at == manifest.closes_at


def test_pre_boundary_report_is_blocked_pending_task21_schedule_move() -> None:
    manifest = SimpleNamespace(
        id=uuid.uuid4(),
        cells=[SimpleNamespace(state="SUCCESS", platform="chatgpt")],
        configured_platforms=["chatgpt"],
        closes_at=datetime(2026, 8, 1, 0, 15, tzinfo=timezone.utc),
        closed_at=None,
    )
    report = SimpleNamespace()

    summary = apply_manifest_to_report(report, manifest)

    assert summary.quality == "BLOCKED"
    assert report.customer_ready is False
    assert report.cutoff_at == manifest.closes_at
    assert "MANIFEST_OPEN" in report.delivery_blockers


def test_dispatch_reuses_frozen_cells_when_current_specs_are_empty() -> None:
    hospital_id = uuid.uuid4()
    manifest = SimpleNamespace(
        hospital_id=hospital_id,
        period_year=2026,
        period_month=7,
        cells=[SimpleNamespace(state="FAILED", query_text="retired after freeze")],
    )
    session = ExistingManifestSession(manifest)

    reused = freeze_dispatch_manifest(
        session, hospital_id, 2026, 7, [], gemini_configured=False
    )

    assert reused is manifest
    assert reused.cells[0].query_text == "retired after freeze"


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
