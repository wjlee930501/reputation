"""섹션 설정 검증·공개 정리에 필요한 병원 사실을 DB에서 읽는다.

공개 판정은 새로 만들지 않고 공개 API의 판정을 그대로 쓴다 — 사진은 `get_hospital_public`과 같은
공개 사진 조회와 원장 사진 신원 판정(`doctor_identity_asset_ids`), FAQ 글은 공개 글 목록과 같은 조회와
공개 가시성(`_load_public_safe_items`)·승인 판(`approved_public_view`)이다. 두 화면이 갈라지지 않게 한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.essence import PHOTO_SOURCE_TYPES, HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital
from app.models.site_config import SITE_CONFIG_PUBLISHED, HospitalSiteConfig
from app.services import site_defaults
from app.services.site_config_validation import SiteConfigContext

FAQ_SCAN_LIMIT = 500


async def public_photos(db: AsyncSession, hospital_id: uuid.UUID) -> list[HospitalSourceAsset]:
    """공개 API가 내보내는 사진과 같은 집합(`get_hospital_public`)."""

    result = await db.execute(
        select(HospitalSourceAsset)
        .where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.is_public.is_(True),
            HospitalSourceAsset.status != SourceStatus.EXCLUDED,
            HospitalSourceAsset.source_type.in_(list(PHOTO_SOURCE_TYPES)),
            HospitalSourceAsset.file_url.is_not(None),
        )
        .order_by(HospitalSourceAsset.updated_at.desc())
    )
    return list(result.scalars().all())


def share_image_ids(photos: Sequence[HospitalSourceAsset]) -> frozenset[uuid.UUID]:
    """공유 이미지로 쓸 수 있는 공개 사진 — 원장 사진은 실인물·신원 사용 승인을 넘은 것만."""

    from app.api.public.physicians import doctor_identity_asset_ids

    identity = doctor_identity_asset_ids(photos)
    return frozenset(
        asset.id
        for asset in photos
        if asset.source_type != SourceType.PHOTO_DOCTOR or asset.id in identity
    )


async def public_faq_entries(
    db: AsyncSession, hospital: Hospital, *, ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, dict[str, str]]:
    """공개 중인 글 중 승인 질문·답이 있는 것 → {id: {question, answer}}.

    `ids`가 있으면 그 글만 본다. 공개 글 목록과 같은 조건에 FAQ 유형을 더한다 — 병원이 글을 내보내지
    않거나 공개 운영 기준이 없으면 비어 있다.
    """

    from app.api.public.site import _load_public_safe_items, is_public_serving_hospital
    from app.models.content import ContentItem, ContentStatus, ContentType
    from app.services.content_visibility import approved_public_view
    from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
    from app.services.essence_readiness import get_public_essence_readiness

    wanted = list(dict.fromkeys(ids)) if ids is not None else None
    if wanted is not None and not wanted:
        return {}
    if not is_public_serving_hospital(hospital):
        return {}
    philosophy = await get_public_essence_readiness(db, hospital.id)
    if philosophy is None:
        return {}
    stmt = (
        select(ContentItem)
        .options(selectinload(ContentItem.active_revision))
        .where(
            ContentItem.hospital_id == hospital.id,
            # 홈 FAQ와 같은 집합 — 사이트 `selectFaqEntries`도 FAQ 유형 글만 쓴다.
            ContentItem.content_type == ContentType.FAQ,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.active_revision_id.is_not(None),
            ContentItem.essence_status == ESSENCE_STATUS_ALIGNED,
            ContentItem.content_philosophy_id == philosophy.id,
        )
        .order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
    )
    if wanted is not None:
        stmt = stmt.where(ContentItem.id.in_(wanted))
    limit = len(wanted) if wanted is not None else FAQ_SCAN_LIMIT
    items = await _load_public_safe_items(db, stmt, offset=0, limit=limit)
    entries: dict[uuid.UUID, dict[str, str]] = {}
    for item in items:
        view = approved_public_view(item)
        question = (getattr(view, "faq_question", None) or "").strip() if view else ""
        answer = (getattr(view, "faq_answer_summary", None) or "").strip() if view else ""
        if question and answer:
            entries[item.id] = {"question": question, "answer": answer}
    return entries


def requested_faq_ids(config: object) -> list[uuid.UUID]:
    """설정이 고른 FAQ 글 id(형식이 틀린 값은 버린다 — 형식 오류는 스키마 검증이 알린다)."""

    faq = config.get("faq") if isinstance(config, dict) else None
    raw_ids = faq.get("content_ids") if isinstance(faq, dict) else None
    ids: list[uuid.UUID] = []
    for raw in raw_ids if isinstance(raw_ids, list) else []:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return ids[:20]


async def load_context(db: AsyncSession, hospital: Hospital, config: object = None) -> SiteConfigContext:
    """검증 맥락. FAQ는 설정이 고른 글만 그 id로 직접 조회한다(최근 글 몇 건으로 자르지 않는다)."""

    photos = await public_photos(db, hospital.id)
    faq = await public_faq_entries(db, hospital, ids=requested_faq_ids(config))
    return text_context(hospital, share_ids=share_image_ids(photos), faq_ids=frozenset(faq))


def text_context(
    hospital: Hospital,
    *,
    share_ids: frozenset[uuid.UUID] = frozenset(),
    faq_ids: frozenset[uuid.UUID] = frozenset(),
) -> SiteConfigContext:
    return SiteConfigContext(
        hero_headline=getattr(hospital, "hero_headline", None),
        hero_description=getattr(hospital, "hero_description", None),
        care_principles=getattr(hospital, "care_principles", None),
        share_image_ids=share_ids,
        faq_content_ids=faq_ids,
    )


async def published_config(db: AsyncSession, hospital_id: uuid.UUID) -> HospitalSiteConfig | None:
    return await db.scalar(
        select(HospitalSiteConfig).where(
            HospitalSiteConfig.hospital_id == hospital_id,
            HospitalSiteConfig.state == SITE_CONFIG_PUBLISHED,
        )
    )


async def public_site_config_for(
    db: AsyncSession,
    hospital: Hospital,
    row: HospitalSiteConfig | None,
    photos: Sequence[HospitalSourceAsset],
    *,
    layout: str | None = None,
) -> dict | None:
    """공개 응답의 `site_config`. 설정이 없으면 None(사이트는 지금 화면 그대로)."""

    from app.services.site_config_public import PublicSiteConfigContext, public_site_config

    if row is None:
        return None
    # 옛 판의 아직 읽지 않던 칸은 지금 기본값으로 다시 합친다(공개·미리보기 모두 여기를 지난다).
    resolved = site_defaults.refresh_stale_defaults(row.resolved or {}, row.config)
    faq = resolved.get("faq") or {}
    faq_entries: dict[uuid.UUID, dict[str, str]] = {}
    if faq.get("mode") == "manual" and faq.get("content_ids"):
        faq_entries = await public_faq_entries(db, hospital, ids=requested_faq_ids(resolved))
    faq_available: bool | None = None
    nav_items = (resolved.get("nav") or {}).get("items") or []
    if faq.get("mode") != "manual" and "faq" in nav_items:
        # 메뉴에 'FAQ'를 넣은 병원만 센다 — 하위 페이지 머리는 홈 FAQ를 모른다.
        faq_available = bool(await public_faq_entries(db, hospital))
    return public_site_config(
        resolved,
        version=row.version,
        ctx=PublicSiteConfigContext(
            layout=layout if layout is not None else getattr(hospital, "site_layout", None),
            texts=text_context(hospital, share_ids=share_image_ids(photos)),
            faq_entries=faq_entries,
            faq_available=faq_available,
        ),
    )


__all__ = (
    "load_context",
    "public_faq_entries",
    "public_photos",
    "public_site_config_for",
    "published_config",
    "share_image_ids",
    "text_context",
)
