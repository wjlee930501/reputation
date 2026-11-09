"""병원별 콘텐츠 주력 주제 — 마케터가 정한 방향으로 다음 글의 주제를 기운다.

원장님이 "11~12월은 내시경 위주로"처럼 방향을 주면 마케터가 Admin 콘텐츠 화면에서 주력 주제와
기간·비중을 저장한다(`hospitals.content_focus_topics`). 글 주제를 고르는 곳(`content_target_planner`)이
이 설정을 읽어, 그 달 주력 주제 글이 목표 편수에 못 미쳤으면 주력 주제와 맞는 측정 질문을 먼저 고른다.

경계:
- 새 측정 질문을 만들지 않는다 — 측정 기준(질문 세트 지문)을 바꾸지 않는다. 이미 있는 질문 중에서 고른다.
- 이미 주제가 정해진 글(예정 질문이 배정됐거나 생성을 시도한 슬롯)은 다시 고르지 않는다.
- 주력 주제는 글의 사실·문구가 아니다. 작가 프롬프트에 넣지 않으므로 의료광고 판정과 무관하다.

저장 모양(판 1)::

    {"version": 1, "topics": ["내시경"], "share": 50, "from_month": "2026-11", "until_month": "2026-12"}

0054가 노원탑에 남긴 문자열 목록(판 없음)은 쓰이지 않던 옛 값이라 꺼진 설정으로 읽는다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

FOCUS_VERSION = 1
MAX_TOPICS = 5
MIN_TOPIC_CHARS = 2
MAX_TOPIC_CHARS = 20
SHARE_CHOICES = (30, 50, 70, 100)
DEFAULT_SHARE = 50

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
# 주제 끝의 일반어는 대조에서 뗀다 — "내시경 검사"는 "위내시경" 질문과 맞아야 한다.
_GENERIC_TAIL = ("관련 키워드", "키워드", "관련", "검사", "진료", "치료", "상담")
# 지역어 끝의 행정 단위. "하남"과 "하남시"를 같은 곳으로 본다.
_REGION_SUFFIX = re.compile(r"(특별시|광역시|특별자치시|특별자치도|시|군|구|동|읍|면)$")
_SPACE = re.compile(r"\s+")
_ALLOWED_TOPIC = re.compile(r"^[0-9A-Za-z가-힣·/()+&\- ]+$")


class ContentFocusError(ValueError):
    """마케터 입력이 저장할 수 없는 모양이다. message는 화면에 그대로 보인다."""


@dataclass(frozen=True, slots=True)
class ContentFocus:
    topics: tuple[str, ...] = ()
    share: int = DEFAULT_SHARE
    from_month: str | None = None
    until_month: str | None = None
    legacy_topics: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.topics)

    def applies_to(self, month: str) -> bool:
        """`YYYY-MM` 달에 이 설정이 켜져 있는가."""
        if not self.topics:
            return False
        if self.from_month and month < self.from_month:
            return False
        if self.until_month and month > self.until_month:
            return False
        return True

    def active_topics(self, month: str) -> tuple[str, ...]:
        return self.topics if self.applies_to(month) else ()

    def to_storage(self) -> dict[str, Any] | list:
        if not self.topics:
            return []
        return {
            "version": FOCUS_VERSION,
            "topics": list(self.topics),
            "share": self.share,
            "from_month": self.from_month,
            "until_month": self.until_month,
        }


def _clean_topic(value: object) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _month_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text if _MONTH.match(text) else None


def parse_content_focus(raw: object) -> ContentFocus:
    """저장 값 → 설정. 깨졌거나 옛 모양이면 꺼진 설정이다(예외를 내지 않는다)."""
    if isinstance(raw, list):
        legacy = tuple(t for t in (_clean_topic(v) for v in raw if isinstance(v, str)) if t)
        return ContentFocus(legacy_topics=legacy)
    if not isinstance(raw, dict) or raw.get("version") != FOCUS_VERSION:
        return ContentFocus()
    topics: list[str] = []
    for value in raw.get("topics") or []:
        topic = _clean_topic(value)
        if topic and topic not in topics:
            topics.append(topic)
    share = raw.get("share")
    if share not in SHARE_CHOICES:
        share = DEFAULT_SHARE
    from_month = _month_or_none(raw.get("from_month"))
    until_month = _month_or_none(raw.get("until_month"))
    if from_month and until_month and from_month > until_month:
        return ContentFocus()
    return ContentFocus(
        topics=tuple(topics[:MAX_TOPICS]),
        share=int(share),
        from_month=from_month,
        until_month=until_month,
    )


def validate_content_focus(
    *,
    topics: Iterable[object],
    share: object,
    from_month: object,
    until_month: object,
) -> ContentFocus:
    """마케터 입력 검사. 문제가 있으면 화면에 보일 문장으로 `ContentFocusError`를 낸다."""
    cleaned: list[str] = []
    for value in topics or []:
        topic = _clean_topic(value)
        if not topic or topic in cleaned:
            continue
        if not (MIN_TOPIC_CHARS <= len(topic) <= MAX_TOPIC_CHARS):
            raise ContentFocusError(
                f"주제 '{topic}'은 {MIN_TOPIC_CHARS}~{MAX_TOPIC_CHARS}자로 적어 주세요."
            )
        if not _ALLOWED_TOPIC.match(topic):
            raise ContentFocusError(f"주제 '{topic}'에 쓸 수 없는 기호가 있습니다.")
        cleaned.append(topic)
    if len(cleaned) > MAX_TOPICS:
        raise ContentFocusError(f"주력 주제는 {MAX_TOPICS}개까지 정할 수 있습니다.")
    if share not in SHARE_CHOICES:
        raise ContentFocusError("비중은 30%·50%·70%·100% 중에서 골라 주세요.")
    start = None
    end = None
    if from_month not in (None, ""):
        start = _month_or_none(from_month)
        if start is None:
            raise ContentFocusError("시작 달을 다시 골라 주세요.")
    if until_month not in (None, ""):
        end = _month_or_none(until_month)
        if end is None:
            raise ContentFocusError("끝 달을 다시 골라 주세요.")
    if start and end and start > end:
        raise ContentFocusError("끝 달이 시작 달보다 앞설 수 없습니다.")
    return ContentFocus(topics=tuple(cleaned), share=int(share), from_month=start, until_month=end)


def _match_key(text: object) -> str:
    return _SPACE.sub("", str(text or "")).lower()


def topic_match_key(topic: str) -> str:
    """대조에 쓰는 주제의 핵심 낱말. 끝의 일반어("검사"·"진료" 등)를 뗀다."""
    key = _match_key(topic)
    changed = True
    while changed:
        changed = False
        for tail in _GENERIC_TAIL:
            tail_key = _match_key(tail)
            if key.endswith(tail_key) and len(key) - len(tail_key) >= MIN_TOPIC_CHARS:
                key = key[: -len(tail_key)]
                changed = True
    return key


def _target_texts(target: Any) -> list[str]:
    texts = [
        getattr(target, "name", None),
        getattr(target, "treatment", None),
        getattr(target, "condition_or_symptom", None),
        getattr(target, "specialty", None),
    ]
    for variant in getattr(target, "variants", None) or []:
        if getattr(variant, "is_active", True):
            texts.append(getattr(variant, "query_text", None))
    return [str(text) for text in texts if text]


def target_matches_topic(target: Any, topic: str) -> bool:
    key = topic_match_key(topic)
    if len(key) < MIN_TOPIC_CHARS:
        return False
    return any(key in _match_key(text) for text in _target_texts(target))


def matching_topic(target: Any, topics: Iterable[str]) -> str | None:
    """측정 질문이 맞는 첫 주력 주제. 없으면 None."""
    if target is None:
        return None
    for topic in topics:
        if target_matches_topic(target, topic):
            return topic
    return None


def focus_quota(share: int, month_total: int) -> int:
    """그 달 글 `month_total`편 중 주력 주제로 쓸 목표 편수."""
    if month_total <= 0:
        return 0
    return min(month_total, math.ceil(month_total * share / 100))


def _region_key(text: object) -> str:
    key = _match_key(text)
    stripped = _REGION_SUFFIX.sub("", key)
    return stripped if len(stripped) >= 2 else key


def target_subject_is_region(target: Any, hospital: Any = None) -> bool:
    """질문의 진료 항목 자리에 지역명이 들어간 엉성한 질문인가.

    예: "하남 진료를 받으려는데 하남시 어느 병원…"(진료 항목 = '하남'), "송파 치료 비용…".
    측정 질문 생성 때 사전에 없는 말이 진료 항목 자리에 들어가 생긴다. 이런 질문으로는 글을
    쓸 수 없으므로 글 주제 후보에서 뺀다(측정 세트는 건드리지 않는다).
    """
    subject = getattr(target, "treatment", None) or getattr(target, "condition_or_symptom", None)
    if not subject:
        return False
    subject_word = topic_match_key(str(subject))
    # 템플릿이 진료 항목 자리에 그 말을 넣은 모양("하남 진료…", "송파 치료…")일 때만 본다.
    # "초산과 경산의 분만 차이"처럼 같은 글자의 의학 용어는 이 모양이 아니다.
    name_key = _match_key(getattr(target, "name", None))
    if not any(f"{subject_word}{word}" in name_key for word in ("진료", "치료")):
        return False
    subject_key = _region_key(subject_word)
    if len(subject_key) < 2:
        return False
    regions = list(getattr(target, "region_terms", None) or [])
    if hospital is not None:
        regions.extend(getattr(hospital, "region", None) or [])
    for region in regions:
        for part in str(region or "").split():
            region_key = _region_key(part)
            if len(region_key) < 2:
                continue
            # 진료 항목이 지역어와 같거나 그 앞부분일 때만이다("위례" ↔ "위례신도시"). 진료 항목이
            # 지역어로 시작하는 것("수정체" ↔ "수정구")은 지역명이 아니다.
            if subject_key == region_key or region_key.startswith(subject_key):
                return True
    return False
