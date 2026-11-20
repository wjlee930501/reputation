"""병원 공개 페이지 섹션 설정 `config` 스키마 v1(설계 3절).

기본값과 다른 칸만 담는다 — 모든 칸이 선택이다. 모르는 키는 거절한다(`extra=forbid`). 형식·범위만
여기서 보고, 레이아웃·필수 섹션·문구 필터·참조는 `site_config_validation`이 본다.

v1 범위 밖(설계 결정 대기·3b 뒤):
- 진료 원칙 카드 4개(결정 S1) — 저장 칸을 넓히는 일은 사이트가 먼저 4개를 읽도록 배포한 뒤(S8)라
  `card_count`는 지금 저장 칸 수(`clinic_page_copy.CARE_PRINCIPLE_CARD_COUNT`)까지만 받는다.
- 버스·지하철을 나눈 오시는 길(결정 S7) — 주차·대중교통 순서와 대중교통 줄 끄기만.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.services.clinic_page_copy import CARE_PRINCIPLE_CARD_COUNT

SECTION_TITLE_MAX = 40
NOTE_MAX = 200
STEP_TITLE_MAX = 30
STEP_BODY_MAX = 200
SHARE_DESCRIPTION_MAX = 160
LINE_MAX = 200
LINES_MAX = 6

SectionId = Literal[
    "hero",
    "section_index",
    "contents",
    "news",
    "hours",
    "treatments",
    "doctor",
    "care_principles",
    "care_flow",
    "gallery",
    "questions",
    "faq",
    "facts",
    "visit",
]
NavItem = Literal["treatments", "visit", "doctor", "contents", "faq", "website", "call"]
MobileBarItem = Literal["call", "hours", "guide", "map"]
HeroBlock = Literal["today", "phone", "location", "booking", "hours"]

# 줄바꿈을 걸 수 있는 칸(고정 목록, 설계 3.3) — 임의 경로는 받지 않는다. FAQ 질문·추적 측정 질문 같은
# 승인 문장에는 쓸 수 없다(I8).
LINE_BREAK_PATHS: tuple[str, ...] = (
    "hero.headline",
    "hero.description",
    "care_principles.title",
    "care_principles.lede",
    *(
        f"care_principles.cards[{index}].{part}"
        for index in range(CARE_PRINCIPLE_CARD_COUNT)
        for part in ("title", "body")
    ),
)
LineBreakPath = Literal[LINE_BREAK_PATHS]  # type: ignore[valid-type]

# 고정 문구 덮어쓰기 — 섹션 아래 짧은 안내 문장(비우면 공통 문구).
COPY_KEYS: tuple[str, ...] = (
    "treatments.note",
    "doctor.note",
    "contents.note",
    "questions.note",
    "faq.note",
    "gallery.note",
    "visit.note",
    "hours.weekend_label",
)
CopyKey = Literal[COPY_KEYS]  # type: ignore[valid-type]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Point(_Strict):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Veil(_Strict):
    opacity: float | None = Field(default=None, ge=0, le=0.9)
    extent: Literal["text", "full", "none"] | None = None


class HeroImage(_Strict):
    focus: Point | None = None
    veil: Veil | None = None


class LineBreak(_Strict):
    pc: Annotated[str, StringConstraints(max_length=LINE_MAX * LINES_MAX)] | None = None
    mobile: Annotated[str, StringConstraints(max_length=LINE_MAX * LINES_MAX)] | None = None


class Hero(_Strict):
    arrangement: Literal["overlay", "split", "text"] | None = None
    title_size: Literal["s", "m", "l"] | None = None
    line_breaks: dict[LineBreakPath, LineBreak] | None = None
    blocks: list[HeroBlock] | None = Field(default=None, min_length=1, max_length=5)
    image: HeroImage | None = None

    @field_validator("blocks")
    @classmethod
    def _unique_blocks(cls, value: list[str] | None) -> list[str] | None:
        return _unique(value, "첫 화면 정보 블록")


class Section(_Strict):
    id: SectionId
    enabled: bool | None = None
    title: Annotated[Text, StringConstraints(max_length=SECTION_TITLE_MAX)] | None = None
    background: Literal["default", "white", "tint", "band"] | None = None
    align: Literal["left", "center"] | None = None
    label: bool | None = None


class Treatments(_Strict):
    show_description: bool | None = None
    style: Literal["cards", "list"] | None = None


class Doctor(_Strict):
    photo_ratio: Literal["portrait", "square", "landscape"] | None = None
    photo_focus: Point | None = None
    title_position: Literal["above", "below"] | None = None
    show_philosophy: bool | None = None


class CarePrinciples(_Strict):
    card_count: int | None = Field(default=None, ge=2, le=CARE_PRINCIPLE_CARD_COUNT)
    use_default_cards: bool | None = None


class CareStep(_Strict):
    title: Annotated[Text, StringConstraints(max_length=STEP_TITLE_MAX)]
    body: Annotated[Text, StringConstraints(max_length=STEP_BODY_MAX)]


class CareFlow(_Strict):
    note: Annotated[Text, StringConstraints(max_length=NOTE_MAX)] | None = None
    steps: list[CareStep] | None = Field(default=None, min_length=2, max_length=6)


class Visit(_Strict):
    order: list[Literal["parking", "transit"]] | None = None
    show_transit: bool | None = None

    @field_validator("order")
    @classmethod
    def _full_order(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and sorted(value) != ["parking", "transit"]:
            raise ValueError("오시는 길 순서는 주차·대중교통을 한 번씩 담아야 합니다")
        return value


class Faq(_Strict):
    mode: Literal["auto", "manual"] | None = None
    limit: int | None = Field(default=None, ge=3, le=10)
    content_ids: list[uuid.UUID] | None = Field(default=None, max_length=10)
    merge_questions: bool | None = None

    @field_validator("content_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> Any:
        # JSON에서 온 문자열 id를 UUID로 받는다(strict 모드는 문자열→UUID 변환을 막는다).
        if isinstance(value, list):
            try:
                return [uuid.UUID(str(item)) for item in value]
            except ValueError as exc:
                raise ValueError("FAQ 글 id 형식이 맞지 않습니다") from exc
        return value

    @field_validator("content_ids")
    @classmethod
    def _unique_ids(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return _unique(value, "FAQ 글")


class RegionTags(_Strict):
    show: bool | None = None
    separator: Literal["comma", "dot", "slash"] | None = None


class Nav(_Strict):
    items: list[NavItem] | None = Field(default=None, max_length=7)
    mobile_bar: list[MobileBarItem] | None = Field(default=None, max_length=4)
    section_index: bool | None = None

    @field_validator("items", "mobile_bar")
    @classmethod
    def _unique_items(cls, value: list[str] | None) -> list[str] | None:
        return _unique(value, "메뉴 항목")


class Share(_Strict):
    image_asset_id: uuid.UUID | None = None
    description: Annotated[Text, StringConstraints(max_length=SHARE_DESCRIPTION_MAX)] | None = None

    @field_validator("image_asset_id", mode="before")
    @classmethod
    def _asset_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError as exc:
                raise ValueError("공유 이미지 id 형식이 맞지 않습니다") from exc
        return value


class Typography(_Strict):
    body_size: Literal["normal", "large"] | None = None


class Article(_Strict):
    aside: Literal["bottom", "right_sticky"] | None = None


class SiteConfigV1(_Strict):
    sections: list[Section] | None = Field(default=None, max_length=20)
    hero: Hero | None = None
    copy_: dict[CopyKey, Annotated[Text, StringConstraints(max_length=NOTE_MAX)]] | None = Field(
        default=None, alias="copy"
    )
    treatments: Treatments | None = None
    doctor: Doctor | None = None
    care_principles: CarePrinciples | None = None
    care_flow: CareFlow | None = None
    visit: Visit | None = None
    faq: Faq | None = None
    region_tags: RegionTags | None = None
    nav: Nav | None = None
    share: Share | None = None
    type: Typography | None = None
    article: Article | None = None

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


def _unique(value: list[Any] | None, label: str) -> list[Any] | None:
    if value is not None and len(set(value)) != len(value):
        raise ValueError(f"{label}이(가) 두 번 들어 있습니다")
    return value


def storage_form(model: SiteConfigV1) -> dict[str, Any]:
    """저장 모양 — 비운 칸(None)은 빼고 JSON으로 쓸 수 있는 값만 남긴다."""

    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = (
    "COPY_KEYS",
    "LINES_MAX",
    "LINE_BREAK_PATHS",
    "LINE_MAX",
    "SiteConfigV1",
    "storage_form",
)
