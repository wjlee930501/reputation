"""
Admin API — 병원 프로파일 관리
POST   /admin/hospitals                 — 신규 등록
GET    /admin/hospitals                 — 전체 목록
GET    /admin/hospitals/{id}            — 상세 조회
PATCH  /admin/hospitals/{id}/profile    — 프로파일 수정 + 완료 시 V0 트리거
PATCH  /admin/hospitals/{id}/domain     — 도메인 연결
PATCH  /admin/hospitals/{id}/activate   — ACTIVE 전환
"""
import re
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, HospitalSourceAsset, PhilosophyStatus, SourceStatus
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.schemas.hospital import HospitalDetail, HospitalListItem
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    compute_sources_snapshot_hash,
)
from app.workers.tasks import trigger_v0_report, build_aeo_site
from app.api.admin.domain import _normalize_dns_name, _resolve_cname
from app.core.config import settings

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Hospitals"])

# 도메인 검증 정규식 (예: info.jangpyeon.com)
_DOMAIN_RE = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


# ── Pydantic 스키마 ───────────────────────────────────────────────
class BusinessHours(BaseModel):
    mon: str | None = Field(None, max_length=100)
    tue: str | None = Field(None, max_length=100)
    wed: str | None = Field(None, max_length=100)
    thu: str | None = Field(None, max_length=100)
    fri: str | None = Field(None, max_length=100)
    sat: str | None = Field(None, max_length=100)
    sun: str | None = Field(None, max_length=100)


