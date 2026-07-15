import re
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.public.assets import public_asset_response, public_asset_url
from app.models.content import ContentItem, ContentStatus
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PHOTO_SOURCE_TYPES,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.services.essence_readiness import get_essence_readiness
from app.utils.domain import normalize_domain
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import check_forbidden

router = APIRouter(prefix="/public/hospitals", tags=["Public — Site"])

# 커스텀 도메인 역조회 표면 — /site 미들웨어가 요청 호스트로 병원을 식별할 때 사용.
# 계약(/api/v1/public/site/hospitals/by-domain/{domain})이 기존 prefix와 달라 별도 라우터.
domain_router = APIRouter(prefix="/public/site/hospitals", tags=["Public — Site"])


# 플랫폼 서브도메인({slug}.{platform host})에서 hospital slug가 아닌 예약 라벨.
# 와일드카드 cert가 이들도 커버하므로 slug로 오인하지 않도록 명시 차단.
_RESERVED_PLATFORM_LABELS = frozenset({"www", "admin", "api", "cname", "static", "assets"})


def _platform_site_host() -> str:
    """공개 표면의 기본 호스트 (예: reputation.motionlabs.kr). SITE_BASE_URL에서 파생."""
    return (urlparse(settings.SITE_BASE_URL).hostname or "").lower()


def _platform_subdomain_slug(host: str) -> str | None:
    """host가 {slug}.{platform host} 형태면 단일 라벨 slug를 반환, 아니면 None.

    하이브리드 도메인의 '기본' 경로: 병원은 자기 도메인 없이도 {slug}.{platform host}로
    서빙된다(와일드카드 cert + A 레코드가 커버). host는 정규화된 소문자여야 한다.
    """
    base = _platform_site_host()
    if not base:
        return None
    suffix = f".{base}"
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label:  # 단일 라벨만 (다중 라벨 서브도메인 제외)
        return None
    if label in _RESERVED_PLATFORM_LABELS:
        return None
    return label


