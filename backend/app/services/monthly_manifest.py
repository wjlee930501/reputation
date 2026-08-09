import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)

EXCLUSION_REASONS: Final[frozenset[str]] = frozenset(
    {"DUPLICATE_TARGET", "RETIRED_BEFORE_MEASUREMENT", "LEGAL_REMOVAL"}
)


class ManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ManifestCellSpec:
    query_key: str
    query_text: str
    platform: str
    query_matrix_id: uuid.UUID | None
    query_target_id: uuid.UUID | None
    query_variant_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ManifestSummary:
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    quality: str
    blockers: tuple[str, ...]
    customer_ready: bool = False


def _month_close(year: int, month: int) -> datetime:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime(next_year, next_month, 1, 0, 15, tzinfo=ZoneInfo("Asia/Seoul"))


def freeze_monthly_manifest(
    session,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
    specs: list[ManifestCellSpec],
    *,
    gemini_configured: bool,
    existing: MonthlyMeasurementManifest | None = None,
) -> MonthlyMeasurementManifest:
    if not specs:
        raise ManifestError("manifest requires at least one cell")
    if existing is not None:
        return existing
    platforms = ["chatgpt", *(["gemini"] if gemini_configured else [])]
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital_id,
        period_year=year,
        period_month=month,
        configured_platforms=platforms,
        platform_provenance={
            "chatgpt": "ALWAYS",
            "gemini": "CONFIGURED" if gemini_configured else "NOT_CONFIGURED",
        },
        closes_at=_month_close(year, month),
    )
    frozen_queries = {spec.query_key: spec for spec in specs}
    manifest.cells = [
        MonthlyMeasurementCell(
            query_key=spec.query_key,
            query_text=spec.query_text,
            query_matrix_id=spec.query_matrix_id,
            query_target_id=spec.query_target_id,
            query_variant_id=spec.query_variant_id,
            platform=platform,
            state="FAILED",
        )
        for spec in frozen_queries.values()
        for platform in platforms
    ]
    session.add(manifest)
    session.flush()
    return manifest


def freeze_dispatch_manifest(
    session,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
    measurement_specs: list[dict],
    *,
    gemini_configured: bool,
) -> MonthlyMeasurementManifest:
    existing = session.execute(
        select(MonthlyMeasurementManifest).where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    ).scalar_one_or_none()
    specs = [
        ManifestCellSpec(
            query_key=(
                f"variant:{spec['variant_id']}"
                if spec.get("variant_id")
                else f"query:{spec['query_id']}"
            ),
            query_text=str(spec["query_text"]),
            platform=str(spec["platform"]).lower(),
            query_matrix_id=spec["query_id"],
            query_target_id=spec.get("target_id"),
            query_variant_id=spec.get("variant_id"),
        )
        for spec in measurement_specs
    ]
    return freeze_monthly_manifest(
        session,
        hospital_id,
        year,
        month,
        specs,
        gemini_configured=gemini_configured,
        existing=existing,
    )


def link_attempt(cell: MonthlyMeasurementCell, sov_record) -> MonthlyMeasurementAttempt:
    attempt = MonthlyMeasurementAttempt(cell=cell, sov_record=sov_record)
    if str(sov_record.measurement_status or "SUCCESS").upper() == "SUCCESS":
        cell.state = "SUCCESS"
    elif cell.state != "SUCCESS":
        cell.state = "FAILED"
    return attempt


def summarize_manifest(
    cells: Iterable[MonthlyMeasurementCell],
    *,
    closed: bool,
    configured_platforms: Iterable[str],
) -> ManifestSummary:
    rows = list(cells)
    excluded = sum(cell.state == "EXCLUDED" for cell in rows)
    planned = len(rows) - excluded
    success = sum(cell.state == "SUCCESS" for cell in rows)
    failed = planned - success
    present_platforms = {str(getattr(cell, "platform", "chatgpt")) for cell in rows}
    platform_gap = bool(set(configured_platforms) - present_platforms)
    quality = "BLOCKED"
    if closed and planned > 0 and success > 0 and not platform_gap:
        quality = "COMPLETE" if success == planned else "DEGRADED"
    blockers: list[str] = []
    if not closed:
        blockers.append("MANIFEST_OPEN")
    if platform_gap:
        blockers.append("CONFIGURED_PLATFORM_WITHOUT_CELLS")
    if planned == 0:
        blockers.append("MANIFEST_EMPTY")
    elif failed:
        blockers.append("MANIFEST_CELL_FAILURES")
    blockers.append("DOCTOR_ARTIFACT_UNVALIDATED")
    return ManifestSummary(planned, success, failed, excluded, quality, tuple(blockers))


def apply_manifest_to_report(
    report, manifest: MonthlyMeasurementManifest | None
) -> ManifestSummary:
    if manifest is None:
        raise ManifestError("manifest is required")
    summary = summarize_manifest(
        manifest.cells,
        closed=manifest.closed_at is not None,
        configured_platforms=manifest.configured_platforms,
    )
    report.manifest_id = manifest.id
    report.quality = summary.quality
    report.planned_count = summary.planned_count
    report.success_count = summary.success_count
    report.failed_count = summary.failed_count
    report.excluded_count = summary.excluded_count
    report.customer_ready = False
    report.delivery_blockers = list(summary.blockers)
    report.cutoff_at = manifest.closed_at or manifest.closes_at
    return summary


def close_manifest(manifest: MonthlyMeasurementManifest, *, now: datetime) -> None:
    if manifest.closed_at is not None:
        return
    if now < manifest.closes_at:
        raise ManifestError("manifest cannot close before cutoff")
    manifest.closed_at = now


def exclude_cell(cell, *, role: str, reason: str, actor_id: uuid.UUID) -> None:
    if role != "OWNER":
        raise ManifestError("monthly exclusions require OWNER role")
    if reason not in EXCLUSION_REASONS:
        raise ManifestError("exclusion reason is not permitted")
    if cell.manifest.closed_at is not None:
        raise ManifestError("manifest is already closed")
    if cell.attempts:
        raise ManifestError("cell with an attempt cannot be excluded")
    cell.state = "EXCLUDED"
    cell.exclusion_reason = reason
    cell.excluded_by_id = actor_id
    cell.excluded_at = datetime.now(timezone.utc)
