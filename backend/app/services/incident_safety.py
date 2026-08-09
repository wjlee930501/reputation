"""Pure incident identity, sanitization, filtering, and operator projection helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement

from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_types import IncidentFilters, IncidentFingerprint, IncidentLabels

_SAFE_SEGMENT: Final = re.compile(r"[^a-z0-9_-]+")
_EMAIL: Final = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_BEARER: Final = re.compile(r"(?i)\bbearer\s+\S+")
_SECRET: Final = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+"
)
_PHONE: Final = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1\d[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_ADMIN_ROUTE_ROOTS: Final = ("/operations", "/hospitals", "/leads")


def build_incident_key(
    pipeline: str,
    object_type: str,
    object_id: str,
    fingerprint: IncidentFingerprint | str,
) -> str:
    """Build a deterministic key from a controlled class, never exception prose."""

    pipeline_key = _segment(pipeline)
    object_key = _segment(object_type)
    try:
        failure_class = IncidentFingerprint(fingerprint).value
    except ValueError:
        failure_class = IncidentFingerprint.UNKNOWN.value
    digest = hashlib.sha256(f"{object_id}\0{failure_class}".encode()).hexdigest()[:32]
    return f"incident:v1:{pipeline_key}:{object_key}:{digest}"


def sanitize_operator_text(value: str | None, *, limit: int = 500) -> str | None:
    """Remove common contact and credential material before durable projection."""

    if value is None:
        return None
    cleaned = _EMAIL.sub("[email redacted]", value)
    cleaned = _BEARER.sub("Bearer [redacted]", cleaned)
    cleaned = _SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _PHONE.sub("[phone redacted]", cleaned)
    return " ".join(cleaned.split())[:limit]


def incident_filter_expressions(
    filters: IncidentFilters, *, now: datetime
) -> tuple[ColumnElement[bool], ...]:
    expressions: list[ColumnElement[bool]] = []
    if filters.states:
        expressions.append(Incident.state.in_([state.value for state in filters.states]))
    if filters.severities:
        expressions.append(Incident.severity.in_([item.value for item in filters.severities]))
    if filters.owner_id is not None:
        expressions.append(Incident.owner_id == filters.owner_id)
    if filters.due_before is not None:
        expressions.append(Incident.sla_due_at <= filters.due_before)
    if filters.overdue_only:
        expressions.extend((Incident.sla_due_at.is_not(None), Incident.sla_due_at < now))
    return tuple(expressions)


def project_incident_labels(incident: Incident, *, now: datetime) -> IncidentLabels:
    state = IncidentState(incident.state)
    severity = IncidentSeverity(incident.severity)
    state_labels = {
        IncidentState.OPEN: "조치 필요",
        IncidentState.RETRYING: "자동 재시도 중",
        IncidentState.RECOVERED: "복구됨",
        IncidentState.ACKNOWLEDGED: "확인 완료",
    }
    severity_labels = {
        IncidentSeverity.LOW: "낮음",
        IncidentSeverity.MEDIUM: "보통",
        IncidentSeverity.HIGH: "높음",
        IncidentSeverity.CRITICAL: "긴급",
    }
    sla_label = "기한 미설정"
    if incident.sla_due_at is not None:
        sla_label = "기한 초과" if incident.sla_due_at < now else "기한 내"
    return IncidentLabels(
        state_labels[state],
        severity_labels[severity],
        "담당자 배정됨" if incident.owner_id else "담당자 미배정",
        sla_label,
        sanitize_operator_text(incident.next_action) or "운영 센터에서 확인",
        normalize_admin_path(incident.admin_path),
        state in {IncidentState.OPEN, IncidentState.RETRYING},
    )


def normalize_incident_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9_-]+", "_", value.strip().upper()).strip("_")[:100] or "UNKNOWN"


def normalize_source_id(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"[A-Za-z0-9:_-]{1,255}", value):
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def normalize_admin_path(value: str) -> str:
    path = value.strip()
    if not path or len(path) > 500 or any(item in path for item in ("?", "#", "\\", "%")):
        return "/operations"
    if path.startswith("//") or any(part == ".." for part in path.split("/")):
        return "/operations"
    allowed = any(path == root or path.startswith(f"{root}/") for root in _ADMIN_ROUTE_ROOTS)
    return path if allowed else "/operations"


def _segment(value: str) -> str:
    return _SAFE_SEGMENT.sub("-", value.strip().lower()).strip("-")[:40] or "unknown"
