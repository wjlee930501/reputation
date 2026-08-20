from datetime import datetime

from pydantic import BaseModel, Field


class DomainVerifyResponse(BaseModel):
    domain: str
    verified: bool
    dns_verified: bool
    certificate_ready: bool
    certificate_phase: str | None = None
    cert_job_state: str | None = None
    cert_job_started_at: datetime | None = None
    cert_job_elapsed_minutes: int | None = None
    cname_value: str | None
    expected_cname: str
    address_values: list[str] = Field(default_factory=list)
    expected_addresses: list[str] = Field(default_factory=list)
    verification_method: str | None = None
    message: str
