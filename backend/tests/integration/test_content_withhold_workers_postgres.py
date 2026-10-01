"""비공개(보존) 글과 자동 발행 보류 — 워커 경로를 실제 Postgres로 검증한다.

- WITHHELD 글은 재생성·이미지 생성 태스크가 공급자 호출 전에 물러나고, IndexNow 일괄
  제출·공개 이미지 교체·자동 발행 due(7일 catch-up 포함)에서 빠진다.
- `AUTO_PUBLISH_HOLD_HOSPITALS`는 공용 due 조건에서 걸러지고, 후보 목록을 만든 뒤
  보류가 켜져도 `_auto_publish_one`이 행 잠금 뒤에 다시 보고 물러난다.
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import arrow
import pytest
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRunState
from app.services import indexnow
from app.services import post_publish_review_policy as policy
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.workers import published_image_refresh, tasks


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


@pytest.fixture
def worker_db(pg_session, monkeypatch):
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    return pg_session


@pytest.fixture
def hold(monkeypatch):
    def set_hold(value: str) -> None:
        monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", value)

    set_hold("")
    return set_hold


def _today() -> date:
    return arrow.now("Asia/Seoul").date()


def _hospital(db, name: str) -> tuple[Hospital, ContentSchedule]:
    hospital = Hospital(
        name=name,
        slug=f"withhold-worker-{uuid.uuid4().hex[:10]}",
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
    db.add(schedule)
    db.flush()
    return hospital, schedule


def _item(db, hospital, schedule, *, status, scheduled_date, sequence_no=1, **extra):
    published = status in (ContentStatus.PUBLISHED, ContentStatus.WITHHELD)
    title = f"글 {uuid.uuid4().hex[:6]}"
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=sequence_no,
        total_count=12,
        title=title,
        body="본문",
        scheduled_date=scheduled_date,
        status=status,
        published_at=datetime(2026, 9, 10, tzinfo=timezone.utc) if published else None,
        published_by="auto" if published else None,
        first_published_at=datetime(2026, 9, 10, tzinfo=timezone.utc) if published else None,
        first_published_by="auto" if published else None,
        **extra,
    )
    db.add(item)
    db.flush()
    return item


def _due_ids(db, today) -> set[uuid.UUID]:
    return set(db.execute(tasks._auto_publish_due_stmt(today)).scalars().all())


def _pre_hotfix_due_ids(db, today) -> set[uuid.UUID]:
    """5862e557의 due 문장 그대로 — 보류가 꺼져 있으면 결과가 같아야 한다."""
    stmt = (
        select(ContentItem.id)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            and_(
                ContentItem.scheduled_date <= today,
                ContentItem.scheduled_date >= policy.auto_publish_catchup_start(today),
                ContentItem.status.in_(policy.AUTO_PUBLISHABLE_STATUSES),
            ),
            policy.publicly_operational_hospital_predicate(),
        )
    )
    return set(db.execute(stmt).scalars().all())


@pytest.fixture
def due_fixture(worker_db):
    """병원 A·B, 오늘·3일 전(catch-up 창 안) 초안과 비공개(보존) 글."""
    db = worker_db
    today = _today()
    a, a_schedule = _hospital(db, "보류A병원")
    b, b_schedule = _hospital(db, "보류B병원")
    rows = {
        "a_today": _item(db, a, a_schedule, status=ContentStatus.DRAFT, scheduled_date=today),
        "a_catchup": _item(
            db, a, a_schedule, status=ContentStatus.READY,
            scheduled_date=today - timedelta(days=3), sequence_no=2,
        ),
        "a_withheld_today": _item(
            db, a, a_schedule, status=ContentStatus.WITHHELD, scheduled_date=today, sequence_no=3
        ),
        "a_withheld_catchup": _item(
            db, a, a_schedule, status=ContentStatus.WITHHELD,
            scheduled_date=today - timedelta(days=3), sequence_no=4,
        ),
        "b_today": _item(db, b, b_schedule, status=ContentStatus.DRAFT, scheduled_date=today),
    }
    return today, a, b, rows


# ── 1·3. 자동 발행 due·7일 catch-up에서 WITHHELD 제외 ─────────────────────


def test_withheld_content_is_never_due_for_auto_publish_including_catchup(
    worker_db, hold, due_fixture
):
    today, _a, _b, rows = due_fixture

    due = _due_ids(worker_db, today)

    assert rows["a_withheld_today"].id not in due
    assert rows["a_withheld_catchup"].id not in due
    assert due >= {rows["a_today"].id, rows["a_catchup"].id, rows["b_today"].id}


def test_auto_publish_one_walks_away_from_a_withheld_row(worker_db, hold, due_fixture, caplog):
    _today_, _a, _b, rows = due_fixture
    item = rows["a_withheld_today"]

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        assert tasks._auto_publish_one(item.id) is None

    worker_db.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert any("reason=not_publishable_status" in r.getMessage() for r in caplog.records)


# ── 6. 자동 발행 보류 ─────────────────────────────────────────────────


def test_hold_off_returns_exactly_the_pre_hotfix_due_rows(worker_db, hold, due_fixture):
    today, _a, _b, rows = due_fixture
    hold("")

    due = _due_ids(worker_db, today)

    assert due == _pre_hotfix_due_ids(worker_db, today)
    assert {rows["a_today"].id, rows["a_catchup"].id, rows["b_today"].id} <= due


def test_hold_star_leaves_nothing_due(worker_db, hold, due_fixture):
    today, _a, _b, rows = due_fixture
    hold("*")

    due = _due_ids(worker_db, today)

    assert due == set()


def test_hold_list_excludes_only_the_listed_hospitals(worker_db, hold, due_fixture):
    today, a, _b, rows = due_fixture
    hold(f" {a.id} , not-a-uuid ,")

    due = _due_ids(worker_db, today)

    assert rows["a_today"].id not in due
    assert rows["a_catchup"].id not in due
    assert rows["b_today"].id in due
    # 다른 병원(다른 테스트가 남긴 행 포함)은 보류 전과 같다.
    hold("")
    unheld = _due_ids(worker_db, today)
    assert due == {row_id for row_id in unheld if row_id not in {
        rows["a_today"].id, rows["a_catchup"].id
    }}


@pytest.mark.parametrize("hold_value", ["*", "{a}"])
def test_auto_publish_one_rechecks_the_hold_after_taking_the_row_lock(
    worker_db, hold, due_fixture, caplog, monkeypatch, hold_value
):
    today, a, _b, rows = due_fixture
    item = rows["a_today"]
    # 후보 목록은 보류가 꺼져 있을 때 만들어졌다.
    assert item.id in _due_ids(worker_db, today)
    hold(hold_value.format(a=a.id))

    def reached_the_gate(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError("보류 중인 글은 발행 게이트까지 가면 안 된다")

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", reached_the_gate)

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        assert tasks._auto_publish_one(item.id) is None

    worker_db.refresh(item)
    assert item.status == ContentStatus.DRAFT
    assert item.published_at is None
    skipped = [r.getMessage() for r in caplog.records if "auto publish skipped" in r.getMessage()]
    assert len(skipped) == 1
    assert "reason=auto_publish_hold" in skipped[0]
    assert str(item.id) in skipped[0]


def test_auto_publish_one_ignores_a_hold_on_another_hospital(
    worker_db, hold, due_fixture, caplog, monkeypatch
):
    _today_, _a, b, rows = due_fixture
    hold(str(b.id))
    gate_calls: list = []

    def stop_at_the_gate(db, hospital_id):
        gate_calls.append(hospital_id)
        raise RuntimeError("stop after the hold check")

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", stop_at_the_gate)

    with pytest.raises(RuntimeError, match="stop after the hold check"):
        tasks._auto_publish_one(rows["a_today"].id)

    assert gate_calls == [rows["a_today"].hospital_id]


# ── 1. 재생성·이미지 생성 태스크: 공급자 호출 전에 물러난다 ─────────────────


@pytest.fixture
def finished_runs(monkeypatch):
    states: list = []

    def record(db, task, item_id, state, **kwargs):
        states.append((item_id, state))
        return None

    monkeypatch.setattr(tasks, "finish_explicit_run", record)
    return states


def test_regenerate_task_cancels_withheld_content_before_generation(
    worker_db, finished_runs, monkeypatch
):
    hospital, schedule = _hospital(worker_db, "재생성병원")
    item = _item(
        worker_db, hospital, schedule, status=ContentStatus.WITHHELD, scheduled_date=_today()
    )

    def generation(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError("WITHHELD 글을 다시 쓰면 안 된다")

    monkeypatch.setattr(tasks, "_generate_single_content_item", generation)

    tasks.regenerate_content_item.run(str(item.id))

    assert finished_runs == [(item.id, OperationRunState.CANCELLED)]
    worker_db.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.body == "본문"


def test_image_task_cancels_withheld_content_before_any_provider_work(
    worker_db, finished_runs, monkeypatch
):
    hospital, schedule = _hospital(worker_db, "이미지병원")
    item = _item(
        worker_db, hospital, schedule, status=ContentStatus.WITHHELD, scheduled_date=_today(),
        image_url="gs://reputation-images/content/" + "d" * 64 + "-kept.png",
    )

    def tripwire(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError("WITHHELD 글의 이미지 작업은 시작하지 않는다")

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", tripwire)
    monkeypatch.setattr(tasks, "claim_generation_lease", tripwire)
    monkeypatch.setattr(tasks, "generate_image", tripwire)

    tasks.generate_content_image.run(str(item.id))

    assert finished_runs == [(item.id, OperationRunState.CANCELLED)]
    worker_db.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.image_url.endswith("-kept.png")


# ── 1. IndexNow 일괄 제출·공개 이미지 교체 스윕에서 제외 ─────────────────────


def test_indexnow_backfill_skips_withheld_content(worker_db, monkeypatch):
    hospital, schedule = _hospital(worker_db, "색인병원")
    published = _item(
        worker_db, hospital, schedule, status=ContentStatus.PUBLISHED, scheduled_date=_today()
    )
    withheld = _item(
        worker_db, hospital, schedule, status=ContentStatus.WITHHELD, scheduled_date=_today(),
        sequence_no=2,
    )
    submitted: list = []
    original = indexnow.hospital_all_urls

    def capture(**kwargs):
        submitted.append(list(kwargs["content_ids"]))
        return original(**kwargs)

    monkeypatch.setattr(indexnow, "is_configured", lambda: True)
    monkeypatch.setattr(indexnow, "hospital_all_urls", capture)

    result = tasks.backfill_indexnow.run(hospital_id=str(hospital.id), dry_run=True)

    assert submitted == [[published.id]]
    assert withheld.id not in submitted[0]
    assert result["hospitals"][0]["contents"] == 1


def test_published_image_refresh_does_not_pick_withheld_content(worker_db):
    hospital, schedule = _hospital(worker_db, "이미지교체병원")
    lender = _item(
        worker_db, hospital, schedule, status=ContentStatus.PUBLISHED, scheduled_date=_today()
    )
    url = "https://storage.googleapis.com/reputation-images/content/" + "e" * 64 + "-lent.png"
    borrowed = {
        "image_url": url,
        "image_content_hash": image_content_hash_from_url(url),
        "image_subject_hash": image_subject_hash(ContentType.DISEASE, "빌려준 글"),
        "image_policy_version": IMAGE_POLICY_VERSION,
        "image_policy_verified_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "image_reused_from_content_id": lender.id,
    }
    visible = _item(
        worker_db, hospital, schedule, status=ContentStatus.PUBLISHED, scheduled_date=_today(),
        sequence_no=2, **borrowed,
    )
    withheld = _item(
        worker_db, hospital, schedule, status=ContentStatus.WITHHELD, scheduled_date=_today(),
        sequence_no=3, **borrowed,
    )

    candidate_ids = {
        item.id for item in worker_db.execute(published_image_refresh._reused_image_stmt())
        .unique().scalars().all()
    }

    assert visible.id in candidate_ids
    assert withheld.id not in candidate_ids
