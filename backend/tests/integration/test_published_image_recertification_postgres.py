"""H-01: 공개 글 제목 편집으로 무효화된 이미지 인증을 시스템이 재검수해 복구한다.

제목 편집은 subject 인증을 지우고, 공유 판정(`assess_public_visibility`)은 그 글을
IMAGE_NOT_CERTIFIED로 숨긴다. 아무도 재인증하지 않으면 사람이 손을 대야 공개가
돌아온다. 여기서 고정하는 것은 두 가지다 — 재인증 write-back이 **그 판·그 제목**에만
쓰이는 것, 그리고 태스크가 저장된 바이트만 재검수해 공개 판을 건드리지 않는 것.
"""

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import OperationRunState
from app.services.content_publication import image_certification_current
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.image_policy import ImagePolicyRejectedError
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


def test_task_marks_rejection_as_operator_incident_without_touching_the_row(
    pg_session, monkeypatch
):
    _hospital, item = _seed_published(pg_session, certified=False)

    async def _reject(image_url, *, content_type, topic, hospital_id=None):
        raise ImagePolicyRejectedError("subject mismatch")

    submitted, revalidated = _wire_task(monkeypatch, pg_session, _reject)
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
    assert not image_certification_current(item)
    assert item.status == ContentStatus.PUBLISHED
    assert finished == [(OperationRunState.FAILED, "PUBLISHED_IMAGE_RECERTIFY_REJECTED")]
    assert submitted == [] and revalidated == []


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
