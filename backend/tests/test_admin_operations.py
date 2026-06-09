"""Tests for the v1.0 operations control plane.

Single-actor model: actor is sourced from settings.ADMIN_ACTOR_NAME, not from
client headers. Transaction order: write_audit_log → commit → apply_async.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import operations as operations_api
from app.models.content import ContentStatus
from app.models.hospital import HospitalStatus


class FakeTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, *, args, queue):
        self.calls.append({"args": args, "queue": queue})


class FakeDB:
    """Records ordering between add()/commit() so we can assert audit→commit→queue."""

    def __init__(self, hospital=None, content=None):
        self.hospital = hospital
        self.content = content
        self.events = []   # ordered list of "add:<obj>" / "commit"

    async def get(self, model, object_id):
        name = getattr(model, "__name__", "")
        if name == "Hospital":
            return self.hospital if self.hospital and self.hospital.id == object_id else None
        if name == "ContentItem":
            return self.content if self.content and self.content.id == object_id else None
        return None

    def add(self, item):
        self.events.append(("add", item))

    async def commit(self):
        self.events.append(("commit", None))

    @property
    def added(self):
        return [item for kind, item in self.events if kind == "add"]

    @property
    def committed(self) -> bool:
        return any(kind == "commit" for kind, _ in self.events)


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        status=HospitalStatus.PENDING_DOMAIN,
        aeo_domain="clinic.example.com",
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        site_live=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _content(hospital_id, **overrides):
    base = dict(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        status=ContentStatus.DRAFT,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_run_sov_operation_queues_task_after_audit_commit(monkeypatch):
    """Audit row must be added and committed BEFORE apply_async is called.

    Otherwise a transient commit failure would leave the queue holding a task
    while the audit trail forgot it.
    """
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)
    task = FakeTask()
    apply_calls_when_invoked = []

    def record_apply(*, args, queue):
        apply_calls_when_invoked.append(("apply", db.events.copy()))
        task.apply_async(args=args, queue=queue)

    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", record_apply)
    monkeypatch.setattr(operations_api.settings, "ADMIN_ACTOR_NAME", "AE-test")

    response = await operations_api.run_sov_operation(hospital.id, db=db)

    assert response == {"detail": "AI 언급률 측정이 큐에 등록되었습니다.", "hospital_id": str(hospital.id)}
    assert task.calls == [{"args": [str(hospital.id)], "queue": "sov"}]
    # audit-row added, commit ran, then apply_async fired (in that order)
    assert apply_calls_when_invoked, "apply_async should have been called"
    events_at_apply = apply_calls_when_invoked[0][1]
    assert events_at_apply[0][0] == "add"
    assert events_at_apply[1] == ("commit", None)
    assert db.added[0].action == "run_sov"
    assert db.added[0].actor == "AE-test"


async def test_run_sov_operation_rejects_onboarding_hospital():
    hospital = _hospital(status=HospitalStatus.ONBOARDING)
    db = FakeDB(hospital=hospital)

    with pytest.raises(HTTPException) as exc:
        await operations_api.run_sov_operation(hospital.id, db=db)

    assert exc.value.status_code == 409


async def test_regenerate_content_operation_blocks_published(monkeypatch):
    hospital = _hospital()
    content = _content(hospital.id, status=ContentStatus.PUBLISHED)
    db = FakeDB(hospital=hospital, content=content)
    task = FakeTask()
    monkeypatch.setattr(operations_api.regenerate_content_item, "apply_async", task.apply_async)

    with pytest.raises(HTTPException) as exc:
        await operations_api.regenerate_content_operation(hospital.id, content.id, db=db)

    assert exc.value.status_code == 409
    assert task.calls == []


async def test_verify_domain_operation_activates_when_cname_matches(monkeypatch):
    hospital = _hospital(schedule_set=True)
    db = FakeDB(hospital=hospital)
    monkeypatch.setattr(operations_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    async def _fake_resolve_cname(domain):
        return "target.motionlabs.io"

    monkeypatch.setattr(operations_api, "_resolve_cname", _fake_resolve_cname)

    response = await operations_api.verify_domain_operation(hospital.id, db=db)

    assert response["verified"] is True
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.added[0].action == "verify_domain"
    detail = db.added[0].detail
    assert detail["verified"] is True
    assert detail["new_status"] == HospitalStatus.ACTIVE.value
    assert detail["previous_status"] == HospitalStatus.PENDING_DOMAIN.value
    assert detail["previous_site_live"] is False


async def test_verify_domain_operation_blocks_live_without_readiness(monkeypatch):
    hospital = _hospital(v0_report_done=False, site_built=True, schedule_set=True)
    db = FakeDB(hospital=hospital)
    monkeypatch.setattr(operations_api.settings, "CNAME_TARGET", "target.motionlabs.io")
    async def _fake_resolve_cname(domain):
        return "target.motionlabs.io"

    monkeypatch.setattr(operations_api, "_resolve_cname", _fake_resolve_cname)

    with pytest.raises(HTTPException) as exc:
        await operations_api.verify_domain_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.added == []
    assert db.committed is False


async def test_actor_uses_admin_actor_name_setting(monkeypatch):
    """X-Admin-Actor 헤더는 무시되고 ENV ADMIN_ACTOR_NAME만 신뢰됨."""
    monkeypatch.setattr(operations_api.settings, "ADMIN_ACTOR_NAME", "Operator-A")
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)
    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", lambda **_: None)

    await operations_api.run_sov_operation(hospital.id, db=db)

    assert db.added[0].actor == "Operator-A"


def test_serialize_audit_log():
    created_at = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    log = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        actor="AE",
        action="run_sov",
        target_type="hospital",
        target_id="target",
        detail={"queued": True},
        created_at=created_at,
    )

    serialized = operations_api._serialize_audit_log(log)

    assert serialized["action"] == "run_sov"
    assert serialized["created_at"] == created_at.isoformat()
