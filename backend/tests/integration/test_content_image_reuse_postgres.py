"""이미지를 만들지 못한 글이 같은 병원의 인증된 이미지를 빌리는 경로 — 실제 SQL로 고정한다.

여기서 확인하는 것은 셋이다. 빌려줄 원본을 고르는 순서와 자격(공개 중 · 인증 현재 ·
자기 자신 아님 · 빌린 이미지 아님), 빌린 인증이 **합성값 없이** 원본 그대로 옮겨지는 것,
그리고 쓰기가 상태·판·claim 가드를 실제로 지키는 것. 마지막 두 가지는 rowcount와 WHERE
절의 SQL 동작이므로 mock으로는 확인할 수 없다.
"""

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.services import content_image_reuse, cost_guard
from app.services.content_image_reuse import (
    apply_reused_image,
    select_reusable_hospital_image,
)
from app.services.content_publication import image_certification_current
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    image_is_publishable,
)


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _image_url(marker: str) -> str:
    digest = image_subject_hash(ContentType.DISEASE, marker)
    return f"https://storage.googleapis.com/reputation-images/content/{digest}-cover.png"


def _seed_hospital(conn) -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '이미지재사용병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"reuse-{uuid.uuid4().hex[:8]}"},
    )
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 9, 1)},
    )
    return hospital_id, schedule_id


def _seed_item(
    conn,
    hospital_id,
    schedule_id,
    *,
    title: str,
    status: str = "PUBLISHED",
    certified_at: datetime | None = None,
    image_marker: str | None = None,
    reused_from: uuid.UUID | None = None,
    sequence_no: int = 1,
    content_type: ContentType = ContentType.DISEASE,
) -> uuid.UUID:
    item_id = uuid.uuid4()
    image_url = _image_url(image_marker) if image_marker else None
    conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            " scheduled_date, status, title, body, content_revision, generated_at, "
            " published_at, image_url, image_content_hash, image_subject_hash, "
            " image_policy_version, image_policy_verified_at, image_reused_from_content_id) "
            "VALUES (:id, :hid, :sid, :content_type, :seq, 12, :d, :status, :title, '본문', 1, "
            " :generated_at, :published_at, :image_url, :content_hash, :subject_hash, "
            " :policy_version, :verified_at, :reused_from)"
        ),
        {
            "id": item_id,
            "hid": hospital_id,
            "sid": schedule_id,
            "content_type": content_type.value,
            "seq": sequence_no,
            "d": date(2026, 9, 10),
            "status": status,
            "title": title,
            "generated_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
            "image_url": image_url,
            "content_hash": image_content_hash_from_url(image_url) if image_url else None,
            "subject_hash": image_subject_hash(content_type, title) if image_url else None,
            "policy_version": IMAGE_POLICY_VERSION if image_url else None,
            "verified_at": certified_at,
            "reused_from": reused_from,
        },
    )
    return item_id


def test_reuse_picks_the_oldest_certified_image_of_the_same_hospital(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    newest = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="최근 인증 글",
        certified_at=base,
        image_marker="newest",
        sequence_no=1,
    )
    oldest = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="가장 오래 인증된 글",
        certified_at=base - timedelta(days=30),
        image_marker="oldest",
        sequence_no=2,
    )
    target = _seed_item(
        pg_conn, hospital_id, schedule_id, title="이미지 없는 글", status="DRAFT", sequence_no=3
    )

    source = select_reusable_hospital_image(pg_session, hospital_id, target)

    assert source is not None
    assert source.id == oldest
    assert source.id != newest


