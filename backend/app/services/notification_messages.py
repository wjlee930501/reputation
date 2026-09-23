"""Pure Slack Block Kit projections and their deterministic identities."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin, urlsplit

from app.models.operations import JSONValue
from app.services.incident_safety import (
    current_admin_path,
    normalize_incident_code,
    sanitize_operator_text,
)
from app.services.incident_types import notification_channel_for_incident_type
from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationIntent,
    NotificationPayloadError,
    SlackMessage,
    validate_admin_url,
    validate_message,
)
from app.services.notification_copy import display_time, incident_copy, readable_detail
from app.services.notification_labels import prefixed_for_event

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
    copy = incident_copy(incident.incident_type)
    hospital_name = _safe_text(incident.hospital_name, 90)
    prefix = "복구" if recovered else "긴급" if incident.severity.upper() == "CRITICAL" else "조치 필요"
    subject = "이전 알림 해결" if recovered else copy.title
    title = f"[{prefix}] {hospital_name} · {subject}"
    url = _admin_url(admin_base_url, incident.admin_path)
    if recovered:
        problem = f"앞서 알린 ‘{copy.title}’ 문제의 정상 복구가 확인됐습니다."
        action = "추가 조치가 필요하지 않습니다. 같은 문제가 다시 발생하면 새로 알립니다."
        details = f"{problem}\n{action}"
        context = f"참조 {_developer_reference(incident)}"
    else:
        impact = readable_detail(incident.customer_impact, fallback="영향 범위는 연결된 화면에서 확인해 주세요.")
        problem = readable_detail(incident.problem, fallback=copy.title + " 상태입니다.")
        if incident.incident_type in {"CONTENT_GENERATION_FAILED", "ESSENCE_AUTO_REVIEW_ESCALATED"}:
            problem = copy.title + " 상태입니다. 상세 지적은 연결된 화면에 있습니다."
        action = copy.action
        if incident.incident_type == "DOMAIN_UNHEALTHY":
            action = readable_detail(incident.next_action, fallback=action, limit=240)
        details = f"{_safe_text(problem, 240)}\n{_safe_text(impact, 200)}\n\n*지금 할 일*\n{_safe_text(action, 300)}"
        labels = []
        owner = incident.owner_label.strip()
        if owner not in {"", "미지정", "확인 필요", "담당자 미배정", "담당 AE"}:
            labels.append("담당: " + _safe_text(owner, 60))
        else:
            role = "개발 담당자" if notification_channel_for_incident_type(incident.incident_type) == "SLACK_DEV" else "병원 운영 담당자"
            labels.append("담당: " + role)
        if incident.sla_label.strip() not in {"", "확인 필요", "기한 미설정", "기한 없음"}:
            labels.append("처리 기한 " + _safe_text(incident.sla_label, 80))
        labels.append("참조 " + _developer_reference(incident))
        context = " · ".join(labels)
    message = _message(
        prefixed_for_event(event, f"{title} | {problem} | {action}"),
        (
            _block("header", "header", {"type": "plain_text", "text": prefixed_for_event(event, title)}),
            _block("section", "incident_context", {"type": "mrkdwn", "text": details}),
            {"type": "context", "block_id": "incident_reference", "elements": [{"type": "plain_text", "text": context}]},
            _action_block("incident_action", url, "복구 기록 보기" if recovered else copy.button),
        ),
        url,
    )
    return NotificationIntent(
        dedupe_key=f"{event}:{incident.incident_id}:e{incident.episode_seq}",
        notification_type=event,
        message=message,
        channel=notification_channel_for_incident_type(incident.incident_type),
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
    channels = {notification_channel_for_incident_type(item.incident_type) for item in ordered}
    if len(channels) != 1:
        raise NotificationPayloadError("SUMMARY_AUDIENCE_CONFLICT")
    lines = tuple(
        f"• *{_safe_text(item.hospital_name, 90)}* — {incident_copy(item.incident_type).title}\n"
        f"  {_safe_text(incident_copy(item.incident_type).action, 240)} · 참조 {_developer_reference(item)}"
        for item in ordered
    )
    chunks = _chunk_lines(lines)
    if len(chunks) > _MAX_BLOCKS - 3:
        raise NotificationPayloadError("SUMMARY_EXCEEDS_SLACK_LIMIT")
    blocks = (
        _block("header", "summary_header", {"type": "plain_text", "text": prefixed_for_event("INCIDENT_SUMMARY", f"[조치 필요] 운영 이슈 {len(ordered)}건")}),
        _block(
            "section",
            "summary_window",
            {
                "type": "mrkdwn",
                "text": (
                    f"{display_time(window_start)} ~ {display_time(window_end)}"
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
        channel=next(iter(channels)),
        message=_message(
            prefixed_for_event("INCIDENT_SUMMARY", f"[조치 필요] 운영 이슈 {len(ordered)}건 | " + " / ".join(
                f"{_safe_text(item.hospital_name, 50)}: {incident_copy(item.incident_type).title}" for item in ordered[:5]
            ) + " | 운영센터에서 담당 항목을 확인해 주세요."),
            blocks,
            url,
        ),
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
    if kind == "header" and isinstance(text.get("text"), str):
        text = {**text, "text": unescape(text["text"])}
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
    # 옛 병원 화면 경로로 저장된 인시던트의 Slack 링크도 지금의 탭을 가리킨다.
    if not path_parts.query and not path_parts.fragment:
        path = current_admin_path(path)
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise NotificationPayloadError("SUMMARY_WINDOW_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_text(value: str, limit: int) -> str:
    cleaned = sanitize_operator_text(value, limit=limit) or "확인 필요"
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _developer_reference(incident: IncidentSlackProjection) -> str:
    identity = f"{incident.incident_id}:{incident.operation_run_id or ''}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12].upper()
    return f"OPS-{digest}"
