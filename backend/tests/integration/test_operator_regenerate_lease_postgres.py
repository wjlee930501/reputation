"""운영자 재생성은 글 단위 lease를 잡는다 — 실제 claim·write-back·해제 SQL로 확인한다.

배경: `regenerate_content_item`은 lease 없이 행에 남은 토큰(대개 NULL)으로 썼고, 이미지
write-back이 claim 필드를 비웠다. 그래서 운영자 재생성이 도는 사이 야간 워커가 같은 슬롯을
claim해 작가를 한 번 더 사고, 운영자의 이미지 저장이 그 워커의 살아 있는 claim을 지웠다.
만료 claim을 읽은 채 시작한 재생성은 그 사이 스윕이 다시 claim하면 0행으로 버려졌다.

- 살아 있는 다른 claim이 있으면 작가를 부르지 않고 CANCELLED(GENERATION_LEASE_ACTIVE)로 끝나며
  그 claim을 건드리지 않는다.
- 재생성이 도는 동안 다른 작업은 같은 글을 claim하지 못한다. 끝나면 자기 claim만 푼다.
- 만료 claim은 재생성이 인수한다.
- 단독 이미지 태스크의 거절된 write-back은 다른 워커의 새 claim을 지우지 않는다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRunState
from app.workers import generation_retry_policy, nightly_generation_batch, tasks
from app.workers.nightly_generation_batch import claim_generation_lease

KST = ZoneInfo("Asia/Seoul")
SLOT = date(2026, 9, 16)
PRESS_AT = datetime(2026, 9, 16, 7, 50, tzinfo=KST)
_IMAGE_URL = "gs://reputation-images/content/" + "c" * 64 + "-content.png"


class _SessionProxy:
    """`with SyncSessionLocal() as db:`를 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def pg_session(pg_conn):
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def other_worker(pg_conn):
    """같은 DB의 다른 워커 — 별도 세션(identity map)이다."""

    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


def _seed_empty_slot(db) -> tuple[uuid.UUID, HospitalContentPhilosophy]:
    hospital = Hospital(
        name="재생성lease의원",
        slug=f"regenerate-lease-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
        profile_complete=True,
        site_built=True,
        schedule_set=True,
    )
    db.add(hospital)
    db.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1, 3], active_from=date(2026, 9, 1)
    )
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
        evidence_map={},
        source_asset_ids=[],
        unsupported_gaps=[],
        conflict_notes=[],
    )
    db.add_all([schedule, philosophy])
    db.flush()
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="허리디스크 초기 증상",
        body=None,
        scheduled_date=SLOT,
        status=ContentStatus.DRAFT,
    )
    db.add(item)
    db.commit()
    return item.id, philosophy


def _claim_fields(db, item_id):
    return tuple(
        db.execute(
            select(ContentItem.generation_claimed_at, ContentItem.generation_claim_token).where(
                ContentItem.id == item_id
            )
        ).one()
    )


@pytest.fixture
def operator_regenerate(pg_session, monkeypatch):
    """운영자 실행 껍데기와 공급자만 바꾼다 — lease·write-back·해제는 실제 SQL이다."""

    state = SimpleNamespace(finished=[], writer_calls=[], during_writer=None)

    async def ignore(*_args, **_kwargs):
        return None

    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def writer(*, hospital, item, existing_titles, philosophy, approved_brief):
        state.writer_calls.append(item.id)
        if state.during_writer is not None:
            state.during_writer(item)
        return (
            {
                "title": "허리디스크 초기 증상과 진료 시점",
                "body": "허리 통증이 다리로 번지면 진료로 원인을 확인합니다.",
                "meta_description": "허리디스크 초기 증상을 정리했습니다.",
                "references": [
                    {"title": "질병관리청 국가건강정보포털", "url": "https://health.kdca.go.kr/x"}
                ],
            },
            SimpleNamespace(status=None, summary={}),
        )

    async def image(*_args, **_kwargs):
        return _IMAGE_URL, "prompt"

    def finish(_db, task, _item_id, run_state, **kwargs):
        state.finished.append((run_state, kwargs.get("safe_error_code")))
        return uuid.uuid4()

    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(tasks, "explicit_run_matches", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks, "finish_explicit_run", finish)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allowed)
    monkeypatch.setattr(tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tasks, "_generate_with_auto_review", writer)
    monkeypatch.setattr(tasks, "_generation_summary", lambda *_args: {})
    monkeypatch.setattr(tasks, "generate_image", image)
    monkeypatch.setattr(tasks, "open_generation_incident", ignore)
    monkeypatch.setattr(tasks, "recover_generation_incidents", ignore)
    _freeze(monkeypatch, PRESS_AT)

    def press(item_id, philosophy):
        monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
        task = tasks.regenerate_content_item
        task.push_request(
            id="operator-worker",
            headers={"operation_run_id": str(uuid.uuid4())},
            operation_run_claim_version=2,
        )
        try:
            task.run(str(item_id))
        finally:
            task.pop_request()

    state.press = press
    return state


