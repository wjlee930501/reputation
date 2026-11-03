import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.config import settings
from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)
from app.models.report import MonthlyReport
from app.services import sov_engine
from app.services.measurement_manifest_policy import (
    manifest_observation_adequacy,
    slotted_observation_adequacy,
)
from app.services.monthly_period import is_monthly_recovery_window

EXCLUSION_REASONS: Final[frozenset[str]] = frozenset(
    {"DUPLICATE_TARGET", "RETIRED_BEFORE_MEASUREMENT", "LEGAL_REMOVAL"}
)
OBSERVATION_SLOT_CONTRACT_VERSION: Final[int] = 1


def _observation_slot_contract() -> dict[str, int]:
    return {
        "version": OBSERVATION_SLOT_CONTRACT_VERSION,
        "repeat_count": min(settings.SOV_REPEAT_COUNT_WEEKLY, 20),
    }


class ManifestError(RuntimeError):
    pass


class ManifestPolicyDrift(ManifestError):
    """The live month-end basis differs from a manifest that cannot be replaced."""


@dataclass(frozen=True, slots=True)
class ManifestCellSpec:
    query_key: str
    query_text: str
    platform: str
    query_matrix_id: uuid.UUID | None
    query_target_id: uuid.UUID | None
    query_variant_id: uuid.UUID | None
    query_intent: str


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


def _configured_platforms(*, gemini_configured: bool) -> list[str]:
    return ["chatgpt", *(["gemini"] if gemini_configured else [])]


def _frozen_cells(specs: list[ManifestCellSpec]) -> list[MonthlyMeasurementCell]:
    # `specs` is already expanded to query × platform by the dispatcher. Platform-
    # specific variants must not be cross-expanded here.
    frozen_queries = {(spec.query_key, spec.platform.lower()): spec for spec in specs}
    return [
        MonthlyMeasurementCell(
            query_key=spec.query_key,
            query_text=spec.query_text,
            query_matrix_id=spec.query_matrix_id,
            query_target_id=spec.query_target_id,
            query_variant_id=spec.query_variant_id,
            platform=spec.platform.lower(),
            state="FAILED",
        )
        for spec in frozen_queries.values()
    ]


def _protocol_snapshot(measurement_protocol_kwargs: dict | None) -> dict:
    return sov_engine.measurement_protocol(**(measurement_protocol_kwargs or {}))


_AUTHORITATIVE_MEASUREMENT_WINDOWS: Final[frozenset[str]] = frozenset(
    {"month_end", "month_start_dawn"}
)


def _is_month_end_tracking_protocol(protocol: dict) -> bool:
    """고정 추적 세트에 묶인 권위 있는 월간 측정 프로토콜인가.

    옛 month_end 창과 새 month_start_dawn 창 둘 다 추적 세트 지문·크기를 든다 —
    둘의 측정 자체는 같은 기준이 아니지만(same_measurement_basis가 가른다),
    슬롯 소유·교체 금지 같은 동결 계약은 둘에 똑같이 적용된다.
    """

    fingerprint = protocol.get("tracking_set_fingerprint")
    size = protocol.get("tracking_set_size")
    return (
        protocol.get("measurement_window") in _AUTHORITATIVE_MEASUREMENT_WINDOWS
        and isinstance(fingerprint, str)
        and bool(fingerprint)
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size > 0
    )


def _manifest_matches_month_end_freeze(
    manifest: MonthlyMeasurementManifest,
    *,
    protocol: dict,
    platforms: list[str],
    cells: list[MonthlyMeasurementCell],
) -> bool:
    provenance = manifest.platform_provenance
    stored_protocol = (
        provenance.get("measurement_protocol") if isinstance(provenance, dict) else None
    )
    stored_keys = {
        (cell.query_key, cell.platform.lower())
        for cell in (getattr(manifest, "cells", ()) or ())
    }
    incoming_keys = {(cell.query_key, cell.platform.lower()) for cell in cells}
    return (
        list(manifest.configured_platforms or []) == platforms
        and stored_keys == incoming_keys
        and sov_engine.same_measurement_basis(
            stored_protocol,
            protocol,
            platforms=tuple(platforms),
        )
    )


