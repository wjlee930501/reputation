"""SQL claim 술어가 Python claim 판정(`generation_claim_is_active`)과 같은 행을 고른다 — 실제 Postgres.

`generation_claim_is_active`는 토큰·claim 시각이 모두 있고 claim 시각이 TTL 안(경계 포함)일
때만 살아 있다고 본다. `claim_generation_lease`가 그 판정으로 새 claim을 허락하므로, 로더·백로그
복구·주제 교체·게시 이미지 교체의 "claim할 수 있는 행" 술어는 그 정확한 부정이어야 하고, 멈춘
claim 조회는 그 판정 그대로여야 한다. 어긋나면 Python은 비어 있다고 보는 슬롯을 로더가 영영
집지 않는다(토큰 NULL + 최근 claim 시각) — 07:45·08:00은 그 행을 "작업 중"으로 보지 않는데
생성도 되지 않는다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.workers import (
    nightly_generation_batch,
    published_image_refresh,
    tasks,
    topic_swap_fallback,
)
from app.workers.nightly_generation_batch import (
    NIGHTLY_GENERATION_CLAIM_TTL_HOURS,
    _nightly_generation_claim_filter,
    generation_claim_is_active,
    load_stuck_claims,
    write_back_published_image,
)
from tests.integration.test_published_image_refresh_postgres import _borrowed_item
from tests.integration.test_topic_swap_fallback_postgres import _seed_hospital, _seed_item

NOW = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)  # 10:00 KST
SLOT = date(2026, 9, 16)
CUTOFF = NOW - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
ONE_MICROSECOND = timedelta(microseconds=1)

# (이름, 토큰 있음, claim 시각) — 경계는 TTL 정각(살아 있음)과 그보다 1µs 이전(만료)이다.
CLAIM_SHAPES = [
    ("no-claim", False, None),
    ("token-only", True, None),  # 운영센터의 claim 해제는 claim 시각만 비운다(operations.py)
    ("tokenless-recent", False, NOW - timedelta(minutes=10)),
    ("tokenless-old", False, NOW - timedelta(hours=3)),
    ("tokenless-past-grace", False, NOW - timedelta(minutes=45)),
    ("live-recent", True, NOW - timedelta(minutes=10)),
    ("live-past-grace", True, NOW - timedelta(minutes=45)),
    ("live-at-cutoff", True, CUTOFF),
    ("expired-by-1us", True, CUTOFF - ONE_MICROSECOND),
    ("expired-old", True, NOW - timedelta(hours=3)),
]


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

    for module in (tasks, nightly_generation_batch, published_image_refresh):
        monkeypatch.setattr(module, "datetime", _Frozen)


def _seed_shapes(pg_conn) -> dict[str, uuid.UUID]:
    """같은 병원의 빈 슬롯(로더 후보)을 claim 모양마다 하나씩 — 시도 기록은 없다."""

    hospital_id = _seed_hospital(pg_conn)
    return {
        name: _seed_item(
            pg_conn,
            hospital_id,
            sequence_no=index + 1,
            title=None,
            image_url=None,
            summary="{}",
            claim_token=uuid.uuid4() if has_token else None,
            claimed_at=claimed_at,
        )
        for index, (name, has_token, claimed_at) in enumerate(CLAIM_SHAPES)
    }


def _python_inactive(pg_session, ids: dict[str, uuid.UUID]) -> set[str]:
    rows = {
        row.id: row
        for row in pg_session.execute(
            select(ContentItem).where(ContentItem.id.in_(ids.values()))
        ).scalars()
    }
    return {name for name, item_id in ids.items() if not generation_claim_is_active(rows[item_id], now=NOW)}


def test_the_shapes_cover_both_python_verdicts(pg_conn, pg_session):
    ids = _seed_shapes(pg_conn)
    assert _python_inactive(pg_session, ids) == {
        "no-claim",
        "token-only",
        "tokenless-recent",
        "tokenless-old",
        "tokenless-past-grace",
        "expired-by-1us",
        "expired-old",
    }


def test_a_tokenless_recent_claim_is_claimed_by_the_loader(pg_conn, pg_session, monkeypatch):
    """토큰 NULL + 10분 전 claim 시각 — Python은 살아 있지 않다고 본다(`claim_generation_lease`가
    인수한다). 로더도 이 슬롯을 claim해야 한다. 종전 필터는 claim 시각만 봐 건너뛰었다."""

    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(
        pg_conn,
        hospital_id,
        title=None,
        image_url=None,
        summary="{}",
        claim_token=None,
        claimed_at=NOW - timedelta(minutes=10),
    )
    row = pg_session.get(ContentItem, item_id)
    assert not generation_claim_is_active(row, now=NOW)
    _freeze(monkeypatch, NOW)

    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session, SLOT - timedelta(days=7), SLOT + timedelta(days=2)
    )

    assert [item.id for item in claimed] == [item_id]
    pg_session.refresh(row)
    assert row.generation_claim_token is not None and row.generation_claimed_at == NOW


def test_the_loader_claims_exactly_the_rows_python_calls_inactive(pg_conn, pg_session, monkeypatch):
    """경계 포함 — TTL 정각의 claim은 살아 있어 집지 않고, 1µs 더 오래된 claim은 집는다."""

    ids = _seed_shapes(pg_conn)
    expected = _python_inactive(pg_session, ids)
    _freeze(monkeypatch, NOW)

    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session, SLOT - timedelta(days=7), SLOT + timedelta(days=2)
    )

    by_id = {item_id: name for name, item_id in ids.items()}
    assert {by_id[item.id] for item in claimed} == expected
    assert "live-at-cutoff" not in expected and "expired-by-1us" in expected


def test_the_claim_filter_itself_matches_python_at_the_cutoff(pg_conn, pg_session):
    ids = _seed_shapes(pg_conn)

    selected = set(
        pg_session.execute(
            select(ContentItem.id).where(
                ContentItem.id.in_(ids.values()), _nightly_generation_claim_filter(CUTOFF)
            )
        ).scalars()
    )

    by_id = {item_id: name for name, item_id in ids.items()}
    assert {by_id[item_id] for item_id in selected} == _python_inactive(pg_session, ids)


def test_topic_swap_treats_a_token_without_a_claim_time_as_inactive(pg_conn, pg_session):
    """운영센터의 claim 해제(operations.py)는 claim 시각만 비우고 토큰을 남긴다. Python은 그 행을
    살아 있지 않다고 보므로(로더도 claim한다) 주제 교체의 비활성 술어도 같아야 한다."""

    ids = _seed_shapes(pg_conn)

    selected = set(
        pg_session.execute(
            select(ContentItem.id).where(
                ContentItem.id.in_(ids.values()),
                topic_swap_fallback._inactive_claim_filter(CUTOFF),
            )
        ).scalars()
    )

    by_id = {item_id: name for name, item_id in ids.items()}
    assert {by_id[item_id] for item_id in selected} == _python_inactive(pg_session, ids)


def test_an_exhausted_slot_whose_claim_was_released_by_the_operator_is_swapped(pg_conn, pg_session):
    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id, claim_token=uuid.uuid4(), claimed_at=None)

    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session, window_start=SLOT - timedelta(days=7), window_end=SLOT, now=NOW
    )

    assert report.swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)
    assert row.topic_swap_history and row.generation_claim_token is None


def test_stuck_claims_are_exactly_the_live_claims_past_the_in_flight_grace(
    pg_conn, pg_session, monkeypatch
):
    """멈춘 claim 보고는 살아 있는 claim만 센다 — 토큰 없는 행은 로더가 집으므로 '멈춤'이 아니다."""

    ids = _seed_shapes(pg_conn)
    _freeze(monkeypatch, NOW)

    stuck = load_stuck_claims(pg_session, SLOT - timedelta(days=7), SLOT + timedelta(days=2))

    by_id = {item_id: name for name, item_id in ids.items()}
    # 유예(30분)보다 오래됐고 TTL 안인 살아 있는 claim만 — 10분 전 claim은 진행 중인 일감이다.
    assert {by_id[item.id] for item in stuck} == {"live-past-grace", "live-at-cutoff"}


def _set_claim(pg_session, item_id, *, token, claimed_at) -> None:
    pg_session.execute(
        update(ContentItem)
        .where(ContentItem.id == item_id)
        .values(generation_claim_token=token, generation_claimed_at=claimed_at)
    )
    pg_session.commit()


@pytest.mark.parametrize(("name", "has_token", "claimed_at"), CLAIM_SHAPES, ids=[s[0] for s in CLAIM_SHAPES])
def test_published_image_refresh_claims_exactly_the_rows_python_calls_inactive(
    pg_conn, pg_session, monkeypatch, name, has_token, claimed_at
):
    item_id, _lender = _borrowed_item(pg_conn)
    _set_claim(
        pg_session, item_id, token=uuid.uuid4() if has_token else None, claimed_at=claimed_at
    )
    row = pg_session.get(ContentItem, item_id)
    inactive = not generation_claim_is_active(row, now=NOW)
    _freeze(monkeypatch, NOW)

    token = published_image_refresh._claim_image_refresh(pg_session, item_id)

    assert (token is not None) == inactive, name


@pytest.mark.parametrize(
    ("claimed_at", "written"),
    [(CUTOFF, 1), (CUTOFF - ONE_MICROSECOND, 0)],
    ids=["lease-at-cutoff-is-still-mine", "lease-expired-by-1us"],
)
def test_the_published_image_write_back_accepts_a_lease_exactly_at_the_cutoff(
    pg_conn, pg_session, monkeypatch, claimed_at, written
):
    """소유자의 lease도 같은 경계다 — TTL 정각에는 아무도 인수할 수 없으므로(로더·`claim_generation_lease`)
    소유자의 저장을 거절하지 않는다."""

    item_id, _lender = _borrowed_item(pg_conn)
    token = uuid.uuid4()
    _set_claim(pg_session, item_id, token=token, claimed_at=claimed_at)
    row = pg_session.get(ContentItem, item_id)
    _freeze(monkeypatch, NOW)

    assert (
        write_back_published_image(
            pg_session,
            item_id=item_id,
            expected_title=row.title,
            expected_revision=row.content_revision,
            expected_claim_token=token,
            values={"image_prompt": "교체"},
        )
        == written
    )
    assert pg_conn.execute(
        text("SELECT generation_claim_token IS NULL FROM content_items WHERE id=:id"),
        {"id": item_id},
    ).scalar_one() is bool(written)


@pytest.mark.parametrize(
    ("claimed_at", "recorded"),
    [(CUTOFF, True), (CUTOFF - ONE_MICROSECOND, False)],
    ids=["lease-at-cutoff-is-still-mine", "lease-expired-by-1us"],
)
def test_the_published_image_failure_record_accepts_a_lease_exactly_at_the_cutoff(
    pg_conn, pg_session, monkeypatch, claimed_at, recorded
):
    """실패 기록도 같은 소유 경계다 — 경계 시각의 lease면 이 실행의 시도를 남기고, 만료됐으면 남기지
    않고 자기 claim만 푼다. 어느 쪽이든 claim은 남지 않는다."""

    item_id, _lender = _borrowed_item(pg_conn)
    token = uuid.uuid4()
    _set_claim(pg_session, item_id, token=token, claimed_at=claimed_at)
    row = pg_session.get(ContentItem, item_id)
    _freeze(monkeypatch, NOW)

    published_image_refresh._remember_claimed_failure(
        pg_session, row, token, row.title, row.content_revision, "IMAGE_GENERATION_FAILED"
    )
    pg_session.commit()

    pg_session.expire_all()
    row = pg_session.get(ContentItem, item_id)
    stored = (row.essence_check_summary or {}).get("generation_attempt") or {}
    assert (stored.get("reason") == "IMAGE_GENERATION_FAILED") is recorded
    assert (row.generation_claim_token, row.generation_claimed_at) == (None, None)
