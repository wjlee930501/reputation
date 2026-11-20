"""병원 공개 페이지 섹션 설정(레이아웃 편집기)의 레이아웃별 기본값.

설정(`hospital_site_configs.config`)은 이 기본값과 **다른 칸만** 담는다. 서버가 둘을 합친 완성본
(`resolved`)을 저장 시점에 스냅샷으로 남기고, 공개 응답은 그 스냅샷을 공개용으로 다시 정리해 낸다
(`site_config_public.py`). 기본값을 바꾸면 `DEFAULTS_VERSION`을 올린다 — 옛 공개 판은 스냅샷으로
그대로 재현되고, 복원할 때만 지금 기본값에 다시 합친다.

섹션 표는 2026-11-21 기준 사이트 홈 마크업을 그대로 옮긴 것이다(`site/app/[slug]/page.tsx`,
`_layouts/HomeLayoutA.tsx`·`HomeLayoutC.tsx`). 기본 제목은 `site/lib/clinic-template-copy.ts`와
같은 원문이다(`tests/test_site_defaults.py`가 사이트 파일과 대조한다).

- B(As-is 발전형)는 레이아웃 도입 전 화면과 같은 섹션·순서다. 독립 '진료시간' 섹션이 없어 첫 화면과
  기본 정보가 대신한다. 바로가기 메뉴(`section_index`)는 2026-11-20 결정 12a로 기본 꺼짐이다.
- A는 '자주 확인하는 질문'이 콘텐츠 영역(`contents`) 자체이고, 오시는 길이 기본 정보 안에 있다.
- C는 최근 소식(`news`)과 진료시간(`hours`)이 첫 화면 안에 붙어 있고, 진료 과정(`care_flow`)이 진료
  원칙(`care_principles`)과 한 섹션이다 — 붙은 섹션(`attached_to`)은 따로 옮길 수 없다.
"""

from __future__ import annotations

import copy
from typing import Any, Final

DEFAULTS_VERSION: Final = "2026-11-21.1"
SCHEMA_VERSION: Final = 1
LAYOUTS: Final = ("A", "B", "C")

TREATMENT_SECTION_TITLE: Final = "진료 영역"
DOCTOR_SECTION_TITLE: Final = "의료진 소개"
QUESTIONS_SECTION_TITLE: Final = "자주 확인하는 질문"
FAQ_SECTION_TITLE: Final = "자주 묻는 질문과 답변 요약"
GALLERY_SECTION_TITLE: Final = "병원 공간"
CARE_PRINCIPLES_TITLE: Final = "증상의 원인부터 확인합니다"
CARE_FLOW_TITLE: Final = "첫 방문부터 경과 관리까지, 진료 4단계"
CARE_FLOW_NOTE: Final = "상담과 진료 확인을 바탕으로 단계적으로 진행합니다."
CARE_PRINCIPLES_LEDE: Final = (
    "어디가 왜 불편한지 먼저 살피고, 검사 결과와 치료 방법을 말씀드린 뒤 치료를 시작합니다."
)
CARE_STEPS: Final = (
    ("상담·문진", "증상과 불편한 점, 생활 습관과 병력을 충분히 듣고 확인합니다."),
    ("진단·원인 확인", "진찰 소견을 바탕으로 필요한 검사를 안내하고 증상의 원인을 확인합니다."),
    ("치료 계획 상담", "확인된 결과를 바탕으로 치료 선택지를 설명하고 함께 결정합니다."),
    ("경과 확인·생활 관리", "치료 후 경과를 확인하고 일상에서의 관리 방법을 안내합니다."),
)

# 끌 수 없는 섹션(위치만 바꾼다). 의료진은 공개 조건을 통과한 전원을 보인다(결정 S3·I4).
FIXED_SECTIONS: Final = frozenset({"hero", "facts", "hours", "doctor"})
# 첫 화면은 언제나 맨 앞이다(h1 하나).
FIRST_SECTION: Final = "hero"

