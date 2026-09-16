import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)
from app.models.sov import QueryMatrix
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
class FrozenReportRecord:
    """Read-only evidence context; never write a historic question onto a live ORM row."""

    record: Any
    report_query_text: str
    report_query_intent: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.record, name)


def _canonical_records(cell, manifest, slots) -> tuple:
    linked = tuple(attempt.sov_record for attempt in cell.attempts)
    if any(record is None for record in linked):
        raise MonthlySovDataError("attempt record", "missing")
    for record in linked:
        for field, expected in (
            ("hospital_id", getattr(manifest, "hospital_id", None)),
            ("query_id", cell.query_matrix_id),
            ("ai_platform", cell.platform),
        ):
            if (
                expected is not None
                and hasattr(record, field)
                and getattr(record, field) != expected
            ):
                raise MonthlySovDataError("attempt ownership", field)
    provenance = getattr(manifest, "platform_provenance", None) or {}
    slot_policy = provenance.get("observation_slots") or {}
    repeat_count = slot_policy.get("repeat_count") if isinstance(slot_policy, dict) else None
    if cell.state == "EXCLUDED":
        return ()
    if repeat_count is not None:
        if type(repeat_count) is not int or repeat_count < 1:
            raise MonthlySovDataError("observation repeat policy", "invalid")
        if any(
            type(slot.repeat_no) is not int or not 1 <= slot.repeat_no <= repeat_count
            for slot in slots
        ):
            raise MonthlySovDataError("observation repeat set", "outside frozen plan")
    if not slots:
        if repeat_count is not None:
            return ()  # Unexecuted repeats are pending, never legacy successes.
        return tuple({record.id: record for record in linked}.values())
    by_id = {record.id: record for record in linked}
    selected = []
    seen = set()
    repeats = set()
    for slot in slots:
        if type(slot.repeat_no) is not int or slot.repeat_no < 1 or slot.repeat_no in repeats:
            raise MonthlySovDataError("observation repeat", "invalid or duplicate")
        repeats.add(slot.repeat_no)
        for field, expected in (
            ("hospital_id", getattr(manifest, "hospital_id", None)),
            ("query_id", cell.query_matrix_id),
            ("platform", cell.platform),
            ("monthly_cell_id", getattr(cell, "id", None)),
            ("scope", "MONTHLY"),
        ):
            if expected is not None and hasattr(slot, field) and getattr(slot, field) != expected:
                raise MonthlySovDataError("slot ownership", field)
        if slot.judgment_status != "CONFIRMED":
            continue  # A failed/ambiguous repeat is not a non-mention, nor its stale retry.
        record = by_id.get(slot.sov_record_id)
        if (
            slot.answer_status != "RECEIVED"
            or record is None
            or not sov_engine.record_is_confirmed(record)
            or record.id in seen
        ):
            raise MonthlySovDataError("confirmed observation", "missing or inconsistent record")
        seen.add(record.id)
        selected.append(record)
    return tuple(sorted(selected, key=lambda record: str(record.id)))


@dataclass(frozen=True, slots=True)
class LoadedMonthlySov:
    cells: tuple[ManifestCellInput, ...]
    # 셀당 대표 응답 1건. 원장 리포트의 '나온 사례/안 나온 사례' 인용문용이다.
    selected_records: tuple[FrozenReportRecord, ...]
    # 점수에 실제로 쓴 성공 측정 전부(셀당 최대 반복 수만큼). 타깃별 언급 빈도처럼
    # 비율을 세는 곳은 대표 1건이 아니라 이쪽을 써야 한다 — 대표는 언급된 시도를
    # 먼저 고르므로 그걸로 비율을 세면 위로 편향된다.
    scored_records: tuple[FrozenReportRecord, ...]


def _state(value: str) -> CellState:
    normalized = value.upper()
    try:
        return {"SUCCESS": "SUCCESS", "FAILED": "FAILED", "EXCLUDED": "EXCLUDED"}[normalized]
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


