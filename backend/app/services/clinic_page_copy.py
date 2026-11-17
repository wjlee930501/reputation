"""병원 공개 페이지에서 병원마다 바꾸는 문구 — 진료 원칙·주차·대중교통(2026-11-17).

홈 '진료 원칙' 칸과 오시는 길의 주차·대중교통 안내는 원래 모든 병원이 같은 템플릿 문구였다.
마케팅팀 요청(365서귀포삼성정형외과)으로 마케터가 병원마다 직접 쓰는 칸을 둔다. 비운 칸은
사이트가 공통 기본 문구를 쓴다 — 기본 문구는 `site/lib/clinic-template-copy.ts` 한 곳에 있다.

저장(관리자 PATCH)과 공개(공개 API) 모두 의료광고 금지 표현 필터를 건다. 저장은 거절하고,
공개는 금지 표현이 든 문장만 비운다(필터가 바뀌어 옛 저장값이 새로 걸려도 공개되지 않게).
"""

from __future__ import annotations

from typing import Any

from app.utils.medical_filter import check_forbidden

CARE_PRINCIPLE_CARD_COUNT = 3
CARE_PRINCIPLES_TITLE_MAX = 60
CARE_PRINCIPLES_LEDE_MAX = 300
CARE_PRINCIPLE_CARD_TITLE_MAX = 60
CARE_PRINCIPLE_CARD_BODY_MAX = 300
VISIT_NOTE_MAX = 200


def _clean(value: Any, *, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}은(는) 글자여야 합니다")
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValueError(f"{label}은(는) {max_length}자 이하로 써 주세요")
    violations = check_forbidden(cleaned)
    if violations:
        raise ValueError(f"의료광고 금지 표현은 사용할 수 없습니다: {', '.join(violations)}")
    return cleaned


def normalize_visit_note(value: Any, *, label: str) -> str | None:
    return _clean(value, label=label, max_length=VISIT_NOTE_MAX)


def normalize_care_principles(value: Any) -> dict[str, Any] | None:
    """관리자 입력을 저장 모양으로 맞춘다. 모두 비었으면 None(공통 기본 문구를 쓴다).

    카드는 자리(1~3번)를 지킨다 — 2번만 바꾸면 1·3번은 빈 자리로 남아 사이트가 그 자리에
    기본 카드를 쓴다. 카드는 제목과 설명이 둘 다 있어야 쓰고, 한쪽만 있으면 거절한다.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("진료 원칙 문구 형식이 맞지 않습니다")
    unknown = set(value) - {"title", "lede", "cards"}
    if unknown:
        raise ValueError(f"진료 원칙 문구에 알 수 없는 칸이 있습니다: {', '.join(sorted(unknown))}")
    title = _clean(value.get("title"), label="진료 원칙 제목", max_length=CARE_PRINCIPLES_TITLE_MAX)
    lede = _clean(
        value.get("lede"), label="진료 원칙 소개 문장", max_length=CARE_PRINCIPLES_LEDE_MAX
    )
    raw_cards = value.get("cards") or []
    if not isinstance(raw_cards, list):
        raise ValueError("진료 원칙 카드 형식이 맞지 않습니다")
    if len(raw_cards) > CARE_PRINCIPLE_CARD_COUNT:
        raise ValueError(f"진료 원칙 카드는 {CARE_PRINCIPLE_CARD_COUNT}개까지입니다")
    cards: list[dict[str, str | None]] = []
    for index in range(CARE_PRINCIPLE_CARD_COUNT):
        raw = raw_cards[index] if index < len(raw_cards) else None
        if raw is None:
            cards.append({"title": None, "body": None})
            continue
        if not isinstance(raw, dict):
            raise ValueError("진료 원칙 카드 형식이 맞지 않습니다")
        number = index + 1
        card_title = _clean(
            raw.get("title"), label=f"카드 {number} 제목", max_length=CARE_PRINCIPLE_CARD_TITLE_MAX
        )
        card_body = _clean(
            raw.get("body"), label=f"카드 {number} 설명", max_length=CARE_PRINCIPLE_CARD_BODY_MAX
        )
        if bool(card_title) != bool(card_body):
            raise ValueError(f"카드 {number}은(는) 제목과 설명을 함께 써 주세요")
        cards.append({"title": card_title, "body": card_body})
    if not title and not lede and not any(card["title"] for card in cards):
        return None
    return {"title": title, "lede": lede, "cards": cards}


def _public_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and not check_forbidden(text) else None


def public_care_principles(value: Any) -> dict[str, Any] | None:
    """공개 API 모양. 금지 표현이 든 문장은 비우고, 남는 것이 없으면 None."""
    if not isinstance(value, dict):
        return None
    cards: list[dict[str, str] | None] = []
    for raw in (value.get("cards") or [])[:CARE_PRINCIPLE_CARD_COUNT]:
        title = _public_text(raw.get("title")) if isinstance(raw, dict) else None
        body = _public_text(raw.get("body")) if isinstance(raw, dict) else None
        cards.append({"title": title, "body": body} if title and body else None)
    while len(cards) < CARE_PRINCIPLE_CARD_COUNT:
        cards.append(None)
    title = _public_text(value.get("title"))
    lede = _public_text(value.get("lede"))
    if not title and not lede and not any(cards):
        return None
    return {"title": title, "lede": lede, "cards": cards}


def public_visit_note(value: Any) -> str | None:
    return _public_text(value)