# 섹션 제목을 바꿀 수 없는 칸 — 제목이 병원 이름에서 만들어지거나(기본 정보) 첫 화면 h1이다.
UNTITLED_SECTIONS: Final = frozenset({"hero", "section_index", "facts"})


def _section(
    section_id: str,
    title: str | None,
    *,
    enabled: bool = True,
    background: str = "default",
    align: str = "left",
    label: bool = False,
    attached_to: str | None = None,
) -> dict[str, Any]:
    return {
        "id": section_id,
        "enabled": enabled,
        "title": title,
        "background": background,
        "align": align,
        "label": label,
        "attached_to": attached_to,
    }


_SECTIONS: Final[dict[str, tuple[dict[str, Any], ...]]] = {
    "B": (
        _section("hero", None),
        _section("section_index", None, enabled=False),
        _section("treatments", TREATMENT_SECTION_TITLE),
        _section("doctor", DOCTOR_SECTION_TITLE),
        _section("care_principles", CARE_PRINCIPLES_TITLE),
        _section("contents", "먼저 정리한 글"),
        _section("questions", QUESTIONS_SECTION_TITLE),
        _section("faq", FAQ_SECTION_TITLE),
        _section("care_flow", CARE_FLOW_TITLE),
        _section("gallery", GALLERY_SECTION_TITLE),
        _section("facts", None),
        _section("visit", "오시는 길·방문 안내"),
    ),
    "A": (
        _section("hero", None),
        _section("treatments", TREATMENT_SECTION_TITLE, align="center"),
        _section("doctor", DOCTOR_SECTION_TITLE, align="center"),
        _section("care_principles", CARE_PRINCIPLES_TITLE, align="center"),
        _section("hours", "오늘 진료", background="band"),
        _section("contents", QUESTIONS_SECTION_TITLE),
        _section("faq", FAQ_SECTION_TITLE),
        _section("care_flow", CARE_FLOW_TITLE, align="center"),
        _section("gallery", GALLERY_SECTION_TITLE),
        _section("facts", None),
    ),
    "C": (
        _section("hero", None),
        _section("news", "최근 의료 정보", attached_to="hero"),
        _section("hours", "진료시간", attached_to="hero"),
        _section("treatments", TREATMENT_SECTION_TITLE, background="tint", label=True),
        _section("doctor", DOCTOR_SECTION_TITLE, label=True),
        _section("care_principles", CARE_PRINCIPLES_TITLE, background="band"),
        _section("care_flow", CARE_FLOW_TITLE, background="band", attached_to="care_principles"),
        _section("questions", QUESTIONS_SECTION_TITLE, label=True),
        _section("faq", FAQ_SECTION_TITLE, label=True),
        _section("gallery", GALLERY_SECTION_TITLE, label=True),
        _section("facts", None),
    ),
}

_COMMON: Final[dict[str, Any]] = {
    "hero": {
        "arrangement": "overlay",
        "title_size": "m",
        "line_breaks": {},
        "blocks": ["today", "phone", "location", "hours"],
        "image": {"focus": {"x": 0.5, "y": 0.5}, "veil": {"opacity": 0.6, "extent": "text"}},
    },
    "copy": {},
    "treatments": {"show_description": True, "style": "cards"},
    "doctor": {
        "photo_ratio": "portrait",
        "photo_focus": {"x": 0.5, "y": 0.2},
        "title_position": "above",
        "show_philosophy": False,
    },
    "care_principles": {"card_count": 3, "use_default_cards": True},
    # 완성본에는 그때의 기본 4단계·안내 문장을 그대로 담는다 — 나중에 기본 문구가 바뀌어도 옛 공개 판이
    # 그대로 재현된다(`resolved` 스냅샷).
    "care_flow": {
        "note": CARE_FLOW_NOTE,
        "steps": [{"title": title, "body": body} for title, body in CARE_STEPS],
    },
    "visit": {"order": ["parking", "transit"], "show_transit": True},
    "faq": {"mode": "auto", "limit": 6, "content_ids": [], "merge_questions": False},
    "region_tags": {"show": True, "separator": "comma"},
    "nav": {
        "items": ["treatments", "visit", "doctor", "contents", "website", "call"],
        "mobile_bar": ["call", "hours", "guide", "map"],
        "section_index": False,
    },
    "share": {"image_asset_id": None, "description": None},
    "type": {"body_size": "normal"},
    "article": {"aside": "bottom"},
}