def _replace_manifest_freeze(
    session,
    manifest: MonthlyMeasurementManifest,
    *,
    year: int,
    month: int,
    specs: list[ManifestCellSpec],
    cells: list[MonthlyMeasurementCell],
    platforms: list[str],
    protocol: dict,
) -> MonthlyMeasurementManifest:
    if manifest.closed_at is not None:
        raise ManifestPolicyDrift("closed monthly manifest cannot be superseded")
    provenance = manifest.platform_provenance
    stored_protocol = (
        provenance.get("measurement_protocol") if isinstance(provenance, dict) else None
    )
    # Once month-end tracking has started, `link_attempt` makes SUCCESS terminal at
    # the cell level. Weekly successes may still be superseded by the authoritative
    # month-end tracking freeze.
    if (
        isinstance(stored_protocol, dict)
        and _is_month_end_tracking_protocol(stored_protocol)
        and any(cell.state == "SUCCESS" for cell in manifest.cells)
    ):
        raise ManifestPolicyDrift("monthly manifest with successful attempts cannot be superseded")
    referenced_report_id = session.execute(
        select(MonthlyReport.id).where(MonthlyReport.manifest_id == manifest.id).limit(1)
    ).scalar_one_or_none()
    if referenced_report_id is not None:
        raise ManifestPolicyDrift("monthly manifest referenced by a report cannot be superseded")

    # Keep the manifest row/id. The transaction-local guard is recognized by the DB
    # triggers only for this pre-measurement protocol supersede; ordinary manifest
    # mutation and attempt deletion stay blocked.
    get_bind = getattr(session, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    postgres_guard = getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"
    if postgres_guard:
        session.execute(text("SET LOCAL app.monthly_manifest_supersede = 'on'"))
    # Clearing and flushing first lets delete-orphan remove old cells/attempts before
    # same-key replacement cells are inserted under the unique constraint.
    manifest.cells.clear()
    session.flush()
    manifest.configured_platforms = platforms
    manifest.platform_provenance = {
        "chatgpt": "ALWAYS",
        "gemini": "CONFIGURED" if "gemini" in platforms else "NOT_CONFIGURED",
        "query_intents": {spec.query_key: spec.query_intent for spec in specs},
        "measurement_protocol": protocol,
        "observation_slots": _observation_slot_contract(),
    }
    manifest.frozen_at = datetime.now(timezone.utc)
    manifest.closes_at = _month_close(year, month)
    manifest.cells = cells
    session.flush()
    if postgres_guard:
        session.execute(text("SET LOCAL app.monthly_manifest_supersede = 'off'"))
    return manifest


def freeze_monthly_manifest(
    session,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
    specs: list[ManifestCellSpec],
    *,
    gemini_configured: bool,
    existing: MonthlyMeasurementManifest | None = None,
    measurement_protocol_kwargs: dict | None = None,
) -> MonthlyMeasurementManifest:
    if not specs:
        if existing is not None:
            return existing
        raise ManifestError("manifest requires at least one cell")
    platforms = _configured_platforms(gemini_configured=gemini_configured)
    protocol = _protocol_snapshot(measurement_protocol_kwargs)
    cells = _frozen_cells(specs)
    if existing is not None:
        if not _is_month_end_tracking_protocol(protocol):
            return existing
        if _manifest_matches_month_end_freeze(
            existing,
            protocol=protocol,
            platforms=platforms,
            cells=cells,
        ):
            return existing
        return _replace_manifest_freeze(
            session,
            existing,
            year=year,
            month=month,
            specs=specs,
            cells=cells,
            platforms=platforms,
            protocol=protocol,
        )
    platform_provenance = {
        "chatgpt": "ALWAYS",
        "gemini": "CONFIGURED" if gemini_configured else "NOT_CONFIGURED",
        "query_intents": {spec.query_key: spec.query_intent for spec in specs},
        # 동결 시점의 측정 정책. 전월 대비 비교는 두 달의 이 스냅샷이 같을 때만
        # 성립한다 — 정책이 바뀐 달을 성과 변화로 붙여 팔 수 없다.
        "measurement_protocol": protocol,
    }
    # Observation slots belong to the authoritative month-end measurement freeze.
    # Weekly manifests remain on their historical lineage so a weekly result cannot
    # accidentally become the terminal repeat for the monthly contract.
    if _is_month_end_tracking_protocol(protocol):
        platform_provenance["observation_slots"] = _observation_slot_contract()
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital_id,
        period_year=year,
        period_month=month,
        configured_platforms=platforms,
        platform_provenance=platform_provenance,
        closes_at=_month_close(year, month),
    )
    manifest.cells = cells
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
    measurement_protocol_kwargs: dict | None = None,
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
            query_intent=str(spec.get("query_intent") or "LOCAL").upper(),
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
        measurement_protocol_kwargs=measurement_protocol_kwargs,
    )


