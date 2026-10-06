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
            return "AI 답변의 출처로 쓰였는지 아직 확인하지 못했습니다"
        if self.cited_cells == 0:
            return "아직 AI 답변의 출처로 쓰인 적은 없습니다"
        return f"AI 답변 {self.cited_cells}건의 출처로 쓰였습니다"

    @property
    def appendix_label(self) -> str:
        if self.cited_cells is None:
            return "확인 못 함"
        if self.cited_cells == 0:
            return "아직 없음"
        return f"답변 {self.cited_cells}건"


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
    # 측정 방식이 바뀌어 직접 비교하지 않는 달에도 원장님이 지난달 받은 수치는 참고로
    # 보여 준다. `previous`(비교 값)와 섞지 않는다 — 증감 문장·검증은 `previous`만 본다.
    reference_previous: float | None = None
    # 각 장의 서술 문단. 원장이 숫자의 의미를 바로 이해하도록 돕는다.
    result_story: str = ""
    work_story: str = ""
    plan_story: str = ""


# 비교하지 않은 이유를 원장님이 읽을 수 있는 한 문장으로. 한 보고서에는 하나만 나온다.
_NOT_COMPARED = "지난달 결과와 직접 비교하지는 않았습니다."
_FIRST_MEASUREMENT_NOTE = (
    "이번이 첫 측정이라 견줄 지난달 숫자는 아직 없습니다. 다음 달부터 이번 결과와 직접 비교해 보여 드리겠습니다."
)
_COMPARISON_NOTES = {
    "NO_PRIOR_MANIFEST": _FIRST_MEASUREMENT_NOTE,
    "ANSWER_MODEL_CHANGED": f"AI 서비스의 답변 모델이 바뀌어서 {_NOT_COMPARED}",
    "MEASUREMENT_POLICY_CHANGED": (
        f"측정 방식이 바뀌어서 {_NOT_COMPARED} 다음 달부터 다시 견주어 보여 드리겠습니다."
    ),
    "QUERY_TEXT_CHANGED": f"AI에게 묻는 질문 문장이 달라져서 {_NOT_COMPARED}",
    "PLATFORM_COHORT_MISSING": f"두 달 동안 물어본 AI 서비스가 서로 달라서 {_NOT_COMPARED}",
    "INTENT_SNAPSHOT_MISSING": f"지난달 질문 기록이 일부 비어 있어서 {_NOT_COMPARED}",
    "NO_MATCHED_CELLS": f"두 달에 걸쳐 똑같이 물어본 질문이 없어서 {_NOT_COMPARED}",
    "ANSWER_MODEL_UNKNOWN": f"어떤 AI 모델이 답했는지 남은 기록이 없어서 {_NOT_COMPARED}",
    "SAMPLE_SHAPE_CHANGED": f"질문마다 물어본 횟수가 지난달과 달라서 {_NOT_COMPARED}",
}
_DEFAULT_COMPARISON_NOTE = f"지난달과 같은 조건에서 물었는지 확인되지 않아 {_NOT_COMPARED}"
# 지난달 수치를 참고로만 두는 달. 첫 장에서는 한 번(제목 문장)만 말하고, 부록의 읽는 법에서는
# 짧게 되풀이한다 — 같은 말을 세 번 하지 않는다(2026-10 마케팅 검토).
_REFERENCE_CONCLUSIONS = {
    "MEASUREMENT_POLICY_CHANGED": (
        "이번 달부터 측정 방식이 바뀌어, 지난달 수치는 참고용으로만 함께 적었습니다. "
        "다음 달부터는 같은 방식으로 비교해 드리겠습니다."
    ),
}
_REFERENCE_CONCLUSION = (
    "지난달과 같은 조건으로 비교할 수 없어, 지난달 수치는 참고용으로만 함께 적었습니다."
)
_REFERENCE_TAIL = "지난달 수치는 참고용으로만 함께 적었습니다."
_REFERENCE_NOTES = {
    "MEASUREMENT_POLICY_CHANGED": (
        "이번 달부터 측정 방식이 바뀌어 지난달 수치는 참고용으로만 적었습니다."
    ),
}
_COMPARABLE_NOTE = "지난달과 같은 질문을 같은 방식으로 물어 두 달의 결과를 비교했습니다."
# 다음 달 할 일 한 줄은 `“질문”: 할 일.` 꼴이다. 인용한 질문이 곧 목표 질문이므로
# "~처럼 … 질문에서도" 같은 틀 문장을 앞에 붙이지 않는다(예시가 따로 있는 듯 읽히고,
# "에서도"는 이미 언급되는 질문을 전제한다). 같은 종류가 여러 줄이면 할 일을 돌려 쓴다.
_LOST_MOVES = (
    "{platform} 답변에 다시 언급되도록 관련 진료 안내 글을 보강합니다",
    "{platform} 답변에 다시 언급되도록 같은 주제를 다른 각도로 다룬 글을 더합니다",
    "{platform} 답변에 다시 언급되도록 환자분들이 쓰는 표현에 맞춰 글을 다듬습니다",
)
_UNMENTIONED_MOVES = (
    "이 질문에 바로 답하는 진료 안내 글을 준비합니다",
    "이 질문을 직접 다루는 글을 새로 씁니다",
    "연관 키워드를 넓혀 기존 글을 보강합니다",
)
# 부록 '그 밖에 살펴볼 질문'의 머리글. 줄마다 같은 앞부분을 되풀이하지 않고 여기서 한 번 말한다.
PRIORITY_APPENDIX_LEAD = "아래 질문에서도 우리 병원이 답변에 나오도록 글 작업을 이어 가겠습니다."


