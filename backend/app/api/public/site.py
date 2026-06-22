"""
Public API — 병원 정보·콘텐츠 허브 공개 표면용
GET /api/v1/public/hospitals/{slug}                      — 병원 기본정보 + 공개 사진
GET /api/v1/public/hospitals/{slug}/contents             — 발행된 콘텐츠 목록
GET /api/v1/public/hospitals/{slug}/contents/{content_id} — 콘텐츠 상세
GET /api/v1/public/site/hospitals/by-domain/{domain}      — 커스텀 도메인 → 병원 역조회
"""
import re
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalSourceAsset, PHOTO_SOURCE_TYPES, SourceStatus, SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.services.asset_storage import resolve_legacy_asset_path, resolve_local_asset_path
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.services.gcs_utils import get_signed_url
from app.utils.domain import normalize_domain

router = APIRouter(prefix="/public/hospitals", tags=["Public — Site"])

# 커스텀 도메인 역조회 표면 — /site 미들웨어가 요청 호스트로 병원을 식별할 때 사용.
# 계약(/api/v1/public/site/hospitals/by-domain/{domain})이 기존 prefix와 달라 별도 라우터.
domain_router = APIRouter(prefix="/public/site/hospitals", tags=["Public — Site"])


@domain_router.get("/by-domain/{domain}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_hospital_by_domain(request: Request, domain: str, db: AsyncSession = Depends(get_db)):
    """커스텀 도메인(aeo_domain) → 병원 식별 정보.

    /site 호스트 라우팅 미들웨어 전용 계약: ACTIVE + site_live 병원의 aeo_domain과
    대소문자 무시(정규화) 일치 시 200 {"slug", "name", "aeo_domain"}, 아니면 404.
    경로 파라미터는 포트/끝 점/스킴 잔재까지 정규화한다 (저장 측도 동일 규칙으로 정규화됨).
    """
    normalized = normalize_domain(domain)
    if not normalized:
        raise HTTPException(status_code=404, detail="Hospital not found")

    result = await db.execute(
        select(Hospital).where(
            # 저장 경로(connect_domain)·migration 0025가 소문자로 정규화하지만, 혹시 모를
            # 레거시 대문자 행까지 흡수하도록 비교는 lower()로 한 번 더 방어한다.
            func.lower(Hospital.aeo_domain) == normalized,
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.site_live.is_(True),
        )
        .limit(1)
    )
    h = result.scalars().first()
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return {"slug": h.slug, "name": h.name, "aeo_domain": h.aeo_domain}


@router.get("")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def list_hospitals(request: Request, db: AsyncSession = Depends(get_db)):
    """Public list of active hospitals for sitemap generation."""
    stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE, Hospital.site_live.is_(True))
    result = await db.execute(stmt)
    hospitals = result.scalars().all()
    return [_serialize_hospital_summary(h) for h in hospitals]


def _serialize_hospital_summary(h: Hospital) -> dict:
    """병원 목록용 공개-안전 요약 (plan/license_number/director_philosophy 제외).

    /llms.txt 루트 인덱스가 name·region·specialties 등을 직접 출력하므로
    목록 응답에도 포함해야 한다.
    """
    return {
        "slug": h.slug,
        "name": h.name,
        "aeo_domain": h.aeo_domain,
        "region": h.region,
        "specialties": h.specialties,
        "director_name": h.director_name,
        "address": h.address,
        "phone": h.phone,
        "website_url": _safe_external_url(h.website_url),
        "updated_at": h.updated_at.isoformat() if h.updated_at else h.created_at.isoformat() if h.created_at else None,
    }


