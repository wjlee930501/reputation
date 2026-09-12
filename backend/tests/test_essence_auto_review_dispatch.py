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


def _run_essence_task(monkeypatch, result: EssenceRefreshResult, *, hospital_status=None):
    hospital = SimpleNamespace(
        id=result.hospital_id,
        name="에센스테스트의원",
        slug="essence-test",
        status=hospital_status,
    )
    opened: list[dict] = []
    recovered: list[dict] = []
    deferred: list[dict] = []

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
    monkeypatch.setattr(
        tasks,
        "_defer_essence_escalation_to_automatic_retry",
        lambda **kwargs: deferred.append(kwargs) or True,
    )

    response = tasks.auto_review_essence_snapshot.run(str(result.hospital_id))
    return response, opened, recovered, deferred


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
    """Non-ACTIVE hospitals (no live content generation yet) still recover silently."""

    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=uuid.uuid4(),
        findings=("new source conflicts with approved essence",),
    )

    response, opened, recovered, _deferred = _run_essence_task(
        monkeypatch, result, hospital_status=tasks.HospitalStatus.PENDING_DOMAIN
    )

    assert response["status"] == "ESCALATED"
    assert opened == []
    assert len(recovered) == 1
    assert recovered[0]["hospital_id"] == hospital_id
    assert recovered[0]["pipeline"] == "essence_auto_review"
    assert recovered[0]["incident_type"] == "ESSENCE_AUTO_REVIEW_ESCALATED"
    assert recovered[0]["notify"] is False


def test_escalated_snapshot_with_base_is_absorbed_without_slack_for_active_hospital(
    monkeypatch,
) -> None:
    """An unresolved active-hospital refresh opens one visible incident."""

    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=uuid.uuid4(),
        findings=("new source conflicts with approved essence",),
    )

    response, opened, recovered, _deferred = _run_essence_task(
        monkeypatch, result, hospital_status=tasks.HospitalStatus.ACTIVE
    )

    assert response["status"] == "ESCALATED"
    assert opened == []
    assert len(recovered) == 1
    assert recovered[0]["hospital_id"] == hospital_id
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

    _, opened, recovered, _deferred = _run_essence_task(monkeypatch, result)

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

    _, opened, recovered, _deferred = _run_essence_task(monkeypatch, result)

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

    async def fake_auto_acknowledged(_db, _incident_id, *, expected_version, **_kwargs):
        transitions.append(("acknowledged", expected_version))
        incident.state = IncidentState.ACKNOWLEDGED.value
        incident.version += 1
        return incident

    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(ops_incident_alerts, "mark_retrying", fake_retrying)
    monkeypatch.setattr(ops_incident_alerts, "mark_recovered", fake_recovered)
    monkeypatch.setattr(
        ops_incident_alerts, "auto_acknowledge_incident", fake_auto_acknowledged
    )

    recovered = await ops_incident_alerts.recover_ops_incidents_for_hospital(
        hospital_id=hospital_id,
        pipeline="essence_auto_review",
        incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
        notify=False,
    )

    assert recovered == 1
    # The machine closes what the machine recovered — no human "확인 완료" click.
    assert transitions == [("retrying", 1), ("recovered", 2), ("acknowledged", 3)]
    assert incident.state == IncidentState.ACKNOWLEDGED.value
    assert db.committed is True


def test_escalation_with_recovery_budget_left_is_not_operator_work(monkeypatch) -> None:
    """자동 재검수가 아직 소유한 보류는 인시던트를 RETRYING으로 둔다(사람의 큐에 없다)."""

    hospital_id = uuid.uuid4()
    retry_at = datetime(2026, 9, 13, tzinfo=timezone.utc)
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=None,
        findings=("독립 검수가 자동 승인을 보류했습니다.",),
        automatic_recovery_cycle=1,
        next_automatic_attempt_at=retry_at,
    )

    _, opened, _recovered, deferred = _run_essence_task(monkeypatch, result)

    # 시도마다 새 인시던트를 열지 않는다 — 같은 병원·snapshot 한 건을 만지기만 한다.
    assert len(opened) == 1
    assert opened[0]["object_id"] == f"{hospital_id}:current-snapshot"
    assert deferred == [
        {"object_id": f"{hospital_id}:current-snapshot", "retry_due_at": retry_at}
    ]


def test_exhausted_escalation_becomes_operator_work(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=None,
        findings=("독립 검수가 자동 승인을 보류했습니다.",),
        automatic_recovery_cycle=4,
        next_automatic_attempt_at=None,
    )

    _, opened, _recovered, deferred = _run_essence_task(monkeypatch, result)

    assert len(opened) == 1
    assert deferred == []


def test_essence_incident_deep_links_point_at_an_existing_tab(monkeypatch) -> None:
    """옛 `/essence` 경로는 2026-10-09에 사라진다 — 딥링크는 현황 탭을 가리킨다."""

    hospital_id = uuid.uuid4()
    result = EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=hospital_id,
        snapshot_hash="current-snapshot",
        previous_philosophy_id=None,
        findings=("보류",),
    )

    _, opened, _recovered, _deferred = _run_essence_task(monkeypatch, result)

    assert opened[0]["admin_path"] == f"/hospitals/{hospital_id}"


def test_essence_provider_calls_spend_the_essence_budget(monkeypatch) -> None:
    """합성·검수가 야간 생성과 같은 `content` 예산을 쓰면 온보딩이 글을 굶긴다."""

    categories: list[str] = []

    async def record(category, *_args, **_kwargs):
        categories.append(category)
        return type("Decision", (), {"allowed": False, "reason": "limit"})()

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", record)

    with pytest.raises(tasks._EssenceReviewCostBlocked):
        tasks._cost_guarded_essence_synthesis(object(), [], [])
    with pytest.raises(tasks._EssenceReviewCostBlocked):
        tasks._cost_guarded_essence_review(object(), None, {}, [])

    assert categories == ["essence", "essence"]


@pytest.mark.parametrize("status", [
    EssenceRefreshStatus.WAITING_FOR_SOURCES,
    EssenceRefreshStatus.SNAPSHOT_CHANGED,
    EssenceRefreshStatus.UP_TO_DATE,
    EssenceRefreshStatus.AUTO_APPROVED,
    EssenceRefreshStatus.DEFERRED,
])
def test_system_owned_essence_refresh_never_opens_human_incident(monkeypatch, status):
    result = EssenceRefreshResult(status=status, hospital_id=uuid.uuid4())
    response, opened, _recovered, _deferred = _run_essence_task(monkeypatch, result)
    assert response["status"] == status.value
    assert opened == []
