"""H-01: 공개 글 제목 편집으로 무효화된 이미지 인증을 시스템이 재검수해 복구한다.

제목 편집은 subject 인증을 지우고, 공유 판정(`assess_public_visibility`)은 그 글을
IMAGE_NOT_CERTIFIED로 숨긴다. 아무도 재인증하지 않으면 사람이 손을 대야 공개가
돌아온다. 여기서 고정하는 것은 세 가지다 — 재인증 write-back이 **그 판·그 제목**에만
쓰이는 것, 태스크가 저장된 바이트만 재검수해 공개 판을 건드리지 않는 것, 그리고 어떤
경로 조합으로도 (글, 이미지 subject)당 유료 재검수가 예산을 넘지 않는 것.
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.admin import content as admin_content
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.services import content_publication
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


def _subject(item, title=None):
    return image_subject_hash(item.content_type, item.title if title is None else title)


def _payload(item, title):
    return recertification.request_payload(
        item.id,
        subject_hash=image_subject_hash(item.content_type, title),
        title=title,
        revision=int(item.content_revision),
    )


def _claimed_run(
    pg_session, hospital, item, *, title=None, key=None, age_minutes=0, attempts=1
):
    """worker가 이미 claim한 실행. 실제 `finish_explicit_run` 경로를 타게 한다.

    `attempts`는 prerun의 claim이 올린 `attempt_count`다. 2면 worker 유실로 재배달돼
    같은 행을 다시 claim한 실행이다.
    """

    worker_id = str(uuid.uuid4())
    title = item.title if title is None else title
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=OperationRunState.RUNNING,
        idempotency_key=key
        or recertification.base_key(item.id, image_subject_hash(item.content_type, title)),
        task_id=worker_id,
        lease_owner=worker_id,
        lease_expires_at=now + timedelta(minutes=15),
        requested_at=now,
        queued_at=now,
        started_at=now,
        request_payload=_payload(item, title),
        attempt_count=attempts,
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
    title=None,
    state=OperationRunState.FAILED,
    finished_minutes_ago: int = 60,
    key: str | None = None,
):
    title = item.title if title is None else title
    finished = datetime.now(timezone.utc) - timedelta(minutes=finished_minutes_ago)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=state,
        idempotency_key=key or f"recertify:{item.id}:s{uuid.uuid4().hex[:6]}",
        request_payload=_payload(item, title),
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


def _lock_releases(pg_session, monkeypatch, run, item):
    """커밋·롤백(= 행 잠금 해제)마다 그 순간의 (실행 상태, 표시 유무)를 남긴다."""

    releases: list[tuple[str, bool]] = []
    real_commit, real_rollback = pg_session.commit, pg_session.rollback

    def _record():
        state = pg_session.execute(
            select(OperationRun.state).where(OperationRun.id == run.id)
        ).scalar_one()
        summary = pg_session.execute(
            select(ContentItem.essence_check_summary).where(ContentItem.id == item.id)
        ).scalar_one()
        releases.append(
            (
                str(getattr(state, "value", state)),
                recertification.MARKER_FIELD in (summary or {}),
            )
        )

    def _commit():
        real_commit()
        _record()

    def _rollback():
        real_rollback()
        _record()

    monkeypatch.setattr(pg_session, "commit", _commit)
    monkeypatch.setattr(pg_session, "rollback", _rollback)
    return releases


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
    """거절은 사람의 결정이다. 같은 subject의 다음 실행은 돈을 쓰지 않고 같은 사고를 갱신한다."""
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
        "subject_hash": _subject(item),
        "title": "새 제목",
        "blocked": True,
        "code": recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED,
    }
    incidents = _open_incidents(pg_session, hospital)
    assert len(incidents) == 1
    assert incidents[0].state == IncidentState.OPEN.value
    assert recertification.OPERATOR_ACTION in incidents[0].next_action

    second, second_worker = _claimed_run(
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 2),
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
    assert opened == 1  # 같은 subject의 반복은 Slack을 다시 보내지 않는다


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
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 4),
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
    assert marker["blocked"] is True and marker["subject_hash"] == _subject(item)


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
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 2),
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
        subject_hash=_subject(blocked),
        title=blocked.title,
        code=recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED,
    )
    pg_session.commit()

    selected = {
        item.id for item in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }
    assert cleared.id in selected
    assert certified.id not in selected
    assert blocked.id not in selected

    recertification.clear_marker(pg_session, item_id=blocked.id, title=blocked.title)
    pg_session.commit()

    reselected = {
        item.id for item in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }
    assert blocked.id in reselected


def test_the_sweep_reads_the_cooldown_and_the_current_subject_from_real_runs(pg_session):
    """예산 세 번이 몇 분에 타지 않고, 지난 제목의 실행이 지금 제목을 막지 않는다."""
    hospital, item = _seed_published(pg_session, certified=False)
    _terminal_run(pg_session, hospital, item, code="COST_BLOCKED", finished_minutes_ago=5)
    now = datetime.now(timezone.utc)
    subject = _subject(item)

    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert not recertification.sweep_may_dispatch(runs, subject, now=now)
    assert recertification.sweep_may_dispatch(
        runs, subject, now=now + recertification.RETRY_COOLDOWN
    )

    # 지난 제목의 진행 중 실행. 시작하자마자 현재 subject를 보고 돈을 쓰지 않고 끝난다.
    _claimed_run(pg_session, hospital, item, title="옛 제목", key="recertify:stale:1")
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert recertification.sweep_may_dispatch(
        runs, subject, now=now + recertification.RETRY_COOLDOWN
    )

    # 같은 subject의 진행 중 실행은 겹쳐 사지 않도록 막는다.
    _claimed_run(pg_session, hospital, item, key=recertification.base_key(item.id, subject))
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert not recertification.sweep_may_dispatch(
        runs, subject, now=now + recertification.RETRY_COOLDOWN
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


def test_a_stale_subject_run_ends_without_paying_or_marking(pg_session, monkeypatch):
    """자기 실행이 만들어진 제목이 지금 제목과 다르면 아무것도 사지 않고 끝난다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _never(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise AssertionError("지난 제목의 실행이 유료 재검수를 호출했다")

    _wire_task(monkeypatch, pg_session, _never)
    _wire_incidents(monkeypatch, pg_session)
    stale, worker_id = _claimed_run(pg_session, hospital, item, title="옛 제목")

    _run_task(item, stale, worker_id)

    pg_session.expire_all()
    assert calls == []
    assert stale.state == OperationRunState.CANCELLED
    assert stale.safe_error_code is None
    assert _marker(pg_session, item) is None
    assert _open_incidents(pg_session, hospital) == []


def test_a_revision_bump_keeps_the_block_marker_budget_and_incident(pg_session, monkeypatch):
    """제목을 건드리지 않는 재승인은 판만 올린다 — 차단을 되살려 다시 사면 안 된다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _reject_once(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise ImagePolicyRejectedError("subject mismatch")

    _wire_task(monkeypatch, pg_session, _reject_once)
    _wire_incidents(monkeypatch, pg_session)
    first, first_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, first, first_worker)
    incidents = _open_incidents(pg_session, hospital)
    assert len(incidents) == 1

    # 운영 기준 재승인. 제목은 그대로인데 판이 오르고 요약이 다시 쓰인다.
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.DRAFT,
    )
    pg_session.add(philosophy)
    pg_session.commit()
    pg_session.expire_all()
    reloaded = pg_session.get(ContentItem, item.id)
    content_publication.apply_essence_revalidation(reloaded, philosophy)
    pg_session.commit()

    pg_session.expire_all()
    assert pg_session.get(ContentItem, item.id).content_revision == 4
    assert _marker(pg_session, item)["blocked"] is True  # 표시가 살아남는다
    assert autonomous_recovery._cleared_certificate_candidates(pg_session) == []

    second, second_worker = _claimed_run(
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 2),
    )
    _run_task(item, second, second_worker)

    pg_session.expire_all()
    assert calls == [item.image_url]  # 판이 올라도 같은 답을 다시 사지 않는다
    assert len(_open_incidents(pg_session, hospital)) == 1  # 두 번째 사고를 열지 않는다
    opened = pg_session.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == incidents[0].id,
            NotificationOutbox.notification_type == "INCIDENT_OPEN",
        )
    )
    assert opened == 1


def test_a_stranded_run_counts_against_the_budget(pg_session, monkeypatch):
    """worker와 함께 유실된 실행도 이미 돈을 썼을 수 있다 — 예산에서 뺄 수 없다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _never(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise AssertionError("예산이 끝난 뒤에 유료 재검수를 호출했다")

    for _ in range(recertification.ATTEMPT_BUDGET - 1):
        _terminal_run(pg_session, hospital, item, code="PROVIDER_UNAVAILABLE")
    # 하드 제한(900초)을 한참 넘긴 RUNNING — 결과는 없지만 이미 샀을 수 있다.
    _claimed_run(pg_session, hospital, item, key="recertify:stranded", age_minutes=45)

    _wire_task(monkeypatch, pg_session, _never)
    _wire_incidents(monkeypatch, pg_session)
    final, worker_id = _claimed_run(
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 4),
    )

    _run_task(item, final, worker_id)

    pg_session.expire_all()
    assert calls == []
    assert final.safe_error_code == recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED


def test_the_rejection_branch_releases_the_lock_only_with_the_recorded_block(
    pg_session, monkeypatch
):
    """유료 호출 뒤 잠금을 놓는 첫 순간에 이미 종결과 표시가 함께 있어야 한다.

    중간에 잠금을 놓으면 그 틈에 온 다른 디스패치가 '아직 아무도 시도하지 않았다'고
    보고 같은 답을 한 번 더 산다.
    """
    hospital, item = _seed_published(pg_session, certified=False)
    _wire_task(monkeypatch, pg_session, _reject)
    opened: list[str] = []
    monkeypatch.setattr(
        worker_tasks,
        "_open_published_recertify_incident",
        lambda **kwargs: opened.append(kwargs["code"]),
    )
    run, worker_id = _claimed_run(pg_session, hospital, item)
    releases = _lock_releases(pg_session, monkeypatch, run, item)

    _run_task(item, run, worker_id)

    assert releases == [("FAILED", True)]
    assert opened == [recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED]


def test_a_block_whose_incident_cannot_open_stays_recoverable(pg_session, monkeypatch):
    """사고를 열지 못하면 차단으로 기록하지 않는다 — 아무도 보지 않는 보류가 된다."""
    hospital, item = _seed_published(pg_session, certified=False)
    _wire_task(monkeypatch, pg_session, _reject)

    def _explode(**_kwargs):
        raise RuntimeError("incident store unavailable")

    monkeypatch.setattr(worker_tasks, "_open_published_recertify_incident", _explode)
    run, worker_id = _claimed_run(pg_session, hospital, item)

    _run_task(item, run, worker_id)

    pg_session.expire_all()
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code not in recertification.OPERATOR_REQUIRED_CODES
    assert _marker(pg_session, item) is None
    assert _open_incidents(pg_session, hospital) == []
    # 표시가 없으니 sweep이 남은 예산 안에서 다시 이어간다.
    assert item.id in {
        candidate.id
        for candidate in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }


def test_a_redelivered_execution_counts_its_earlier_attempt_against_the_budget(
    pg_session, monkeypatch
):
    """worker가 유료 호출 도중 죽어 재배달된 실행은 자기 앞의 호출을 스스로 센다.

    `task_acks_late`·`task_reject_on_worker_lost`에서 broker는 같은 task를 다시 보내고
    prerun은 같은 행을 다시 claim한다. 행 수로 세면 그 행은 여전히 하나라, 예산 세 번을
    쓴 subject가 네 번째 유료 재검수를 산다.
    """
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _never(image_url, *, content_type, topic, hospital_id=None):
        calls.append(image_url)
        raise AssertionError("예산이 끝난 뒤에 유료 재검수를 호출했다")

    for _ in range(recertification.ATTEMPT_BUDGET - 1):
        _terminal_run(pg_session, hospital, item, code="PROVIDER_UNAVAILABLE")
    _wire_task(monkeypatch, pg_session, _never)
    _wire_incidents(monkeypatch, pg_session)
    redelivered, worker_id = _claimed_run(
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 3),
        attempts=2,
    )

    _run_task(item, redelivered, worker_id)

    pg_session.expire_all()
    assert calls == []
    assert redelivered.safe_error_code == (
        recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED
    )
    incidents = _open_incidents(pg_session, hospital)
    assert [incident.state for incident in incidents] == [IncidentState.OPEN.value]


