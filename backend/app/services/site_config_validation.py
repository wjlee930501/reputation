"""병원 공개 페이지 섹션 설정 검증(설계 4.1) — 저장과 공개 반영이 같은 함수를 쓴다.

순수 함수다. DB에서 읽어야 하는 사실(병원 문구 원문, 공개 사진·공개 FAQ 글 id)은 호출부가
`SiteConfigContext`로 넘긴다(`site_config_refs.load_context`).

검사:
1. 형식 — `SiteConfigV1`(모르는 키·enum·범위).
2. 섹션 — 레이아웃에 없는 섹션, 같은 섹션 두 번, 필수 섹션 끄기, 제목을 바꿀 수 없는 섹션의 제목,
   첫 화면이 맨 앞이 아닌 순서, 붙은 섹션을 떼어 옮긴 순서.
3. 문구 — 사람이 쓴 칸마다, 그리고 **화면에 이어 보이는 묶음**(섹션 제목+안내, 단계 제목+설명)을
   기본값과 합친 최종 표시 문장으로 한 번 더 의료광고 금지 표현 필터에 건다. 칸마다는 통과해도
   이으면 걸리는 표현('타 병원보다' + '저렴합니다')을 막는다. 줄바꿈 지정은 원문과 글자가 같아야 한다.
4. 참조 — 공유 이미지는 그 병원의 공개 사진(원장 사진이면 실인물·신원 승인)이어야 하고, 고른 FAQ
   글은 그 병원의 공개 FAQ 글 중 승인 질문·답이 있는 것이어야 한다.
5. 메뉴 — 꺼진 섹션을 가리키는 항목은 경고(저장은 된다). 공개 응답이 그 항목을 뺀다.
6. 정보 띠 — B안의 '예약' 칸은 네이버 플레이스가 없으면 경고(저장은 된다). 공개 화면이 그 칸을 뺀다.
7. B안 전용 칸 — 첫 화면 정보 띠 칸(`hero.blocks`)과 진료 항목 모양(`treatments.style`)은 B안에서만 그린다.
   A·C에서 기본값과 다른 값은 거절한다(기본값과 같은 값·비운 칸은 그대로 통과 — 옛 초안·완성본이 막히지 않게).
8. 아직 그리지 않는 칸 — 병원 화면이 그리지 않는 칸(진료 철학·질문 합치기·진료 지역 표기·바로가기·본문 글자 크기·
   글 보조 칸), 머리 없는 섹션(첫 화면·바로가기)의 정렬·소제목·배경과 바로가기 켜기, 배경을 바꿀 수 없는
   섹션의 배경은 기본값과 다르면 거절한다(설계 20절). 공개 반영이 이 칸을 공개해 다음 사이트 배포가 재공개 없이
   화면을 바꾸지 않게 한다.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.services import site_defaults
from app.services.site_config_schema import LINE_MAX, LINES_MAX, SiteConfigV1, storage_form
from app.utils.medical_filter import check_forbidden


@dataclass(frozen=True)
class SiteConfigContext:
    """검증에 필요한 병원 사실. 줄바꿈 원문은 병원 정보에 **저장된** 문장이다."""

    hero_headline: str | None = None
    hero_description: str | None = None
    care_principles: dict[str, Any] | None = None
    share_image_ids: frozenset[uuid.UUID] = frozenset()
    faq_content_ids: frozenset[uuid.UUID] = frozenset()
    # '예약' 칸이 여는 네이버 플레이스가 있는가(주소나 플레이스 번호). 모르면 경고하지 않는다.
    has_naver_place: bool = True


@dataclass
class SiteConfigCheck:
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] | None = None
    resolved: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, path: str, code: str, message: str, **extra: Any) -> None:
        self.errors.append({"path": path, "code": code, "message": message, **extra})

    def warn(self, path: str, code: str, message: str) -> None:
        self.warnings.append({"path": path, "code": code, "message": message})


# 메뉴 항목 → 그 항목이 가리키는 섹션(레이아웃에 있는 첫 후보). 웹사이트·전화는 섹션이 아니다.
_NAV_TARGETS: dict[str, tuple[str, ...]] = {
    "treatments": ("treatments",),
    "doctor": ("doctor",),
    "faq": ("faq",),
    "contents": ("contents", "news"),
    "visit": ("visit", "facts"),
}


def nav_target(layout: str, item: str) -> str | None:
    ids = site_defaults.section_ids(layout)
    return next((candidate for candidate in _NAV_TARGETS.get(item, ()) if candidate in ids), None)


def line_break_source(path: str, ctx: SiteConfigContext) -> str | None:
    """줄바꿈을 거는 칸의 지금 원문. 사이트가 기본 문구로 채우는 빈 칸은 None이다."""

    if path == "hero.headline":
        return ctx.hero_headline or None
    if path == "hero.description":
        return ctx.hero_description or None
    principles = ctx.care_principles if isinstance(ctx.care_principles, dict) else {}
    if path == "care_principles.title":
        return principles.get("title") or None
    if path == "care_principles.lede":
        return principles.get("lede") or None
    if path.startswith("care_principles.cards["):
        index = int(path[len("care_principles.cards[") : path.index("]")])
        part = path.rsplit(".", 1)[1]
        cards = principles.get("cards") or []
        card = cards[index] if index < len(cards) and isinstance(cards[index], dict) else {}
        return card.get(part) or None
    return None


def joined(text: str) -> str:
    """여러 줄 글을 화면에 이어 보이는 한 문장으로(공백·줄바꿈을 공백 하나로)."""

    return " ".join(text.split())


def split_lines(value: str) -> list[str]:
    """줄 나누기 — LF만이 아니라 CR·CRLF·유니코드 줄 구분자도 줄 경계로 본다."""

    return [line.strip() for line in value.splitlines()] or [""]


def forbidden_words(text: str | None) -> list[str]:
    """의료광고 금지 표현 — 줄바꿈을 공백으로 이은 글과 **붙여** 이은 글 둘 다 본다.

    필터는 줄바꿈을 넘는 표현을 보지 못하고('완\n치 보장'), 줄바꿈을 공백으로 바꾸면 단어 중간 줄바꿈이
    '완 치 보장'이 되어 또 빠져나간다. 화면은 줄 경계에 공백을 넣지 않을 수 있으므로 둘 다 검사한다.
    """

    if not text:
        return []
    variants = {joined(text), "".join(split_lines(text))}
    words: list[str] = []
    for variant in variants:
        for word in check_forbidden(variant):
            if word not in words:
                words.append(word)
    return words


def lines_match(value: str, source: str) -> bool:
    """줄을 이으면 원문과 글자·띄어쓰기까지 같은가.

    줄 경계는 원문의 공백 한 칸 자리이거나(그 공백이 줄바꿈이 된다) 단어 중간(공백 없음)이어야 한다.
    줄 안의 띄어쓰기는 원문과 같아야 한다 — 띄어쓰기를 바꾸는 통로가 되지 않게.
    """

    target = joined(source)
    position = 0
    lines = [joined(line) for line in split_lines(value)]
    for index, line in enumerate(lines):
        if not line or not target.startswith(line, position):
            return False
        position += len(line)
        if index < len(lines) - 1 and position < len(target) and target[position] == " ":
            position += 1
    return position == len(target)


def line_starts(value: str, source: str) -> set[int]:
    """`lines_match`를 통과한 줄 지정에서 각 줄이 시작하는 자리(이은 원문 기준 글자 위치)."""

    target = joined(source)
    position = 0
    starts: set[int] = set()
    lines = [joined(line) for line in split_lines(value)]
    for index, line in enumerate(lines):
        starts.add(position)
        position += len(line)
        if index < len(lines) - 1 and position < len(target) and target[position] == " ":
            position += 1
    return starts


HEADLINE_PARTS_MAX = 3
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_JS_EDGE_SPACES = re.compile(f"^[{_JS_SPACE}]+|[{_JS_SPACE}]+$")


def _js_trim(text: str) -> str:
    return _JS_EDGE_SPACES.sub("", text)


def forced_line_starts(layout: str, path: str, source: str) -> list[int]:
    """레이아웃이 줄 지정과 무관하게 늘 새 줄로 시작하는 자리(이은 원문 기준).

    첫 화면 제목은 병원 정보에서 나눈 줄(최대 3줄, 사이트 `buildClinicHeroHeadline`)을 조각으로 그린다.
    두 줄 이상이면 A·C는 마지막 줄(강조 줄)을 블록으로, B는 모든 줄을 블록으로 쌓는다. 줄 지정이 그 자리를
    한 줄에 이어 두면 화면은 지정과 다르게 나뉘므로 저장에서 거절한다.
    """

    if path != "hero.headline":
        return []
    # 조각 고르기는 사이트와 같이 JS `trim()` 기준이다(파이썬 공백과 집합이 다르다 — U+0085는 JS 공백이 아니다).
    picked = [part for part in source.split("\n") if _js_trim(part)][:HEADLINE_PARTS_MAX]
    if len(picked) < 2:
        return []
    # 자리는 이은 원문 기준 — 이은 원문에서 글자가 없는 조각은 자리를 차지하지 않는다(사이트 `partStarts`).
    starts: list[int | None] = []
    position = 0
    seen = False
    for part in picked:
        text = joined(part)
        if not text:
            starts.append(None)
            continue
        if seen:
            position += 1
        starts.append(position if seen else None)
        position += len(text)
        seen = True
    later = starts[1:]
    forced = later if layout == "B" else later[-1:]
    return [at for at in forced if at is not None]


def validate_site_config(
    layout: str, config: Any, ctx: SiteConfigContext
) -> SiteConfigCheck:
    check = SiteConfigCheck()
    if layout not in site_defaults.LAYOUTS:
        check.error("layout", "LAYOUT_INVALID", "레이아웃은 A·B·C 중 하나여야 합니다.")
        return check
    if not isinstance(config, dict):
        check.error("config", "SCHEMA", "설정 형식이 맞지 않습니다.")
        return check
    try:
        model = SiteConfigV1.model_validate(config)
    except ValidationError as exc:
        for item in exc.errors():
            path = ".".join(str(part) for part in item["loc"]) or "config"
            check.error(path, "SCHEMA", item["msg"])
        return check

    stored = storage_form(model)
    _check_sections(check, layout, stored)
    _check_b_only(check, layout, stored)
    _check_unrendered(check, layout, stored)
    if not check.ok:
        return check
    resolved = site_defaults.resolve(layout, stored)
    _check_order(check, layout, resolved)
    _check_copy(check, stored, resolved, ctx)
    _check_line_breaks(check, layout, stored, ctx)
    _check_references(check, stored, ctx)
    _check_nav(check, layout, resolved)
    _check_hero_blocks(check, layout, resolved, ctx)
    check.config = stored
    check.resolved = resolved
    return check


def _check_sections(check: SiteConfigCheck, layout: str, stored: dict[str, Any]) -> None:
    available = set(site_defaults.section_ids(layout))
    seen: set[str] = set()
    for index, section in enumerate(stored.get("sections") or []):
        section_id = section["id"]
        path = f"sections[{index}]"
        if section_id not in available:
            check.error(f"{path}.id", "UNKNOWN_SECTION", f"레이아웃 {layout}에는 이 섹션이 없습니다: {section_id}")
            continue
        if section_id in seen:
            check.error(f"{path}.id", "DUPLICATE_SECTION", f"같은 섹션이 두 번 들어 있습니다: {section_id}")
            continue
        seen.add(section_id)
        if section_id in site_defaults.FIXED_SECTIONS and section.get("enabled") is False:
            check.error(f"{path}.enabled", "FIXED_SECTION", "이 섹션은 끌 수 없습니다(위치만 바꿀 수 있습니다).")
        if section_id in site_defaults.UNTITLED_SECTIONS and section.get("title") is not None:
            check.error(f"{path}.title", "SECTION_TITLE_FIXED", "이 섹션은 제목을 바꿀 수 없습니다.")


# B안에서만 그리는 칸 — A 첫 화면에는 정보 띠가 없고 C는 오늘 진료 위젯 하나다. A 알약·C 번호 타일은
# 카드도 목록도 아니고 설명을 싣지 않는다(설계 14절 '결정됨 — ③-2에서 B에만 열기'). 오시는 길 섹션은 B에만 있다.
_B_ONLY_FIELDS: tuple[tuple[str, str], ...] = (
    ("hero", "blocks"),
    ("treatments", "style"),
    ("treatments", "show_description"),
    ("visit", "order"),
    ("visit", "show_transit"),
)


def _check_b_only(check: SiteConfigCheck, layout: str, stored: dict[str, Any]) -> None:
    if layout == "B":
        return
    base = site_defaults.defaults(layout)
    for group, key in _B_ONLY_FIELDS:
        value = (stored.get(group) or {}).get(key)
        if value is not None and value != base[group][key]:
            check.error(
                f"{group}.{key}",
                "B_LAYOUT_ONLY",
                "이 칸은 B 레이아웃에서만 바꿀 수 있습니다.",
            )


# 병원 화면이 아직 그리지 않는 칸(모든 레이아웃) — 어드민 `UNRENDERED_FIELDS`와 같다.
_UNRENDERED_FIELDS: tuple[tuple[str, str], ...] = (
    ("doctor", "show_philosophy"),
    ("faq", "merge_questions"),
    ("region_tags", "show"),
    ("region_tags", "separator"),
    ("type", "body_size"),
    ("article", "aside"),
    ("nav", "section_index"),
)
# 정렬·소제목·배경을 그릴 머리가 없는 섹션. 바로가기는 켜기도 그리지 않는다.
_HEADLESS_SECTIONS: frozenset[str] = frozenset({"hero", "section_index"})
# 배경을 바꿀 수 없는 섹션(사이트 `BACKGROUND_FIXED`·어드민 `BACKGROUND_FIXED`와 같다).
_BACKGROUND_FIXED: dict[str, frozenset[str]] = {
    "A": frozenset({"hero", "hours"}),
    "B": frozenset({"hero", "section_index"}),
    "C": frozenset({"hero", "news", "hours", "care_principles", "care_flow"}),
}

# 병원 화면이 그리지 않는 안내 문구 칸(어드민 `UNRENDERED_COPY_KEYS`와 같다) — 진료시간 '주말 진료 이름'은
# A·C 진료시간 어디에도 그릴 자리가 정해지지 않았다.
_UNRENDERED_COPY_KEYS: frozenset[str] = frozenset({"hours.weekend_label"})


def _copy_section_in_layout(layout: str, key: str) -> bool:
    """안내 문구 칸(`{섹션}.…`)의 섹션이 그 레이아웃에 있는가 — 없는 섹션의 문구는 그릴 곳이 없다(A 오시는 길 등)."""

    return key.split(".", 1)[0] in site_defaults.section_ids(layout)


# B에만 있는 글의 줄바꿈 — A·C는 진료 원칙 안내 문구(`care_principles.lede`)를 그리지 않는다(어드민 `B_ONLY_LINE_BREAK_PATHS`와 같다).
_B_ONLY_LINE_BREAKS: frozenset[str] = frozenset({"care_principles.lede"})


def _set_line_break(spec: Any) -> bool:
    return isinstance(spec, dict) and any(
        isinstance(spec.get(d), str) and spec.get(d).strip() for d in ("pc", "mobile")
    )


def _undrawn_veil_keys(layout: str, hero: Any) -> list[str]:
    """그려지지 않는 첫 화면 덮개 칸 — 덮개는 '사진 위 글자' 배치에서만, 농도는 '사진 전체' 덮개에서만 그린다."""

    base = site_defaults.defaults(layout)["hero"]
    hero = hero if isinstance(hero, dict) else {}
    image = hero.get("image") if isinstance(hero.get("image"), dict) else {}
    veil = image.get("veil") if isinstance(image.get("veil"), dict) else {}
    arrangement = hero.get("arrangement", base["arrangement"])
    extent = veil.get("extent", base["image"]["veil"]["extent"])
    if arrangement != "overlay":
        return ["extent", "opacity"]
    return [] if extent == "full" else ["opacity"]


def _undrawn_focus(layout: str, hero: Any) -> bool:
    """'글자만' 배치는 첫 화면 사진을 그리지 않는다 — 사진 초점이 화면에 없다."""

    base = site_defaults.defaults(layout)["hero"]
    hero = hero if isinstance(hero, dict) else {}
    return hero.get("arrangement", base["arrangement"]) == "text"


def strip_unrendered(layout: str, config: Any, *, hero_headline: str | None = None) -> Any:
    """옛 판 복원용 — 화면이 그리지 않는 칸(A·C의 B 전용 칸 포함)을 덮어쓰기에서 뺀다(그 칸은 기본값으로 돌아간다).

    그 칸이 거절되기 전에 저장·공개된 판도 복원할 수 있게 한다. 형식이 틀린 설정은 그대로 둔다 — 검증이 알린다.
    `hero_headline`(지금 병원 정보 제목)을 주면 레이아웃이 늘 나누는 자리를 건너뛴 제목 줄 지정도 뺀다
    (그 기기는 자동 줄바꿈으로 돌아간다).
    """

    if not isinstance(config, dict) or layout not in site_defaults.LAYOUTS:
        return config
    out = copy.deepcopy(config)
    drop = list(_UNRENDERED_FIELDS) + ([] if layout == "B" else list(_B_ONLY_FIELDS))
    for group, key in drop:
        value = out.get(group)
        if isinstance(value, dict) and key in value:
            value.pop(key)
            if not value:
                out.pop(group)
    copy_notes = out.get("copy")
    if isinstance(copy_notes, dict):
        for key in list(copy_notes):
            if key in _UNRENDERED_COPY_KEYS or not _copy_section_in_layout(layout, key):
                copy_notes.pop(key)
        if not copy_notes:
            out.pop("copy")
    hero = out.get("hero")
    image = hero.get("image") if isinstance(hero, dict) else None
    veil = image.get("veil") if isinstance(image, dict) else None
    if isinstance(veil, dict):
        for key in _undrawn_veil_keys(layout, hero):
            veil.pop(key, None)
    if isinstance(image, dict) and _undrawn_focus(layout, hero):
        image.pop("focus", None)
    breaks = hero.get("line_breaks") if isinstance(hero, dict) else None
    if layout != "B" and isinstance(breaks, dict):
        for path in _B_ONLY_LINE_BREAKS:
            breaks.pop(path, None)
    spec = breaks.get("hero.headline") if isinstance(breaks, dict) else None
    if hero_headline and isinstance(spec, dict):
        forced = set(forced_line_starts(layout, "hero.headline", hero_headline))
        for device in ("pc", "mobile"):
            value = spec.get(device)
            if (
                isinstance(value, str)
                and lines_match(value, hero_headline)
                and not forced <= line_starts(value, hero_headline)
            ):
                spec.pop(device)
        if not any(isinstance(spec.get(device), str) for device in ("pc", "mobile")):
            breaks.pop("hero.headline")
    sections = out.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = section.get("id")
            fixed: set[str] = set()
            if section_id in _HEADLESS_SECTIONS:
                fixed |= {"align", "label", "background"}
            if section_id == "section_index":
                fixed.add("enabled")
            if section_id in _BACKGROUND_FIXED[layout]:
                fixed.add("background")
            for key in fixed:
                section.pop(key, None)
    return out


def _check_unrendered(check: SiteConfigCheck, layout: str, stored: dict[str, Any]) -> None:
    base = site_defaults.defaults(layout)
    message = "이 칸은 아직 병원 화면에 반영되지 않아 바꿀 수 없습니다."
    for group, key in _UNRENDERED_FIELDS:
        value = (stored.get(group) or {}).get(key)
        if value is not None and value != base[group][key]:
            check.error(f"{group}.{key}", "FIELD_NOT_RENDERED", message)
    copy_notes = stored.get("copy") or {}
    if isinstance(copy_notes, dict):
        for key, value in sorted(copy_notes.items()):
            unrendered = key in _UNRENDERED_COPY_KEYS or not _copy_section_in_layout(layout, key)
            if unrendered and isinstance(value, str) and value.strip():
                check.error(f"copy.{key}", "FIELD_NOT_RENDERED", message)
    stored_hero = stored.get("hero") or {}
    stored_veil = ((stored_hero.get("image") or {}) if isinstance(stored_hero, dict) else {}).get("veil") or {}
    base_veil = base["hero"]["image"]["veil"]
    stored_focus = ((stored_hero.get("image") or {}) if isinstance(stored_hero, dict) else {}).get("focus")
    if _undrawn_focus(layout, stored_hero) and stored_focus is not None and stored_focus != base["hero"]["image"]["focus"]:
        check.error("hero.image.focus", "FIELD_NOT_RENDERED", message)
    for key in _undrawn_veil_keys(layout, stored_hero):
        value = stored_veil.get(key) if isinstance(stored_veil, dict) else None
        if value is not None and value != base_veil[key]:
            check.error(f"hero.image.veil.{key}", "FIELD_NOT_RENDERED", message)
    breaks = (stored.get("hero") or {}).get("line_breaks") or {}
    if layout != "B" and isinstance(breaks, dict):
        for path in sorted(_B_ONLY_LINE_BREAKS):
            if _set_line_break(breaks.get(path)):
                check.error(f"hero.line_breaks.{path}", "FIELD_NOT_RENDERED", message)
    defaults_by_id = {section["id"]: section for section in base["sections"]}
    for index, section in enumerate(stored.get("sections") or []):
        section_id = section.get("id")
        default = defaults_by_id.get(section_id)
        if default is None:
            continue
        fixed: list[str] = []
        if section_id in _HEADLESS_SECTIONS:
            fixed += ["align", "label", "background"]
        if section_id == "section_index":
            fixed.append("enabled")
        if section_id in _BACKGROUND_FIXED[layout]:
            fixed.append("background")
        for key in dict.fromkeys(fixed):
            value = section.get(key)
            if value is not None and value != default.get(key):
                check.error(f"sections.{index}.{key}", "FIELD_NOT_RENDERED", message)


def _check_order(check: SiteConfigCheck, layout: str, resolved: dict[str, Any]) -> None:
    sections = resolved["sections"]
    if sections[0]["id"] != site_defaults.FIRST_SECTION:
        check.error("sections", "FIRST_SECTION", "첫 화면은 맨 앞에 있어야 합니다.")
    for index, section in enumerate(sections):
        host = section.get("attached_to")
        if not host:
            continue
        previous = sections[index - 1] if index else None
        if previous is None or (previous["id"] != host and previous.get("attached_to") != host):
            check.error(
                "sections",
                "SECTION_ATTACHED",
                f"'{section['id']}' 섹션은 '{host}' 섹션 안에 붙어 있어 따로 옮길 수 없습니다.",
            )


def _forbidden(check: SiteConfigCheck, path: str, text: str | None, *, source: str | None = None) -> None:
    if not text:
        return
    words = forbidden_words(text)
    if words:
        message = f"의료광고 금지 표현은 사용할 수 없습니다: {', '.join(words)}"
        if source:
            message += f" ({source})"
        check.error(path, "FORBIDDEN_EXPRESSION", message, words=words)


def section_title(section: dict[str, Any], ctx: SiteConfigContext | None = None) -> str | None:
    """화면에 보일 섹션 제목. 진료 원칙은 설정이 제목을 바꾸지 않았으면 병원 정보의 제목이 이긴다."""

    title = section.get("title")
    if section.get("id") == "care_principles" and title == site_defaults.CARE_PRINCIPLES_TITLE:
        principles = ctx.care_principles if ctx and isinstance(ctx.care_principles, dict) else {}
        return principles.get("title") or title
    return title


def section_note(
    resolved: dict[str, Any], section_id: str, ctx: SiteConfigContext | None = None
) -> str | None:
    """섹션 제목 바로 아래 이어 보이는 문장."""

    if section_id == "care_flow":
        return (resolved.get("care_flow") or {}).get("note")
    if section_id == "care_principles":
        principles = ctx.care_principles if ctx and isinstance(ctx.care_principles, dict) else {}
        return principles.get("lede") or site_defaults.CARE_PRINCIPLES_LEDE
    return (resolved.get("copy") or {}).get(f"{section_id}.note")


def effective_care_steps(resolved: dict[str, Any]) -> list[tuple[str, str]]:
    steps = (resolved.get("care_flow") or {}).get("steps") or []
    return [(step["title"], step["body"]) for step in steps]


def stored_text_groups(ctx: SiteConfigContext) -> list[tuple[str, str]]:
    """병원 정보에 저장된 문장 중 화면에 이어 보이는 묶음(첫 화면 제목+설명, 원칙 카드 제목+설명).

    칸마다는 병원 정보 저장이 이미 걸렀지만 이어 보이면 걸릴 수 있다. 편집기는 이 문장을 바꾸지 못하므로
    걸리면 병원 정보에서 고치라고 알린다.
    """

    groups = []
    headline = " ".join(part for part in (ctx.hero_headline, ctx.hero_description) if part)
    if headline:
        groups.append(("hero", headline))
    principles = ctx.care_principles if isinstance(ctx.care_principles, dict) else {}
    for index, card in enumerate(principles.get("cards") or []):
        if isinstance(card, dict) and (card.get("title") or card.get("body")):
            text = " ".join(part for part in (card.get("title"), card.get("body")) if part)
            groups.append((f"care_principles.cards[{index}]", text))
    return groups


def _check_copy(
    check: SiteConfigCheck, stored: dict[str, Any], resolved: dict[str, Any], ctx: SiteConfigContext
) -> None:
    # 칸마다.
    for index, section in enumerate(stored.get("sections") or []):
        _forbidden(check, f"sections[{index}].title", section.get("title"))
    for key, value in (stored.get("copy") or {}).items():
        _forbidden(check, f"copy.{key}", value)
    care_flow = stored.get("care_flow") or {}
    _forbidden(check, "care_flow.note", care_flow.get("note"))
    for index, step in enumerate(care_flow.get("steps") or []):
        _forbidden(check, f"care_flow.steps[{index}].title", step.get("title"))
        _forbidden(check, f"care_flow.steps[{index}].body", step.get("body"))
    _forbidden(check, "share.description", (stored.get("share") or {}).get("description"))
    if not check.ok:
        return
    # 화면에 이어 보이는 묶음 — 병원 원문·기본값·덮어쓰기를 합친 최종 표시 문장.
    for section in resolved["sections"]:
        parts = [section_title(section, ctx), section_note(resolved, section["id"], ctx)]
        _forbidden(check, f"sections.{section['id']}", " ".join(part for part in parts if part))
    for index, (title, body) in enumerate(effective_care_steps(resolved)):
        _forbidden(check, f"care_flow.steps[{index}]", f"{title} {body}")
    for path, text in stored_text_groups(ctx):
        _forbidden(check, path, text, source="병원 정보의 문장을 고쳐 주세요")


def _check_line_breaks(
    check: SiteConfigCheck, layout: str, stored: dict[str, Any], ctx: SiteConfigContext
) -> None:
    line_breaks = (stored.get("hero") or {}).get("line_breaks") or {}
    for path, spec in line_breaks.items():
        where = f"hero.line_breaks.{path}"
        source = line_break_source(path, ctx)
        if not source:
            check.error(
                where,
                "LINES_SOURCE_EMPTY",
                "병원 정보에 저장된 문장이 없어 줄바꿈을 정할 수 없습니다(기본 문구에는 줄바꿈을 걸 수 없습니다).",
            )
            continue
        for device in ("pc", "mobile"):
            value = spec.get(device)
            if value is None:
                continue
            lines = split_lines(value)
            if len(lines) > LINES_MAX or any(not line or len(line) > LINE_MAX for line in lines):
                check.error(
                    f"{where}.{device}",
                    "LINES_INVALID",
                    f"줄은 {LINES_MAX}줄까지, 빈 줄 없이 한 줄 {LINE_MAX}자 이하여야 합니다.",
                )
                continue
            if not lines_match(value, source):
                check.error(
                    f"{where}.{device}",
                    "LINES_TEXT_MISMATCH",
                    "줄바꿈 위치만 바꿀 수 있습니다 — 줄을 이은 글이 병원 정보의 문장과 같아야 합니다.",
                )
                continue
            if not set(forced_line_starts(layout, path, source)) <= line_starts(value, source):
                check.error(
                    f"{where}.{device}",
                    "LINES_LAYOUT_BREAK",
                    (
                        "B안은 병원 정보에서 나눈 제목 줄마다 새 줄로 시작합니다"
                        if layout == "B"
                        else "이 레이아웃은 제목의 마지막 줄(강조 줄)을 늘 새 줄로 시작합니다"
                    )
                    + " — 그 자리를 한 줄에 잇지 말고 나눠 주세요.",
                )
                continue
            _forbidden(check, f"{where}.{device}", value)


def _check_references(check: SiteConfigCheck, stored: dict[str, Any], ctx: SiteConfigContext) -> None:
    image_id = (stored.get("share") or {}).get("image_asset_id")
    if image_id is not None and uuid.UUID(image_id) not in ctx.share_image_ids:
        check.error(
            "share.image_asset_id",
            "SHARE_IMAGE_INVALID",
            "공유 이미지는 이 병원의 공개 사진이어야 합니다(원장 사진은 실인물·신원 사용 승인이 필요합니다).",
        )
    faq = stored.get("faq") or {}
    ids = faq.get("content_ids") or []
    for index, content_id in enumerate(ids):
        if uuid.UUID(content_id) not in ctx.faq_content_ids:
            check.error(
                f"faq.content_ids[{index}]",
                "FAQ_CONTENT_INVALID",
                "이 병원의 공개 글 중 승인된 질문·답이 있는 글만 고를 수 있습니다.",
            )
    if faq.get("mode") == "manual" and not ids:
        check.error("faq.content_ids", "FAQ_MANUAL_EMPTY", "직접 고르기를 쓰려면 FAQ 글을 하나 이상 골라 주세요.")


def _check_nav(check: SiteConfigCheck, layout: str, resolved: dict[str, Any]) -> None:
    enabled = {section["id"] for section in resolved["sections"] if section.get("enabled")}
    for item in (resolved.get("nav") or {}).get("items") or []:
        target = nav_target(layout, item)
        if item in _NAV_TARGETS and (target is None or target not in enabled):
            check.warn(
                f"nav.items.{item}",
                "NAV_TARGET_DISABLED",
                "이 메뉴가 가리키는 섹션이 꺼져 있어 공개 화면에서는 메뉴에서 빠집니다.",
            )


def _check_hero_blocks(
    check: SiteConfigCheck, layout: str, resolved: dict[str, Any], ctx: SiteConfigContext
) -> None:
    blocks = (resolved.get("hero") or {}).get("blocks") or []
    if layout == "B" and "booking" in blocks and not ctx.has_naver_place:
        check.warn(
            "hero.blocks",
            "BOOKING_NO_PLACE",
            "네이버 플레이스가 없어 공개 화면에서는 '예약' 칸이 빠집니다.",
        )


def changed_sections(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """직전 공개 판과 다른 설정 구역·섹션 키(이력 화면용)."""

    if before is None:
        return sorted(key for key in after if key not in {"defaults_version", "schema_version"})
    keys: set[str] = set()
    for key in set(before) | set(after):
        if key in {"defaults_version", "schema_version", "sections"}:
            continue
        if before.get(key) != after.get(key):
            keys.add(key)
    before_sections = {section["id"]: section for section in before.get("sections") or []}
    after_sections = {section["id"]: section for section in after.get("sections") or []}
    for section_id in set(before_sections) | set(after_sections):
        if before_sections.get(section_id) != after_sections.get(section_id):
            keys.add(f"sections.{section_id}")
    if [s["id"] for s in before.get("sections") or []] != [s["id"] for s in after.get("sections") or []]:
        keys.add("sections.order")
    return sorted(keys)


__all__ = (
    "SiteConfigCheck",
    "SiteConfigContext",
    "changed_sections",
    "effective_care_steps",
    "forced_line_starts",
    "forbidden_words",
    "joined",
    "lines_match",
    "section_title",
    "line_break_source",
    "line_starts",
    "nav_target",
    "section_note",
    "split_lines",
    "validate_site_config",
)
