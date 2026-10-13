import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.operations import Incident, IncidentSeverity, IncidentState
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
    retry_at = datetime.now(timezone.utc) + timedelta(hours=24)
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
    # 처음부터 RETRYING+다음 자동 재검수 시각으로 연다(OPEN→Slack→RETRYING 경합 없음).
    # 재검수는 15분 재조정이 집는다 — 그 주기와 여유만큼 늦춰 보류된 열림이 먼저 나가지 않게 한다.
    assert opened[0]["retry_until"] == retry_at + tasks.ESSENCE_ESCALATION_RETRY_MARGIN
    assert opened[0]["severity"] == IncidentSeverity.MEDIUM
    assert deferred == []


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
    assert opened[0]["retry_until"] is None
    assert opened[0]["severity"] == IncidentSeverity.HIGH
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


# ── 첫 일정 자동 생성 ──────────────────────────────────────────────────────


def test_auto_approval_creates_the_first_schedule_once(monkeypatch) -> None:
    calls: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        tasks,
        "_ensure_initial_schedule_safe",
        lambda hospital_id, *, trigger: calls.append((hospital_id, trigger)) or "CREATED",
    )
    result = EssenceRefreshResult(EssenceRefreshStatus.AUTO_APPROVED, uuid.uuid4())

    response, _opened, _recovered, _deferred = _run_essence_task(monkeypatch, result)

    assert calls == [(result.hospital_id, "essence_auto_approval")]
    assert response["initial_schedule"] == "CREATED"


@pytest.mark.parametrize(
    "status", [EssenceRefreshStatus.ESCALATED, EssenceRefreshStatus.UP_TO_DATE]
)
def test_other_outcomes_leave_scheduling_to_the_sweep(monkeypatch, status) -> None:
    calls: list = []
    monkeypatch.setattr(
        tasks, "_ensure_initial_schedule_safe", lambda *a, **k: calls.append(a) or "CREATED"
    )
    result = EssenceRefreshResult(status, uuid.uuid4(), previous_philosophy_id=uuid.uuid4())

    _run_essence_task(monkeypatch, result)

    assert calls == []


def test_schedule_failure_never_undoes_the_approval(monkeypatch) -> None:
    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _Boom())
    assert tasks._ensure_initial_schedule_safe(uuid.uuid4(), trigger="x") == "FAILED"


def test_sweep_offers_only_unscheduled_public_hospitals(monkeypatch) -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]
    statements: list = []

    class _IdsDB:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, stmt):
            statements.append(str(stmt))

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return list(ids)

            return _R()

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _IdsDB())
    outcomes = iter(["CREATED", "ESSENCE_NOT_CURRENT"])
    seen: list = []
    monkeypatch.setattr(
        tasks,
        "_ensure_initial_schedule_safe",
        lambda hospital_id, *, trigger: seen.append((hospital_id, trigger)) or next(outcomes),
    )

    assert tasks._ensure_initial_schedules() == 1
    assert seen == [(ids[0], "reconcile_sweep"), (ids[1], "reconcile_sweep")]
    sql = statements[0]
    assert "content_schedules" in sql and "schedule_set" in sql and "status" in sql


def _escalated(next_attempt_at):
    return EssenceRefreshResult(
        status=EssenceRefreshStatus.ESCALATED,
        hospital_id=uuid.uuid4(),
        snapshot_hash="s",
        previous_philosophy_id=None,
        next_automatic_attempt_at=next_attempt_at,
    )


def test_essence_retry_window_covers_the_reconcile_interval() -> None:
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    due = now + timedelta(hours=48)

    assert tasks.ESSENCE_ESCALATION_RETRY_MARGIN >= timedelta(minutes=30)
    assert tasks._essence_escalation_retry_until(_escalated(due), now) == (
        due + tasks.ESSENCE_ESCALATION_RETRY_MARGIN
    )
    # 예산 소진은 기한이 없다 — 곧바로 사람의 일이다.
    assert tasks._essence_escalation_retry_until(_escalated(None), now) is None


def test_essence_retry_due_now_marker_never_opens_immediately() -> None:
    """옛 초안·마지막 시도 시각 없음(`datetime.min`)은 '지금 바로' 재검수다 — 즉시 OPEN이 아니다."""

    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    marker = datetime.min.replace(tzinfo=timezone.utc)

    for due in (marker, now - timedelta(hours=2)):
        retry_until = tasks._essence_escalation_retry_until(_escalated(due), now)
        assert retry_until == now + tasks.ESSENCE_ESCALATION_RETRY_MARGIN
