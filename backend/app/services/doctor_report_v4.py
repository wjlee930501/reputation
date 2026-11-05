"""원장용 월간 보고서 v4 — 뷰 모델 하나, 규칙 문장, 검증·대조 계약.

2026-11-04 대표 지시문(「보고서 개편」)과 결정 R1~R8을 구현한다. 설계는
`docs/plans/2026-11-04-doctor-report-v4-design.md`.

원칙
- 숫자는 전부 코드 바인딩이다. LLM을 쓰지 않는다(R8). 모르는 값은 None이고 0과 섞지 않는다.
- 문장은 이 파일에만 있다. 템플릿(`doctor_report_v4.html`)은 이 모델의 필드만 읽고 분기를 두지 않는다.
- 렌더 검증(`doctor_pdf_v4`)과 템플릿 갱신의 숫자 대조(`monthly_template_refresh`)는 같은 함수
  (`required_texts`·`facts`)를 쓴다 — 검증 파일에 문장을 손으로 복사하지 않는다.
- 용어는 R3: AI 노출률·노출 키워드·측정 키워드·콘텐츠·AI 인용·최적화·키워드 확장·콘텐츠 고도화·재측정.
- 비율은 정수 %로 찍되 100%는 `100.0%`로 찍는다 — 의료광고 필터가 `100%`를 어디서나 막는다(필터에 보고서 예외를 두지 않는다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from math import isfinite
from typing import Any, Literal

import arrow

from app.services.competitor_scope import registered_competitor_names, scope_competitor_records
from app.services.content_citations import normalize_cited_url
from app.services.query_target_structure import (
    INTENT_AVAILABILITY,
    INTENT_COMPARISON,
    INTENT_COST,
    INTENT_INFO,
    INTENT_RECOMMENDATION,
    INTENT_SYMPTOM,
    clinical_keyword_from_query,
)
from app.services.report_engine import _excerpt_around, _query_text_of
from app.services.report_work_evidence import published_work_evidence

TEMPLATE_VERSION = "v4"
ReportKindV4 = Literal["MONTHLY", "SETUP"]
PLATFORM_NAMES = {"chatgpt": "ChatGPT", "gemini": "Gemini"}
NOT_MEASURED_YET = "다음 달부터 측정"
MEASUREMENT_INCOMPLETE = "측정 미완료"
UNKNOWN_TREATMENT = "확인 필요"
GENERAL_TREATMENT = "일반"
# 본문 P6 표에 두는 행 수. 넘치는 행은 부록에 '이어짐'으로 간다(R12-2).
PLAN_ROWS_IN_BODY = 8
TREATMENT_NAMES_ON_CARD = 4
EXCERPT_CHARS = 260
TOP_COMPETITORS = 5
PLATFORM_ORDER = ("chatgpt", "gemini")

# 부록 D — 보고서 전체에서 면책은 여기 세 줄뿐이다(R5).
CAVEAT_LINES = (
    "AI 답변에 노출된 횟수는 환자 수가 아닙니다.",
    "순위나 환자 수 증가를 약속하지 않습니다.",
    "다음 달 계획은 예정 사항입니다.",
)
# 부록 C — 용어 정의. R6의 안내 두 문장 뜻은 첫 줄과 넷째 줄이 받는다. 한 문장 40자 이내.
GLOSSARY = (
    ("AI 노출률", "측정 키워드로 AI에 물었을 때 병원명이 나온 답변의 비율입니다."),
    ("노출 키워드", "측정 키워드 가운데 병원명이 한 번 이상 나온 키워드입니다."),
    ("AI 인용", "AI가 답을 만들 때 병원 콘텐츠를 출처로 쓴 경우입니다."),
    ("측정 키워드", "환자가 AI에 물어볼 만한 질문을 매달 같은 방식으로 묻습니다."),
)
METHOD_VARIABILITY_LINE = "AI 답변은 질문할 때마다 달라질 수 있습니다."
METHOD_COMPARISON_LINE = "월별 비교는 고정 키워드로만 계산합니다."
# 비교하지 않은 달의 P1 하단 한 줄. 이유가 '방식이 바뀜'인지 '확인할 수 없음'인지 가른다(검토 14).
METHOD_CHANGED_LINE = "측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다."
NOT_VERIFIED_LINE = "지난달과 같은 조건으로 물었는지 확인되지 않아 직접 비교하지 않았습니다."
REFERENCE_IN_APPENDIX_LINE = "지난달 수치는 부록 A에 있습니다."
_METHOD_CHANGE_REASONS = frozenset(
    {
        "MEASUREMENT_POLICY_CHANGED",
        "QUERY_TEXT_CHANGED",
        "SAMPLE_SHAPE_CHANGED",
        "PLATFORM_COHORT_MISSING",
        "ANSWER_MODEL_CHANGED",
    }
)

GROUP_LABELS = {
    "A": "병원명 질문",
    "B": "진료·검사 탐색",
    "C": "증상·고민 질문",
    "D": "상황·조건 질문",
}
_INTENT_TO_GROUP = {
    INTENT_RECOMMENDATION: "B",
    INTENT_AVAILABILITY: "B",
    INTENT_SYMPTOM: "C",
    INTENT_COST: "D",
    INTENT_COMPARISON: "D",
    INTENT_INFO: None,
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 뷰 사실 중 저장 요약에서 다시 만들 수 있어 근거가 꼭 있어야 하는 키(접두어 포함).
_STORED_BACKED_KEYS = frozenset({"rate.current", "rate.previous", "card.rate", "card.keywords", "card.published", "citation"})
_STORED_BACKED_PREFIXES = ("platform.", "kw.")


# ── 뷰 모델 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Card:
    label: str
    value: str
    definition: str
    # 카드 4(노출 진료 항목)의 작은 이름 나열. 다른 카드는 비어 있다.
    names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryPage:
    title: str
    subtitle: str
    cards: tuple[Card, ...]
    # 잘된 점 · 보완할 점 · 다음 달 중점
    key_lines: tuple[str, ...]
    # 비교하지 않은 달에만 한 줄. 아니면 빈 문자열.
    notice: str = ""


@dataclass(frozen=True, slots=True)
class ContentRow:
    published_on: str  # "10-03"
    title: str
    treatment: str
    keyword: str
    url: str | None


@dataclass(frozen=True, slots=True)
class BarItem:
    label: str
    value: float | None  # 비율(%) 또는 편수. None은 측정 없음.
    detail: str  # "3편" / "AI 답변 10건 중 3건"


@dataclass(frozen=True, slots=True)
class ContentPage:
    headline: str  # "콘텐츠 발행 현황"
    bars: tuple[BarItem, ...]  # 진료 항목별 발행 편수
    rows: tuple[ContentRow, ...]
    empty_text: str = ""


@dataclass(frozen=True, slots=True)
class TrendPoint:
    label: str  # "8월"
    value: float


@dataclass(frozen=True, slots=True)
class GroupRow:
    group: str  # "B"
    label: str
    keyword_count: int
    rate: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class HeatCell:
    platform: str
    state: Literal["mentioned", "unmentioned", "failed", "unmeasured"]
    detail: str  # "3/5"


@dataclass(frozen=True, slots=True)
class HeatRow:
    keyword: str
    cells: tuple[HeatCell, ...]


@dataclass(frozen=True, slots=True)
class ExposurePage:
    headline: str
    trend: tuple[TrendPoint, ...]  # 현재 달 포함, 비교 가능한 달이 2개 미만이면 빈 튜플
    treatment_bars: tuple[BarItem, ...]
    platform_bars: tuple[BarItem, ...]
    group_rows: tuple[GroupRow, ...]
    heatmap: tuple[HeatRow, ...]
    citation_lines: tuple[str, ...]
    # 비교한 달에 헤드라인(같은 질문만)과 AI 서비스별 수치(이번 달 전체)의 분모가 다를 때 한 줄.
    platform_note: str = ""


@dataclass(frozen=True, slots=True)
class CompetitorRow:
    name: str
    mention_count: int
    observed_count: int
    rate: float
    # 템플릿이 찍는 칸 문자열 "20건 중 12건" — 검증·대조가 같은 문자열을 본다.
    count_label: str = ""


@dataclass(frozen=True, slots=True)
class ReplacedRow:
    keyword: str
    competitors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompetitionPage:
    headline: str
    basis: str  # "등록한 경쟁 병원 3곳 기준"
    rows: tuple[CompetitorRow, ...]
    replaced: tuple[ReplacedRow, ...]
    unexposed_count: int | None
    empty_text: str = ""


@dataclass(frozen=True, slots=True)
class Case:
    label: str
    keyword: str
    excerpt: str
    highlight: str  # 발췌 안에서 강조할 구간. 없으면 빈 문자열
    platform: str
    measured_on: str  # "2026-10-01"
    insight: str


@dataclass(frozen=True, slots=True)
class CasesPage:
    headline: str
    found: Case | None
    missing: Case | None
    empty_text: str = ""


PlanKind = Literal["publish", "optimize", "first"]


@dataclass(frozen=True, slots=True)
class PlanRow:
    priority: int
    treatment: str
    rate_label: str  # "0%" / "다음 달부터 측정"
    action: str
    keyword: str
    kind: PlanKind = "publish"


@dataclass(frozen=True, slots=True)
class PlanPage:
    headline: str
    rows: tuple[PlanRow, ...]  # 본문에 두는 행
    continued: tuple[PlanRow, ...]  # 부록으로 이어지는 행
    empty_text: str = ""


@dataclass(frozen=True, slots=True)
class AppendixKeywordRow:
    keyword: str
    group: str
    treatment: str
    cells: tuple[str, ...]  # 플랫폼 순서대로 "3/5"
    total: str  # "6/10"
    previous: str  # 비교한 달이면 "2/10", 아니면 ""


@dataclass(frozen=True, slots=True)
class Appendix:
    keyword_rows: tuple[AppendixKeywordRow, ...]
    keyword_note: str  # 지난달 참고 수치·V0 한 줄. 없으면 ""
    method_lines: tuple[str, ...]
    glossary: tuple[tuple[str, str], ...]
    caveat_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DoctorReportV4:
    template_version: Literal["v4"]
    report_kind: ReportKindV4
    hospital_name: str
    period_label: str  # "2026년 10월"
    period_ym: str  # "2026-10"
    public_url: str
    platforms: tuple[str, ...]
    summary: SummaryPage
    content: ContentPage
    exposure: ExposurePage
    competition: CompetitionPage
    cases: CasesPage
    plan: PlanPage
    appendix: Appendix
    # 지난달·이번 달 노출률(비교한 달만 previous가 있다). 검증·대조가 읽는다.
    current_rate: float | None
    previous_rate: float | None
    reference_previous_rate: float | None
    # AI 인용 수(출처 확인 가능한 답변 수·인용 답변 수). 모르면 None.
    citation_measured: int | None = None
    citation_cited: int | None = None
    # 측정된 키워드 수·노출된 키워드 수(헤드라인이 없어 카드에 안 찍혀도 남긴다 — v3 대조용).
    keyword_measured: int | None = None
    keyword_exposed: int | None = None
    sample_label: str = ""

    # 내부 AE 화면·Admin이 읽는 세 줄. 원장 PDF 본문의 핵심 3줄과 같다.
    @property
    def talking_points(self) -> list[str]:
        return list(self.summary.key_lines)

    # 렌더 검증이 쪽마다 요구하는 문구. 템플릿은 이 문자열을 글자 그대로 찍는다.
    def required_texts(self) -> tuple[tuple[str, ...], ...]:
        s = self.summary
        p1: list[str] = [s.title, self.hospital_name, *s.key_lines]
        for card in s.cards:
            p1.extend((card.label, card.value, card.definition))
            p1.extend(card.names)
        if s.notice:
            p1.append(s.notice)
        p2: list[str] = [self.content.headline]
        for row in self.content.rows:
            p2.extend((row.title, row.treatment))
        p2.extend(bar.detail for bar in self.content.bars)
        if self.content.empty_text:
            p2.append(self.content.empty_text)
        p3: list[str] = [self.exposure.headline]
        for bar in (*self.exposure.platform_bars, *self.exposure.treatment_bars):
            p3.extend((bar.label, bar.detail))
            if bar.value is not None:
                p3.append(_pct(bar.value))
        for row in self.exposure.group_rows:
            p3.append(row.detail)
            if row.rate is not None:
                p3.append(_pct(row.rate))
        p3.extend(_pct(point.value) for point in self.exposure.trend)
        p3.extend(self.exposure.citation_lines)
        if self.exposure.platform_note:
            p3.append(self.exposure.platform_note)
        p4: list[str] = [self.competition.headline]
        if self.competition.basis:
            p4.append(self.competition.basis)
        for row in self.competition.rows:
            p4.extend((row.name, row.count_label, _pct(row.rate)))
        if self.competition.empty_text:
            p4.append(self.competition.empty_text)
        p5: list[str] = [self.cases.headline]
        for case in (self.cases.found, self.cases.missing):
            if case is not None:
                p5.extend((case.keyword, case.insight))
        if self.cases.empty_text:
            p5.append(self.cases.empty_text)
        p6: list[str] = [self.plan.headline]
        for row in self.plan.rows:
            p6.extend((row.keyword, row.action))
        if self.plan.empty_text:
            p6.append(self.plan.empty_text)
        appendix: list[str] = []
        for row in self.appendix.keyword_rows:
            appendix.extend((row.keyword, row.total))
        for row in self.plan.continued:
            appendix.extend((row.keyword, row.action))
        appendix.extend(self.appendix.method_lines)
        for term, definition in self.appendix.glossary:
            appendix.extend((term, definition))
        appendix.extend(self.appendix.caveat_lines)
        if self.appendix.keyword_note:
            appendix.append(self.appendix.keyword_note)
        return (
            tuple(_dedupe(p1)), tuple(_dedupe(p2)), tuple(_dedupe(p3)), tuple(_dedupe(p4)),
            tuple(_dedupe(p5)), tuple(_dedupe(p6)), tuple(_dedupe(appendix)),
        )

    def required_links(self) -> tuple[str, ...]:
        return tuple(_dedupe(row.url for row in self.content.rows if row.url))

    # 템플릿 갱신의 숫자 대조가 쓰는 '정본 사실'. 모르는 값은 뺀다(0과 섞지 않는다).
    # 키워드 사실은 키워드 문장을 키에 넣어 행이 바뀌어도 정체성이 남는다(검토 4).
    def facts(self) -> dict[str, str]:
        facts: dict[str, str] = {"template": TEMPLATE_VERSION}
        for key, value in (
            ("rate.current", self.current_rate),
            ("rate.previous", self.previous_rate),
            ("rate.reference_previous", self.reference_previous_rate),
        ):
            if value is not None:
                facts[key] = _pct(value)
        for card, key in zip(self.summary.cards, ("published", "rate", "keywords", "treatments"), strict=False):
            if _NUMBER.search(card.value):
                facts[f"card.{key}"] = card.value
            if key == "published":
                match = re.search(r"계획 (\d+편 중 \d+편)", card.definition)
                if match:
                    facts["card.plan"] = match.group(1)
                match = re.search(r"누적 (\d+편)", card.definition)
                if match:
                    facts["card.cumulative"] = match.group(1)
        for bar in self.exposure.platform_bars:
            if bar.value is not None:
                platform = _platform_id(bar.label)
                facts[f"platform.{platform}"] = _pct(bar.value)
                facts[f"platform.{platform}.answers"] = bar.detail
        for bar in self.exposure.treatment_bars:
            if bar.value is not None:
                facts[f"treatment.{bar.label}"] = bar.detail
                facts[f"treatment.{bar.label}.rate"] = _pct(bar.value)
        for row in self.exposure.group_rows:
            if row.rate is not None:
                facts[f"group.{row.group}"] = row.detail
                facts[f"group.{row.group}.rate"] = _pct(row.rate)
        for point in self.exposure.trend:
            facts[f"trend.{point.label}"] = _pct(point.value)
        if self.citation_measured is not None and self.citation_cited is not None:
            facts["citation"] = f"{self.citation_measured}건 중 {self.citation_cited}건"
        for row in self.competition.rows:
            facts[f"competitor.{row.name}"] = row.count_label
            facts[f"competitor.{row.name}.rate"] = _pct(row.rate)
        for row in self.appendix.keyword_rows:
            if row.total != "—":
                facts[f"kw.{row.keyword}.total"] = row.total
            for platform, cell in zip(self.platforms, row.cells, strict=False):
                if cell != "—":
                    facts[f"kw.{row.keyword}.{platform}"] = cell
            if row.previous:
                facts[f"kw.{row.keyword}.previous"] = row.previous
        return facts


# ── 공통 ────────────────────────────────────────────────────────────────────


def _pct(value: float) -> str:
    rounded = round(value)
    # 의료광고 필터가 '100%'를 어디서나 막는다. 숫자는 그대로 두고 표기만 '100.0%'로 한다.
    return "100.0%" if rounded >= 100 else f"{rounded}%"


def _platform_id(label: str) -> str:
    for key, name in PLATFORM_NAMES.items():
        if label.startswith(name):
            return key
    return label.lower()


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(str(platform or "").lower(), "기타 AI 서비스")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return float(value)


def _percent(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and 0 <= number <= 100 else None


def _rate(mentioned: int, attempts: int) -> float | None:
    return None if attempts <= 0 else round(mentioned / attempts * 100, 2)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _comparable_comparison(comparison: Mapping[str, Any] | None) -> bool:
    """`report_narrative`·`monthly_template_refresh._comparable`과 같은 판정(검토 6)."""
    if not isinstance(comparison, Mapping):
        return False
    return bool(
        comparison.get("status") == "COMPARABLE"
        and comparison.get("reason") == "MATCHED_COHORT"
        and isinstance(comparison.get("matched_cell_count"), (int, float))
        and comparison.get("matched_cell_count") > 0
        and _percent(comparison.get("prior_sov_pct")) is not None
        and _percent(comparison.get("current_sov_pct")) is not None
    )


# ── 입력 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KeywordInfo:
    """측정 키워드 하나의 보조 정보 — 타깃에서 읽는다. 타깃이 없으면 폴백."""

    treatment: str
    group: str | None
    region: str | None


def _target_texts(target: Any) -> set[str]:
    texts = {str(getattr(target, "name", "") or "").strip()}
    for variant in getattr(target, "variants", None) or ():
        texts.add(str(getattr(variant, "query_text", "") or "").strip())
    texts.discard("")
    return texts


def _target_treatment(target: Any) -> str | None:
    for name in ("condition_or_symptom", "treatment", "specialty"):
        value = getattr(target, name, None)
        if value and str(value).strip():
            return str(value).strip()
    return None


def _keyword_index(
    query_targets: Sequence[Any], hospital_regions: Sequence[str]
) -> tuple[dict[str, KeywordInfo], dict[str, KeywordInfo]]:
    """질문 문장 → 보조 정보, 타깃 id → 보조 정보."""
    by_text: dict[str, KeywordInfo] = {}
    by_id: dict[str, KeywordInfo] = {}
    for target in query_targets:
        regions = [str(term) for term in (getattr(target, "region_terms", None) or []) if str(term).strip()]
        region = regions[0] if regions else (str(hospital_regions[0]) if hospital_regions else None)
        treatment = _target_treatment(target)
        if treatment is None:
            treatment = clinical_keyword_from_query(
                str(getattr(target, "name", "") or ""), regions or list(hospital_regions)
            ) or UNKNOWN_TREATMENT
        intent = str(getattr(target, "target_intent", "") or "")
        info = KeywordInfo(treatment=treatment, group=_INTENT_TO_GROUP.get(intent, "B"), region=region)
        by_id[str(getattr(target, "id", ""))] = info
        for text in _target_texts(target):
            by_text.setdefault(text, info)
    return by_text, by_id


def _info_for(keyword: str, by_text: Mapping[str, KeywordInfo], hospital_regions: Sequence[str]) -> KeywordInfo:
    info = by_text.get(keyword)
    if info is not None:
        return info
    treatment = clinical_keyword_from_query(keyword, list(hospital_regions)) or UNKNOWN_TREATMENT
    return KeywordInfo(treatment=treatment, group=None, region=str(hospital_regions[0]) if hospital_regions else None)


@dataclass(frozen=True, slots=True)
class _Keyword:
    text: str
    info: KeywordInfo
    attempts: int
    mentioned: int
    prior_attempts: int | None
    prior_mentioned: int | None
    # 플랫폼별 (상태, 언급, 시도) — 같은 문장의 셀은 플랫폼 안에서 합친다(검토 8).
    cells: dict[str, tuple[str, int, int]] = field(default_factory=dict)

    @property
    def rate(self) -> float | None:
        return _rate(self.mentioned, self.attempts)

    @property
    def exposed(self) -> bool:
        return self.mentioned > 0

    @property
    def prior_comparable(self) -> bool:
        return self.prior_attempts is not None and self.prior_attempts > 0

    # 키워드 단위의 변화 — 플랫폼 한 곳의 변화를 키워드 변화로 올리지 않는다(검토 9).
    @property
    def newly_exposed(self) -> bool:
        return self.exposed and self.prior_comparable and (self.prior_mentioned or 0) == 0

    @property
    def lost(self) -> bool:
        return self.attempts > 0 and not self.exposed and self.prior_comparable and (self.prior_mentioned or 0) > 0


def _merge_state(current: str, incoming: str) -> str:
    order = {"SUCCESS": 0, "FAILED": 1, "EXCLUDED": 2, "": 3}
    return incoming if order.get(incoming, 3) < order.get(current, 3) else current


def _keywords(
    coverage: Mapping[str, Any] | None,
    attribution: Mapping[str, Any] | None,
    by_text: Mapping[str, KeywordInfo],
    hospital_regions: Sequence[str],
) -> list[_Keyword]:
    """측정 키워드 목록. 저장 payload의 `queries`(합계)와 `cells`(플랫폼별)에서 만든다."""
    rows: dict[str, dict[str, Any]] = {}
    for query in (coverage or {}).get("queries") or []:
        text = str(query.get("query_text") or "").strip()
        if not text or str(query.get("query_intent") or "LOCAL") != "LOCAL":
            continue
        row = rows.setdefault(text, {"attempts": 0, "mentioned": 0, "cells": {}})
        row["attempts"] += int(query.get("attempts_used") or 0)
        row["mentioned"] += int(query.get("mentioned_attempts") or 0)
    for cell in (coverage or {}).get("cells") or []:
        text = str(cell.get("query_text") or "").strip()
        if text not in rows:
            continue
        platform = str(cell.get("platform") or "").lower()
        state, mentioned, attempts = rows[text]["cells"].get(platform, ("", 0, 0))
        rows[text]["cells"][platform] = (
            _merge_state(state, str(cell.get("state") or "")),
            mentioned + int(cell.get("mentioned_attempts") or 0),
            attempts + int(cell.get("attempts_used") or 0),
        )
    # 지난달 수치는 같은 문장의 모든 행(플랫폼·변형)이 비교 가능할 때만 합친다 — 한 행만 비교 가능하면
    # 지난달 Gemini 언급이 사라져 '새로 노출'로 읽힐 수 있다(검토 2-3).
    prior: dict[str, tuple[int, int]] = {}
    prior_broken: set[str] = set()
    for row in (attribution or {}).get("question_rows") or []:
        text = str(row.get("query_text") or "").strip()
        if not text:
            continue
        if not row.get("prior_comparable"):
            prior_broken.add(text)
            continue
        attempts, mentioned = prior.get(text, (0, 0))
        prior[text] = (
            attempts + int(row.get("prior_attempts_used") or 0),
            mentioned + int(row.get("prior_mentioned_attempts") or 0),
        )
    keywords = []
    for text, row in rows.items():
        prior_pair = None if text in prior_broken else prior.get(text)
        keywords.append(
            _Keyword(
                text=text,
                info=_info_for(text, by_text, hospital_regions),
                attempts=row["attempts"],
                mentioned=row["mentioned"],
                prior_attempts=prior_pair[0] if prior_pair else None,
                prior_mentioned=prior_pair[1] if prior_pair else None,
                cells=row["cells"],
            )
        )
    return keywords


def _appendix_order(keyword: _Keyword) -> tuple:
    return (keyword.info.group or "Z", -keyword.mentioned, keyword.text)


def _setup_keywords(query_targets: Sequence[Any], by_text: Mapping[str, KeywordInfo]) -> list[_Keyword]:
    """기준 설정 달의 계획 행 — 다음 달 실제로 물을 추적 세트(ACTIVE·in_tracking_set)의 질문 문장만(검토 2-10)."""
    rows: list[_Keyword] = []
    seen: set[str] = set()
    for target in query_targets:
        if str(getattr(target, "status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE":
            continue
        if not bool(getattr(target, "in_tracking_set", False)):
            continue
        variants = [
            str(getattr(variant, "query_text", "") or "").strip()
            for variant in (getattr(target, "variants", None) or ())
            if bool(getattr(variant, "is_active", True))
        ]
        texts = [text for text in variants if text] or [str(getattr(target, "name", "") or "").strip()]
        for text in texts:
            if text and text not in seen and text in by_text:
                seen.add(text)
                rows.append(_Keyword(text, by_text[text], 0, 0, None, None))
    return rows


# ── 빌더 ────────────────────────────────────────────────────────────────────


def build_doctor_report_v4(
    *,
    hospital: Any,
    period_year: int,
    period_month: int,
    public_url: str,
    report_kind: ReportKindV4,
    sov_coverage: Mapping[str, Any] | None,
    attribution: Mapping[str, Any] | None,
    citations: Mapping[str, Any] | None,
    published_contents: Sequence[Any] = (),
    query_targets: Sequence[Any] = (),
    records: Sequence[Any] = (),
    plan_quota: int | None = None,
    published_count: int = 0,
    fulfilled_count: int | None = None,
    cumulative_published_count: int | None = None,
    comparison_reason: str | None = None,
    reference_prev_sov_pct: float | None = None,
    v0_baseline: Mapping[str, Any] | None = None,
    trend_points: Sequence[tuple[str, float]] = (),
    platforms: Sequence[str] | None = None,
    sample_label: str = "",
) -> DoctorReportV4:
    """원장용 v4 뷰. 입력은 `_build_monthly_report_for_hospital`이 이미 조립한 값이다."""
    hospital_name = str(getattr(hospital, "name", "") or "")
    regions = [str(term) for term in (getattr(hospital, "region", None) or []) if str(term).strip()]
    platform_ids = tuple(str(p).lower() for p in (platforms or PLATFORM_ORDER))
    period_label = f"{period_year}년 {period_month}월"
    period_ym = f"{period_year}-{period_month:02d}"
    setup = report_kind == "SETUP"
    coverage = dict(sov_coverage or {})
    comparison = coverage.get("comparison") or {}
    # 저장 payload의 판정이 정본이다 — 호출부의 comparison_reason은 payload에 없을 때만 쓴다(검토 7).
    comparable = not setup and _comparable_comparison(comparison)
    current = None if setup else _percent(comparison.get("current_sov_pct") if comparable else coverage.get("sov_pct"))
    previous = _percent(comparison.get("prior_sov_pct")) if comparable else None
    reason = str(comparison.get("reason") or comparison_reason or "")
    not_compared = not setup and not comparable and current is not None and reason not in ("", "NO_PRIOR_MANIFEST")
    reference = _percent(reference_prev_sov_pct) if not_compared else None
    # 헤드라인이 같은 질문(매칭 코호트)으로 선 달에는 분모도 그 코호트의 답변 수다(검토 2-5).
    if comparable and int(comparison.get("current_attempts_used") or 0) > 0:
        attempts_used = int(comparison.get("current_attempts_used") or 0)
        mentioned_attempts = int(comparison.get("current_mentioned_attempts") or 0)
    else:
        attempts_used = int(coverage.get("attempts_used") or 0)
        mentioned_attempts = int(coverage.get("mentioned_attempts") or 0)
    platform_note = (
        "AI 서비스별 수치는 이번 달 측정 키워드 전체 기준이고, 첫 장의 AI 노출률은 지난달과 같은 키워드 기준입니다."
        if comparable and int(comparison.get("current_unmatched_cell_count") or 0) > 0
        else ""
    )
    significance = str(coverage.get("significance") or "")

    by_text, by_id = _keyword_index(query_targets, regions)
    keywords = [] if setup else _keywords(coverage, attribution, by_text, regions)
    registered_names = registered_competitor_names(hospital)
    measured = [k for k in keywords if k.attempts > 0]
    exposed = [k for k in measured if k.exposed]
    unexposed = [k for k in measured if not k.exposed]
    # 경쟁 현황은 **지금 등록된** 경쟁 병원만 등록 표기로 센다 — 등록에서 뺀 이름의 판정은 기록에 남아 있어도 보고서에
    # 싣지 않는다(경쟁 병원 반영이 기록을 지우지 않는 대신 여기서 거른다). 등록 목록이 비어 있으면 종전처럼 기록 그대로다.
    competitor_records = scope_competitor_records(records, registered_names)
    competitors_by_keyword = _competitors_by_keyword(competitor_records)
    registered = len(
        {
            str(c.get("name") or "").strip()
            for r in competitor_records
            for c in (getattr(r, "competitor_mentions", None) or [])
            if isinstance(c, dict) and str(c.get("name") or "").strip()
        }
    )
    outcomes = _competitor_outcomes(competitor_records)
    works = published_work_evidence(hospital, list(published_contents), citations)
    content_rows = _content_rows(published_contents, works, by_id)
    fulfilled = published_count if fulfilled_count is None else fulfilled_count
    measured_cells = int((citations or {}).get("measured_cell_count") or 0)
    citation_measured = measured_cells if (measured_cells and not setup) else None
    citation_cited = int((citations or {}).get("cited_cell_count") or 0) if citation_measured else None

    plan = _plan_page(
        keywords=keywords, measured=measured, hospital=hospital,
        competitors_by_keyword=competitors_by_keyword, regions=regions, setup=setup,
        setup_keywords=_setup_keywords(query_targets, by_text) if setup else [],
        current=current,
    )
    summary = _summary_page(
        hospital_name=hospital_name, period_label=period_label, setup=setup,
        current=current, previous=previous, significance=significance,
        attempts_used=attempts_used, mentioned_attempts=mentioned_attempts,
        measured=measured, exposed=exposed, unexposed=unexposed,
        plan_quota=plan_quota, published_count=published_count, fulfilled=fulfilled,
        cumulative=cumulative_published_count, citation_cited=citation_cited, plan=plan,
        not_compared=not_compared, reason=reason, reference=reference,
    )
    exposure = _exposure_page(
        setup=setup, current=current, period_month=period_month, trend_points=trend_points,
        keywords=keywords, measured=measured, coverage=coverage, platform_ids=platform_ids,
        hospital_name=hospital_name, citation_measured=citation_measured, citation_cited=citation_cited,
        platform_note=platform_note,
    )
    competition = _competition_page(
        outcomes=outcomes, unexposed=unexposed, competitors_by_keyword=competitors_by_keyword,
        setup=setup, registered=registered, measured=measured, registered_names=registered_names,
    )
    cases = _cases_page(
        records=competitor_records, hospital_name=hospital_name, setup=setup, by_text=by_text,
        regions=regions, works=works, registered=registered, registered_names=registered_names,
    )
    appendix = _appendix(
        keywords=keywords, platform_ids=platform_ids, comparable=comparable,
        reference=reference, v0_baseline=v0_baseline, coverage=coverage, setup=setup,
    )
    return DoctorReportV4(
        template_version=TEMPLATE_VERSION,
        report_kind=report_kind,
        hospital_name=hospital_name,
        period_label=period_label,
        period_ym=period_ym,
        public_url=public_url,
        platforms=platform_ids,
        summary=summary,
        content=ContentPage(
            headline="콘텐츠 발행 현황",
            bars=_treatment_publish_bars(content_rows),
            rows=content_rows,
            empty_text="" if content_rows else "이번 달 발행한 콘텐츠가 없습니다.",
        ),
        exposure=exposure,
        competition=competition,
        cases=cases,
        plan=plan,
        appendix=appendix,
        current_rate=current,
        previous_rate=previous,
        reference_previous_rate=reference,
        citation_measured=citation_measured,
        citation_cited=citation_cited,
        keyword_measured=len(measured) if not setup else None,
        keyword_exposed=len(exposed) if not setup else None,
        sample_label=sample_label,
    )


# ── P1 ──────────────────────────────────────────────────────────────────────


def _summary_page(
    *, hospital_name, period_label, setup, current, previous, significance, attempts_used,
    mentioned_attempts, measured, exposed, unexposed, plan_quota, published_count, fulfilled,
    cumulative, citation_cited, plan, not_compared, reason, reference,
) -> SummaryPage:
    cumulative_text = f" · 누적 {cumulative}편" if cumulative is not None else ""
    if plan_quota is not None:
        published_card = Card(
            "발행 콘텐츠", f"{published_count}편",
            f"계획 {plan_quota}편 중 {fulfilled}편 발행{cumulative_text}",
        )
    else:
        published_card = Card("발행 콘텐츠", f"{published_count}편", f"이번 달 발행{cumulative_text}")
    if setup:
        rate_card = Card("AI 노출률", NOT_MEASURED_YET, "다음 달 첫 측정으로 기준점을 만듭니다")
        keyword_card = Card("노출 키워드", NOT_MEASURED_YET, "다음 달 첫 측정부터 셉니다")
        treatment_card = Card("노출 진료 항목", NOT_MEASURED_YET, "병원명이 노출된 진료·검사 항목")
    elif current is None:
        rate_card = Card("AI 노출률", MEASUREMENT_INCOMPLETE, "이번 달은 측정이 충분히 이뤄지지 않았습니다")
        keyword_card = Card("노출 키워드", MEASUREMENT_INCOMPLETE, "측정 키워드별 결과를 확인하지 못했습니다")
        treatment_card = Card("노출 진료 항목", MEASUREMENT_INCOMPLETE, "병원명이 노출된 진료·검사 항목")
    else:
        definition = f"AI 답변 {attempts_used}건 중 {mentioned_attempts}건에 {hospital_name} 노출"
        if previous is not None:
            definition += f" · 지난달 {_pct(previous)}"
        rate_card = Card("AI 노출률", _pct(current), definition)
        keyword_card = Card(
            "노출 키워드", f"{len(measured)}개 중 {len(exposed)}개",
            f"측정 키워드 {len(measured)}개 중 {len(exposed)}개에서 병원명 노출",
        )
        names = _dedupe(k.info.treatment for k in exposed if k.info.treatment != UNKNOWN_TREATMENT)
        shown = tuple(names[:TREATMENT_NAMES_ON_CARD])
        if len(names) > TREATMENT_NAMES_ON_CARD:
            shown = (*shown, f"외 {len(names) - TREATMENT_NAMES_ON_CARD}개")
        treatment_card = Card(
            "노출 진료 항목",
            f"{len(names)}개 항목" if names else (UNKNOWN_TREATMENT if exposed else "0개 항목"),
            "병원명이 노출된 진료·검사 항목",
            names=shown,
        )
    key_lines = _key_lines(
        hospital_name=hospital_name, setup=setup, current=current, previous=previous,
        significance=significance, measured=measured, exposed=exposed, unexposed=unexposed,
        citation_cited=citation_cited, plan=plan,
    )
    notice = ""
    if not_compared:
        notice = METHOD_CHANGED_LINE if reason in _METHOD_CHANGE_REASONS else NOT_VERIFIED_LINE
        if reference is not None:
            notice = f"{notice} {REFERENCE_IN_APPENDIX_LINE}"
    return SummaryPage(
        title=f"{period_label} AI 노출 성과 보고서",
        subtitle=f"{hospital_name} · {period_label}",
        cards=(published_card, rate_card, keyword_card, treatment_card),
        key_lines=key_lines,
        notice=notice,
    )


def _key_lines(
    *, hospital_name, setup, current, previous, significance, measured, exposed, unexposed,
    citation_cited, plan,
) -> tuple[str, str, str]:
    if setup:
        return (
            "측정 키워드와 방식을 정했습니다.",
            "다음 달 첫 측정으로 기준점을 만듭니다.",
            _focus_line(plan),
        )
    if current is None or not measured:
        return (
            "이번 달은 측정이 충분히 이뤄지지 않았습니다.",
            "같은 키워드로 재측정합니다.",
            _focus_line(plan),
        )
    new = sorted(k.text for k in exposed if k.newly_exposed)
    if new:
        good = f"'{new[0]}' 등 {len(new)}개 키워드에서 새로 노출됐습니다."
    elif previous is not None and significance == "SIGNIFICANT_UP":
        good = f"AI 노출률이 {_pct(previous)}에서 {_pct(current)}로 올랐습니다."
    elif citation_cited:
        good = f"{hospital_name} 콘텐츠가 AI 답변 {citation_cited}건에 인용됐습니다."
    else:
        good = f"측정 키워드 {len(measured)}개 중 {len(exposed)}개에서 노출됐습니다."
    lost = sorted(k.text for k in unexposed if k.lost)
    if lost:
        weak = f"'{lost[0]}' 등 {len(lost)}개 키워드에서 노출이 끊겼습니다."
    elif unexposed:
        first = sorted(unexposed, key=lambda k: k.text)[0]
        weak = f"'{first.text}' 등 {len(unexposed)}개 키워드는 아직 노출되지 않았습니다."
    else:
        weak = "측정 키워드 전체에서 노출됐습니다."
    return (good, weak, _focus_line(plan))


def _focus_line(plan: PlanPage) -> str:
    """P6 1순위 행의 실행 종류를 그대로 요약한다 — 발행 편수를 지어내지 않는다(검토 12)."""
    rows = [*plan.rows, *plan.continued]
    if not rows:
        return plan.empty_text or "현재 노출 키워드를 유지하도록 콘텐츠를 이어서 발행합니다."
    top = rows[0]
    if top.kind == "first":
        return f"'{top.treatment}' 키워드부터 첫 측정 결과를 확인합니다."
    if top.kind == "optimize":
        return f"'{top.treatment}' 기존 콘텐츠를 고도화하고 재측정합니다."
    count = sum(1 for row in rows if row.treatment == top.treatment and row.kind == "publish")
    return f"'{top.treatment}' 키워드 대응 콘텐츠 {count}편을 우선 발행합니다."


# ── P2 ──────────────────────────────────────────────────────────────────────


def _content_rows(published_contents, works, by_id) -> tuple[ContentRow, ...]:
    urls = {work.content_id: work.url for work in works}
    rows = []
    for item in published_contents:
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        published_at = getattr(item, "first_published_at", None) or getattr(item, "published_at", None)
        date_label = arrow.get(published_at).to("Asia/Seoul").format("MM-DD") if published_at else "—"
        target_id = getattr(item, "query_target_id", None)
        info = by_id.get(str(target_id)) if target_id else None
        brief = getattr(item, "content_brief", None) or {}
        keyword = str(brief.get("target_keyword") or "").strip() if isinstance(brief, Mapping) else ""
        # 진료 항목은 타깃에서만, 타깃 키워드는 브리프에서만 온다 — 서로 대신하지 않는다(검토 17).
        rows.append(
            ContentRow(
                published_on=date_label,
                title=title,
                treatment=info.treatment if info else GENERAL_TREATMENT,
                keyword=keyword or GENERAL_TREATMENT,
                url=urls.get(str(getattr(item, "id", ""))),
            )
        )
    rows.sort(key=lambda row: (row.published_on, row.title))
    return tuple(rows)


def _treatment_publish_bars(rows: Sequence[ContentRow]) -> tuple[BarItem, ...]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.treatment] = counts.get(row.treatment, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(BarItem(name, float(count), f"{count}편") for name, count in ordered)


# ── P3 ──────────────────────────────────────────────────────────────────────


def _exposure_page(
    *, setup, current, period_month, trend_points, keywords, measured, coverage, platform_ids,
    hospital_name, citation_measured, citation_cited, platform_note,
) -> ExposurePage:
    trend: tuple[TrendPoint, ...] = ()
    if not setup and current is not None and len(trend_points) >= 1:
        points = [TrendPoint(_month_label(label), float(value)) for label, value in trend_points]
        points.append(TrendPoint(f"{period_month}월", current))
        trend = tuple(points)
    by_treatment: dict[str, list[int]] = {}
    for keyword in measured:
        bucket = by_treatment.setdefault(keyword.info.treatment, [0, 0])
        bucket[0] += keyword.mentioned
        bucket[1] += keyword.attempts
    treatment_bars = tuple(
        BarItem(name, _rate(m, a), f"AI 답변 {a}건 중 {m}건")
        for name, (m, a) in sorted(
            by_treatment.items(), key=lambda pair: (-(pair[1][0] / pair[1][1]) if pair[1][1] else 0, pair[0])
        )
    )
    platform_rows = {str(row.get("platform") or "").lower(): row for row in coverage.get("platforms") or []}
    platform_bars = []
    for platform in platform_ids:
        row = platform_rows.get(platform) or {}
        attempts = int(row.get("attempts_used") or 0)
        mentioned = int(row.get("mentioned_attempts") or 0)
        # 저장된 mention_rate는 v4 이전 보고서에서 셀 평균이었다 — 표시는 언제나 답변 k/n에서 계산한다(검토 3-18).
        rate = _rate(mentioned, attempts)
        if setup:
            platform_bars.append(BarItem(platform_name(platform), None, NOT_MEASURED_YET))
        elif rate is None:
            platform_bars.append(BarItem(platform_name(platform), None, MEASUREMENT_INCOMPLETE))
        else:
            platform_bars.append(BarItem(platform_name(platform), rate, f"AI 답변 {attempts}건 중 {mentioned}건"))
    groups: dict[str, list[int]] = {}
    group_keywords: dict[str, int] = {}
    for keyword in measured:
        group = keyword.info.group or "B"
        bucket = groups.setdefault(group, [0, 0])
        bucket[0] += keyword.mentioned
        bucket[1] += keyword.attempts
        group_keywords[group] = group_keywords.get(group, 0) + 1
    group_rows = []
    for group, label in GROUP_LABELS.items():
        if group == "A":
            group_rows.append(GroupRow(group, label, 0, None, NOT_MEASURED_YET))
        elif group in groups:
            mentioned, attempts = groups[group]
            group_rows.append(
                GroupRow(group, label, group_keywords[group], _rate(mentioned, attempts), f"AI 답변 {attempts}건 중 {mentioned}건")
            )
    # 히트맵은 측정을 못 한 키워드도 보인다 — 전부 실패한 키워드가 사라지지 않게(검토 11).
    heatmap = tuple(
        HeatRow(
            keyword.text,
            tuple(_heat_cell(platform, keyword.cells.get(platform)) for platform in platform_ids),
        )
        for keyword in sorted(keywords, key=lambda k: (-k.mentioned, -k.attempts, k.text))
    )
    if setup:
        citation_lines = ("AI 인용은 다음 달 첫 측정부터 함께 확인합니다.",)
    elif citation_measured is not None:
        citation_lines = (
            f"출처 확인이 가능한 AI 답변 {citation_measured}건 중 {hospital_name} 콘텐츠 인용 {citation_cited}건",
        )
    else:
        citation_lines = ("이번 달은 AI 답변의 출처를 확인하지 못했습니다. 0건이라는 뜻은 아닙니다.",)
    return ExposurePage(
        headline="AI 노출 성과",
        trend=trend,
        treatment_bars=treatment_bars,
        platform_bars=tuple(platform_bars),
        group_rows=tuple(group_rows),
        heatmap=heatmap,
        citation_lines=citation_lines,
        platform_note=platform_note,
    )


def _heat_cell(platform: str, cell: tuple[str, int, int] | None) -> HeatCell:
    if cell is None:
        return HeatCell(platform, "unmeasured", "—")
    state, mentioned, attempts = cell
    if attempts <= 0:
        return HeatCell(platform, "failed" if state == "FAILED" else "unmeasured", "—")
    return HeatCell(platform, "mentioned" if mentioned > 0 else "unmentioned", f"{mentioned}/{attempts}")


def _month_label(label: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})", label)
    return f"{int(match.group(2))}월" if match else label


# ── P4 ──────────────────────────────────────────────────────────────────────


def _competitor_outcomes(records: Sequence[Any]) -> list[CompetitorRow]:
    counts: dict[str, list[int]] = {}
    for record in records:
        seen: set[str] = set()
        for item in getattr(record, "competitor_mentions", None) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen or not isinstance(item.get("is_mentioned"), bool):
                continue
            seen.add(name)
            bucket = counts.setdefault(name, [0, 0])
            bucket[0] += 1
            bucket[1] += int(item["is_mentioned"])
    rows = [
        CompetitorRow(name, mentioned, observed, round(mentioned / observed * 100, 1), f"{observed}건 중 {mentioned}건")
        for name, (observed, mentioned) in counts.items()
    ]
    rows.sort(key=lambda row: (-row.mention_count, row.name))
    return rows


def _record_competitors(record: Any) -> list[str]:
    names: list[str] = []
    for item in getattr(record, "competitor_mentions", None) or []:
        if isinstance(item, dict) and item.get("is_mentioned") is True:
            name = str(item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _competitors_by_keyword(records: Sequence[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for record in records:
        text = _record_query_text(record)
        if not text:
            continue
        names = result.setdefault(text, [])
        for name in _record_competitors(record):
            if name not in names:
                names.append(name)
    return result


def _competition_page(
    *, outcomes, unexposed, competitors_by_keyword, setup, registered, measured, registered_names
) -> CompetitionPage:
    headline = "경쟁 현황"
    if setup:
        return CompetitionPage(headline, "", (), (), None, "경쟁 병원은 다음 달 첫 측정부터 확인합니다.")
    unexposed_count = len(unexposed) if measured else None
    if not outcomes:
        # 등록이 없는 것과 등록은 있는데 이번 달 관측이 없는 것을 가른다(검토 2-14).
        empty = (
            "등록한 경쟁 병원의 언급을 이번 달은 확인하지 못했습니다."
            if registered_names
            else "등록한 경쟁 병원이 없어 이번 달은 경쟁 현황을 집계하지 않았습니다."
        )
        return CompetitionPage(headline, "", (), (), unexposed_count, empty)
    replaced = tuple(
        ReplacedRow(keyword.text, tuple(competitors_by_keyword.get(keyword.text, [])))
        for keyword in sorted(unexposed, key=lambda k: k.text)
        if competitors_by_keyword.get(keyword.text)
    )
    return CompetitionPage(
        headline,
        f"등록한 경쟁 병원 {registered}곳 기준",
        tuple(outcomes[:TOP_COMPETITORS]),
        replaced,
        unexposed_count,
    )


# ── P5 ──────────────────────────────────────────────────────────────────────


def _record_query_text(record: Any) -> str:
    """월간 보고서는 동결 질문(`report_query_text`) 우선 — 측정 뒤 질문을 고쳐도 사례가 바뀌지 않는다(검토 2-2)."""
    text = _query_text_of(record)
    if not text:
        text = getattr(record, "query_text", None)
    return str(text or "").strip()


def _successful(record: Any) -> bool:
    return str(getattr(record, "measurement_status", "SUCCESS") or "SUCCESS") == "SUCCESS" and isinstance(
        getattr(record, "is_mentioned", None), bool
    )


def _normalize_answer(text: str) -> str:
    """`_excerpt_around`와 같은 정리(마크다운 제거·공백 접기)를 거친 본문."""
    return _excerpt_around(text or "", "", width=10**7)


def _record_cites(record: Any, urls_to_title: Mapping[str, str]) -> str:
    """고른 답변 자신의 출처 URL이 발행 콘텐츠 URL과 같을 때만 그 제목 — 같은 질문의 다른 반복이 인용한 것을 이 답변의 것으로 말하지 않는다(검토 7-5)."""
    for value in getattr(record, "source_urls", None) or []:
        normalized = normalize_cited_url(value)
        if normalized is None:
            continue
        title = urls_to_title.get(f"{normalized.host}{normalized.path}")
        if title:
            return title
    return ""


def usable_case_records(records: Sequence[Any]) -> list[Any]:
    """대표 사례 후보 — 판정이 끝났고 답변 본문이 있는 관측만. 빌더와 AE 수정 판의 후보 목록이 같은 집합을 본다."""
    return [r for r in records if _successful(r) and (getattr(r, "raw_response", None) or "").strip()]


def urls_to_titles(works: Iterable[Any]) -> dict[str, str]:
    """발행 콘텐츠 URL(host+path) → 제목. `works`는 `PublishedWork`나 `ContentRow`처럼 `url`·`title`을 가진 것."""
    urls_to_title: dict[str, str] = {}
    for work in works:
        url = getattr(work, "url", None)
        normalized = normalize_cited_url(url) if url else None
        if normalized is not None:
            urls_to_title[f"{normalized.host}{normalized.path}"] = str(getattr(work, "title", "") or "")
    return urls_to_title


def case_from_record(
    record: Any,
    *,
    found: bool,
    hospital_name: str,
    treatment: str,
    urls_to_title: Mapping[str, str],
    registered: bool,
    registered_names: Sequence[str] = (),
) -> Case:
    """관측 기록 하나를 P5 사례로 만든다 — 빌더(`_cases_page`)와 AE 수정 판의 사례 교체가 같은 규칙을 쓴다.

    '등록한 경쟁 병원도 언급되지 않았다'는 이 답변의 등록 경쟁사(`registered_names`, 없으면 기록에 든 이름) **전부**가
    확정 미언급(False)일 때만 말한다 — 하나라도 미확정(null)이거나 판정이 없으면 모르는 것이지 미언급이 아니다."""
    keyword = _record_query_text(record)
    body = _normalize_answer(record.raw_response or "")
    needle = ""
    if found:
        for candidate in (str(getattr(record, "matched_text", None) or ""), hospital_name):
            candidate = " ".join(candidate.split())
            if candidate and candidate in body:
                needle = candidate
                break
    excerpt = _excerpt_around(record.raw_response or "", needle, width=EXCERPT_CHARS)
    highlight = needle if needle and needle in excerpt else ""
    measured_at = getattr(record, "measured_at", None)
    measured_on = arrow.get(measured_at).to("Asia/Seoul").format("YYYY-MM-DD") if measured_at else ""
    platform = platform_name(getattr(record, "ai_platform", ""))
    if found:
        cited_title = _record_cites(record, urls_to_title)
        if cited_title:
            insight = f"'{cited_title}' 콘텐츠가 출처로 쓰였습니다 → 같은 형식으로 다른 항목도 발행"
        else:
            insight = f"'{treatment}' 키워드에서 노출됐습니다 → 인접 항목으로 키워드 확장"
        label = "노출 사례"
    else:
        names = _record_competitors(record)
        mentions = [item for item in (getattr(record, "competitor_mentions", None) or []) if isinstance(item, dict)]
        judged_false = {str(item.get("name") or "").strip() for item in mentions if item.get("is_mentioned") is False}
        expected = set(registered_names) if registered_names else {str(item.get("name") or "").strip() for item in mentions}
        confirmed_absent = bool(expected) and expected <= judged_false and all(
            item.get("is_mentioned") is False for item in mentions
        )
        if names:
            insight = f"{names[0]}이 대신 언급됐습니다 → '{treatment}' 콘텐츠 고도화"
        elif registered and confirmed_absent:
            insight = f"등록한 경쟁 병원도 언급되지 않았습니다 → '{treatment}' 콘텐츠 1편 발행"
        else:
            insight = f"병원명이 나오지 않았습니다 → '{treatment}' 콘텐츠 1편 발행"
        label = "미노출 사례"
    return Case(label, keyword, excerpt, highlight, platform, measured_on, insight)


def _cases_page(*, records, hospital_name, setup, by_text, regions, works, registered, registered_names=()) -> CasesPage:
    headline = "대표 답변 사례"
    if setup:
        return CasesPage(headline, None, None, "AI 답변 사례는 다음 달 첫 측정부터 보여 드립니다.")
    usable = usable_case_records(records)
    found_record = next((r for r in usable if r.is_mentioned), None)
    missing_record = next((r for r in usable if not r.is_mentioned), None)
    # 인용 주장은 고른 답변 자신의 출처 URL이 발행 콘텐츠 URL과 맞을 때만 한다.
    urls_to_title = urls_to_titles(works)

    def _case(record: Any, *, found: bool) -> Case | None:
        if record is None:
            return None
        keyword = _record_query_text(record)
        return case_from_record(
            record,
            found=found,
            hospital_name=hospital_name,
            treatment=_info_for(keyword, by_text, regions).treatment,
            urls_to_title=urls_to_title,
            registered=bool(registered),
            registered_names=registered_names,
        )

    found = _case(found_record, found=True)
    missing = _case(missing_record, found=False)
    empty = "" if (found or missing) else "이번 달은 보여 드릴 AI 답변을 확인하지 못했습니다. 같은 키워드로 재측정합니다."
    return CasesPage(headline, found, missing, empty)


# ── P6 ──────────────────────────────────────────────────────────────────────


def _hospital_focus_names(hospital: Any) -> set[str]:
    names: set[str] = set()
    for value in getattr(hospital, "hero_specialties", None) or []:
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    for value in getattr(hospital, "treatments", None) or []:
        if isinstance(value, Mapping) and str(value.get("name") or "").strip():
            names.add(str(value["name"]).strip())
        elif isinstance(value, str) and value.strip():
            names.add(value.strip())
    return names


def _plan_page(*, keywords, measured, hospital, competitors_by_keyword, regions, setup, setup_keywords, current) -> PlanPage:
    headline = "다음 달 실행 계획"
    focus = _hospital_focus_names(hospital)
    if setup:
        pool = setup_keywords
    else:
        # 미노출(이번 달 측정됐는데 안 나옴) + 이탈(지난달엔 나왔는데 이번 달엔 안 나옴)은 같은 집합이다.
        pool = [k for k in measured if not k.exposed]
    candidates: list[tuple[tuple, _Keyword, str, PlanKind]] = []
    for keyword in pool:
        treatment = keyword.info.treatment
        is_focus = any(name in treatment or treatment in name for name in focus)
        contested = bool(competitors_by_keyword.get(keyword.text))
        rate = keyword.rate if keyword.attempts else None
        region = keyword.info.region or (regions[0] if regions else "")
        region_prefix = f"{region} " if region else ""
        kind: PlanKind
        if setup:
            kind = "first"
            action = f"'{region_prefix}{treatment}' 키워드 첫 측정 후 콘텐츠 발행 예정"
        elif keyword.lost:
            kind = "optimize"
            action = f"'{treatment}' 기존 콘텐츠 고도화 후 재측정"
        else:
            kind = "publish"
            action = f"'{region_prefix}{treatment}' 키워드 대응 콘텐츠 1편 발행 예정"
        sort_key = (0 if is_focus else 1, 0 if contested else 1, rate if rate is not None else -1.0, keyword.text)
        candidates.append((sort_key, keyword, action, kind))
    candidates.sort(key=lambda item: item[0])
    rows = tuple(
        PlanRow(
            priority=index + 1,
            treatment=keyword.info.treatment,
            rate_label=(NOT_MEASURED_YET if setup else (_pct(keyword.rate) if keyword.rate is not None else MEASUREMENT_INCOMPLETE)),
            action=action,
            keyword=keyword.text,
            kind=kind,
        )
        for index, (_, keyword, action, kind) in enumerate(candidates)
    )
    if rows:
        empty = ""
    elif setup:
        empty = "측정 키워드가 아직 없습니다."
    elif not measured or current is None:
        # 측정이 없던 달을 '전부 노출'로 읽지 않는다(검토 11).
        empty = "이번 달은 측정이 충분히 이뤄지지 않아 같은 키워드로 재측정합니다."
    else:
        empty = "측정 키워드 전체에서 노출돼 추가 계획 없이 현재 콘텐츠를 유지합니다."
    return PlanPage(headline, rows[:PLAN_ROWS_IN_BODY], rows[PLAN_ROWS_IN_BODY:], empty)


# ── 부록 ────────────────────────────────────────────────────────────────────


def _appendix(*, keywords, platform_ids, comparable, reference, v0_baseline, coverage, setup) -> Appendix:
    rows = []
    for keyword in sorted(keywords, key=_appendix_order):
        cells = []
        for platform in platform_ids:
            cell = keyword.cells.get(platform)
            cells.append(f"{cell[1]}/{cell[2]}" if cell and cell[2] > 0 else "—")
        previous = ""
        if comparable and keyword.prior_comparable:
            previous = f"{keyword.prior_mentioned}/{keyword.prior_attempts}"
        rows.append(
            AppendixKeywordRow(
                keyword=keyword.text,
                group=keyword.info.group or "—",
                treatment=keyword.info.treatment,
                cells=tuple(cells),
                total=f"{keyword.mentioned}/{keyword.attempts}" if keyword.attempts else "—",
                previous=previous,
            )
        )
    notes = []
    if reference is not None:
        notes.append(f"지난달 AI 노출률 {_pct(reference)} (측정 방식이 달라 참고만 합니다)")
    if v0_baseline and isinstance(v0_baseline.get("of_hundred"), int) and not isinstance(v0_baseline.get("of_hundred"), bool):
        notes.append(f"처음 측정 {_pct(float(v0_baseline['of_hundred']))} (측정 방식이 달라 참고만 합니다)")
    names = "·".join(platform_name(p) for p in platform_ids)
    if setup:
        method_lines = (
            f"다음 달부터 측정 키워드를 {names}에 같은 방식으로 묻습니다.",
            METHOD_COMPARISON_LINE,
            METHOD_VARIABILITY_LINE,
        )
    else:
        adequacy = coverage.get("observation_adequacy") or {}
        planned = adequacy.get("planned_slots")
        confirmed = adequacy.get("confirmed_slots")
        lineage = str(adequacy.get("lineage") or "")
        planned_cells = int(coverage.get("planned_count") or 0)
        success_cells = int(coverage.get("success_count") or 0)
        failed_cells = int(coverage.get("failed_count") or 0)
        cells_line = f"키워드·AI 서비스 조합 계획 {planned_cells}개 / 확인 {success_cells}개 / 실패 {failed_cells}개"
        if (
            lineage == "SLOTTED"
            and isinstance(planned, int) and isinstance(confirmed, int) and not isinstance(planned, bool)
        ):
            # 반복 단위(회). 실패에는 판정 보류·미도착도 들어간다.
            counts_line = f"계획 {planned}회 / 확인 {confirmed}회 / 실패 {max(planned - confirmed, 0)}회"
        elif lineage == "MIXED":
            # 일부 조합만 반복 기록이 남아 있다 — 반복 횟수를 합계처럼 적지 않는다(검토 4-29).
            counts_line = f"{cells_line} (반복 횟수 기록은 일부만 남아 있습니다)"
        else:
            # 반복 기록이 없는 옛 측정: 조합 수만 적는다. 0회로 쓰지 않는다.
            counts_line = cells_line
        method_lines = (
            f"측정 키워드 {len(keywords)}개를 {names}에 매달 1일 새벽에 같은 방식으로 묻습니다.",
            counts_line,
            METHOD_COMPARISON_LINE,
            METHOD_VARIABILITY_LINE,
        )
    return Appendix(
        keyword_rows=tuple(rows),
        keyword_note=" ".join(notes),
        method_lines=method_lines,
        glossary=GLOSSARY,
        caveat_lines=CAVEAT_LINES,
    )


# ── 얼리기·복원 ─────────────────────────────────────────────────────────────


def freeze_v4(view: DoctorReportV4) -> dict[str, Any]:
    return asdict(view)


def restore_v4(payload: Mapping[str, Any]) -> DoctorReportV4:
    data = dict(payload)
    s = dict(data["summary"])
    s["cards"] = tuple(Card(**{**c, "names": tuple(c.get("names") or ())}) for c in s["cards"])
    s["key_lines"] = tuple(s["key_lines"])
    c = dict(data["content"])
    c["bars"] = tuple(BarItem(**b) for b in c["bars"])
    c["rows"] = tuple(ContentRow(**r) for r in c["rows"])
    e = dict(data["exposure"])
    e["trend"] = tuple(TrendPoint(**t) for t in e["trend"])
    e["treatment_bars"] = tuple(BarItem(**b) for b in e["treatment_bars"])
    e["platform_bars"] = tuple(BarItem(**b) for b in e["platform_bars"])
    e["group_rows"] = tuple(GroupRow(**g) for g in e["group_rows"])
    e["heatmap"] = tuple(HeatRow(r["keyword"], tuple(HeatCell(**h) for h in r["cells"])) for r in e["heatmap"])
    e["citation_lines"] = tuple(e["citation_lines"])
    e["platform_note"] = str(e.get("platform_note") or "")
    k = dict(data["competition"])
    k["rows"] = tuple(CompetitorRow(**r) for r in k["rows"])
    k["replaced"] = tuple(ReplacedRow(r["keyword"], tuple(r["competitors"])) for r in k["replaced"])
    cs = dict(data["cases"])
    cs["found"] = Case(**cs["found"]) if cs.get("found") else None
    cs["missing"] = Case(**cs["missing"]) if cs.get("missing") else None
    p = dict(data["plan"])
    p["rows"] = tuple(PlanRow(**r) for r in p["rows"])
    p["continued"] = tuple(PlanRow(**r) for r in p["continued"])
    a = dict(data["appendix"])
    a["keyword_rows"] = tuple(AppendixKeywordRow(**{**r, "cells": tuple(r["cells"])}) for r in a["keyword_rows"])
    a["method_lines"] = tuple(a["method_lines"])
    a["glossary"] = tuple(tuple(pair) for pair in a["glossary"])
    a["caveat_lines"] = tuple(a["caveat_lines"])
    platforms = data.get("platforms")
    return DoctorReportV4(
        template_version="v4",
        report_kind=data["report_kind"],
        hospital_name=data["hospital_name"],
        period_label=data["period_label"],
        period_ym=data["period_ym"],
        public_url=data["public_url"],
        platforms=tuple(platforms) if platforms is not None else PLATFORM_ORDER,
        summary=SummaryPage(**s),
        content=ContentPage(**c),
        exposure=ExposurePage(**e),
        competition=CompetitionPage(**k),
        cases=CasesPage(**cs),
        plan=PlanPage(**p),
        appendix=Appendix(**a),
        current_rate=_finite(data.get("current_rate")),
        previous_rate=_finite(data.get("previous_rate")),
        reference_previous_rate=_finite(data.get("reference_previous_rate")),
        citation_measured=data.get("citation_measured"),
        citation_cited=data.get("citation_cited"),
        keyword_measured=data.get("keyword_measured"),
        keyword_exposed=data.get("keyword_exposed"),
        sample_label=str(data.get("sample_label") or ""),
    )


def is_v4_payload(payload: Any) -> bool:
    return isinstance(payload, Mapping) and payload.get("template_version") == TEMPLATE_VERSION


# ── 템플릿 갱신의 독립 대조 ──────────────────────────────────────────────────


def stored_facts_v4(sov_summary: Mapping[str, Any], content_summary: Mapping[str, Any]) -> dict[str, str]:
    """저장된 측정·콘텐츠 요약만으로 다시 만든 v4 사실(뷰가 저장값에서 만들 수 있는 키만).

    진료 항목·그룹·경쟁·누적 편수·지난달 참고치는 현재 행이나 다른 달에서 읽으므로 여기에 없다.
    빌더와 같은 판정(`_comparable_comparison`·SETUP)을 쓴다(검토 7).
    """
    setup = sov_summary.get("report_kind") == "SETUP"
    comparison = sov_summary.get("comparison") or {}
    comparable = not setup and _comparable_comparison(comparison)
    facts: dict[str, str] = {"template": TEMPLATE_VERSION}
    current = None if setup else _percent(comparison.get("current_sov_pct") if comparable else sov_summary.get("sov_pct"))
    if current is not None:
        facts["rate.current"] = _pct(current)
        facts["card.rate"] = _pct(current)
    if comparable:
        facts["rate.previous"] = _pct(float(comparison["prior_sov_pct"]))
    facts["card.published"] = f"{content_summary.get('published_count')}편"
    operations = content_summary.get("operations") or {}
    timing = content_summary.get("contract_timing") or {}
    quota = operations.get("plan_quota")
    if quota is not None and timing.get("published_for_contract_count") is not None:
        facts["card.plan"] = f"{quota}편 중 {timing['published_for_contract_count']}편"
    if not setup:
        platforms = {str(r.get("platform") or "").lower(): r for r in sov_summary.get("platforms") or [] if isinstance(r, Mapping)}
        for platform in PLATFORM_ORDER:
            row = platforms.get(platform) or {}
            attempts = int(row.get("attempts_used") or 0)
            rate = _rate(int(row.get("mentioned_attempts") or 0), attempts)
            if rate is not None:
                facts[f"platform.{platform}"] = _pct(rate)
                facts[f"platform.{platform}.answers"] = f"AI 답변 {attempts}건 중 {int(row.get('mentioned_attempts') or 0)}건"
        citations = content_summary.get("citations") or {}
        measured_cells = int(citations.get("measured_cell_count") or 0)
        if measured_cells:
            facts["citation"] = f"{measured_cells}건 중 {int(citations.get('cited_cell_count') or 0)}건"
        keywords = _keywords(sov_summary, content_summary.get("attribution") or {}, {}, [])
        measured = [k for k in keywords if k.attempts > 0]
        if current is not None:
            facts["card.keywords"] = f"{len(measured)}개 중 {sum(1 for k in measured if k.exposed)}개"
        for keyword in keywords:
            if keyword.attempts:
                facts[f"kw.{keyword.text}.total"] = f"{keyword.mentioned}/{keyword.attempts}"
            for platform, (_state, mentioned, attempts) in keyword.cells.items():
                if attempts > 0 and platform in PLATFORM_ORDER:
                    facts[f"kw.{keyword.text}.{platform}"] = f"{mentioned}/{attempts}"
            if comparable and keyword.prior_comparable:
                facts[f"kw.{keyword.text}.previous"] = f"{keyword.prior_mentioned}/{keyword.prior_attempts}"
    return facts


COMPETITOR_FACT_PREFIX = "competitor."


def is_competitor_fact(key: str) -> bool:
    """04절 경쟁 현황 행의 사실(`competitor.{이름}`·`.rate`) — 경쟁 병원 반영이 바꿔도 되는 유일한 사실."""
    return key.startswith(COMPETITOR_FACT_PREFIX)


def compare_facts_excluding_competitors(frozen: Mapping[str, str], view: Mapping[str, str]) -> list[str]:
    """얼린 뷰 사실과 다시 만든 뷰 사실을 경쟁 사실만 빼고 **모든 키**로 대조한다(경쟁 병원 반영의 숫자 보존 판정).

    v3→v4 승격과 달리 두 뷰가 같은 템플릿이라 진료 항목·그룹·추이·누적까지 전부 같아야 한다 — 하나라도 다르면
    현재 행(진료 항목 라벨·누적 편수 등)이 바뀐 것이고, 그때는 경쟁 현황만 바꾸는 갱신이 아니다.
    """
    problems = [
        (f"원장 뷰에 없음: {key}={frozen[key]}" if key not in view else f"{key}: 얼린 {frozen[key]} ≠ 뷰 {view[key]}")
        for key in sorted(frozen)
        if not is_competitor_fact(key) and view.get(key) != frozen[key]
    ]
    problems.extend(
        f"얼린 사실 없음: {key}={view[key]}"
        for key in sorted(set(view) - set(frozen))
        if not is_competitor_fact(key)
    )
    return problems


def competitor_fact_changes(frozen: Mapping[str, str], view: Mapping[str, str]) -> list[str]:
    """경쟁 사실의 변화(새 행·바뀐 값·사라진 행) — 막지 않고 기록한다."""
    changes: list[str] = []
    for key in sorted(set(frozen) | set(view)):
        if not is_competitor_fact(key) or frozen.get(key) == view.get(key):
            continue
        changes.append(f"{key}: {frozen.get(key, '없음')} → {view.get(key, '없음')}")
    return changes


def compare_facts_v4(stored: Mapping[str, str], view: Mapping[str, str]) -> list[str]:
    """저장값에서 다시 만든 사실과 뷰 사실을 키 단위로 대조한다.

    저장 사실이 뷰에서 빠지거나 값이 다르면 차이다. 뷰에만 있는 사실은 저장 요약에서 만들 수
    있어야 하는 키(비율·카드·플랫폼·키워드·인용)만 차이로 본다 — 진료 항목·그룹·경쟁·누적은 현재 행의 값이다.
    """
    problems = [
        (
            f"원장 뷰에 없음: {key}={stored[key]}"
            if key not in view
            else f"{key}: 저장 {stored[key]} ≠ 뷰 {view[key]}"
        )
        for key in sorted(stored)
        if view.get(key) != stored[key]
    ]
    problems.extend(
        f"저장값 근거 없음: {key}={view[key]}"
        for key in sorted(set(view) - set(stored))
        if key in _STORED_BACKED_KEYS or key.startswith(_STORED_BACKED_PREFIXES)
    )
    return problems


def pdf_tokens_v4(facts: Mapping[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    """사실마다 템플릿이 실제로 찍는 토큰(공백 제거).

    표 칸이 붙어 추출되므로('5/60/6', '3편4개') 단위·분수로 끝나는 토큰은 뒤를 보지 않고,
    분수는 앞도 보지 않는다. 인용 사실은 문장 안의 두 숫자를 따로 요구한다(검토 2).
    """
    tokens: list[tuple[str, re.Pattern[str]]] = []
    keyword_rows: dict[str, dict[str, str]] = {}
    for key in sorted(facts):
        value = "".join(facts[key].split())
        if key.startswith("kw."):
            keyword, _, column = key[3:].rpartition(".")
            keyword_rows.setdefault(keyword, {})[column] = value
            continue
        if key in ("template", "rate.current", "rate.previous") or not _NUMBER.search(value):
            continue
        if key == "citation":
            measured, cited = re.findall(r"\d+", value)[:2]
            tokens.append((key, re.compile(rf"답변{measured}건중")))
            tokens.append((key, re.compile(rf"인용{cited}건(?!\d)")))
        elif value.endswith("%"):
            tokens.append((key, re.compile(rf"(?<![\d.]){re.escape(value[:-1])}%")))
        else:
            tail = r"(?!\d|\.\d)" if value[-1].isdigit() else ""
            tokens.append((key, re.compile(rf"(?<![\d.]){re.escape(value)}{tail}")))
    # 부록 A 행: 키워드 문장 뒤(그룹·진료 항목을 건너)에 플랫폼 칸·합계·지난달이 그 순서로 붙어야 한다(검토 2-9).
    # 사실에 없는 플랫폼 칸은 '—'(측정 없음)이거나 그 달에 구성되지 않은 플랫폼(칸 없음)이라 둘 다 허용한다.
    for keyword, columns in sorted(keyword_rows.items()):
        cells = "".join(
            re.escape(columns[platform]) if platform in columns else "(?:—)?" for platform in PLATFORM_ORDER
        )
        row = cells + re.escape(columns.get("total", "") + columns.get("previous", ""))
        head = re.escape("".join(keyword.split()))
        # 토큰 키는 사실 키 하나여야 한다(`pdf_fact_problems`가 facts[key]를 읽는다) — 합계 칸을 대표로 쓴다.
        key = f"kw.{keyword}.total" if "total" in columns else f"kw.{keyword}.{next(iter(columns))}"
        # 진료 항목이 숫자로 끝날 수 있어('비타민D3') 앞 경계는 두지 않는다 — '15/6'이 '5/6'을 만족하는 경우는
        # 렌더 검증(`required_appendix_rows`, 커서 경계)이 먼저 잡는다. 이 토큰은 CLI 이차 확인이다.
        tokens.append((key, re.compile(head + r".{0,60}?" + row + r"(?!\d)")))
    return tokens


# ── v3 스냅샷에서 v4로 올릴 때의 독립 대조 ───────────────────────────────────


def info_query_texts(sov_summary: Mapping[str, Any]) -> frozenset[str]:
    """저장 측정 요약에서 LOCAL이 아닌(일반 건강정보) 질문 문장 — v4 키워드 표에는 없고 v3 질문표에는 있었다."""
    return frozenset(
        str(query.get("query_text") or "").strip()
        for query in sov_summary.get("queries") or []
        if isinstance(query, Mapping) and str(query.get("query_intent") or "LOCAL") != "LOCAL"
    )


def v3_platform_rate_changes(v3_facts: Mapping[str, str], view: DoctorReportV4) -> list[str]:
    """v3 타일(셀 평균)과 v4 막대(답변 k/n)가 다른 AI 서비스 — 의도된 계산식 변경이라 막지 않고 경고로 남긴다."""
    facts = view.facts()
    changes = []
    for platform in PLATFORM_ORDER:
        old, new = v3_facts.get(f"tile.{platform}"), facts.get(f"platform.{platform}")
        if old is None or new is None or not _NUMBER.search(old):
            continue
        if round(float(_NUMBER.search(old).group(0))) != round(float(_NUMBER.search(new).group(0))):
            changes.append(f"{platform_name(platform)}: v3 셀 평균 {old} → v4 답변 기준 {new}")
    return changes


def v3_frozen_facts_problems(
    v3_facts: Mapping[str, str],
    view: DoctorReportV4,
    v3_view: Mapping[str, Any] | None = None,
    *,
    skip_keywords: frozenset[str] = frozenset(),
) -> list[str]:
    """v3 보고서에 얼려 둔 숫자 사실(`doctor_view_facts` v3 키)과 올린 v4 뷰가 같은 숫자를 말하는가.

    저장 요약에서 다시 만든 사실(`stored_facts_v4`)은 v4 빌더와 같은 입력을 읽어 동어반복이 될 수 있으므로,
    원장이 실제로 받았던 v3 판의 숫자와도 맞춘다. 비율은 v3의 표기 정밀도(`%.1f%%`)로 **원값**을 다시 찍어
    문자열로 비교한다(이중 반올림 없음, 검토 3-19). 플랫폼 타일은 v3가 셀 평균이라 비교하지 않는다.
    """
    facts = view.facts()
    problems: list[str] = []
    for key, value in (("rate.current", view.current_rate), ("rate.previous", view.previous_rate)):
        if key in v3_facts:
            printed = None if value is None else f"{value:.1f}%"
            if printed != v3_facts[key]:
                problems.append(f"{key}: v3 {v3_facts[key]} ≠ v4 원값 {printed}")
    contract = v3_facts.get("tile.contract")
    if contract and "중" in contract and facts.get("card.plan") != contract:
        problems.append(f"tile.contract: v3 {contract} ≠ v4 {facts.get('card.plan')}")
    if contract and "중" not in contract and facts.get("card.published") != contract:
        problems.append(f"tile.contract: v3 {contract} ≠ v4 {facts.get('card.published')}")
    published = v3_facts.get("highlight.published_this_month")
    if published is not None and facts.get("card.published") != f"{published}편":
        problems.append(f"highlight.published_this_month: v3 {published} ≠ v4 {facts.get('card.published')}")
    cited_measured, cited = v3_facts.get("highlight.cited_answers_measured"), v3_facts.get("highlight.cited_questions")
    if cited_measured is not None and cited is not None and facts.get("citation") != f"{cited_measured}건 중 {cited}건":
        problems.append(f"highlight.citation: v3 {cited_measured}/{cited} ≠ v4 {facts.get('citation')}")
    cumulative = v3_facts.get("highlight.cumulative_published")
    if cumulative is not None and facts.get("card.cumulative") != f"{cumulative}편":
        problems.append(f"highlight.cumulative_published: v3 {cumulative} ≠ v4 {facts.get('card.cumulative')}")
    # 지난달 참고치·초기 측정 참고선은 얼린 값을 그대로 써야 한다(검토 4-25).
    reference = v3_facts.get("rate.reference_previous")
    printed_reference = None if view.reference_previous_rate is None else f"{view.reference_previous_rate:.1f}%"
    if (reference is None) != (printed_reference is None) or (reference is not None and reference != printed_reference):
        problems.append(f"rate.reference_previous: v3 {reference} ≠ v4 원값 {printed_reference}")
    v0 = v3_facts.get("v0.of_hundred")
    has_v0_note = "처음 측정" in view.appendix.keyword_note
    if v0 is not None and f"처음 측정 {_pct(float(_NUMBER.search(v0).group(0)))}" not in view.appendix.keyword_note:
        problems.append(f"v0.of_hundred: v3 {v0} ≠ v4 부록 '{view.appendix.keyword_note}'")
    if v0 is None and has_v0_note:
        problems.append(f"v0.of_hundred: v3 없음 ≠ v4 부록 '{view.appendix.keyword_note}'")
    # 질문별 결과는 질문 문장으로 맞춘다 — v3 '6번 중 5번'(시도 6·언급 5) = v4 '5/6'.
    if v3_view is not None:
        v4_rows = {row.keyword: row for row in view.appendix.keyword_rows}
        for row in v3_view.get("appendix_rows") or []:
            text = str(row.get("query_text") or "").strip()
            if text in skip_keywords:
                continue  # 일반 건강정보 질문은 v4 키워드 표(헤드라인 코호트) 밖이다(검토 5-30)
            v4_row = v4_rows.get(text)
            for label_key, attr in (("current_label", "total"), ("prev_label", "previous")):
                label = str(row.get(label_key) or "")
                match = re.fullmatch(r"(\d+)번 중 (\d+)번", label)
                if not match:
                    continue
                expected = f"{match.group(2)}/{match.group(1)}"
                actual = getattr(v4_row, attr) if v4_row is not None else None
                if actual != expected:
                    problems.append(f"appendix '{text}' {label_key}: v3 {label} ≠ v4 {actual}")
    return problems


# ── AE 수정 판 — 서술 문구·진료 항목 라벨만 덮어쓰기 ────────────────────────────
# 저장된 숫자는 건드리지 않는다. 수정 값은 `render_inputs.ae_overrides.fields`(필드 경로 → 값)에 있고,
# 뷰 조립 뒤 이 함수 하나가 pristine 뷰(시스템이 만든 판)에 적용한다. 템플릿은 바뀌지 않는다.
# 경로 문법(화이트리스트 — 여기 없는 경로는 거절한다):
#   summary.key_lines[0|1|2]        P1 핵심 3줄
#   summary.cards[3].names          카드 4 '{n}개 항목'의 이름 목록(숫자 n은 그대로, '외 N개' 꼬리는 시스템이 붙인다)
#   cases.found.insight / cases.missing.insight    P5 시사점 문장
#   cases.found.record_id / cases.missing.record_id 대표 사례 교체(그 달 매니페스트의 관측 기록 중에서만)
#   plan.action[<측정 키워드>]        P6 '실행 내용' 문구(본문 행·부록 이어짐 행)
#   treatment_label[<측정 키워드>]    측정 키워드의 진료 항목 라벨 — 부록 A·P6·막대·카드 4·콘텐츠 표에 일관 적용
AE_OVERRIDES_SCHEMA_VERSION = 1
AE_FIELD_KEY_LINE = "key_line"
AE_FIELD_CARD_NAMES = "card_names"
AE_FIELD_CASE_INSIGHT = "case_insight"
AE_FIELD_CASE_RECORD = "case_record"
AE_FIELD_PLAN_ACTION = "plan_action"
AE_FIELD_TREATMENT_LABEL = "treatment_label"
AE_PATH_CARD_NAMES = "summary.cards[3].names"
_AE_KEY_LINE_RE = re.compile(r"^summary\.key_lines\[([0-2])\]$")
_AE_CASE_RE = re.compile(r"^cases\.(found|missing)\.(insight|record_id)$")
_AE_PLAN_ACTION_RE = re.compile(r"^plan\.action\[(.+)\]$", re.DOTALL)
_AE_TREATMENT_LABEL_RE = re.compile(r"^treatment_label\[(.+)\]$", re.DOTALL)
_AE_CARD_TAIL_RE = re.compile(r"^외 \d+개$")
_QUOTED = re.compile(r"'([^']*)'")


@dataclass(frozen=True, slots=True)
class AeField:
    """해석된 수정 경로. `arg`는 핵심 줄 번호(int)·사례 자리('found'/'missing')·측정 키워드(str)다."""

    kind: str
    arg: Any = None


@dataclass(frozen=True, slots=True)
class CaseCandidate:
    """대표 사례로 고를 수 있는 관측 기록 하나 — `record_id`는 SovRecord id, `case`는 그 기록으로 만든 P5 사례."""

    record_id: str
    found: bool
    case: Case


def parse_ae_path(path: str) -> AeField | None:
    """수정 경로 → 필드. 화이트리스트 밖이면 None."""
    if not isinstance(path, str):
        return None
    if path == AE_PATH_CARD_NAMES:
        return AeField(AE_FIELD_CARD_NAMES)
    if (match := _AE_KEY_LINE_RE.match(path)) is not None:
        return AeField(AE_FIELD_KEY_LINE, int(match.group(1)))
    if (match := _AE_CASE_RE.match(path)) is not None:
        kind = AE_FIELD_CASE_INSIGHT if match.group(2) == "insight" else AE_FIELD_CASE_RECORD
        return AeField(kind, match.group(1))
    if (match := _AE_PLAN_ACTION_RE.match(path)) is not None:
        return AeField(AE_FIELD_PLAN_ACTION, match.group(1))
    if (match := _AE_TREATMENT_LABEL_RE.match(path)) is not None:
        return AeField(AE_FIELD_TREATMENT_LABEL, match.group(1))
    return None


def ae_path(kind: str, arg: Any = None) -> str:
    """`parse_ae_path`의 역함수 — 화면·응답이 쓰는 경로 문자열."""
    if kind == AE_FIELD_CARD_NAMES:
        return AE_PATH_CARD_NAMES
    if kind == AE_FIELD_KEY_LINE:
        return f"summary.key_lines[{int(arg)}]"
    if kind == AE_FIELD_CASE_INSIGHT:
        return f"cases.{arg}.insight"
    if kind == AE_FIELD_CASE_RECORD:
        return f"cases.{arg}.record_id"
    if kind == AE_FIELD_PLAN_ACTION:
        return f"plan.action[{arg}]"
    if kind == AE_FIELD_TREATMENT_LABEL:
        return f"treatment_label[{arg}]"
    raise ValueError(f"unknown AE field kind: {kind}")


def split_card_names(names: Sequence[str]) -> tuple[list[str], str | None]:
    """카드 4 이름 목록을 (이름들, '외 N개' 꼬리)로 가른다 — 꼬리는 숫자라 사람이 고치지 않는다."""
    items = list(names)
    if items and _AE_CARD_TAIL_RE.match(items[-1]):
        return items[:-1], items[-1]
    return items, None


def relabel_quoted(text: str, old: str, new: str) -> str:
    """규칙 문장 안의 작은따옴표 구간(`'치료하'`·`'서울 치료하'`)만 새 라벨로 바꾼다. 나머지 글자는 그대로다."""
    if not old or old == new:
        return text

    def swap(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner == old:
            return f"'{new}'"
        if inner.endswith(" " + old):
            return f"'{inner[: -len(old)]}{new}'"
        return match.group(0)

    return _QUOTED.sub(swap, text)


def treatment_bars_from_appendix(rows: Iterable[AppendixKeywordRow]) -> tuple[BarItem, ...]:
    """부록 A 행(키워드별 `m/a`)에서 진료 항목별 노출률 막대를 다시 만든다 — 빌더(`_exposure_page`)와 같은 합산·정렬.

    라벨을 바꾸면 묶음이 바뀌므로 막대는 라벨을 적용한 행에서 다시 센다. 숫자는 행의 분수 그대로다.
    """
    by_treatment: dict[str, list[int]] = {}
    for row in rows:
        if row.total == "—":
            continue
        mentioned, attempts = (int(part) for part in row.total.split("/", 1))
        bucket = by_treatment.setdefault(row.treatment, [0, 0])
        bucket[0] += mentioned
        bucket[1] += attempts
    return tuple(
        BarItem(name, _rate(m, a), f"AI 답변 {a}건 중 {m}건")
        for name, (m, a) in sorted(
            by_treatment.items(), key=lambda pair: (-(pair[1][0] / pair[1][1]) if pair[1][1] else 0, pair[0])
        )
    )


def _treatment_by_keyword(view: DoctorReportV4) -> dict[str, str]:
    return {row.keyword: row.treatment for row in view.appendix.keyword_rows}


def _top_plan_row(plan: PlanPage) -> PlanRow | None:
    rows = [*plan.rows, *plan.continued]
    return rows[0] if rows else None


def _relabel_case(case: Case | None, labels: Mapping[str, str], old_labels: Mapping[str, str]) -> Case | None:
    if case is None or case.keyword not in labels:
        return case
    old = old_labels.get(case.keyword, "")
    return replace(case, insight=relabel_quoted(case.insight, old, labels[case.keyword]))


def apply_ae_overrides(
    view: DoctorReportV4,
    fields: Mapping[str, Any],
    *,
    case_candidates: Sequence[CaseCandidate] = (),
) -> DoctorReportV4:
    """pristine v4 뷰에 AE 수정 값을 적용한 새 뷰. 숫자 사실(`facts`)은 진료 항목 라벨 묶음 말고는 바뀌지 않는다.

    순서: 라벨(묶음·막대·카드 이름·규칙 문장의 따옴표 구간) → 사례 교체 → 명시한 문장. 명시한 문장이 유도된
    값을 이긴다. 경로가 화이트리스트 밖이거나 값이 None(= 수정 취소)이면 건너뛴다 — 거절은 호출부의 검증이 한다.
    """
    parsed: list[tuple[AeField, Any]] = []
    for path, value in fields.items():
        field_ = parse_ae_path(path)
        if field_ is not None and value is not None:
            parsed.append((field_, value))
    old_labels = _treatment_by_keyword(view)
    labels = {
        f.arg: str(v).strip()
        for f, v in parsed
        if f.kind == AE_FIELD_TREATMENT_LABEL and f.arg in old_labels and str(v).strip()
    }
    summary, content, exposure, cases, plan, appendix = (
        view.summary, view.content, view.exposure, view.cases, view.plan, view.appendix,
    )
    if labels:
        appendix = replace(
            appendix,
            keyword_rows=tuple(
                replace(row, treatment=labels[row.keyword]) if row.keyword in labels else row
                for row in appendix.keyword_rows
            ),
        )

        def relabel_plan(row: PlanRow) -> PlanRow:
            if row.keyword not in labels:
                return row
            new = labels[row.keyword]
            return replace(row, treatment=new, action=relabel_quoted(row.action, row.treatment, new))

        plan = replace(
            plan,
            rows=tuple(relabel_plan(row) for row in plan.rows),
            continued=tuple(relabel_plan(row) for row in plan.continued),
        )
        content_rows = tuple(
            replace(row, treatment=labels[row.keyword]) if row.keyword in labels else row
            for row in content.rows
        )
        content = replace(content, rows=content_rows, bars=_treatment_publish_bars(content_rows))
        exposure = replace(exposure, treatment_bars=treatment_bars_from_appendix(appendix.keyword_rows))
        cards = list(summary.cards)
        # 카드 4 이름은 **노출된** 키워드의 진료 항목이다 — 미노출 키워드의 라벨을 고쳐도 카드는 바뀌지 않고(검토 1-P2-2),
        # 노출 키워드의 라벨을 고치면 노출 행(부록 A의 m>0)에서 이름을 다시 모은다. 숫자 n과 '외 N개' 꼬리는 그대로다.
        exposed_relabelled = any(
            row.keyword in labels and row.total != "—" and int(row.total.split("/", 1)[0]) > 0
            for row in view.appendix.keyword_rows
        )
        if len(cards) >= 4 and cards[3].names and exposed_relabelled:
            _names, tail = split_card_names(cards[3].names)
            renamed = _dedupe(
                row.treatment
                for row in appendix.keyword_rows
                if row.total != "—" and int(row.total.split("/", 1)[0]) > 0 and row.treatment != UNKNOWN_TREATMENT
            )[:TREATMENT_NAMES_ON_CARD]
            cards[3] = replace(cards[3], names=(*renamed, *([tail] if tail else ())))
        key_lines = list(summary.key_lines)
        top = _top_plan_row(view.plan)
        if top is not None and top.keyword in labels and len(key_lines) == 3:
            key_lines[2] = relabel_quoted(key_lines[2], top.treatment, labels[top.keyword])
        summary = replace(summary, cards=tuple(cards), key_lines=tuple(key_lines))
        cases = replace(
            cases,
            found=_relabel_case(cases.found, labels, old_labels),
            missing=_relabel_case(cases.missing, labels, old_labels),
        )
    candidates = {(c.found, c.record_id): c for c in case_candidates}
    for field_, value in parsed:
        if field_.kind != AE_FIELD_CASE_RECORD:
            continue
        candidate = candidates.get((field_.arg == "found", str(value)))
        if candidate is None:
            continue
        chosen = candidate.case
        if labels:
            chosen = _relabel_case(chosen, labels, old_labels) or chosen
        cases = replace(cases, **{field_.arg: chosen}, empty_text="")
    key_lines = list(summary.key_lines)
    cards = list(summary.cards)
    plan_actions: dict[str, str] = {}
    for field_, value in parsed:
        if field_.kind == AE_FIELD_KEY_LINE and 0 <= field_.arg < len(key_lines):
            key_lines[field_.arg] = str(value).strip()
        elif field_.kind == AE_FIELD_CARD_NAMES and len(cards) >= 4 and isinstance(value, (list, tuple)):
            _, tail = split_card_names(cards[3].names)
            renamed = _dedupe(str(name).strip() for name in value)
            cards[3] = replace(cards[3], names=(*renamed, *([tail] if tail else ())))
        elif field_.kind == AE_FIELD_CASE_INSIGHT:
            current = getattr(cases, field_.arg)
            if current is not None:
                cases = replace(cases, **{field_.arg: replace(current, insight=str(value).strip())})
        elif field_.kind == AE_FIELD_PLAN_ACTION:
            plan_actions[field_.arg] = str(value).strip()
    if plan_actions:
        plan = replace(
            plan,
            rows=tuple(replace(r, action=plan_actions[r.keyword]) if r.keyword in plan_actions else r for r in plan.rows),
            continued=tuple(
                replace(r, action=plan_actions[r.keyword]) if r.keyword in plan_actions else r for r in plan.continued
            ),
        )
    summary = replace(summary, cards=tuple(cards), key_lines=tuple(key_lines))
    return replace(
        view, summary=summary, content=content, exposure=exposure, cases=cases, plan=plan, appendix=appendix
    )


def case_candidates_for_view(
    view: DoctorReportV4, records: Sequence[Any], *, registered: bool | None = None
) -> list[CaseCandidate]:
    """그 달 관측 기록으로 P5 사례 후보를 만든다 — 진료 항목·인용 제목·경쟁 등록 여부는 (pristine) 뷰에서 읽는다."""
    treatments = _treatment_by_keyword(view)
    urls_to_title = urls_to_titles(view.content.rows)
    is_registered = bool(view.competition.rows or view.competition.basis) if registered is None else registered
    candidates: list[CaseCandidate] = []
    for record in usable_case_records(records):
        record_id = str(getattr(record, "id", "") or "")
        if not record_id:
            continue
        keyword = _record_query_text(record)
        found = bool(record.is_mentioned)
        case = case_from_record(
            record,
            found=found,
            hospital_name=view.hospital_name,
            treatment=treatments.get(keyword, UNKNOWN_TREATMENT),
            urls_to_title=urls_to_title,
            registered=is_registered,
        )
        candidates.append(CaseCandidate(record_id, found, case))
    return candidates


__all__ = [
    "Appendix", "AppendixKeywordRow", "BarItem", "CAVEAT_LINES", "Card", "Case", "CasesPage",
    "CompetitionPage", "CompetitorRow", "ContentPage", "ContentRow", "DoctorReportV4",
    "ExposurePage", "GLOSSARY", "GROUP_LABELS", "GroupRow", "HeatCell", "HeatRow",
    "METHOD_CHANGED_LINE", "NOT_VERIFIED_LINE", "REFERENCE_IN_APPENDIX_LINE", "NOT_MEASURED_YET",
    "MEASUREMENT_INCOMPLETE", "PLAN_ROWS_IN_BODY", "PlanPage", "PlanRow", "ReplacedRow",
    "SummaryPage", "TEMPLATE_VERSION", "TrendPoint", "build_doctor_report_v4", "compare_facts_v4",
    "freeze_v4", "is_v4_payload", "pdf_tokens_v4", "platform_name", "restore_v4", "stored_facts_v4",
    "v3_frozen_facts_problems", "v3_platform_rate_changes", "info_query_texts",
    "AE_OVERRIDES_SCHEMA_VERSION", "AE_PATH_CARD_NAMES", "AeField", "CaseCandidate", "ae_path",
    "apply_ae_overrides", "case_candidates_for_view", "case_from_record", "parse_ae_path",
    "relabel_quoted", "split_card_names", "treatment_bars_from_appendix", "urls_to_titles",
    "usable_case_records",
]
