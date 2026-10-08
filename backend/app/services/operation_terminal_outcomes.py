"""Canonical identity for terminal operation outcomes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.models.operations import JSONValue, OperationRun
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint

_FINGERPRINT = IncidentFingerprint.UNKNOWN
_SIGNALLED_DOMAIN_OPERATIONS: Final = frozenset(
    {
        "CREATE_LEAD_DIAGNOSIS",
        "GENERATE_CONTENT_ITEM",
        "GENERATE_MONTHLY_REPORT",
        "MONTHLY_SOV_PERIOD",
        "RECERTIFY_PUBLISHED_IMAGE",
        "RECOVER_LEAD_MEASUREMENT",
        "RECOVER_LEAD_REPORT",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
        "RUN_SOV",
        "SCHEDULED_MONTHLY_REPORT",
        "TRIGGER_V0_REPORT",
    }
)
_DOMAIN_OUTCOME_NAMES: Final = {
    "CREATE_LEAD_DIAGNOSIS": "LEAD_DIAGNOSIS_CREATION",
    "GENERATE_CONTENT_ITEM": "CONTENT_BODY_GENERATION",
    "REGENERATE_CONTENT": "CONTENT_BODY_GENERATION",
    "REGENERATE_CONTENT_IMAGE": "CONTENT_IMAGE_GENERATION",
    "RECERTIFY_PUBLISHED_IMAGE": "CONTENT_IMAGE_CERTIFICATION",
    "GENERATE_MONTHLY_REPORT": "MONTHLY_REPORT",
    "SCHEDULED_MONTHLY_REPORT": "MONTHLY_REPORT",
    "MONTHLY_SOV_PERIOD": "MONTHLY_SOV_FLEET",
    "RUN_SOV": "SOV_MEASUREMENT",
    "RECOVER_LEAD_MEASUREMENT": "LEAD_MEASUREMENT_RECOVERY",
    "RECOVER_LEAD_REPORT": "LEAD_REPORT_RECOVERY",
    "TRIGGER_V0_REPORT": "V0_REPORT",
}
_CLASSIFIED_DOMAIN_OWNERS: Final = {
    "CREATE_LEAD_DIAGNOSIS": "lead_diagnosis_tasks",
    "GENERATE_CONTENT_ITEM": "generation_incident_control",
    "GENERATE_MONTHLY_REPORT": "monthly_artifact_incident_control",
    "RECERTIFY_PUBLISHED_IMAGE": "published_image_recertification",
    "RECOVER_LEAD_MEASUREMENT": "lead_recovery_incidents",
    "RECOVER_LEAD_REPORT": "lead_recovery_incidents",
    "REGENERATE_CONTENT": "generation_incident_control",
    "REGENERATE_CONTENT_IMAGE": "generation_incident_control",
    "RUN_SOV": "weekly_sov_incident_control",
    "TRIGGER_V0_REPORT": "v0_incident_control",
}
_PERIOD_TOKEN: Final = re.compile(
    r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2]|W(?:0[1-9]|[1-4]\d|5[0-3]))"
)


@dataclass(frozen=True, slots=True)
class TerminalOutcomeIdentity:
    """Human exception identity for one durable domain outcome."""

    dedupe_key: str
    source_id: str
    target_type: str
    target_id: str
    period: str | None
    cause: str


def terminal_outcome_identity(run: OperationRun) -> TerminalOutcomeIdentity | None:
    """Return the explicit domain outcome owned by a Celery terminal signal."""

    if run.operation_type not in _SIGNALLED_DOMAIN_OPERATIONS:
        return None
    cause = str(run.safe_error_code or "TASK_FAILED")
    if cause != "TASK_FAILED" and run.operation_type in _CLASSIFIED_DOMAIN_OWNERS:
        return None
    payload = run.request_payload if isinstance(run.request_payload, Mapping) else {}
    dispatch = payload.get("_dispatch")
    dispatch_map = dispatch if isinstance(dispatch, Mapping) else {}
    target_type = str(
        dispatch_map.get("target_type") or payload.get("source_type") or "hospital"
    )
    target_id = str(
        dispatch_map.get("target_id")
        or payload.get("source_id")
        or run.hospital_id
        or "fleet"
    )
    period = _operation_period(run, payload, dispatch_map)
    context = _domain_context(payload, run.result_summary)
    source_id = "|".join(
        (
            _DOMAIN_OUTCOME_NAMES[run.operation_type],
            str(run.hospital_id or "fleet"),
            target_type,
            target_id,
            period or "none",
            context,
        )
    )
    return TerminalOutcomeIdentity(
        dedupe_key=build_incident_key(
            "operation_terminal",
            target_type,
            f"{source_id}|{cause}",
            _FINGERPRINT,
        ),
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        period=period,
        cause=cause,
    )


def _operation_period(
    run: OperationRun,
    payload: Mapping[str, JSONValue],
    dispatch: Mapping[str, JSONValue],
) -> str | None:
    summary = run.result_summary if isinstance(run.result_summary, Mapping) else {}
    for key in ("measurement_month", "measurement_week"):
        value = summary.get(key)
        if isinstance(value, str) and _PERIOD_TOKEN.fullmatch(value):
            return value
    year, month = summary.get("period_year"), summary.get("period_month")
    if isinstance(year, int) and isinstance(month, int) and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    args = dispatch.get("task_args")
    if isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
        values = tuple(args)
        if len(values) >= 4 and values[1] == "monthly":
            if isinstance(values[2], int) and isinstance(values[3], int):
                return f"{values[2]:04d}-{values[3]:02d}"
        if run.operation_type == "GENERATE_MONTHLY_REPORT" and len(values) >= 3:
            if isinstance(values[1], int) and isinstance(values[2], int):
                return f"{values[1]:04d}-{values[2]:02d}"
    for candidate in (payload.get("source_id"), run.idempotency_key):
        if isinstance(candidate, str) and (match := _PERIOD_TOKEN.search(candidate)):
            return match.group(0)
    return None


def _domain_context(
    payload: Mapping[str, JSONValue], summary_value: JSONValue
) -> str:
    summary = summary_value if isinstance(summary_value, Mapping) else {}
    for container in (payload, summary):
        for key in (
            "revision",
            "subject_hash",
            "generation_context_hash",
            "source_snapshot_hash",
        ):
            value = container.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return f"{key}:{value}"
    return "current"