def link_attempt(cell: MonthlyMeasurementCell, sov_record) -> MonthlyMeasurementAttempt:
    attempt = MonthlyMeasurementAttempt(cell=cell, sov_record=sov_record)
    if sov_engine.record_is_confirmed(sov_record):
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
    planned_cells = [cell for cell in rows if cell.state != "EXCLUDED"]
    provenance = getattr(next(iter(rows), None), "manifest", None)
    provenance = getattr(provenance, "platform_provenance", None)
    slot_contract = provenance.get("observation_slots") if isinstance(provenance, dict) else None
    contract_repeat_count = (
        slot_contract.get("repeat_count") if isinstance(slot_contract, dict) else None
    )
    has_slot_contract = (
        isinstance(slot_contract, dict)
        and slot_contract.get("version") == OBSERVATION_SLOT_CONTRACT_VERSION
        and isinstance(contract_repeat_count, int)
        and not isinstance(contract_repeat_count, bool)
        and contract_repeat_count > 0
    )
    # Manifests frozen before the slot contract keep their legacy quality. We do
    # not backfill repeat lineage. A closed contract manifest follows the authored
    # final adequacy: missing frozen repeats stay pending, LIMITED is DEGRADED, and
    # UNAVAILABLE stays BLOCKED under the honest-unavailable delivery contract.
    if has_slot_contract:
        adequacy = slotted_observation_adequacy(
            planned_cells,
            repeat_count=contract_repeat_count,
            deadline_reached=closed,
        )
        slot_set_corrupt = adequacy is None
        if (
            not slot_set_corrupt
            and closed
            and adequacy.status == "COMPLETE"
            and success == planned
            and not platform_gap
        ):
            quality = "COMPLETE"
        elif not slot_set_corrupt and closed and adequacy.status == "LIMITED":
            quality = "DEGRADED"
        else:
            quality = "BLOCKED"
    blockers: list[str] = []
    if not closed:
        blockers.append("MANIFEST_OPEN")
    if platform_gap:
        blockers.append("CONFIGURED_PLATFORM_WITHOUT_CELLS")
    if planned == 0:
        blockers.append("MANIFEST_EMPTY")
    elif failed:
        blockers.append("MANIFEST_CELL_FAILURES")
    if has_slot_contract:
        if slot_set_corrupt:
            blockers.append("MEASUREMENT_SLOT_SET_INCOMPLETE")
        elif adequacy.status == "LIMITED":
            blockers.append("MEASUREMENT_SAMPLE_LIMITED")
        elif adequacy.status == "UNAVAILABLE":
            blockers.append("MEASUREMENT_SAMPLE_UNAVAILABLE")
        elif adequacy.status == "IN_PROGRESS":
            blockers.append("MEASUREMENT_SLOTS_PENDING")
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


def reopen_incomplete_manifest_for_recovery(
    session, manifest: MonthlyMeasurementManifest, *, now: datetime
) -> bool:
    """Reopen only an incomplete prior-month manifest during the bounded window."""

    if manifest.closed_at is None or not is_monthly_recovery_window(
        now, manifest.period_year, manifest.period_month
    ):
        return False
    # 최종 표본(COMPLETE·LIMITED·UNAVAILABLE)은 절대 다시 열지 않는다 — 열리면
    # 07:00 마감 기준과 보고서 회복이 흔들린다. 미해결 미완료(IN_PROGRESS)는
    # closed_at이 있어도 창 안에서는 다시 열 수 있다.
    adequacy = manifest_observation_adequacy(manifest, deadline_reached=False)
    if adequacy is not None and adequacy.status in {
        "COMPLETE",
        "LIMITED",
        "UNAVAILABLE",
    }:
        return False
    summary = summarize_manifest(
        manifest.cells,
        closed=True,
        configured_platforms=manifest.configured_platforms,
    )
    if (
        summary.quality == "COMPLETE"
        and summary.failed_count == 0
        and summary.excluded_count == 0
    ):
        return False
    get_bind = getattr(session, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    postgres_guard = getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"
    if postgres_guard:
        session.execute(text("SET LOCAL app.monthly_manifest_recovery = 'on'"))
    manifest.closed_at = None
    session.flush()
    if postgres_guard:
        session.execute(text("SET LOCAL app.monthly_manifest_recovery = 'off'"))
    return True


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