def test_reuse_skips_itself_other_hospitals_drafts_and_already_reused_images(
    pg_conn, pg_session
):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    other_hospital_id, other_schedule_id = _seed_hospital(pg_conn)
    certified_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    # 다른 병원의 인증 이미지는 빌려오지 않는다.
    _seed_item(
        pg_conn,
        other_hospital_id,
        other_schedule_id,
        title="남의 병원 글",
        certified_at=certified_at - timedelta(days=90),
        image_marker="other-hospital",
    )
    # 아직 공개되지 않은 글의 이미지도 빌려주지 않는다.
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="초안 글",
        status="DRAFT",
        certified_at=certified_at - timedelta(days=60),
        image_marker="draft",
        sequence_no=2,
    )
    lender = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="원래 빌려준 글",
        certified_at=certified_at - timedelta(days=50),
        image_marker="lender",
        sequence_no=3,
    )
    # 이미 빌려 쓰는 글은 다시 빌려주지 않는다 — 한 장이 병원 전체로 번지지 않게 한다.
    borrower = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="빌려 쓰는 글",
        certified_at=certified_at - timedelta(days=40),
        image_marker="lender",
        reused_from=lender,
        sequence_no=4,
    )

    source = select_reusable_hospital_image(pg_session, hospital_id, borrower)

    assert source is not None and source.id == lender


def test_reuse_ignores_published_items_whose_certification_is_stale(pg_conn, pg_session):
    """SQL이 좁힌 후보라도 공개 게이트와 같은 함수가 거부하면 빌려주지 않는다."""
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    stale = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="제목이 바뀐 글",
        certified_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        image_marker="stale",
    )
    pg_conn.execute(
        text("UPDATE content_items SET title = '편집된 제목' WHERE id = :id"), {"id": stale}
    )
    pg_session.expire_all()

    assert select_reusable_hospital_image(pg_session, hospital_id, None) is None


def test_reuse_returns_none_when_the_hospital_has_no_certified_image(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    target = _seed_item(
        pg_conn, hospital_id, schedule_id, title="이미지 없는 글", status="DRAFT"
    )

    assert select_reusable_hospital_image(pg_session, hospital_id, target) is None


def test_applying_a_reused_image_copies_the_certificate_without_synthesizing_one(
    pg_conn, pg_session
):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    certified_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    source_id = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="빌려주는 글",
        certified_at=certified_at,
        image_marker="source",
    )
    target_id = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="빌리는 글",
        status="DRAFT",
        sequence_no=2,
    )
    source = pg_session.get(ContentItem, source_id)
    target = pg_session.get(ContentItem, target_id)

    written = apply_reused_image(
        pg_session, item=target, source=source, expected_revision=1
    )

    assert written == 1
    pg_session.expire_all()
    stored = pg_session.get(ContentItem, target_id)
    assert stored.image_url == source.image_url
    assert stored.image_content_hash == source.image_content_hash
    # 원본의 주제 결합을 그대로 들고 간다 — 새 제목으로 만든 합성값이 아니다.
    assert stored.image_subject_hash == source.image_subject_hash
    assert stored.image_subject_hash != image_subject_hash(ContentType.DISEASE, "빌리는 글")
    assert stored.image_policy_verified_at == certified_at
    assert stored.image_reused_from_content_id == source_id
    assert stored.image_prompt is None
    # 이미지 부착은 본문 후보를 바꾸지 않는다.
    assert stored.content_revision == 1
    # 그리고 빌린 인증은 공개 게이트가 실제로 통과시키는 모양이다.
    assert image_certification_current(stored) is True


def test_applying_a_reused_image_respects_status_revision_and_claim_guards(
    pg_conn, pg_session
):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    source_id = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="빌려주는 글",
        certified_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        image_marker="source",
    )
    target_id = _seed_item(
        pg_conn, hospital_id, schedule_id, title="빌리는 글", status="DRAFT", sequence_no=2
    )
    source = pg_session.get(ContentItem, source_id)
    target = pg_session.get(ContentItem, target_id)

    # 그 사이 판이 올라갔다 — 이 결과는 버려야 한다.
    assert apply_reused_image(pg_session, item=target, source=source, expected_revision=7) == 0
    # claim이 다른 실행의 것이면 쓰지 않는다.
    assert (
        apply_reused_image(
            pg_session,
            item=target,
            source=source,
            expected_revision=1,
            expected_claim_token=uuid.uuid4(),
        )
        == 0
    )
    # 운영자가 취소한 글에는 쓰지 않는다.
    pg_conn.execute(
        text("UPDATE content_items SET status = 'CANCELLED' WHERE id = :id"), {"id": target_id}
    )
    pg_session.expire_all()
    cancelled = pg_session.get(ContentItem, target_id)
    assert apply_reused_image(pg_session, item=cancelled, source=source) == 0
    pg_session.expire_all()
    assert pg_session.get(ContentItem, target_id).image_url is None
    assert pg_session.get(ContentItem, target_id).status == ContentStatus.CANCELLED


