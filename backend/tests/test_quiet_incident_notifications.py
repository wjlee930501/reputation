"""Slack에 올리지 않는 사고 종류 — 사후검수 지적은 콘텐츠 탭에만 남는다(2026-10-08 대표 지시).

사후검수 스윕이 하루 20편씩 공개 글을 보며 지적·교정 복구마다 Slack을 보내 24시간에 60건이 나갔다.
계약상 후행 검수는 운영자 큐로 올리지 않는 항목이라 Slack에도 보내지 않는다.
"""

import uuid

from sqlalchemy.dialects import postgresql

from app.services import notification_store
from app.services.incident_types import incident_is_quiet


def test_post_publish_review_flags_are_quiet_and_others_are_not():
    assert incident_is_quiet("POST_PUBLISH_REVIEW_FLAGGED") is True
    assert incident_is_quiet("post_publish_review_flagged") is True
    assert incident_is_quiet("CONTENT_GENERATION_FAILED") is False
    assert incident_is_quiet(None) is False


def test_claim_query_never_picks_quiet_incident_notifications():
    sql = str(
        notification_store.claimable_notifications_stmt(
            claimed_at=notification_store.datetime.now(notification_store.UTC), limit=10
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "POST_PUBLISH_REVIEW_FLAGGED" in sql
    assert "incidents" in sql


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _SyncDb:
    def __init__(self, incident_type):
        self.incident_type = incident_type
        self.executed = []

    def execute(self, statement):
        self.executed.append(statement)
        return _Result(self.incident_type)


def _intent(incident_id):
    from types import SimpleNamespace

    message = SimpleNamespace(payload=lambda: {}, fallback_text="x")
    return SimpleNamespace(
        incident_id=incident_id,
        hospital_id=None,
        operation_run_id=None,
        dedupe_key="k",
        notification_type="INCIDENT_OPEN",
        channel="SLACK",
        message=message,
        max_attempts=3,
    )


def test_sync_enqueue_skips_quiet_incident(monkeypatch):
    monkeypatch.setattr(notification_store, "validate_message", lambda *a, **k: None)
    db = _SyncDb("POST_PUBLISH_REVIEW_FLAGGED")

    notification_store.enqueue_notification_sync(db, _intent(uuid.uuid4()))

    # 사고 종류 조회 한 번뿐, outbox insert는 없다.
    assert len(db.executed) == 1


def test_sync_enqueue_keeps_other_incidents(monkeypatch):
    monkeypatch.setattr(notification_store, "validate_message", lambda *a, **k: None)
    db = _SyncDb("CONTENT_GENERATION_FAILED")

    notification_store.enqueue_notification_sync(db, _intent(uuid.uuid4()))

    assert len(db.executed) == 2
