from datetime import date, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.models.handoff import HandoffSource, HandoffState
from app.models.hospital import Plan


class ContractRegistration(BaseModel):
    """한 화면 계약 등록 — 병원 생성·계약 기록·인수 수락을 한 요청으로 받는다.

    인수 처리 기한(`sla_due_at`)은 받지 않는다. 같은 요청에서 담당 AE가 인수까지
    끝내므로 기한은 승인 시각 자체이고, 화면에 기한 칸을 두면 이미 지난 약속을
    운영자가 지어내게 된다.

    계약 번호·효력일·영업 담당도 화면에서 받지 않는다. 셋 다 저장만 되고 어떤 동작도
    바꾸지 않아 운영자에게 의미 없는 기입이었다. 비어 오면 서버가 채운다(번호는 자동 생성,
    효력일은 등록일, 영업 담당은 등록한 운영자). 값이 오면 그대로 쓴다 — 배포 순서가
    api → admin이라 이전 화면은 계속 세 값을 보낸다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    lead_id: UUID | None = None
    contract_reference: str | None = Field(default=None, min_length=1, max_length=200)
    contract_effective_at: date | None = None
    plan: Plan
    ae_owner_id: UUID
    sales_owner_id: UUID | None = None

    @field_validator("name", "contract_reference")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # 공백만 넣은 값은 min_length를 통과한다 — 이름 없는 병원과 빈 계약 번호가
        # 그대로 저장되면 화면에서 고칠 수 없으므로 여기서 막는다.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("값을 입력해 주세요.")
        return cleaned


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
