"""Typed contracts shared by doctor-PDF rendering and binary validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class DoctorHeadline(TypedDict):
    of_hundred: int | None
    prev_of_hundred: int | None
    delta: int | None
    delta_sentence: str | None


class DoctorTile(TypedDict):
    label: str
    value: str
    hint: str


class DoctorMentionSentence(TypedDict):
    query_text: str
    platform_label: str


class DoctorEvidenceCase(TypedDict):
    question: str
    excerpt: str
    platform: str
    measured_at: datetime | None
    competitors: list[str]


class DoctorEvidence(TypedDict):
    found: DoctorEvidenceCase | None
    missing: DoctorEvidenceCase | None


class DoctorNextActions(TypedDict):
    ours: list[str]
    yours: list[str]


class DoctorReportView(TypedDict):
    measured: bool
    hospital_name: str
    headline: DoctorHeadline
    summary: str
    coverage_text: str
    tiles: list[DoctorTile]
    new_mention_sentences: list[DoctorMentionSentence]
    new_mention_empty_text: str
    evidence: DoctorEvidence
    next_actions: DoctorNextActions
    footnotes: list[str]


@dataclass(frozen=True, slots=True)
class DoctorPdfExpectation:
    hospital_name: str
    coverage_text: str
    caveat_text: str
    public_url: str


class DoctorArtifactMetadata(BaseModel):
    """Closed JSON shape stored with a validated doctor artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_version: Literal["doctor-pdf-v1"]
    validation_source: Literal["SYSTEM"]
    page_count: Literal[1]
    page_size: Literal["A4"]
    glyph_count: int = Field(gt=0)
    font_family: Literal["Pretendard"]
    font_embedded: Literal[True]
    korean_to_unicode: Literal[True]
    link_count: int = Field(gt=0)
    expected_link_present: Literal[True]
    required_text_present: Literal[True]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class ValidatedDoctorPdf:
    pdf_bytes: bytes
    sha256: str
    byte_size: int
    metadata: DoctorArtifactMetadata


@dataclass(frozen=True, slots=True)
class PublishedDoctorPdf:
    path: str
    sha256: str
    byte_size: int
    metadata: DoctorArtifactMetadata


class DoctorPdfValidationError(RuntimeError):
    def __init__(
        self,
        code: str,
        problem: str,
        customer_impact: str = "현재 파일은 원장님께 전달할 수 없습니다.",
        next_action: str = (
            "리포트 화면에서 ‘리포트 다시 만들기’를 눌러 주세요. 다시 실패하면 "
            "‘개발팀 문의용 정보 복사’를 개발팀에 전달해 주세요."
        ),
    ) -> None:
        super().__init__(problem)
        self.code = code
        self.problem = problem
        self.customer_impact = customer_impact
        self.next_action = next_action
