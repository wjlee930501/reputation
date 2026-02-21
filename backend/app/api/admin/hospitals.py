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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.schemas.hospital import HospitalDetail, HospitalListItem
from app.workers.tasks import trigger_v0_report, build_aeo_site

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
    business_hours: dict | None = None

    # URL
    website_url: str | None = Field(None, max_length=500)
    blog_url: str | None = Field(None, max_length=500)
    kakao_channel_url: str | None = Field(None, max_length=500)

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
    treatments: list[dict] | None = None

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

    update_data = body.model_dump(exclude_none=True)
    was_complete = h.profile_complete
    for field, value in update_data.items():
        setattr(h, field, value)

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
    h.aeo_domain = body.domain
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
    """ACTIVE 상태로 전환 (도메인 연결 + 스케줄 설정 완료 후)"""
    h = await _get_or_404(db, hospital_id)
    h.status = HospitalStatus.ACTIVE
    h.site_live = True
    await db.commit()
    return {"detail": f"{h.name} activated"}


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


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
        "aeo_domain": h.aeo_domain,
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
        "site_live": h.site_live,
        "schedule_set": h.schedule_set,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
