import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest

BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _cell_with_attempts():
    records = (
        SimpleNamespace(
            id=uuid.uuid4(), measured_at=BASE_TIME, measurement_status="FAILED", is_mentioned=False
        ),
        SimpleNamespace(
            id=uuid.UUID(int=2),
            measured_at=BASE_TIME + timedelta(minutes=2),
            measurement_status="SUCCESS",
            is_mentioned=False,
        ),
        SimpleNamespace(
            id=uuid.UUID(int=1),
            measured_at=BASE_TIME + timedelta(minutes=2),
            measurement_status="SUCCESS",
            is_mentioned=True,
        ),
    )
    return SimpleNamespace(
        query_key="variant:stable",
        query_text="강남 환자 질문",
        platform="chatgpt",
        state="SUCCESS",
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
        attempts=[SimpleNamespace(sov_record=record) for record in records],
    )


def _load(cell, *, live_intent: str, snapshots: dict[str, str] | None):
    class FakeResult:
        def all(self):
            return [(cell, live_intent)]

    class FakeSession:
        def execute(self, _statement):
            return FakeResult()

    provenance = {"query_intents": snapshots} if snapshots is not None else {}
    return load_monthly_sov_manifest(
        FakeSession(), SimpleNamespace(id=uuid.uuid4(), platform_provenance=provenance)
    )


def test_loader_preserves_source_ids_and_selects_one_success_record() -> None:
    cell = _cell_with_attempts()

    loaded = _load(cell, live_intent="LOCAL", snapshots={cell.query_key: "LOCAL"})

    metric_cell = loaded.cells[0]
    assert metric_cell.query_key == "variant:stable"
    assert metric_cell.query_matrix_id == cell.query_matrix_id
    assert metric_cell.query_target_id == cell.query_target_id
    assert metric_cell.query_variant_id == cell.query_variant_id
    assert metric_cell.query_intent_source == "FROZEN"
    assert [record.id for record in loaded.selected_records] == [uuid.UUID(int=1)]


def test_frozen_query_intent_wins_over_later_live_classification_change() -> None:
    cell = _cell_with_attempts()

    loaded = _load(cell, live_intent="INFO", snapshots={cell.query_key: "LOCAL"})
    summary = build_monthly_sov(loaded.cells, ("chatgpt",))

    assert loaded.cells[0].query_intent == "LOCAL"
    assert loaded.cells[0].query_intent_source == "FROZEN"
    assert summary.sov_pct == 100.0


def test_legacy_manifest_marks_live_intent_as_unfrozen() -> None:
    cell = _cell_with_attempts()

    loaded = _load(cell, live_intent="INFO", snapshots=None)

    assert loaded.cells[0].query_intent == "INFO"
    assert loaded.cells[0].query_intent_source == "LEGACY_LIVE"
