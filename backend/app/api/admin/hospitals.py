"""
Admin API — 병원 프로파일 관리
POST   /admin/hospitals                 — 신규 등록
GET    /admin/hospitals                 — 전체 목록
GET    /admin/hospitals/{id}            — 상세 조회
PATCH  /admin/hospitals/{id}/profile    — 프로파일 수정 + 완료 시 V0 트리거
PATCH  /admin/hospitals/{id}/domain     — 공개 도메인 상태 확인
PATCH  /admin/hospitals/{id}/activate   — ACTIVE 전환
"""
import logging
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, HospitalSourceAsset, PhilosophyStatus, SourceStatus
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.schemas.hospital import HospitalDetail, HospitalListItem
from app.services import notifier
from app.services.audit_log import default_actor, write_audit_log
from app.services.hospital_profile_autofill import autofill_profile
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    compute_sources_snapshot_hash,
)
from app.workers.tasks import trigger_v0_report
from app.api.admin.domain import check_domain_dns, domain_dns_strategy_for_hospital
from app.core.config import settings
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Hospitals"])


async def _trigger_v0_report_safe(hospital_id_str: str, hospital_name: str) -> None:
    """V0 리포트 태스크를 큐잉하되, 실패 시 무음으로 사라지지 않도록 운영 알림으로 강등한다.

    BackgroundTasks 안에서 apply_async가 브로커 장애 등으로 예외를 내면 Starlette가
    조용히 삼켜 AE가 프로파일 완료 후 V0가 트리거되지 않은 것을 알 수 없게 된다 (#8).
    """
    try:
        trigger_v0_report.apply_async(args=[hospital_id_str], queue="reports")
    except Exception as exc:  # noqa: BLE001 — 큐잉 실패는 요청 흐름에 영향 없이 강등
        logger.warning("V0 report enqueue failed for hospital %s: %s", hospital_id_str, exc)
        try:
            await notifier.notify_ops_alert(
                title="V0 리포트 큐잉 실패",
                message=(
                    f"병원: {hospital_name}\n"
                    f"프로파일 완료로 V0 리포트를 자동 트리거하려 했지만 큐잉에 실패했습니다.\n"
                    f"오류: `{str(exc)[:200]}`\n"
                    f"Admin 운영 탭에서 수동으로 V0 리포트를 실행해 주세요."
                ),
            )
        except Exception:
            logger.exception("V0 enqueue-failure ops alert delivery failed (non-fatal)")


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


class DirectorCredentials(BaseModel):
    """Physician.hasCredential / alumniOf / memberOf 매핑용."""

    medical_school: str | None = Field(None, max_length=200)
    board_certifications: list[str] | None = None
    society_memberships: list[str] | None = None
    license_number: str | None = Field(None, max_length=50)  # 공개 노출 X, 내부 보관


class HospitalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plan: Plan = Plan.PLAN_8
    onboarding_note: str | None = Field(default=None, max_length=2000)


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

    # 엔티티 식별자 (sameAs 그래프)
    wikidata_qid: str | None = Field(None, max_length=50)
    gbp_place_id: str | None = Field(None, max_length=255)
    naver_place_id: str | None = Field(None, max_length=100)
    kakao_place_id: str | None = Field(None, max_length=100)
    hira_org_id: str | None = Field(None, max_length=50)

    # 타겟
    region: list[str] | None = None
    specialties: list[str] | None = None
    keywords: list[str] | None = None
    competitors: list[str] | None = None

    # 원장
    director_name: str | None = Field(None, max_length=100)
    director_career: str | None = Field(None, max_length=2000)
    director_philosophy: str | None = Field(None, max_length=1000)
    director_credentials: DirectorCredentials | None = None

    # 진료 항목
    treatments: list[TreatmentItem] | None = None

    # 완료 플래그 (프로파일 다 입력됐으면 True로)
    profile_complete: bool | None = None

    @field_validator("wikidata_qid")
    @classmethod
    def validate_wikidata_qid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            return None
        if not re.fullmatch(r"Q\d{1,12}", cleaned):
            raise ValueError("Wikidata Q-ID must look like Q12345")
        return cleaned

    @field_validator(
        "website_url",
        "blog_url",
        "kakao_channel_url",
        "google_business_profile_url",
        "google_maps_url",
        "naver_place_url",
    )
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be absolute http(s)")
        return cleaned


