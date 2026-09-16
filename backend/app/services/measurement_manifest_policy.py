"""Frozen measurement completion rules; never widen a resumed sampling cohort."""

from collections.abc import Mapping

from app.models.monthly_control import MonthlyMeasurementCell, MonthlyMeasurementManifest
from app.services import sov_engine
from app.services.measurement_selection import normalize_platform
from app.services.measurement_slots import (
    ObservationAdequacy,
    slot_is_terminal,
    summarize_observation_slots,
)


def sov_spec_identity(spec: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(spec.get("query_id") or ""),
        normalize_platform(str(spec.get("platform") or "")),
        str(spec.get("target_id") or ""),
    )


def sov_spec_identities_match(left: tuple[str, str, str], right: tuple[str, str, str]) -> bool:
    left_query, left_platform, left_target = left
    right_query, right_platform, right_target = right
    return (
        bool(left_query)
        and left_query == right_query
        and left_platform == right_platform
        and (not left_target or not right_target or left_target == right_target)
    )


def manifest_slot_repeat_count(manifest: MonthlyMeasurementManifest) -> int | None:
    provenance = getattr(manifest, "platform_provenance", None)
    contract = provenance.get("observation_slots") if isinstance(provenance, dict) else None
    repeat_count = contract.get("repeat_count") if isinstance(contract, dict) else None
    if isinstance(repeat_count, int) and not isinstance(repeat_count, bool) and repeat_count > 0:
        return repeat_count
    return None


def manifest_cell_slots_resolved(cell: MonthlyMeasurementCell, repeat_count: int) -> bool:
    slots = list(getattr(cell, "observation_slots", ()) or ())
    return (
        len(slots) == repeat_count
        and {slot.repeat_no for slot in slots} == set(range(1, repeat_count + 1))
        and all(slot_is_terminal(slot) for slot in slots)
    )


def manifest_observation_adequacy(
    manifest: MonthlyMeasurementManifest, *, deadline_reached: bool
) -> ObservationAdequacy | None:
    repeat_count = manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return None
    planned_cells = [
        cell for cell in (getattr(manifest, "cells", ()) or ()) if cell.state != "EXCLUDED"
    ]
    if not planned_cells or any(
        len(list(getattr(cell, "observation_slots", ()) or ())) != repeat_count
        for cell in planned_cells
    ):
        return None
    return summarize_observation_slots(
        (slot for cell in planned_cells for slot in (getattr(cell, "observation_slots", ()) or ())),
        deadline_reached=deadline_reached,
    )


def pending_weekly_manifest_specs(
    manifest: MonthlyMeasurementManifest, selected_specs: list[dict]
) -> list[dict]:
    """Reconnect selected specs to unresolved frozen cells without widening the cap."""

    selected = [sov_spec_identity(spec) for spec in selected_specs]
    repeat_count = manifest_slot_repeat_count(manifest)
    pending: list[dict] = []
    for cell in getattr(manifest, "cells", ()) or ():
        spec = {
            "query_id": cell.query_matrix_id,
            "query_text": cell.query_text,
            "platform": cell.platform,
            "target_id": cell.query_target_id,
            "variant_id": cell.query_variant_id,
            "manifest_cell": cell,
        }
        identity = sov_spec_identity(spec)
        needs_work = (
            cell.state == "FAILED"
            if repeat_count is None
            else cell.state != "EXCLUDED" and not manifest_cell_slots_resolved(cell, repeat_count)
        )
        if needs_work and any(sov_spec_identities_match(identity, chosen) for chosen in selected):
            pending.append(spec)
    return pending


def selected_weekly_manifest_is_resolved(
    manifest: MonthlyMeasurementManifest, selected_specs: list[dict]
) -> bool:
    cells = list(getattr(manifest, "cells", ()) or ())
    selected = [sov_spec_identity(spec) for spec in selected_specs]
    repeat_count = manifest_slot_repeat_count(manifest)
    if not selected:
        return False
    for chosen in selected:
        matching = [
            cell
            for cell in cells
            if sov_spec_identities_match(
                sov_spec_identity(
                    {
                        "query_id": cell.query_matrix_id,
                        "platform": cell.platform,
                        "target_id": cell.query_target_id,
                    }
                ),
                chosen,
            )
        ]
        if not matching:
            return False
        if repeat_count is None:
            if any(cell.state not in {"SUCCESS", "EXCLUDED"} for cell in matching):
                return False
        elif any(
            cell.state != "EXCLUDED" and not manifest_cell_slots_resolved(cell, repeat_count)
            for cell in matching
        ):
            return False
    return True


def weekly_manifest_is_resolved(manifest: MonthlyMeasurementManifest) -> bool:
    cells = list(getattr(manifest, "cells", ()) or ())
    if not cells:
        return False
    repeat_count = manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return all(getattr(cell, "state", None) in {"SUCCESS", "EXCLUDED"} for cell in cells)
    return all(
        cell.state == "EXCLUDED" or manifest_cell_slots_resolved(cell, repeat_count)
        for cell in cells
    )


def manifest_execution_policy_matches(manifest: MonthlyMeasurementManifest) -> bool:
    provenance = getattr(manifest, "platform_provenance", None)
    snapshot = provenance.get("measurement_protocol") if isinstance(provenance, dict) else None
    platforms = tuple(getattr(manifest, "configured_platforms", ()) or ())
    return bool(platforms) and sov_engine.same_execution_policy(
        snapshot,
        sov_engine.measurement_protocol(),
        platforms=platforms,
    )
