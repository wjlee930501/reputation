"""빌린 대표 이미지를 그 글의 주제 이미지로 바꿔 다는 사후 스윕.

빌린 인증은 유효하지만 최종 상태는 아니다. 이 스윕이 교체를 끝내야 글이 자기 주제의
이미지를 갖는다. 여기서 고정하는 것은 넷이다 — 성공했을 때 marker가 풀리고 **판이
그대로**인 것, 실패했을 때 아무것도 바뀌지 않고 시도만 기록되는 것, 예산이 아직
돌아오지 않은 글을 사지 않는 것, 그리고 교체 중 편집이 끼어들면 버리는 것.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentType
from app.services import image_engine, site_revalidate
from app.services.content_publication import image_certification_current
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.workers import published_image_refresh
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    read_generation_attempt,
)
from app.workers.generation_retry_policy import environment_attempt_period

_FRESH_URL = (
    "https://storage.googleapis.com/reputation-images/content/" + "b" * 64 + "-fresh.png"
)


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
def revalidated(monkeypatch):
    calls: list = []

    async def fake_revalidate(slug, content_id, **kwargs):
        calls.append((slug, content_id))
        return True

    monkeypatch.setattr(site_revalidate, "trigger_content_site_revalidate_safe", fake_revalidate)
    return calls


def _borrowed_item(pg_conn, *, title: str = "빌려 쓰는 글") -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    lender_id = uuid.uuid4()
    item_id = uuid.uuid4()
    lender_title = "빌려주는 글"
    lender_url = (
        "https://storage.googleapis.com/reputation-images/content/" + "a" * 64 + "-lent.png"
    )
    pg_conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '이미지교체병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"refresh-{uuid.uuid4().hex[:8]}"},
    )
    pg_conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 9, 1)},
    )
    insert = (
        "INSERT INTO content_items "
        "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
        " scheduled_date, status, title, body, content_revision, published_at, "
        " image_url, image_content_hash, image_subject_hash, image_policy_version, "
        " image_policy_verified_at, image_reused_from_content_id) "
        "VALUES (:id, :hid, :sid, 'DISEASE', :seq, 12, :d, 'PUBLISHED', :title, '본문', 3, "
        " :published_at, :image_url, :content_hash, :subject_hash, :policy_version, "
        " :verified_at, :reused_from)"
    )
    common = {
        "hid": hospital_id,
        "sid": schedule_id,
        "d": date(2026, 9, 10),
        "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "image_url": lender_url,
        "content_hash": image_content_hash_from_url(lender_url),
        "subject_hash": image_subject_hash(ContentType.DISEASE, lender_title),
        "policy_version": IMAGE_POLICY_VERSION,
        "verified_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    pg_conn.execute(
        text(insert),
        {**common, "id": lender_id, "seq": 1, "title": lender_title, "reused_from": None},
    )
    pg_conn.execute(
        text(insert),
        {**common, "id": item_id, "seq": 2, "title": title, "reused_from": lender_id},
    )
    return item_id, lender_id


def _run(monkeypatch, pg_session, generate):
    monkeypatch.setattr(
        published_image_refresh, "SyncSessionLocal", _SessionProxy(pg_session)
    )
    monkeypatch.setattr(image_engine, "generate_image", generate)
    return published_image_refresh.refresh_reused_content_images.run()


def test_successful_replacement_clears_the_marker_without_bumping_the_revision(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id, lender_id = _borrowed_item(pg_conn)

    async def generate(*_args, **_kwargs):
        return (_FRESH_URL, "새 프롬프트")

    result = _run(monkeypatch, pg_session, generate)

    assert result["replaced"] == 1
    pg_session.expire_all()
    item = pg_session.get(ContentItem, item_id)
    assert item.image_url == _FRESH_URL
    assert item.image_reused_from_content_id is None
    # 자기 주제로 인증된 이미지가 됐고, 공개 게이트가 통과시키는 모양이다.
    assert item.image_content_hash == image_content_hash_from_url(_FRESH_URL)
    assert item.image_subject_hash == image_subject_hash(ContentType.DISEASE, item.title)
    assert item.image_policy_version == IMAGE_POLICY_VERSION
    assert image_certification_current(item) is True
    # 본문 후보는 바뀌지 않았다 — 판을 올리면 후보 hash에 매인 검수·인증이 무효가 된다.
    assert item.content_revision == 3
    # 빌려준 글은 그대로다.
    assert pg_session.get(ContentItem, lender_id).image_reused_from_content_id is None
    # 공개 이미지 URL이 바뀌었으므로 Site 캐시를 깨운다(실패해도 저장은 되돌리지 않는다).
    assert revalidated == [(pg_session.get(ContentItem, item_id).hospital.slug, item_id)]


def test_provider_failure_keeps_the_borrowed_image_and_records_one_attempt(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id, lender_id = _borrowed_item(pg_conn)
    borrowed_url = pg_session.get(ContentItem, item_id).image_url

    async def generate(*_args, **_kwargs):
        raise RuntimeError("provider down")

    result = _run(monkeypatch, pg_session, generate)

    assert result == {"replaced": 0, "skipped": 0, "failed": 1}
    pg_session.expire_all()
    item = pg_session.get(ContentItem, item_id)
    # 빌린 인증은 그대로 유효하다 — 공개 페이지가 그림을 잃지 않는다.
    assert item.image_url == borrowed_url
    assert item.image_reused_from_content_id == lender_id
    assert image_certification_current(item) is True
    attempt = read_generation_attempt(item)
    assert attempt["reason"] == "IMAGE_GENERATION_FAILED"
    assert attempt["provider_attempt_count"] == 1
    assert attempt["attempt_period"] == environment_attempt_period()
    assert attempt["next_retry_at"]
    # 정상 자동 복구는 사람의 채널로 새지 않는다.
    assert revalidated == []


def test_cost_guard_deferral_does_not_spend_the_provider_budget(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id, _ = _borrowed_item(pg_conn)

    async def generate(*_args, diagnostics=None, **_kwargs):
        if diagnostics is not None:
            diagnostics["reason"] = "COST_BLOCKED"
        return ("", "")

    result = _run(monkeypatch, pg_session, generate)

    assert result["failed"] == 1
    pg_session.expire_all()
    attempt = read_generation_attempt(pg_session.get(ContentItem, item_id))
    assert attempt["reason"] == "COST_BLOCKED"
    assert attempt["provider_attempt_count"] == 0


def test_an_attempt_that_is_not_due_is_never_bought_again(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id, _ = _borrowed_item(pg_conn)
    item = pg_session.get(ContentItem, item_id)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            "reason": "IMAGE_GENERATION_FAILED",
            "retry_class": "SAMPLE_RECOVERABLE",
            "attempt_period": environment_attempt_period(),
            "provider_attempt_count": 1,
            "next_retry_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
        }
    }
    pg_session.commit()
    calls: list = []

    async def generate(*_args, **_kwargs):
        calls.append(1)
        return (_FRESH_URL, "새 프롬프트")

    result = _run(monkeypatch, pg_session, generate)

    assert result == {"replaced": 0, "skipped": 1, "failed": 0}
    assert calls == []


def test_an_edit_during_replacement_discards_the_new_image(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id, lender_id = _borrowed_item(pg_conn)
    borrowed_url = pg_session.get(ContentItem, item_id).image_url

    async def generate(*_args, **_kwargs):
        # 공급자 호출 중 운영자가 제목을 바꿨다 — 이 결과는 그 글의 주제가 아니다.
        pg_conn.execute(
            text("UPDATE content_items SET title = '편집된 제목' WHERE id = :id"),
            {"id": item_id},
        )
        return (_FRESH_URL, "새 프롬프트")

    result = _run(monkeypatch, pg_session, generate)

    assert result == {"replaced": 0, "skipped": 1, "failed": 0}
    pg_session.expire_all()
    item = pg_session.get(ContentItem, item_id)
    assert item.image_url == borrowed_url
    assert item.image_reused_from_content_id == lender_id
    assert revalidated == []


def _hospital_fallback_item(pg_conn) -> uuid.UUID:
    """빌릴 이미지가 없어 병원 히어로 대체본으로 발행된 글(첫 글)."""

    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hero_url = (
        "https://storage.googleapis.com/reputation-images/content/" + "b" * 64 + "-hero.png"
    )
    pg_conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '첫글병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"fallback-{uuid.uuid4().hex[:8]}"},
    )
    pg_conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 9, 1)},
    )
    pg_conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            " scheduled_date, status, title, body, content_revision, published_at, "
            " image_url, image_content_hash, image_subject_hash, image_policy_version, "
            " image_policy_verified_at, image_reused_from_content_id, image_fallback_source) "
            "VALUES (:id, :hid, :sid, 'DISEASE', 1, 12, :d, 'PUBLISHED', '첫 글', '본문', 3, "
            " :published_at, :image_url, :content_hash, NULL, :policy_version, "
            " :verified_at, NULL, 'HOSPITAL_HERO')"
        ),
        {
            "id": item_id,
            "hid": hospital_id,
            "sid": schedule_id,
            "d": date(2026, 9, 10),
            "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
            "image_url": hero_url,
            "content_hash": image_content_hash_from_url(hero_url),
            "policy_version": IMAGE_POLICY_VERSION,
            "verified_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        },
    )
    return item_id


def test_the_sweep_also_replaces_a_hospital_hero_fallback_and_clears_its_marker(
    pg_conn, pg_session, monkeypatch, revalidated
):
    """히어로 대체본도 최종 상태가 아니다 — 그 글의 주제 이미지로 바꿔 달고 marker를 푼다."""
    item_id = _hospital_fallback_item(pg_conn)

    async def generate(*_args, **_kwargs):
        return (_FRESH_URL, "새 프롬프트")

    result = _run(monkeypatch, pg_session, generate)

    assert result["replaced"] == 1
    pg_session.expire_all()
    item = pg_session.get(ContentItem, item_id)
    assert item.image_url == _FRESH_URL
    assert item.image_fallback_source is None
    assert item.image_subject_hash == image_subject_hash(ContentType.DISEASE, item.title)
    assert image_certification_current(item) is True
    assert item.content_revision == 3


def test_a_failed_replacement_keeps_the_hospital_hero_fallback_certified(
    pg_conn, pg_session, monkeypatch, revalidated
):
    item_id = _hospital_fallback_item(pg_conn)

    async def generate(*_args, **_kwargs):
        return ("", "")

    result = _run(monkeypatch, pg_session, generate)

    assert result["failed"] == 1
    pg_session.expire_all()
    item = pg_session.get(ContentItem, item_id)
    # 공개 페이지는 계속 그림이 있는 글을 보여준다.
    assert item.image_fallback_source == "HOSPITAL_HERO"
    assert image_certification_current(item) is True


def test_replacement_clears_the_summary_substitution_keys(
    pg_conn, pg_session, monkeypatch, revalidated
):
    """교체되면 "대체 이미지" 표시는 요약에서도 사라진다 — 운영 화면이 계속 대체라 읽지 않게."""
    item_id = _hospital_fallback_item(pg_conn)
    pg_conn.execute(
        text(
            "UPDATE content_items SET essence_check_summary = "
            "'{\"image_reused\": true, \"image_fallback\": \"HOSPITAL_HERO\"}'::jsonb "
            "WHERE id = :id"
        ),
        {"id": item_id},
    )

    async def generate(*_args, **_kwargs):
        return (_FRESH_URL, "새 프롬프트")

    assert _run(monkeypatch, pg_session, generate)["replaced"] == 1
    pg_session.expire_all()
    summary = pg_session.get(ContentItem, item_id).essence_check_summary or {}
    assert "image_reused" not in summary
    assert "image_fallback" not in summary
