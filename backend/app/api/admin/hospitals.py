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
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from slugify import slugify
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.api.admin.domain import (
    check_domain_dns,
    domain_dns_strategy_for_hospital,
)
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.content import ContentItem, ContentStatus
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.report import V0_REPORT_TYPE, MonthlyReport
from app.models.sov import SovRecord
from app.schemas.hospital import HospitalDetail, HospitalListItem
from app.services.audit_log import default_actor, write_audit_log
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
)
from app.services.essence_readiness import get_essence_readiness
from app.services.hospital_duplicates import find_duplicate_hospitals, normalize_hospital_name
from app.services.hospital_lifecycle import (
    activation_gate_error,
    evaluate_activation_gate,
    missing_profile_requirement_keys,
)
from app.services.hospital_profile_autofill import autofill_profile
from app.services.keyword_analysis import (
    analyze_keyword,
)
from app.services.keyword_analysis import (
    normalize as normalize_keyword,
)
from app.services.operation_runs import (
    OperationCommand,
    OperationQueueUnavailable,
    dispatch_operation,
)
from app.services.readiness_operator_copy import readiness_next_actions
from app.services.service_intervals import (
    ServiceIntervalProvenance,
    close_service_interval,
    open_service_interval,
)
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)
from app.utils.medical_filter import check_forbidden
from app.workers.tasks import trigger_v0_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Hospitals"])


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
    plan: Plan = Plan.PLAN_12
    onboarding_note: str | None = Field(default=None, max_length=2000)
    sales_owner_id: uuid.UUID | None = None
    ae_owner_id: uuid.UUID | None = None
    onboarding_request_id: uuid.UUID | None = None


def _normalized_hospital_name(value: str) -> str:
    return normalize_hospital_name(value)


