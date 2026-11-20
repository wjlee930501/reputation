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
"""

from __future__ import annotations

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
    if not check.ok:
        return check
    resolved = site_defaults.resolve(layout, stored)
    _check_order(check, layout, resolved)
    _check_copy(check, stored, resolved, ctx)
    _check_line_breaks(check, stored, ctx)
    _check_references(check, stored, ctx)
    _check_nav(check, layout, resolved)
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


def _check_line_breaks(check: SiteConfigCheck, stored: dict[str, Any], ctx: SiteConfigContext) -> None:
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
    "forbidden_words",
    "joined",
    "lines_match",
    "section_title",
    "line_break_source",
    "nav_target",
    "section_note",
    "split_lines",
    "validate_site_config",
)
