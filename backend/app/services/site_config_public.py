"""공개 응답의 `site_config` — 공개 판 완성본을 공개용으로 다시 정리한다(설계 5절).

저장은 검증을 통과했지만, 공개하는 순간의 사실은 그 뒤에 바뀔 수 있다. 그래서 공개 응답은
읽을 때마다 다시 정리한다(`clinic_page_copy.public_care_principles`와 같은 이중 방어):

- 금지 표현이 든 문구는 비운다(필터가 바뀌어 옛 저장값이 새로 걸려도 공개되지 않게). 섹션 제목·안내는
  칸 하나 또는 이어 보이는 묶음이 걸리면 기본 문구로 돌아가고, 진료 과정 단계는 하나라도 걸리면 기본
  4단계로 돌아간다. 필터는 줄바꿈을 공백으로 이은 글과 붙여 이은 글 둘 다 본다(`forbidden_words`).
- 원문이 바뀐 줄바꿈 지정은 버린다(사이트는 자동 줄바꿈으로 그린다).
- 공개가 끝난 사진·내려간 FAQ 글은 뺀다. 직접 고른 FAQ의 질문·답은 스냅샷이 아니라 지금 공개된 글에서
  읽는다(I8 — 승인 질문·답은 편집기가 바꾸지 않는다). 고른 글이 모두 내려가면 빈 목록이다(다른 글을
  대신 싣지 않는다).
- 꺼진 섹션을 가리키는 메뉴 항목은 뺀다.

설정이 없거나 공개 판의 레이아웃이 병원의 지금 레이아웃과 다르면 `None`이다 — 사이트는 지금 화면
그대로 그린다(I6).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services import site_defaults
from app.services.site_config_validation import (
    SiteConfigContext,
    forbidden_words,
    line_break_source,
    lines_match,
    nav_target,
    section_note,
)


@dataclass(frozen=True)
class PublicSiteConfigContext:
    layout: str | None
    texts: SiteConfigContext
    # 공개 FAQ 글 id → {"question", "answer"} (지금 공개된 승인 질문·답).
    faq_entries: dict[uuid.UUID, dict[str, str]] = field(default_factory=dict)


def _clean(value: str | None) -> str | None:
    if not value or forbidden_words(value):
        return None
    return value


def public_site_config(
    resolved: dict[str, Any] | None,
    *,
    version: int | None,
    ctx: PublicSiteConfigContext,
) -> dict[str, Any] | None:
    if not isinstance(resolved, dict):
        return None
    layout = resolved.get("layout")
    if layout not in site_defaults.LAYOUTS or layout != ctx.layout:
        return None
    out = copy.deepcopy(resolved)
    defaults_by_id = {section["id"]: section for section in site_defaults.default_sections(layout)}
    available = set(defaults_by_id)

    # 섹션 — 이 레이아웃에 지금도 있는 것만, 제목은 걸리면 기본 제목으로.
    sections = []
    for section in out.get("sections") or []:
        section_id = section.get("id")
        if section_id not in available:
            continue
        default_title = defaults_by_id[section_id]["title"]
        title = section.get("title")
        if title != default_title and _clean(title) is None:
            section["title"] = default_title
        sections.append(section)
    out["sections"] = sections

    # 고정 문구.
    out["copy"] = {key: value for key, value in (out.get("copy") or {}).items() if _clean(value)}
    # 이어 보이는 묶음(제목+안내)이 걸리면 그 섹션의 안내를 비우고, 그래도 걸리면 기본 제목으로.
    # 진료 과정 — 안내 문장이 걸리면 기본 안내, 단계 하나라도 걸리면 기본 4단계.
    care_flow = out.setdefault("care_flow", {})
    if _clean(care_flow.get("note")) is None:
        care_flow["note"] = site_defaults.CARE_FLOW_NOTE
    steps = care_flow.get("steps") or []
    if not steps or any(
        forbidden_words(f"{step.get('title', '')} {step.get('body', '')}") for step in steps
    ):
        care_flow["steps"] = [{"title": title, "body": body} for title, body in site_defaults.CARE_STEPS]

    # 이어 보이는 묶음(제목+안내)이 걸리면 그 섹션의 안내를 비우고 기본 제목으로.
    for section in out["sections"]:
        section_id = section["id"]
        combined = " ".join(
            part for part in (section.get("title"), section_note(out, section_id, ctx.texts)) if part
        )
        if combined and forbidden_words(combined):
            out["copy"].pop(f"{section_id}.note", None)
            if section_id == "care_flow":
                care_flow["note"] = site_defaults.CARE_FLOW_NOTE
            section["title"] = defaults_by_id[section_id]["title"]

    # 줄바꿈 — 원문이 그대로이고 걸리지 않는 것만.
    hero = out.setdefault("hero", {})
    kept: dict[str, Any] = {}
    for path, spec in (hero.get("line_breaks") or {}).items():
        source = line_break_source(path, ctx.texts)
        if not source or forbidden_words(source):
            continue
        entry = {
            device: value
            for device, value in (spec or {}).items()
            if isinstance(value, str) and lines_match(value, source) and not forbidden_words(value)
        }
        if entry:
            kept[path] = {"pc": entry.get("pc"), "mobile": entry.get("mobile")}
    hero["line_breaks"] = kept

    # 공유.
    share = out.setdefault("share", {})
    image_id = share.get("image_asset_id")
    if image_id is not None and _as_uuid(image_id) not in ctx.texts.share_image_ids:
        share["image_asset_id"] = None
    share["description"] = _clean(share.get("description"))

    # FAQ — 직접 고른 글은 지금 공개된 승인 질문·답을 실어 보낸다.
    faq = out.setdefault("faq", {})
    entries = []
    for raw_id in faq.get("content_ids") or []:
        content_id = _as_uuid(raw_id)
        entry = ctx.faq_entries.get(content_id) if content_id else None
        if entry:
            entries.append({"id": str(content_id), "question": entry["question"], "answer": entry["answer"]})
    faq["content_ids"] = [entry["id"] for entry in entries]
    # 직접 고른 글이 모두 내려가도 고르지 않은 글을 대신 싣지 않는다(빈 목록 — FAQ 섹션·FAQPage 없음).
    faq["entries"] = entries if faq.get("mode") == "manual" else None

    # 메뉴 — 꺼진 섹션을 가리키는 항목은 뺀다.
    enabled = {section["id"] for section in out["sections"] if section.get("enabled")}
    nav = out.setdefault("nav", {})
    nav["items"] = [
        item
        for item in nav.get("items") or []
        if nav_target(layout, item) in enabled or item in {"website", "call"}
    ]

    out["version"] = version
    return out


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = ("PublicSiteConfigContext", "public_site_config")