def load_monthly_sov_manifest(session, manifest: MonthlyMeasurementManifest) -> LoadedMonthlySov:
    rows = session.execute(
        select(MonthlyMeasurementCell, QueryMatrix.query_intent)
        .outerjoin(QueryMatrix, MonthlyMeasurementCell.query_matrix_id == QueryMatrix.id)
        .options(
            selectinload(MonthlyMeasurementCell.attempts).selectinload(
                MonthlyMeasurementAttempt.sov_record
            ),
            selectinload(MonthlyMeasurementCell.observation_slots),
        )
        .where(MonthlyMeasurementCell.manifest_id == manifest.id)
        .order_by(MonthlyMeasurementCell.query_key, MonthlyMeasurementCell.platform)
    ).all()
    cells: list[ManifestCellInput] = []
    records_by_id: dict[uuid.UUID, FrozenReportRecord] = {}
    for cell, live_intent in rows:
        observation_slots = list(getattr(cell, "observation_slots", ()) or ())
        canonical_records = _canonical_records(cell, manifest, observation_slots)
        slot_policy = (getattr(manifest, "platform_provenance", None) or {}).get(
            "observation_slots"
        ) or {}
        planned_repeats = slot_policy.get("repeat_count") if isinstance(slot_policy, dict) else None
        planned_repeats = (
            planned_repeats
            if type(planned_repeats) is int and planned_repeats > 0
            else len(observation_slots)
        )
        missing_repeats = max(0, planned_repeats - len(observation_slots))
        attempts = tuple(
            CellAttempt(
                record_id=record.id,
                measured_at=record.measured_at,
                # **확정 판정만 성공으로 승격한다.** AMBIGUOUS는 status가 SUCCESS이고
                # is_mentioned가 None이다 — 그대로 흘리면 셀 빈도(k/n)의 분모에
                # 보류가 섞여 미언급으로 계상된다 (PRD F3-7 위반).
                succeeded=sov_engine.record_is_confirmed(record),
                is_mentioned=bool(record.is_mentioned),
                mention_context=getattr(record, "mention_context", None),
                answer_model=(
                    str(answer_model).strip()
                    if (answer_model := getattr(record, "answer_model", None))
                    else None
                ),
                search_calls=getattr(record, "search_calls", None),
            )
            for record in canonical_records
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
            planned_repeat_count=planned_repeats,
            received_answer_count=sum(
                slot.answer_status == "RECEIVED" for slot in observation_slots
            ),
            confirmed_slot_count=sum(
                slot.judgment_status == "CONFIRMED" for slot in observation_slots
            ),
            ambiguous_slot_count=sum(
                slot.judgment_status == "AMBIGUOUS" for slot in observation_slots
            ),
            answer_failed_slot_count=sum(
                slot.answer_status == "FAILED" for slot in observation_slots
            ),
            judgment_failed_slot_count=sum(
                slot.answer_status == "RECEIVED" and slot.judgment_status == "FAILED"
                for slot in observation_slots
            ),
            pending_slot_count=missing_repeats
            + sum(
                slot.judgment_status not in {"CONFIRMED", "AMBIGUOUS"} for slot in observation_slots
            ),
            slot_lineage="SLOTTED" if planned_repeats else "LEGACY_UNKNOWN",
        )
        cells.append(metric_cell)
        for record in canonical_records:
            if record.id in records_by_id:
                raise MonthlySovDataError("record reuse", "same observation in multiple cells")
            records_by_id[record.id] = FrozenReportRecord(record, cell.query_text, query_intent)
    selected_ids = tuple(
        selected.record_id
        for cell in cells
        for selected in (cell.selected_attempt,)
        if selected is not None
    )
    scored_ids = tuple(attempt.record_id for cell in cells for attempt in cell.successful_attempts)
    return LoadedMonthlySov(
        cells=tuple(cells),
        selected_records=tuple(records_by_id[record_id] for record_id in selected_ids),
        scored_records=tuple(records_by_id[record_id] for record_id in scored_ids),
    )
