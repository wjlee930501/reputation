"""사이트 준비(REBUILD_SITE) 실행의 사고 소유권 계약 (H-13).

자동 재실행은 자동 복구 sweep이 예산 아래에서 관리한다. 시도 하나하나가 사람의 할 일이
되면 안 되고(F1), 운영자가 실패한 실행을 다시 시도해 성공하면 sweep이 남긴 최종 차단이
닫혀야 한다(F3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from operation_run_signal_support import (
    SYNC_DATABASE_URL,
    RecordingTask,
    dispatch_test_run,
)
from operation_run_signal_support import (
    signal_store as _signal_store_fixture,  # noqa: F401
)
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.services import site_build_incidents
from app.services.dependency_incident_helpers import incident_projection
from app.services.incident_safety import (
    REBUILD_SITE_SWEEP_KEY_PREFIX,
    site_build_incident_key,
)
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.workers import autonomous_recovery, operation_run_signals, task_incident_control


def _sync_factory() -> sessionmaker[Session]:
    return sessionmaker(
        create_engine(SYNC_DATABASE_URL), expire_on_commit=False, class_=Session
    )


def _exhausted_incident(
    hospital_id: uuid.UUID, run_id: uuid.UUID, **overrides: object
) -> Incident:
    now = datetime.now(UTC)
    incident = Incident(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_run_id=run_id,
        dedupe_key=site_build_incident_key(hospital_id),
        incident_type="SITE_BUILD_RETRIES_EXHAUSTED",
        state="OPEN",
        severity="HIGH",
        customer_impact="병원 공개 페이지 준비가 끝나지 않아 공개가 미뤄지고 있습니다.",
        source_type="SITE_BUILD",
        source_id=str(hospital_id),
        safe_error_code="SITE_BUILD_RETRIES_EXHAUSTED",
        safe_error_message="사이트 준비 자동 재실행이 하루치 예산을 모두 사용했습니다.",
        next_action="병원 기본 정보와 공개 준비 오류를 확인하고 운영센터에서 다시 시도하세요.",
        admin_path=f"/hospitals/{hospital_id}",
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    for field, value in overrides.items():
        setattr(incident, field, value)
    return incident


def _cleanup(factory: sessionmaker[Session], incident_id: uuid.UUID) -> None:
    with factory() as db:
        db.execute(
            delete(NotificationOutbox).where(NotificationOutbox.incident_id == incident_id)
        )
        db.execute(delete(Incident).where(Incident.id == incident_id))
        db.commit()


@pytest.mark.asyncio
async def test_site_build_failure_stays_with_the_sweep_and_never_opens_a_generic_incident(
    signal_store,
    monkeypatch,
) -> None:
    """F1: sweep이 만든 자동 시도의 실패는 사람의 할 일이 아니다. run만 FAILED로 남는다.

    시도마다 generic 사고를 열면 예산(하루 3회)을 다 쓰기 전에 조치 요청이 세 번 생기고,
    뒤이은 자동 성공은 그중 자기 run의 사고 한 건만 닫는다.
    """
    factory, hospital_id = signal_store
    run = await dispatch_test_run(
        factory,
        hospital_id,
        RecordingTask(),
        f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-09-09:0",
    )
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    operation_run_signals.track_operation_prerun(task_id=run.task_id, task=celery_task)
    operation_run_signals.track_operation_failure(
        task_id=run.task_id, task=celery_task, exception=RuntimeError("build failed")
    )
    operation_run_signals.track_operation_postrun(
        task_id=run.task_id, task=celery_task, state="FAILURE"
    )

    assert task_incident_control.record_task_failure(celery_task, run.task_id) is False

    with sync_factory() as db:
        # run 종결은 이 반환값과 무관한 별도 신호 다리가 책임진다.
        stored = db.get(OperationRun, run.id)
        assert stored is not None
        assert stored.state == OperationRunState.FAILED.value
        assert stored.safe_error_code == "TASK_FAILED"
        assert db.scalar(select(Incident).where(Incident.operation_run_id == run.id)) is None
        assert (
            db.scalar(
                select(Incident).where(
                    Incident.dedupe_key == task_incident_control._incident_key(run.id)
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_operator_retry_success_recovers_the_exhausted_site_build_incident(
    signal_store,
    monkeypatch,
) -> None:
    """F3: 운영자 재시도의 성공이 병원 단위 최종 차단을 닫는다.

    아무도 닫지 않으면 이미 해결된 일이 사람의 할 일 목록에 영원히 남는다. OPEN 공지가
    Slack에 나갔으므로 그 짝인 RECOVERED도 따라간다.
    """
    factory, hospital_id = signal_store
    run = await dispatch_test_run(factory, hospital_id, RecordingTask(), "site-build-retry")
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    monkeypatch.setattr(
        task_incident_control, "_audit", lambda *_args, **_kwargs: None
    )
    incident = _exhausted_incident(hospital_id, run.id)
    with sync_factory() as db:
        db.add(incident)
        db.flush()
        enqueue_notification_sync(
            db,
            build_open_incident_notification(
                incident_projection(incident, "재시도 의원", run.id, "확인 필요"),
                settings.ADMIN_BASE_URL,
            ),
        )
        db.commit()
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    try:
        assert task_incident_control.record_task_success(celery_task, run.task_id) is True

        with sync_factory() as db:
            closed = db.get(Incident, incident.id)
            assert closed is not None
            assert closed.state == "ACKNOWLEDGED"
            assert closed.recovered_at is not None
            # 시스템이 닫았다는 표시 — 사람의 "확인 완료" 클릭을 만들지 않는다.
            assert closed.acknowledged_by_id is None
            notices = sorted(
                notice.notification_type
                for notice in db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == incident.id
                    )
                )
            )
            assert notices == ["INCIDENT_OPEN", "INCIDENT_RECOVERED"]

        # 닫힌 뒤의 같은 성공은 아무것도 바꾸지 않는다.
        assert task_incident_control.record_task_success(celery_task, run.task_id) is False
    finally:
        _cleanup(sync_factory, incident.id)


@pytest.mark.asyncio
async def test_site_build_recovery_stays_silent_when_no_open_notice_reached_slack(
    signal_store,
    monkeypatch,
) -> None:
    """F3: OPEN 공지가 나간 적 없으면 복구도 Slack이 아니라 DB에만 남는다."""
    factory, hospital_id = signal_store
    run = await dispatch_test_run(factory, hospital_id, RecordingTask(), "site-build-quiet")
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    monkeypatch.setattr(
        task_incident_control, "_audit", lambda *_args, **_kwargs: None
    )
    incident = _exhausted_incident(hospital_id, run.id)
    with sync_factory() as db:
        db.add(incident)
        db.commit()
    celery_task = SimpleNamespace(
        request=SimpleNamespace(headers={"operation_run_id": str(run.id)})
    )
    try:
        assert task_incident_control.record_task_success(celery_task, run.task_id) is True

        with sync_factory() as db:
            closed = db.get(Incident, incident.id)
            assert closed is not None and closed.state == "ACKNOWLEDGED"
            assert (
                db.scalar(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == incident.id
                    )
                )
                is None
            )
    finally:
        _cleanup(sync_factory, incident.id)


def _dispatch_headers(run_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(request=SimpleNamespace(headers={"operation_run_id": str(run_id)}))


@pytest.mark.asyncio
async def test_operator_retry_failure_records_on_the_open_site_build_incident(
    signal_store,
    monkeypatch,
) -> None:
    """R1: 운영자 재시도의 실패는 이미 열린 최종 차단 한 건에 실린다.

    sweep이 더 이상 고르지 않는 병원에서도 운영센터 재시도는 눌린다. 그 실패가 조용하면
    누른 사람은 실패한 줄 모른다. 그렇다고 사고를 또 열면 같은 원인이 두 줄이 된다.
    """
    factory, hospital_id = signal_store
    swept = await dispatch_test_run(
        factory,
        hospital_id,
        RecordingTask(),
        f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-09-09:2",
    )
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "operator-retry-open"
    )
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    audits: list[str] = []
    monkeypatch.setattr(
        site_build_incidents,
        "audit_site_build_incident",
        lambda _db, incident: audits.append(incident.state),
    )
    incident = _exhausted_incident(hospital_id, swept.id, state="OPEN")
    with sync_factory() as db:
        db.add(incident)
        db.commit()

    try:
        assert task_incident_control.record_task_failure(
            _dispatch_headers(run.id), run.task_id
        ) is True

        with sync_factory() as db:
            stored = db.get(Incident, incident.id)
            assert stored is not None
            assert stored.state == "OPEN"
            assert stored.occurrence_count == 2
            # 관리자 화면의 낙관적 잠금이 이 변경을 알아채야 한다.
            assert stored.version == 2
            # 조치 버튼이 방금 실패한 실행을 가리킨다.
            assert stored.operation_run_id == run.id
            assert stored.operation_run_id != swept.id
            # 원인 하나에 사고 하나 — generic 사고를 따로 열지 않는다.
            assert (
                db.scalar(
                    select(Incident).where(
                        Incident.dedupe_key == task_incident_control._incident_key(run.id)
                    )
                )
                is None
            )
            # 이미 열린 에피소드는 다시 알리지 않는다.
            assert (
                db.scalar(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == incident.id
                    )
                )
                is None
            )
        assert audits == ["OPEN"]
    finally:
        _cleanup(sync_factory, incident.id)


@pytest.mark.asyncio
async def test_operator_retry_failure_reopens_a_closed_site_build_incident(
    signal_store,
    monkeypatch,
) -> None:
    """R1: 닫힌 최종 차단은 sweep과 같은 규칙으로 새 에피소드가 되어 다시 알린다."""
    factory, hospital_id = signal_store
    swept = await dispatch_test_run(
        factory,
        hospital_id,
        RecordingTask(),
        f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-09-10:2",
    )
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "operator-retry-closed"
    )
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    audits: list[str] = []
    monkeypatch.setattr(
        site_build_incidents,
        "audit_site_build_incident",
        lambda _db, incident: audits.append(incident.state),
    )
    closed_at = datetime.now(UTC)
    incident = _exhausted_incident(
        hospital_id,
        swept.id,
        state="ACKNOWLEDGED",
        recovered_at=closed_at,
        acknowledged_at=closed_at,
    )
    with sync_factory() as db:
        db.add(incident)
        db.commit()

    try:
        assert task_incident_control.record_task_failure(
            _dispatch_headers(run.id), run.task_id
        ) is True

        with sync_factory() as db:
            stored = db.get(Incident, incident.id)
            assert stored is not None
            assert stored.state == "OPEN"
            assert stored.episode_seq == 2
            assert stored.occurrence_count == 2
            assert stored.version == 2
            assert stored.recovered_at is None
            assert stored.acknowledged_at is None
            assert stored.operation_run_id == run.id
            notices = list(
                db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.incident_id == incident.id
                    )
                )
            )
            # 새 에피소드 하나에 OPEN 공지 하나 — dedupe 키가 에피소드를 포함한다.
            assert [notice.notification_type for notice in notices] == ["INCIDENT_OPEN"]
            assert notices[0].dedupe_key.endswith(":e2")
        assert audits == ["OPEN"]
    finally:
        _cleanup(sync_factory, incident.id)


@pytest.mark.asyncio
async def test_operator_retry_failure_without_an_incident_opens_the_hospital_one(
    signal_store,
    monkeypatch,
) -> None:
    """D2: 병원 단위 사고가 아직 없어도 실패는 그 한 건으로 열린다.

    사람이 시작한 시도의 실패는 어떤 경우에도 조용히 사라지지 않는다. 그렇다고 실행 단위
    generic 사고로 열면, 그 실행은 sweep의 예산에도 함께 세어져 뒤이은 자동 실패 두 번이
    같은 원인의 두 번째 OPEN과 두 번째 Slack을 만든다. 재시도의 성공은 병원 단위 한 건만
    닫으므로 generic 쪽은 남는다. 처음부터 같은 dedupe 키 한 건으로 연다.
    """
    factory, hospital_id = signal_store
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "operator-retry-orphan"
    )
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)

    assert task_incident_control.record_task_failure(
        _dispatch_headers(run.id), run.task_id
    ) is True

    with sync_factory() as db:
        opened = db.scalar(
            select(Incident).where(
                Incident.dedupe_key == site_build_incident_key(hospital_id)
            )
        )
        assert opened is not None
        assert opened.incident_type == "SITE_BUILD_FAILED"
        assert opened.safe_error_code == "SITE_BUILD_FAILED"
        assert opened.state == "OPEN"
        assert opened.severity == "HIGH"
        assert opened.operation_run_id == run.id
        assert opened.admin_path == f"/hospitals/{hospital_id}"
        # 사람이 볼 사고 하나에 운영자 채널 알림 하나.
        assert [
            notice.notification_type
            for notice in db.scalars(
                select(NotificationOutbox).where(
                    NotificationOutbox.incident_id == opened.id
                )
            )
        ] == ["INCIDENT_OPEN"]
        # 같은 원인이 두 줄이 되지 않는다 — 실행 단위 generic 사고는 열리지 않는다.
        assert (
            db.scalar(
                select(Incident).where(
                    Incident.dedupe_key == task_incident_control._incident_key(run.id)
                )
            )
            is None
        )
    # 다음 재시도의 성공이 그 한 건을 닫는다 — 사고 유형과 무관하게 dedupe 키로 찾는다.
    retry = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "operator-retry-orphan-again"
    )
    try:
        assert task_incident_control.record_task_success(
            _dispatch_headers(retry.id), retry.task_id
        ) is True
        with sync_factory() as db:
            closed = db.get(Incident, opened.id)
            assert closed is not None and closed.state == "ACKNOWLEDGED"
    finally:
        _cleanup(sync_factory, opened.id)


@pytest.mark.asyncio
async def test_a_later_sweep_touches_the_incident_the_operator_failure_opened(
    signal_store,
    monkeypatch,
) -> None:
    """D2: 그 뒤 예산을 다 쓴 sweep은 두 번째 사고가 아니라 같은 한 건을 만진다."""
    factory, hospital_id = signal_store
    run = await dispatch_test_run(
        factory, hospital_id, RecordingTask(), "operator-retry-then-sweep"
    )
    sync_factory = _sync_factory()
    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", sync_factory)
    assert task_incident_control.record_task_failure(
        _dispatch_headers(run.id), run.task_id
    ) is True

    observed_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    with sync_factory() as db:
        newest = _sweep_run(
            hospital_id,
            f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-08-10:0",
            OperationRunState.FAILED,
            observed_at - timedelta(hours=1),
        )
        db.add(newest)
        db.commit()
        autonomous_recovery._open_rebuild_site_incident(
            db, SimpleNamespace(id=hospital_id, name="재시도 실패 의원"), newest, observed_at
        )
        db.commit()

    with sync_factory() as db:
        rows = list(
            db.scalars(select(Incident).where(Incident.hospital_id == hospital_id))
        )
        assert len(rows) == 1
        # 만든 유형 그대로, 두 번째 사고 없이 관측만 는다.
        assert rows[0].incident_type == "SITE_BUILD_FAILED"
        assert rows[0].occurrence_count == 2
        assert rows[0].operation_run_id == newest.id
    _cleanup(sync_factory, rows[0].id)


@pytest.mark.asyncio
async def test_the_sweep_key_stays_unique_after_a_successful_run(signal_store) -> None:
    """D1: 성공 뒤에도 sweep의 idempotency key는 그날 이미 쓴 값으로 되돌아가지 않는다.

    꼬리표를 실패 수로 세면 성공 한 번이 계수를 0으로 되돌려 같은 날 이미 쓴 키가 다시
    나온다. 유일 제약 위반은 savepoint가 삼키므로 아무 흔적도 없이, 그 병원은 자정까지
    자동 재실행을 한 번도 받지 못한다. 실제 Postgres 제약 위에서 새 행을 확인한다.
    """
    _factory, hospital_id = signal_store
    observed_at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    prefix = f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital_id}:2026-08-10"
    sync_factory = _sync_factory()
    with sync_factory() as db:
        db.add(
            _sweep_run(
                hospital_id,
                f"{prefix}:0",
                OperationRunState.FAILED,
                observed_at - timedelta(hours=3),
            )
        )
        db.add(
            _sweep_run(
                hospital_id,
                f"{prefix}:1",
                OperationRunState.SUCCEEDED,
                observed_at - timedelta(hours=2),
            )
        )
        db.commit()

        created = autonomous_recovery._ensure_rebuild_site_run(
            db, db.get(Hospital, hospital_id), observed_at
        )

        assert created is not None
        assert sorted(
            db.scalars(
                select(OperationRun.idempotency_key).where(
                    OperationRun.hospital_id == hospital_id
                )
            )
        ) == [f"{prefix}:0", f"{prefix}:1", f"{prefix}:2"]


def _sweep_run(
    hospital_id: uuid.UUID,
    idempotency_key: str,
    state: OperationRunState,
    requested_at: datetime,
) -> OperationRun:
    """sweep이 만들었을 모양 그대로의 REBUILD_SITE 실행 한 건."""

    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="REBUILD_SITE",
        state=state,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        requested_at=requested_at,
        completed_at=requested_at + timedelta(minutes=5),
        request_payload={},
        version=1,
    )
