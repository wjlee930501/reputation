"""생성 글이 작가에게 준 운영 기준·병원 자료 문장을 그대로 옮겼는지 보는 결정적 검사.

결정 대기 15번 A(2026-11 어드민 개편, 11-24 회의 결정 전 draft): 필수 문구(must_use_messages)는
원장 문장을 원문 그대로 넣는다(`must_use_verbatim`). 그 밖의 설문·자료 문장은 뜻만 살려 글의
합니다체로 다시 써야 한다 — 마케팅이 본 「설문 원문 혼입·문체 불일치」의 다른 절반이다.

작가가 베낄 수 있는 것은 프롬프트에 실린 글뿐이다. 근거 노트 원문은 프롬프트에 실리지 않고
(노트 id만 간다), 설문 문장은 운영 기준의 서술 칸(포지셔닝·문체·환자 약속·원칙·진료 서술의
환자 설명·선호 메시지 — 결정적 폴백 판은 노트 원문을 그대로 싣는다)과 병원 정보의 진료 철학으로
들어간다. 그래서 그 칸들만 원문 집합으로 쓴다. 공급자·DB를 부르지 않는다.

판정: 공백·구두점·따옴표를 뺀 글자열에서 원문 문장과 본문 문장이 `MIN_COPIED_RUN`자 이상
연달아 같으면 옮긴 문장이다. 필수 문구를 담은 본문 문장과 필수 문구와 같은 원문 문장은 보지
않는다 — 원문 그대로가 규칙인 문장이다. 병원명·원장명·짧은 의학 표현은 길이 하한으로 걸리지 않는다.

이 검사는 **발행을 막지 않는다**. 생성 검수 세션이 중복 주제와 같은 문체 보완 예산 한 번으로
다시 쓰게 하고, 그래도 남으면 `essence_check_summary`에 기록만 남긴다(사고·Slack 없음).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.services.must_use_verbatim import sentence_holds_must_use

#: 원문과 본문이 연달아 같아야 하는 최소 글자 수(공백·구두점 제외). 한 절 정도다 —
#: 병원명·원장명·「정확한 진단이 필요합니다」 같은 흔한 표현은 이보다 짧다.
MIN_COPIED_RUN = 24
#: 기록·재작성 지적에 싣는 최대 문장 수와 한 문장 앞부분 길이.
MAX_REPORTED = 5
EXCERPT_CHARS = 40

SOURCE_COPY_SUMMARY_KEY = "source_copy_findings"

SOURCE_COPY_FINDING_PREFIX = "[자료 문장 그대로 옮김]"
_REMEDIATION_INSTRUCTION = (
    f"{SOURCE_COPY_FINDING_PREFIX} 아래 본문 문장은 운영 기준·병원 자료(원장 설문 등)의 문장을 그대로 "
    "옮겼습니다. 필수 문구(must_use_messages)가 아닌 자료 문장은 뜻만 살려 이 글의 합니다체 문장으로 "
    "다시 쓰세요. 새 사실·수치·효과·장비·경력을 더하지 말고, 필수 문구는 지금처럼 원문 그대로 둡니다: "
)

# 본문 문장 경계: 종결 부호(닫는 따옴표 포함) 뒤의 공백, 또는 줄바꿈.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。])[\"'”’」』)]*\s+|\n+")
# 줄 머리의 마크다운 구조 기호(제목·목록·인용·표 칸 구분).
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|[-+*]\s+|>\s*|\d+[.)]\s+)+")
# 비교에서 남기는 글자: 한글·영문·숫자. 나머지(공백·구두점·따옴표·마크다운)는 지운다.
_KEEP = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㆎ]")


@dataclass(frozen=True, slots=True)
class SourceCopy:
    """옮긴 본문 문장 하나와 그 원문이 실린 칸."""

    sentence: str
    field: str


def _normalize(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return "".join(_KEEP.findall(value))


def _split_sentences(text: object) -> list[str]:
    sentences: list[str] = []
    for line in str(text or "").splitlines():
        line = _LINE_MARKER.sub("", line).strip()
        if not line:
            continue
        for part in _SENTENCE_BOUNDARY.split(line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        text = value.get("text")
        return [text] if isinstance(text, str) and text.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def source_texts(hospital: object, philosophy: object | None) -> list[tuple[str, str]]:
    """작가 프롬프트에 실리는 서술 원문 (칸 이름, 글) 목록.

    안전 문구(avoid·medical_ad_risk_rules·cautions)와 문체 지시(tone_guidelines)는 넣지 않는다 —
    안전 문장은 그대로 옮겨도 되는 문장이고 지시는 본문에 들어갈 문장이 아니다. 약력(`director_career`)은
    자격명을 표기 그대로 쓰라는 규칙이 따로 있다.
    """

    texts: list[tuple[str, str]] = []
    for text in _strings(getattr(hospital, "director_philosophy", None)):
        texts.append(("director_philosophy", text))
    if philosophy is None:
        return texts
    for field in ("positioning_statement", "doctor_voice", "patient_promise"):
        for text in _strings(getattr(philosophy, field, None)):
            texts.append((field, text))
    for field in ("content_principles", "prefer_messages"):
        for text in _strings(getattr(philosophy, field, None)):
            texts.append((field, text))
    narratives = getattr(philosophy, "treatment_narratives", None)
    for entry in narratives if isinstance(narratives, (list, tuple)) else ():
        if isinstance(entry, dict):
            for text in _strings(entry.get("patient_language")):
                texts.append(("treatment_narratives", text))
    return texts


def _source_runs(
    texts: Iterable[tuple[str, str]], must_use_keys: set[str]
) -> dict[str, str]:
    """원문 문장의 `MIN_COPIED_RUN`자 조각 → 칸 이름. 필수 문구와 같은 문장은 빼고 센다."""

    runs: dict[str, str] = {}
    for field, text in texts:
        for sentence in _split_sentences(text):
            key = _normalize(sentence)
            if len(key) < MIN_COPIED_RUN or any(
                key in must or must in key for must in must_use_keys if must
            ):
                continue
            for start in range(len(key) - MIN_COPIED_RUN + 1):
                runs.setdefault(key[start : start + MIN_COPIED_RUN], field)
    return runs


def copied_source_sentences(
    body: object,
    hospital: object,
    philosophy: object | None,
    must_use_messages: Iterable[str] = (),
) -> list[SourceCopy]:
    """본문 문장 중 운영 기준·병원 자료 문장을 그대로 옮긴 것. 필수 문구 문장은 보지 않는다."""

    messages = [str(message) for message in must_use_messages if str(message).strip()]
    runs = _source_runs(source_texts(hospital, philosophy), {_normalize(m) for m in messages})
    if not runs:
        return []
    found: list[SourceCopy] = []
    for sentence in _split_sentences(body):
        if messages and sentence_holds_must_use(sentence, messages):
            continue
        key = _normalize(sentence)
        for start in range(len(key) - MIN_COPIED_RUN + 1):
            field = runs.get(key[start : start + MIN_COPIED_RUN])
            if field is not None:
                found.append(SourceCopy(sentence=sentence, field=field))
                break
        if len(found) >= MAX_REPORTED:
            break
    return found


def _excerpt(sentence: str) -> str:
    return sentence if len(sentence) <= EXCERPT_CHARS else f"{sentence[:EXCERPT_CHARS]}…"


def source_copy_remediation(copies: list[SourceCopy]) -> list[str]:
    """한 번의 문체 보완 재작성에 싣는 지적 한 줄(없으면 빈 목록)."""

    if not copies:
        return []
    quoted = ", ".join(f"「{_excerpt(copy.sentence)}」" for copy in copies[:3])
    return [f"{_REMEDIATION_INSTRUCTION}{quoted}"]


def source_copy_record(copies: list[SourceCopy]) -> list[dict[str, str]]:
    """재작성 뒤에도 남은 문장의 기록(요약 저장용, 발행을 막지 않는다)."""

    return [
        {"sentence": _excerpt(copy.sentence), "field": copy.field}
        for copy in copies[:MAX_REPORTED]
    ]
