"""H-01: 공개 글 제목 편집으로 무효화된 이미지 인증을 시스템이 재검수해 복구한다.

제목 편집은 subject 인증을 지우고, 공유 판정(`assess_public_visibility`)은 그 글을
IMAGE_NOT_CERTIFIED로 숨긴다. 아무도 재인증하지 않으면 사람이 손을 대야 공개가
돌아온다. 여기서 고정하는 것은 세 가지다 — 재인증 write-back이 **그 판·그 제목**에만
쓰이는 것, 태스크가 저장된 바이트만 재검수해 공개 판을 건드리지 않는 것, 그리고 어떤
경로 조합으로도 (글, 판)당 유료 재검수가 예산을 넘지 않는 것.
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.admin import content as admin_content
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.services import published_image_recertification as recertification
from app.services.content_publication import image_certification_current
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.image_policy import ImagePolicyRejectedError, ImagePolicyUnavailableError
from app.workers import autonomous_recovery, generation_incident_control
from app.workers import tasks as worker_tasks
from app.workers.nightly_generation_batch import write_back_published_image_certificate


class _SessionProxy:
    """`with SyncSessionLocal() as db:` 문법을 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


class _AsyncSessionFacade:
    """사고·알림 경로(async)를 같은 테스트 트랜잭션의 sync 세션 위에서 그대로 돌린다.

    incident/outbox 코드는 스텁하지 않는다 — 실제 UPSERT·중복 제거·감사 기록이 이
    트랜잭션 안에서 돌고 끝나면 함께 롤백된다.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return self._session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self._session.scalar(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return self._session.get(*args, **kwargs)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    def add(self, value):
        self._session.add(value)


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _seed_published(pg_session, *, certified: bool):
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"재인증 {label}",
        slug=f"recert-{label}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan=Plan.PLAN_12,
        publish_days=[1, 3],
        active_from=date(2026, 7, 1),
    )
    image_url = f"https://storage.googleapis.com/reputation-images/{'b' * 64}-{label}.png"
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="새 제목",
        body="본문 " * 400,
        scheduled_date=date.today(),
        status=ContentStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
        published_by="ae@example.com",
        essence_status="ALIGNED",
        image_url=image_url,
        content_revision=3,
        image_policy_verified_at=datetime.now(timezone.utc) if certified else None,
        image_content_hash=image_content_hash_from_url(image_url) if certified else None,
        image_subject_hash=(
            image_subject_hash(ContentType.DISEASE, "새 제목") if certified else None
        ),
        image_policy_version=IMAGE_POLICY_VERSION if certified else None,
    )
    pg_session.add_all([hospital, schedule, item])
    pg_session.commit()
    return hospital, item


def _wire_task(monkeypatch, pg_session, certify):
    """태스크가 테스트 트랜잭션 안에서 실제 SQL을 돌게 붙인다."""

    monkeypatch.setattr(worker_tasks, "certify_existing_image", certify)
    monkeypatch.setattr(worker_tasks, "SyncSessionLocal", lambda: _SessionProxy(pg_session))
    monkeypatch.setattr(worker_tasks, "_run_async", lambda coro: asyncio.run(coro))
    submitted: list[uuid.UUID] = []
    revalidated: list[str] = []
    monkeypatch.setattr(
        worker_tasks.indexnow,
        "enqueue_content_published_sync",
        lambda db, **kwargs: submitted.append(kwargs["content_id"]),
    )

    async def _revalidate(slug, content_id, **kwargs):
        revalidated.append(slug)
        return True

    monkeypatch.setattr(worker_tasks, "trigger_content_site_revalidate_safe", _revalidate)
    return submitted, revalidated


def _wire_incidents(monkeypatch, pg_session):
    """사고·Slack 경로를 같은 트랜잭션의 실제 코드로 돌린다."""

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: (lambda: _AsyncSessionFacade(pg_session)),
    )


def _claimed_run(pg_session, hospital, item, *, revision=None, key=None):
    """worker가 이미 claim한 실행. 실제 `finish_explicit_run` 경로를 타게 한다."""

    worker_id = str(uuid.uuid4())
    revision = int(item.content_revision if revision is None else revision)
    now = datetime.now(timezone.utc)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=OperationRunState.RUNNING,
        idempotency_key=key or recertification.base_key(item.id, revision),
        task_id=worker_id,
        lease_owner=worker_id,
        lease_expires_at=now + timedelta(minutes=15),
        requested_at=now,
        queued_at=now,
        started_at=now,
        request_payload=recertification.request_payload(item.id, revision),
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        version=1,
    )
    pg_session.add(run)
    pg_session.commit()
    return run, worker_id


def _terminal_run(
    pg_session,
    hospital,
    item,
    *,
    code: str,
    revision=None,
    state=OperationRunState.FAILED,
    finished_minutes_ago: int = 60,
    key: str | None = None,
):
    revision = int(item.content_revision if revision is None else revision)
    finished = datetime.now(timezone.utc) - timedelta(minutes=finished_minutes_ago)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=state,
        idempotency_key=key or f"recertify:{item.id}:{revision}:s{uuid.uuid4().hex[:6]}",
        request_payload=recertification.request_payload(item.id, revision),
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        safe_error_code=code,
        requested_at=finished,
        completed_at=finished,
        version=2,
    )
    pg_session.add(run)
    pg_session.commit()
    return run


def _run_task(item, run, worker_id):
    task = worker_tasks.recertify_published_content_image
    task.push_request(
        id=worker_id,
        headers={"operation_run_id": str(run.id)},
        operation_run_claim_version=run.version,
        called_directly=False,
    )
    try:
        task.run(str(item.id))
    finally:
        task.pop_request()


def _marker(pg_session, item):
    pg_session.expire_all()
    summary = pg_session.get(ContentItem, item.id).essence_check_summary or {}
    return summary.get(recertification.MARKER_FIELD)


def _open_incidents(pg_session, hospital):
    return list(
        pg_session.execute(
            select(Incident).where(Incident.hospital_id == hospital.id)
        ).scalars()
    )


async def _reject(image_url, *, content_type, topic, hospital_id=None):
    raise ImagePolicyRejectedError("subject mismatch")


async def _unavailable(image_url, *, content_type, topic, hospital_id=None):
    raise ImagePolicyUnavailableError("policy reviewer is down")


def test_write_back_only_touches_the_published_row_at_the_expected_revision(pg_session):
    _hospital, item = _seed_published(pg_session, certified=False)
    values = {
        "image_content_hash": image_content_hash_from_url(item.image_url),
        "image_subject_hash": image_subject_hash(ContentType.DISEASE, "새 제목"),
        "image_policy_version": IMAGE_POLICY_VERSION,
        "image_policy_verified_at": datetime.now(timezone.utc),
    }

    assert (
        write_back_published_image_certificate(
            pg_session,
            item_id=item.id,
            expected_title="다른 제목",
            expected_revision=3,
            values=values,
        )
        == 0
    )
    assert (
        write_back_published_image_certificate(
            pg_session,
            item_id=item.id,
            expected_title="새 제목",
            expected_revision=99,
            values=values,
        )
        == 0
    )
    assert (
        write_back_published_image_certificate(
            pg_session,
            item_id=item.id,
            expected_title="새 제목",
            expected_revision=3,
            values=values,
        )
        == 1
    )

    pg_session.commit()
    pg_session.expire(item)
    assert item.content_revision == 3  # 재인증은 판을 바꾸지 않는다
    assert item.status == ContentStatus.PUBLISHED
    assert image_certification_current(item)


def test_write_back_matches_a_null_title_instead_of_reporting_no_change(pg_session):
    """제목이 NULL인 판에서 `= NULL`은 항상 거짓이라 안 바뀐 척하는 고리가 됐다."""
    _hospital, item = _seed_published(pg_session, certified=False)
    pg_session.execute(
        update(ContentItem).where(ContentItem.id == item.id).values(title=None)
    )
    pg_session.commit()

    written = write_back_published_image_certificate(
        pg_session,
        item_id=item.id,
        expected_title=None,
        expected_revision=3,
        values={"image_policy_verified_at": datetime.now(timezone.utc)},
    )

    assert written == 1


def test_task_recertifies_stored_bytes_and_leaves_status_published(pg_session, monkeypatch):
    hospital, item = _seed_published(pg_session, certified=False)

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        return image_content_hash_from_url(image_url), image_subject_hash(content_type, topic)

    submitted, revalidated = _wire_task(monkeypatch, pg_session, _certify)

    worker_tasks.recertify_published_content_image.run(str(item.id))

    pg_session.expire(item)
    assert item.status == ContentStatus.PUBLISHED
    assert item.content_revision == 3
    assert image_certification_current(item)
    # 공개가 돌아왔으니 색인 재제출과 사이트 캐시 갱신이 따라야 한다.
    assert submitted == [item.id]
    assert revalidated == [hospital.slug]


def test_task_skips_the_paid_review_when_the_certificate_is_already_current(
    pg_session, monkeypatch
):
    """이미 인증이 유효하면 공급자를 다시 사지 않는다 (중복 디스패치·재배달)."""

    _hospital, item = _seed_published(pg_session, certified=True)
    calls: list[str] = []

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise AssertionError("이미 인증된 글에 유료 재검수를 호출했다")

    submitted, revalidated = _wire_task(monkeypatch, pg_session, _certify)
    finished: list[tuple] = []
    monkeypatch.setattr(
        worker_tasks,
        "finish_explicit_run",
        lambda db, task, item_id, state, **kw: finished.append(
            (state, kw.get("safe_error_code"))
        ),
    )

    worker_tasks.recertify_published_content_image.run(str(item.id))

    assert calls == []
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert submitted == [] and revalidated == []


def test_a_title_edit_during_the_review_discards_the_late_certificate(pg_session, monkeypatch):
    """재검수 중 제목이 또 바뀌면 늦은 인증은 버려진다 — 그 편집이 다시 요청한다."""

    _hospital, item = _seed_published(pg_session, certified=False)

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        # 재검수가 도는 사이 AE가 제목을 다시 고쳤다 (판도 함께 올라간다).
        pg_session.execute(
            ContentItem.__table__.update()
            .where(ContentItem.id == item.id)
            .values(title="더 새로운 제목", content_revision=4)
        )
        pg_session.commit()
        return image_content_hash_from_url(image_url), image_subject_hash(content_type, topic)

    submitted, revalidated = _wire_task(monkeypatch, pg_session, _certify)
    finished: list[tuple] = []
    monkeypatch.setattr(
        worker_tasks,
        "finish_explicit_run",
        lambda db, task, item_id, state, **kw: finished.append(
            (state, kw.get("safe_error_code"))
        ),
    )

    worker_tasks.recertify_published_content_image.run(str(item.id))

    pg_session.expire(item)
    assert item.title == "더 새로운 제목"
    assert not image_certification_current(item)
    assert finished == [(OperationRunState.CANCELLED, None)]
    assert submitted == [] and revalidated == []


def test_a_rejection_opens_one_incident_and_a_second_dispatch_pays_nothing(
    pg_session, monkeypatch
):
    """거절은 사람의 결정이다. 같은 판의 다음 실행은 돈을 쓰지 않고 같은 사고를 갱신한다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _reject_once(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise ImagePolicyRejectedError("subject mismatch")

    _wire_task(monkeypatch, pg_session, _reject_once)
    _wire_incidents(monkeypatch, pg_session)

    first, first_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, first, first_worker)

    pg_session.expire_all()
    assert first.state == OperationRunState.FAILED
    assert first.safe_error_code == recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED
    assert _marker(pg_session, item) == {
        "revision": 3,
        "blocked": True,
        "code": recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED,
    }
    incidents = _open_incidents(pg_session, hospital)
    assert len(incidents) == 1
    assert incidents[0].state == IncidentState.OPEN.value
    assert recertification.OPERATOR_ACTION in incidents[0].next_action

    second, second_worker = _claimed_run(
        pg_session, hospital, item, key=recertification.sweep_key(item.id, 3, 2)
    )
    _run_task(item, second, second_worker)

    pg_session.expire_all()
    assert calls == [item.image_url]  # 두 번째 실행은 공급자를 부르지 않는다
    assert second.state == OperationRunState.FAILED
    assert second.safe_error_code == recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED
    assert len(_open_incidents(pg_session, hospital)) == 1
    opened = pg_session.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == incidents[0].id,
            NotificationOutbox.notification_type == "INCIDENT_OPEN",
        )
    )
    assert opened == 1  # 같은 판의 반복은 Slack을 다시 보내지 않는다