async def _exact_name_candidates(db: AsyncSession, name: str) -> list[Hospital]:
    return await find_duplicate_hospitals(db, name=name)


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

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def round_coordinates(cls, value):
        if value is None or value == "":
            return None
        return round(float(value), 6)

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

    # 공개 사이트 identity — 운영자가 근거를 확인하고 승인한 값만 저장한다.
    brand_primary_color: str | None = Field(None, max_length=7)
    brand_accent_color: str | None = Field(None, max_length=7)
    logo_url: str | None = Field(None, max_length=500)
    hero_image_url: str | None = Field(None, max_length=500)
    hero_media_kind: Literal["VERIFIED_FACILITY", "BRAND_GRAPHIC"] | None = None
    hero_headline: str | None = Field(None, max_length=160)
    hero_description: str | None = Field(None, max_length=320)
    image_style_direction: str | None = Field(None, max_length=600)
    site_access_mode: Literal["urgent", "appointment", "specialist"] | None = None

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

    @field_validator("brand_primary_color", "brand_accent_color")
    @classmethod
    def validate_brand_color(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", cleaned):
            raise ValueError("brand colors must use #RRGGBB")
        return cleaned

    # 운영 화면의 '자동 선택' 항목은 빈 문자열을 보낸다. 이를 Literal 위반으로 거부하면
    # 프로파일 전체를 한 번에 PATCH하는 화면에서 요청 전체가 422가 되어, 같이 보낸
    # 대표색·첫 화면 카피·이미지 방향까지 저장되지 않는다.
    @field_validator("hero_media_kind", "site_access_mode", mode="before")
    @classmethod
    def blank_selection_clears_field(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("hero_headline", "hero_description")
    @classmethod
    def validate_public_hero_copy(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        violations = check_forbidden(cleaned)
        if violations:
            raise ValueError(f"의료광고 금지 표현은 사용할 수 없습니다: {', '.join(violations)}")
        return cleaned

    @field_validator(
        "website_url",
        "blog_url",
        "kakao_channel_url",
        "google_business_profile_url",
        "google_maps_url",
        "naver_place_url",
        "logo_url",
        "hero_image_url",
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


def profile_completion_handoff_blocker(
    _hospital: Hospital, handoff: HospitalHandoff | None
) -> dict[str, object] | None:
    if handoff is not None and handoff.state is HandoffState.HANDOFF_ACCEPTED:
        return None
    return {
        "code": "HANDOFF_NOT_ACCEPTED",
        "missing": ["handoff_accepted"],
        "message": "고객 인수 승인이 완료된 뒤 프로파일을 완료할 수 있습니다.",
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
    "brand_primary_color",
    "brand_accent_color",
    "logo_url",
    "hero_image_url",
    "hero_media_kind",
    "hero_headline",
    "hero_description",
    "image_style_direction",
    "site_access_mode",
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


# site/lib/host-routing.ts의 RESERVED_PREFIXES와 동기 유지해야 한다.
# 한쪽만 고치면 "등록은 되는데 페이지가 안 열리는 병원"이 조용히 생긴다.
RESERVED_SITE_SLUGS = frozenset(
    {
        ".well-known",
        "api",
        "ai-diagnosis",
        "landing",
        "privacy",
        "terms",
        "robots",
        "sitemap",
        "favicon",
    }
)


async def _load_replayed_onboarding_request(
    db: AsyncSession,
    body: HospitalCreate,
    *,
    requested_sales_owner_id: uuid.UUID | None,
    requested_ae_owner_id: uuid.UUID | None,
) -> dict[str, object] | None:
    if body.onboarding_request_id is None:
        return None

    prior = await db.get(Hospital, body.onboarding_request_id)
    if prior is None:
        return None

    prior_handoff = (
        await db.execute(select(HospitalHandoff).where(HospitalHandoff.hospital_id == prior.id))
    ).scalar_one_or_none()
    same_request = (
        prior.name == body.name
        and prior.plan == body.plan
        and prior_handoff is not None
        and (
            requested_sales_owner_id is None
            or prior_handoff.sales_owner_id == requested_sales_owner_id
        )
        and (requested_ae_owner_id is None or prior_handoff.ae_owner_id == requested_ae_owner_id)
    )
    if not same_request:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ONBOARDING_REQUEST_CONFLICT",
                "message": "같은 등록 요청 식별자가 다른 병원 정보에 사용되었습니다.",
            },
        )

    return {**_serialize(prior), "handoff": _serialize_handoff_summary(prior_handoff)}


# ── 엔드포인트 ────────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_hospital(
    body: HospitalCreate,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
):
    """신규 병원 등록 (계약 완료 후 AE가 첫 번째로 실행)"""
    actor_id = actor.id if isinstance(actor, AdminUser) else None
    requested_sales_owner_id = body.sales_owner_id or actor_id
    requested_ae_owner_id = body.ae_owner_id or actor_id
    replayed = await _load_replayed_onboarding_request(
        db,
        body,
        requested_sales_owner_id=requested_sales_owner_id,
        requested_ae_owner_id=requested_ae_owner_id,
    )
    if replayed is not None:
        return replayed

    duplicate_candidates = await _exact_name_candidates(db, body.name)
    if duplicate_candidates:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_HOSPITAL_NAME",
                "message": "같은 이름의 병원이 이미 등록되어 있습니다. 기존 병원을 확인해 주세요.",
                "candidates": [
                    {
                        "id": str(candidate.id),
                        "name": candidate.name,
                        "status": candidate.status.value,
                        "onboarding_url": f"/hospitals/{candidate.id}/onboarding",
                    }
                    for candidate in duplicate_candidates
                ],
            },
        )

    slug = slugify(body.name, separator="-")
    # 공개 표면의 예약 경로와 겹치면 그 병원 페이지가 통째로 가려진다.
    # 예: slug가 'ai-diagnosis'인 병원은 무료 진단 퍼널에 먹혀 영원히 열리지 않는다 (PRD F1-3).
    if slug in RESERVED_SITE_SLUGS:
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    # slug 중복 방지
    existing = await db.execute(select(Hospital).where(Hospital.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    hospital = Hospital(
        id=body.onboarding_request_id or uuid.uuid4(),
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
        actor_id = actor.id if isinstance(actor, AdminUser) else uuid.uuid4()
        sales_owner_id = body.sales_owner_id or actor_id
        ae_owner_id = body.ae_owner_id or actor_id
        if isinstance(actor, AdminUser):
            for owner_id in (sales_owner_id, ae_owner_id):
                owner = await db.get(AdminUser, owner_id)
                if owner is None or not owner.is_active:
                    raise HTTPException(
                        status_code=422,
                        detail={"code": "ACTIVE_OWNER_REQUIRED", "owner_id": str(owner_id)},
                    )
        handoff = HospitalHandoff.pending(
            hospital.id,
            sales_owner_id=sales_owner_id,
            ae_owner_id=ae_owner_id,
            source=HandoffSource.DIRECT_CREATE,
        )
        db.add(handoff)
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
                "plan": _enum_value(body.plan, Plan.PLAN_12.value),
            },
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        replayed = await _load_replayed_onboarding_request(
            db,
            body,
            requested_sales_owner_id=requested_sales_owner_id,
            requested_ae_owner_id=requested_ae_owner_id,
        )
        if replayed is not None:
            return replayed
        raise HTTPException(
            status_code=409,
            detail="이미 사용 중인 슬러그 또는 도메인입니다. 병원명을 확인해 주세요.",
        ) from exc
    await db.refresh(hospital)
    return {
        **_serialize(hospital),
        "handoff": _serialize_handoff_summary(handoff),
    }


@router.get("/candidates")
async def list_hospital_name_candidates(
    name: str = Query(min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    candidates = await _exact_name_candidates(db, name)
    return {
        "normalized_name": _normalized_hospital_name(name),
        "candidates": [
            {
                "id": str(candidate.id),
                "name": candidate.name,
                "status": candidate.status.value,
                "onboarding_url": f"/hospitals/{candidate.id}/onboarding",
            }
            for candidate in candidates
        ],
    }


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
    _ = background_tasks
    h = await _get_or_404(db, hospital_id)

    if body.keywords is not None:
        regions = body.region if body.region is not None else (h.region or [])
        invalid_keywords = []
        for keyword in body.keywords:
            analysis = analyze_keyword(keyword, regions)
            normalized = normalize_keyword(keyword)
            if analysis.embedded_region is not None:
                invalid_keywords.append(
                    {
                        "keyword": keyword,
                        "suggested_keyword": (
                            analysis.canonical_term
                            if analysis.canonical_term and analysis.canonical_term != normalized
                            else None
                        ),
                    }
                )
        if invalid_keywords:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REGION_KEYWORD_MIXED",
                    "message": "지역 태그는 핵심 키워드와 분리해 입력해 주세요.",
                    "invalid_keywords": invalid_keywords,
                },
            )

    PROFILE_FIELDS = {
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
        "wikidata_qid",
        "gbp_place_id",
        "naver_place_id",
        "kakao_place_id",
        "hira_org_id",
        "region",
        "specialties",
        "keywords",
        "competitors",
        "director_name",
        "director_career",
        "director_philosophy",
        "director_credentials",
        "brand_primary_color",
        "brand_accent_color",
        "logo_url",
        "hero_image_url",
        "hero_media_kind",
        "hero_headline",
        "hero_description",
        "image_style_direction",
        "site_access_mode",
        "treatments",
        "profile_complete",
    }
    # exclude_unset: 보내지 않은 필드는 유지하되, 명시적 null/빈 문자열은 '비우기'로
    # 처리한다. exclude_none이었을 때는 잘못 입력된 URL/식별자를 지울 API 경로가 없었다.
    # 비우기는 nullable 선택 필드에만 허용 — 필수·NOT NULL 필드의 null은 기존처럼 무시.
    CLEARABLE_FIELDS = {
        "website_url",
        "blog_url",
        "kakao_channel_url",
        "google_business_profile_url",
        "google_maps_url",
        "naver_place_url",
        "latitude",
        "longitude",
        "wikidata_qid",
        "gbp_place_id",
        "naver_place_id",
        "kakao_place_id",
        "hira_org_id",
        "director_career",
        "director_philosophy",
        "director_credentials",
        "brand_primary_color",
        "brand_accent_color",
        "logo_url",
        "hero_image_url",
        "hero_media_kind",
        "hero_headline",
        "hero_description",
        "image_style_direction",
        "site_access_mode",
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

    # Admin UI와 동일한 authoritative checklist를 서버에서 강제한다. 직접 API 호출로
    # 불완전한 병원을 완료 처리해 V0/사이트 파이프라인에 흘려보낼 수 없어야 한다.
    if h.profile_complete:
        required_missing = missing_profile_requirement_keys(h)
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
        if not was_complete:
            handoff_result = await db.execute(
                select(HospitalHandoff).where(HospitalHandoff.hospital_id == hospital_id)
            )
            handoff = handoff_result.scalar_one_or_none()
            blocker = profile_completion_handoff_blocker(h, handoff)
            if blocker is not None:
                raise HTTPException(status_code=409, detail=blocker)

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

    needs_site_revalidate = _has_public_site(h) and any(
        field in PUBLIC_PROFILE_FIELDS for field in changed_fields
    )
    if needs_site_revalidate:
        ensure_site_revalidate_configured()

    await db.commit()
    await db.refresh(h)

    # 프로파일 완료로 변경된 경우 V0 분석 자동 트리거 — OperationRun을 남겨 운영 화면에 붙인다.
    if not was_complete and h.profile_complete:
        try:
            await dispatch_operation(
                db,
                OperationCommand(
                    operation_type="TRIGGER_V0_REPORT",
                    hospital_id=h.id,
                    requested_by_id=None,
                    idempotency_key=None,
                    audit_actor=default_actor(),
                    target_type="hospital",
                    target_id=str(h.id),
                    queue="reports",
                    task_args=(str(h.id),),
                ),
                trigger_v0_report,
            )
        except OperationQueueUnavailable:
            logger.warning("V0 report enqueue failed for hospital %s after profile complete", h.id)
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
            "rejected_fields": [v["field"] for v in result.rejected_fields],
            "sources": [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources],
        },
    )
    await db.commit()

    return {
        "draft": result.draft,
        "field_meta": result.field_meta,
        "violations": result.violations,
        "rejected_fields": result.rejected_fields,
        "naver_place_id": result.naver_place_id,
        "sources": [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources],
    }


@router.patch("/{hospital_id}/activate")
async def activate_hospital(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Start STEP 5 public operation after profile, V0, and site build.

    Content scheduling belongs to STEP 6 and is deliberately not an activation
    prerequisite.
    """
    h = await _get_or_404(db, hospital_id)
    if h.status == HospitalStatus.ACTIVE:
        return {
            "detail": f"{h.name} activated",
            "status": HospitalStatus.ACTIVE.value,
            "site_live": bool(h.site_live),
        }

    gate = await evaluate_activation_gate(db, h)
    if not gate["ready"]:
        raise HTTPException(
            status_code=409,
            detail=activation_gate_error(gate),
        )

    # DM-F3: 기본 플랫폼 주소 활성화는 커스텀 도메인과 독립적으로 가능.
    # 커스텀 도메인이 설정되어 있어도 기본 서브도메인으로 라이브할 수 있다.
    # 커스텀 도메인 검증은 별도의 /domain/verify 엔드포인트에서 처리한다.

    previous_status = h.status.value if hasattr(h.status, "value") else str(h.status)
    ensure_site_revalidate_configured()
    h.status = HospitalStatus.ACTIVE
    h.site_live = True
    await open_service_interval(db, hospital_id, ServiceIntervalProvenance.ACTIVATION)
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
            "activation_method": "platform_subdomain",
            "certificate_ready": True,
            "activation_gate": gate,
        },
    )
    await db.commit()
    # 커밋 이후이므로 실패해도 raise하지 않는다 (R4) — 활성화는 이미 성공했다.
    await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)
    return {
        "detail": f"{h.name} activated",
        "status": HospitalStatus.ACTIVE.value,
        "site_live": True,
    }


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
    await close_service_interval(db, hospital_id)
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

    활성화 게이트와 사용자 도메인의 현재 DNS/인증서를 다시 확인한다.
    """
    h = await _get_or_404(db, hospital_id)

    if h.status != HospitalStatus.PAUSED:
        raise HTTPException(status_code=409, detail="재개는 PAUSED 상태에서만 가능합니다.")

    gate = await evaluate_activation_gate(db, h)
    if not gate["ready"]:
        raise HTTPException(status_code=409, detail=activation_gate_error(gate))

    # DM-F4: DNS 검증만 확인. 인증서는 후속 작업이므로 재개를 블록하지 않음.
    if h.aeo_domain:
        dns_check = await check_domain_dns(h.aeo_domain, domain_dns_strategy_for_hospital(h))
        if not dns_check.verified:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DOMAIN_NOT_READY",
                    "message": "재개 전 사용자 도메인의 DNS 설정을 확인해 주세요.",
                },
            )

    h.status = HospitalStatus.ACTIVE
    h.site_live = True
    await open_service_interval(db, hospital_id, ServiceIntervalProvenance.RESUME)

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
            "activation_gate": gate,
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
    # 온보딩 3단계는 "초기 진단 + PDF"를 요구한다. 두 조건을 모두 봐야 한다.
    #
    #   - PDF: 측정만 끝나고 PDF 생성이 실패하면 리포트 행은 남지만 원장에게 보여줄
    #     파일이 없다. 행 수로 판정하면 온보딩이 사실과 어긋난다.
    #   - V0: 월간 리포트 PDF는 초기 진단이 아니다. 종류를 가리지 않으면 운영 몇 달째
    #     병원이 월간 PDF 덕에 초기 진단을 건너뛴 채로 완료 표시된다.
    v0_report_pdf_count = await _count(
        db,
        select(func.count())
        .select_from(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == h.id,
            MonthlyReport.report_type == V0_REPORT_TYPE,
            MonthlyReport.pdf_path.is_not(None),
            MonthlyReport.pdf_path != "",
        ),
    )
    essence = await get_essence_readiness(db, h.id)
    approved_philosophy = essence.approved
    essence_fresh = essence.is_fresh
    blocked_content_condition = ContentItem.essence_status.in_(
        [
            ESSENCE_STATUS_MISSING_APPROVED,
            ESSENCE_STATUS_NEEDS_REVIEW,
        ]
    )
    if essence.current is not None:
        # 새 Essence를 승인한 뒤에도 이전 버전에 연결된 콘텐츠는 공개 API에서
        # 의도적으로 숨겨진다. 상태 문자열이 ALIGNED였다는 이유로 준비 완료로
        # 오판하지 않도록 실제 현재 버전 연결까지 같은 서버 게이트에서 확인한다.
        blocked_content_condition = or_(
            blocked_content_condition,
            ContentItem.content_philosophy_id.is_distinct_from(essence.current.id),
        )
    essence_blocked_content_count = await _count(
        db,
        select(func.count())
        .select_from(ContentItem)
        .where(
            ContentItem.hospital_id == h.id,
            ContentItem.body.is_not(None),
            blocked_content_condition,
        ),
    )

    has_core_profile = not missing_profile_requirement_keys(h)
    has_local_entity = bool(h.google_business_profile_url or h.google_maps_url)
    has_external_profiles = bool(
        h.website_url or h.blog_url or h.kakao_channel_url or h.naver_place_url
    )
    readiness_actions = readiness_next_actions()

    checks = [
        ReadinessCheck(
            "core_profile",
            "병원 핵심 프로파일",
            bool(has_core_profile),
            18,
            readiness_actions["core_profile"],
        ),
        ReadinessCheck(
            "local_entity",
            "Google 지도·프로필 정보",
            has_local_entity,
            14,
            readiness_actions["local_entity"],
        ),
        ReadinessCheck(
            "external_profiles",
            "외부 공식 채널",
            has_external_profiles,
            8,
            readiness_actions["external_profiles"],
        ),
        ReadinessCheck(
            "essence_sources",
            "콘텐츠 운영 기준 자료",
            essence.processed_source_count > 0 and not essence.has_unprocessed_sources,
            12,
            readiness_actions["essence_sources"],
        ),
        ReadinessCheck(
            "essence_philosophy",
            "승인된 콘텐츠 운영 기준",
            approved_philosophy is not None,
            14,
            readiness_actions["essence_philosophy"],
        ),
        ReadinessCheck(
            "essence_freshness",
            "운영 기준 자료 최신성",
            essence_fresh,
            8,
            readiness_actions["essence_freshness"],
        ),
        ReadinessCheck(
            "content_alignment",
            "콘텐츠 운영 기준 정렬",
            essence_blocked_content_count == 0,
            6,
            readiness_actions["content_alignment"],
        ),
        ReadinessCheck(
            "v0_report",
            "초기 진단 리포트",
            v0_report_pdf_count > 0,
            12,
            readiness_actions["v0_report"],
        ),
        ReadinessCheck(
            "site_built",
            "콘텐츠 허브 노출 준비",
            bool(h.site_built),
            10,
            readiness_actions["site_built"],
        ),
        ReadinessCheck(
            "domain",
            "공개 도메인 상태",
            bool(h.site_live),
            10,
            readiness_actions["domain"],
        ),
        ReadinessCheck(
            "schedule",
            "콘텐츠 스케줄",
            bool(h.schedule_set),
            8,
            readiness_actions["schedule"],
        ),
        ReadinessCheck(
            "published_content",
            "발행 콘텐츠",
            published_count > 0,
            12,
            readiness_actions["published_content"],
        ),
        ReadinessCheck(
            "sov_data",
            "AI 답변 언급률 측정 데이터",
            sov_count > 0,
            8,
            readiness_actions["sov_data"],
        ),
    ]

    total_weight = sum(c.weight for c in checks)
    earned = sum(c.weight for c in checks if c.passed)
    score = round(earned / total_weight * 100)

    # READY is a safety contract, not a weighted marketing score. A high score
    # must never mask one missing mandatory gate (for example no fresh Essence).
    readiness_status = "READY" if all(check.passed for check in checks) else "NEEDS_WORK"

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
        "v0_report_pdf_count": v0_report_pdf_count,
        "essence": {
            "processed_source_count": essence.processed_source_count,
            "required_source_count": essence.required_source_count,
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
        "domain_management_mode": _enum_value(
            getattr(h, "domain_management_mode", None), "HOSPITAL_MANAGED"
        ),
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
        "brand_primary_color": getattr(h, "brand_primary_color", None),
        "brand_accent_color": getattr(h, "brand_accent_color", None),
        "logo_url": getattr(h, "logo_url", None),
        "hero_image_url": getattr(h, "hero_image_url", None),
        "hero_media_kind": getattr(h, "hero_media_kind", None),
        "hero_headline": getattr(h, "hero_headline", None),
        "hero_description": getattr(h, "hero_description", None),
        "image_style_direction": getattr(h, "image_style_direction", None),
        "site_access_mode": getattr(h, "site_access_mode", None),
        "treatments": h.treatments,
        "profile_complete": h.profile_complete,
        "domain_cert_job_state": getattr(h, "domain_cert_job_state", None),
        "domain_cert_job_started_at": (
            getattr(h, "domain_cert_job_started_at", None).isoformat()
            if getattr(h, "domain_cert_job_started_at", None)
            else None
        ),
        "domain_cert_dns_verified_at": (
            getattr(h, "domain_cert_dns_verified_at", None).isoformat()
            if getattr(h, "domain_cert_dns_verified_at", None)
            else None
        ),
        **_serialize_domain_live_check(h),
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
        "domain_cert_dns_verified_at": getattr(h, "domain_cert_dns_verified_at", None).isoformat()
        if getattr(h, "domain_cert_dns_verified_at", None)
        else None,
        "domain_cert_job_state": getattr(h, "domain_cert_job_state", None),
        **_serialize_domain_live_check(h),
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


def _serialize_domain_live_check(h: Hospital) -> dict:
    """마지막 실제 관측 결과 — 인증서 작업 상태와 달리 도메인 재저장에 지워지지 않는다."""
    checked_at = getattr(h, "domain_last_checked_at", None)
    return {
        "domain_last_checked_at": checked_at.isoformat() if checked_at else None,
        "domain_last_check_ok": getattr(h, "domain_last_check_ok", None),
        "domain_last_check_reason": getattr(h, "domain_last_check_reason", None),
    }


def _serialize_handoff_summary(handoff: HospitalHandoff) -> dict[str, object]:
    return {
        "id": handoff.id,
        "hospital_id": handoff.hospital_id,
        "state": handoff.state,
        "sales_owner_id": handoff.sales_owner_id,
        "ae_owner_id": handoff.ae_owner_id,
        "contract_reference": handoff.contract_reference,
        "contract_effective_at": handoff.contract_effective_at,
        "plan": handoff.plan,
        "sla_due_at": handoff.sla_due_at,
        "accepted_by_id": handoff.accepted_by_id,
        "accepted_at": handoff.accepted_at,
        "acceptance_source": handoff.acceptance_source,
        "version": handoff.version,
    }