def test_a_live_nightly_claim_cancels_the_operator_run_and_is_kept(pg_session, operator_regenerate):
    item_id, philosophy = _seed_empty_slot(pg_session)
    nightly_at = (PRESS_AT - timedelta(minutes=5)).astimezone(UTC)
    _item, nightly = claim_generation_lease(pg_session, item_id, now=nightly_at)

    operator_regenerate.press(item_id, philosophy)

    assert operator_regenerate.writer_calls == []
    assert operator_regenerate.finished == [
        (OperationRunState.CANCELLED, "GENERATION_LEASE_ACTIVE")
    ]
    pg_session.rollback()
    assert _claim_fields(pg_session, item_id) == (nightly_at, nightly)


def test_no_other_job_can_claim_the_slot_while_the_operator_writes(
    pg_session, other_worker, operator_regenerate
):
    """종전에는 운영자 재생성이 NULL 토큰으로 돌아 야간 claim이 끼어들었고, 운영자의 이미지
    저장이 그 claim을 지웠다."""

    item_id, philosophy = _seed_empty_slot(pg_session)
    mid_run: list = []
    operator_regenerate.during_writer = lambda _item: mid_run.append(
        claim_generation_lease(other_worker, item_id)
    )

    operator_regenerate.press(item_id, philosophy)

    assert operator_regenerate.writer_calls == [item_id]
    assert mid_run == [None]
    assert operator_regenerate.finished[0][0] in (
        OperationRunState.SUCCEEDED,
        OperationRunState.FAILED,  # 발행 준비 판정은 이 테스트의 관심사가 아니다
    )
    pg_session.rollback()
    row = pg_session.get(ContentItem, item_id, populate_existing=True)
    assert row.body == "허리 통증이 다리로 번지면 진료로 원인을 확인합니다."
    assert row.image_url == _IMAGE_URL
    assert _claim_fields(pg_session, item_id) == (None, None)


def test_an_expired_claim_is_taken_over_instead_of_being_discarded_later(
    pg_session, other_worker, operator_regenerate
):
    """종전에는 만료 claim의 토큰을 읽은 채 쓰다가, 그 사이 스윕이 다시 claim하면 0행으로 버려졌다."""

    item_id, philosophy = _seed_empty_slot(pg_session)
    dead = uuid.uuid4()
    pg_session.execute(
        update(ContentItem)
        .where(ContentItem.id == item_id)
        .values(
            generation_claim_token=dead,
            generation_claimed_at=(PRESS_AT - timedelta(hours=3)).astimezone(UTC),
        )
    )
    pg_session.commit()
    mid_run: list = []
    operator_regenerate.during_writer = lambda _item: mid_run.append(
        claim_generation_lease(other_worker, item_id)
    )

    operator_regenerate.press(item_id, philosophy)

    assert operator_regenerate.writer_calls == [item_id]
    assert mid_run == [None]
    assert operator_regenerate.finished[0][0] != OperationRunState.CANCELLED
    pg_session.rollback()
    row = pg_session.get(ContentItem, item_id, populate_existing=True)
    assert row.body == "허리 통증이 다리로 번지면 진료로 원인을 확인합니다."
    assert _claim_fields(pg_session, item_id) == (None, None)


def test_a_refused_image_write_back_keeps_another_workers_newer_claim(
    pg_session, other_worker, monkeypatch
):
    """단독 이미지 태스크의 거절 뒤 해제는 자기 토큰만 푼다 — 넘겨받은 새 소유자의 claim은 남는다."""

    item_id, philosophy = _seed_empty_slot(pg_session)
    pg_session.execute(
        update(ContentItem).where(ContentItem.id == item_id).values(body="저장된 본문")
    )
    pg_session.commit()
    newer = uuid.uuid4()
    newer_at = PRESS_AT.astimezone(UTC)
    finished: list = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "finish_explicit_run",
        lambda _db, _task, _item_id, state, **_kwargs: finished.append(state),
    )

    async def _generate_while_another_worker_takes_over(*_args, **_kwargs):
        # 이 태스크의 lease가 풀리고(OWNER 강제 해제 등) 다른 워커가 새 토큰으로 claim했다.
        other_worker.execute(
            update(ContentItem)
            .where(ContentItem.id == item_id)
            .values(generation_claim_token=newer, generation_claimed_at=newer_at)
        )
        other_worker.commit()
        return _IMAGE_URL, "prompt"

    monkeypatch.setattr(tasks, "generate_image", _generate_while_another_worker_takes_over)
    _freeze(monkeypatch, PRESS_AT)

    tasks.generate_content_image.run(str(item_id))

    assert finished == [OperationRunState.CANCELLED]
    pg_session.rollback()
    assert _claim_fields(pg_session, item_id) == (newer_at, newer)
    assert pg_session.get(ContentItem, item_id, populate_existing=True).image_url is None
