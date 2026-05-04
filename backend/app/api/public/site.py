"""
Public API — AI 노출 웹블로그용
GET /api/v1/public/hospitals/{slug}                      — 병원 기본정보
GET /api/v1/public/hospitals/{slug}/contents             — 발행된 콘텐츠 목록
GET /api/v1/public/hospitals/{slug}/contents/{content_id} — 콘텐츠 상세
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.services.gcs_utils import get_signed_url

router = APIRouter(prefix="/public/hospitals", tags=["Public — Site"])


@router.get("")
async def list_hospitals(db: AsyncSession = Depends(get_db)):
    """Public list of active hospitals for sitemap generation."""
    stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
    result = await db.execute(stmt)
    hospitals = result.scalars().all()
    return [
        {
            "slug": h.slug,
            "aeo_domain": h.aeo_domain,
            "updated_at": h.updated_at.isoformat() if h.updated_at else h.created_at.isoformat() if h.created_at else None,
        }
        for h in hospitals
    ]


@router.get("/{slug}")
async def get_hospital_public(slug: str, db: AsyncSession = Depends(get_db)):
    """병원 기본정보 (ACTIVE 상태 병원만 공개)"""
    result = await db.execute(select(Hospital).where(Hospital.slug == slug))
    h = result.scalar_one_or_none()
    if not h or h.status != HospitalStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return _serialize_hospital(h)


@router.get("/{slug}/contents")
async def list_published_contents(
    slug: str,
    limit: int = Query(default=20, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """발행된 콘텐츠 목록 (최신순)"""
    h = await _get_active_hospital(db, slug)

    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == h.id,
            ContentItem.status == ContentStatus.PUBLISHED,
        )
        .order_by(ContentItem.published_at.desc())
        .limit(limit)
    )
    items = result.scalars().all()
    return [_serialize_item(item) for item in items]


@router.get("/{slug}/contents/{content_id}")
async def get_content_public(slug: str, content_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """콘텐츠 상세"""
    h = await _get_active_hospital(db, slug)

    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != h.id or item.status != ContentStatus.PUBLISHED:
        raise HTTPException(status_code=404, detail="Content not found")
    return _serialize_item(item, full=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_active_hospital(db: AsyncSession, slug: str) -> Hospital:
    result = await db.execute(select(Hospital).where(Hospital.slug == slug))
    h = result.scalar_one_or_none()
    if not h or h.status != HospitalStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _serialize_hospital(h: Hospital) -> dict:
    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "plan": h.plan,
        "address": h.address,
        "phone": h.phone,
        "business_hours": h.business_hours,
        "website_url": h.website_url,
        "blog_url": h.blog_url,
        "kakao_channel_url": h.kakao_channel_url,
        "google_business_profile_url": h.google_business_profile_url,
        "google_maps_url": h.google_maps_url,
        "naver_place_url": h.naver_place_url,
        "aeo_domain": h.aeo_domain,
        "latitude": h.latitude,
        "longitude": h.longitude,
        "region": h.region,
        "specialties": h.specialties,
        "keywords": h.keywords,
        "director_name": h.director_name,
        "director_career": h.director_career,
        "director_philosophy": h.director_philosophy,
        "director_photo_url": h.director_photo_url,
        "treatments": h.treatments,
    }


def _serialize_item(item: ContentItem, full: bool = False) -> dict:
    d = {
        "id": str(item.id),
        "content_type": item.content_type,
        "title": item.title,
        "meta_description": item.meta_description,
        "image_url": get_signed_url(item.image_url) if item.image_url else None,
        "scheduled_date": str(item.scheduled_date),
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }
    if full:
        d["body"] = item.body
    return d
