import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator


QueryTargetPriority = Literal["HIGH", "NORMAL", "LOW"]
QueryTargetStatus = Literal["ACTIVE", "PAUSED", "ARCHIVED"]

_TARGET_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _clean_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _clean_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class AIQueryVariantBase(BaseModel):
    query_text: str = Field(min_length=1, max_length=500)
    platform: str = Field(default="CHATGPT", min_length=1, max_length=50)
    language: str = Field(default="ko", min_length=1, max_length=20)
    is_active: bool = True
    query_matrix_id: uuid.UUID | None = None

    @field_validator("query_text", "platform", "language")
    @classmethod
    def clean_required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value


class AIQueryVariantCreate(AIQueryVariantBase):
    pass


class AIQueryVariantUpdate(BaseModel):
    query_text: str | None = Field(default=None, min_length=1, max_length=500)
    platform: str | None = Field(default=None, min_length=1, max_length=50)
    language: str | None = Field(default=None, min_length=1, max_length=20)
    is_active: bool | None = None
    query_matrix_id: uuid.UUID | None = None

    @field_validator("query_text", "platform", "language")
    @classmethod
    def clean_optional_required_string(cls, value: str | None) -> str | None:
        return _clean_optional_string(value)


class AIQueryVariantResponse(BaseModel):
    id: str
    query_target_id: str
    query_text: str
    platform: str
    language: str
    is_active: bool
    query_matrix_id: str | None
    created_at: str | None
    updated_at: str | None


class AIQueryTargetBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_intent: str = Field(min_length=1, max_length=100)
    region_terms: list[str] = Field(default_factory=list)
    specialty: str | None = Field(default=None, max_length=200)
    condition_or_symptom: str | None = Field(default=None, max_length=200)
    treatment: str | None = Field(default=None, max_length=200)
    decision_criteria: list[str] = Field(default_factory=list)
    patient_language: str = Field(default="ko", min_length=1, max_length=20)
    platforms: list[str] = Field(default_factory=lambda: ["CHATGPT", "GEMINI"])
    competitor_names: list[str] = Field(default_factory=list)
    priority: QueryTargetPriority = "NORMAL"
    status: QueryTargetStatus = "ACTIVE"
    target_month: str | None = Field(default=None, max_length=7)
    created_by: str | None = Field(default=None, max_length=100)
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("name", "target_intent", "patient_language")
    @classmethod
    def clean_required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value

    @field_validator("specialty", "condition_or_symptom", "treatment", "target_month", "created_by", "updated_by")
    @classmethod
    def clean_optional_string(cls, value: str | None) -> str | None:
        return _clean_optional_string(value)

    @field_validator("region_terms", "decision_criteria", "platforms", "competitor_names", mode="before")
    @classmethod
    def clean_list_fields(cls, value: object) -> list[str]:
        return _clean_string_list(value)

    @field_validator("platforms")
    @classmethod
    def require_platforms(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("At least one platform is required")
        return value

    @field_validator("target_month")
    @classmethod
    def validate_target_month(cls, value: str | None) -> str | None:
        if value and not _TARGET_MONTH_RE.match(value):
            raise ValueError("target_month must be YYYY-MM")
        return value


class AIQueryTargetCreate(AIQueryTargetBase):
    variants: list[AIQueryVariantCreate] = Field(default_factory=list)


class AIQueryTargetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_intent: str | None = Field(default=None, min_length=1, max_length=100)
    region_terms: list[str] | None = None
    specialty: str | None = Field(default=None, max_length=200)
    condition_or_symptom: str | None = Field(default=None, max_length=200)
    treatment: str | None = Field(default=None, max_length=200)
    decision_criteria: list[str] | None = None
    patient_language: str | None = Field(default=None, min_length=1, max_length=20)
    platforms: list[str] | None = None
    competitor_names: list[str] | None = None
    priority: QueryTargetPriority | None = None
    status: QueryTargetStatus | None = None
    target_month: str | None = Field(default=None, max_length=7)
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("name", "target_intent", "patient_language")
    @classmethod
    def clean_optional_required_string(cls, value: str | None) -> str | None:
        return _clean_optional_string(value)

    @field_validator("specialty", "condition_or_symptom", "treatment", "target_month", "updated_by")
    @classmethod
    def clean_optional_string(cls, value: str | None) -> str | None:
        return _clean_optional_string(value)

    @field_validator("region_terms", "decision_criteria", "platforms", "competitor_names", mode="before")
    @classmethod
    def clean_optional_list_fields(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        return _clean_string_list(value)

    @field_validator("platforms")
    @classmethod
    def require_platforms(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not value:
            raise ValueError("At least one platform is required")
        return value

    @field_validator("target_month")
    @classmethod
    def validate_target_month(cls, value: str | None) -> str | None:
        if value and not _TARGET_MONTH_RE.match(value):
            raise ValueError("target_month must be YYYY-MM")
        return value


class AIQueryTargetSummary(BaseModel):
    variant_count: int
    active_variant_count: int
    linked_query_matrix_count: int
    latest_sov_pct: float | None = None
    last_measured_at: str | None = None
    gap_status: str | None = None
    next_action: str | None = None


class AIQueryTargetListItem(BaseModel):
    id: str
    hospital_id: str
    name: str
    target_intent: str
    region_terms: list[str]
    specialty: str | None
    condition_or_symptom: str | None
    treatment: str | None
    decision_criteria: list[str]
    patient_language: str
    platforms: list[str]
    competitor_names: list[str]
    priority: str
    status: str
    target_month: str | None
    created_by: str | None
    updated_by: str | None
    created_at: str | None
    updated_at: str | None
    variants: list[AIQueryVariantResponse]
    summary: AIQueryTargetSummary


class AIQueryTargetDetail(AIQueryTargetListItem):
    pass
