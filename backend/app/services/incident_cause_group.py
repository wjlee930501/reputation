"""Cause-group key shared by the operations center and the daily heartbeat.

같은 원인으로 묶이는 인시던트는 운영센터에서 카드·행 하나이고, 일일 요약에서도 한 건이다.
묶는 규칙을 두 곳에 따로 두면 "확인 필요 N건"과 운영센터 묶음 수가 갈리므로 이 파일 하나만
정의한다.
"""

from __future__ import annotations

from typing import Final

from app.services import cost_guard

_COST_LIMIT_CAUSE_CODES: Final = frozenset(
    {
        "COST_BLOCKED",
        "COST_GUARD_LIMIT_REACHED",
        "LEAD_DIAGNOSIS_COST_BLOCKED",
        "WEEKLY_SOV_COST_GUARD_BLOCKED",
        "MONTHLY_SOV_COST_GUARD_BLOCKED",
    }
)
COST_LIMIT_CAUSE_CODE: Final = "COST_LIMIT_EXHAUSTED"


def canonical_cause_code(code: str | None, incident_type: str) -> str:
    """Return a stable root-cause key shared by cost-limit symptoms."""
    normalized = (code or incident_type or "").strip().upper() or "OPERATION_FAILED"
    return COST_LIMIT_CAUSE_CODE if normalized in _COST_LIMIT_CAUSE_CODES else normalized


def cost_guard_category(
    cause_code: str,
    *,
    incident_type: str,
    source_type: str | None,
    source_id: str | None,
    run_operation_type: str | None,
) -> str | None:
    """Resolve the budget bucket behind a canonical cost-limit incident.

    Takes the five scalars it actually reads rather than the ORM rows, so the queue's
    cheap grouping pass can call it on a column projection instead of loading whole
    `Incident` objects just to throw them away.
    """
    if cause_code != COST_LIMIT_CAUSE_CODE:
        return None
    if source_type == "COST_GUARD" and source_id:
        category = source_id.split(":", 1)[0].lower()
        if category in cost_guard.CATEGORIES:
            return category
    context = " ".join(filter(None, (incident_type, source_type, run_operation_type))).upper()
    if "LEAD" in context:
        return "leadgen"
    if any(token in context for token in ("SOV", "MEASUREMENT", "V0_REPORT")):
        return "sov"
    if "IMAGE" in context:
        return "image"
    if "ESSENCE" in context:
        return "essence"
    return "content"


def cause_group_key(
    *,
    incident_safe_error_code: str | None,
    incident_type: str,
    source_type: str | None,
    source_id: str | None,
    run_safe_error_code: str | None,
    run_operation_type: str | None,
) -> str:
    """Same grouping key `serialize_incident_row` derives, without building a full row.

    Must stay identical to `serialize_incident_row`'s `projected_group_key` or the
    queue, the overview cards and the heartbeat disagree on which incidents share a group.
    """
    projected_code = canonical_cause_code(
        incident_safe_error_code or run_safe_error_code, incident_type
    )
    budget_category = cost_guard_category(
        projected_code,
        incident_type=incident_type,
        source_type=source_type,
        source_id=source_id,
        run_operation_type=run_operation_type,
    )
    return f"{projected_code}:{budget_category}" if budget_category is not None else projected_code
