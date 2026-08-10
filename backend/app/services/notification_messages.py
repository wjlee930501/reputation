"""Pure Slack Block Kit projections and their deterministic identities."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from app.models.operations import JSONValue
from app.services.incident_safety import normalize_incident_code, sanitize_operator_text
from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationIntent,
    NotificationPayloadError,
    SlackMessage,
    validate_admin_url,
    validate_message,
)

_MAX_BLOCKS = 50
_MAX_SECTION_CHARS = 2900


def build_open_incident_notification(
    incident: IncidentSlackProjection, admin_base_url: str
) -> NotificationIntent:
    return _incident_notification(incident, admin_base_url, recovered=False)


def build_recovered_incident_notification(
    incident: IncidentSlackProjection, admin_base_url: str
) -> NotificationIntent:
    return _incident_notification(incident, admin_base_url, recovered=True)


def _incident_notification(
    incident: IncidentSlackProjection, admin_base_url: str, *, recovered: bool
) -> NotificationIntent:
    event = "INCIDENT_RECOVERED" if recovered else "INCIDENT_OPEN"
    status = "복구 확인" if recovered else "운영 확인 필요"
    hospital_name = _safe_text(incident.hospital_name, 100)
    owner_label = _operator_owner_label(incident.owner_label)
    deadline_label = _operator_deadline_label(incident.sla_label)
    severity_label = _operator_severity_label(incident.severity)
    url = _admin_url(admin_base_url, incident.admin_path)
    problem = "자동 복구가 확인되었습니다." if recovered else incident.problem
    next_action = (
        "운영센터에서 복구 결과를 확인하고 ‘확인 완료’ 처리하세요."
        if recovered
        else incident.next_action
    )
    action_label = "복구 상태 확인" if recovered else "운영센터에서 조치하기"
    support_fallback = (
        "운영센터의 조치 버튼을 사용할 수 없거나 같은 문제가 반복되면 "
        "‘개발팀 문의용 정보 복사’를 개발팀에 전달하세요."
    )
    developer_reference = _developer_reference(incident)
    message = _message(
        f"[{status}] {hospital_name}",
        (
            _block("header", "header", {"type": "plain_text", "text": status}),
            _block(
                "section",
                "incident_identity",
                {
                    "type": "mrkdwn",
                    "text": f"*{hospital_name}* · {severity_label}",
                },
            ),
            _block(
                "section",
                "incident_context",
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*무슨 문제인지*\n{_safe_text(problem, 500)}\n"
                        f"*고객 영향*\n{_safe_text(incident.customer_impact, 500)}\n"
                        f"*지금 할 일*\n{_safe_text(next_action, 500)} "
                        f"{support_fallback}\n"
                        f"담당: {owner_label} · "
                        f"언제까지: {deadline_label}"
                    ),
                },
            ),
            _block(
                "section",
                "developer_reference",
                {
                    "type": "mrkdwn",
                    "text": f"*개발팀에 전달할 정보*\n`{developer_reference}`",
                },
            ),
            _action_block("incident_action", url, action_label),
        ),
        url,
    )
    return NotificationIntent(
        dedupe_key=f"{event}:{incident.incident_id}:v{incident.version}",
        notification_type=event,
        message=message,
        hospital_id=incident.hospital_id,
        incident_id=incident.incident_id,
        operation_run_id=incident.operation_run_id,
    )


def build_summary_notification(
    incidents: Sequence[IncidentSlackProjection],
    window_start: datetime,
    window_end: datetime,
    event_type: str,
    admin_base_url: str,
) -> NotificationIntent:
    unique: dict[uuid.UUID, IncidentSlackProjection] = {}
    for incident in incidents:
        existing = unique.get(incident.incident_id)
        if existing is not None and existing != incident:
            raise NotificationPayloadError("SUMMARY_INCIDENT_CONFLICT")
        unique[incident.incident_id] = incident
    ordered = tuple(sorted(unique.values(), key=lambda item: str(item.incident_id)))
    if not ordered:
        raise NotificationPayloadError("SUMMARY_REQUIRES_INCIDENTS")
    normalized_event = normalize_incident_code(event_type)
    identity = {
        "event_type": normalized_event,
        "incident_ids": [str(item.incident_id) for item in ordered],
        "window_end": _canonical_time(window_end),
        "window_start": _canonical_time(window_start),
    }
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    url = _admin_url(admin_base_url, "/operations?queue=incidents&status=OPEN")
    lines = tuple(
        (
            f"• *{_safe_text(item.hospital_name, 100)}*\n"
            f"  무슨 문제인지: {_safe_text(item.problem, 300)}\n"
            f"  고객 영향: {_safe_text(item.customer_impact, 300)}\n"
            f"  지금 할 일: {_safe_text(item.next_action, 300)}\n"
            f"  개발팀에 전달할 정보: `{_developer_reference(item)}`"
        )
        for item in ordered
    )
    chunks = _chunk_lines(lines)
    if len(chunks) > _MAX_BLOCKS - 3:
        raise NotificationPayloadError("SUMMARY_EXCEEDS_SLACK_LIMIT")
    blocks = (
        _block("header", "summary_header", {"type": "plain_text", "text": "운영 알림 요약"}),
        _block(
            "section",
            "summary_window",
            {
                "type": "mrkdwn",
                "text": (
                    f"집계 기간: {_canonical_time(window_start)} ~ {_canonical_time(window_end)}"
                ),
            },
        ),
        *(
            _block("section", f"summary_incidents_{index}", {"type": "mrkdwn", "text": chunk})
            for index, chunk in enumerate(chunks)
        ),
        _action_block("summary_action", url, "운영 센터에서 모아보기"),
    )
    return NotificationIntent(
        dedupe_key=f"INCIDENT_SUMMARY:{digest}",
        notification_type="INCIDENT_SUMMARY",
        message=_message(f"[운영 요약] {len(ordered)}건 확인 필요", blocks, url),
    )


def _message(
    fallback_text: str, blocks: tuple[dict[str, JSONValue], ...], admin_url: str
) -> SlackMessage:
    message = SlackMessage(fallback_text=fallback_text, blocks=blocks, admin_url=admin_url)
    validate_message(message, allowed_admin_base_url=admin_url)
    return message


def _chunk_lines(lines: Sequence[str]) -> tuple[str, ...]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _MAX_SECTION_CHARS and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def _block(kind: str, block_id: str, text: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {"type": kind, "block_id": block_id, "text": text}


def _action_block(block_id: str, url: str, label: str) -> dict[str, JSONValue]:
    return {
        "type": "actions",
        "block_id": block_id,
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": label},
            "url": url,
        }],
    }


def _admin_url(base_url: str, path: str) -> str:
    path_parts = urlsplit(path)
    allowed_root = any(
        path_parts.path == root or path_parts.path.startswith(f"{root}/")
        for root in ("/operations", "/hospitals", "/leads")
    )
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(segment == ".." for segment in path_parts.path.split("/"))
        or path_parts.scheme
        or path_parts.netloc
        or not allowed_root
    ):
        raise NotificationPayloadError("ADMIN_URL_INVALID")
    validate_admin_url(base_url)
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise NotificationPayloadError("SUMMARY_WINDOW_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_text(value: str, limit: int) -> str:
    cleaned = sanitize_operator_text(value, limit=limit) or "확인 필요"
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _operator_owner_label(value: str) -> str:
    cleaned = _safe_text(value, 100)
    if cleaned in {"미지정", "확인 필요", "담당자 미배정"}:
        return "미지정(담당자 지정 필요)"
    return cleaned


def _operator_deadline_label(value: str) -> str:
    cleaned = _safe_text(value, 100)
    if cleaned in {"확인 필요", "기한 미설정"}:
        return "운영 센터에서 확인"
    return cleaned


def _operator_severity_label(value: str) -> str:
    return {
        "LOW": "낮음",
        "MEDIUM": "보통",
        "HIGH": "높음",
        "CRITICAL": "긴급",
    }.get(value.upper(), "상세 확인 필요")


def _developer_reference(incident: IncidentSlackProjection) -> str:
    parts = [f"사건 {incident.incident_id}"]
    if incident.operation_run_id is not None:
        parts.append(f"작업 {incident.operation_run_id}")
    return " · ".join(parts)
