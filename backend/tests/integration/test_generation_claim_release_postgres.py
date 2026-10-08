"""끝난 생성 워커는 자기 claim을 반드시 푼다 — 실제 UPDATE 술어로 확인한다.

배경: per-item 태스크의 finally는 `_needs_generation_recovery()`에 걸리는 행의 claim만
풀었다. 본문·이미지 URL·검수 시각은 있는데 내용 hash가 없는 행처럼 복구 필터 밖의
이유로 막힌 채 끝나면 claim이 2시간 동안 살아 있는 것처럼 남고, 07:45 게이트는 그 행을
"워커가 쓰는 중"으로 보고 기록·인시던트·요약 없이 건너뛴다.

- 성공·건너뜀·실패·예외(트랜잭션이 깨진 예외 포함) 어느 종료든 claim이 NULL이 된다.
- 다른 워커가 새로 잡은 claim은 토큰이 달라 지우지 않는다.
- 해제는 종료 시점에만 일어난다 — 본문 write-back 뒤의 이미지 write-back이 같은
  토큰을 쓸 수 있어야 한다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRunState
from app.workers import generation_retry_policy, nightly_generation_batch, tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY, fresh_generation_attempt
from app.workers.generation_run_control import GenerationItemState
from app.workers.nightly_generation_batch import (
    _needs_generation_recovery,
    claim_generation_lease,
    write_back_generated_content,
    write_back_generated_image,
)

KST = ZoneInfo("Asia/Seoul")
SLOT = date(2026, 9, 16)
CLAIMED_AT = datetime(2026, 9, 16, 7, 10, tzinfo=KST)  # 07:00 스윕의 글 단위 태스크
GATE_AT = datetime(2026, 9, 16, 7, 45, tzinfo=KST)


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


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
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


@pytest.fixture
def worker(pg_session, monkeypatch):
    """per-item 태스크 껍데기만 남긴다 — claim·해제는 실제 SQL, 생성 몸통은 테스트가 정한다."""

    finished: list[OperationRunState] = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks, "_resolve_claimed_item_run", lambda *_args: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setattr(tasks, "GenerationItemRecorder", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        tasks,
        "_finish_claimed_item_run",
        lambda _db, _task, _item_id, _run, state, **_kwargs: finished.append(state),
    )
    _freeze(monkeypatch, CLAIMED_AT)
    return finished


def _seed_finished_blocked_item(db) -> uuid.UUID:
    """워커가 끝난 뒤에도 금지 표현 차단을 보고해야 하는 행."""

    hospital = Hospital(
        name="claim해제의원",
        slug=f"claim-release-{uuid.uuid4().hex[:8]}",
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
        body="이 치료는 완치를 약속합니다.",
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
        image_url="https://cdn.example/generated.png",
        image_policy_verified_at=datetime(2026, 9, 16, 7, 5, tzinfo=KST),
        image_content_hash=None,
        content_philosophy_id=philosophy.id,
        essence_status="ALIGNED",
        essence_check_summary={GENERATION_ATTEMPT_KEY: fresh_generation_attempt()},
        scheduled_date=SLOT,
        status=ContentStatus.DRAFT,
    )
    db.add(item)
    db.commit()
    return item.id


def _claim_fields(db, item_id):
    return db.execute(
        select(ContentItem.generation_claimed_at, ContentItem.generation_claim_token).where(
            ContentItem.id == item_id
        )
    ).one()


def _is_recovery_candidate(db, item_id) -> bool:
    return (
        db.execute(
            select(ContentItem.id).where(ContentItem.id == item_id, _needs_generation_recovery())
        ).first()
        is not None
    )


def _returns(state):
    def _run(*_args, **_kwargs):
        return state, None, None

    return _run


def _raises(*_args, **_kwargs):
    raise RuntimeError("provider exploded")


def _breaks_the_transaction(db, *_args, **_kwargs):
    # 세션을 "rollback 전에는 아무것도 못 하는" 상태로 남긴 채 예외로 끝난다.
    db.execute(text("SELECT 1 / 0"))


EXITS = [
    pytest.param(_returns(GenerationItemState.SUCCEEDED), None, id="succeeded"),
    pytest.param(_returns(GenerationItemState.SKIPPED), None, id="skipped"),
    pytest.param(_returns(GenerationItemState.FAILED), None, id="failed"),
    pytest.param(_returns(GenerationItemState.PARTIAL), None, id="partial"),
    pytest.param(_raises, RuntimeError, id="exception"),
    pytest.param(_breaks_the_transaction, Exception, id="broken-transaction"),
]


@pytest.mark.parametrize(("body", "raised"), EXITS)
def test_a_finished_worker_releases_its_claim_outside_the_recovery_filter(
    pg_session, worker, monkeypatch, body, raised
):
    item_id = _seed_finished_blocked_item(pg_session)
    assert not _is_recovery_candidate(pg_session, item_id)  # 시나리오의 전제
    monkeypatch.setattr(tasks, "_run_generation_item", body)

    if raised is None:
        tasks.generate_claimed_content_item.run(str(item_id), None)
    else:
        with pytest.raises(raised):
            tasks.generate_claimed_content_item.run(str(item_id), None)

    pg_session.rollback()
    assert tuple(_claim_fields(pg_session, item_id)) == (None, None)


def test_a_redelivered_token_is_released_the_same_way(pg_session, worker, monkeypatch):
    """배치가 토큰을 넘긴 배포(`load_claimed_generation_item` 경로)도 같은 종료 해제를 쓴다."""

    item_id = _seed_finished_blocked_item(pg_session)
    _item, token = claim_generation_lease(pg_session, item_id)
    monkeypatch.setattr(tasks, "_run_generation_item", _returns(GenerationItemState.FAILED))

    tasks.generate_claimed_content_item.run(str(item_id), str(token))

    assert worker == [OperationRunState.FAILED]
    assert tuple(_claim_fields(pg_session, item_id)) == (None, None)


def test_run_resolution_failure_still_releases_the_claim(pg_session, worker, monkeypatch):
    """lease를 잡은 뒤 실행 기록을 만들다 죽어도 claim을 TTL까지 남기지 않는다."""

    item_id = _seed_finished_blocked_item(pg_session)
    monkeypatch.setattr(tasks, "_resolve_claimed_item_run", _raises)
    monkeypatch.setattr(
        tasks, "_run_generation_item", lambda *_a, **_k: pytest.fail("실행 기록 없이 생성했다")
    )

    with pytest.raises(RuntimeError):
        tasks.generate_claimed_content_item.run(str(item_id), None)

    assert tuple(_claim_fields(pg_session, item_id)) == (None, None)


@pytest.mark.parametrize(
    "offset",
    [
        pytest.param(timedelta(minutes=3), id="later"),
        # 같은 시각에 찍힌 claim도 토큰이 다르면 남의 것이다 — 소유의 정본은 토큰이다.
        pytest.param(timedelta(0), id="same-instant"),
    ],
)
def test_a_finished_worker_never_clears_a_newer_claim(pg_session, worker, monkeypatch, offset):
    item_id = _seed_finished_blocked_item(pg_session)
    newer = uuid.uuid4()
    newer_at = CLAIMED_AT + offset

    def _taken_over(db, _recorder, item, *_args, **_kwargs):
        # 이 워커가 도는 사이 다른 소유자가 lease를 넘겨받았다(만료 인수·운영자 재시도 등).
        db.execute(
            update(ContentItem)
            .where(ContentItem.id == item.id)
            .values(generation_claim_token=newer, generation_claimed_at=newer_at)
        )
        db.commit()
        return GenerationItemState.SUCCEEDED, None, None

    monkeypatch.setattr(tasks, "_run_generation_item", _taken_over)

    tasks.generate_claimed_content_item.run(str(item_id), None)

    claimed_at, token = _claim_fields(pg_session, item_id)
    assert token == newer
    assert claimed_at == newer_at


def test_the_morning_gate_processes_a_row_whose_worker_has_finished(
    pg_session, worker, monkeypatch
):
    """07:00 태스크가 끝난 뒤의 07:45 게이트는 그 행을 "작업 중"으로 건너뛰지 않는다."""

    item_id = _seed_finished_blocked_item(pg_session)
    monkeypatch.setattr(tasks, "_run_generation_item", _returns(GenerationItemState.FAILED))
    tasks.generate_claimed_content_item.run(str(item_id), None)

    incidents: list[tuple[uuid.UUID, str]] = []

    async def capture_incident(**kwargs):
        incidents.append((kwargs["item_id"], kwargs["code"]))

    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    monkeypatch.setattr(
        tasks,
        "ensure_publication_block_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4()),
    )
    monkeypatch.setattr(tasks, "enqueue_generation_blocked_digest_sync", lambda *_a: None)
    _freeze(monkeypatch, GATE_AT)

    tasks._page_morning_stored_publication_gates(pg_session, now_kst=arrow.get(GATE_AT))

    assert [row_id for row_id, _code in incidents] == [item_id]


def test_the_text_write_back_keeps_the_claim_for_the_image_write_back(pg_session):
    """해제는 종료 시점의 일이다. 본문 저장이 claim을 풀면 같은 토큰의 이미지 저장이 0행이 된다."""

    item_id = _seed_finished_blocked_item(pg_session)
    item, token = claim_generation_lease(pg_session, item_id, now=CLAIMED_AT.astimezone(UTC))

    assert (
        write_back_generated_content(
            pg_session,
            item_id=item_id,
            expected_revision=item.content_revision,
            expected_claim_token=token,
            values={"title": "새 제목", "body": "새 본문", "image_url": None},
        )
        == 1
    )
    pg_session.commit()
    assert _claim_fields(pg_session, item_id).generation_claim_token == token

    pg_session.refresh(item)
    assert (
        write_back_generated_image(
            pg_session,
            item_id=item_id,
            expected_title="새 제목",
            expected_revision=item.content_revision,
            expected_claim_token=token,
            values={"image_url": "https://cdn.example/new.png"},
        )
        == 1
    )
    pg_session.commit()
    assert tuple(_claim_fields(pg_session, item_id)) == (None, None)


def test_a_refused_image_write_back_releases_the_image_task_claim(pg_session, monkeypatch):
    """단독 이미지 태스크: 생성 도중 제목이 바뀌어 저장이 거절돼도 자기 claim은 푼다."""

    item_id = _seed_finished_blocked_item(pg_session)
    finished: list[OperationRunState] = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(
        tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setattr(
        tasks,
        "finish_explicit_run",
        lambda _db, _task, _item_id, state, **_kwargs: finished.append(state),
    )

    async def _generate_while_the_title_changes(*_args, **_kwargs):
        # 다른 세션의 편집이다 — 이 태스크의 추적 객체에는 반영되지 않는다.
        pg_session.execute(
            update(ContentItem)
            .where(ContentItem.id == item_id)
            .values(title="운영자가 고친 제목", content_revision=ContentItem.content_revision + 1)
            .execution_options(synchronize_session=False)
        )
        pg_session.commit()
        return "https://cdn.example/late.png", "prompt"

    monkeypatch.setattr(tasks, "generate_image", _generate_while_the_title_changes)
    _freeze(monkeypatch, CLAIMED_AT)

    tasks.generate_content_image.run(str(item_id))

    assert finished == [OperationRunState.CANCELLED]
    assert tuple(_claim_fields(pg_session, item_id)) == (None, None)
