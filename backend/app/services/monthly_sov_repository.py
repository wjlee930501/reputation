import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)
from app.models.sov import QueryMatrix, SovRecord
from app.services import sov_engine
from app.services.monthly_sov_types import (
    CellAttempt,
    CellState,
    ManifestCellInput,
    QueryIntent,
    QueryIntentSource,
)


@dataclass(frozen=True, slots=True)
class MonthlySovDataError(RuntimeError):
    field: str
    value: str

    def __str__(self) -> str:
        return f"invalid monthly SoV {self.field}: {self.value}"  # copy-guard: internal-only


@dataclass(frozen=True, slots=True)
class LoadedMonthlySov:
    cells: tuple[ManifestCellInput, ...]
    selected_records: tuple[SovRecord, ...]


def _state(value: str) -> CellState:
    normalized = value.upper()
    try:
        return {"SUCCESS": "SUCCESS", "FAILED": "FAILED", "EXCLUDED": "EXCLUDED"}[
            normalized
        ]
    except KeyError as exc:
        raise MonthlySovDataError("cell state", normalized) from exc


def _intent(value: str) -> QueryIntent:
    return "INFO" if value.upper() == "INFO" else "LOCAL"


def _intent_snapshot(
    manifest: MonthlyMeasurementManifest, query_key: str, live_intent: str
) -> tuple[QueryIntent, QueryIntentSource]:
    try:
        frozen_value = manifest.platform_provenance["query_intents"][query_key]
    except (KeyError, TypeError):
        frozen_value = None
    if frozen_value in ("LOCAL", "INFO"):
        return _intent(str(frozen_value)), "FROZEN"
    return _intent(live_intent), "LEGACY_LIVE"


def load_monthly_sov_manifest(
    session, manifest: MonthlyMeasurementManifest
) -> LoadedMonthlySov:
    rows = session.execute(
        select(MonthlyMeasurementCell, QueryMatrix.query_intent)
        .outerjoin(QueryMatrix, MonthlyMeasurementCell.query_matrix_id == QueryMatrix.id)
        .options(
            selectinload(MonthlyMeasurementCell.attempts).selectinload(
                MonthlyMeasurementAttempt.sov_record
            )
        )
        .where(MonthlyMeasurementCell.manifest_id == manifest.id)
        .order_by(MonthlyMeasurementCell.query_key, MonthlyMeasurementCell.platform)
    ).all()
    cells: list[ManifestCellInput] = []
    records_by_id: dict[uuid.UUID, SovRecord] = {}
    for cell, live_intent in rows:
        attempts = tuple(
            CellAttempt(
                record_id=attempt.sov_record.id,
                measured_at=attempt.sov_record.measured_at,
                # **확정 판정만 성공으로 승격한다.** AMBIGUOUS는 status가 SUCCESS이고
                # is_mentioned가 None이다 — 그대로 흘리면 selected_attempt가 보류를
                # 선택해 `sum(attempt.is_mentioned ...)`이 None에서 TypeError로 죽거나,
                # falsy 비교 경로에서는 보류가 미언급으로 계상된다 (PRD F3-7 위반).
                succeeded=sov_engine.record_is_confirmed(attempt.sov_record),
                is_mentioned=bool(attempt.sov_record.is_mentioned),
            )
            for attempt in cell.attempts
        )
        query_intent, intent_source = _intent_snapshot(
            manifest, cell.query_key, str(live_intent or "LOCAL")
        )
        metric_cell = ManifestCellInput(
            query_key=cell.query_key,
            query_text=cell.query_text,
            platform=cell.platform,
            query_intent=query_intent,
            state=_state(cell.state),
            query_matrix_id=cell.query_matrix_id,
            query_target_id=cell.query_target_id,
            query_variant_id=cell.query_variant_id,
            query_intent_source=intent_source,
            attempts=attempts,
        )
        cells.append(metric_cell)
        records_by_id.update(
            (attempt.sov_record.id, attempt.sov_record) for attempt in cell.attempts
        )
    selected_ids = tuple(
        selected.record_id
        for cell in cells
        for selected in (cell.selected_attempt,)
        if selected is not None
    )
    return LoadedMonthlySov(
        cells=tuple(cells),
        selected_records=tuple(records_by_id[record_id] for record_id in selected_ids),
    )
