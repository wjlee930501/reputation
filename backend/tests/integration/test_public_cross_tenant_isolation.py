"""공개 표면의 테넌트 경계 — 실제 Postgres SQL 술어로 검증한다.

기존 `tests/test_public_site.py`는 전부 fake DB(`_SequentialFakeDB`)라서
`ContentItem.hospital_id == h.id` 같은 술어가 **한 번도 실행되지 않는다**. 술어를
통째로 지워도 그 유닛 테스트는 초록으로 남는다.

여기서는 병원 A·B를 실제로 시드하고 라우트 함수를 실제 AsyncSession으로 호출해,
B의 slug로 A의 자원을 요청하면 404가 나고 B의 목록에 A의 항목이 섞이지 않는지 확인한다.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.public import site as site_api
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash

# slowapi @limiter.limit 우회 — 라우트를 FastAPI 요청 라이프사이클 밖에서 직접 호출한다
# (tests/test_public_site.py와 동일 패턴).
get_hospital_public = site_api.get_hospital_public.__wrapped__
list_published_contents = site_api.list_published_contents.__wrapped__
get_content_public = site_api.get_content_public.__wrapped__
get_public_content_image = site_api.get_public_content_image.__wrapped__
get_public_hospital_asset = site_api.get_public_hospital_asset.__wrapped__


class _Tenant:
    def __init__(self, hospital, philosophy, schedule, content, photo):
        self.hospital = hospital
        self.philosophy = philosophy
        self.schedule = schedule
        self.content = content
        self.photo = photo

    @property
    def slug(self) -> str:
        return self.hospital.slug


async def _seed_tenant(session, *, label: str) -> _Tenant:
    """ACTIVE + site_live 병원 1곳 — 승인된 운영 기준·발행 콘텐츠·공개 사진까지."""
    suffix = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"교차테넌트{label}병원",
        slug=f"xtenant-{label}-{suffix}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        treatments=[],
    )
    session.add(hospital)
    await session.flush()

    processed_at = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{label} 홈페이지",
        raw_text="근거 자료 본문",
        content_hash=f"hash-{suffix}",
        status=SourceStatus.PROCESSED,
        processed_at=processed_at,
    )
    photo = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_DOCTOR,
        title=f"{label} 원장 사진",
        file_url="gs://reputation-images/doctor.png",
        mime_type="image/png",
        is_public=True,
        source_metadata={
            "asset_kind": "VERIFIED_REAL_PERSON",
            "approved_usage": ["DOCTOR_IDENTITY"],
        },
        photo_source_owner=f"{label} 원장",
        photo_rights_basis="OWNER_CONSENT",
        photo_evidence_reference=f"consent/{suffix}",
        photo_verified_by="owner@example.com",
        photo_verified_at=processed_at,
        status=SourceStatus.PROCESSED,
    )
    session.add_all([source, photo])
    await session.flush()

    # public_philosophy로 인정받으려면 승인본의 snapshot hash가 처리된 자료와 일치해야 한다.
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement=f"{label} 병원은 근거 중심으로 충분히 설명합니다.",
        patient_promise="확인된 정보만 환자에게 안내합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        approved_at=processed_at,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 7, 1),
    )
    session.add_all([philosophy, schedule])
    await session.flush()

    content = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.FAQ,
        sequence_no=1,
        total_count=8,
        title=f"{label} 병원 전용 콘텐츠",
        body="본문",
        image_url="gs://reputation-images/content/x.png",
        image_policy_verified_at=datetime(2026, 7, 1, 8, 59, tzinfo=timezone.utc),
        scheduled_date=date(2026, 7, 15),
        status=ContentStatus.PUBLISHED,
        published_at=processed_at,
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy.id,
    )
    session.add(content)
    await session.flush()
    return _Tenant(hospital, philosophy, schedule, content, photo)


async def _seed_content(session, tenant: _Tenant, *, philosophy_id, title: str) -> ContentItem:
    """tenant 소유 발행 콘텐츠 1건 — 운영 기준 연결만 호출부가 지정한다."""
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=tenant.hospital.id,
        schedule_id=tenant.schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=2,
        total_count=8,
        title=title,
        body="본문",
        scheduled_date=date(2026, 7, 22),
        status=ContentStatus.PUBLISHED,
        published_at=datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc),
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy_id,
    )
    session.add(item)
    await session.flush()
    return item


@pytest.fixture
async def tenants(pg_async_session):
    a = await _seed_tenant(pg_async_session, label="a")
    b = await _seed_tenant(pg_async_session, label="b")
    return a, b


async def _status_of(coro) -> int:
    try:
        await coro
    except HTTPException as exc:
        return exc.status_code
    return 200


async def test_each_tenant_serves_only_its_own_published_content(pg_async_session, tenants):
    """전제 조건: 자기 slug로는 자기 콘텐츠가 정상적으로 보인다."""
    a, b = tenants

    a_items = await list_published_contents(
        None, a.slug, limit=20, offset=0, db=pg_async_session
    )
    b_items = await list_published_contents(
        None, b.slug, limit=20, offset=0, db=pg_async_session
    )

    assert [item["id"] for item in a_items] == [str(a.content.id)]
    assert [item["id"] for item in b_items] == [str(b.content.id)]


async def test_content_list_never_leaks_another_hospitals_items(pg_async_session, tenants):
    a, b = tenants

    b_items = await list_published_contents(
        None, b.slug, limit=500, offset=0, db=pg_async_session
    )

    assert str(a.content.id) not in {item["id"] for item in b_items}
    assert all(a.hospital.name not in item["title"] for item in b_items)


async def test_content_detail_under_another_hospitals_slug_is_404(pg_async_session, tenants):
    """병원 B의 slug + 병원 A의 content_id → 404. 술어가 사라지면 200이 된다."""
    a, b = tenants

    status = await _status_of(
        get_content_public(None, b.slug, a.content.id, db=pg_async_session)
    )

    assert status == 404


async def test_content_detail_is_404_even_when_it_points_at_the_requesting_hospitals_philosophy(
    pg_async_session, tenants
):
    """`hospital_id` 술어만이 유일한 방어선인 경우를 고정한다.

    `content_items.content_philosophy_id`에는 테넌트 제약이 없다 — 다른 병원의 운영
    기준 행을 가리키는 콘텐츠가 DB 수준에서 만들어질 수 있다(생성 경로 버그·데이터 이관
    사고). 그 상태에서는 운영 기준 일치 검사가 통과해버리므로, `hospital_id` 비교가
    빠지면 병원 B의 slug로 병원 A의 콘텐츠 본문이 그대로 공개된다.
    """
    a, b = tenants
    misfiled = await _seed_content(
        pg_async_session, a, philosophy_id=b.philosophy.id, title="A 병원 콘텐츠(운영 기준 오연결)"
    )

    status = await _status_of(
        get_content_public(None, b.slug, misfiled.id, db=pg_async_session)
    )

    assert status == 404


async def test_content_list_excludes_items_misfiled_onto_the_requesting_hospitals_philosophy(
    pg_async_session, tenants
):
    a, b = tenants
    misfiled = await _seed_content(
        pg_async_session, a, philosophy_id=b.philosophy.id, title="A 병원 콘텐츠(운영 기준 오연결)"
    )

    b_items = await list_published_contents(
        None, b.slug, limit=500, offset=0, db=pg_async_session
    )

    assert str(misfiled.id) not in {item["id"] for item in b_items}
    assert [item["id"] for item in b_items] == [str(b.content.id)]


async def test_content_image_under_another_hospitals_slug_is_404(pg_async_session, tenants):
    a, b = tenants

    status = await _status_of(
        get_public_content_image(None, b.slug, a.content.id, db=pg_async_session)
    )

    assert status == 404


async def test_verified_content_image_uses_the_canonical_webp_contract(
    pg_async_session, tenants, monkeypatch
):
    a, _ = tenants
    captured = {}
    response = object()

    def fake_public_asset_response(file_url, *, hospital_id, media_type):
        captured.update(
            file_url=file_url,
            hospital_id=hospital_id,
            media_type=media_type,
        )
        return response

    monkeypatch.setattr(site_api, "public_asset_response", fake_public_asset_response)

    result = await get_public_content_image(
        None, a.slug, a.content.id, db=pg_async_session
    )

    assert result is response
    assert captured == {
        "file_url": a.content.image_url,
        "hospital_id": a.hospital.id,
        "media_type": "image/webp",
    }


async def test_public_asset_under_another_hospitals_slug_is_404(pg_async_session, tenants):
    a, b = tenants

    status = await _status_of(
        get_public_hospital_asset(None, b.slug, a.photo.id, db=pg_async_session)
    )

    assert status == 404


async def test_hospital_detail_exposes_only_the_requested_tenants_photos(
    pg_async_session, tenants
):
    a, b = tenants

    serialized = await get_hospital_public(None, b.slug, db=pg_async_session)

    photo_ids = {photo["id"] for photo in serialized["photos"]}
    assert photo_ids == {str(b.photo.id)}
    assert str(a.photo.id) not in photo_ids
    assert serialized["public_about"].startswith("b 병원은")


async def test_inactive_hospital_content_is_not_public_even_with_its_own_slug(
    pg_async_session, tenants
):
    """비활성 전환은 그 병원의 공개 표면 전체를 즉시 닫아야 한다."""
    a, _b = tenants
    a.hospital.status = HospitalStatus.ONBOARDING
    await pg_async_session.flush()

    assert await _status_of(get_hospital_public(None, a.slug, db=pg_async_session)) == 404
    assert (
        await _status_of(
            get_content_public(None, a.slug, a.content.id, db=pg_async_session)
        )
        == 404
    )
