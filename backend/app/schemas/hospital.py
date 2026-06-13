from typing import Any, Optional

from pydantic import BaseModel


class HospitalListItem(BaseModel):
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
    created_at: Optional[str]


class HospitalDetail(HospitalListItem):
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
    aeo_domain: Optional[str]
    domain_management_mode: str = "HOSPITAL_MANAGED"
    domain_dns_strategy: str = "CNAME"
    domain_registrar: Optional[str] = None
    domain_dns_provider: Optional[str] = None
    domain_purchase_note: Optional[str] = None
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
    director_credentials: Optional[Any] = None
    treatments: list
