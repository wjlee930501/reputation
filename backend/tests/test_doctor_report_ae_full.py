"""AE 수정 판 전면 확장(2026-11-05 대표 결정) — 원장 PDF v4의 모든 글자를 고치거나 지우고 숫자도 바꾼다(DB 없음, 실제 렌더·이진 검증).

마케터가 Slack에 남긴 다섯 가지 예가 실제로 되는지를 측정 실패가 있는 픽스처(`test_doctor_pdf_v4._view`, Gemini 측정 실패 셀·
측정 없음 셀 포함)로 증명한다: '실패 N회' 숫자 고치기, '순위나 환자 수 증가를 약속하지 않습니다' 지우기, 비교 안내 문장 지우기,
부록 D 통째로 지우기, '측정 실패' 표시 지우기, 부록 B 지우기, 'AI 답변 n건 중 … 인용 0건' 지우기.
"""

from __future__ import annotations

import re
from io import BytesIO

import pytest
from pypdf import PdfReader
from test_doctor_pdf_v4 import PUBLIC_URL, _view
from test_doctor_report_v4 import SCENARIOS, build

from app.services.doctor_pdf_pagination import PageBreaks
from app.services.doctor_pdf_rendering import render_v4_html, render_validated_doctor_pdf
from app.services.doctor_report_ae_edit import (
    carry_ae_fields,
    editable_fields,
    editor_items,
    empty_section_problems,
    fact_changes,
    field_changes,
    normalize_ae_fields,
    number_changes,
    numbers_differ,
    validate_ae_fields,
)
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import (
    AE_DELETE,
    CAVEAT_LINES,
    DoctorReportV4,
    FigureBand,
    apply_ae_overrides,
    freeze_v4,
    parse_ae_path,
    path_class,
    restore_v4,
    walk_ae_paths,
)
from app.workers import tasks


