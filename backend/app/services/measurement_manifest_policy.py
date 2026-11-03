"""Frozen measurement completion rules; never widen a resumed sampling cohort."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.models.monthly_control import MonthlyMeasurementCell, MonthlyMeasurementManifest
from app.services import sov_engine
from app.services.measurement_selection import normalize_platform
from app.services.measurement_slots import (
    ObservationAdequacy,
    slot_is_terminal,
    summarize_observation_slots,
)
from app.services.monthly_sov_types import LEGACY_FINAL_LINEAGE


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


def legacy_cells_final_adequacy(
    manifest: MonthlyMeasurementManifest,
) -> ObservationAdequacy | None:
    """측정 마감이 지난 옛(slot 계약 없는) 매니페스트를 셀 하나 = 표본 하나로 최종 판정한다.

    옛 매니페스트는 실패 셀이 하나라도 있으면 `legacy_complete`가 영원히 거짓이라, 마감 뒤에도
    LIMITED/UNAVAILABLE로 닫히지 않았다 — 월간 측정 사고가 열린 채 남고 그 달 보고서가 전달
    게이트를 통과하지 못했다(2026-09 신기한·장앤김). 마감 뒤에는 확인된 셀만 확정 표본으로 센다.
    전 셀이 성공한 옛 달은 종전 판정(`legacy_complete`)을 그대로 쓰도록 None을 돌려준다.
    """
    planned = [
        cell for cell in (getattr(manifest, "cells", ()) or ()) if cell.state != "EXCLUDED"
    ]
    if not planned or all(cell.state == "SUCCESS" for cell in planned):
        return None
    confirmed = sum(cell.state == "SUCCESS" for cell in planned)
    failed = len(planned) - confirmed
    return ObservationAdequacy(
        planned_slots=len(planned),
        received_answers=confirmed,
        confirmed_slots=confirmed,
        ambiguous_slots=0,
        answer_failed_slots=failed,
        judgment_failed_slots=0,
        pending_slots=failed,
        status="LIMITED" if confirmed else "UNAVAILABLE",
        lineage=LEGACY_FINAL_LINEAGE,
    )


def legacy_manifest_is_final(
    manifest: MonthlyMeasurementManifest | None, *, deadline_reached: bool
) -> bool:
    """마감이 지난 미완료 옛 매니페스트인가 — 보고서가 셀 단위 최종 판정으로 숫자를 세는 조건."""
    return bool(
        manifest is not None
        and deadline_reached
        and manifest_slot_repeat_count(manifest) is None
        and legacy_cells_final_adequacy(manifest) is not None
    )


def slotted_observation_adequacy(
    planned_cells: Sequence[MonthlyMeasurementCell],
    *,
    repeat_count: int,
    deadline_reached: bool,
) -> ObservationAdequacy | None:
    """동결 반복 계약의 최종 적정성 — 계획한 반복은 모두 pending으로 센다.

    만들어지지 않은 반복은 실패한 답변이 아니라 pending 슬롯이다 — 사기 결과가
    아니다. 계획과 모순되는 반복 집합(초과·중복·범위 밖 repeat_no)은 오염된 자료라
    결과가 아니다 — 측정 결과를 꾸며내지 않고 None을 돌려준다. `deadline_reached`
    는 '이 표본은 최종이다'의 뜻 — 측정 마감(07:00)이 지났거나 동결이 닫힌 뒤다.
    """
    if not planned_cells:
        return None
    cell_rows = [
        list(getattr(cell, "observation_slots", ()) or ()) for cell in planned_cells
    ]
    for slots in cell_rows:
        repeat_numbers = [slot.repeat_no for slot in slots]
        if (
            len(slots) > repeat_count
            or len(set(repeat_numbers)) != len(repeat_numbers)
            or any(number < 1 or number > repeat_count for number in repeat_numbers)
        ):
            return None
    rows = [slot for slots in cell_rows for slot in slots]
    expected = len(planned_cells) * repeat_count
    missing = expected - len(rows)
    finished = deadline_reached or (
        missing == 0 and all(slot_is_terminal(row) for row in rows)
    )
    summary = summarize_observation_slots(rows, deadline_reached=finished)
    status = (
        "COMPLETE"
        if expected > 0 and summary.confirmed_slots == expected
        else "LIMITED"
        if finished and summary.confirmed_slots > 0
        else "UNAVAILABLE"
        if finished
        else "IN_PROGRESS"
    )
    return replace(
        summary,
        planned_slots=expected,
        pending_slots=summary.pending_slots + missing,
        status=status,
    )


def manifest_observation_adequacy(
    manifest: MonthlyMeasurementManifest, *, deadline_reached: bool
) -> ObservationAdequacy | None:
    repeat_count = manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return legacy_cells_final_adequacy(manifest) if deadline_reached else None
    planned_cells = [
        cell for cell in (getattr(manifest, "cells", ()) or ()) if cell.state != "EXCLUDED"
    ]
    return slotted_observation_adequacy(
        planned_cells, repeat_count=repeat_count, deadline_reached=deadline_reached
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


@dataclass(frozen=True, slots=True)
class ManifestPolicyVersionTransition:
    period_key: str
    frozen_version: str
    current_version: str


def manifest_policy_version_transition(
    manifest: MonthlyMeasurementManifest,
) -> ManifestPolicyVersionTransition | None:
    """The frozen and live `policy_version` values, only when both exist and differ.

    Only the recorded version string decides this. Any other execution-policy
    difference under the same version, or a missing/unreadable version, returns None.
    """

    provenance = getattr(manifest, "platform_provenance", None)
    snapshot = provenance.get("measurement_protocol") if isinstance(provenance, dict) else None
    frozen = snapshot.get("policy_version") if isinstance(snapshot, dict) else None
    current = sov_engine.measurement_protocol().get("policy_version")
    year = getattr(manifest, "period_year", None)
    month = getattr(manifest, "period_month", None)
    if (
        not isinstance(frozen, str)
        or not frozen.strip()
        or not isinstance(current, str)
        or not current.strip()
        or frozen == current
        or not isinstance(year, int)
        or isinstance(year, bool)
        or not isinstance(month, int)
        or isinstance(month, bool)
    ):
        return None
    return ManifestPolicyVersionTransition(
        period_key=f"{year:04d}-{month:02d}",
        frozen_version=frozen,
        current_version=current,
    )