def _conclusion(value: float | None, prior: float | None, *, first: bool) -> str:
    """첫 장의 한 문장. 숫자는 칸에 그대로 두고, 문장은 우리의 다음 수를 말한다."""
    if value is None:
        return "측정을 다시 진행한 뒤, 결과가 확인되는 대로 바로 알려 드리겠습니다."
    if first:
        return "첫 측정 결과입니다. 앞으로 이 숫자와 견주며 변화를 살피겠습니다."
    if prior is None:
        return "이번 결과를 새 기준점으로 두고, 다음 달부터 흐름을 짚어 드리겠습니다."
    if value > prior:
        return "AI 답변이 지난달보다 우리 병원을 더 자주 언급했습니다."
    if value < prior:
        return "다음 달에는 더 넓은 키워드로 AI 답변 속 언급을 다시 늘려 가겠습니다."
    if value == 0:
        return "AI 답변에서 우리 병원 이름이 보이도록, 키워드를 넓히고 새 질문 유형까지 다뤄 보겠습니다."
    return "지난달만큼 꾸준히 언급되고 있습니다. 다음 달에는 새로운 질문 유형으로 범위를 넓혀 보겠습니다."


def _denominator(
    *, value: float | None, attempts: object, mentions: object, platforms: str, comparable: bool
) -> str:
    tail = "환자 수가 아닌 AI 답변 횟수 기준입니다."
    if value is None:
        return f"확인된 AI 답변이 충분하지 않아 이번 달 비율은 계산하지 않았습니다. {tail}"
    if type(attempts) is not int or type(mentions) is not int:
        return f"물어본 횟수가 기록에 남지 않아, 비율의 근거는 함께 싣지 못했습니다. {tail}"
    asked = (
        f"지난달과 같은 질문을 {platforms}에 총 {attempts}회 물었고"
        if comparable
        else f"{platforms}에 환자 질문을 총 {attempts}회 물었고"
    )
    found = (
        f"{mentions}회의 답변에 우리 병원이 언급됐습니다."
        if mentions
        else "아직은 답변에 우리 병원이 언급되지 않았습니다."
    )
    return f"{asked}, {found} {tail}"


def _result_story(value, prior, first, attempts, mentions, new_mention_count=0):
    """1장의 서술 문단. 숫자가 무슨 일인지 두 문장으로 풀어 쓴다."""
    if value is None:
        return "이번 달은 AI 답변을 충분히 확인하지 못해 비율을 내지 못했습니다."
    new_part = f" 새로 언급된 질문 {new_mention_count}개 포함." if new_mention_count else ""
    if first:
        return f"저희가 처음 측정한 결과입니다.{new_part}"
    if prior is not None:
        if value > prior:
            return f"지난달 {prior:.1f}%에서 {value:.1f}%로 올랐습니다.{new_part}"
        if value < prior:
            return f"지난달 {prior:.1f}%에서 {value:.1f}%로 내려갔습니다. 언급이 줄은 질문부터 계획에 넣었습니다."
        return f"지난달과 같은 {value:.1f}%를 유지했습니다.{new_part}"
    return f"이번 달 언급 비율은 {value:.1f}%입니다.{new_part}"