def _pdf_text(data: bytes) -> str:
    return "".join(("\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)).split())


def _render(view: DoctorReportV4):
    return render_validated_doctor_pdf(
        view=view, period_label=view.period_ym, public_url=PUBLIC_URL,
        expectation=build_doctor_pdf_expectation(view, PUBLIC_URL),
    )


def _edit(view: DoctorReportV4, fields: dict) -> DoctorReportV4:
    assert validate_ae_fields(fields, view) == []
    edited = apply_ae_overrides(view, normalize_ae_fields(fields, view))
    assert empty_section_problems(edited) == []
    return edited


@pytest.fixture(scope="module")
def failure_view() -> DoctorReportV4:
    """10월 장편한 보고서처럼 측정 실패가 있는 달 — Gemini 측정 실패 셀 하나·측정 없음 셀 하나, 부록 B에 '실패 N개'."""
    view = _view("comparable")
    assert any(cell.state == "failed" for row in view.exposure.heatmap for cell in row.cells)
    assert any("실패" in line for line in view.appendix.method_lines)
    return view


# ── 템플릿 문구는 뷰의 기본값이다 ────────────────────────────────────────────────


TEMPLATE_LITERALS = (
    "AI 노출 성과 보고서", "잘된 점", "보완할 점", "다음 달 중점", "병원 안내 페이지 보기", "진료 항목별 발행 편수",
    "발행 콘텐츠 목록", "진료 항목별 AI 노출률", "AI 서비스별 노출률", "질문 그룹별 노출률", "측정 키워드별 노출",
    "주황 노출 · 회색 미노출 · 빗금 측정 실패", "언급 답변 수", "시사점", "다음 달 대응 키워드", "A. 측정 키워드 전체와 결과",
    "칸의 수는 노출 답변 수/AI 답변 수입니다.", "B. 측정 방법", "C. 용어 정의", "D. 유의사항",
)


def test_template_literals_now_come_from_the_view_and_old_snapshots_render_identically(failure_view):
    html = render_v4_html(failure_view, period_label=failure_view.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    squashed = "".join(re.sub(r"<[^>]+>", " ", html).split())  # 렌더러가 낱말을 nowrap span으로 감싼다
    for literal in TEMPLATE_LITERALS:
        assert "".join(literal.split()) in squashed, literal
    # 확장 전 스냅샷(새 필드 없음)은 기본값·파생값으로 채워져 빌더의 뷰와 같은 글자를 그린다.
    payload = freeze_v4(failure_view)
    for section in ("summary", "content", "exposure", "competition", "cases", "plan", "appendix"):
        for key in list(payload[section]):
            if key in (
                "eyebrow", "key_labels", "public_link_label", "bars_heading", "rows_heading", "rows_aside", "table_headers",
                "link_label", "empty_figure", "trend_heading", "platform_heading", "treatment_heading", "group_heading",
                "group_headers", "heatmap_heading", "heatmap_aside", "heatmap_keyword_head", "treatment_empty_text",
                "heatmap_empty_text", "keyword_figure", "replaced_heading", "figure", "insight_label", "continued_caption",
                "continued_heading", "heading", "table_caption", "empty_text", "method_heading", "glossary_heading", "caveat_heading",
            ) and not (section in ("content", "competition", "cases", "plan") and key == "empty_text"):
                payload[section].pop(key)
    for slot in ("found", "missing"):
        if payload["cases"][slot]:
            payload["cases"][slot].pop("caption")
    payload.pop("platform_labels")
    restored = restore_v4(payload)
    assert restored == failure_view
    assert render_v4_html(restored, period_label=restored.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks()) == html


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_fixture_scenario_round_trips_and_lists_its_paths(name):
    view = build(**SCENARIOS[name])
    assert restore_v4(freeze_v4(view)) == view
    paths = [item.path for item in walk_ae_paths(view)]
    assert len(paths) == len(set(paths))
    for path in paths:
        assert parse_ae_path(path) is not None, path
    assert validate_ae_fields({path: value for path, value in editable_fields(view).items()}, view) == []


# ── 마케터 예시 다섯 가지 ─────────────────────────────────────────────────────────


def test_marketer_example_1_failure_count_in_the_method_line_can_be_rewritten(failure_view):
    line_index = next(i for i, line in enumerate(failure_view.appendix.method_lines) if "실패" in line)
    old = failure_view.appendix.method_lines[line_index]
    new = re.sub(r"실패 \d+개", "실패 61회", old)
    assert new != old
    edited = _edit(failure_view, {f"appendix.method_lines[{line_index}]": new})
    assert edited.appendix.method_lines[line_index] == new
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "실패61회" in text and "".join(old.split()) not in text
    # 측정값과 다른 숫자는 표시·기록된다. 사실(`facts`)에는 방법 줄의 수가 없어 사실 변화는 없다.
    assert numbers_differ(old, new)
    assert fact_changes(failure_view, edited) == {}


def test_marketer_example_2_the_promise_caveat_can_be_deleted(failure_view):
    edited = _edit(failure_view, {"appendix.caveat_lines[1]": AE_DELETE})
    assert edited.appendix.caveat_lines == (CAVEAT_LINES[0], "", CAVEAT_LINES[2])
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "약속하지않습니다" not in text
    assert "".join(CAVEAT_LINES[0].split()) in text and "D.유의사항" in text


def test_marketer_example_3_the_comparison_notice_can_be_deleted():
    view = _view("method-changed")
    assert "측정 방식이 바뀌어" in view.summary.notice and "부록 A에 있습니다" in view.summary.notice
    edited = _edit(view, {"summary.notice": AE_DELETE})
    assert edited.summary.notice == ""
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "측정방식이바뀌어" not in text and "지난달수치는부록A에" not in text
    # 지난달 참고 줄은 그대로다(사실 `rate.reference_previous`도 그대로).
    assert "rate.reference_previous" in edited.facts()
    # 참고 줄까지 지우면 그 사실도 따라 빠진다 — postcheck가 PDF에 없는 숫자를 요구하지 않는다.
    without_note = _edit(view, {"summary.notice": AE_DELETE, "appendix.keyword_note": AE_DELETE})
    assert "rate.reference_previous" not in without_note.facts()
    _render(without_note)


def test_marketer_example_4_appendix_d_can_be_deleted_whole_including_its_heading(failure_view):
    edited = _edit(failure_view, {f"appendix.caveat_lines[{i}]": AE_DELETE for i in range(3)})
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "D.유의사항" not in text
    for line in CAVEAT_LINES:
        assert "".join(line.split()) not in text
    assert "C.용어정의" in text
    # 제목만 지워도 소제목은 사라지고 줄은 남는다.
    heading_only = _edit(failure_view, {"appendix.caveat_heading": AE_DELETE})
    text = _pdf_text(_render(heading_only).pdf_bytes)
    assert "D.유의사항" not in text and "".join(CAVEAT_LINES[1].split()) in text


def test_marketer_example_5_the_measurement_failure_marks_can_be_removed(failure_view):
    failed = [
        (row.keyword, platform)
        for row in failure_view.exposure.heatmap
        for platform, cell in zip(failure_view.platforms, row.cells, strict=True)
        if cell.state == "failed"
    ]
    assert failed
    fields: dict = {"exposure.heatmap_aside": "주황 노출 · 회색 미노출"}
    for keyword, platform in failed:
        fields[f"exposure.heatmap[{keyword}].cells[{platform}].state"] = "unmeasured"
    line_index = next(i for i, line in enumerate(failure_view.appendix.method_lines) if "실패" in line)
    fields[f"appendix.method_lines[{line_index}]"] = re.sub(r" / 실패 \d+개", "", failure_view.appendix.method_lines[line_index])
    edited = _edit(failure_view, fields)
    assert all(cell.state != "failed" for row in edited.exposure.heatmap for cell in row.cells)
    html = render_v4_html(edited, period_label=edited.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    assert 'class="heat heat-failed"' not in html
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "측정실패" not in text and "실패" not in text


def test_marketer_example_6_appendix_b_can_be_deleted_whole(failure_view):
    edited = _edit(failure_view, {f"appendix.method_lines[{i}]": AE_DELETE for i in range(len(failure_view.appendix.method_lines))})
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "B.측정방법" not in text and "매달1일새벽" not in text
    assert "A.측정키워드전체와결과" in text and "C.용어정의" in text


def test_marketer_example_7_the_citation_line_can_be_deleted_and_its_fact_follows(failure_view):
    assert "citation" in failure_view.facts()
    edited = _edit(failure_view, {"exposure.citation_lines[0]": AE_DELETE})
    assert "citation" not in edited.facts()
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "콘텐츠인용" not in text
    # 숫자를 고치면 사실도 고친 숫자다.
    rewritten = _edit(failure_view, {"exposure.citation_lines[0]": "출처 확인이 가능한 AI 답변 10건 중 병원 콘텐츠 인용 3건"})
    assert rewritten.facts()["citation"] == "10건 중 3건"


# ── 숫자 ─────────────────────────────────────────────────────────────────────


def test_numbers_change_freely_and_bar_lengths_follow(failure_view):
    bar = failure_view.exposure.platform_bars[0]
    fields = {
        f"exposure.platform_bars[{bar.label}].value": "80",
        f"exposure.platform_bars[{bar.label}].detail": "AI 답변 10건 중 8건",
        "summary.cards[1].value": "80%",
        "content.bars[위내시경].value": 9,
    }
    assert validate_ae_fields(fields, failure_view) == []
    edited = _edit(failure_view, fields)
    html = render_v4_html(edited, period_label=edited.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    assert 'style="width:80.0%"' in html
    assert edited.facts()["platform.chatgpt"] == "80%" and edited.facts()["card.rate"] == "80%"
    changes = fact_changes(failure_view, edited)
    assert {"platform.chatgpt", "platform.chatgpt.answers", "card.rate"} <= set(changes)
    assert changes["card.rate"] == {"before": failure_view.summary.cards[1].value, "after": "80%"}
    recorded = {c["path"] for c in number_changes(failure_view, {}, normalize_ae_fields(fields, failure_view), edited_by="ae", edited_at="t")}
    assert recorded == set(fields)
    audit = {c["path"]: c["numbers_changed"] for c in field_changes(failure_view, {}, normalize_ae_fields(fields, failure_view))}
    assert all(audit.values())
    _render(edited)
    # 비울 수 없는 수·범위 밖·글자는 거절한다.
    problems = {p.path: p.code for p in validate_ae_fields({
        "exposure.trend[8월].value": AE_DELETE, "competition.rows[검증용 가상 A내과].rate": "120", "plan.rows[가상구 심장초음파 잘하는 병원 01].priority": "x",
    }, failure_view)}
    assert problems["exposure.trend[8월].value"] == "NOT_DELETABLE"
    assert problems.get("competition.rows[검증용 가상 A내과].rate") in ("OUT_OF_RANGE", "UNKNOWN_KEY")


def test_deleting_rows_recounts_untouched_derived_counts_but_not_explicit_ones(failure_view):
    title = failure_view.content.rows[0].title
    edited = _edit(failure_view, {f"content.rows[{title}]": AE_DELETE})
    assert len(edited.content.rows) == len(failure_view.content.rows) - 1
    assert edited.content.rows_aside == f"{len(edited.content.rows)}편"
    explicit = _edit(failure_view, {f"content.rows[{title}]": AE_DELETE, "content.rows_aside": "6편(수정)"})
    assert explicit.content.rows_aside == "6편(수정)"
    # 지운 행의 링크는 더는 요구하지 않는다.
    assert failure_view.content.rows[0].url not in edited.required_links()
    _render(edited)


def test_a_section_cannot_be_emptied_and_the_whole_page_rule_is_reported(failure_view):
    fields = {"cases.found": AE_DELETE, "cases.missing": AE_DELETE, "cases.headline": AE_DELETE, "cases.empty_text": AE_DELETE, "cases.empty_figure": AE_DELETE}
    assert validate_ae_fields(fields, failure_view) == []
    edited = apply_ae_overrides(failure_view, normalize_ae_fields(fields, failure_view))
    assert [p.path for p in empty_section_problems(edited)] == ["p5"]
    # 사례 하나만 지우면 다른 사례·시사점이 남아 통과한다.
    _render(_edit(failure_view, {"cases.missing": AE_DELETE}))


def test_figure_bands_and_headings_can_be_deleted_separately(failure_view):
    edited = _edit(failure_view, {"plan.figure": AE_DELETE, "competition.figure.caption": AE_DELETE, "exposure.heatmap_aside": AE_DELETE, "content.bars_heading": AE_DELETE})
    assert edited.plan.figure == FigureBand("", "") and edited.competition.figure.caption == ""
    html = render_v4_html(edited, period_label=edited.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    assert "다음 달 대응 키워드" not in html and "section-aside" not in html.split('id="p3-start"')[1].split('id="p3-end"')[0]
    text = _pdf_text(_render(edited).pdf_bytes)
    assert "진료항목별발행편수" not in text and "다음달대응키워드" not in text


# ── 승계 ───────────────────────────────────────────────────────────────────────


def test_numbers_changed_rebuild_keeps_labels_and_static_copy_only(failure_view):
    keyword = failure_view.appendix.keyword_rows[0].keyword
    fields = {
        f"treatment_label[{keyword}]": "새 라벨",
        "appendix.caveat_lines[1]": AE_DELETE,
        "content.bars_heading": "발행 편수",
        "appendix.glossary[AI 인용].definition": "뜻을 고쳤습니다.",
        "summary.key_labels[0]": "좋았던 점",
        "exposure.platform_bars[ChatGPT].value": 80,
        "summary.key_lines[0]": "사람이 쓴 문장입니다.",
        f"content.rows[{failure_view.content.rows[0].title}]": AE_DELETE,
        "summary.notice": AE_DELETE,
        "plan.figure.value": "9개",
        "cases.found": AE_DELETE,
    }
    kept, dropped = carry_ae_fields(fields, numbers_changed=True)
    assert set(kept) == {f"treatment_label[{keyword}]", "appendix.caveat_lines[1]", "content.bars_heading", "appendix.glossary[AI 인용].definition", "summary.key_labels[0]"}
    assert set(dropped) == set(fields) - set(kept)
    assert path_class("appendix.caveat_lines[1]") == "static" and path_class("summary.notice") == "sentence"
    assert path_class("plan.figure.value") == "cell" and path_class("cases.found") == "structure"
    assert carry_ae_fields(fields, numbers_changed=False) == (fields, {})


def test_competitor_refresh_drops_only_competition_paths_and_competitor_sentences(failure_view):
    stored = {
        "competition.rows[검증용 가상 A내과].rate": 80,
        "competition.figure": AE_DELETE,
        "competition.headline": "경쟁",
        "summary.title": "제목",
        "appendix.caveat_lines[0]": AE_DELETE,
        "summary.key_lines[0]": "검증용 가상 A내과보다 앞섰습니다.",
        "summary.key_lines[1]": "다른 내용의 문장입니다.",
    }
    kept, dropped = tasks._competitor_refresh_carry(stored, failure_view, failure_view, ["검증용 가상 A내과"])
    assert set(dropped) == {"competition.rows[검증용 가상 A내과].rate", "competition.figure", "competition.headline", "summary.key_lines[0]"}
    assert set(kept) == {"summary.title", "appendix.caveat_lines[0]", "summary.key_lines[1]"}


# ── 화면 항목 ───────────────────────────────────────────────────────────────────


def test_editor_items_follow_report_order_and_flag_numbers_and_deletions(failure_view):
    stored = {"summary.cards[1].value": "80%", "appendix.caveat_lines[1]": AE_DELETE, f"content.rows[{failure_view.content.rows[0].title}]": AE_DELETE, "summary.key_lines[0]": "숫자 없는 문장."}
    items = editor_items(failure_view, stored)
    sections = [item["section"] for item in items]
    order = ["p1", "p2", "p3", "p4", "p5", "p6", "appendix"]
    assert [s for i, s in enumerate(sections) if i == 0 or sections[i - 1] != s] == order
    by_path = {item["path"]: item for item in items}
    assert by_path["summary.cards[1].value"]["numbers_changed"] and by_path["summary.cards[1].value"]["value"] == "80%"
    assert by_path["appendix.caveat_lines[1]"]["deleted"] and by_path["appendix.caveat_lines[1]"]["value"] == CAVEAT_LINES[1]
    assert by_path[f"content.rows[{failure_view.content.rows[0].title}].title"]["row_deleted"]
    assert not by_path["summary.key_lines[0]"]["numbers_changed"]  # 숫자를 뺀 문장은 '다름'이 아니다
    assert by_path["treatment_label[" + failure_view.appendix.keyword_rows[0].keyword + "]"]["deletable"] is False
    assert all(item["kind"] and item["type"] for item in items)


def test_parent_deletion_beats_stored_child_edits_and_blank_glossary_hides_its_heading(failure_view):
    edited = _edit(failure_view, {"plan.figure.value": "99개", "plan.figure": AE_DELETE, "cases.found.insight": "x", "cases.found": AE_DELETE})
    assert edited.plan.figure == FigureBand("", "") and edited.cases.found is None
    blank = _edit(failure_view, {f"appendix.glossary[{term}].{col}": AE_DELETE for term, _d in failure_view.appendix.glossary for col in ("term", "definition")})
    html = render_v4_html(blank, period_label=blank.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    assert "C. 용어 정의" not in html and blank.appendix.glossary_heading not in blank.required_texts()[6]
    _render(blank)
    # 발췌만 남긴 P5는 빈 절이 아니다(Codex 3-P2-2).
    only_excerpt = {"cases.missing": AE_DELETE, "cases.headline": AE_DELETE, "cases.insight_label": AE_DELETE}
    only_excerpt.update({f"cases.found.{attr}": AE_DELETE for attr in ("label", "keyword", "caption", "insight")})
    kept = _edit(failure_view, only_excerpt)
    assert kept.cases.found.excerpt and empty_section_problems(kept) == []
    # 값을 지운 카드 4에는 이름 상한이 없다(Codex 3-P2-5).
    assert validate_ae_fields({"summary.cards[3].value": AE_DELETE, "summary.cards[3].names": [f"항목{i}" for i in range(6)]}, failure_view) == []


# ── Codex 1차 반영 ─────────────────────────────────────────────────────────────


def test_legacy_plan_action_alias_and_new_path_never_leave_two_overrides_on_one_cell(failure_view):
    from app.services.doctor_report_ae_edit import merge_ae_fields

    keyword = failure_view.plan.rows[0].keyword
    merged = merge_ae_fields({f"plan.action[{keyword}]": "오전 수정"}, {f"plan.rows[{keyword}].action": "오후 수정"})
    assert merged == {f"plan.rows[{keyword}].action": "오후 수정"}
    edited = apply_ae_overrides(failure_view, normalize_ae_fields(merged, failure_view))
    assert edited.plan.rows[0].action == "오후 수정"
    items = {item["path"]: item for item in editor_items(failure_view, {f"plan.action[{keyword}]": "오전 수정"})}
    assert items[f"plan.rows[{keyword}].action"]["value"] == "오전 수정" and items[f"plan.rows[{keyword}].action"]["overridden"]


def test_duplicate_titles_get_distinct_row_keys(failure_view):
    from dataclasses import replace as _replace

    rows = failure_view.content.rows
    twin = _replace(failure_view, content=_replace(failure_view.content, rows=(rows[0], _replace(rows[1], title=rows[0].title), *rows[2:])))
    paths = [item.path for item in walk_ae_paths(twin)]
    assert len(paths) == len(set(paths))
    # 같은 제목은 콘텐츠 URL의 글 id로 가른다 — 다음 버전에서 순서가 바뀌어도 같은 글을 가리킨다(Codex 3-P1-A).
    ident = lambda row: row.url.rstrip("/").rsplit("/", 1)[-1][-8:]  # noqa: E731
    first, second = f"content.rows[{rows[0].title}#{ident(rows[0])}]", f"content.rows[{rows[0].title}#{ident(rows[1])}]"
    assert f"{first}.published_on" in paths and f"{second}.published_on" in paths
    edited = _edit(twin, {f"{second}.published_on": "10-31", first: AE_DELETE})
    assert edited.content.rows[0].title == rows[0].title and edited.content.rows[0].published_on == "10-31"
    assert len(edited.content.rows) == len(rows) - 1
    swapped = _replace(twin, content=_replace(twin.content, rows=(twin.content.rows[1], twin.content.rows[0], *rows[2:])))
    again = _edit(swapped, {f"{second}.published_on": "10-31", first: AE_DELETE})
    assert [r.url for r in again.content.rows][: 1] == [rows[1].url] and again.content.rows[0].published_on == "10-31"
    # 실제 제목이 만든 키와 같으면 접미를 더 붙여 비킨다(Codex 2-P1-6).
    triple = _replace(twin, content=_replace(twin.content, rows=(twin.content.rows[0], twin.content.rows[1], _replace(rows[2], title=f"{rows[0].title}#{ident(rows[1])}"), *rows[3:])))
    keys = [item.context for item in walk_ae_paths(triple) if item.kind == "content.rows.title"]
    assert len(keys) == len(set(keys)) and keys[2] == f"{rows[0].title}#{ident(rows[1])}" and keys[1] != keys[2]


def test_hidden_elements_are_not_required_and_card_rows_are_not_deletable(failure_view):
    # 시사점을 다 지우면 패널·소제목이 사라지고 필수 문구에서도 빠진다.
    edited = _edit(failure_view, {"cases.found.insight": AE_DELETE, "cases.missing.insight": AE_DELETE})
    assert edited.cases.insight_label not in edited.required_texts()[4]
    _render(edited)
    # 추이를 한 점만 남기면 그래프가 사라지고 남은 값은 요구하지 않는다(사실에서도 빠진다).
    gone = _edit(failure_view, {f"exposure.trend[{failure_view.exposure.trend[0].label}]": AE_DELETE, f"exposure.trend[{failure_view.exposure.trend[1].label}]": AE_DELETE})
    assert len(gone.exposure.trend) == 1 and not any(k.startswith("trend.") for k in gone.facts())
    _render(gone)
    # 막대 값을 비우면 '—'가 찍히고 옛 글자는 요구하지 않는다.
    bar = failure_view.content.bars[0]
    blank = _edit(failure_view, {f"content.bars[{bar.label}].value": AE_DELETE})
    assert blank.content.bars[0].value is None
    assert 'class="bar-value muted">—' in render_v4_html(blank, period_label=blank.period_ym, public_url=PUBLIC_URL, density="comfortable", breaks=PageBreaks())
    _render(blank)
    # 히트맵 칸 글자는 필수 문구이고, 지난달 참고치는 참고 줄의 숫자를 따른다.
    row = failure_view.exposure.heatmap[0]
    cell = _edit(failure_view, {f"exposure.heatmap[{row.keyword}].cells[{failure_view.platforms[0]}].detail": "9/9"})
    assert "9/9" in cell.required_texts()[2]
    _render(cell)
    reference = _view("method-changed")
    renoted = _edit(reference, {"appendix.keyword_note": reference.appendix.keyword_note.replace("31%", "77%")})
    assert renoted.facts()["rate.reference_previous"] == "77%"
    # 카드는 행 단위로 지울 수 없고(칸만), 셀 수 없는 긴 경로·거대한 수는 예외 없이 거절한다.
    card_items = [item for item in walk_ae_paths(failure_view) if item.row == "summary.cards[0]"]
    assert card_items and not any(item.row_deletable for item in card_items)
    problems = {p.code for p in validate_ae_fields({"summary.cards[0]": AE_DELETE, "summary.cards[" + "9" * 5000 + "].label": "x", f"plan.rows[{failure_view.plan.rows[0].keyword}].priority": 10**400, "summary.cards[0].names": ["a"] * 60}, failure_view)}
    assert problems == {"UNKNOWN_FIELD", "NOT_A_NUMBER", "TOO_MANY_ITEMS"}
    # 카드 값을 '9개 항목'으로 고치면 이름 6개도 받는다.
    assert validate_ae_fields({"summary.cards[3].value": "9개 항목", "summary.cards[3].names": [f"항목{i}" for i in range(6)]}, failure_view) == []
