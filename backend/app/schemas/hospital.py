from typing import Any, Optional

from pydantic import BaseModel


class HospitalListItem(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    plan: Optional[str]
    profile_complete: bool
    v0_report_done: bool
    site_live: bool
    schedule_set: bool
    created_at: Optional[str]


class HospitalDetail(HospitalListItem):
    address: Optional[str]
    phone: Optional[str]
    business_hours: Optional[Any]
    website_url: Optional[str]
    blog_url: Optional[str]
    aeo_domain: Optional[str]
    region: list
    specialties: list
    keywords: list
    competitors: list
    director_name: Optional[str]
    director_career: Optional[str]
    director_philosophy: Optional[str]
    treatments: list
