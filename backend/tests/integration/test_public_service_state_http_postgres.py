"""Canonical lifecycle verdicts through real ASGI routes and PostgreSQL."""

import hashlib
import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter

from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_revision_storage import reconcile_content_revisions
from app.services.content_visibility import assess_public_visibility
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.essence_readiness import get_public_approved_philosophy_id
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from tests.publication_test_support import verified_reference_checks

pytestmark = pytest.mark.asyncio

_SCHEDULELESS_HOSPITAL_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
_PAUSED_HOSPITAL_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
_CONTENT_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")


async def _seed_public_history(db, *, hospital_id: uuid.UUID, slug: str) -> tuple[Hospital, ContentItem]:
    hospital = Hospital(
        id=hospital_id,
        name="일정 없는 안전 병원",
        slug=slug,
        address="서울시 강남구 테헤란로 1",
        phone="02-1234-5678",
        treatments=[{"name": "대장항문 진료", "description": "진료 범위를 안내합니다."}],
        status=HospitalStatus.ACTIVE,
        profile_complete=False,
        site_built=True,
        site_live=True,
        schedule_set=False,
        region=["서울"],
        specialties=["외과"],
        keywords=[],
        competitors=[],
    )
    db.add(hospital)
    await db.flush()

    now = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="병원 공식 홈페이지",
        raw_text="진료 범위와 연락처를 확인한 공식 자료입니다.",
        content_hash="lifecycle-source-hash",
        status=SourceStatus.PROCESSED,
        processed_at=now,
    )
    db.add(source)
    await db.flush()

    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="확인된 진료 정보를 충분히 설명합니다.",
        patient_promise="공식 자료에 근거한 정보를 안내합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        source_asset_ids=[str(source.id)],
        approved_at=now,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 9, 1),
        is_active=False,
    )
    db.add_all([philosophy, schedule])
    await db.flush()

    content_type = ContentType.FAQ
    title = "진료 예약 전에 확인할 내용"
    image_hash = hashlib.sha256(b"lifecycle-content-image").hexdigest()
    image_url = f"gs://reputation-images/content/{image_hash}-fixture.png"
    content = ContentItem(
        id=_CONTENT_ID,
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=content_type,
        sequence_no=1,
        total_count=12,
        title=title,
        body="예약 전에는 증상과 복용 중인 약을 정리해 알려 주세요.",
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo.do",
            }
        ],
        faq_question="진료 예약 전에 무엇을 준비하나요?",
        faq_answer_summary="증상과 복용 중인 약을 정리해 주세요.",
        image_url=image_url,
        image_policy_verified_at=now,
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash(content_type, title),
        image_policy_version=IMAGE_POLICY_VERSION,
        scheduled_date=date(2026, 9, 15),
        status=ContentStatus.PUBLISHED,
        published_at=now,
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy.id,
        generation_philosophy_id=philosophy.id,
        last_reviewed_philosophy_id=philosophy.id,
        content_brief={
            "schema_version": "content-brief-v2",
            "target_query": "진료 예약 전 준비 사항",
            "treatment_narrative": {
                "source": "approved_philosophy",
                "angle": "공식 자료에 근거한 예약 안내",
            },
            "source_snapshot": {
                "hash": philosophy.source_snapshot_hash,
                "source_asset_ids": [str(source.id)],
            },
        },
        essence_check_summary={
            "generation_provenance": {"source_asset_ids": [str(source.id)]}
        },
    )
    content.reference_checks = verified_reference_checks(content, checked_at=now)
    db.add(content)
    await db.flush()
    written = await reconcile_content_revisions(db, content_item_id=content.id)
    assert written.created_count == 1
    await db.refresh(content, attribute_names=["active_revision_id", "active_revision"])
    return hospital, content


async def _client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, previous_limiter


async def test_scheduleless_clinic_keeps_published_article_http(pg_async_session):
    hospital, content = await _seed_public_history(
        pg_async_session,
        hospital_id=_SCHEDULELESS_HOSPITAL_ID,
        slug="scheduleless-clinic",
    )
    public_philosophy_id = await get_public_approved_philosophy_id(
        pg_async_session, hospital.id
    )
    visibility = assess_public_visibility(content, public_philosophy_id)
    assert public_philosophy_id == content.content_philosophy_id
    assert visibility.visible, visibility.blockers
    client, previous_limiter = await _client(pg_async_session)
    try:
        async with client:
            profile = await client.get(f"/api/v1/public/hospitals/{hospital.slug}")
            article = await client.get(
                f"/api/v1/public/hospitals/{hospital.slug}/contents/{content.id}"
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert profile.status_code == 200
    assert article.status_code == 200
    assert article.json()["id"] == str(content.id)
    for optional_field in (
        "website_url",
        "blog_url",
        "kakao_channel_url",
        "latitude",
        "longitude",
        "director_name",
    ):
        assert optional_field not in profile.json()


async def test_public_service_state_matches_list_detail_host_and_admin_http(pg_async_session):
    live, _content = await _seed_public_history(
        pg_async_session,
        hospital_id=_SCHEDULELESS_HOSPITAL_ID,
        slug="scheduleless-clinic",
    )
    paused = Hospital(
        id=_PAUSED_HOSPITAL_ID,
        name="일시 정지 병원",
        slug="paused-clinic",
        address="서울시 종로구 1",
        phone="02-9876-5432",
        treatments=[{"name": "내과 진료"}],
        status=HospitalStatus.PAUSED,
        profile_complete=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        aeo_domain="paused.example.com",
    )
    actor = AdminUser(
        email="lifecycle-owner@example.com",
        name="Lifecycle Owner",
        role=ROLE_OWNER,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    pg_async_session.add_all([paused, actor])
    await pg_async_session.flush()

    client, previous_limiter = await _client(pg_async_session)
    headers = {"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email}
    try:
        async with client:
            public_list = await client.get("/api/v1/public/hospitals")
            live_profile = await client.get(f"/api/v1/public/hospitals/{live.slug}")
            live_host = await client.get(
                f"/api/v1/public/site/hospitals/by-domain/{live.slug}.reputation.motionlabs.kr"
            )
            paused_profile = await client.get(f"/api/v1/public/hospitals/{paused.slug}")
            paused_host = await client.get(
                "/api/v1/public/site/hospitals/by-domain/paused.example.com"
            )
            admin_list = await client.get("/api/v1/admin/hospitals", headers=headers)
            admin_detail = await client.get(
                f"/api/v1/admin/hospitals/{paused.id}", headers=headers
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert public_list.status_code == 200
    assert [row["slug"] for row in public_list.json()] == [live.slug]
    assert live_profile.status_code == 200
    assert live_host.status_code == 200
    assert paused_profile.status_code == 404
    assert paused_host.status_code == 404
    assert admin_list.status_code == 200
    admin_rows = {row["id"]: row for row in admin_list.json()}
    assert admin_rows[str(live.id)]["public_service_state"] == {"kind": "live", "remaining": []}
    assert admin_rows[str(paused.id)]["public_service_state"] == {
        "kind": "paused",
        "remaining": [],
    }
    assert admin_detail.status_code == 200
    assert admin_detail.json()["public_service_state"] == {"kind": "paused", "remaining": []}
