"""Deterministic customer narrative; no provider calls or inferred causality."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from app.services.monthly_sov_payload import MonthlySovPayload
from app.services.report_attribution import CitationSummaryPayload, ContentAttributionPayload

ReportKind = Literal["LEGACY", "MONTHLY", "INITIAL"]


# 원장용 문장의 플랫폼 이름은 랜딩과 같은 제품명을 쓴다(API 이름이 아니다).
PLATFORM_NAMES = {"chatgpt": "ChatGPT", "gemini": "Gemini"}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(str(platform or "").lower(), "AI 서비스")


@dataclass(frozen=True, slots=True)
class PublishedWork:
    title: str
    content_id: str
    url: str | None
    cited_cells: int | None
    queries: tuple[str, ...]

    @property
    def citation_label(self) -> str:
        """본문 카드의 한 줄. 미확인(None)과 확인한 0을 다르게 말한다."""
        if self.cited_cells is None:
            return "AI 답변에 쓰였는지는 아직 확인하지 못했습니다"
        if self.cited_cells == 0:
            return "이번 달 AI 답변의 출처로는 아직 쓰이지 않았습니다"
        return f"AI 답변의 출처로 쓰였습니다 · 질문 {self.cited_cells}건"

    @property
    def appendix_label(self) -> str:
        if self.cited_cells is None:
            return "확인 못 함"
        if self.cited_cells == 0:
            return "아직 없음"
        return f"질문 {self.cited_cells}건"


@dataclass(frozen=True, slots=True)
class MonthlyNarrative:
    title: str
    conclusion: str
    current: float | None
    previous: float | None
    comparison_note: str
    denominator: str
    priorities: tuple[str, ...]
    works: tuple[PublishedWork, ...]
    platform_details: tuple[str, ...]
    methods: tuple[str, ...]
    citation_scope: str
    citation_details: tuple[str, ...]
    fulfillment_note: str
    # 숫자가 없을 때 칸에 쓰는 말. 첫 측정과 '비교하지 않음'을 구분한다.
    previous_label: str = "비교 없음"
    current_label: str = "측정 못 함"
    # 측정 방식이 바뀌어 나란히 비교하지 않는 달에도 원장님이 지난달 받은 수치는 참고로
    # 보여 준다. `previous`(비교 값)와 섞지 않는다 — 증감 문장·검증은 `previous`만 본다.
    reference_previous: float | None = None


# 비교하지 않은 이유를 원장님이 읽을 수 있는 한 문장으로. 한 보고서에는 하나만 나온다.
_NOT_COMPARED = "이번 달은 지난달과 나란히 비교하지 않았습니다."
_FIRST_MEASUREMENT_NOTE = (
    "첫 측정이라 지난달과 비교할 숫자가 없습니다. 다음 달부터 이번 달과 나란히 비교해 드립니다."
)
_COMPARISON_NOTES = {
    "NO_PRIOR_MANIFEST": _FIRST_MEASUREMENT_NOTE,
    "ANSWER_MODEL_CHANGED": f"AI 서비스의 답변 모델이 바뀌어 {_NOT_COMPARED}",
    "MEASUREMENT_POLICY_CHANGED": (
        f"측정 방식이 바뀌어 {_NOT_COMPARED} 다음 달부터 다시 비교해 드립니다."
    ),
    "QUERY_TEXT_CHANGED": f"물어보는 질문 문장이 바뀌어 {_NOT_COMPARED}",
    "PLATFORM_COHORT_MISSING": f"두 달에 물어본 AI 서비스가 달라 {_NOT_COMPARED}",
    "INTENT_SNAPSHOT_MISSING": f"지난달 질문 기록이 온전하지 않아 {_NOT_COMPARED}",
    "NO_MATCHED_CELLS": f"두 달에 똑같이 물어본 질문이 없어 {_NOT_COMPARED}",
    "ANSWER_MODEL_UNKNOWN": f"어떤 AI 모델이 답했는지 기록이 없어 {_NOT_COMPARED}",
    "SAMPLE_SHAPE_CHANGED": f"질문마다 물어본 횟수가 지난달과 달라 {_NOT_COMPARED}",
}
_DEFAULT_COMPARISON_NOTE = f"지난달과 같은 조건인지 확인하지 못해 {_NOT_COMPARED}"
_REFERENCE_CONCLUSION = "측정 방식이 바뀐 달이라, 지난달 수치는 참고로 함께 보여 드립니다."
_REFERENCE_TAIL = "지난달 수치는 참고로만 보여 드립니다."
_REFERENCE_NOTES = {
    "MEASUREMENT_POLICY_CHANGED": (
        "AI 답변을 받는 방식이 바뀌어 지난달 수치는 참고로만 보여 드립니다. "
        "다음 달부터 같은 방식으로 비교해 드립니다."
    ),
}
_COMPARABLE_NOTE = "지난달과 같은 질문을 같은 방식으로 물어본 결과끼리 비교했습니다."
# 같은 종류의 할 일이 여러 줄일 때 문장이 똑같이 반복되지 않게 돌려 쓴다.
_LOST_MOVES = (
    "관련 진료 안내 글을 보강하겠습니다",
    "같은 주제를 다른 각도에서 다룬 글을 더하겠습니다",
    "환자가 실제로 묻는 표현에 맞춰 안내 글을 다듬겠습니다",
)
_UNMENTIONED_MOVES = (
    "이 질문에 답이 되는 진료 안내 글을 더하겠습니다",
    "이 질문을 다루는 글을 새로 써서 공략하겠습니다",
    "관련 키워드를 넓혀 안내 글을 채우겠습니다",
)


def _conclusion(value: float | None, prior: float | None, *, first: bool) -> str:
    """첫 장의 한 문장. 숫자는 칸에 그대로 두고, 문장은 우리의 다음 수를 말한다."""
    if value is None:
        return "이번 달 측정을 다시 진행해, 결과를 확인하는 대로 알려 드리겠습니다."
    if first:
        return "이번 달 결과는 첫 측정 결과로서, 앞으로의 기준점이 됩니다."
    if prior is None:
        return "이번 달 결과를 새 기준점으로 삼겠습니다."
    if value > prior:
        return "지난달보다 AI 답변에 더 자주 언급됐습니다."
    if value < prior:
        return "AI 언급 횟수를 늘리기 위해, 더 넓은 키워드를 공략하겠습니다."
    if value == 0:
        return "AI 답변에 언급되도록, 더 넓은 키워드와 새로운 질문 유형을 공략하겠습니다."
    return "지난달과 비슷하게 꾸준히 언급되고 있습니다. 다음 달에는 새로운 질문 유형까지 공략하겠습니다."


def _denominator(
    *, value: float | None, attempts: object, mentions: object, platforms: str, comparable: bool
) -> str:
    tail = "환자 수가 아니라 AI 답변 횟수입니다."
    if value is None:
        return f"이번 달은 AI 답변을 충분히 확인하지 못해 비율을 계산하지 않았습니다. {tail}"
    if type(attempts) is not int or type(mentions) is not int:
        return f"물어본 횟수 기록이 없어 비율의 근거를 함께 보여 드리지 못했습니다. {tail}"
    asked = (
        f"지난달과 같은 질문으로 {platforms}에 모두 {attempts}번 물었고"
        if comparable
        else f"{platforms}에 환자 질문을 모두 {attempts}번 물었고"
    )
    found = (
        f"그중 {mentions}번 우리 병원이 언급됐습니다."
        if mentions
        else "이번 달 답변에서는 아직 언급되지 않았습니다."
    )
    return f"{asked}, {found} {tail}"


def build_monthly_narrative(
    *,
    kind: ReportKind,
    coverage: MonthlySovPayload | None,
    attribution: ContentAttributionPayload | None,
    citations: CitationSummaryPayload | None,
    works: tuple[PublishedWork, ...],
    current: float | None,
    previous: float | None,
    comparison_reason: str | None,
    shortfall: int,
    protocol_label: str | None = None,
    reference_previous: float | None = None,
) -> MonthlyNarrative:
    data = coverage or {}
    comparison = data.get("comparison") or {}
    comparable = (
        kind == "MONTHLY"
        and comparison.get("status") == "COMPARABLE"
        and comparison.get("reason") == "MATCHED_COHORT"
        and comparison_reason in (None, "MATCHED_COHORT")
        and comparison.get("matched_cell_count", 0) > 0
        and all(
            value is not None and isfinite(value) and 0 <= value <= 100
            for value in (comparison.get("prior_sov_pct"), comparison.get("current_sov_pct"))
        )
    )
    prior = comparison.get("prior_sov_pct") if comparable else None
    value = comparison.get("current_sov_pct") if comparable else current
    reason = comparison.get("reason") or comparison_reason
    first = not comparable and (kind == "INITIAL" or reason == "NO_PRIOR_MANIFEST")
    # 나란히 비교하지 않는 달이라도 원장님이 지난달 받은 수치가 있으면 참고로 보여 준다.
    # 몇 달째 관리해 온 병원에 "기준점"이라고 말하지 않는다(2026-10-02).
    reference = (
        reference_previous
        if (
            kind == "MONTHLY"
            and not comparable
            and not first
            and value is not None
            and reference_previous is not None
            and isfinite(reference_previous)
            and 0 <= reference_previous <= 100
        )
        else None
    )
    conclusion = (
        _REFERENCE_CONCLUSION if reference is not None else _conclusion(value, prior, first=first)
    )
    if comparable:
        note = _COMPARABLE_NOTE
    elif value is None:
        note = "이번 달은 AI 답변을 충분히 확인하지 못해 지난달과 비교하지 않았습니다."
    elif kind == "INITIAL":
        note = _FIRST_MEASUREMENT_NOTE
    else:
        note = _COMPARISON_NOTES.get(reason, _DEFAULT_COMPARISON_NOTE)
    if reference is not None:
        note = _REFERENCE_NOTES.get(reason, f"{note} {_REFERENCE_TAIL}")
    platform_names = "·".join(
        dict.fromkeys(platform_name(row["platform"]) for row in data.get("platforms", []))
    ) or "ChatGPT·Gemini"
    attempts = comparison.get("current_attempts_used") if comparable else data.get("attempts_used")
    mentions = (
        comparison.get("current_mentioned_attempts")
        if comparable
        else data.get("mentioned_attempts")
    )
    denominator = _denominator(
        value=value,
        attempts=attempts,
        mentions=mentions,
        platforms=platform_names,
        comparable=comparable,
    )
    priorities: list[str] = []
    if value is None:
        priorities.append(
            "이번 달 측정을 다시 진행하고, 결과를 확인하는 대로 알려 드리겠습니다."
        )
    lost_rows = (attribution or {}).get("lost_mention_cells", []) if comparable else []
    for index, row in enumerate(lost_rows):
        priorities.append(
            f"“{row['query_text']}” 질문에서 {row['platform_label']} 답변에 다시 언급되도록, "
            f"{_LOST_MOVES[index % len(_LOST_MOVES)]}."
        )
    lost_questions = (
        {row["query_text"] for row in (attribution or {}).get("lost_mention_cells", [])}
        if comparable
        else set()
    )
    unmentioned = [
        row for row in (attribution or {}).get("question_rows", [])
        if row["query_text"] not in lost_questions
        and row.get("current_attempts_used", 0)
        and not row.get("current_mentioned_attempts", 0)
    ]
    for index, row in enumerate(unmentioned):
        priorities.append(
            f"“{row['query_text']}”처럼 환자가 묻는 질문에서도 언급되도록, "
            f"{_UNMENTIONED_MOVES[index % len(_UNMENTIONED_MOVES)]}."
        )
    if not priorities and comparable:
        for row in (attribution or {}).get("new_mention_cells", [])[:2]:
            priorities.append(
                f"“{row['query_text']}” 질문에서 {row['platform_label']} 답변에 새로 "
                "언급되기 시작했습니다. 같은 주제의 글을 이어 써 언급을 넓히겠습니다."
            )
    if not priorities:
        priorities.append(
            "언급 범위를 넓히기 위해, 새로운 질문 유형과 더 넓은 키워드를 공략하겠습니다."
        )
    platforms: list[str] = []
    methods: list[str] = [
        f"측정 방식 버전: {protocol_label or '기록 없음 — 지금 설정으로 대신 적지 않았습니다'}"
    ]
    for row in data.get("platforms", []):
        name = platform_name(row["platform"])
        rate = row.get("mention_rate")
        score = f"{rate:.1f}%" if rate is not None else "측정 못 함"
        platforms.append(
            f"{name} · 질문별로 언급된 비율의 평균 {score} · 모두 {row.get('attempts_used', 0)}번 "
            f"물어 {row.get('mentioned_attempts', 0)}번 언급 · 질문 {row.get('planned_count', 0)}건 중 "
            f"답 확인 {row.get('success_count', 0)}건, 실패 {row.get('failed_count', 0)}건, "
            f"제외 {row.get('excluded_count', 0)}건"
        )
        slots = [
            cell["observation_slots"]
            for cell in data.get("cells", [])
            if cell["platform"] == row["platform"] and "observation_slots" in cell
        ]
        if slots:
            counts = {
                key: sum(int(slot.get(key, 0)) for slot in slots)
                for key in (
                    "planned",
                    "confirmed",
                    "ambiguous",
                    "answer_failed",
                    "judgment_failed",
                    "pending",
                )
            }
            methods.append(
                f"{name}에 물어본 횟수: 계획 {counts['planned']}번 / 답 확인 {counts['confirmed']}번"
                f" / 판단하기 어려움 {counts['ambiguous']}번 / 답변 실패 {counts['answer_failed']}번"
                f" / 판단 실패 {counts['judgment_failed']}번 / 기다리는 중 {counts['pending']}번"
            )
        methods.append(
            f"{name} 답변 모델: {', '.join(row.get('answer_models', [])) or '기록 없음'}"
        )
    adequate = data.get("observation_adequacy") or {}
    if adequate.get("lineage") == "SLOTTED":
        status_label = {
            "COMPLETE": "모두 확인",
            "LIMITED": "일부만 확인",
            "UNAVAILABLE": "확인한 답 없음",
        }.get(adequate.get("status"), "확인 필요")
        methods.append(
            f"같은 질문 반복 확인: 계획 {adequate.get('planned_slots', '기록 없음')}번 / "
            f"답 확인 {adequate.get('confirmed_slots', '기록 없음')}번 · {status_label}"
        )
    elif adequate:
        methods.append(
            "같은 질문 반복 확인 기록: 일부만 남아 있어, 위 횟수를 전체 측정으로 보지 않습니다."
            if adequate.get("lineage") == "MIXED"
            else "같은 질문 반복 확인 기록: 남아 있지 않아, 계획·확인 횟수를 0으로 적지 않았습니다."
        )
    cite = citations or {}
    scope = (
        f"물어본 질문 {cite.get('measured_cell_count', 0)}건 중 {cite.get('cited_cell_count', 0)}건에서 "
        "우리 병원 글이나 안내 페이지가 AI 답변의 출처로 쓰였습니다. 출처로 쓰인 것만으로 "
        "결과가 달라진 이유를 단정하지는 않습니다."
    )
    details: list[str] = []
    for item in cite.get("cited_items", []):
        details.append(
            f"우리 병원 글 · {item.get('title') or '제목 없음'} · 출처로 쓰인 질문 {item['cited_cell_count']}건"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    for item in cite.get("hub_pages", []):
        details.append(
            f"우리 병원 안내 페이지 · {item['label']} · 출처로 쓰인 질문 {item['cited_cell_count']}건"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    if citations is not None and not cite.get("measured_cell_count"):
        scope = (
            "이번 달은 확인한 AI 답변이 없어, 우리 병원 글이 출처로 쓰였는지 알 수 없습니다. "
            "확인하지 못한 것이지 0번이라는 뜻은 아닙니다."
        )
    if citations is None:
        scope = (
            "이번 달은 우리 병원 글이 AI 답변의 출처로 쓰였는지 집계하지 못했습니다. "
            "확인하지 못한 것이지 0번이라는 뜻은 아닙니다."
        )
    return MonthlyNarrative(
        title="첫 측정 보고서" if kind == "INITIAL" else "AI 답변 노출 월간 보고서",
        conclusion=conclusion,
        current=value,
        previous=prior,
        comparison_note=note,
        denominator=denominator,
        priorities=tuple(dict.fromkeys(priorities)),
        works=tuple(sorted(works, key=lambda work: -(work.cited_cells or 0))),
        platform_details=tuple(platforms),
        methods=tuple(methods),
        citation_scope=scope,
        citation_details=tuple(details),
        fulfillment_note=(
            f"남은 {shortfall}편은 안전 기준을 통과하는 대로 이어서 올리겠습니다."
            if shortfall
            else "다음 달에도 계획한 글을 차례로 올리겠습니다."
        ),
        previous_label="첫 측정" if first else "비교 없음",
        reference_previous=reference,
        current_label="측정 못 함",
    )