# ── 유형 선호 ───────────────────────────────────────────────────────────


def test_reuse_prefers_the_same_content_type_over_an_older_other_type(pg_conn, pg_session):
    """같은 유형의 그림이 글과 덜 어긋난다 — 오래된 순서보다 유형이 먼저다."""
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    older_other_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="가장 오래된 질환 글",
        certified_at=base - timedelta(days=90),
        image_marker="oldest-disease",
        content_type=ContentType.DISEASE,
        sequence_no=1,
    )
    same_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="같은 유형의 생활건강 글",
        certified_at=base - timedelta(days=5),
        image_marker="health",
        content_type=ContentType.HEALTH,
        sequence_no=2,
    )
    target = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="이미지 없는 생활건강 글",
        status="DRAFT",
        content_type=ContentType.HEALTH,
        sequence_no=3,
    )

    source = select_reusable_hospital_image(
        pg_session, hospital_id, target, content_type=ContentType.HEALTH
    )

    assert source is not None
    assert source.id == same_type
    assert source.id != older_other_type


def test_reuse_prefers_the_oldest_within_the_same_content_type(pg_conn, pg_session):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    newest_same_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="최근 생활건강 글",
        certified_at=base,
        image_marker="health-new",
        content_type=ContentType.HEALTH,
        sequence_no=1,
    )
    oldest_same_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="오래된 생활건강 글",
        certified_at=base - timedelta(days=20),
        image_marker="health-old",
        content_type=ContentType.HEALTH,
        sequence_no=2,
    )
    target = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="이미지 없는 생활건강 글",
        status="DRAFT",
        content_type=ContentType.HEALTH,
        sequence_no=3,
    )

    source = select_reusable_hospital_image(
        pg_session, hospital_id, target, content_type=ContentType.HEALTH
    )

    assert source is not None
    assert source.id == oldest_same_type
    assert source.id != newest_same_type


def test_reuse_falls_back_to_any_type_when_the_same_type_has_none(pg_conn, pg_session):
    """선호는 자격을 좁히지 않는다 — 같은 유형이 없으면 종전처럼 아무 유형이나 빌린다."""
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    other_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="질환 글",
        certified_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        image_marker="disease",
        content_type=ContentType.DISEASE,
        sequence_no=1,
    )
    target = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="이미지 없는 FAQ",
        status="DRAFT",
        content_type=ContentType.FAQ,
        sequence_no=2,
    )

    source = select_reusable_hospital_image(
        pg_session, hospital_id, target, content_type=ContentType.FAQ
    )

    assert source is not None and source.id == other_type


def test_reuse_infers_the_preferred_type_from_the_target_item(pg_conn, pg_session):
    """호출부가 유형을 따로 계산하지 않아도 선호가 적용된다."""
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    base = datetime(2026, 9, 8, tzinfo=timezone.utc)
    _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="가장 오래된 질환 글",
        certified_at=base - timedelta(days=90),
        image_marker="oldest-disease",
        content_type=ContentType.DISEASE,
        sequence_no=1,
    )
    same_type = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="칼럼",
        certified_at=base,
        image_marker="column",
        content_type=ContentType.COLUMN,
        sequence_no=2,
    )
    target = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="이미지 없는 칼럼",
        status="DRAFT",
        content_type=ContentType.COLUMN,
        sequence_no=3,
    )

    source = select_reusable_hospital_image(pg_session, hospital_id, target)

    assert source is not None and source.id == same_type


# ── 병원 히어로 대체 이미지 ─────────────────────────────────────────────


