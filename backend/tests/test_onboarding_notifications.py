from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.models.operations import JSONValue, NotificationOutboxState
from app.services.onboarding_notifications import (
    SITE_BUILT_NOTIFICATION_TYPE,
    V0_READY_NOTIFICATION_TYPE,
    build_site_built_notification,
    build_v0_ready_notification,
    enqueue_onboarding_notification_sync,
)

_ADMIN = "https://admin.example.test"
_HOSPITAL_ID = uuid.UUID("b1400000-0000-0000-0000-000000000001")
_REPORT_ID = uuid.UUID("c1400000-0000-0000-0000-000000000001")


def _urls(value: JSONValue) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, nested in value.items()
            for item in ([nested] if key == "url" else _urls(nested))
            if isinstance(item, str)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _urls(nested)]
    return []


def test_v0_ready_intent_is_report_identified_safe_and_has_one_action() -> None:
    first = build_v0_ready_notification(
        hospital_id=_HOSPITAL_ID,
        hospital_name="장편한외과 doctor@example.com 010-1234-5678 gs://private/report.pdf",
        report_id=_REPORT_ID,
        sov_pct=12.345,
        platforms=["chatgpt", "gemini", "chatgpt"],
        admin_base_url=_ADMIN,
    )
    second = build_v0_ready_notification(
        hospital_id=_HOSPITAL_ID,
        hospital_name="병원명이 바뀌어도 리포트 사건은 동일",
        report_id=_REPORT_ID,
        sov_pct=99.0,
        platforms=["chatgpt"],
        admin_base_url=_ADMIN,
    )

    encoded = json.dumps(first.message.payload(), ensure_ascii=False)
    assert first.dedupe_key == second.dedupe_key == f"ONBOARDING_V0_READY:{_REPORT_ID}"
    assert first.notification_type == V0_READY_NOTIFICATION_TYPE
    assert _urls(first.message.payload()) == [f"{_ADMIN}/hospitals/{_HOSPITAL_ID}/reports"]
    assert "doctor@example.com" not in encoded
    assert "010-1234-5678" not in encoded
    assert "gs://private/report.pdf" not in encoded
    assert "12.3%" in encoded
    assert "ChatGPT · Gemini" in encoded
    assert all(
        label in encoded for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )


def test_site_built_intent_is_hospital_milestone_identified_and_has_one_action() -> None:
    first = build_site_built_notification(
        hospital_id=_HOSPITAL_ID,
        hospital_name="장편한외과",
        admin_base_url=_ADMIN,
    )
    second = build_site_built_notification(
        hospital_id=_HOSPITAL_ID,
        hospital_name="변경된 병원명",
        admin_base_url=_ADMIN,
    )

    assert first.dedupe_key == second.dedupe_key == f"ONBOARDING_SITE_BUILT:{_HOSPITAL_ID}:v1"
    assert first.notification_type == SITE_BUILT_NOTIFICATION_TYPE
    assert _urls(first.message.payload()) == [
        f"{_ADMIN}/hospitals/{_HOSPITAL_ID}/profile#domain-setup"
    ]


def test_sync_enqueue_adds_deduplicated_pending_outbox_without_committing() -> None:
    intent = build_site_built_notification(
        hospital_id=_HOSPITAL_ID,
        hospital_name="장편한외과",
    )
    now = datetime(2026, 8, 15, 2, 0, tzinfo=UTC)
    inserted = object()
    result = MagicMock()
    result.scalar_one_or_none.return_value = inserted
    db = MagicMock()
    db.execute.return_value = result

    row = enqueue_onboarding_notification_sync(db, intent, now=now)

    assert row is inserted
    db.commit.assert_not_called()
    statement = db.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    parameters = compiled.params
    assert "ON CONFLICT (dedupe_key) DO NOTHING" in str(compiled)
    assert parameters["dedupe_key"] == intent.dedupe_key
    assert parameters["notification_type"] == SITE_BUILT_NOTIFICATION_TYPE
    assert parameters["state"] == NotificationOutboxState.PENDING.value
    assert parameters["next_attempt_at"] == now


def test_v0_and_site_workers_commit_outbox_with_the_domain_milestone() -> None:
    source = (Path(__file__).parents[1] / "app/workers/tasks.py").read_text()
    v0_start = source.index("def trigger_v0_report")
    site_start = source.index("def build_aeo_site")
    nightly_start = source.index("def nightly_content_generation")
    v0_source = source[v0_start:site_start]
    site_source = source[site_start:nightly_start]

    v0_milestone = v0_source.index("hospital.v0_report_done = True")
    v0_outbox = v0_source.index("enqueue_onboarding_notification_sync", v0_milestone)
    v0_commit = v0_source.index("db.commit()", v0_outbox)
    site_milestone = site_source.index("hospital.site_built = True")
    site_outbox = site_source.index("enqueue_onboarding_notification_sync", site_milestone)
    site_commit = site_source.index("db.commit()", site_outbox)

    assert v0_milestone < v0_outbox < v0_commit
    assert site_milestone < site_outbox < site_commit
    assert "notify_v0_report_ready" not in v0_source
    assert "notify_site_built" not in site_source
