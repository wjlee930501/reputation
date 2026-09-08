from typing import Any, Optional

from pydantic import BaseModel


class HospitalStateItem(BaseModel):
    """공개 서비스·콘텐츠 준비 상태. `remaining`은 아직 채워지지 않은 조건 키다."""

    kind: str
    remaining: list[str]


class HospitalDomainStateItem(BaseModel):
    kind: str
    reason: Optional[str] = None
    last_checked_at: Optional[str] = None
    last_check_ok: Optional[bool] = None


class HospitalAeOwner(BaseModel):
    id: str
    name: str


class ProfileRequirementItem(BaseModel):
    """아직 채워지지 않은 필수 항목. 화면은 라벨만 그대로 보여 준다."""

    key: str
    label: str


class HospitalItemBase(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    plan: Optional[str]
    source_lead_id: Optional[str] = None
    profile_complete: bool
    v0_report_done: bool
    site_built: bool
    site_live: bool
    schedule_set: bool
    # 승인이 남은 공개 표면 시각 항목의 사람이 읽는 라벨. 비어 있으면 승인 완료다.
    # 목록이 이 값을 보지 않으면 상세와 다른 결론을 내놓는다(O-2).
    visual_approval_missing: list[str] = []
    aeo_domain: Optional[str] = None
    domain_cert_dns_verified_at: Optional[str] = None
    domain_cert_job_state: Optional[str] = None
    # 커스텀 도메인의 마지막 실관측 결과. 활성화 게이트가 아니라 Admin 소프트 상태다.
    domain_last_checked_at: Optional[str] = None
    domain_last_check_ok: Optional[bool] = None
    domain_last_check_reason: Optional[str] = None
    created_at: Optional[str]


class HospitalListItem(HospitalItemBase):
    # 3상태는 백엔드가 판정해 내려주고 admin은 라벨만 붙인다(설계 §4.2). 화면마다 판정하면
    # 목록·헤더·현황이 다른 답을 한다(PR-0A H-06). 상세 화면은 `overview`가 따로 답한다.
    public_service_state: HospitalStateItem
    content_state: HospitalStateItem
    domain_state: HospitalDomainStateItem
    # 인시던트(OPEN·RETRYING·ACKNOWLEDGED) + 사람이 풀어야 하는 예외 초안.
    open_exception_count: int
    # 계약이 없거나 담당 AE가 비어 있으면 None이다.
    ae_owner: Optional[HospitalAeOwner] = None


class HospitalDetail(HospitalItemBase):
    # `profile_complete`는 서버가 이 목록에서 파생한다 — 화면은 "남은 필수 항목 N개"만
    # 보여 주고 완료를 직접 표시하지 않는다(설계 §4.5).
    missing_profile_requirements: list[ProfileRequirementItem]
    onboarding_note: Optional[str] = None
    address: Optional[str]
    phone: Optional[str]
    business_hours: Optional[Any]
    website_url: Optional[str]
    blog_url: Optional[str]
    kakao_channel_url: Optional[str]
    google_business_profile_url: Optional[str]
    google_maps_url: Optional[str]
    naver_place_url: Optional[str]
    domain_management_mode: str = "HOSPITAL_MANAGED"
    domain_dns_strategy: str = "CNAME"
    domain_registrar: Optional[str] = None
    domain_dns_provider: Optional[str] = None
    domain_purchase_note: Optional[str] = None
    domain_cert_job_started_at: Optional[str] = None
    latitude: Optional[float]
    longitude: Optional[float]
    wikidata_qid: Optional[str] = None
    gbp_place_id: Optional[str] = None
    naver_place_id: Optional[str] = None
    kakao_place_id: Optional[str] = None
    hira_org_id: Optional[str] = None
    region: list
    specialties: list
    keywords: list
    competitors: list
    director_name: Optional[str]
    director_career: Optional[str]
    director_philosophy: Optional[str]
    brand_primary_color: Optional[str] = None
    brand_accent_color: Optional[str] = None
    logo_url: Optional[str] = None
    hero_image_url: Optional[str] = None
    hero_media_kind: Optional[str] = None
    hero_headline: Optional[str] = None
    hero_description: Optional[str] = None
    image_style_direction: Optional[str] = None
    site_access_mode: Optional[str] = None
    director_credentials: Optional[Any] = None
    treatments: list
