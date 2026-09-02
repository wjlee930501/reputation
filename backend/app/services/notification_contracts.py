"""Typed notification intent contract and Slack payload validation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import assert_never
from urllib.parse import urlsplit

from app.models.operations import JSONValue

_MAX_BLOCKS = 50


@dataclass(frozen=True, slots=True)
class NotificationPayloadError(ValueError):
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class SlackMessage:
    fallback_text: str
    blocks: tuple[dict[str, JSONValue], ...]
    admin_url: str

    def payload(self) -> dict[str, JSONValue]:
        return {"text": self.fallback_text, "blocks": list(self.blocks)}

    def payload_json(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class NotificationIntent:
    dedupe_key: str
    notification_type: str
    message: SlackMessage
    channel: str = "SLACK"
    hospital_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    operation_run_id: uuid.UUID | None = None
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class IncidentSlackProjection:
    incident_id: uuid.UUID
    hospital_name: str
    severity: str
    customer_impact: str
    next_action: str
    admin_path: str
    owner_label: str
    sla_label: str
    hospital_id: uuid.UUID | None = None
    operation_run_id: uuid.UUID | None = None
    version: int = 1
    problem: str = "자동 작업이 완료되지 않았습니다."
    episode_seq: int = 1
    # Registry key that decides which Slack channel carries this projection.
    # Required and keyword-only on purpose: an omitted type silently routed a
    # developer-only incident to the AE channel, and a positional 14th argument
    # is the kind of thing that goes missing again. Omission now fails at
    # construction.
    incident_type: str = field(kw_only=True)


def validate_message(message: SlackMessage, *, allowed_admin_base_url: str) -> None:
    if not message.fallback_text.strip() or not 1 <= len(message.blocks) <= _MAX_BLOCKS:
        raise NotificationPayloadError("INVALID_SLACK_MESSAGE")
    block_ids = [block.get("block_id") for block in message.blocks]
    if any(not isinstance(block_id, str) or not block_id for block_id in block_ids):
        raise NotificationPayloadError("SLACK_BLOCK_ID_REQUIRED")
    if len(block_ids) != len(set(block_ids)):
        raise NotificationPayloadError("SLACK_BLOCK_IDS_NOT_UNIQUE")
    urls = _collect_urls(list(message.blocks))
    if urls != [message.admin_url] or _origin(message.admin_url) != _origin(allowed_admin_base_url):
        raise NotificationPayloadError("SLACK_ADMIN_LINK_INVALID")


def validate_admin_url(url: str) -> None:
    _origin(url)


def _collect_urls(value: JSONValue) -> list[str]:
    match value:
        case dict() as mapping:
            return [
                nested
                for key, item in mapping.items()
                for nested in (
                    [item] if key == "url" and isinstance(item, str) else _collect_urls(item)
                )
            ]
        case list() as items:
            return [nested for item in items for nested in _collect_urls(item)]
        case str() | int() | float() | bool() | None:
            return []
        case unreachable:
            assert_never(unreachable)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise NotificationPayloadError("ADMIN_URL_INVALID")
    return parts.scheme, parts.hostname.lower(), parts.port
