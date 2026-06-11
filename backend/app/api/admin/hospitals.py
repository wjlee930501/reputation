"""
Admin API — 병원 프로파일 관리
POST   /admin/hospitals                 — 신규 등록
GET    /admin/hospitals                 — 전체 목록
GET    /admin/hospitals/{id}            — 상세 조회
PATCH  /admin/hospitals/{id}/profile    — 프로파일 수정 + 완료 시 V0 트리거
PATCH  /admin/hospitals/{id}/domain     — 공개 도메인 상태 확인
PATCH  /admin/hospitals/{id}/activate   — ACTIVE 전환
"""
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

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
from app.services.audit_log import default_actor, write_audit_log
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    compute_sources_snapshot_hash,
)
from app.workers.tasks import trigger_v0_report, build_aeo_site
from app.api.admin.domain import _normalize_dns_name, _resolve_cname
from app.core.config import settings
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)
from app.utils.domain import is_valid_hostname, normalize_domain

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


class DomainConnect(BaseModel):
    # 원본 입력은 스킴/경로가 붙어 올 수 있어 길이만 느슨하게 받고,
    # 정규화·형식 검증은 엔드포인트에서 한국어 422로 처리한다.
    domain: str = Field(min_length=1, max_length=500)  # 예: "info.jangpyeon.com"


def _normalize_and_validate_domain(raw: str) -> str:
    """저장용 도메인 정규화 + 검증. 실패 시 한국어 422.

    - 소문자/공백·스킴·경로·포트·끝 점 제거 (조회 측 by-domain lookup과 동일 규칙)
    - 호스트명 형식 검증
    - 플랫폼 자체 도메인(CNAME_TARGET 및 그 하위 호스트) 거부
    """
    normalized = normalize_domain(raw)
    if not normalized or not is_valid_hostname(normalized):
        raise HTTPException(
            status_code=422,
            detail=(
                "유효한 도메인 형식이 아닙니다. "
                "스킴(https://)이나 경로 없이 호스트명만 입력해 주세요. 예: info.jangpyeon.com"
            ),
        )
    platform_domain = normalize_domain(settings.CNAME_TARGET)
    if platform_domain and (
        normalized == platform_domain or normalized.endswith(f".{platform_domain}")
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"플랫폼 기본 도메인({settings.CNAME_TARGET})은 병원 도메인으로 사용할 수 없습니다. "
                "병원이 보유한 별도 도메인을 입력해 주세요."
            ),
        )
    return normalized


async def _ensure_domain_not_taken(
    db: AsyncSession, hospital_id: uuid.UUID, normalized_domain: str
) -> None:
    """다른 병원이 이미 사용 중인 도메인이면 409. 같은 병원의 재저장은 허용."""
    result = await db.execute(
        select(Hospital)
        .where(
            func.lower(Hospital.aeo_domain) == normalized_domain,
            Hospital.id != hospital_id,
        )
        .limit(1)
    )
    other = result.scalars().first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"이미 다른 병원({other.name})에 연결된 도메인입니다. 도메인을 확인해 주세요.",
        )


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

    # 프로파일 완료로 변경된 경우 V0 분석 자동 트리거
    if not was_complete and h.profile_complete:
        background_tasks.add_task(
            trigger_v0_report.apply_async,
            args=[str(hospital_id)],
            queue="reports",
        )
    if needs_site_revalidate:
        # 커밋 이후이므로 실패해도 raise하지 않는다 (R4) — 저장은 이미 성공했다.
        await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)

    return _serialize(h)


@router.patch("/{hospital_id}/domain")
async def connect_domain(
    hospital_id: uuid.UUID,
    body: DomainConnect,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """공개 도메인 정보 저장 + 콘텐츠 허브 노출 상태 갱신"""
    h = await _get_or_404(db, hospital_id)
    # 정규화 + 형식/플랫폼 도메인 검증 (422), 타 병원 중복 검사 (409).
    domain = _normalize_and_validate_domain(body.domain)
    await _ensure_domain_not_taken(db, hospital_id, domain)
    previous_domain = h.aeo_domain
    previous_status = h.status.value if hasattr(h.status, "value") else str(h.status)
    previous_site_live = bool(h.site_live)
    domain_changed = _normalize_dns_name(previous_domain) != _normalize_dns_name(domain)
    h.aeo_domain = domain

    # 도메인이 바뀌면 기존 DNS 검증은 더 이상 유효하지 않다.
    # 이미 라이브였던 병원이 새 미검증 도메인으로도 라이브처럼 보이는 것을 막는다.
    if domain_changed:
        h.site_live = False
        if h.status == HospitalStatus.ACTIVE:
            h.status = HospitalStatus.PENDING_DOMAIN
    if previous_site_live:
        ensure_site_revalidate_configured()

    await write_audit_log(
        db,
        action="connect_domain",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="domain",
        target_id=domain,
        detail={
            "previous_domain": previous_domain,
            "new_domain": domain,
            "domain_changed": domain_changed,
            "previous_status": previous_status,
            "previous_site_live": previous_site_live,
            "new_status": h.status.value if hasattr(h.status, "value") else str(h.status),
        },
    )
    await db.commit()
    if previous_site_live:
        # 커밋 이후이므로 실패해도 raise하지 않는다 (R4).
        await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)

    # 콘텐츠 허브 노출 상태 갱신 (legacy task name) — 도메인이 실제로 바뀌었거나 최초 준비가
    # 안 된 경우에만. 동일 도메인 재저장 시 불필요한 상태 전환·Slack 알림을 만들지 않는다.
    if domain_changed or not h.site_built:
        background_tasks.add_task(
            build_aeo_site.apply_async,
            args=[str(hospital_id)],
            queue="default",
        )
        return {"detail": f"Domain {domain} set. Content hub exposure refresh triggered."}
    return {"detail": f"Domain {domain} unchanged. No exposure refresh needed."}


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

    if not h.aeo_domain:
        raise HTTPException(status_code=400, detail="도메인이 설정되지 않았습니다. 먼저 도메인을 입력해 주세요.")

    cname_value = await _resolve_cname(h.aeo_domain)
    if _normalize_dns_name(cname_value) != _normalize_dns_name(settings.CNAME_TARGET):
        raise HTTPException(
            status_code=400,
            detail=(
                f"CNAME 설정이 확인되지 않았습니다. "
                f"{h.aeo_domain} → {settings.CNAME_TARGET} 로 설정해 주세요."
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
            "cname_value": cname_value,
        },
    )
    await db.commit()
    # 커밋 이후이므로 실패해도 raise하지 않는다 (R4) — 활성화는 이미 성공했다.
    await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)
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
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