class ProfileAutofillRequest(BaseModel):
    """자동 채우기 입력 — name 미입력 시 병원 등록명을 사용한다."""

    name: str | None = Field(None, max_length=200)
    website_url: str | None = Field(None, max_length=500)
    blog_url: str | None = Field(None, max_length=500)

    @field_validator("website_url", "blog_url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be absolute http(s)")
        return cleaned


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    passed: bool
    weight: int
    next_action: str


READINESS_STATUS_LABELS = {
    "READY": "운영 준비 완료",
    "NEEDS_WORK": "보완 필요",
}
READINESS_CHECK_STATE_LABELS = {
    True: "완료",
    False: "필요",
}
PUBLIC_PROFILE_FIELDS = {
    "address",
    "phone",
    "business_hours",
    "website_url",
    "blog_url",
    "kakao_channel_url",
    "google_business_profile_url",
    "google_maps_url",
    "naver_place_url",
    "latitude",
    "longitude",
    "region",
    "specialties",
    "keywords",
    "director_name",
    "director_career",
    "director_philosophy",
    "treatments",
}


def _readiness_status_label(status_value: str) -> str:
    return READINESS_STATUS_LABELS.get(status_value, status_value)


def _serialize_readiness_check(check: ReadinessCheck) -> dict:
    return {
        "key": check.key,
        "label": check.label,
        "passed": check.passed,
        "weight": check.weight,
        "next_action": check.next_action,
        "display": {
            "state_label": READINESS_CHECK_STATE_LABELS[check.passed],
        },
    }


