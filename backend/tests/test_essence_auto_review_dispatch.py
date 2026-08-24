import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.operations import Incident, IncidentState
from app.services import ops_incident_alerts
from app.services.essence_auto_review import EssenceRefreshResult, EssenceRefreshStatus
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.workers import tasks
from app.workers.dispatch_envelope import expected_purpose, expected_target


class _EssenceTaskDB:
    def __init__(self, hospital):
        self.hospital = hospital

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args):
        return self.hospital


def _run_essence_task(monkeypatch, result: EssenceRefreshResult):
    hospital = SimpleNamespace(id=result.hospital_id, name="에센스테스트의원", slug="essence-test")
    opened: list[dict] = []
    recovered: list[dict] = []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    async def fake_recover_exact(**_kwargs):
        return False

    async def fake_recover_hospital(**kwargs):
        recovered.append(kwargs)
        return 1

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _EssenceTaskDB(hospital))
    monkeypatch.setattr(tasks, "refresh_essence_snapshot", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(tasks, "recover_ops_incident", fake_recover_exact)
    monkeypatch.setattr(
        tasks, "recover_ops_incidents_for_hospital", fake_recover_hospital
    )

    response = tasks.auto_review_essence_snapshot.run(str(result.hospital_id))
    return response, opened, recovered


def test_essence_review_dispatch_is_purpose_and_hospital_bound() -> None:
    hospital_id = str(uuid.uuid4())

    assert (
        expected_purpose("app.workers.tasks.auto_review_essence_snapshot")
        == "auto-review-essence-snapshot"
    )
    assert (
        expected_target("app.workers.tasks.auto_review_essence_snapshot", [hospital_id])
        == hospital_id
    )
    assert (
        expected_purpose("app.workers.tasks.reconcile_essence_snapshots")
        == "reconcile-essence-snapshots"
    )


def test_reconcile_offset_rotates_across_every_page() -> None:
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(0, timezone.utc)) == 0
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(900, timezone.utc)) == 200
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(1800, timezone.utc)) == 400
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(2700, timezone.utc)) == 0


def test_essence_reviewer_cost_guard_blocks_before_provider(monkeypatch) -> None:
    async def blocked(*_args, **_kwargs):
        return type("Decision", (), {"allowed": False, "reason": "limit"})()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("review provider must not run")

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", blocked)
    monkeypatch.setattr(tasks, "review_essence_candidate", unexpected)

    with pytest.raises(tasks._EssenceReviewCostBlocked, match="limit"):
        tasks._cost_guarded_essence_review(object(), object(), {}, [])


def test_escalated_snapshot_with_approved_essence_recovers_without_opening(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=uuid.uuid4(),
        findings=("new source conflicts with approved essence",),
    )

    response, opened, recovered = _run_essence_task(monkeypatch, result)

    assert response["status"] == "ESCALATED"
    assert opened == []
    assert len(recovered) == 1
    assert recovered[0]["hospital_id"] == hospital_id
    assert recovered[0]["pipeline"] == "essence_auto_review"
    assert recovered[0]["incident_type"] == "ESSENCE_AUTO_REVIEW_ESCALATED"
    assert recovered[0]["notify"] is False


def test_escalated_snapshot_without_approved_essence_opens_incident(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=None,
        findings=("operator review required before generation",),
    )

    _, opened, recovered = _run_essence_task(monkeypatch, result)

    assert len(opened) == 1
    assert opened[0]["incident_type"] == "ESSENCE_AUTO_REVIEW_ESCALATED"
    assert opened[0]["object_id"] == f"{hospital_id}:current-snapshot"
    assert recovered == []


@pytest.mark.parametrize(
    "status", (EssenceRefreshStatus.AUTO_APPROVED, EssenceRefreshStatus.UP_TO_DATE)
)
def test_healthy_essence_status_recovers_hospital_escalations(monkeypatch, status) -> None:
    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=status,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        philosophy_id=uuid.uuid4(),
        previous_philosophy_id=uuid.uuid4(),
    )

    _, opened, recovered = _run_essence_task(monkeypatch, result)

    assert opened == []
    assert len(recovered) == 1
    assert recovered[0]["hospital_id"] == hospital_id


class _IncidentResult:
    def __init__(self, incidents):
        self.incidents = incidents

    def scalars(self):
        return self

    def all(self):
        return self.incidents


class _IncidentRecoveryDB:
    def __init__(self, incidents):
        self.incidents = incidents
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        return _IncidentResult(self.incidents)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_hospital_recovery_ignores_changed_snapshot_hash(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    old_object_id = f"{hospital_id}:old-snapshot-hash"
    incident = Incident(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        dedupe_key=build_incident_key(
            "essence_auto_review",
            "essence_snapshot",
            old_object_id,
            IncidentFingerprint.VALIDATION_FAILED,
        ),
        incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
        state=IncidentState.OPEN.value,
        severity="HIGH",
        customer_impact="draft awaits review",
        source_type="ESSENCE_AUTO_REVIEW",
        source_id=old_object_id,
        next_action="review draft",
        admin_path=f"/hospitals/{hospital_id}/essence",
        version=1,
        episode_seq=1,
    )
    db = _IncidentRecoveryDB([incident])
    transitions: list[tuple[str, int]] = []

    async def fake_retrying(_db, _incident_id, *, expected_version, **_kwargs):
        transitions.append(("retrying", expected_version))
        incident.state = IncidentState.RETRYING.value
        incident.version += 1
        return incident

    async def fake_recovered(_db, _incident_id, *, expected_version, **_kwargs):
        transitions.append(("recovered", expected_version))
        incident.state = IncidentState.RECOVERED.value
        incident.version += 1
        return incident

    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(ops_incident_alerts, "mark_retrying", fake_retrying)
    monkeypatch.setattr(ops_incident_alerts, "mark_recovered", fake_recovered)

    recovered = await ops_incident_alerts.recover_ops_incidents_for_hospital(
        hospital_id=hospital_id,
        pipeline="essence_auto_review",
        incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
        notify=False,
    )

    assert recovered == 1
    assert transitions == [("retrying", 1), ("recovered", 2)]
    assert incident.state == IncidentState.RECOVERED.value
    assert db.committed is True
