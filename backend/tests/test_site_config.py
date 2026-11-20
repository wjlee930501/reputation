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