# ── 엔드포인트 ────────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_201_CREATED, response_model=HospitalDetail)
async def create_hospital(body: HospitalCreate, db: AsyncSession = Depends(get_db)):
    """신규 병원 등록 (계약 완료 후 AE가 첫 번째로 실행)"""
    slug = slugify(body.name, separator="-")
    # slug 중복 방지
    existing = await db.execute(select(Hospital).where(Hospital.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    hospital = Hospital(
        name=body.name,
        slug=slug,
        plan=body.plan,
        onboarding_note=body.onboarding_note or "Created from admin hospital registration.",
    )
    db.add(hospital)
    # 순서 규약: write_audit_log → db.commit(). flush로 hospital.id를 먼저 확보한다.
    # check-then-insert 경합(같은 slug/도메인 동시 등록)은 500이 아니라 409로 변환한다.
    try:
        await db.flush()
        await write_audit_log(
            db,
            action="create_hospital",
            hospital_id=hospital.id,
            actor=default_actor(),
            target_type="hospital",
            target_id=hospital.id,
            detail={
                "name": hospital.name,
                "slug": slug,
                "plan": _enum_value(body.plan, Plan.PLAN_8.value),
            },
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="이미 사용 중인 슬러그 또는 도메인입니다. 병원명을 확인해 주세요.",
        ) from exc
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
        "naver_place_url", "latitude", "longitude",
        "wikidata_qid", "gbp_place_id", "naver_place_id", "kakao_place_id", "hira_org_id",
        "region", "specialties", "keywords", "competitors",
        "director_name", "director_career", "director_philosophy", "director_credentials",
        "treatments",
        "profile_complete",
    }
    # exclude_unset: 보내지 않은 필드는 유지하되, 명시적 null/빈 문자열은 '비우기'로
    # 처리한다. exclude_none이었을 때는 잘못 입력된 URL/식별자를 지울 API 경로가 없었다.
    # 비우기는 nullable 선택 필드에만 허용 — 필수·NOT NULL 필드의 null은 기존처럼 무시.
    CLEARABLE_FIELDS = {
        "website_url", "blog_url", "kakao_channel_url", "google_business_profile_url",
        "google_maps_url", "naver_place_url", "latitude", "longitude",
        "wikidata_qid", "gbp_place_id", "naver_place_id", "kakao_place_id", "hira_org_id",
        "director_career", "director_philosophy", "director_credentials",
    }
    update_data = body.model_dump(exclude_unset=True)
    was_complete = h.profile_complete
    changed_fields: list[str] = []
    for field, value in update_data.items():
        if field not in PROFILE_FIELDS:
            continue
        if value is None and field not in CLEARABLE_FIELDS:
            continue
        if getattr(h, field, None) != value:
            changed_fields.append(field)
        setattr(h, field, value)

    # 필수 필드 검증 — 완료 전환 시점뿐 아니라, 이미 완료된 프로파일이 PATCH로 필수
    # 필드를 빈 값([]/"")으로 비우는 것도 차단한다 (P2-10). 완료 플래그가 True로
    # 유지되는 한 V0/콘텐츠 생성 파이프라인이 이 필드들에 의존한다.
    if h.profile_complete:
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
            if was_complete:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"프로파일 완료 상태에서는 필수 항목을 비울 수 없습니다: "
                        f"{', '.join(required_missing)}. 값을 입력하거나 profile_complete를 해제해 주세요."
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail=f"프로파일 완료에 필요한 필드 누락: {', '.join(required_missing)}",
            )

    if changed_fields:
        await write_audit_log(
            db,
            action="update_profile",
            hospital_id=hospital_id,
            actor=default_actor(),
            target_type="hospital",
            target_id=hospital_id,
            detail={
                "changed_fields": changed_fields,
                "profile_complete_transition": (not was_complete) and bool(h.profile_complete),
            },
        )

    needs_site_revalidate = _has_public_site(h) and any(field in PUBLIC_PROFILE_FIELDS for field in changed_fields)
    if needs_site_revalidate:
        ensure_site_revalidate_configured()

    await db.commit()
    await db.refresh(h)

    # 프로파일 완료로 변경된 경우 V0 분석 자동 트리거 (큐잉 실패는 운영 알림으로 강등)
    if not was_complete and h.profile_complete:
        background_tasks.add_task(_trigger_v0_report_safe, str(hospital_id), h.name)
    if needs_site_revalidate:
        # 커밋 이후이므로 실패해도 raise하지 않는다 (R4) — 저장은 이미 성공했다.
        await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)

    return _serialize(h)