_LAYOUT_OVERRIDES: Final[dict[str, dict[str, Any]]] = {
    "A": {"hero": {"arrangement": "overlay"}},
    "B": {"hero": {"arrangement": "overlay"}},
    "C": {"hero": {"arrangement": "split"}},
}


def section_ids(layout: str) -> tuple[str, ...]:
    return tuple(section["id"] for section in _SECTIONS[layout])


def default_sections(layout: str) -> list[dict[str, Any]]:
    return copy.deepcopy(list(_SECTIONS[layout]))


def defaults(layout: str) -> dict[str, Any]:
    """레이아웃의 완성본 기본값(설정 `{}`을 합친 것과 같다)."""

    if layout not in _SECTIONS:
        raise ValueError(f"unknown layout: {layout}")
    base = copy.deepcopy(_COMMON)
    _deep_merge(base, copy.deepcopy(_LAYOUT_OVERRIDES[layout]))
    return {"layout": layout, "sections": default_sections(layout), **base}


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict) and key not in _REPLACED_DICTS:
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


# 통째로 바꾸는 사전 — 키 단위로 합치면 지운 항목이 기본값에서 되살아난다.
_REPLACED_DICTS: Final = frozenset({"copy", "line_breaks"})


def merge_sections(layout: str, overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기본 섹션 위에 설정의 섹션을 얹는다.

    설정에 나온 섹션끼리만 설정 순서대로 자리를 바꾸고, 나오지 않은 섹션은 기본 자리를 지킨다.
    붙은 섹션이 자기 자리를 지키는지는 검증이 본다(`site_config_validation`).
    """

    by_id = {section["id"]: section for section in default_sections(layout)}
    base_order = [section["id"] for section in _SECTIONS[layout]]
    listed = [item["id"] for item in overrides if item.get("id") in by_id]
    listed_set = set(listed)
    slots = iter(listed)
    order = [next(slots) if section_id in listed_set else section_id for section_id in base_order]
    for item in overrides:
        section = by_id.get(item.get("id"))
        if section is None:
            continue
        for key, value in item.items():
            if key in {"id", "attached_to"} or value is None:
                continue
            section[key] = value
    return [by_id[section_id] for section_id in order]


def resolve(layout: str, config: dict[str, Any]) -> dict[str, Any]:
    """기본값 + 설정 → 완성본. 설정은 검증을 통과한 저장 모양이어야 한다."""

    resolved = defaults(layout)
    patch = {key: value for key, value in config.items() if key != "sections"}
    _deep_merge(resolved, patch)
    resolved["sections"] = merge_sections(layout, list(config.get("sections") or []))
    resolved["defaults_version"] = DEFAULTS_VERSION
    resolved["schema_version"] = SCHEMA_VERSION
    return resolved


__all__ = (
    "CARE_FLOW_NOTE",
    "CARE_PRINCIPLES_LEDE",
    "CARE_FLOW_TITLE",
    "CARE_STEPS",
    "DEFAULTS_VERSION",
    "FIRST_SECTION",
    "FIXED_SECTIONS",
    "LAYOUTS",
    "SCHEMA_VERSION",
    "UNTITLED_SECTIONS",
    "defaults",
    "merge_sections",
    "resolve",
    "section_ids",
)
