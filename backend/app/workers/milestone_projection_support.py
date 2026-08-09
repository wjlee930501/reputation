"""Stable time and identity primitives for milestone projectors."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import MilestoneProjection

_WINDOW_MINUTES: Final = 15
_EVENT_NAMESPACE: Final = uuid.UUID("e1300000-0000-0000-0000-000000000013")


@dataclass(frozen=True, slots=True)
class ProjectionWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class MilestoneStateScan:
    milestones: tuple[MilestoneProjection, ...]
    states: dict[str, str]


def canonical_projection_window(now: datetime) -> ProjectionWindow:
    """Return the last completed quarter-hour, stable across Beat retries."""

    if now.tzinfo is None:
        raise NotificationPayloadError("MILESTONE_WINDOW_MUST_BE_TIMEZONE_AWARE")
    current = now.astimezone(UTC)
    end = current.replace(
        minute=(current.minute // _WINDOW_MINUTES) * _WINDOW_MINUTES,
        second=0,
        microsecond=0,
    )
    return ProjectionWindow(end - timedelta(minutes=_WINDOW_MINUTES), end)


def event_uuid(kind: str, source_id: uuid.UUID, occurred_at: datetime) -> uuid.UUID:
    return uuid.uuid5(_EVENT_NAMESPACE, f"{kind}:{source_id}:{occurred_at.isoformat()}")


def state_uuid(kind: str, source_id: uuid.UUID, fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(_EVENT_NAMESPACE, f"{kind}:{source_id}:{fingerprint}")


def in_window(value: datetime, window: ProjectionWindow) -> bool:
    return window.start <= value < window.end