def _work_story(works):
    """2장의 서술 문단. 이번 달에 한 일을 풀어 쓴다."""
    if not works:
        return "이번 달에 올린 글은 없습니다. 다음 달에는 계획한 글을 올리고 결과를 알려 드리겠습니다."
    cited = [w for w in works if w.cited_cells]
    if cited:
        return (
            f"이번 달 글 {len(works)}편을 올렸고, 그중 {len(cited)}편이 AI 답변의 출처로 쓰였습니다. "
            "글이 실제 답변을 만드는 데 쓰이고 있다는 뜻입니다."
        )
    return (
        f"이번 달 글 {len(works)}편을 올렸습니다. "
        "다음 달에는 AI 답변이 참고할 만한 수준으로 글을 다듬어 올리겠습니다."
    )


def _question_totals(rows) -> dict[str, tuple[int, int]]:
    """질문 문장별 (이번 달 물어본 횟수, 언급된 횟수). 문장이 같은 행은 하나로 합친다."""
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        text = str(row.get("query_text") or "").strip()
        if not text:
            continue
        attempts_used, mentioned_attempts = totals.get(text, (0, 0))
        totals[text] = (
            attempts_used + int(row.get("current_attempts_used") or 0),
            mentioned_attempts + int(row.get("current_mentioned_attempts") or 0),
        )
    return totals


