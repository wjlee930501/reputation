"""Versioned, self-sufficient render inputs for a closed monthly report."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from app.services.doctor_pdf_contracts import DoctorReportView
from app.services.report_narrative import MonthlyNarrative, PublishedWork

REPORT_SNAPSHOT_SCHEMA_VERSION = 1
LEGACY_RENDER_INPUTS_INCOMPLETE = "LEGACY_RENDER_INPUTS_INCOMPLETE"


class FrozenHospitalRenderInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    slug: str
    plan: str | None
    region: list[str]
    specialties: list[str]


class FrozenPeriodRenderInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    month: int
    starts_at: datetime
    ends_at: datetime
    cutoff_at: datetime


class FrozenAeRenderInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    sov_pct: float | None
    published_count: int
    repeat_count: int
    attribution: JsonValue
    strategy: JsonValue
    sov_coverage: JsonValue
    content_operations: JsonValue
    citations: JsonValue
    talking_points: list[str]


class FrozenMeasurementRenderInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol: JsonValue
    platforms: list[str]
    cells: list[dict[str, JsonValue]]
    availability: JsonValue
    comparison: JsonValue


class ReportRenderInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    hospital: FrozenHospitalRenderInput
    period: FrozenPeriodRenderInput
    public_url: str
    doctor_view: dict[str, JsonValue]
    doctor_metric_facts: dict[str, str]
    ae_report: FrozenAeRenderInput
    measurement: FrozenMeasurementRenderInput


class ReportSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    render_inputs: ReportRenderInputs


def _json_default(value: datetime | MonthlyNarrative) -> str | dict:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, MonthlyNarrative):
        return asdict(value)
    msg = f"unsupported report snapshot value: {type(value).__name__}"
    raise TypeError(msg)


def freeze_doctor_view(view: DoctorReportView) -> dict[str, JsonValue]:
    """Convert the typed view to values accepted by a PostgreSQL JSON column."""
    return json.loads(json.dumps(view, ensure_ascii=False, default=_json_default))


def _optional_number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return float(value)


def restore_doctor_view(payload: Mapping[str, JsonValue]) -> DoctorReportView:
    """Restore runtime dataclasses and datetimes consumed by the doctor template."""
    view = dict(payload)
    evidence = view.get("evidence")
    if isinstance(evidence, Mapping):
        restored_evidence = dict(evidence)
        for key in ("found", "missing"):
            case = restored_evidence.get(key)
            if isinstance(case, Mapping) and isinstance(case.get("measured_at"), str):
                restored_case = dict(case)
                restored_case["measured_at"] = datetime.fromisoformat(case["measured_at"])
                restored_evidence[key] = restored_case
        view["evidence"] = restored_evidence
    raw = view.get("narrative")
    if isinstance(raw, Mapping):
        works = tuple(
            PublishedWork(
                title=str(work.get("title") or ""),
                content_id=str(work.get("content_id") or ""),
                url=str(work["url"]) if work.get("url") is not None else None,
                cited_cells=int(work["cited_cells"]) if work.get("cited_cells") is not None else None,
                queries=tuple(str(query) for query in (work.get("queries") or [])),
            )
            for work in (raw.get("works") or [])
            if isinstance(work, Mapping)
        )
        view["narrative"] = MonthlyNarrative(
            title=str(raw.get("title") or ""),
            conclusion=str(raw.get("conclusion") or ""),
            current=_optional_number(raw.get("current")),
            previous=_optional_number(raw.get("previous")),
            comparison_note=str(raw.get("comparison_note") or ""),
            denominator=str(raw.get("denominator") or ""),
            priorities=tuple(str(item) for item in (raw.get("priorities") or [])),
            works=works,
            platform_details=tuple(str(item) for item in (raw.get("platform_details") or [])),
            methods=tuple(str(item) for item in (raw.get("methods") or [])),
            citation_scope=str(raw.get("citation_scope") or ""),
            citation_details=tuple(str(item) for item in (raw.get("citation_details") or [])),
            fulfillment_note=str(raw.get("fulfillment_note") or ""),
            previous_label=str(raw.get("previous_label") or "비교 없음"),
            current_label=str(raw.get("current_label") or "측정 못 함"),
            reference_previous=_optional_number(raw.get("reference_previous")),
            result_story=str(raw.get("result_story") or ""),
            work_story=str(raw.get("work_story") or ""),
            plan_story=str(raw.get("plan_story") or ""),
        )
    return view


def report_render_inputs(
    content_summary: Mapping[str, JsonValue] | None,
) -> ReportRenderInputs | None:
    """Parse a snapshot without adapting incomplete legacy history from live rows."""
    if not isinstance(content_summary, Mapping):
        return None
    snapshot = content_summary.get("report_snapshot")
    if snapshot is None:
        return None
    try:
        return ReportSnapshot.model_validate(snapshot).render_inputs
    except ValidationError:
        return None
