from datetime import date, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models.handoff import HandoffSource, HandoffState
from app.models.hospital import Plan


class ContractRegistration(BaseModel):
    """한 화면 계약 등록 — 병원 생성·계약 기록·인수 수락을 한 요청으로 받는다.

    인수 처리 기한(`sla_due_at`)은 받지 않는다. 같은 요청에서 담당 AE가 인수까지
    끝내므로 기한은 승인 시각 자체이고, 화면에 기한 칸을 두면 이미 지난 약속을
    운영자가 지어내게 된다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    lead_id: UUID | None = None
    contract_reference: str = Field(min_length=1, max_length=200)
    contract_effective_at: date
    plan: Plan
    ae_owner_id: UUID
    sales_owner_id: UUID | None = None


class HandoffContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    contract_reference: str = Field(min_length=1, max_length=200)
    contract_effective_at: AwareDatetime
    plan: Plan
    sla_due_at: AwareDatetime


class HandoffAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class HandoffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    hospital_id: UUID
    state: HandoffState
    sales_owner_id: UUID | None
    ae_owner_id: UUID | None
    contract_reference: str | None
    contract_effective_at: datetime | None
    plan: Plan | None
    sla_due_at: datetime | None
    accepted_by_id: UUID | None
    accepted_at: datetime | None
    acceptance_source: HandoffSource
    version: int
    created_at: datetime
    updated_at: datetime