@router.post("/{hospital_id}/profile/autofill")
async def autofill_hospital_profile(
    hospital_id: uuid.UUID,
    body: ProfileAutofillRequest,
    db: AsyncSession = Depends(get_db),
):
    """병원명 + URL로 온라인 정보(홈페이지·블로그·네이버 플레이스)를 스크랩해 프로파일 초안 생성.

    **저장하지 않는다.** 초안(draft) + 필드별 출처/신뢰도(field_meta) + 의료광고 위반(violations)
    + 소스 상태(sources)를 반환하고, AE가 검수 후 PATCH /profile 로 저장한다. best-effort —
    일부 소스가 막혀도 가능한 필드만 채운다.
    """
    h = await _get_or_404(db, hospital_id)
    name = (body.name or h.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="병원명이 필요합니다.")
    website_url = body.website_url or h.website_url
    blog_url = body.blog_url or h.blog_url

    result = await autofill_profile(name, website_url=website_url, blog_url=blog_url)

    await write_audit_log(
        db,
        action="autofill_profile",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital_id,
        detail={
            "name": name,
            "filled_fields": sorted(result.draft.keys()),
            "violation_fields": [v["field"] for v in result.violations],
            "sources": [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources],
        },
    )
    await db.commit()

    return {
        "draft": result.draft,
        "field_meta": result.field_meta,
        "violations": result.violations,
        "naver_place_id": result.naver_place_id,
        "sources": [
            {"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources
        ],
    }


@router.patch("/{hospital_id}/activate")
async def activate_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """ACTIVE 상태로 전환 (공개 도메인/노출 상태 + 스케줄 설정 완료 후)"""
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

    # 하이브리드 도메인: 자기 도메인이 연결돼 있으면 그 DNS를 검증하고, 없으면
    # 기본 서브도메인({slug}.{platform host}, 와일드카드 cert+A로 커버)으로 라이브한다.
    # 자기 도메인 검증 실패는 라이브를 막지만(운영자가 명시 연결한 경우), 미연결은 막지 않는다.
    dns_check = None
    if h.aeo_domain:
        dns_check = await check_domain_dns(h.aeo_domain, domain_dns_strategy_for_hospital(h))
        if not dns_check.verified:
            address_hint = (
                f" 또는 A/AAAA {', '.join(dns_check.expected_addresses)}"
                if dns_check.expected_addresses
                else ""
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"DNS 설정이 확인되지 않았습니다. "
                    f"{h.aeo_domain} → {settings.CNAME_TARGET}{address_hint} 로 설정해 주세요."
                ),
            )

    previous_status = h.status.value if hasattr(h.status, "value") else str(h.status)
    ensure_site_revalidate_configured()
    h.status = HospitalStatus.ACTIVE
    h.site_live = True
    await write_audit_log(
        db,
        action="activate_hospital",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital_id,
        detail={
            "previous_status": previous_status,
            "new_status": HospitalStatus.ACTIVE.value,
            "aeo_domain": h.aeo_domain,
            "cname_value": dns_check.cname_value if dns_check else None,
            "address_values": dns_check.address_values if dns_check else [],
            "verification_method": dns_check.verification_method if dns_check else "platform_subdomain",
        },
    )
    await db.commit()
    # 커밋 이후이므로 실패해도 raise하지 않는다 (R4) — 활성화는 이미 성공했다.
    await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)
    return {"detail": f"{h.name} activated"}


def _activation_missing(h: Hospital) -> list[str]:
    """activate 게이트와 동일한 사전 조건 목록 — pause/resume가 재사용한다 (#11)."""
    missing = []
    if not h.profile_complete:
        missing.append("profile_complete")
    if not h.v0_report_done:
        missing.append("v0_report_done")
    if not h.site_built:
        missing.append("site_built")
    if not h.schedule_set:
        missing.append("schedule_set")
    return missing


@router.post("/{hospital_id}/pause", response_model=HospitalDetail)
async def pause_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """병원 운영을 일시 정지 (ACTIVE 또는 PENDING_DOMAIN 상태에서만 허용)."""
    h = await _get_or_404(db, hospital_id)

    if h.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
        raise HTTPException(
            status_code=409,
            detail="일시 정지는 ACTIVE 또는 PENDING_DOMAIN 상태에서만 가능합니다.",
        )

    previous_status = h.status.value if hasattr(h.status, "value") else str(h.status)
    h.status = HospitalStatus.PAUSED
    await write_audit_log(
        db,
        action="pause_hospital",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital_id,
        detail={
            "previous_status": previous_status,
            "new_status": HospitalStatus.PAUSED.value,
        },
    )
    await db.commit()
    await db.refresh(h)
    return _serialize(h)