def test_the_spent_budget_becomes_one_incident_without_another_paid_review(
    pg_session, monkeypatch
):
    """예산이 끝난 뒤의 실행은 유료 호출 없이 '반복 실패' 하나로 닫힌다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _never(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise AssertionError("예산이 끝난 뒤에 유료 재검수를 호출했다")

    for _ in range(recertification.ATTEMPT_BUDGET):
        _terminal_run(pg_session, hospital, item, code="PROVIDER_UNAVAILABLE")

    _wire_task(monkeypatch, pg_session, _never)
    _wire_incidents(monkeypatch, pg_session)
    final, worker_id = _claimed_run(
        pg_session, hospital, item, key=recertification.sweep_key(item.id, 3, 4)
    )

    _run_task(item, final, worker_id)

    pg_session.expire_all()
    assert calls == []
    assert final.state == OperationRunState.FAILED
    assert final.safe_error_code == (
        recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED
    )
    incidents = _open_incidents(pg_session, hospital)
    assert len(incidents) == 1
    assert incidents[0].safe_error_code == (
        recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED
    )
    marker = _marker(pg_session, item)
    assert marker["blocked"] is True and marker["revision"] == 3


def test_a_transient_failure_ends_this_run_and_leaves_the_budget_to_the_sweep(
    pg_session, monkeypatch
):
    """일시 오류는 실행 하나만 쓴다 — 태스크가 스스로 다시 사지 않는다."""
    hospital, item = _seed_published(pg_session, certified=False)
    _wire_task(monkeypatch, pg_session, _unavailable)
    _wire_incidents(monkeypatch, pg_session)
    run, worker_id = _claimed_run(pg_session, hospital, item)

    with pytest.raises(ImagePolicyUnavailableError):
        _run_task(item, run, worker_id)

    pg_session.expire_all()
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code not in recertification.OPERATOR_REQUIRED_CODES
    # 자동 복구가 이어질 실패에는 사람을 부르지도, 후보에서 빼지도 않는다.
    assert _open_incidents(pg_session, hospital) == []
    assert _marker(pg_session, item) is None


def test_success_clears_the_block_and_recovers_the_incident(pg_session, monkeypatch):
    """복구가 확인되면 표시와 사고를 사람 개입 없이 닫는다."""
    hospital, item = _seed_published(pg_session, certified=False)
    _wire_task(monkeypatch, pg_session, _reject)
    _wire_incidents(monkeypatch, pg_session)
    blocked_run, blocked_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, blocked_run, blocked_worker)
    assert _marker(pg_session, item)["blocked"] is True

    # 사람이 원본 이미지를 되살려 인증이 다시 유효해진 판. 중복 디스패치가 이를 본다.
    pg_session.execute(
        update(ContentItem)
        .where(ContentItem.id == item.id)
        .values(
            image_content_hash=image_content_hash_from_url(item.image_url),
            image_subject_hash=image_subject_hash(ContentType.DISEASE, "새 제목"),
            image_policy_version=IMAGE_POLICY_VERSION,
            image_policy_verified_at=datetime.now(timezone.utc),
        )
    )
    pg_session.commit()
    healthy, healthy_worker = _claimed_run(
        pg_session, hospital, item, key=recertification.sweep_key(item.id, 3, 2)
    )

    _run_task(item, healthy, healthy_worker)

    pg_session.expire_all()
    assert healthy.state == OperationRunState.SUCCEEDED
    assert _marker(pg_session, item) is None
    incidents = _open_incidents(pg_session, hospital)
    assert [incident.state for incident in incidents] == [IncidentState.RECOVERED.value]


def test_the_sweep_candidate_sql_skips_certified_and_blocked_rows(pg_session):
    """자동으로 고칠 수 있는 글만 후보다 — 사람 대기 중인 행이 자리를 뺏지 않는다."""
    _cleared_hospital, cleared = _seed_published(pg_session, certified=False)
    _certified_hospital, certified = _seed_published(pg_session, certified=True)
    _blocked_hospital, blocked = _seed_published(pg_session, certified=False)
    recertification.mark_blocked(
        pg_session,
        item_id=blocked.id,
        revision=3,
        code=recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED,
    )
    pg_session.commit()

    selected = {
        item.id for item in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }
    assert cleared.id in selected
    assert certified.id not in selected
    assert blocked.id not in selected

    recertification.clear_marker(pg_session, item_id=blocked.id, revision=3)
    pg_session.commit()

    reselected = {
        item.id for item in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }
    assert blocked.id in reselected


def test_the_sweep_reads_the_cooldown_and_the_current_revision_from_real_runs(pg_session):
    """예산 세 번이 몇 분에 타지 않고, 지난 판의 실행이 지금 판을 막지 않는다."""
    hospital, item = _seed_published(pg_session, certified=False)
    _terminal_run(pg_session, hospital, item, code="COST_BLOCKED", finished_minutes_ago=5)
    now = datetime.now(timezone.utc)

    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert not recertification.sweep_may_dispatch(runs, 3, now=now)
    assert recertification.sweep_may_dispatch(
        runs, 3, now=now + recertification.RETRY_COOLDOWN
    )

    # 지난 판의 진행 중 실행. 시작하자마자 현재 판을 보고 끝나므로 막지 않는다.
    _claimed_run(pg_session, hospital, item, revision=2, key="recertify:stale:2")
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert recertification.sweep_may_dispatch(
        runs, 3, now=now + recertification.RETRY_COOLDOWN
    )

    # 같은 판의 진행 중 실행은 겹쳐 사지 않도록 막는다.
    _claimed_run(pg_session, hospital, item, key=recertification.base_key(item.id, 3))
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert not recertification.sweep_may_dispatch(
        runs, 3, now=now + recertification.RETRY_COOLDOWN
    )


async def test_a_title_edit_on_a_paused_hospital_does_not_dispatch(
    pg_async_session, monkeypatch
):
    """공개 표면이 없는 병원에 지금 재인증을 사지 않는다 — 재개 뒤 sweep이 잇는다."""
    label = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"재인증 정지 {label}",
        slug=f"recert-paused-{label}",
        status=HospitalStatus.PAUSED,
        site_live=True,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan=Plan.PLAN_12,
        publish_days=[1, 3],
        active_from=date(2026, 7, 1),
    )
    image_url = f"https://storage.googleapis.com/reputation-images/{'c' * 64}-{label}.png"
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="원래 제목",
        body="본문 " * 400,
        scheduled_date=date.today(),
        status=ContentStatus.PUBLISHED,
        published_at=datetime.now(timezone.utc),
        published_by="ae@example.com",
        essence_status="ALIGNED",
        image_url=image_url,
        content_revision=3,
        image_policy_verified_at=datetime.now(timezone.utc),
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash(ContentType.DISEASE, "원래 제목"),
        image_policy_version=IMAGE_POLICY_VERSION,
    )
    pg_async_session.add_all([hospital, schedule, item])
    await pg_async_session.commit()

    published: list[str] = []
    monkeypatch.setattr(
        admin_content,
        "recertify_published_content_image",
        type(
            "_Task",
            (),
            {"apply_async": lambda self, **kwargs: published.append(kwargs["task_id"])},
        )(),
    )

    await admin_content.update_content(
        hospital.id,
        item.id,
        admin_content.ContentPatch(title="바뀐 제목"),
        db=pg_async_session,
    )

    await pg_async_session.refresh(item)
    assert item.image_policy_verified_at is None  # 인증은 그대로 무효화된다
    assert published == []
    runs = await pg_async_session.scalar(
        select(func.count(OperationRun.id)).where(OperationRun.hospital_id == hospital.id)
    )
    assert runs == 0
