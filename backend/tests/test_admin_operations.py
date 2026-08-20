"""Tests for the v1.0 operations control plane.

Single-actor model: actor is sourced from settings.ADMIN_ACTOR_NAME, not from
client headers. Transaction order: OperationRun + audit → commit → apply_async.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import operations as operations_api
from app.models.audit import AdminAuditLog
from app.models.content import ContentStatus
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import DomainDnsStrategy, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.models.operations import OperationRun
from app.services import audit_log, operation_runs


class FakeTask:
    def __init__(self):
        self.calls = []
        self.headers = []

    def apply_async(self, *, args, queue, headers, task_id):
        self.calls.append({"args": args, "queue": queue})
        self.headers.append({**headers, "task_id": task_id})
        return SimpleNamespace(id=task_id)


class FakeDB:
    """Records ordering between add()/commit() so we can assert audit→commit→queue."""

    def __init__(
        self,
        hospital=None,
        content=None,
        *,
        handoff_state=HandoffState.HANDOFF_ACCEPTED,
    ):
        self.hospital = hospital
        self.content = content
        self.events = []  # ordered list of "add:<obj>" / "commit"
        self.handoff_state = handoff_state

    async def get(self, model, object_id):
        name = getattr(model, "__name__", "")
        if name == "Hospital":
            return self.hospital if self.hospital and self.hospital.id == object_id else None
        if name == "ContentItem":
            return self.content if self.content and self.content.id == object_id else None
        return None

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is HospitalHandoff:
            return self.handoff_state
        if entity is HospitalServiceInterval:
            return None
        if entity is OperationRun:
            return next(
                (item for item in self.added if isinstance(item, OperationRun)),
                None,
            )
        return None

    def add(self, item):
        self.events.append(("add", item))

    async def commit(self):
        self.events.append(("commit", None))

    async def execute(self, _statement):
        if not getattr(_statement, "is_update", False):
            return SimpleNamespace(scalar_one_or_none=lambda: uuid.uuid4())
        run = next(item for item in self.added if isinstance(item, OperationRun))
        run.state = "QUEUED"
        run.queued_at = datetime.now(timezone.utc)
        run.version += 1
        return SimpleNamespace(scalar_one_or_none=lambda: run)

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
        domain_dns_strategy=DomainDnsStrategy.CNAME,
        profile_complete=True,
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

    def record_apply(*, args, queue, headers, task_id):
        apply_calls_when_invoked.append(("apply", db.events.copy()))
        task.apply_async(args=args, queue=queue, headers=headers, task_id=task_id)

    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", record_apply)
    async def active_variant(_db, _hospital_id):
        return True
    monkeypatch.setattr(operations_api, "_has_active_query_variant", active_variant)
    monkeypatch.setattr(audit_log.settings, "ADMIN_ACTOR_NAME", "AE-test")

    response = await operations_api.run_sov_operation(hospital.id, db=db)

    assert response["detail"] == "AI 언급률 측정이 큐에 등록되었습니다."
    assert response["hospital_id"] == str(hospital.id)
    assert response["operation_state"] == "QUEUED"
    assert response["idempotent_replay"] is False
    assert task.calls == [{"args": [str(hospital.id)], "queue": "sov"}]
    assert task.headers == [
        {
            "operation_run_id": response["operation_run_id"],
            "task_id": response["task_id"],
        }
    ]
    # audit-row added, commit ran, then apply_async fired (in that order)
    assert apply_calls_when_invoked, "apply_async should have been called"
    events_at_apply = apply_calls_when_invoked[0][1]
    assert isinstance(events_at_apply[0][1], OperationRun)
    assert isinstance(events_at_apply[1][1], AdminAuditLog)
    assert events_at_apply[2] == ("commit", None)
    audit_rows = [row for row in db.added if isinstance(row, AdminAuditLog)]
    assert [row.action for row in audit_rows] == ["run_sov_requested", "run_sov"]
    assert audit_rows[0].detail["queued"] is False
    assert audit_rows[1].detail["queued"] is True
    assert audit_rows[0].actor == "AE-test"


async def test_queue_failure_never_records_queued_true(monkeypatch):
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)

    def fail_dispatch(*, args, queue, headers, task_id):
        del args, queue, headers, task_id
        raise ConnectionError("broker unavailable")

    incident_requests = []

    async def record_incident(_db, request, **_kwargs):
        incident_requests.append(request)
        return SimpleNamespace()

    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", fail_dispatch)
    monkeypatch.setattr(operation_runs, "open_or_touch_incident", record_incident)

    with pytest.raises(HTTPException) as exc:
        await operations_api.run_sov_operation(hospital.id, db=db)

    assert exc.value.status_code == 503
    audit_rows = [row for row in db.added if isinstance(row, AdminAuditLog)]
    assert [row.action for row in audit_rows] == [
        "run_sov_requested",
        "run_sov_queue_failed",
    ]
    assert all(row.detail["queued"] is False for row in audit_rows)
    assert audit_rows[1].detail["error_code"] == "BROKER_UNAVAILABLE"
    run = next(row for row in db.added if isinstance(row, OperationRun))
    assert run.state == "FAILED"
    assert exc.value.detail["operation_run_id"] == str(run.id)
    assert [request.operation_run_id for request in incident_requests] == [run.id]


async def test_idempotency_header_replays_same_operation_run(monkeypatch):
    # Given: the same Admin command and client idempotency key
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)
    task = FakeTask()

    async def active_variant(_db, _hospital_id):
        return True

    monkeypatch.setattr(operations_api, "_has_active_query_variant", active_variant)
    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", task.apply_async)

    # When: the route receives a browser retry with the same key
    first = await operations_api.run_sov_operation(
        hospital.id,
        db=db,
        idempotency_key="OPS-QA-API-REPLAY",
    )
    second = await operations_api.run_sov_operation(
        hospital.id,
        db=db,
        idempotency_key="OPS-QA-API-REPLAY",
    )

    # Then: both responses identify one run and only one broker dispatch exists
    assert second["operation_run_id"] == first["operation_run_id"]
    assert second["idempotent_replay"] is True
    assert task.calls == [{"args": [str(hospital.id)], "queue": "sov"}]


async def test_run_sov_operation_rejects_onboarding_hospital():
    hospital = _hospital(status=HospitalStatus.ONBOARDING)
    db = FakeDB(hospital=hospital)

    with pytest.raises(HTTPException) as exc:
        await operations_api.run_sov_operation(hospital.id, db=db)

    assert exc.value.status_code == 409


async def test_run_sov_operation_rejects_when_no_active_target_variant(monkeypatch):
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)
    task = FakeTask()

    async def no_active_variant(_db, _hospital_id):
        return False

    monkeypatch.setattr(operations_api, "_has_active_query_variant", no_active_variant)
    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", task.apply_async)

    with pytest.raises(HTTPException) as exc:
        await operations_api.run_sov_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert "활성 문구" in exc.value.detail
    assert task.calls == []
    assert db.added == []


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


async def test_regenerate_content_image_operation_queues_without_replacing_text(monkeypatch):
    hospital = _hospital()
    content = _content(
        hospital.id,
        title="검수 중인 제목",
        body="검수 중인 본문",
        image_url=None,
    )
    db = FakeDB(hospital=hospital, content=content)
    task = FakeTask()
    monkeypatch.setattr(operations_api.generate_content_image, "apply_async", task.apply_async)

    response = await operations_api.regenerate_content_image_operation(
        hospital.id,
        content.id,
        db=db,
    )

    assert response["detail"] == "Content image generation queued"
    assert task.calls == [{"args": [str(content.id)], "queue": "content"}]
    assert db.committed is True
    assert [row.action for row in db.added if isinstance(row, AdminAuditLog)] == [
        "regenerate_content_image_requested",
        "regenerate_content_image",
    ]
    assert content.title == "검수 중인 제목"
    assert content.body == "검수 중인 본문"


async def test_owner_force_release_is_versioned_audited_and_idempotent():
    claimed_at = datetime.now(timezone.utc)
    hospital = _hospital()
    content = _content(
        hospital.id,
        title="검수 중인 제목",
        body=None,
        generation_claimed_at=claimed_at,
    )

    class ForceReleaseDB(FakeDB):
        async def flush(self):
            return None

        async def rollback(self):
            return None

        async def execute(self, statement):
            if getattr(getattr(statement, "table", None), "name", None) == "content_items":
                return SimpleNamespace(scalar_one_or_none=lambda: content.id)
            return await super().execute(statement)

    db = ForceReleaseDB(hospital=hospital, content=content)
    actor = SimpleNamespace(id=uuid.uuid4(), email="owner@example.com", role="OWNER")
    payload = operations_api.GenerationClaimReleaseRequest(
        expected_claimed_at=claimed_at,
        reason="stale worker confirmed",
    )

    first = await operations_api.force_release_generation_claim(
        hospital.id,
        content.id,
        payload,
        idempotency_key="release-claim-001",
        db=db,
        actor=actor,
    )
    second = await operations_api.force_release_generation_claim(
        hospital.id,
        content.id,
        payload,
        idempotency_key="release-claim-001",
        db=db,
        actor=actor,
    )

    assert first["released"] is True
    assert first["operation_state"] == "SUCCEEDED"
    assert second["operation_run_id"] == first["operation_run_id"]
    assert second["idempotent_replay"] is True
    audits = [row for row in db.added if isinstance(row, AdminAuditLog)]
    assert len(audits) == 1
    assert audits[0].detail["reason"] == "stale worker confirmed"
    assert content.title == "검수 중인 제목"


async def test_verify_domain_operation_activates_when_cname_matches(monkeypatch):
    hospital = _hospital(schedule_set=True)
    db = FakeDB(hospital=hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert strategy == DomainDnsStrategy.CNAME
        return SimpleNamespace(
            verified=True,
            cname_value="target.motionlabs.io",
            address_values=[],
            expected_cname="target.motionlabs.io",
            expected_addresses=[],
            verification_method="cname",
        )

    monkeypatch.setattr(operations_api, "check_domain_dns", _fake_check_domain_dns)

    response = await operations_api.verify_domain_operation(hospital.id, db=db)

    assert response["verified"] is True
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    
    # New contract: verify_domain + optional provision_domain_certificate
    audit_rows = [item for item in db.added if hasattr(item, "action")]
    verify_audit = next((a for a in audit_rows if a.action == "verify_domain"), None)
    assert verify_audit is not None
    detail = verify_audit.detail
    assert detail["verified"] is True
    assert detail["new_status"] == HospitalStatus.ACTIVE.value
    assert detail["previous_status"] == HospitalStatus.PENDING_DOMAIN.value
    assert detail["previous_site_live"] is False


async def test_verify_domain_operation_blocks_live_without_readiness(monkeypatch):
    hospital = _hospital(v0_report_done=False, site_built=True, schedule_set=True)
    db = FakeDB(hospital=hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert strategy == DomainDnsStrategy.CNAME
        return SimpleNamespace(
            verified=True,
            cname_value="target.motionlabs.io",
            address_values=[],
            expected_cname="target.motionlabs.io",
            expected_addresses=[],
            verification_method="cname",
        )

    monkeypatch.setattr(operations_api, "check_domain_dns", _fake_check_domain_dns)

    with pytest.raises(HTTPException) as exc:
        await operations_api.verify_domain_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.added == []
    assert db.committed is False


async def test_verify_domain_operation_rejects_apex_when_cname_exists_even_if_address_matches(
    monkeypatch,
):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr",
        domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS,
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
    )
    db = FakeDB(hospital=hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert domain == "jangclinic.co.kr"
        assert strategy == DomainDnsStrategy.APEX_ADDRESS
        return SimpleNamespace(
            verified=False,
            cname_value="target.motionlabs.io",
            address_values=["34.117.10.20"],
            expected_cname="target.motionlabs.io",
            expected_addresses=["34.117.10.20"],
            verification_method=None,
        )

    monkeypatch.setattr(operations_api, "check_domain_dns", _fake_check_domain_dns)

    response = await operations_api.verify_domain_operation(hospital.id, db=db)

    # New contract: DNS fail does not change state, does not block further tries
    assert response["verified"] is False
    assert response["verification_method"] is None
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    # No state change = no audit
    assert all(not hasattr(item, "action") for item in db.added)
    assert db.committed is False


async def test_verify_domain_operation_accepts_apex_address_strategy(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr",
        domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS,
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
    )
    db = FakeDB(hospital=hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert domain == "jangclinic.co.kr"
        assert strategy == DomainDnsStrategy.APEX_ADDRESS
        return SimpleNamespace(
            verified=True,
            cname_value=None,
            address_values=["34.117.10.20"],
            expected_cname="target.motionlabs.io",
            expected_addresses=["34.117.10.20"],
            verification_method="address",
        )

    monkeypatch.setattr(operations_api, "check_domain_dns", _fake_check_domain_dns)

    response = await operations_api.verify_domain_operation(hospital.id, db=db)

    assert response["verified"] is True
    assert response["verification_method"] == "address"
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True
    
    # Audit log for activation
    audit_rows = [item for item in db.added if hasattr(item, "action")]
    verify_audit = next((a for a in audit_rows if a.action == "verify_domain"), None)
    assert verify_audit is not None


@pytest.mark.parametrize(
    ("missing_key", "overrides", "handoff_state"),
    [
        ("profile_complete", {"profile_complete": False}, HandoffState.HANDOFF_ACCEPTED),
        ("v0_report_done", {"v0_report_done": False}, HandoffState.HANDOFF_ACCEPTED),
        ("site_built", {"site_built": False}, HandoffState.HANDOFF_ACCEPTED),
    ],
)
async def test_operations_verify_blocks_each_authoritative_gate(
    monkeypatch, missing_key, overrides, handoff_state
):
    hospital = _hospital(**overrides)
    db = FakeDB(hospital=hospital, handoff_state=handoff_state)

    async def _verified_dns(domain, strategy=DomainDnsStrategy.CNAME):
        return SimpleNamespace(
            verified=True,
            cname_value="target.motionlabs.io",
            address_values=[],
            expected_cname="target.motionlabs.io",
            expected_addresses=[],
            verification_method="cname",
        )

    monkeypatch.setattr(operations_api, "check_domain_dns", _verified_dns)

    with pytest.raises(HTTPException) as exc:
        await operations_api.verify_domain_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["missing"] == [missing_key]
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert hospital.site_live is False
    assert not any(isinstance(item, HospitalServiceInterval) for item in db.added)


async def test_actor_uses_admin_actor_name_setting(monkeypatch):
    """X-Admin-Actor 헤더는 무시되고 ENV ADMIN_ACTOR_NAME만 신뢰됨."""
    monkeypatch.setattr(audit_log.settings, "ADMIN_ACTOR_NAME", "Operator-A")
    hospital = _hospital(status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital=hospital)
    monkeypatch.setattr(operations_api.run_sov_for_hospital, "apply_async", lambda **_: None)
    async def active_variant(_db, _hospital_id):
        return True
    monkeypatch.setattr(operations_api, "_has_active_query_variant", active_variant)

    await operations_api.run_sov_operation(hospital.id, db=db)

    audit_row = next(row for row in db.added if isinstance(row, AdminAuditLog))
    assert audit_row.actor == "Operator-A"


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


async def test_trigger_v0_rejects_a_hospital_that_already_has_one(monkeypatch):
    """이미 V0가 있으면 태스크가 조용히 return한다 — 큐에 넣으면 화면만 성공한다.

    AE는 '등록했습니다'를 보고 리포트를 기다리는데 아무것도 생기지 않는다.
    큐에 넣기 전에 거절해 사유를 알려주는 것이 옳다.
    """
    hospital = _hospital(v0_report_done=True)
    db = FakeDB(hospital=hospital)
    queued = []

    monkeypatch.setattr(
        operations_api.trigger_v0_report,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc:
        await operations_api.trigger_v0_report_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert queued == [], "거절된 요청은 큐에 들어가면 안 된다"
    assert not db.committed, "거절된 요청은 감사 로그도 남기지 않는다"


async def test_trigger_v0_still_works_for_a_failed_or_pending_report(monkeypatch):
    """버튼의 본래 용도 — V0가 실패해 v0_report_done이 False로 남은 병원의 복구."""
    hospital = _hospital(v0_report_done=False)
    db = FakeDB(hospital=hospital)
    task = FakeTask()

    monkeypatch.setattr(operations_api.trigger_v0_report, "apply_async", task.apply_async)
    monkeypatch.setattr(audit_log.settings, "ADMIN_ACTOR_NAME", "AE-test")

    response = await operations_api.trigger_v0_report_operation(hospital.id, db=db)

    assert response["hospital_id"] == str(hospital.id)
    assert task.calls == [{"args": [str(hospital.id)], "queue": "reports"}]


async def test_trigger_v0_rejects_analyzing_in_progress(monkeypatch):
    """살아 있는 ANALYZING 클레임은 큐에 넣지 않고 409로 진행 중임을 알린다."""
    hospital = _hospital(v0_report_done=False, status=HospitalStatus.ANALYZING)
    db = FakeDB(hospital=hospital)
    queued = []
    active = SimpleNamespace(id=uuid.uuid4(), state=SimpleNamespace(value="RUNNING"))

    async def _alive(_db, _hospital_id):
        return True

    async def _active(_db, _hospital_id):
        return active

    monkeypatch.setattr(operations_api, "v0_claim_is_alive", _alive)
    monkeypatch.setattr(operations_api, "latest_active_v0_run", _active)
    monkeypatch.setattr(
        operations_api.trigger_v0_report,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc:
        await operations_api.trigger_v0_report_operation(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert "이미 초기 진단을 만들고 있습니다" in exc.value.detail["message"]
    assert exc.value.detail["operation_run_id"] == str(active.id)
    assert queued == []
