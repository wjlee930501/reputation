from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from app.models.operations import JSONValue
from app.services.notification_milestone_messages import (
    build_milestone_action_notification,
    build_milestone_recovery_notification,
)
from app.services.onboarding_events import (
    OnboardingEvent,
    OnboardingEventType,
    project_onboarding_event,
)
from app.workers.milestone_event_tasks import (
    canonical_projection_window,
)

_ADMIN = "https://admin.example.test"
_NOW = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
OnboardingValue = str | uuid.UUID | datetime | OnboardingEventType | None


def _urls(value: JSONValue) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, nested in value.items()
            for item in ([nested] if key == "url" else _urls(nested))
            if isinstance(item, str)
        ]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _urls(nested)]
    return []


def _event(event_type: OnboardingEventType, **overrides: OnboardingValue) -> OnboardingEvent:
    values = {
        "event_id": uuid.UUID("a1300000-0000-0000-0000-000000000001"),
        "event_type": event_type,
        "hospital_id": uuid.UUID("b1300000-0000-0000-0000-000000000001"),
        "hospital_name": "장편한외과의원 doctor@example.com gs://private/report.pdf",
        "owner_label": "AE 010-1234-5678",
        "occurred_at": _NOW,
        "sla_due_at": _NOW - timedelta(hours=1),
        "recovered_from_event_id": None,
    }
    values.update(overrides)
    return OnboardingEvent(**values)


def test_handoff_overdue_action_is_stable_safe_and_admin_linked() -> None:
    # Given: one overdue handoff containing contact and storage-path material
    event = _event(OnboardingEventType.HANDOFF_OVERDUE)

    # When: the same persisted transition is projected twice
    first = project_onboarding_event(event)
    second = project_onboarding_event(event)
    intent = build_milestone_action_notification(first, _ADMIN)

    # Then: identity is stable and only one safe Admin action leaves Slack
    payload = intent.message.payload()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert first.stable_id == second.stable_id
    assert intent.dedupe_key.endswith(first.stable_id)
    assert _urls(payload) == [f"{_ADMIN}/operations"]
    assert len(intent.message.blocks) <= 50
    assert "doctor@example.com" not in encoded
    assert "010-1234-5678" not in encoded
    assert "gs://private/report.pdf" not in encoded
    assert "문제:" in encoded
    assert "고객 영향:" in encoded
    assert "지금 할 일:" in encoded
    assert "인수 처리 기한:" in encoded
    assert "SLA:" not in encoded
    assert all(
        label in intent.message.fallback_text for label in ("문제:", "고객 영향:", "지금 할 일:")
    )


def test_accepted_handoff_can_close_an_overdue_action_without_success_spam() -> None:
    # Given: an accepted transition explicitly linked to the overdue transition it closes
    overdue_id = uuid.UUID("a1300000-0000-0000-0000-000000000009")
    accepted = project_onboarding_event(
        _event(
            OnboardingEventType.HANDOFF_ACCEPTED,
            event_id=uuid.UUID("a1300000-0000-0000-0000-000000000010"),
            recovered_from_event_id=overdue_id,
            sla_due_at=None,
        )
    )

    # When: a recovery projection is built
    intent = build_milestone_recovery_notification(accepted, _ADMIN)

    # Then: it is recovery-only and remains idempotent by transition ID
    assert accepted.is_recovery is True
    assert accepted.requires_action is False
    assert str(overdue_id) in intent.message.payload_json()
    assert _urls(intent.message.payload()) == [f"{_ADMIN}/operations"]


def test_projector_window_is_the_same_completed_quarter_for_safe_reruns() -> None:
    # Given: two retries inside the same 15-minute beat interval
    first_now = datetime(2026, 8, 10, 2, 47, 3, tzinfo=UTC)
    retry_now = datetime(2026, 8, 10, 2, 59, 59, tzinfo=UTC)

    # When: the canonical projector window is derived
    first = canonical_projection_window(first_now)
    retry = canonical_projection_window(retry_now)

    # Then: both scan the same completed interval and therefore share dedupe identity
    assert first == retry
    assert first.start == datetime(2026, 8, 10, 2, 30, tzinfo=UTC)
    assert first.end == datetime(2026, 8, 10, 2, 45, tzinfo=UTC)


def test_milestone_projector_is_included_routed_and_scheduled_every_15_minutes() -> None:
    # Given/When: the production Celery declaration is loaded
    from app.core.celery_app import celery_app

    task_name = "app.workers.milestone_event_tasks.project_milestone_events"

    # Then: Beat can import and route the projector to a consumed queue
    assert "app.workers.milestone_event_tasks" in celery_app.conf.include
    assert celery_app.conf.task_routes[task_name]["queue"] == "default"
    entry = celery_app.conf.beat_schedule["project-milestone-events"]
    assert entry["task"] == task_name
    assert entry["schedule"].minute == {0, 15, 30, 45}
