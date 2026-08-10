"""Slack-safe rendering primitives for milestone notifications."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urljoin, urlsplit

from app.models.operations import JSONValue
from app.services.incident_safety import sanitize_operator_text
from app.services.notification_contracts import (
    NotificationPayloadError,
    SlackMessage,
    validate_admin_url,
    validate_message,
)

MAX_BLOCKS: Final = 50
MAX_SECTION_CHARS: Final = 2900
MAX_SUMMARY_ITEMS: Final = 15
_STABLE_ID: Final = re.compile(r"[A-Za-z0-9:._-]{1,180}")
_STORAGE_PATH: Final = re.compile(r"(?i)(?:gs|s3|file)://\S+|(?:/tmp|/var/tmp|/Users|/home)/\S+")


@dataclass(frozen=True, slots=True)
class RenderedSlackMessage:
    fallback_text: str
    blocks: tuple[dict[str, JSONValue], ...]
    admin_url: str


def validated_message(rendered: RenderedSlackMessage, admin_base_url: str) -> SlackMessage:
    message = SlackMessage(
        fallback_text=rendered.fallback_text,
        blocks=rendered.blocks,
        admin_url=rendered.admin_url,
    )
    validate_message(message, allowed_admin_base_url=admin_base_url)
    return message


def header_block(block_id: str, text: str) -> dict[str, JSONValue]:
    return {
        "type": "header",
        "block_id": block_id,
        "text": {"type": "plain_text", "text": text},
    }


def section_block(block_id: str, text: str) -> dict[str, JSONValue]:
    return {
        "type": "section",
        "block_id": block_id,
        "text": {"type": "mrkdwn", "text": text},
    }


def action_block(block_id: str, url: str, label: str) -> dict[str, JSONValue]:
    return {
        "type": "actions",
        "block_id": block_id,
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": safe_text(label, 75)},
                "url": url,
            }
        ],
    }


def chunk_lines(lines: Sequence[str]) -> tuple[str, ...]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_SECTION_CHARS and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def admin_url(base_url: str, path: str) -> str:
    validate_admin_url(base_url)
    parts = urlsplit(path)
    allowed = any(
        parts.path == root or parts.path.startswith(f"{root}/")
        for root in ("/operations", "/hospitals")
    )
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parts.scheme
        or parts.netloc
        or "\\" in path
        or any(segment == ".." for segment in parts.path.split("/"))
        or not allowed
    ):
        raise NotificationPayloadError("MILESTONE_ADMIN_PATH_INVALID")
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise NotificationPayloadError("MILESTONE_WINDOW_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def operator_deadline(value: datetime | None) -> str:
    if value is None:
        return "기한 없음"
    zone = value.tzname()
    suffix = f" ({zone})" if zone else ""
    return f"{value.year}년 {value.month}월 {value.day}일 {value.hour:02d}:{value.minute:02d}{suffix}"


def validate_stable_id(value: str) -> None:
    if _STABLE_ID.fullmatch(value) is None:
        raise NotificationPayloadError("MILESTONE_STABLE_ID_INVALID")


def safe_text(value: str, limit: int) -> str:
    without_paths = _STORAGE_PATH.sub("[storage path redacted]", value)
    cleaned = sanitize_operator_text(without_paths, limit=limit) or "확인 필요"
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
