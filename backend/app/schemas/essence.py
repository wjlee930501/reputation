from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.essence import EvidenceNoteType, PhilosophyStatus, SourceStatus, SourceType


class SourceAssetCreate(BaseModel):
    source_type: SourceType
    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    raw_text: str | None = None
    operator_note: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_url_or_text(self):
        if not (self.url and self.url.strip()) and not (self.raw_text and self.raw_text.strip()):
            raise ValueError("url 또는 raw_text 중 하나는 필수입니다.")
        return self


class SourceAssetPatch(BaseModel):
    source_type: SourceType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    raw_text: str | None = None
    operator_note: str | None = None
    source_metadata: dict[str, Any] | None = None
    updated_by: str | None = Field(default=None, max_length=100)


class EvidenceNoteResponse(BaseModel):
    id: str
    hospital_id: str
    source_asset_id: str
    note_type: EvidenceNoteType
    claim: str
    source_excerpt: str
    excerpt_start: int | None
    excerpt_end: int | None
    confidence: float | None
    note_metadata: dict[str, Any]
    created_at: str | None


class SourceAssetResponse(BaseModel):
    id: str
    hospital_id: str
    source_type: SourceType
    title: str
    url: str | None
    raw_text: str | None = None
    operator_note: str | None = None
    source_metadata: dict[str, Any]
    content_hash: str | None
    status: SourceStatus
    process_error: str | None
    processed_at: str | None
    created_by: str | None
    updated_by: str | None
    created_at: str | None
    updated_at: str | None
    evidence_note_count: int = 0
    evidence_notes: list[EvidenceNoteResponse] | None = None


class PhilosophyDraftCreate(BaseModel):
    source_asset_ids: list[str] | None = None
    operator_note: str | None = None
    created_by: str | None = Field(default=None, max_length=100)


class PhilosophyPatch(BaseModel):
    positioning_statement: str | None = None
    doctor_voice: str | None = None
    patient_promise: str | None = None
    content_principles: list[str] | None = None
    tone_guidelines: list[str] | None = None
    must_use_messages: list[str] | None = None
    avoid_messages: list[str] | None = None
    treatment_narratives: list[dict[str, Any]] | None = None
    local_context: dict[str, Any] | None = None
    medical_ad_risk_rules: list[str] | None = None
    evidence_map: dict[str, list[str]] | None = None
    unsupported_gaps: list[Any] | None = None
    conflict_notes: list[Any] | None = None
    synthesis_notes: str | None = None


class PhilosophyApprove(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=100)
    approval_note: str | None = None
    confirm_evidence_reviewed: bool

    @field_validator("confirm_evidence_reviewed")
    @classmethod
    def evidence_review_must_be_confirmed(cls, value: bool) -> bool:
        if not value:
            raise ValueError("근거 검토 확인이 필요합니다.")
        return value


class PhilosophyResponse(BaseModel):
    id: str
    hospital_id: str
    version: int
    status: PhilosophyStatus
    positioning_statement: str | None
    doctor_voice: str | None
    patient_promise: str | None
    content_principles: list[Any]
    tone_guidelines: list[Any]
    must_use_messages: list[Any]
    avoid_messages: list[Any]
    treatment_narratives: list[Any]
    local_context: dict[str, Any]
    medical_ad_risk_rules: list[Any]
    evidence_map: dict[str, Any]
    source_asset_ids: list[Any]
    unsupported_gaps: list[Any]
    conflict_notes: list[Any]
    synthesis_notes: str | None
    source_snapshot_hash: str | None
    created_by: str | None
    reviewed_by: str | None
    approved_at: str | None
    approval_note: str | None
    created_at: str | None
    updated_at: str | None


class ApprovedPhilosophyResponse(BaseModel):
    approved: PhilosophyResponse | None