@router.get("/{slug}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_hospital_public(request: Request, slug: str, db: AsyncSession = Depends(get_db)):
    """병원 기본정보 (ACTIVE 상태 병원만 공개) + AE가 검수해 공개로 표시한 사진."""
    result = await db.execute(select(Hospital).where(Hospital.slug == slug))
    h = result.scalar_one_or_none()
    if not h or h.status != HospitalStatus.ACTIVE or not h.site_live:
        raise HTTPException(status_code=404, detail="Hospital not found")

    # is_public=True 사진만 노출. 의료광고법 우려가 큰 카테고리는 enum에 포함되지 않으므로
    # PHOTO_SOURCE_TYPES 자체가 안전 게이트 역할.
    # AE가 자료를 EXCLUDED로 전환했을 때 /site에 잔존하지 않도록 status로 한 번 더 차단.
    photos_result = await db.execute(
        select(HospitalSourceAsset)
        .where(
            HospitalSourceAsset.hospital_id == h.id,
            HospitalSourceAsset.is_public.is_(True),
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.in_(list(PHOTO_SOURCE_TYPES)),
            HospitalSourceAsset.file_url.is_not(None),
        )
        .order_by(HospitalSourceAsset.updated_at.desc())
    )
    photos = photos_result.scalars().all()
    return _serialize_hospital(h, photos)


@router.get("/{slug}/assets/{source_id}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_public_hospital_asset(
    request: Request,
    slug: str,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Serve only photos explicitly approved for public site exposure."""
    h = await _get_active_hospital(db, slug)
    result = await db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.id == source_id,
            HospitalSourceAsset.hospital_id == h.id,
            HospitalSourceAsset.is_public.is_(True),
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.in_(list(PHOTO_SOURCE_TYPES)),
            HospitalSourceAsset.file_url.is_not(None),
        )
    )
    asset = result.scalar_one_or_none()
    if not asset or not asset.file_url:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _asset_response(asset.file_url, hospital_id=h.id, media_type=asset.mime_type)


@router.get("/{slug}/contents")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def list_published_contents(
    request: Request,
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
            ContentItem.essence_status == ESSENCE_STATUS_ALIGNED,
        )
        .order_by(ContentItem.published_at.desc())
        .limit(limit)
    )
    items = result.scalars().all()
    return [_serialize_item(item, h.slug) for item in items]


@router.get("/{slug}/contents/{content_id}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_content_public(
    request: Request, slug: str, content_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """콘텐츠 상세"""
    h = await _get_active_hospital(db, slug)

    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != h.id or not _is_public_safe_content(item):
        raise HTTPException(status_code=404, detail="Content not found")
    return _serialize_item(item, h.slug, full=True)


@router.get("/{slug}/contents/{content_id}/image")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_public_content_image(
    request: Request, slug: str, content_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """발행된 콘텐츠 대표 이미지를 안정 URL로 서빙 (요청마다 fresh signed URL로 302)."""
    h = await _get_active_hospital(db, slug)
    item = await db.get(ContentItem, content_id)
    if (
        not item
        or item.hospital_id != h.id
        or not _is_public_safe_content(item)
        or not item.image_url
    ):
        raise HTTPException(status_code=404, detail="Content image not found")
    return _asset_response(item.image_url, hospital_id=h.id, media_type="image/png")


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_active_hospital(db: AsyncSession, slug: str) -> Hospital:
    result = await db.execute(select(Hospital).where(Hospital.slug == slug))
    h = result.scalar_one_or_none()
    if not h or h.status != HospitalStatus.ACTIVE or not h.site_live:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _serialize_hospital(h: Hospital, photos: list[HospitalSourceAsset] | None = None) -> dict:
    photo_records: list[HospitalSourceAsset] = list(photos or [])

    # 카테고리별 첫 사진 — 컴포넌트 fallback용 단축 필드.
    by_type: dict[SourceType, HospitalSourceAsset] = {}
    for asset in photo_records:
        if asset.source_type not in by_type:
            by_type[asset.source_type] = asset

    # 먼저 sanitize — 비 http(s) URL은 버린다. 무효/누락이면 AE가 공개 승인한
    # PHOTO_DOCTOR 자산으로 폴백한다 (sanitize가 else 분기에만 있으면 무효 URL이
    # null이 되면서 승인된 자산 폴백까지 건너뛰는 버그가 있었다).
    director_photo = _safe_external_url(h.director_photo_url)
    if not director_photo and SourceType.PHOTO_DOCTOR in by_type:
        director_photo = _public_asset_url(h.slug, by_type[SourceType.PHOTO_DOCTOR])

    serialized_photos = [
        {
            "id": str(asset.id),
            "source_type": asset.source_type.value if hasattr(asset.source_type, "value") else asset.source_type,
            "title": asset.title,
            "url": _public_asset_url(h.slug, asset),
        }
        for asset in photo_records
    ]

    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "address": h.address,
        "phone": h.phone,
        "business_hours": h.business_hours,
        "website_url": _safe_external_url(h.website_url),
        "blog_url": _safe_external_url(h.blog_url),
        "kakao_channel_url": _safe_external_url(h.kakao_channel_url),
        "google_business_profile_url": _safe_external_url(h.google_business_profile_url),
        "google_maps_url": _safe_external_url(h.google_maps_url),
        "naver_place_url": _safe_external_url(h.naver_place_url),
        "aeo_domain": h.aeo_domain,
        "latitude": h.latitude,
        "longitude": h.longitude,
        "wikidata_qid": getattr(h, "wikidata_qid", None),
        "gbp_place_id": getattr(h, "gbp_place_id", None),
        "naver_place_id": getattr(h, "naver_place_id", None),
        "kakao_place_id": getattr(h, "kakao_place_id", None),
        "hira_org_id": getattr(h, "hira_org_id", None),
        "region": h.region,
        "specialties": h.specialties,
        "keywords": h.keywords,
        "director_name": h.director_name,
        "director_career": h.director_career,
        # Legacy profile notes are intentionally not exposed publicly. Public-facing
        # clinic writing standards must come from an approved, source-backed review
        # flow rather than the free-text profile field.
        "director_philosophy": None,
        "director_photo_url": director_photo,
        "director_credentials": _safe_credentials(getattr(h, "director_credentials", None)),
        "treatments": h.treatments,
        "photos": serialized_photos,
    }


def _safe_credentials(credentials: dict | None) -> dict | None:
    """공개 표면에는 license_number를 노출하지 않는다(내부 보관 전용)."""
    if not isinstance(credentials, dict):
        return None
    return {
        key: value
        for key, value in credentials.items()
        if key != "license_number" and value not in (None, "", [], {})
    }


def _is_public_safe_content(item: ContentItem) -> bool:
    return item.status == ContentStatus.PUBLISHED and item.essence_status == ESSENCE_STATUS_ALIGNED


def _public_asset_url(slug: str, asset: HospitalSourceAsset) -> str:
    return f"/api/v1/public/hospitals/{slug}/assets/{asset.id}"


def _content_image_url(slug: str, item: ContentItem) -> str:
    # gs:// 저장본만 안정 프록시 경로로 노출한다 — 요청마다 backend가 fresh signed URL로
    # 302하므로 SSG/CDN 캐시 HTML이 만료 URL을 박아 403으로 깨지는 일을 막는다.
    # 이미 사용 가능한 URL(레거시 상대 public asset 경로 "/api/.../assets/..." 또는 http(s))은
    # 프록시로 감싸면 _asset_response가 처리 못 해 404가 나므로 그대로 통과시킨다.
    ref = item.image_url or ""
    if ref.startswith("gs://"):
        return f"/api/v1/public/hospitals/{slug}/contents/{item.id}/image"
    return ref


def _safe_external_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value.strip()


def _asset_response(asset_ref: str, *, hospital_id: uuid.UUID, media_type: str | None):
    if asset_ref.startswith("local://"):
        path = resolve_local_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(path, media_type=media_type)
    if asset_ref.startswith("gs://"):
        signed_url = get_signed_url(asset_ref)
        if not signed_url or signed_url == asset_ref:
            raise HTTPException(status_code=503, detail="Could not create signed asset URL")
        return RedirectResponse(url=signed_url, status_code=302)
    if asset_ref.startswith("/assets/"):
        path = resolve_legacy_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if path and path.exists():
            return FileResponse(path, media_type=media_type)
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset_ref.startswith("http://") or asset_ref.startswith("https://"):
        return RedirectResponse(url=asset_ref, status_code=302)
    raise HTTPException(status_code=404, detail="Asset not found")


# 한국어 평균 읽기 속도 약 600자/분 — site 상세 페이지 calculateReadingMinutes와 동일 기준.
_KOREAN_READING_SPEED_CHARS_PER_MIN = 600


def _reading_minutes(body: str | None) -> int:
    """본문 길이 기반 읽기 시간(분). 목록 응답은 body를 생략하므로 서버에서 계산해 내려준다."""
    if not body:
        return 1
    stripped = re.sub(r"https?://\S+", "", body)
    stripped = re.sub(r"[#*_\[\]()`>!\-\s]", "", stripped)
    return max(1, round(len(stripped) / _KOREAN_READING_SPEED_CHARS_PER_MIN))


def _serialize_item(item: ContentItem, slug: str, full: bool = False) -> dict:
    d = {
        "id": str(item.id),
        "content_type": item.content_type,
        "title": item.title,
        "meta_description": item.meta_description,
        "image_url": _content_image_url(slug, item) if item.image_url else None,
        "scheduled_date": str(item.scheduled_date),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "body_updated_at": item.body_updated_at.isoformat() if item.body_updated_at else None,
        "references": item.references_list or [],
        "faq_question": item.faq_question,
        "faq_answer_summary": item.faq_answer_summary,
        "reading_minutes": _reading_minutes(item.body),
    }
    if full:
        d["body"] = item.body
    return d
