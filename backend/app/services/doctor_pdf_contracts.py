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
    # 헤드라인이 선 표본과 그 불확실성. 델타 문장이 "의미 있는/정상 변동 범위"를
    # 고르는 근거가 여기에 그대로 남는다 — 문구만 있고 근거가 없으면 방어 못 한다.
    attempts_used: int | None
    mention_frequency: float | None
    ci95_low_of_hundred: int | None
    ci95_high_of_hundred: int | None
    margin_of_hundred: int | None
    significance: str | None


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


class DoctorCitedItem(TypedDict):
    """AI 답변이 인용한 우리 글 1건."""

    title: str | None
    cited_cell_count: int


class DoctorPublishedItem(TypedDict):
    """막 1 "이번 달 저희가 한 일"에 이름을 올리는 글 1편."""

    title: str
    cited: bool


class DoctorAppendixRow(TypedDict):
    """2쪽 부록의 질문 1줄. 숫자는 전부 뷰가 만든 문자열이다."""

    query_text: str
    prev_label: str
    current_label: str
    competitor: str
    cited_title: str


class DoctorV0Baseline(TypedDict):
    """서비스 시작 시점(V0) 대비 — 질문 세트가 충분히 겹칠 때만 채운다."""

    of_hundred: int
    current_of_hundred: int
    sentence: str


class DoctorReportView(TypedDict):
    """원장 1페이지의 3막 + 선택적 2쪽 부록.

    막 1 "이번 달 저희가 한 일" → 막 2 "무엇이 달라졌나" → 막 3 "다음 달 계획".
    """

    measured: bool
    hospital_name: str
    headline: DoctorHeadline
    summary: str
    coverage_text: str
    tiles: list[DoctorTile]
    # 막 1
    published_items: list[DoctorPublishedItem]
    citation_line: str | None
    # 막 2
    new_mention_sentences: list[DoctorMentionSentence]
    new_mention_empty_text: str
    lost_mention_sentences: list[DoctorMentionSentence]
    v0_baseline: DoctorV0Baseline | None
    evidence: DoctorEvidence
    # 막 3
    next_actions: DoctorNextActions
    footnotes: list[str]
    # 2쪽 부록 — 비어 있으면 렌더하지 않고 PDF도 1쪽으로 검증된다.
    appendix_rows: list[DoctorAppendixRow]
    # 페이지 1이 넘칠 때 실제로 무엇을 뺐는지. 트리밍 순서를 테스트로 고정한다.
    trimmed: list[str]
    # 인용 귀속
    cited_content_count: int
    cited_cells: int
    top_cited_items: list[DoctorCitedItem]
    # AE 전용 — 원장 PDF에는 렌더하지 않고 내부 리포트·Admin이 읽는다.
    talking_points: list[str]


@dataclass(frozen=True, slots=True)
class DoctorPdfExpectation:
    hospital_name: str
    coverage_text: str
    caveat_text: str
    public_url: str
    # 부록이 렌더되는 리포트만 2쪽이다. 렌더 여부를 기대값으로 못 박아야
    # "왜인지 모르게 2쪽"인 PDF가 원장에게 나가지 않는다.
    appendix_expected: bool = False


class DoctorArtifactMetadata(BaseModel):
    """Closed JSON shape stored with a validated doctor artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_version: Literal["doctor-pdf-v1"]
    validation_source: Literal["SYSTEM"]
    page_count: Literal[1, 2]
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