def test_a_redelivered_execution_still_pays_once_while_the_budget_holds(
    pg_session, monkeypatch
):
    """재배달 자체가 유료 재검수를 막지는 않는다 — 예산이 남아 있으면 한 번은 산다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        calls.append(topic)
        return image_content_hash_from_url(image_url), image_subject_hash(
            content_type, topic
        )

    _wire_task(monkeypatch, pg_session, _certify)
    _wire_incidents(monkeypatch, pg_session)
    redelivered, worker_id = _claimed_run(pg_session, hospital, item, attempts=2)

    _run_task(item, redelivered, worker_id)

    pg_session.expire_all()
    assert calls == [item.title]
    assert redelivered.state == OperationRunState.SUCCEEDED
    assert image_certification_current(pg_session.get(ContentItem, item.id))
    # 직전 execution 하나와 이번 하나 — 예산 세 번을 넘지 않는다.
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    assert (
        recertification.attempts_spent(
            runs, _subject(item), now=datetime.now(timezone.utc)
        )
        == 2
    )


def _retitle(pg_session, item, title: str, *, clear_certificate: bool = True):
    """제목 편집이 남기는 상태 — 인증이 지워진 공개 글."""

    values = {"title": title}
    if clear_certificate:
        values |= {
            "image_policy_verified_at": None,
            "image_subject_hash": None,
            "image_content_hash": None,
            "image_policy_version": None,
        }
    pg_session.execute(update(ContentItem).where(ContentItem.id == item.id).values(**values))
    pg_session.commit()
    pg_session.expire_all()
    return pg_session.get(ContentItem, item.id)


def test_success_recovers_only_the_succeeding_subjects_incident(pg_session, monkeypatch):
    """A→B 거절 뒤 A로 되돌린 성공은 B의 사고를 닫지 않는다.

    닫아버리면 운영자가 B를 다시 적용했을 때 — PATCH는 종결된 B 실행의 멱등 재생이라
    실행이 생기지 않고, sweep은 B의 보류 코드를 보고 거절한다 — 아무도 보지 않는
    보류가 된다.
    """
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _certify(image_url, *, content_type, topic, hospital_id=None):
        calls.append(topic)
        if topic == "B 제목":
            raise ImagePolicyRejectedError("subject mismatch")
        return image_content_hash_from_url(image_url), image_subject_hash(
            content_type, topic
        )

    _wire_task(monkeypatch, pg_session, _certify)
    _wire_incidents(monkeypatch, pg_session)

    item = _retitle(pg_session, item, "B 제목")
    subject_b = _subject(item)
    rejected, rejected_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, rejected, rejected_worker)
    assert _marker(pg_session, item)["subject_hash"] == subject_b

    # 운영자가 제목을 A로 되돌린다. 그 subject의 재인증은 성공한다.
    item = _retitle(pg_session, item, "A 제목")
    healthy, healthy_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, healthy, healthy_worker)

    pg_session.expire_all()
    assert healthy.state == OperationRunState.SUCCEEDED
    assert _marker(pg_session, item) is None
    incidents = _open_incidents(pg_session, hospital)
    assert len(incidents) == 1
    assert incidents[0].dedupe_key == recertification.incident_dedupe_key(
        item.id, subject_b
    )
    # B는 여전히 막혀 있다 — 그 사고를 닫는 것은 사실이 아니다.
    assert incidents[0].state == IncidentState.OPEN.value

    # 운영자가 B를 다시 적용한다. PATCH는 종결된 B 실행의 멱등 재생이라 실행이 없다.
    item = _retitle(pg_session, item, "B 제목")
    assert item.id in {
        candidate.id
        for candidate in autonomous_recovery._cleared_certificate_candidates(pg_session)
    }
    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    later = datetime.now(timezone.utc) + recertification.RETRY_COOLDOWN
    # 사람이 볼 사고가 살아 있으므로 sweep은 같은 거절을 다시 사지 않는다.
    assert recertification.incident_dedupe_key(
        item.id, subject_b
    ) in autonomous_recovery._visible_block_incidents(pg_session, [item])
    assert not recertification.sweep_may_dispatch(
        runs, subject_b, now=later, block_visible=True
    )
    assert calls == ["B 제목", "A 제목"]


def test_a_manually_closed_block_is_reopened_without_paying(pg_session, monkeypatch):
    """사람이 닫은 사고 위에 같은 차단이 남아 있으면 무료 실행 하나가 다시 연다."""
    hospital, item = _seed_published(pg_session, certified=False)
    calls: list[str] = []

    async def _reject_once(image_url, *, content_type, topic, hospital_id=None):
        calls.append(topic)
        raise ImagePolicyRejectedError("subject mismatch")

    _wire_task(monkeypatch, pg_session, _reject_once)
    _wire_incidents(monkeypatch, pg_session)
    rejected, rejected_worker = _claimed_run(pg_session, hospital, item)
    _run_task(item, rejected, rejected_worker)
    incident = _open_incidents(pg_session, hospital)[0]

    # 운영자가 사고를 닫고 표시도 사라진 상태(다른 subject의 성공이 지운다).
    pg_session.execute(
        update(Incident)
        .where(Incident.id == incident.id)
        .values(state=IncidentState.RECOVERED.value, recovered_at=datetime.now(timezone.utc))
    )
    recertification.clear_marker(pg_session, item_id=item.id, title=item.title)
    pg_session.commit()

    runs = autonomous_recovery._recertify_runs_by_item(pg_session, [item])[str(item.id)]
    later = datetime.now(timezone.utc) + recertification.RETRY_COOLDOWN
    visible = autonomous_recovery._visible_block_incidents(pg_session, [item])
    assert recertification.incident_dedupe_key(item.id, _subject(item)) not in visible
    assert recertification.sweep_may_dispatch(
        runs, _subject(item), now=later, block_visible=False
    )

    free, free_worker = _claimed_run(
        pg_session,
        hospital,
        item,
        key=recertification.sweep_key(item.id, _subject(item), 2),
    )
    _run_task(item, free, free_worker)

    pg_session.expire_all()
    assert calls == [item.title]  # 무료 실행이다
    assert free.safe_error_code == recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED
    reopened = pg_session.get(Incident, incident.id)
    assert reopened.state == IncidentState.OPEN.value
    assert reopened.episode_seq == 2
    assert _marker(pg_session, item)["blocked"] is True
