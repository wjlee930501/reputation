"""병원 공개 페이지 섹션 설정(레이아웃 편집기) 3a — 스키마·기본값·검증·공개 정리·미리보기 토큰."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.services import site_defaults
from app.services.site_config_public import PublicSiteConfigContext, public_site_config
from app.services.site_config_validation import (
    SiteConfigContext,
    changed_sections,
    validate_site_config,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_COPY = ROOT / "site" / "lib" / "clinic-template-copy.ts"
SITE_DEFAULTS = ROOT / "site" / "lib" / "site-config-defaults.json"

HEADLINE = "365서귀포삼성정형외과 제주 서귀포 정형외과 전문의 진료"


def _ctx(**overrides) -> SiteConfigContext:
    values = {"hero_headline": HEADLINE, "hero_description": None, "care_principles": None}
    values.update(overrides)
    return SiteConfigContext(**values)


def _codes(check) -> list[str]:
    return [error["code"] for error in check.errors]


# ── 기본값 ─────────────────────────────────────────────────────────


def test_site_defaults_fixture_matches_backend_defaults() -> None:
    """사이트는 이 파일로 기본 순서·제목을 대조한다 — 기본값을 바꾸면 파일도 같이 바꾼다(판도 올린다)."""

    import json

    fixture = json.loads(SITE_DEFAULTS.read_text(encoding="utf-8"))
    assert fixture == {
        "defaults_version": site_defaults.DEFAULTS_VERSION,
        "schema_version": site_defaults.SCHEMA_VERSION,
        "layouts": {layout: site_defaults.defaults(layout) for layout in site_defaults.LAYOUTS},
    }


def test_defaults_match_current_screens() -> None:
    """기본값 = 지금 화면(3b 감사): 좌우 분할·덮개 없음, FAQ 10개, C안 영문 소제목 전부, A·C 진료 항목 설명 없음."""

    for layout in site_defaults.LAYOUTS:
        resolved = site_defaults.defaults(layout)
        assert resolved["hero"]["arrangement"] == "split"
        assert resolved["hero"]["image"]["veil"]["extent"] == "none"
        assert resolved["faq"]["limit"] == 10
        assert resolved["treatments"]["show_description"] is (layout == "B")
        assert resolved["region_tags"]["separator"] == "dot"
    b_align = {s["id"]: s["align"] for s in site_defaults.defaults("B")["sections"]}
    assert {k for k, v in b_align.items() if v == "center"} == {
        "treatments", "doctor", "contents", "faq", "care_flow", "gallery", "facts", "visit"
    }
    a_hours = next(s for s in site_defaults.defaults("A")["sections"] if s["id"] == "hours")
    assert a_hours["align"] == "center"
    c_labels = {s["id"]: s["label"] for s in site_defaults.defaults("C")["sections"]}
    assert all(label for section_id, label in c_labels.items() if section_id != "hero")

    # ③-1 감사: 의료진 사진 비율(A 가로 4:3)·초점(세 안 모두 가운데), 첫 화면 사진 초점(B 위 48%),
    # B 진료 원칙·진료 과정 연한 띠.
    ratio = {layout: site_defaults.defaults(layout)["doctor"]["photo_ratio"] for layout in site_defaults.LAYOUTS}
    assert ratio == {"A": "landscape", "B": "portrait", "C": "portrait"}
    doctor_focus = {layout: site_defaults.defaults(layout)["doctor"]["photo_focus"] for layout in site_defaults.LAYOUTS}
    assert doctor_focus == {layout: {"x": 0.5, "y": 0.5} for layout in site_defaults.LAYOUTS}
    hero_focus = {layout: site_defaults.defaults(layout)["hero"]["image"]["focus"] for layout in site_defaults.LAYOUTS}
    assert hero_focus == {"A": {"x": 0.5, "y": 0.5}, "B": {"x": 0.5, "y": 0.48}, "C": {"x": 0.5, "y": 0.5}}
    b_background = {s["id"]: s["background"] for s in site_defaults.defaults("B")["sections"]}
    assert {k for k, v in b_background.items() if v != "default"} == {"care_principles", "care_flow"}


def _css_rule(path: Path, selector: str) -> str:
    """미디어 쿼리 밖(들여쓰기 없는) 규칙들을 이어 붙인다 — 뒤 규칙이 앞 규칙을 덮으므로 뒤가 마지막에 온다."""

    css = path.read_text(encoding="utf-8")
    rules = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rules, selector
    return "\n".join(rules)


def test_photo_defaults_follow_the_site_css() -> None:
    """기본값은 CSS를 옮긴 것이다 — CSS가 바뀌면 이 테스트가 기본값도 같이 고치라고 알린다."""

    globals_css = ROOT / "site" / "app" / "globals.css"
    layouts = ROOT / "site" / "app" / "[slug]" / "_layouts"
    assert "object-position: center 48%" in _css_rule(globals_css, ".clinic-hero-editorial-image")
    # B 의료진 사진은 ClinicAvatar의 <img>(클래스 없음)에 `.clinic-curator-portrait img`가 붙는다 — 초점 지정 없음.
    assert "object-position" not in _css_rule(globals_css, ".clinic-curator-portrait img")
    avatar = (ROOT / "site" / "app" / "[slug]" / "_components" / "ClinicAvatar.tsx").read_text(encoding="utf-8")
    assert "className" not in re.search(r"<Image[^>]*>", avatar, re.S).group(0)
    assert 'wrapperClassName="clinic-curator-portrait"' in (
        ROOT / "site" / "app" / "[slug]" / "_components" / "DoctorIntro.tsx"
    ).read_text(encoding="utf-8")
    assert "aspect-ratio: 4 / 5" in _css_rule(globals_css, ".clinic-shell .clinic-curator-figure .clinic-curator-portrait")
    assert "aspect-ratio: 4 / 3" in _css_rule(layouts / "layout-a.module.css", ".doctorPhoto")
    assert "aspect-ratio: 3 / 4" in _css_rule(layouts / "layout-c.module.css", ".doctorPhoto")
    for module in ("layout-a.module.css", "layout-c.module.css"):
        assert "object-position" not in _css_rule(layouts / module, ".doctorPhoto")


# ── 옛 판 보호(③-1) ─────────────────────────────────────────────────


def test_defaults_versions_compare_by_date_then_number() -> None:
    parse = site_defaults.parse_defaults_version
    assert parse("2026-11-21.10") > parse("2026-11-21.9")
    assert parse("2026-11-22.1") > parse("2026-11-21.10")
    for broken in (None, "", "2026-11-21", "2026-11-21.x", "26-11-21.1", 3, "2026-1-21.1"):
        assert parse(broken) is None


def _old_snapshot(layout: str, config: dict, *, version: str | None) -> dict:
    """그 판의 기본값으로 저장된 듯한 스냅샷 — 지금 칸에 옛 기본값을 덮는다."""

    snapshot = site_defaults.resolve(layout, config)
    snapshot["defaults_version"] = version
    snapshot["doctor"]["photo_focus"] = {"x": 0.5, "y": 0.2}
    snapshot["doctor"]["photo_ratio"] = "portrait"
    snapshot["hero"]["image"]["focus"] = {"x": 0.5, "y": 0.5}
    for section in snapshot["sections"]:
        section["background"] = "default"
        section["align"] = "left"
    snapshot["faq"]["limit"] = config.get("faq", {}).get("limit", 6)
    return snapshot


def test_old_snapshots_refresh_only_the_fields_the_site_did_not_read() -> None:
    config = {
        "sections": [
            {"id": "doctor", "title": "원장 소개"},
            {"id": "treatments", "enabled": True},
            {"id": "gallery", "enabled": False, "align": "left"},
        ],
        "copy": {"treatments.note": "자주 찾는 진료입니다."},
        "hero": {"blocks": ["phone", "today"]},
        "care_principles": {"card_count": 2},
        "faq": {"mode": "auto", "limit": 4},
        "nav": {"items": ["visit", "doctor", "call"], "mobile_bar": ["call", "map"]},
        "share": {"description": "공유 설명"},
    }
    snapshot = _old_snapshot("B", config, version="2026-11-21.2")
    refreshed = site_defaults.refresh_stale_defaults(snapshot, config)

    assert refreshed is not snapshot and snapshot["doctor"]["photo_focus"] == {"x": 0.5, "y": 0.2}  # 원본 불변
    fresh = site_defaults.resolve("B", config)
    # 아직 읽지 않던 칸 → 그 판의 설정 + 지금 기본값.
    assert refreshed["doctor"] == fresh["doctor"]
    assert refreshed["hero"] == fresh["hero"] and refreshed["hero"]["blocks"] == ["phone", "today"]
    for key in ("treatments", "visit", "region_tags", "type", "article"):
        assert refreshed[key] == fresh[key]
    look = {s["id"]: (s["background"], s["align"], s["label"]) for s in refreshed["sections"]}
    assert look == {s["id"]: (s["background"], s["align"], s["label"]) for s in fresh["sections"]}
    assert look["gallery"][1] == "left"  # 설정에서 직접 바꾼 칸은 남는다
    # 사이트가 이미 읽는 칸 → 스냅샷 그대로(순서·켜기·제목, 문구, 카드 수, FAQ, 메뉴, 공유, 진료 과정).
    assert [(s["id"], s["enabled"], s["title"]) for s in refreshed["sections"]] == [
        (s["id"], s["enabled"], s["title"]) for s in snapshot["sections"]
    ]
    for key in ("copy", "care_flow", "nav", "share"):
        if key == "nav":
            assert {k: v for k, v in refreshed[key].items() if k != "section_index"} == {
                k: v for k, v in snapshot[key].items() if k != "section_index"
            }
        else:
            assert refreshed[key] == snapshot[key]
    assert refreshed["care_principles"]["card_count"] == 2
    assert refreshed["faq"]["limit"] == 4 and refreshed["faq"]["mode"] == "auto"
    assert refreshed["defaults_version"] == "2026-11-21.2"


def test_current_snapshots_are_served_as_saved() -> None:
    snapshot = site_defaults.resolve("A", {"doctor": {"photo_ratio": "square"}})
    snapshot["hero"]["image"]["focus"] = {"x": 0.1, "y": 0.9}  # 저장 뒤 기본값이 바뀐 척
    assert site_defaults.refresh_stale_defaults(snapshot, {}) is snapshot


def test_fields_the_site_already_reads_are_never_refreshed() -> None:
    """FAQ 개수는 `.2`에서 기본값이 6 → 10으로 바뀌었지만 사이트가 이미 읽는다 — 옛 판도 6 그대로."""

    for version in ("2026-11-21.1", None, "broken"):
        snapshot = _old_snapshot("C", {}, version=version)
        assert snapshot["faq"]["limit"] == 6
        assert site_defaults.refresh_stale_defaults(snapshot, {})["faq"]["limit"] == 6
    read_by_site = {"faq.limit", "faq.mode", "faq.content_ids", "care_principles.card_count", "care_flow", "copy",
                    "nav.items", "nav.mobile_bar", "share", "sections.enabled", "sections.title"}
    assert not read_by_site & {path for path, _ in site_defaults.REFRESHED_SINCE}


def test_refresh_survives_unknown_layouts_and_odd_configs() -> None:
    assert site_defaults.refresh_stale_defaults({"layout": "Z"}, {}) == {"layout": "Z"}
    snapshot = _old_snapshot("A", {}, version="2026-11-20.1")
    refreshed = site_defaults.refresh_stale_defaults(snapshot, {"sections": "not-a-list"})
    assert refreshed["doctor"] == site_defaults.defaults("A")["doctor"]
    # 칸 하나가 깨진 옛 설정 — 500 대신 그 칸만 기본값.
    for broken in ({"care_principles": None}, {"hero": None}, {"doctor": {"photo_focus": None}}, {"visit": "x"}):
        refreshed = site_defaults.refresh_stale_defaults(_old_snapshot("B", {}, version="2026-11-21.2"), broken)
        expected = site_defaults.defaults("B")
        assert refreshed["care_principles"]["use_default_cards"] is True
        assert refreshed["hero"] == expected["hero"] and refreshed["doctor"] == expected["doctor"]
        assert refreshed["visit"] == expected["visit"]



def test_one_broken_config_field_keeps_the_others_and_never_breaks_the_public_response() -> None:
    """Codex 2차: 깨진 칸 하나가 다른 칸의 직접 바꾼 값을 지우거나 공개 응답을 500으로 만들지 않는다."""

    good = {"doctor": {"photo_ratio": "square"}, "visit": {"show_transit": False}}
    snapshot = _old_snapshot("B", good, version="2026-11-21.2")
    broken = {**good, "sections": "not-a-list"}
    refreshed = site_defaults.refresh_stale_defaults(snapshot, broken)
    assert refreshed["doctor"]["photo_ratio"] == "square"
    assert refreshed["visit"]["show_transit"] is False

    for odd in (
        {"hero": {"line_breaks": "broken"}},
        {"hero": {"line_breaks": {"hero.headline": "broken"}}},
        {"hero": {"blocks": [1, 2]}, "visit": {"order": "parking"}},
        {"hero": {"image": {"focus": "middle", "veil": [0.3]}}},
        {"sections": [{"id": "gallery", "background": 3, "align": None}, "x", {"id": 5}]},
    ):
        out = site_defaults.refresh_stale_defaults(_old_snapshot("B", {}, version="2026-11-21.2"), odd)
        assert out["hero"]["line_breaks"] == {}
        assert out["hero"]["blocks"] == site_defaults.defaults("B")["hero"]["blocks"]
        assert out["hero"]["image"] == site_defaults.defaults("B")["hero"]["image"]
        assert out["visit"] == site_defaults.defaults("B")["visit"]
        _public(out)  # 예외 없이 정리된다


def test_default_titles_match_the_site_template_copy() -> None:
    source = TEMPLATE_COPY.read_text(encoding="utf-8")

    def ts(name: str) -> str:
        match = re.search(rf"export const {name} = '([^']*)'", source)
        assert match, name
        return match.group(1)

    assert site_defaults.TREATMENT_SECTION_TITLE == ts("TREATMENT_SECTION_TITLE")
    assert site_defaults.DOCTOR_SECTION_TITLE == ts("DOCTOR_SECTION_TITLE")
    assert site_defaults.QUESTIONS_SECTION_TITLE == ts("QUESTIONS_SECTION_TITLE")
    assert site_defaults.FAQ_SECTION_TITLE == ts("FAQ_SECTION_TITLE")
    assert site_defaults.GALLERY_SECTION_TITLE == ts("GALLERY_SECTION_TITLE")
    assert site_defaults.CARE_PRINCIPLES_TITLE == ts("CARE_PRINCIPLES_TITLE")
    assert site_defaults.CARE_FLOW_TITLE == ts("CARE_FLOW_TITLE")
    assert site_defaults.CARE_FLOW_NOTE == ts("CARE_FLOW_NOTE")
    steps = re.findall(r"title: '([^']*)',\n\s+body: '([^']*)'", source.split("CARE_STEPS", 1)[1])
    assert tuple(steps) == site_defaults.CARE_STEPS


@pytest.mark.parametrize("layout", site_defaults.LAYOUTS)
def test_every_layout_starts_with_hero_and_keeps_fixed_sections_on(layout: str) -> None:
    resolved = site_defaults.defaults(layout)
    sections = resolved["sections"]
    assert sections[0]["id"] == "hero"
    for section in sections:
        if section["id"] in site_defaults.FIXED_SECTIONS:
            assert section["enabled"] is True
    assert "doctor" in [section["id"] for section in sections]
    # 설정 {}을 합친 완성본 = 기본값.
    merged = site_defaults.resolve(layout, {})
    assert merged["sections"] == sections
    assert merged["defaults_version"] == site_defaults.DEFAULTS_VERSION


def test_section_index_is_off_by_default_in_b() -> None:
    sections = {s["id"]: s for s in site_defaults.defaults("B")["sections"]}
    assert sections["section_index"]["enabled"] is False


def test_listed_sections_swap_only_among_their_own_slots() -> None:
    merged = site_defaults.merge_sections(
        "B", [{"id": "faq"}, {"id": "treatments", "title": "진료 과목"}]
    )
    order = [s["id"] for s in merged]
    base = list(site_defaults.section_ids("B"))
    # treatments 자리에 faq, faq 자리에 treatments. 나머지는 기본 자리.
    expected = [
        "faq" if sid == "treatments" else "treatments" if sid == "faq" else sid for sid in base
    ]
    assert order == expected
    assert next(s for s in merged if s["id"] == "treatments")["title"] == "진료 과목"


# ── 검증: 형식·섹션 ───────────────────────────────────────────────


def test_empty_config_is_valid_and_resolves_to_defaults() -> None:
    check = validate_site_config("A", {}, _ctx())
    assert check.ok, check.errors
    assert check.config == {}
    assert check.resolved["sections"] == site_defaults.defaults("A")["sections"]


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": True},
        {"sections": [{"id": "nope"}]},
        {"hero": {"title_size": "xl"}},
        {"hero": {"image": {"focus": {"x": 1.5, "y": 0}}}},
        {"faq": {"limit": 30}},
        {"copy": {"hero.title": "x"}},
        {"hero": {"line_breaks": {"faq.question": {"pc": "a"}}}},
        {"care_principles": {"card_count": 4}},
        {"care_flow": {"steps": [{"title": "하나", "body": "본문"}]}},
        {"nav": {"items": ["call", "call"]}},
        {"visit": {"order": ["parking"]}},
    ],
)
def test_schema_rejects_shapes_and_ranges(config) -> None:
    check = validate_site_config("B", config, _ctx())
    assert _codes(check) and set(_codes(check)) == {"SCHEMA"}


def test_layout_must_be_a_b_or_c() -> None:
    assert _codes(validate_site_config("D", {}, _ctx())) == ["LAYOUT_INVALID"]


def test_section_must_exist_in_the_layout_and_appear_once() -> None:
    assert "UNKNOWN_SECTION" in _codes(validate_site_config("A", {"sections": [{"id": "visit"}]}, _ctx()))
    assert "DUPLICATE_SECTION" in _codes(
        validate_site_config("B", {"sections": [{"id": "faq"}, {"id": "faq"}]}, _ctx())
    )


@pytest.mark.parametrize("section_id", ["hero", "facts", "doctor"])
def test_fixed_sections_cannot_be_turned_off(section_id: str) -> None:
    check = validate_site_config("B", {"sections": [{"id": section_id, "enabled": False}]}, _ctx())
    assert "FIXED_SECTION" in _codes(check)


def test_hero_must_stay_first() -> None:
    check = validate_site_config("B", {"sections": [{"id": "faq"}, {"id": "hero"}]}, _ctx())
    assert "FIRST_SECTION" in _codes(check)


def test_attached_sections_cannot_be_moved_apart_from_their_host() -> None:
    moved = validate_site_config(
        "C", {"sections": [{"id": "questions"}, {"id": "care_flow"}]}, _ctx()
    )
    assert "SECTION_ATTACHED" in _codes(moved)
    # 붙은 섹션을 끄거나 제목만 바꾸는 것은 된다.
    ok = validate_site_config("C", {"sections": [{"id": "news", "enabled": False}]}, _ctx())
    assert ok.ok, ok.errors


def test_untitled_sections_reject_titles() -> None:
    check = validate_site_config("B", {"sections": [{"id": "facts", "title": "정보"}]}, _ctx())
    assert "SECTION_TITLE_FIXED" in _codes(check)


# ── 검증: 문구 ───────────────────────────────────────────────────


def test_forbidden_expression_in_a_field_names_the_path_and_word() -> None:
    check = validate_site_config(
        "B", {"sections": [{"id": "treatments", "title": "최고의 진료"}]}, _ctx()
    )
    error = next(e for e in check.errors if e["code"] == "FORBIDDEN_EXPRESSION")
    assert error["path"] == "sections[0].title"
    assert error["words"] == ["최고"]


def test_combined_title_and_note_are_checked_as_one_visible_sentence() -> None:
    # 칸마다는 통과하지만 이어 보이면 '할인·유인'으로 걸린다(설계 Codex 검토 1).
    check = validate_site_config(
        "B",
        {
            "sections": [{"id": "treatments", "title": "타 병원보다"}],
            "copy": {"treatments.note": "저렴합니다"},
        },
        _ctx(),
    )
    error = next(e for e in check.errors if e["code"] == "FORBIDDEN_EXPRESSION")
    assert error["path"] == "sections.treatments"


def test_care_step_title_and_body_are_checked_together() -> None:
    steps = [
        {"title": "타 병원보다", "body": "저렴합니다"},
        {"title": "상담", "body": "증상을 듣습니다"},
    ]
    check = validate_site_config("B", {"care_flow": {"steps": steps}}, _ctx())
    assert [e["path"] for e in check.errors] == ["care_flow.steps[0]"]


def test_filter_sees_across_line_breaks_in_multi_line_fields() -> None:
    # 저장 모양은 앞뒤 공백만 다듬으므로 줄바꿈이 남고, 필터는 이어 붙인 글로 본다.
    check = validate_site_config("B", {"share": {"description": "완치\n보장"}}, _ctx())
    assert "FORBIDDEN_EXPRESSION" in _codes(check)
    # 단어 중간 줄바꿈 — 공백으로 이으면 '완 치 보장'이 되어 빠져나가므로 붙여 이은 글도 본다.
    for value in ("완\n치 보장", "최\n고의 진료"):
        check = validate_site_config("B", {"share": {"description": value}}, _ctx())
        assert "FORBIDDEN_EXPRESSION" in _codes(check), value
        steps = [{"title": "상담", "body": value}, {"title": "진단", "body": "원인을 확인합니다"}]
        assert "FORBIDDEN_EXPRESSION" in _codes(validate_site_config("B", {"care_flow": {"steps": steps}}, _ctx()))
    resolved = site_defaults.resolve("B", {})
    resolved["share"]["description"] = "완\n치 보장"
    assert _public(resolved)["share"]["description"] is None


def test_other_line_separators_cannot_split_a_forbidden_word() -> None:
    for value in ("완\r치 보장", "완\r\n치 보장", "완\u2028치 보장"):
        check = validate_site_config("B", {"share": {"description": value}}, _ctx())
        assert "FORBIDDEN_EXPRESSION" in _codes(check), repr(value)
        resolved = site_defaults.resolve("B", {})
        resolved["share"]["description"] = value
        assert _public(resolved)["share"]["description"] is None


def test_stored_principles_title_and_lede_are_checked_together() -> None:
    principles = {"title": "타 병원보다", "lede": "저렴합니다", "cards": [None, None, None]}
    check = validate_site_config("B", {}, _ctx(care_principles=principles))
    assert [e["path"] for e in check.errors] == ["sections.care_principles"]


def test_section_title_is_checked_with_the_stored_principles_lede() -> None:
    principles = {"title": None, "lede": "저렴합니다", "cards": [None, None, None]}
    check = validate_site_config(
        "B", {"sections": [{"id": "care_principles", "title": "타 병원보다"}]}, _ctx(care_principles=principles)
    )
    assert [e["path"] for e in check.errors] == ["sections.care_principles"]


def test_stored_hero_and_card_pairs_are_checked_too() -> None:
    check = validate_site_config("B", {}, _ctx(hero_headline="타 병원보다", hero_description="저렴합니다"))
    error = next(e for e in check.errors if e["path"] == "hero")
    assert "병원 정보" in error["message"]


def test_resolved_snapshots_the_default_care_flow() -> None:
    resolved = site_defaults.resolve("B", {})
    assert resolved["care_flow"]["note"] == site_defaults.CARE_FLOW_NOTE
    assert [(s["title"], s["body"]) for s in resolved["care_flow"]["steps"]] == list(site_defaults.CARE_STEPS)


# ── 검증: 줄바꿈 ─────────────────────────────────────────────────


def test_line_breaks_may_only_move_breaks_not_change_text() -> None:
    ok = validate_site_config(
        "B",
        {
            "hero": {
                "line_breaks": {
                    "hero.headline": {
                        "pc": "365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료",
                        "mobile": "365서귀포\n삼성정형외과\n제주 서귀포\n정형외과 전문의 진료",
                    }
                }
            }
        },
        _ctx(),
    )
    assert ok.ok, ok.errors
    changed = validate_site_config(
        "B",
        {"hero": {"line_breaks": {"hero.headline": {"pc": "365서귀포삼성정형외과\n제주 최고 정형외과"}}}},
        _ctx(),
    )
    assert _codes(changed) == ["LINES_TEXT_MISMATCH"]


def test_line_breaks_need_a_stored_source_sentence() -> None:
    check = validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.description": {"pc": "설명\n문장"}}}}, _ctx()
    )
    assert _codes(check) == ["LINES_SOURCE_EMPTY"]


def test_line_breaks_on_care_principle_cards_use_the_stored_card() -> None:
    principles = {
        "title": None,
        "lede": None,
        "cards": [{"title": "검사 결과를 쉽게 풀어 드립니다", "body": "어려운 용어 없이 설명합니다."}, None, None],
    }
    check = validate_site_config(
        "B",
        {"hero": {"line_breaks": {"care_principles.cards[0].title": {"pc": "검사 결과를\n쉽게 풀어 드립니다"}}}},
        _ctx(care_principles=principles),
    )
    assert check.ok, check.errors


def test_line_breaks_cannot_change_spacing() -> None:
    source = "아버지가 방에 들어가신다"
    ctx = _ctx(hero_headline=source)
    assert validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.headline": {"pc": "아버지가 방에\n들어가신다"}}}}, ctx
    ).ok
    assert validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.headline": {"pc": "아버지가\n방에 들어가신다"}}}}, ctx
    ).ok
    assert validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.headline": {"pc": "아버지\n가 방에 들어가신다"}}}}, ctx
    ).ok
    joined_wrong = validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.headline": {"pc": "아버지가방에\n들어가신다"}}}}, ctx
    )
    assert _codes(joined_wrong) == ["LINES_TEXT_MISMATCH"]


def test_line_break_lines_are_bounded() -> None:
    check = validate_site_config(
        "B", {"hero": {"line_breaks": {"hero.headline": {"pc": "365\n\n서귀포"}}}}, _ctx()
    )
    assert _codes(check) == ["LINES_INVALID"]


# ── 검증: 참조·메뉴 ──────────────────────────────────────────────


def test_share_image_and_faq_ids_must_be_public_for_this_hospital() -> None:
    photo, other, faq = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ctx = _ctx(share_image_ids=frozenset({photo}), faq_content_ids=frozenset({faq}))
    assert validate_site_config("B", {"share": {"image_asset_id": str(photo)}}, ctx).ok
    assert _codes(validate_site_config("B", {"share": {"image_asset_id": str(other)}}, ctx)) == [
        "SHARE_IMAGE_INVALID"
    ]
    assert validate_site_config("B", {"faq": {"mode": "manual", "content_ids": [str(faq)]}}, ctx).ok
    assert _codes(
        validate_site_config("B", {"faq": {"mode": "manual", "content_ids": [str(other)]}}, ctx)
    ) == ["FAQ_CONTENT_INVALID"]
    assert _codes(validate_site_config("B", {"faq": {"mode": "manual"}}, ctx)) == ["FAQ_MANUAL_EMPTY"]


def test_menu_item_pointing_to_a_disabled_section_is_a_warning() -> None:
    check = validate_site_config("B", {"sections": [{"id": "faq", "enabled": False}], "nav": {"items": ["faq", "call"]}}, _ctx())
    assert check.ok
    assert [w["code"] for w in check.warnings] == ["NAV_TARGET_DISABLED"]


def test_booking_block_without_a_naver_place_is_a_warning_on_b_only() -> None:
    config = {"hero": {"blocks": ["phone", "booking", "location"]}}
    check = validate_site_config("B", config, _ctx(has_naver_place=False))
    assert check.ok
    assert [w["code"] for w in check.warnings] == ["BOOKING_NO_PLACE"]
    assert validate_site_config("B", config, _ctx(has_naver_place=True)).warnings == []
    # 기본 칸에는 '예약'이 없다 — 플레이스가 없어도 경고하지 않는다.
    assert validate_site_config("B", {}, _ctx(has_naver_place=False)).warnings == []


def test_text_context_knows_whether_the_hospital_has_a_naver_place() -> None:
    from types import SimpleNamespace

    from app.services.site_config_refs import text_context

    def ctx_for(**fields):
        return text_context(SimpleNamespace(**{"naver_place_url": None, "naver_place_id": None, **fields}))

    assert ctx_for().has_naver_place is False
    assert ctx_for(naver_place_url=" ").has_naver_place is False
    assert ctx_for(naver_place_url="https://m.place.naver.com/place/1").has_naver_place is True
    assert ctx_for(naver_place_id="1595672233").has_naver_place is True


def test_changed_sections_lists_blocks_sections_and_order() -> None:
    before = site_defaults.resolve("B", {})
    after = site_defaults.resolve(
        "B", {"sections": [{"id": "faq"}, {"id": "treatments", "title": "진료 과목"}], "faq": {"limit": 4}}
    )
    changed = changed_sections(before, after)
    assert "faq" in changed and "sections.treatments" in changed and "sections.order" in changed


# ── 공개 정리 ─────────────────────────────────────────────────────


def _public(resolved, *, layout="B", ctx=None, faq_entries=None, version=3, faq_available=None):
    return public_site_config(
        resolved,
        version=version,
        ctx=PublicSiteConfigContext(
            layout=layout, texts=ctx or _ctx(), faq_entries=faq_entries or {}, faq_available=faq_available
        ),
    )


def test_faq_menu_item_is_dropped_when_the_home_faq_would_be_empty() -> None:
    auto = site_defaults.resolve("B", {"nav": {"items": ["faq", "call"]}})
    assert _public(auto, faq_available=False)["nav"]["items"] == ["call"]
    assert _public(auto, faq_available=True)["nav"]["items"] == ["faq", "call"]
    manual = site_defaults.resolve(
        "B", {"nav": {"items": ["faq", "call"]}, "faq": {"mode": "manual", "content_ids": [str(uuid.uuid4())]}}
    )
    # 고른 글이 모두 내려가 직접 FAQ가 비면 메뉴에서 뺀다.
    assert _public(manual)["nav"]["items"] == ["call"]


def test_no_config_or_a_different_current_layout_is_null() -> None:
    assert _public(None) is None
    assert _public(site_defaults.resolve("A", {}), layout="B") is None
    assert _public(site_defaults.resolve("A", {}), layout=None) is None
    out = _public(site_defaults.resolve("B", {}))
    assert out["layout"] == "B" and out["version"] == 3


def test_public_output_clears_text_the_filter_now_blocks() -> None:
    # 저장 뒤 필터가 바뀌었다고 가정하고 금지 표현이 든 완성본을 직접 만든다.
    resolved = site_defaults.resolve("B", {})
    treatments = next(s for s in resolved["sections"] if s["id"] == "treatments")
    treatments["title"] = "최고의 진료"
    resolved["copy"] = {"faq.note": "할인 이벤트", "gallery.note": "넓은 대기실"}
    resolved["share"]["description"] = "완치 보장"
    resolved["care_flow"]["steps"] = [
        {"title": "상담", "body": "완치 보장"},
        {"title": "진단", "body": "원인을 확인합니다"},
    ]
    out = _public(resolved)
    sections = {s["id"]: s for s in out["sections"]}
    assert sections["treatments"]["title"] == site_defaults.TREATMENT_SECTION_TITLE
    assert out["copy"] == {"gallery.note": "넓은 대기실"}
    assert out["share"]["description"] is None
    assert out["care_flow"]["steps"] == [{"title": t, "body": b} for t, b in site_defaults.CARE_STEPS]


def test_public_output_drops_combined_title_and_note_hits() -> None:
    resolved = site_defaults.resolve("B", {})
    treatments = next(s for s in resolved["sections"] if s["id"] == "treatments")
    treatments["title"] = "타 병원보다"
    resolved["copy"] = {"treatments.note": "저렴합니다"}
    out = _public(resolved)
    assert "treatments.note" not in out["copy"]
    assert next(s for s in out["sections"] if s["id"] == "treatments")["title"] == site_defaults.TREATMENT_SECTION_TITLE


def test_public_output_drops_line_breaks_whose_source_changed() -> None:
    resolved = site_defaults.resolve(
        "B",
        {"hero": {"line_breaks": {"hero.headline": {"pc": "365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료"}}}},
    )
    assert _public(resolved)["hero"]["line_breaks"]["hero.headline"]["pc"]
    changed = _public(resolved, ctx=_ctx(hero_headline="새 문장"))
    assert changed["hero"]["line_breaks"] == {}


def test_public_output_keeps_only_public_share_image_and_faq() -> None:
    photo, gone, faq, down = (uuid.uuid4() for _ in range(4))
    resolved = site_defaults.resolve(
        "B",
        {"share": {"image_asset_id": str(gone)}, "faq": {"mode": "manual", "content_ids": [str(faq), str(down)]}},
    )
    out = _public(
        resolved,
        ctx=_ctx(share_image_ids=frozenset({photo})),
        faq_entries={faq: {"question": "질문?", "answer": "답."}},
    )
    assert out["share"]["image_asset_id"] is None
    assert out["faq"]["content_ids"] == [str(faq)]
    assert out["faq"]["entries"] == [{"id": str(faq), "question": "질문?", "answer": "답."}]
    # 고른 글이 모두 내려가도 고르지 않은 글을 대신 싣지 않는다.
    emptied = _public(resolved, faq_entries={})
    assert emptied["faq"]["mode"] == "manual" and emptied["faq"]["entries"] == []


def test_public_menu_drops_items_for_disabled_sections() -> None:
    resolved = site_defaults.resolve(
        "B", {"sections": [{"id": "faq", "enabled": False}], "nav": {"items": ["faq", "treatments", "call"]}}
    )
    assert _public(resolved)["nav"]["items"] == ["treatments", "call"]


def test_c_layout_contents_menu_points_to_news() -> None:
    resolved = site_defaults.resolve("C", {"nav": {"items": ["contents", "visit"]}})
    assert _public(resolved, layout="C")["nav"]["items"] == ["contents", "visit"]
    off = site_defaults.resolve("C", {"sections": [{"id": "news", "enabled": False}], "nav": {"items": ["contents"]}})
    assert _public(off, layout="C")["nav"]["items"] == []


# ── 미리보기 토큰 ─────────────────────────────────────────────────


def test_preview_token_round_trip_tamper_expiry_and_disabled(monkeypatch) -> None:
    from app.core.config import settings
    from app.services import site_preview_token as tokens

    hospital_id, draft_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "")
    assert tokens.preview_enabled() is False
    with pytest.raises(tokens.PreviewDisabledError):
        tokens.issue_preview_token(hospital_id, draft_id, 1)

    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "preview-secret")
    token, expires_at = tokens.issue_preview_token(hospital_id, draft_id, 7, now=1000)
    claim = tokens.verify_preview_token(token, now=1000 + 10)
    assert claim == tokens.PreviewClaim(hospital_id, draft_id, 7, expires_at)
    assert tokens.verify_preview_token(token, now=expires_at) is None
    body, signature = token.split(".")
    assert tokens.verify_preview_token(f"{body}.{signature[:-1]}A", now=1010) is None
    # 다른 비밀값으로는 열리지 않는다.
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "other")
    assert tokens.verify_preview_token(token, now=1010) is None
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "")
    assert tokens.verify_preview_token(token, now=1010) is None
    # 모양이 틀린 입력(비ASCII 포함)은 예외 없이 None.
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "preview-secret")
    for bad in ("body.한글", "a.b.c", "", "x" * 600, f"{body}.{signature}\n"):
        assert tokens.verify_preview_token(bad, now=1010) is None


def test_preview_payload_points_assets_at_the_token_routes() -> None:
    from app.api.public.site import _preview_asset_urls

    payload = {
        "logo_url": "/api/v1/public/hospitals/demo/logo",
        "photos": [{"url": "/api/v1/public/hospitals/demo/assets/abc"}],
        "physicians": [{"photo_url": "/api/v1/public/hospitals/demo/assets/def"}],
        "hero_image_url": "https://storage.googleapis.com/x.png",
        "other": "/api/v1/public/hospitals/other/assets/abc",
        "contents": "/api/v1/public/hospitals/demo/contents",
    }
    out = _preview_asset_urls(payload, "demo", "tok")
    assert out["logo_url"] == "/api/v1/public/site-preview/tok/logo"
    assert out["photos"][0]["url"] == "/api/v1/public/site-preview/tok/assets/abc"
    assert out["physicians"][0]["photo_url"] == "/api/v1/public/site-preview/tok/assets/def"
    # 다른 병원·외부 주소·자산이 아닌 경로는 그대로.
    assert out["hero_image_url"] == payload["hero_image_url"]
    assert out["other"] == payload["other"]
    assert out["contents"] == payload["contents"]


def test_preview_tokens_are_redacted_from_logs() -> None:
    from app.core.observability import redact_link_tokens

    line = '"GET /api/v1/public/site-preview/eyJoIjoiMSJ9.abcdefghijABCDEFGHIJ0123456789_-abcdefghijk/assets/x HTTP/1.1" 200'
    redacted = redact_link_tokens(line)
    assert "eyJoIjoiMSJ9" not in redacted
    assert "/site-preview/[token]/assets/x" in redacted
    assert redact_link_tokens("/doctor-survey/abcdefghijklmnopqrstu") == "/doctor-survey/[token]"


def test_preview_assets_use_signed_urls_no_longer_than_the_token(monkeypatch) -> None:
    from app.api.public import assets
    from app.api.public import site as public_site

    seen = {}

    def fake_signed_url(ref, expiration_hours=24, response_disposition=None):
        seen["hours"] = expiration_hours
        return "https://storage.googleapis.com/b/x?sig=1"

    monkeypatch.setattr(assets, "get_signed_url", fake_signed_url)
    response = assets.public_asset_response(
        "gs://bucket/x.png",
        hospital_id=uuid.uuid4(),
        media_type=None,
        signed_url_hours=public_site._PREVIEW_SIGNED_URL_HOURS,
    )
    assert response.status_code == 302
    assert seen["hours"] * 60 <= 10