def _png_bytes(color: tuple[int, int, int] = (200, 200, 180)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _stored_url(payload: bytes) -> str:
    """`_upload_png_to_gcs`와 같은 이름 규칙 — 내용 hash가 URL 안에 있어야 인증이 성립한다."""

    digest = hashlib.sha256(payload).hexdigest()
    return f"gs://reputation-images-test/content/hero/{digest}-{uuid.uuid4().hex}.png"


def _set_hero(conn, hospital_id, url: str) -> None:
    conn.execute(
        text("UPDATE hospitals SET hero_image_url = :url WHERE id = :id"),
        {"url": url, "id": hospital_id},
    )


def _patch_hero_pipeline(monkeypatch, payload: bytes, assessment):
    calls: dict[str, int] = {"fetch": 0, "review": 0, "store": 0}

    def _fetch(_url):
        calls["fetch"] += 1
        return payload

    def _review(*_args, **_kwargs):
        calls["review"] += 1
        if not image_is_publishable(assessment):
            raise ImagePolicyRejectedError(assessment)
        return assessment

    def _store(image_bytes, _hospital):
        calls["store"] += 1
        return _stored_url(image_bytes)

    monkeypatch.setattr(content_image_reuse, "_fetch_hero_bytes", _fetch)
    monkeypatch.setattr(content_image_reuse, "_validate_generated_image", _review)
    monkeypatch.setattr(content_image_reuse, "store_certified_image_bytes", _store)

    async def _reserve(_category):
        return SimpleNamespace(allowed=True, receipt=None, reason=None)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cost_guard, "reserve", _reserve)
    monkeypatch.setattr(cost_guard, "settle_reservation", _noop)
    monkeypatch.setattr(cost_guard, "record_provider_call", _noop)
    return calls


def _assessment(**overrides):
    base = {
        "has_text": False,
        "has_logo": False,
        "has_recognizable_people": False,
        "impersonates_real_clinic": False,
        "topic_relevant": True,
    }
    base.update(overrides)
    return ImagePolicyAssessment(**base)


async def test_hero_certification_allows_the_clinic_logo_but_not_text_or_people(
    pg_conn, pg_session, monkeypatch
):
    """자기 병원 히어로에 자기 로고가 있는 것은 사칭이 아니다 — 글자와 인물만 막는다."""
    hospital_id, _schedule_id = _seed_hospital(pg_conn)
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero.jpg")
    pg_session.expire_all()
    hospital = pg_session.get(Hospital, hospital_id)
    payload = _png_bytes()

    _patch_hero_pipeline(
        monkeypatch,
        payload,
        # 생성 이미지 기준으로는 거절(로고 있음·주제 무관)이지만 히어로에는 허용한다.
        _assessment(has_logo=True, impersonates_real_clinic=True, topic_relevant=False),
    )
    certified = await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital)

    assert certified is not None
    assert certified.policy_version == IMAGE_POLICY_VERSION
    # 인증은 저장한 바이트에서 계산한다 — 합성값이 아니다.
    assert certified.content_hash == image_content_hash_from_url(certified.image_url)


@pytest.mark.parametrize(
    "rejecting",
    [{"has_text": True}, {"has_recognizable_people": True}],
)
async def test_hero_certification_refuses_text_and_recognizable_people(
    pg_conn, pg_session, monkeypatch, rejecting
):
    hospital_id, _schedule_id = _seed_hospital(pg_conn)
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero.jpg")
    pg_session.expire_all()
    hospital = pg_session.get(Hospital, hospital_id)
    _patch_hero_pipeline(monkeypatch, _png_bytes(), _assessment(**rejecting))

    assert await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital) is None
    pg_session.expire_all()
    assert pg_session.get(Hospital, hospital_id).fallback_image_url is None