class TreatmentItem(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = Field(None, max_length=500)


class HospitalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plan: Plan


class HospitalProfileUpdate(BaseModel):
    # 연락처
    address: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    business_hours: BusinessHours | None = None

    # URL
    website_url: str | None = Field(None, max_length=500)
    blog_url: str | None = Field(None, max_length=500)
    kakao_channel_url: str | None = Field(None, max_length=500)
    google_business_profile_url: str | None = Field(None, max_length=500)
    google_maps_url: str | None = Field(None, max_length=500)
    naver_place_url: str | None = Field(None, max_length=500)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    # 타겟
    region: list[str] | None = None
    specialties: list[str] | None = None
    keywords: list[str] | None = None
    competitors: list[str] | None = None

    # 원장
    director_name: str | None = Field(None, max_length=100)
    director_career: str | None = Field(None, max_length=2000)
    director_philosophy: str | None = Field(None, max_length=1000)

    # 진료 항목
    treatments: list[TreatmentItem] | None = None

    # 완료 플래그 (프로파일 다 입력됐으면 True로)
    profile_complete: bool | None = None


class DomainConnect(BaseModel):
    domain: str = Field(min_length=4, max_length=253)  # 예: "info.jangpyeon.com"

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        if not _DOMAIN_RE.match(v):
            raise ValueError("Invalid domain format")
        return v.lower()


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    passed: bool
    weight: int
    next_action: str


# ── 엔드포인트 ────────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_201_CREATED, response_model=HospitalDetail)
async def create_hospital(body: HospitalCreate, db: AsyncSession = Depends(get_db)):
    """신규 병원 등록 (계약 완료 후 AE가 첫 번째로 실행)"""
    slug = slugify(body.name, separator="-")
    # slug 중복 방지
    existing = await db.execute(select(Hospital).where(Hospital.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    hospital = Hospital(name=body.name, slug=slug, plan=body.plan)
    db.add(hospital)
    await db.commit()
    await db.refresh(hospital)
    return _serialize(hospital)


@router.get("", response_model=list[HospitalListItem])
async def list_hospitals(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """전체 병원 목록 — 상태별 필터링"""
    result = await db.execute(
        select(Hospital).order_by(Hospital.created_at.desc()).offset(skip).limit(limit)
    )
    hospitals = result.scalars().all()
    return [_serialize_list(h) for h in hospitals]


@router.get("/{hospital_id}", response_model=HospitalDetail)
async def get_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    h = await _get_or_404(db, hospital_id)
    return _serialize(h)


@router.patch("/{hospital_id}/profile", response_model=HospitalDetail)
async def update_profile(
    hospital_id: uuid.UUID,
    body: HospitalProfileUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    프로파일 수정.
    profile_complete=True 설정 시 자동으로 V0 분석 트리거.
    """
    h = await _get_or_404(db, hospital_id)

    PROFILE_FIELDS = {
        "address", "phone", "business_hours", "website_url", "blog_url",
        "kakao_channel_url", "google_business_profile_url", "google_maps_url",
        "naver_place_url", "latitude", "longitude", "region", "specialties", "keywords", "competitors",
        "director_name", "director_career", "director_philosophy", "treatments",
        "profile_complete",
    }
    update_data = body.model_dump(exclude_none=True)
    was_complete = h.profile_complete
    for field, value in update_data.items():
        if field not in PROFILE_FIELDS:
            continue
        setattr(h, field, value)

    # 프로파일 완료 시 필수 필드 검증
    if h.profile_complete and not was_complete:
        required_missing = []
        if not h.region:
            required_missing.append("region")
        if not h.specialties:
            required_missing.append("specialties")
        if not h.keywords:
            required_missing.append("keywords")
        if not h.director_name:
            required_missing.append("director_name")
        if not h.address:
            required_missing.append("address")
        if required_missing:
            raise HTTPException(
                status_code=400,
                detail=f"프로파일 완료에 필요한 필드 누락: {', '.join(required_missing)}",
            )

    await db.commit()
    await db.refresh(h)

    # 프로파일 완료로 변경된 경우 V0 분석 자동 트리거
    if not was_complete and h.profile_complete:
        background_tasks.add_task(
            trigger_v0_report.apply_async,
            args=[str(hospital_id)],
            queue="reports",
        )

    return _serialize(h)


@router.patch("/{hospital_id}/domain")
async def connect_domain(
    hospital_id: uuid.UUID,
    body: DomainConnect,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """도메인 연결 완료 처리 + 사이트 리빌드"""
    h = await _get_or_404(db, hospital_id)
    previous_domain = h.aeo_domain
    domain_changed = _normalize_dns_name(previous_domain) != _normalize_dns_name(body.domain)
    h.aeo_domain = body.domain

    # 도메인이 바뀌면 기존 DNS 검증은 더 이상 유효하지 않다.
    # 이미 라이브였던 병원이 새 미검증 도메인으로도 라이브처럼 보이는 것을 막는다.
    if domain_changed:
        h.site_live = False
        if h.status == HospitalStatus.ACTIVE:
            h.status = HospitalStatus.PENDING_DOMAIN

    await db.commit()

    # 사이트 리빌드 (도메인 반영)
    background_tasks.add_task(
        build_aeo_site.apply_async,
        args=[str(hospital_id)],
        queue="default",
    )
    return {"detail": f"Domain {body.domain} set. Site rebuild triggered."}


@router.patch("/{hospital_id}/activate")
async def activate_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """ACTIVE 상태로 전환 (도메인 DNS 검증 + 스케줄 설정 완료 후)"""
    h = await _get_or_404(db, hospital_id)

    missing = []
    if not h.profile_complete:
        missing.append("profile_complete")
    if not h.v0_report_done:
        missing.append("v0_report_done")
    if not h.site_built:
        missing.append("site_built")
    if not h.schedule_set:
        missing.append("schedule_set")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"활성화 사전 조건 미충족: {', '.join(missing)}",
        )

    if not h.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.")

    cname_value = _resolve_cname(h.aeo_domain)
    if _normalize_dns_name(cname_value) != _normalize_dns_name(settings.CNAME_TARGET):
        raise HTTPException(
            status_code=400,
            detail=(
                f"CNAME 설정이 확인되지 않았습니다. "
                f"{h.aeo_domain} → {settings.CNAME_TARGET} 로 설정해 주세요."
            ),
        )

    h.status = HospitalStatus.ACTIVE
    h.site_live = True
    await db.commit()
    return {"detail": f"{h.name} activated"}


@router.get("/{hospital_id}/readiness")
async def get_readiness(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """병원별 AI 검색 운영 준비도를 계산한다."""
    h = await _get_or_404(db, hospital_id)

    published_count = await _count(
        db,
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.hospital_id == h.id, ContentItem.status == ContentStatus.PUBLISHED),
    )
    sov_count = await _count(
        db,
        select(func.count()).select_from(SovRecord).where(SovRecord.hospital_id == h.id),
    )
    report_count = await _count(
        db,
        select(func.count()).select_from(MonthlyReport).where(MonthlyReport.hospital_id == h.id),
    )
    processed_sources_result = await db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.hospital_id == h.id,
            HospitalSourceAsset.status == SourceStatus.PROCESSED,
        )
    )
    processed_sources = processed_sources_result.scalars().all()
    approved_result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == h.id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    approved_philosophy = approved_result.scalar_one_or_none()
    current_snapshot_hash = compute_sources_snapshot_hash(processed_sources)
    essence_fresh = bool(
        approved_philosophy
        and processed_sources
        and approved_philosophy.source_snapshot_hash == current_snapshot_hash
    )
    essence_blocked_content_count = await _count(
        db,
        select(func.count())
        .select_from(ContentItem)
        .where(
            ContentItem.hospital_id == h.id,
            ContentItem.essence_status.in_([
                ESSENCE_STATUS_MISSING_APPROVED,
                ESSENCE_STATUS_NEEDS_REVIEW,
            ]),
        ),
    )

    has_core_profile = all([
        h.name,
        h.address,
        h.phone,
        h.region,
        h.specialties,
        h.keywords,
        h.director_name,
        h.treatments,
    ])
    has_local_entity = bool(h.google_business_profile_url or h.google_maps_url)
    has_external_profiles = bool(
        h.website_url or h.blog_url or h.kakao_channel_url or h.naver_place_url
    )

    checks = [
        ReadinessCheck(
            "core_profile",
            "병원 핵심 프로파일",
            bool(has_core_profile),
            18,
            "프로파일에서 주소, 전화, 지역, 진료과목, 키워드, 원장, 진료항목을 채우세요.",
        ),
        ReadinessCheck(
            "local_entity",
            "Google 지도·프로필 정보",
            has_local_entity,
            14,
            "Google Business Profile 또는 Google Maps URL을 입력하세요.",
        ),
        ReadinessCheck(
            "external_profiles",
            "외부 공식 채널",
            has_external_profiles,
            8,
            "기존 홈페이지, 블로그, 카카오 채널, Naver Place 중 하나 이상을 연결하세요.",
        ),
        ReadinessCheck(
            "essence_sources",
            "콘텐츠 운영 기준 자료",
            len(processed_sources) > 0,
            12,
            "운영 기준 탭에서 병원 자료를 입력하고 근거 추출을 완료하세요.",
        ),
        ReadinessCheck(
            "essence_philosophy",
            "승인된 콘텐츠 운영 기준",
            approved_philosophy is not None,
            14,
            "운영 기준 탭에서 근거 기반 콘텐츠 운영 기준 초안을 생성하고 승인하세요.",
        ),
        ReadinessCheck(
            "essence_freshness",
            "운영 기준 자료 최신성",
            essence_fresh,
            8,
            "처리된 병원 자료가 바뀌었습니다. 콘텐츠 운영 기준 새 버전을 검토하세요.",
        ),
        ReadinessCheck(
            "content_alignment",
            "콘텐츠 운영 기준 정렬",
            essence_blocked_content_count == 0,
            6,
            "승인 철학 누락 또는 재검수 상태 콘텐츠를 수정하세요.",
        ),
        ReadinessCheck(
            "v0_report",
            "V0 진단 리포트",
            bool(h.v0_report_done or report_count > 0),
            12,
            "프로파일 완료 후 V0 리포트를 생성하세요.",
        ),
        ReadinessCheck(
            "site_built",
            "AI 노출 웹블로그 준비",
            bool(h.site_built),
            10,
            "AI 노출 웹블로그 준비 작업을 완료하세요.",
        ),
        ReadinessCheck(
            "domain",
            "도메인 연결",
            bool(h.aeo_domain and h.site_live),
            10,
            "병원 정보 허브 도메인을 입력하고 DNS 검증을 완료하세요.",
        ),
        ReadinessCheck(
            "schedule",
            "콘텐츠 스케줄",
            bool(h.schedule_set),
            8,
            "월간 콘텐츠 스케줄을 설정하세요.",
        ),
        ReadinessCheck(
            "published_content",
            "발행 콘텐츠",
            published_count > 0,
            12,
            "초안 콘텐츠를 검수하고 최소 1편 이상 발행하세요.",
        ),
        ReadinessCheck(
            "sov_data",
            "AI 답변 언급률 측정 데이터",
            sov_count > 0,
            8,
            "ChatGPT/Gemini 질의 세트 측정을 실행하세요.",
        ),
    ]

    total_weight = sum(c.weight for c in checks)
    earned = sum(c.weight for c in checks if c.passed)
    score = round(earned / total_weight * 100)

    return {
        "hospital_id": str(h.id),
        "score": score,
        "status": (
            "READY"
            if score >= 80 and h.site_live and approved_philosophy is not None and essence_fresh
            else "NEEDS_WORK"
        ),
        "published_content_count": published_count,
        "sov_record_count": sov_count,
        "report_count": report_count,
        "essence": {
            "processed_source_count": len(processed_sources),
            "approved_philosophy_exists": approved_philosophy is not None,
            "philosophy_version": approved_philosophy.version if approved_philosophy else None,
            "source_stale": bool(approved_philosophy and not essence_fresh),
            "blocked_content_count": essence_blocked_content_count,
        },
        "checks": [
            {
                "key": c.key,
                "label": c.label,
                "passed": c.passed,
                "weight": c.weight,
                "next_action": c.next_action,
            }
            for c in checks
        ],
    }


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


async def _count(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


def _serialize(h: Hospital) -> dict:
    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "status": h.status,
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
        "competitors": h.competitors,
        "director_name": h.director_name,
        "director_career": h.director_career,
        "director_philosophy": h.director_philosophy,
        "treatments": h.treatments,
        "profile_complete": h.profile_complete,
        "v0_report_done": h.v0_report_done,
        "site_built": h.site_built,
        "site_live": h.site_live,
        "schedule_set": h.schedule_set,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def _serialize_list(h: Hospital) -> dict:
    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "status": h.status,
        "plan": h.plan,
        "profile_complete": h.profile_complete,
        "v0_report_done": h.v0_report_done,
        "site_built": h.site_built,
        "site_live": h.site_live,
        "schedule_set": h.schedule_set,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