@router.post("/{hospital_id}/resume", response_model=HospitalDetail)
async def resume_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """일시 정지된 병원을 재개 (PAUSED 상태에서만 허용).

    이전 활성 조건(activate 게이트 + site_live)이 여전히 충족되면 ACTIVE로, 아니면
    PENDING_DOMAIN으로 복귀한다.
    """
    h = await _get_or_404(db, hospital_id)

    if h.status != HospitalStatus.PAUSED:
        raise HTTPException(status_code=409, detail="재개는 PAUSED 상태에서만 가능합니다.")

    missing = _activation_missing(h)
    if not missing and h.site_live:
        h.status = HospitalStatus.ACTIVE
    else:
        h.status = HospitalStatus.PENDING_DOMAIN

    await write_audit_log(
        db,
        action="resume_hospital",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital_id,
        detail={
            "previous_status": HospitalStatus.PAUSED.value,
            "new_status": h.status.value,
            "activation_missing": missing,
            "site_live": bool(h.site_live),
        },
    )
    await db.commit()
    await db.refresh(h)
    return _serialize(h)


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
            "구글 병원 정보 또는 구글 지도 URL을 입력하세요.",
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
            "승인된 콘텐츠 운영 기준이 없거나 재검토가 필요한 콘텐츠를 수정하세요.",
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
            "콘텐츠 허브 노출 준비",
            bool(h.site_built),
            10,
            "승인된 병원 정보와 콘텐츠가 공개 표면에 노출될 준비를 완료하세요.",
        ),
        ReadinessCheck(
            "domain",
            "공개 도메인 상태",
            bool(h.aeo_domain and h.site_live),
            10,
            "병원 정보 허브의 공개 도메인과 노출 상태를 확인하세요.",
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
            "ChatGPT/Gemini에 확인할 환자 질문 측정을 실행하세요.",
        ),
    ]

    total_weight = sum(c.weight for c in checks)
    earned = sum(c.weight for c in checks if c.passed)
    score = round(earned / total_weight * 100)

    readiness_status = (
        "READY"
        if score >= 80 and h.site_live and approved_philosophy is not None and essence_fresh
        else "NEEDS_WORK"
    )

    return {
        "hospital_id": str(h.id),
        "score": score,
        "status": readiness_status,
        "display": {
            "status_label": _readiness_status_label(readiness_status),
        },
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
        "checks": [_serialize_readiness_check(c) for c in checks],
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


def _has_public_site(h: Hospital) -> bool:
    return h.status == HospitalStatus.ACTIVE and bool(h.site_live)


def _serialize(h: Hospital) -> dict:
    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "status": h.status,
        "plan": h.plan,
        "source_lead_id": str(h.source_lead_id) if h.source_lead_id else None,
        "onboarding_note": h.onboarding_note,
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
        "domain_management_mode": _enum_value(getattr(h, "domain_management_mode", None), "HOSPITAL_MANAGED"),
        "domain_dns_strategy": _enum_value(getattr(h, "domain_dns_strategy", None), "CNAME"),
        "domain_registrar": getattr(h, "domain_registrar", None),
        "domain_dns_provider": getattr(h, "domain_dns_provider", None),
        "domain_purchase_note": getattr(h, "domain_purchase_note", None),
        "latitude": h.latitude,
        "longitude": h.longitude,
        "wikidata_qid": h.wikidata_qid,
        "gbp_place_id": h.gbp_place_id,
        "naver_place_id": h.naver_place_id,
        "kakao_place_id": h.kakao_place_id,
        "hira_org_id": h.hira_org_id,
        "region": h.region,
        "specialties": h.specialties,
        "keywords": h.keywords,
        "competitors": h.competitors,
        "director_name": h.director_name,
        "director_career": h.director_career,
        "director_philosophy": h.director_philosophy,
        "director_credentials": h.director_credentials,
        "treatments": h.treatments,
        "profile_complete": h.profile_complete,
        "v0_report_done": h.v0_report_done,
        "site_built": h.site_built,
        "site_live": h.site_live,
        "schedule_set": h.schedule_set,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def _enum_value(value: object, default: str) -> str:
    if value is None:
        return default
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _serialize_list(h: Hospital) -> dict:
    return {
        "id": str(h.id),
        "name": h.name,
        "slug": h.slug,
        "status": h.status,
        "plan": h.plan,
        "source_lead_id": str(h.source_lead_id) if h.source_lead_id else None,
        "profile_complete": h.profile_complete,
        "v0_report_done": h.v0_report_done,
        "site_built": h.site_built,
        "site_live": h.site_live,
        "schedule_set": h.schedule_set,
        "aeo_domain": h.aeo_domain,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