def _plan_story(priorities):
    """3장의 서술 문단. 다음 달 계획의 방향을 풀어 쓴다."""
    if not priorities:
        return ""
    # 3쪽에는 앞의 세 줄까지만 싣는다. 실제로 싣는 줄 수와 문장이 어긋나지 않게 센다.
    count = ("한", "두", "세")[min(len(priorities), 3) - 1]
    return f"다음 달에는 아래 {count} 가지를 먼저 챙기고, 같은 질문으로 다시 물어 결과를 보고드리겠습니다."


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
    # 직접 비교하지 않는 달이라도 원장님이 지난달 받은 수치가 있으면 참고로 보여 준다.
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
        _REFERENCE_CONCLUSIONS.get(reason, _REFERENCE_CONCLUSION)
        if reference is not None
        else _conclusion(value, prior, first=first)
    )
    if comparable:
        note = _COMPARABLE_NOTE
    elif value is None:
        note = "충분한 AI 답변을 확인하지 못해 이번 달은 지난달과 비교하지 않았습니다."
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
            "이번 달 측정을 다시 진행하고, 결과를 받는 대로 정리해 전해 드리겠습니다."
        )
    # 한 질문에는 할 일 하나. 질문 행은 측정 키마다 따로 올 수 있어(같은 문장이 둘 이상),
    # 부록 표·첫 장 칸과 같이 문장 기준으로 합친 뒤에 고른다. 합치지 않으면 다른 키에서
    # 이미 언급된 질문이 '언급 안 된 질문'으로 계획에 오르고, 같은 질문이 두 번 나온다.
    question_totals = _question_totals((attribution or {}).get("question_rows", []))
    lost_by_question: dict[str, list[str]] = {}
    if comparable:
        for row in (attribution or {}).get("lost_mention_cells", []):
            text = str(row.get("query_text") or "").strip()
            if text:
                lost_by_question.setdefault(text, []).append(row["platform_label"])
    for index, (text, labels) in enumerate(lost_by_question.items()):
        move = _LOST_MOVES[index % len(_LOST_MOVES)].format(
            platform="·".join(dict.fromkeys(labels))
        )
        priorities.append(f"“{text}”: {move}.")
    unmentioned = [
        text for text, (attempts_used, mentioned_attempts) in question_totals.items()
        if text not in lost_by_question and attempts_used and not mentioned_attempts
    ]
    for index, text in enumerate(unmentioned):
        priorities.append(f"“{text}”: {_UNMENTIONED_MOVES[index % len(_UNMENTIONED_MOVES)]}.")
    if not priorities and comparable:
        for row in (attribution or {}).get("new_mention_cells", [])[:2]:
            priorities.append(
                f"“{row['query_text']}” 질문에서 {row['platform_label']} 답변에 새로 "
                "언급되기 시작했습니다. 같은 주제의 글을 이어 쓰며 이 흐름을 넓혀 가겠습니다."
            )
    if not priorities:
        priorities.append(
            "새로운 질문 유형과 더 넓은 키워드로, 언급되는 범위를 한 걸음 더 넓히겠습니다."
        )
    platforms: list[str] = []
    methods: list[str] = [
        f"측정 방식 버전: {protocol_label or '기록 없음 (현재 설정값으로 채우지 않았습니다)'}"
    ]
    for row in data.get("platforms", []):
        name = platform_name(row["platform"])
        rate = row.get("mention_rate")
        score = f"{rate:.1f}%" if rate is not None else "측정 못 함"
        platforms.append(
            f"{name} · 질문별로 언급된 비율의 평균 {score} · {row.get('attempts_used', 0)}번 "
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
            "같은 질문 반복 확인 기록: 일부만 남아 있어 위 횟수를 전체 측정으로 보지는 않습니다."
            if adequate.get("lineage") == "MIXED"
            else "같은 질문 반복 확인 기록: 남아 있지 않아 계획·확인 횟수를 0으로 적지 않았습니다."
        )
    cite = citations or {}
    # 출처 집계의 단위는 질문×AI 서비스의 답변 한 건이다. '질문 30건'이라 쓰면 질문 15개와
    # 헷갈리므로 답변으로 센다. 0건이면 출처로 쓰였다는 사실을 전제한 단서를 붙이지 않는다.
    answers = cite.get("measured_cell_count", 0)
    cited_answers = cite.get("cited_cell_count", 0)
    scope = (
        f"{platform_names} 답변 {answers}건을 확인했고, 그중 {cited_answers}건에서 "
        "우리 병원 글이나 안내 페이지가 출처로 쓰였습니다. "
        "다만 출처로 쓰였다는 사실만으로 결과의 원인을 판단하지는 않습니다."
        if cited_answers
        else f"{platform_names} 답변 {answers}건을 확인했고, "
        "우리 병원 글이나 안내 페이지가 출처로 쓰인 답변은 없었습니다."
    )
    details: list[str] = []
    for item in cite.get("cited_items", []):
        details.append(
            f"우리 병원 글 · {item.get('title') or '제목 없음'} · 출처로 쓰인 답변 {item['cited_cell_count']}건"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    for item in cite.get("hub_pages", []):
        details.append(
            f"우리 병원 안내 페이지 · {item['label']} · 출처로 쓰인 답변 {item['cited_cell_count']}건"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    if citations is not None and not cite.get("measured_cell_count"):
        scope = (
            "확인된 AI 답변이 없어 우리 병원 글이 출처로 쓰였는지 알 수 없었습니다. "
            "확인하지 못했을 뿐, 0번이라는 뜻은 아닙니다."
        )
    if citations is None:
        scope = (
            "우리 병원 글이 AI 답변의 출처로 쓰였는지는 이번 달 집계하지 못했습니다. "
            "확인하지 못했을 뿐, 0번이라는 뜻은 아닙니다."
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
            f"남은 {shortfall}편도 검수가 끝나는 대로 올려 드리겠습니다."
            if shortfall
            else "다음 달에도 계획한 글을 일정에 맞춰 올리겠습니다."
        ),
        previous_label="첫 측정" if first else "비교 없음",
        reference_previous=reference,
        current_label="측정 못 함",
        result_story=_result_story(
            value, prior, first, attempts, mentions,
            new_mention_count=len((attribution or {}).get("new_mention_cells", [])),
        ),
        work_story=_work_story(tuple(sorted(works, key=lambda work: -(work.cited_cells or 0)))),
        plan_story=_plan_story(tuple(dict.fromkeys(priorities))),
    )
