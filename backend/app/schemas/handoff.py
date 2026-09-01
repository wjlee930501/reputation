from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models.handoff import HandoffSource, HandoffState
from app.models.hospital import Plan


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
