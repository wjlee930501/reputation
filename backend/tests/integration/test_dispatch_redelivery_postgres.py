"""브로커가 되돌린(redelivered) 같은 task id 사본은 자기 실행을 다시 claim해 그대로 돈다(2026-10-08).

cold shutdown이 실행 중 요청을 취소하고 채널을 닫으면, kombu는 확인 안 된 메시지를 몇 초 안에
`redelivered=True`로 되돌린다. 그 실행 행은 같은 task id의 RUNNING이다. 검증 뒤 claim으로 순서를
바꾼 뒤에도 이 사본은 claim에 성공하고 AUTHORIZED여야 한다 — 같은 상태의 재배달 아닌 사본만
중복(STALE)으로 건너뛴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore
from sqlalchemy.orm import Session

from app.core import database
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import dispatch_auth, operation_run_signals

IMAGE_TASK = "app.workers.tasks.generate_content_image"


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(pg_conn, monkeypatch):
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    monkeypatch.setattr(database, "SyncSessionLocal", lambda: _session(pg_conn))
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", lambda: _session(pg_conn))
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _running_image_run(db) -> tuple[OperationRun, str]:
    hospital = Hospital(
        name="재배달 가상의원",
        slug=f"redelivery-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
    )
    db.add(hospital)
    db.flush()
    content_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="REGENERATE_CONTENT_IMAGE",
        state=OperationRunState.RUNNING,
        task_id="interrupted-copy",
        lease_owner="interrupted-copy",
        # 끊긴 직후라 lease는 아직 살아 있다.
        lease_expires_at=now + timedelta(minutes=50),
        started_at=now - timedelta(minutes=10),
        attempt_count=1,
        version=3,
        request_payload=build_request_payload(
            DispatchPayload("content_item", content_id, "content", (content_id,))
        ),
    )
    db.add(run)
    db.commit()
    return run, content_id


def _delivery(run, content_id: str, *, redelivered: bool):
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id=run.task_id,
        args=[content_id],
        kwargs={},
        retries=0,
        headers={"operation_run_id": str(run.id)},
    )
    return SimpleNamespace(
        name=IMAGE_TASK,
        request=SimpleNamespace(
            id=run.task_id,
            retries=0,
            headers=headers,
            delivery_info={"redelivered": redelivered},
        ),
    )


def test_a_redelivered_copy_reclaims_its_own_running_run_and_runs(db) -> None:
    run, content_id = _running_image_run(db)
    task = _delivery(run, content_id, redelivered=True)

    # 예외 없이 끝나면 Celery가 태스크 본문을 실행한다.
    dispatch_auth.AuthenticatedTask.before_start(task, run.task_id, (content_id,), {})

    db.expire_all()
    current = db.get(OperationRun, run.id)
    assert task.request.operation_run_claim_version == current.version == 4
    assert current.state == OperationRunState.RUNNING
    assert current.lease_owner == run.task_id
    assert current.attempt_count == 2


def test_a_non_redelivered_duplicate_of_a_running_run_is_skipped(db) -> None:
    run, content_id = _running_image_run(db)
    task = _delivery(run, content_id, redelivered=False)

    with pytest.raises(Ignore):
        dispatch_auth.AuthenticatedTask.before_start(task, run.task_id, (content_id,), {})

    db.expire_all()
    current = db.get(OperationRun, run.id)
    assert current.version == 3
    assert current.attempt_count == 1