async def test_hero_certification_is_cached_and_redone_when_the_hero_changes(
    pg_conn, pg_session, monkeypatch
):
    hospital_id, _schedule_id = _seed_hospital(pg_conn)
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero.jpg")
    pg_session.expire_all()
    hospital = pg_session.get(Hospital, hospital_id)
    calls = _patch_hero_pipeline(monkeypatch, _png_bytes(), _assessment())

    first = await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital)
    second = await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital)

    assert first is not None and second is not None
    assert second.image_url == first.image_url
    # 두 번째 호출은 공급자를 다시 부르지 않는다.
    assert calls["review"] == 1

    # 히어로가 바뀌면 옛 인증은 새 원본을 말해 주지 않는다 — 다시 검수한다.
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero-v2.jpg")
    pg_session.expire_all()
    changed = pg_session.get(Hospital, hospital_id)
    third = await content_image_reuse.certify_hospital_fallback_image(pg_session, changed)

    assert third is not None
    assert calls["review"] == 2
    assert third.source_url == "https://cdn.example.com/hero-v2.jpg"


async def test_hospital_fallback_is_applied_as_the_certification_shape_the_gate_accepts(
    pg_conn, pg_session, monkeypatch
):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero.jpg")
    target_id = _seed_item(
        pg_conn,
        hospital_id,
        schedule_id,
        title="첫 글이라 빌릴 이미지가 없다",
        status="DRAFT",
        content_type=ContentType.FAQ,
    )
    pg_session.expire_all()
    hospital = pg_session.get(Hospital, hospital_id)
    _patch_hero_pipeline(monkeypatch, _png_bytes(), _assessment(has_logo=True))

    fallback = await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital)
    target = pg_session.get(ContentItem, target_id)
    written = content_image_reuse.apply_hospital_fallback_image(
        pg_session, item=target, fallback=fallback, expected_revision=1
    )

    assert written == 1
    pg_session.expire_all()
    stored = pg_session.get(ContentItem, target_id)
    assert stored.image_url == fallback.image_url
    assert stored.image_content_hash == fallback.content_hash
    # 이 글의 주제로 검수된 적이 없으므로 주제 hash를 만들어 넣지 않는다.
    assert stored.image_subject_hash is None
    assert stored.image_reused_from_content_id is None
    assert stored.image_fallback_source == "HOSPITAL_HERO"
    assert stored.image_prompt is None
    assert stored.content_revision == 1
    # 그리고 이 모양을 공개 게이트가 실제로 통과시킨다.
    assert image_certification_current(stored) is True


async def test_hospital_fallback_respects_the_status_and_revision_guards(
    pg_conn, pg_session, monkeypatch
):
    hospital_id, schedule_id = _seed_hospital(pg_conn)
    _set_hero(pg_conn, hospital_id, "https://cdn.example.com/hero.jpg")
    target_id = _seed_item(
        pg_conn, hospital_id, schedule_id, title="첫 글", status="DRAFT"
    )
    pg_session.expire_all()
    hospital = pg_session.get(Hospital, hospital_id)
    _patch_hero_pipeline(monkeypatch, _png_bytes(), _assessment())
    fallback = await content_image_reuse.certify_hospital_fallback_image(pg_session, hospital)
    target = pg_session.get(ContentItem, target_id)

    assert (
        content_image_reuse.apply_hospital_fallback_image(
            pg_session, item=target, fallback=fallback, expected_revision=7
        )
        == 0
    )
    pg_conn.execute(
        text("UPDATE content_items SET status = 'CANCELLED' WHERE id = :id"), {"id": target_id}
    )
    pg_session.expire_all()
    cancelled = pg_session.get(ContentItem, target_id)
    assert (
        content_image_reuse.apply_hospital_fallback_image(
            pg_session, item=cancelled, fallback=fallback
        )
        == 0
    )
    pg_session.expire_all()
    assert pg_session.get(ContentItem, target_id).image_url is None


def test_hospital_without_a_hero_has_no_fallback(pg_conn, pg_session):
    hospital_id, _schedule_id = _seed_hospital(pg_conn)
    hospital = pg_session.get(Hospital, hospital_id)

    assert content_image_reuse.stored_hospital_fallback_image(hospital) is None
