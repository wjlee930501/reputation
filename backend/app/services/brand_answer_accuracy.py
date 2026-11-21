"""병원명 질문(측정 키워드 그룹 A) 답변의 병원 정보 정확도 — 결정적 규칙, 모델 호출 없음.

"{병원명} 어때?", "{병원명} 진료 시간 알려줘" 같은 질문은 병원명이 질문에 있어 노출은 거의 늘
나온다. 이 그룹의 성과는 **AI가 우리 병원 정보를 맞게 아는가**다(지침 3-2 A). 그래서 보고서를
만들 때 그 달 측정표의 A 칸 확정 답변 원문을 병원 정보와 맞춘다(설계 §5).

- 주소: 병원 도로명 주소의 도로명 + 건물번호가 답변에 있으면 일치, 다른 도로명 주소만 있으면 다름.
- 진료 시간: 답변의 시각이 모두 병원 진료 시간표의 시각에 있으면 일치, 하나라도 없으면 다름.
- 진료 항목: 등록 진료 항목·키워드 이름이 하나라도 있으면 일치(빠진 항목은 '다름'으로 세지 않는다).
- 셋 다 답변이 그 정보를 말하지 않으면 '언급 없음'이다. 병원 쪽 기준값이 없으면 그 항목은 보지 않는다.

답변에 다른 병원 정보가 섞이는 것을 줄이려고, 병원명이 나오면 그 뒤 구간만 본다(이름이 없으면
질문이 우리 병원을 물었으므로 답변 전체를 본다). 측정 판정 정책(`JUDGE_POLICY_MATERIAL`)과 무관해
측정 기준 지문을 바꾸지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

FieldVerdict = Literal["MATCH", "MISMATCH", "ABSENT"]
FIELDS: Final[tuple[str, ...]] = ("address", "hours", "treatments")
FIELD_LABELS: Final[dict[str, str]] = {
    "address": "주소",
    "hours": "진료 시간",
    "treatments": "진료 항목",
}
# 병원명이 나온 뒤 이만큼의 글자를 그 병원 이야기로 본다.
_WINDOW_CHARS: Final = 400
_EXCERPT_CHARS: Final = 160
_MAX_EXCERPTS: Final = 3

_ROAD_RE = re.compile(r"([가-힣A-Za-z0-9]+(?:대로|로|길))\s*(\d+(?:-\d+)?)(?![\d.])")
_CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-4])\s*[:：]\s*([0-5]\d)(?!\d)")
_KOREAN_TIME_RE = re.compile(
    r"(오전|오후|낮|저녁|밤)?\s*(?<!\d)(2[0-4]|1\d|[1-9])\s*시(?!간)(?:\s*([0-5]?\d)\s*분|\s*(반))?"
)
_SPACE_RE = re.compile(r"\s+")


def _squash(value: object) -> str:
    return _SPACE_RE.sub("", str(value or "")).lower()


@dataclass(frozen=True, slots=True)
class BrandFacts:
    name: str
    road: tuple[str, str] | None = None
    hours_minutes: frozenset[int] = frozenset()
    treatment_names: tuple[str, ...] = ()

    @classmethod
    def from_hospital(
        cls,
        *,
        name: str,
        address: str | None,
        business_hours: Any,
        treatments: Iterable[Any] = (),
        keywords: Iterable[Any] = (),
    ) -> BrandFacts:
        road = None
        match = _ROAD_RE.search(str(address or ""))
        if match:
            road = (match.group(1), match.group(2))
        minutes: set[int] = set()
        if isinstance(business_hours, Mapping):
            for value in business_hours.values():
                if isinstance(value, str):
                    minutes.update(_times(value))
        names: list[str] = []
        for item in (*tuple(treatments or ()), *tuple(keywords or ())):
            value = item.get("name") if isinstance(item, Mapping) else item
            text = str(value or "").strip()
            if len(_squash(text)) >= 2 and text not in names:
                names.append(text)
        return cls(
            name=str(name or "").strip(),
            road=road,
            hours_minutes=frozenset(minutes),
            treatment_names=tuple(names),
        )


def _times(text: str) -> set[int]:
    """글 안의 시각을 하루 중 분으로. 오후·저녁·밤 표지가 있으면 12시간을 더한다."""

    found: set[int] = set()
    for hour, minute in _CLOCK_RE.findall(text):
        found.add(int(hour) * 60 + int(minute))
    for meridiem, hour, minute, half in _KOREAN_TIME_RE.findall(text):
        value = int(hour)
        if meridiem in ("오후", "저녁", "밤") and value < 12:
            value += 12
        minutes = 30 if half else int(minute or 0)
        found.add(value * 60 + minutes)
    return found


def _windows(facts: BrandFacts, text: str) -> list[str]:
    if not facts.name:
        return [text]
    windows = []
    start = 0
    while True:
        index = text.find(facts.name, start)
        if index < 0:
            break
        windows.append(text[index : index + len(facts.name) + _WINDOW_CHARS])
        start = index + len(facts.name)
    return windows or [text]


def _address(facts: BrandFacts, scope: str) -> FieldVerdict | None:
    if facts.road is None:
        return None
    roads = {(road, number) for road, number in _ROAD_RE.findall(scope)}
    if not roads:
        return "ABSENT"
    own_road, own_number = facts.road
    if any(
        _squash(road) == _squash(own_road) and number == own_number for road, number in roads
    ):
        return "MATCH"
    return "MISMATCH"


def _hours(facts: BrandFacts, scope: str) -> FieldVerdict | None:
    if not facts.hours_minutes:
        return None
    mentioned = _times(scope)
    if not mentioned:
        return "ABSENT"
    return "MATCH" if mentioned <= facts.hours_minutes else "MISMATCH"


def _treatments(facts: BrandFacts, scope: str) -> FieldVerdict | None:
    if not facts.treatment_names:
        return None
    squashed = _squash(scope)
    return (
        "MATCH"
        if any(_squash(name) in squashed for name in facts.treatment_names)
        else "ABSENT"
    )


def check_answer(facts: BrandFacts, text: str) -> dict[str, FieldVerdict]:
    """답변 하나의 항목별 판정. 병원 쪽 기준값이 없는 항목은 결과에 없다."""

    scope = "\n".join(_windows(facts, str(text or "")))
    verdicts: dict[str, FieldVerdict] = {}
    for name, check in (("address", _address), ("hours", _hours), ("treatments", _treatments)):
        verdict = check(facts, scope)
        if verdict is not None:
            verdicts[name] = verdict
    return verdicts


@dataclass
class _FieldTally:
    match: int = 0
    mismatch: int = 0
    absent: int = 0
    excerpts: list[str] = field(default_factory=list)


def summarize(facts: BrandFacts, answers: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """(질문 문구, 답변 원문) 목록의 항목별 집계. 보고서 `sov_summary.brand_accuracy`에 저장한다.

    `fields[항목]`은 일치·다름·언급 없음 답변 수, `checked`는 그 항목을 말한 답변 수(일치 + 다름)다.
    '다름' 발췌는 내부 화면용이다(원장 보고서에 원문을 싣지 않는다).
    """

    tallies = {name: _FieldTally() for name in FIELDS}
    answer_count = 0
    for question, text in answers:
        answer_count += 1
        for name, verdict in check_answer(facts, text).items():
            tally = tallies[name]
            if verdict == "MATCH":
                tally.match += 1
            elif verdict == "MISMATCH":
                tally.mismatch += 1
                if len(tally.excerpts) < _MAX_EXCERPTS:
                    snippet = _SPACE_RE.sub(" ", "\n".join(_windows(facts, text)))[:_EXCERPT_CHARS]
                    tally.excerpts.append(f"{question} → {snippet.strip()}")
            else:
                tally.absent += 1
    return {
        "version": 1,
        "answers": answer_count,
        "fields": {
            name: {
                "label": FIELD_LABELS[name],
                "match": tally.match,
                "mismatch": tally.mismatch,
                "absent": tally.absent,
                "checked": tally.match + tally.mismatch,
                "mismatch_excerpts": list(tally.excerpts),
            }
            for name, tally in tallies.items()
            if tally.match or tally.mismatch or tally.absent
        },
    }