@domain_router.get("/by-domain/{domain}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_hospital_by_domain(request: Request, domain: str, db: AsyncSession = Depends(get_db)):
    """요청 호스트 → 병원 식별 정보.

    /site 호스트 라우팅 미들웨어 전용 계약: ACTIVE + site_live 병원에 한해
    200 {"slug", "name", "aeo_domain"}, 아니면 404. 두 경로를 해석한다:
      1. 기본 서브도메인 {slug}.{platform host} → slug로 직접 조회 (자기 도메인 불필요).
      2. 병원 자기 도메인 → aeo_domain 정규화 일치.
    경로 파라미터는 포트/끝 점/스킴 잔재까지 정규화한다 (저장 측도 동일 규칙으로 정규화됨).
    """
    normalized = normalize_domain(domain)
    if not normalized:
        raise HTTPException(status_code=404, detail="Hospital not found")

    subdomain_slug = _platform_subdomain_slug(normalized)
    if subdomain_slug is not None:
        match_clause = Hospital.slug == subdomain_slug
    else:
        # 저장 경로(connect_domain)·migration 0025가 소문자로 정규화하지만, 혹시 모를
        # 레거시 대문자 행까지 흡수하도록 비교는 lower()로 한 번 더 방어한다.
        match_clause = func.lower(Hospital.aeo_domain) == normalized

    result = await db.execute(
        select(Hospital)
        .where(
            match_clause,
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
    stmt = select(Hospital).where(
        Hospital.status == HospitalStatus.ACTIVE, Hospital.site_live.is_(True)
    )
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
        "updated_at": h.updated_at.isoformat()
        if h.updated_at
        else h.created_at.isoformat()
        if h.created_at
        else None,
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

    # 승인된 콘텐츠 운영 기준(positioning/promise)만 공개 about 서사로 노출한다.
    # 자유 입력 director_philosophy와 달리 근거 기반 검수를 거친 필드다.
    essence = await get_essence_readiness(db, h.id)
    return _serialize_hospital(h, photos, essence.current)


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
    return public_asset_response(asset.file_url, hospital_id=h.id, media_type=asset.mime_type)


@router.get("/{slug}/contents")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def list_published_contents(
    request: Request,
    slug: str,
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """발행된 콘텐츠 목록 (최신순).

    limit은 500 하드캡을 유지하되, offset으로 페이지를 넘길 수 있게 해 500편을
    넘어서는 병원(수년 누적)도 호출부(sitemap 등)가 전체 발행 콘텐츠를 순회할 수 있다.
    """
    h = await _get_active_hospital(db, slug)
    essence = await get_essence_readiness(db, h.id)
    if essence.current is None:
        return []

    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == h.id,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.essence_status == ESSENCE_STATUS_ALIGNED,
            ContentItem.content_philosophy_id == essence.current.id,
        )
        .order_by(ContentItem.published_at.desc())
        .offset(offset)
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
    essence = await get_essence_readiness(db, h.id)

    item = await db.get(ContentItem, content_id)
    if (
        not item
        or item.hospital_id != h.id
        or not _is_public_safe_content(item, essence.current.id if essence.current else None)
    ):
        raise HTTPException(status_code=404, detail="Content not found")
    return _serialize_item(item, h.slug, full=True)


@router.get("/{slug}/contents/{content_id}/image")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_public_content_image(
    request: Request, slug: str, content_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """발행된 콘텐츠 대표 이미지를 안정 URL로 서빙 (요청마다 fresh signed URL로 302)."""
    h = await _get_active_hospital(db, slug)
    essence = await get_essence_readiness(db, h.id)
    item = await db.get(ContentItem, content_id)
    if (
        not item
        or item.hospital_id != h.id
        or not _is_public_safe_content(item, essence.current.id if essence.current else None)
        or not item.image_url
    ):
        raise HTTPException(status_code=404, detail="Content image not found")
    return public_asset_response(item.image_url, hospital_id=h.id, media_type="image/png")


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_active_hospital(db: AsyncSession, slug: str) -> Hospital:
    result = await db.execute(select(Hospital).where(Hospital.slug == slug))
    h = result.scalar_one_or_none()
    if not h or h.status != HospitalStatus.ACTIVE or not h.site_live:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _vetted_public_about(philosophy: HospitalContentPhilosophy | None) -> str | None:
    """승인된 운영 기준에서 의료광고 검수를 통과한 공개 about 서사를 만든다.

    승인된 HospitalContentPhilosophy의 positioning_statement + patient_promise만 사용하고
    (자유 입력 director_philosophy는 절대 사용 안 함), 의료광고 금지 표현이 하나라도 섞인
    문장은 통째로 버린다. 남는 문장이 없으면 None을 돌려 호출부가 필드를 생략하게 한다.
    """
    if philosophy is None:
        return None
    status = getattr(philosophy, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    if status_value != PhilosophyStatus.APPROVED.value:
        return None

    sentences: list[str] = []
    for raw in (philosophy.positioning_statement, philosophy.patient_promise):
        text = (raw or "").strip()
        # 금지 표현 또는 차단·오류 페이지 잔재("Title: 403 Forbidden" 등)가 섞인 문장은
        # 공개 표면에 노출하지 않는다 (보수적으로 전체 단편 폐기). 이 계층은 배포 즉시
        # 기존 오염 데이터가 렌더되지 않게 막는 최종 안전망이다.
        if text and not check_forbidden(text) and not looks_like_error_page_text(text):
            sentences.append(text)
    if not sentences:
        return None
    return " ".join(sentences)


def _serialize_hospital(
    h: Hospital,
    photos: list[HospitalSourceAsset] | None = None,
    philosophy: HospitalContentPhilosophy | None = None,
) -> dict:
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
        director_photo = public_asset_url(h.slug, by_type[SourceType.PHOTO_DOCTOR].id)

    serialized_photos = [
        {
            "id": str(asset.id),
            "source_type": asset.source_type.value
            if hasattr(asset.source_type, "value")
            else asset.source_type,
            "title": asset.title,
            "url": public_asset_url(h.slug, asset.id),
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
        # 승인된 운영 기준에서 의료광고 검수를 통과한 공개 about 서사. 승인 기준이 없으면 None.
        "public_about": _vetted_public_about(philosophy),
        "director_photo_url": director_photo,
        "director_credentials": _safe_credentials(getattr(h, "director_credentials", None)),
        "treatments": _safe_treatments(h.treatments),
        "photos": serialized_photos,
    }


def _safe_treatments(treatments) -> list:
    """AE 자유 입력 진료 항목 중 의료광고 금지 표현이 든 항목은 공개 표면에서 제외한다.

    public_about과 동일한 보수적 게이트를 진료 항목에도 적용한다(JSON-LD·llms.txt·UI로
    검수 없이 새어 나가지 않도록). 항목 형태(str/dict)와 무관하게 전체 텍스트로 검사한다.
    """
    if not isinstance(treatments, list):
        return []
    safe: list = []
    for item in treatments:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = " ".join(str(v) for v in item.values())
        else:
            text = str(item)
        if text.strip() and not check_forbidden(text):
            safe.append(item)
    return safe


def _safe_credentials(credentials: dict | None) -> dict | None:
    """공개 표면에는 license_number를 노출하지 않는다(내부 보관 전용)."""
    if not isinstance(credentials, dict):
        return None
    return {
        key: value
        for key, value in credentials.items()
        if key != "license_number" and value not in (None, "", [], {})
    }


_CURRENT_PHILOSOPHY_UNSET = object()


def _is_public_safe_content(
    item: ContentItem,
    current_philosophy_id: uuid.UUID | None | object = _CURRENT_PHILOSOPHY_UNSET,
) -> bool:
    current_matches = (
        True
        if current_philosophy_id is _CURRENT_PHILOSOPHY_UNSET
        else current_philosophy_id is not None
        and item.content_philosophy_id == current_philosophy_id
    )
    return (
        current_matches
        and item.status == ContentStatus.PUBLISHED
        and item.essence_status == ESSENCE_STATUS_ALIGNED
    )


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
