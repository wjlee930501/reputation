"""AE 수정 판 — 원장용 v4 보고서의 서술 문구·진료 항목 라벨만 고치고 숫자는 그대로(DB 없음).

적용(`apply_ae_overrides`)·화이트리스트와 금지 표현 검증·숫자 보존 판정·이전 버전 승계·원장 PDF 검증·API 계약·
워커 계획 단계를 가상 기록으로 본다. 저장소·DB 경로는 `test_doctor_report_ae_edit_postgres.py`.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import arrow
import pytest
from fastapi import HTTPException
from test_doctor_report_v4 import (
    HOSPITAL_NAME,
    PUBLIC_URL,
    SCENARIOS,
    _records,
    _render_inputs,
    _stored,
    build,
)

from app.api.admin import operations
from app.models.operations import OperationRun, OperationRunState
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.doctor_report_ae_edit import (
    AE_EDIT_DROP_REASON_NUMBERS_CHANGED,
    ae_edit_fact_problems,
    base_view_of,
    build_ae_overrides,
    carry_ae_fields,
    editable_fields,
    editable_summary,
    field_changes,
    merge_ae_fields,
    normalize_ae_fields,
    numbers_changed_between,
    stored_ae_overrides,
    validate_ae_fields,
)
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import (
    AE_PATH_CARD_NAMES,
    DoctorReportV4,
    apply_ae_overrides,
    case_candidates_for_view,
    parse_ae_path,
    relabel_quoted,
    split_card_names,
    treatment_bars_from_appendix,
)
from app.services.monthly_period import ReportBuildReason, plan_report_version
from app.services.monthly_report_snapshot import (
    REPORT_SNAPSHOT_SCHEMA_VERSION,
    ReportSnapshot,
    freeze_doctor_view,
    report_render_inputs,
    restore_doctor_view,
)
from app.services.monthly_template_refresh import doctor_view_facts
from app.workers import tasks

KW_HEART = "가상동 심장초음파 잘하는 병원"


def _records_with_ids():
    """`_records()`는 id가 없는 가상 기록이다 — 사례 후보는 SovRecord id로 고르므로 결정적인 id를 단다."""
    records = _records()
    for index, record in enumerate(records):
        record.id = uuid.UUID(int=index + 1)
    return records
KW_ENDO = "가상동 위내시경 가능한 병원"
LABEL = f"treatment_label[{KW_ENDO}]"


def _plan_keyword(view: DoctorReportV4) -> str:
    return (*view.plan.rows, *view.plan.continued)[0].keyword


# ── 적용 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_treatment_bars_rebuilt_from_appendix_rows_equal_the_builders_bars(name):
    """라벨을 바꿀 때 막대를 부록 행에서 다시 세는 근거 — 바꾸지 않으면 빌더의 막대와 정확히 같다."""
    view = build(**SCENARIOS[name])
    assert treatment_bars_from_appendix(view.appendix.keyword_rows) == view.exposure.treatment_bars


def test_label_override_relabels_every_surface_and_regroups_bars_without_touching_numbers():
    view = build()
    row = next(r for r in view.appendix.keyword_rows if r.keyword == KW_ENDO)
    assert row.treatment == "위내시경"
    plan_row = next(r for r in (*view.plan.rows, *view.plan.continued) if r.keyword == KW_ENDO)
    assert "'위내시경'" in plan_row.action
    edited = apply_ae_overrides(view, {LABEL: "위 내시경 검사"})
    assert next(r for r in edited.appendix.keyword_rows if r.keyword == KW_ENDO).treatment == "위 내시경 검사"
    new_plan_row = next(r for r in (*edited.plan.rows, *edited.plan.continued) if r.keyword == KW_ENDO)
    assert new_plan_row.treatment == "위 내시경 검사"
    assert "'위 내시경 검사'" in new_plan_row.action and "'위내시경'" not in new_plan_row.action
    assert new_plan_row.rate_label == plan_row.rate_label
    # 막대는 새 라벨로 묶이고 숫자(분수)는 같다.
    labels = {bar.label: bar.detail for bar in edited.exposure.treatment_bars}
    assert "위 내시경 검사" in labels and "위내시경" not in labels
    assert labels["위 내시경 검사"] == {b.label: b.detail for b in view.exposure.treatment_bars}["위내시경"]
    # 콘텐츠 표도 같은 키워드면 같은 라벨.
    for before, after in zip(view.content.rows, edited.content.rows, strict=True):
        assert after.treatment == ("위 내시경 검사" if before.keyword == KW_ENDO else before.treatment)
    # 숫자 사실은 진료 항목 묶음 말고 같다.
    assert ae_edit_fact_problems(view, edited) == []
    assert {k: v for k, v in edited.facts().items() if not k.startswith("treatment.")} == {
        k: v for k, v in view.facts().items() if not k.startswith("treatment.")
    }
    assert edited.facts()["treatment.위 내시경 검사"] == view.facts()["treatment.위내시경"]


def test_label_override_rewrites_the_quoted_span_in_rule_sentences_only():
    view = build()
    top = _plan_keyword(view)
    old = next(r for r in view.appendix.keyword_rows if r.keyword == top).treatment
    edited = apply_ae_overrides(view, {f"treatment_label[{top}]": "새 항목"})
    assert edited.summary.key_lines[2] == relabel_quoted(view.summary.key_lines[2], old, "새 항목")
    assert "'새 항목'" in edited.summary.key_lines[2]
    assert edited.summary.key_lines[:2] == view.summary.key_lines[:2]
    # 카드 4 이름도 바뀌고 숫자 칸('{n}개 항목')은 그대로다.
    assert edited.summary.cards[3].value == view.summary.cards[3].value
    assert relabel_quoted("'서울 치료하' 키워드", "치료하", "도수치료") == "'서울 도수치료' 키워드"
    assert relabel_quoted("'치료하' 키워드와 '치료하기'", "치료하", "도수치료") == "'도수치료' 키워드와 '치료하기'"
    assert relabel_quoted("문장 치료하 그대로", "치료하", "도수치료") == "문장 치료하 그대로"


def test_card_names_follow_exposed_keywords_only():
    """카드 4는 노출된 키워드의 진료 항목이다 — 미노출 키워드의 라벨을 고쳐도 카드는 그대로(검토 1-P2-2)."""
    view = build()
    exposed = [r for r in view.appendix.keyword_rows if r.total != "—" and int(r.total.split("/")[0]) > 0]
    unexposed = [r for r in view.appendix.keyword_rows if r.total != "—" and int(r.total.split("/")[0]) == 0]
    assert exposed and unexposed
    same = apply_ae_overrides(view, {f"treatment_label[{unexposed[0].keyword}]": exposed[0].treatment})
    assert same.summary.cards[3].names == view.summary.cards[3].names
    assert same.summary.cards[3].value == view.summary.cards[3].value
    renamed = apply_ae_overrides(view, {f"treatment_label[{exposed[0].keyword}]": "새 노출 항목"})
    names, tail = split_card_names(renamed.summary.cards[3].names)
    assert "새 노출 항목" in names and exposed[0].treatment not in names
    assert tail == split_card_names(view.summary.cards[3].names)[1]
    assert renamed.summary.cards[3].value == view.summary.cards[3].value


def test_text_overrides_replace_sentences_and_keep_every_fact():
    view = build()
    top = _plan_keyword(view)
    found_keyword = view.cases.found.keyword
    fields = {
        "summary.key_lines[0]": "이번 달은 심장초음파 키워드에서 새로 노출됐습니다.",
        "summary.key_lines[1]": "위내시경 키워드는 아직 노출되지 않았습니다.",
        f"plan.action[{top}]": "'가상동 위내시경' 키워드 대응 콘텐츠 1편 발행 예정(안내문 보강)",
        "cases.found.insight": "심장초음파 설명이 출처로 쓰였습니다 → 같은 형식으로 다른 항목도 발행",
        AE_PATH_CARD_NAMES: ["심장초음파", "위내시경"],
    }
    edited = apply_ae_overrides(view, fields)
    assert edited.summary.key_lines[0] == fields["summary.key_lines[0]"]
    assert edited.summary.key_lines[1] == fields["summary.key_lines[1]"]
    assert edited.summary.key_lines[2] == view.summary.key_lines[2]
    assert (*edited.plan.rows, *edited.plan.continued)[0].action == fields[f"plan.action[{top}]"]
    assert edited.cases.found.insight == fields["cases.found.insight"]
    assert edited.cases.found.keyword == found_keyword
    assert edited.cases.missing == view.cases.missing
    _names, tail = split_card_names(view.summary.cards[3].names)
    assert edited.summary.cards[3].names == ("심장초음파", "위내시경", *([tail] if tail else ()))
    assert edited.facts() == view.facts()
    assert ae_edit_fact_problems(view, edited) == []
    assert edited.talking_points == list(edited.summary.key_lines)


def test_apply_ignores_unknown_paths_and_none_and_is_idempotent():
    view = build()
    fields = {"summary.key_lines[0]": "새 문장입니다.", "summary.cards[1].value": "99%", "summary.key_lines[1]": None}
    edited = apply_ae_overrides(view, fields)
    assert edited.summary.cards[1].value == view.summary.cards[1].value
    assert edited.summary.key_lines[1] == view.summary.key_lines[1]
    assert apply_ae_overrides(view, {}) == view
    assert apply_ae_overrides(edited, {"summary.key_lines[0]": "새 문장입니다."}) == edited


def test_case_record_override_swaps_the_case_from_the_months_candidates_only():
    view = build()
    candidates = case_candidates_for_view(view, _records_with_ids())
    assert {(c.found, c.case.keyword) for c in candidates} >= {(True, KW_HEART), (False, KW_ENDO)}
    other_found = next(c for c in candidates if c.found and c.case.keyword != view.cases.found.keyword)
    edited = apply_ae_overrides(view, {"cases.found.record_id": other_found.record_id}, case_candidates=candidates)
    assert edited.cases.found == other_found.case
    assert edited.cases.missing == view.cases.missing
    # 반대 자리의 후보는 받지 않는다(노출 사례 자리에 미노출 기록).
    missing_candidate = next(c for c in candidates if not c.found)
    assert apply_ae_overrides(view, {"cases.found.record_id": missing_candidate.record_id}, case_candidates=candidates) == view
    problems = validate_ae_fields({"cases.found.record_id": missing_candidate.record_id}, view, case_candidates=candidates)
    assert [p.code for p in problems] == ["UNKNOWN_CANDIDATE"]
    assert validate_ae_fields({"cases.found.record_id": other_found.record_id}, view, case_candidates=candidates) == []
    # 라벨을 함께 바꾸면 교체한 사례의 시사점도 새 라벨을 쓴다.
    both = apply_ae_overrides(
        view,
        {"cases.found.record_id": other_found.record_id, f"treatment_label[{other_found.case.keyword}]": "새 라벨"},
        case_candidates=candidates,
    )
    assert "'새 라벨'" in both.cases.found.insight or other_found.case.insight == both.cases.found.insight


# ── 검증 ──────────────────────────────────────────────────────────────────────


def _codes(problems):
    return sorted(problem.code for problem in problems)


def test_whitelist_rejects_numbers_keywords_titles_and_appendix_text():
    view = build()
    for path in (
        "summary.cards[1].value", "summary.cards[3].value", "appendix.keyword_rows[0].keyword",
        "content.rows[0].title", "exposure.platform_bars[0].detail", "appendix.caveat_lines[0]",
        "summary.key_lines[3]", "plan.action[]", "treatment_label[]",
    ):
        assert parse_ae_path(path) is None, path
        assert _codes(validate_ae_fields({path: "x"}, view)) == ["UNKNOWN_FIELD"], path
    assert _codes(validate_ae_fields({"plan.action[없는 키워드]": "문장"}, view)) == ["UNKNOWN_KEYWORD"]
    assert _codes(validate_ae_fields({"treatment_label[없는 키워드]": "라벨"}, view)) == ["UNKNOWN_KEYWORD"]


def test_forbidden_expressions_and_internal_markers_are_rejected():
    view = build()
    assert "FORBIDDEN_EXPRESSION" in _codes(validate_ae_fields({"summary.key_lines[0]": "최고의 심장초음파 병원입니다."}, view))
    assert "FORBIDDEN_EXPRESSION" in _codes(validate_ae_fields({LABEL: "완치 검사"}, view))
    assert "INTERNAL_MARKER" in _codes(validate_ae_fields({"summary.key_lines[1]": "AE 전용 메모입니다."}, view))
    assert validate_ae_fields({"summary.key_lines[0]": "심장초음파 키워드에서 노출됐습니다."}, view) == []


def test_new_numbers_are_rejected_but_numbers_from_the_original_sentence_may_stay():
    view = build()
    original = view.summary.key_lines[1]  # "'…' 등 N개 키워드는 아직 노출되지 않았습니다."
    tokens = [token for token in original.replace("'", " ").split() if any(ch.isdigit() for ch in token)]
    assert tokens, original
    assert validate_ae_fields({"summary.key_lines[1]": original.replace("아직", "이번 달에도")}, view) == []
    assert "NUMBER_NOT_IN_ORIGINAL" in _codes(validate_ae_fields({"summary.key_lines[1]": "노출률이 80%였습니다."}, view))
    assert "NUMBER_NOT_IN_ORIGINAL" in _codes(validate_ae_fields({"summary.key_lines[0]": "콘텐츠 12편을 더 올립니다."}, view))


def test_length_emptiness_control_chars_and_shapes_are_rejected():
    view = build()
    assert _codes(validate_ae_fields({"summary.key_lines[0]": "   "}, view)) == ["EMPTY"]
    assert "TOO_LONG" in _codes(validate_ae_fields({"summary.key_lines[0]": "가" * 201}, view))
    assert "TOO_LONG" in _codes(validate_ae_fields({LABEL: "가" * 41}, view))
    assert "CONTROL_CHARS" in _codes(validate_ae_fields({"summary.key_lines[0]": "한 줄\n두 줄"}, view))
    assert "CONTROL_CHARS" in _codes(validate_ae_fields({"summary.key_lines[0]": "앞\r뒤"}, view))
    assert _codes(validate_ae_fields({"summary.key_lines[0]": 3}, view)) == ["NOT_TEXT"]
    assert _codes(validate_ae_fields({AE_PATH_CARD_NAMES: "심장초음파"}, view)) == ["NOT_LIST"]
    assert "TOO_MANY_NAMES" in _codes(validate_ae_fields({AE_PATH_CARD_NAMES: [f"항목{i}" for i in range(9)]}, view))
    assert validate_ae_fields({"summary.key_lines[0]": None}, view) == []  # 수정 취소
    unavailable = build(**SCENARIOS["unavailable"])
    assert _codes(validate_ae_fields({"cases.found.insight": "문장"}, unavailable)) == ["NO_SUCH_CASE"]


def test_normalize_drops_values_equal_to_the_original_and_merge_removes_with_none():
    view = build()
    originals = editable_fields(view)
    fields = {"summary.key_lines[0]": f"  {originals['summary.key_lines[0]']} ", "summary.key_lines[1]": "새 문장", "x.y": "z"}
    assert normalize_ae_fields(fields, view) == {"summary.key_lines[1]": "새 문장"}
    merged = merge_ae_fields({"summary.key_lines[1]": "옛 문장", LABEL: "라벨"}, {"summary.key_lines[1]": None, "summary.key_lines[2]": "셋"})
    assert merged == {LABEL: "라벨", "summary.key_lines[2]": "셋"}
    changes = field_changes(view, {"summary.key_lines[1]": "옛 문장"}, {"summary.key_lines[2]": "셋"})
    assert changes == [
        {"path": "summary.key_lines[1]", "before": "옛 문장", "after": originals["summary.key_lines[1]"]},
        {"path": "summary.key_lines[2]", "before": originals["summary.key_lines[2]"], "after": "셋"},
    ]


def test_editable_summary_exposes_only_whitelisted_surfaces_with_read_only_numbers():
    view = build()
    summary = editable_summary(view)
    assert summary["key_lines"] == list(view.summary.key_lines)
    assert summary["card_value"] == view.summary.cards[3].value
    assert {row["keyword"] for row in summary["keyword_labels"]} == {r.keyword for r in view.appendix.keyword_rows}
    assert all("rate_label" in row and "action" in row for row in summary["plan_rows"])
    assert summary["cases"]["found"]["keyword"] == view.cases.found.keyword
    assert set(editable_fields(view)) >= {"summary.key_lines[0]", AE_PATH_CARD_NAMES, LABEL, "cases.found.insight"}


# ── 숫자 보존·승계 ───────────────────────────────────────────────────────────


def test_fact_problems_catch_a_view_whose_numbers_moved():
    view = build()
    tampered = replace(view, summary=replace(view.summary, cards=(
        view.summary.cards[0], replace(view.summary.cards[1], value="99%"), *view.summary.cards[2:],
    )))
    assert any("card.rate" in line for line in ae_edit_fact_problems(view, tampered))
    assert numbers_changed_between(view.facts(), tampered.facts())
    assert not numbers_changed_between(view.facts(), apply_ae_overrides(view, {LABEL: "새 라벨"}).facts())


def test_carry_rules_keep_everything_when_numbers_hold_and_only_labels_when_they_change():
    fields = {"summary.key_lines[0]": "문장", LABEL: "라벨", "cases.found.record_id": "rid", AE_PATH_CARD_NAMES: ["a"]}
    assert carry_ae_fields(fields, numbers_changed=False) == (fields, {})
    kept, dropped = carry_ae_fields(fields, numbers_changed=True)
    assert kept == {LABEL: "라벨"}
    assert dropped == {"summary.key_lines[0]": "문장", "cases.found.record_id": "rid", AE_PATH_CARD_NAMES: ["a"]}


def test_build_ae_overrides_keeps_a_bounded_history_and_the_pristine_base_view():
    view = build()
    previous = None
    for version in range(2, 30):
        previous = build_ae_overrides(
            fields={"summary.key_lines[0]": f"문장 {version}"}, base_view_payload=freeze_doctor_view(view),
            edited_by="ae@example.invalid", edited_at=f"2026-11-{version:02d}T00:00:00+00:00",
            previous=previous, changed_paths=["summary.key_lines[0]"], report_version=version,
        )
    assert len(previous["history"]) == 20 and previous["history"][-1]["report_version"] == 29
    inputs = _render_inputs(view).model_copy(update={"ae_overrides": previous})
    assert stored_ae_overrides(inputs) is not None and base_view_of(inputs) == view
    assert stored_ae_overrides(_render_inputs(view)) is None and base_view_of(_render_inputs(view)) == view


def test_snapshot_model_accepts_and_round_trips_ae_overrides():
    view = build()
    edited = apply_ae_overrides(view, {"summary.key_lines[0]": "고친 문장입니다."})
    overrides = build_ae_overrides(
        fields={"summary.key_lines[0]": "고친 문장입니다."}, base_view_payload=freeze_doctor_view(view),
        edited_by="ae@example.invalid", edited_at="2026-11-05T01:00:00+00:00", previous=None,
        changed_paths=["summary.key_lines[0]"], report_version=2,
    )
    inputs = _render_inputs(edited).model_copy(update={"ae_overrides": overrides, "ae_overrides_dropped": None})
    parsed = ReportSnapshot.model_validate(
        ReportSnapshot(schema_version=REPORT_SNAPSHOT_SCHEMA_VERSION, render_inputs=inputs).model_dump(mode="json")
    ).render_inputs
    assert restore_doctor_view(parsed.doctor_view) == edited
    assert base_view_of(parsed) == view
    assert parsed.ae_overrides["fields"] == {"summary.key_lines[0]": "고친 문장입니다."}
    # 옛 스냅샷(키 없음)도 그대로 읽힌다.
    legacy = ReportSnapshot.model_validate({"schema_version": 1, "render_inputs": {k: v for k, v in _render_inputs(view).model_dump(mode="json").items()}})
    assert legacy.render_inputs.ae_overrides is None


def test_ae_edit_version_plan_supersedes_like_a_template_refresh():
    report_id = uuid.uuid4()
    plan = plan_report_version(latest_report_id=report_id, latest_version=3, reason_code=ReportBuildReason.AE_EDIT, correlation_key="k")
    assert (plan.version, plan.supersedes_report_id, plan.create) == (4, report_id, True)
    with pytest.raises(Exception):
        plan_report_version(latest_report_id=None, latest_version=None, reason_code=ReportBuildReason.AE_EDIT, correlation_key="k")


# ── 원장 PDF 검증 ────────────────────────────────────────────────────────────


def test_edited_view_renders_and_passes_the_v4_doctor_pdf_validation():
    view = build()
    top = _plan_keyword(view)
    edited = apply_ae_overrides(view, {
        "summary.key_lines[0]": "심장초음파 키워드에서 병원명이 새로 노출됐습니다.",
        LABEL: "위 내시경 검사",
        f"plan.action[{top}]": "'가상동 위내시경' 키워드 대응 콘텐츠 1편 발행 예정(안내문 보강)",
    })
    rendered = render_validated_doctor_pdf(
        view=edited, period_label=edited.period_ym, public_url=PUBLIC_URL,
        expectation=build_doctor_pdf_expectation(edited, PUBLIC_URL),
    )
    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert doctor_view_facts(edited) == edited.facts()


# ── 워커 계획 단계 ───────────────────────────────────────────────────────────


def _latest(view: DoctorReportV4, scenario: dict | None = None):
    sov, content = _stored(view, scenario or {})
    return SimpleNamespace(id=uuid.uuid4(), version=1, sov_summary=sov, content_summary=content, manifest_id=None)


def test_plan_applies_the_edit_to_the_snapshot_and_keeps_facts():
    view = build()
    latest = _latest(view)
    content = copy.deepcopy(latest.content_summary)
    ae_edit = {
        "report_id": str(latest.id),
        "fields": {"summary.key_lines[0]": "고친 첫 줄입니다.", LABEL: "위 내시경 검사"},
        "edited_by": "ae@example.invalid", "requested_at": "2026-11-05T01:00:00+00:00",
    }
    edited, problems = tasks._apply_ae_edit_to_plan(None, SimpleNamespace(id=uuid.uuid4()), latest, arrow.get("2026-10-31T23:59:00+09:00"), content, view, ae_edit)
    assert problems == []
    assert edited.summary.key_lines[0] == "고친 첫 줄입니다."
    inputs = report_render_inputs(content)
    assert restore_doctor_view(inputs.doctor_view) == edited
    assert inputs.doctor_metric_facts == edited.facts()
    assert inputs.ae_overrides["fields"] == ae_edit["fields"]
    assert inputs.ae_overrides["edited_by"] == "ae@example.invalid"
    assert inputs.ae_overrides["history"][-1]["paths"] == sorted(ae_edit["fields"])
    assert base_view_of(inputs) == view
    assert content["talking_points"] == list(edited.summary.key_lines) == list(inputs.ae_report.talking_points)
    # 다음 템플릿 갱신(요청 값 없음)은 저장된 수정 값을 pristine 뷰에 다시 얹어 같은 뷰를 만든다.
    again = copy.deepcopy(content)
    same, problems = tasks._apply_ae_edit_to_plan(None, SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(**{**latest.__dict__, "content_summary": again, "version": 2}), arrow.get("2026-10-31T23:59:00+09:00"), again, edited, None)
    assert problems == [] and same == edited
    assert report_render_inputs(again).ae_overrides["history"] == inputs.ae_overrides["history"]
    # 수정을 전부 지우면 pristine 뷰로 돌아가고 ae_overrides 키가 사라진다.
    cleared = copy.deepcopy(content)
    back, problems = tasks._apply_ae_edit_to_plan(None, SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(**{**latest.__dict__, "content_summary": cleared, "version": 2}), arrow.get("2026-10-31T23:59:00+09:00"), cleared, edited, {"report_id": str(latest.id), "fields": {"summary.key_lines[0]": None, LABEL: None}})
    assert problems == [] and back == view
    assert report_render_inputs(cleared).ae_overrides is None


def test_plan_blocks_invalid_stale_or_unknown_case_requests_without_touching_the_snapshot():
    view = build()
    latest = _latest(view)
    now = arrow.get("2026-10-31T23:59:00+09:00")
    content = copy.deepcopy(latest.content_summary)
    _same, problems = tasks._apply_ae_edit_to_plan(None, None, latest, now, content, view, {"report_id": str(latest.id), "fields": {"summary.key_lines[0]": "최고의 병원"}})
    assert [(k, c) for k, c, _d in problems] == [("BLOCKER", "AE_EDIT_INVALID")]
    _same, problems = tasks._apply_ae_edit_to_plan(None, None, latest, now, content, view, {"report_id": str(uuid.uuid4()), "fields": {"summary.key_lines[0]": "문장"}})
    assert [c for _k, c, _d in problems] == ["AE_EDIT_STALE_REPORT"]
    _same, problems = tasks._apply_ae_edit_to_plan(None, None, latest, now, content, view, {"report_id": str(latest.id), "fields": {"cases.found.record_id": "없는 기록"}})
    assert [c for _k, c, _d in problems] == ["AE_EDIT_INVALID"] and "UNKNOWN_CANDIDATE" in problems[0][2]
    assert content == latest.content_summary


def test_plan_refuses_when_the_edited_facts_disagree_with_the_stored_summary(monkeypatch):
    """적용 뒤 뷰 사실과 저장 요약 사실이 다르면 DIFF — 수정이 숫자를 흔들지 않아도 저장값 대조는 그대로 선다."""
    from app.services import doctor_report_v4

    view = build()
    latest = _latest(view)
    content = copy.deepcopy(latest.content_summary)
    real = doctor_report_v4.stored_facts_v4
    monkeypatch.setattr(doctor_report_v4, "stored_facts_v4", lambda sov, c: {**real(sov, c), "card.published": "9편"})
    _same, problems = tasks._apply_ae_edit_to_plan(None, None, latest, arrow.get("2026-10-31T23:59:00+09:00"), content, view, {"report_id": str(latest.id), "fields": {"summary.key_lines[0]": "고친 문장"}})
    assert [(k, c) for k, c, _d in problems] == [("DIFF", "AE_EDIT_FACTS")]
    assert content == latest.content_summary


def test_general_rebuild_carries_labels_only_when_numbers_changed_and_everything_otherwise():
    view = build()
    overrides = build_ae_overrides(
        fields={"summary.key_lines[0]": "사람이 쓴 첫 줄", LABEL: "위 내시경 검사"}, base_view_payload=freeze_doctor_view(view),
        edited_by="ae@example.invalid", edited_at="2026-11-05T01:00:00+00:00", previous=None,
        changed_paths=["summary.key_lines[0]", LABEL], report_version=2,
    )
    _sov, content = _stored(apply_ae_overrides(view, overrides["fields"]), {})
    content["report_snapshot"]["render_inputs"]["ae_overrides"] = overrides
    previous = SimpleNamespace(id=uuid.uuid4(), content_summary=content)
    db = SimpleNamespace(get=lambda _model, _pk: previous)
    now = arrow.get("2026-11-01T00:00:00+00:00").datetime
    # 같은 숫자로 다시 만든 판: 전부 잇는다.
    applied, carried, dropped = tasks._carry_ae_overrides(db, previous.id, view, _records_with_ids(), now)
    assert dropped is None and carried["fields"] == overrides["fields"]
    assert applied.summary.key_lines[0] == "사람이 쓴 첫 줄"
    assert carried["edited_by"] == "ae@example.invalid" and carried["history"] == overrides["history"]
    # 숫자가 바뀐 판(발행 편수 4편): 라벨만 잇고 문장은 버린다.
    changed = build(published_count=4, fulfilled_count=4)
    applied, carried, dropped = tasks._carry_ae_overrides(db, previous.id, changed, _records_with_ids(), now)
    assert carried["fields"] == {LABEL: "위 내시경 검사"}
    assert dropped["reason"] == AE_EDIT_DROP_REASON_NUMBERS_CHANGED
    assert dropped["fields"] == {"summary.key_lines[0]": "사람이 쓴 첫 줄"} and dropped["from_report_id"] == str(previous.id)
    assert applied.summary.key_lines[0] == changed.summary.key_lines[0]
    assert next(r for r in applied.appendix.keyword_rows if r.keyword == KW_ENDO).treatment == "위 내시경 검사"
    # 키워드가 사라진 판: 그 라벨도 버린다.
    assert tasks._carry_ae_overrides(db, None, view, _records_with_ids(), now) == (view, None, None)
    setup = build(**SCENARIOS["setup"])
    applied, carried, dropped = tasks._carry_ae_overrides(db, previous.id, setup, [], now)
    assert carried is None and set(dropped["fields"]) == set(overrides["fields"]) and applied == setup


# ── API ───────────────────────────────────────────────────────────────────────


def _context(view: DoctorReportV4, stored=None):
    latest = _latest(view)
    if stored is not None:
        latest.content_summary["report_snapshot"]["render_inputs"]["ae_overrides"] = stored
    render_inputs = report_render_inputs(latest.content_summary)
    report = SimpleNamespace(id=latest.id, version=1, period_year=2026, period_month=10, sov_summary=latest.sov_summary, content_summary=latest.content_summary)
    return operations._AeEditContext(SimpleNamespace(id=uuid.uuid4(), name=HOSPITAL_NAME), report, view, render_inputs, stored_ae_overrides(render_inputs))


@pytest.fixture
def api(monkeypatch):
    view = build()
    context = _context(view)
    candidates = case_candidates_for_view(view, _records_with_ids())
    seen: dict = {}

    async def load_context(_db, _hospital_id, _report_id):
        return context

    async def prepare(*_args, **kwargs):
        seen["audit"] = kwargs
        return True

    async def enqueue(*_args, **kwargs):
        seen["enqueue"] = kwargs
        run = OperationRun(id=uuid.uuid4(), hospital_id=context.hospital.id, operation_type="GENERATE_MONTHLY_REPORT", state=OperationRunState.QUEUED, request_payload={})
        return SimpleNamespace(run=run, replayed=False)

    async def no_replay(*_args, **_kwargs):
        return None

    monkeypatch.setattr(operations, "_find_ae_edit_replay", no_replay)
    monkeypatch.setattr(operations, "_load_ae_edit_context", load_context)
    monkeypatch.setattr(operations, "_load_ae_candidates_sync", lambda *_a: candidates)
    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", enqueue)
    return SimpleNamespace(view=view, context=context, candidates=candidates, seen=seen)


@pytest.mark.asyncio
async def test_preview_returns_editable_fields_candidates_and_problems_without_writing(api):
    payload = await operations.preview_report_ae_edit(api.context.hospital.id, api.context.report.id, overrides=None, db=AsyncMock())
    assert payload["template_version"] == "v4" and payload["overrides"] == {} and payload["preview"] is not None
    assert payload["numbers_preserved"] is True and payload["redelivery_required"] is True
    assert {c["record_id"] for c in payload["candidates"]["found"]} == {c.record_id for c in api.candidates if c.found}
    assert payload["original_fields"]["summary.key_lines[0]"] == api.view.summary.key_lines[0]
    bad = await operations.preview_report_ae_edit(api.context.hospital.id, api.context.report.id, overrides='{"summary.key_lines[0]": "최고의 병원", "summary.cards[1].value": "9%"}', db=AsyncMock())
    assert {p["code"] for p in bad["problems"]} == {"FORBIDDEN_EXPRESSION", "UNKNOWN_FIELD"} and bad["preview"] is None
    good = await operations.preview_report_ae_edit(api.context.hospital.id, api.context.report.id, overrides='{"summary.key_lines[0]": "고친 첫 줄입니다."}', db=AsyncMock())
    assert good["problems"] == [] and good["preview"]["key_lines"][0] == "고친 첫 줄입니다."
    assert good["merged"] == {"summary.key_lines[0]": "고친 첫 줄입니다."}
    with pytest.raises(HTTPException) as caught:
        await operations.preview_report_ae_edit(api.context.hospital.id, api.context.report.id, overrides="{not json", db=AsyncMock())
    assert caught.value.status_code == 400
    assert "audit" not in api.seen and "enqueue" not in api.seen


@pytest.mark.asyncio
async def test_ae_edit_request_dispatches_a_template_refresh_with_the_edit_payload_and_field_audit(api):
    body = operations.ReportAeEditRequest(reason="진료 항목 라벨 정정", fields={LABEL: "위 내시경 검사", "summary.key_lines[0]": "고친 첫 줄입니다."})
    response = await operations.request_report_ae_edit(api.context.hospital.id, api.context.report.id, body, db=AsyncMock(), idempotency_key="ae-edit:k1", actor=SimpleNamespace(email="ae@example.invalid"))
    assert response["redelivery_required"] is True and response["report_id"] == str(api.context.report.id)
    assert {c["path"] for c in response["changes"]} == {LABEL, "summary.key_lines[0]"}
    enqueue = api.seen["enqueue"]
    assert enqueue["args"] == [str(api.context.hospital.id), 2026, 10, True, False, True]
    assert enqueue["idempotency_key"] == "ae-edit:k1"
    ae_edit = enqueue["request_payload_extra"]["ae_edit"]
    assert ae_edit["report_id"] == str(api.context.report.id) and ae_edit["fields"] == body.fields
    assert ae_edit["edited_by"] == "ae@example.invalid"
    audit = api.seen["audit"]
    assert audit["template_only"] is True and audit["mode_detail"]["mode"] == "AE_EDIT"
    assert audit["mode_detail"]["report_id"] == str(api.context.report.id)
    assert {c["path"] for c in audit["mode_detail"]["changes"]} == {LABEL, "summary.key_lines[0]"}
    assert audit["reason"] == "진료 항목 라벨 정정" and (audit["year"], audit["month"]) == (2026, 10)


@pytest.mark.asyncio
async def test_ae_edit_request_rejects_forbidden_values_and_no_op_requests_before_writing(api):
    with pytest.raises(HTTPException) as caught:
        await operations.request_report_ae_edit(api.context.hospital.id, api.context.report.id, operations.ReportAeEditRequest(fields={"summary.key_lines[0]": "최고의 병원입니다."}), db=AsyncMock(), idempotency_key="k", actor=SimpleNamespace(email="ae@example.invalid"))
    assert caught.value.status_code == 422 and caught.value.detail["code"] == "AE_EDIT_REJECTED"
    assert caught.value.detail["problems"][0]["code"] == "FORBIDDEN_EXPRESSION"
    with pytest.raises(HTTPException) as caught:
        await operations.request_report_ae_edit(api.context.hospital.id, api.context.report.id, operations.ReportAeEditRequest(fields={"summary.key_lines[0]": api.view.summary.key_lines[0]}), db=AsyncMock(), idempotency_key="k", actor=SimpleNamespace(email="ae@example.invalid"))
    assert caught.value.status_code == 422 and caught.value.detail["code"] == "AE_EDIT_NO_CHANGES"
    assert "audit" not in api.seen and "enqueue" not in api.seen


@pytest.mark.asyncio
async def test_ae_edit_request_replay_with_different_values_is_a_conflict(api, monkeypatch):
    async def replayed(*_args, **kwargs):
        run = OperationRun(id=uuid.uuid4(), hospital_id=api.context.hospital.id, operation_type="GENERATE_MONTHLY_REPORT", state=OperationRunState.QUEUED, request_payload={"ae_edit": {"fields": {"summary.key_lines[0]": "다른 값"}}})
        return SimpleNamespace(run=run, replayed=True)

    async def prepare(*_args, **_kwargs):
        return False

    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", replayed)
    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    with pytest.raises(HTTPException) as caught:
        await operations.request_report_ae_edit(api.context.hospital.id, api.context.report.id, operations.ReportAeEditRequest(fields={"summary.key_lines[0]": "고친 첫 줄"}), db=AsyncMock(), idempotency_key="k", actor=SimpleNamespace(email="ae@example.invalid"))
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_ae_edit_context_requires_the_latest_v4_version(monkeypatch):
    view = build()
    latest = _latest(view)
    older = SimpleNamespace(id=uuid.uuid4(), hospital_id=uuid.uuid4(), report_type="MONTHLY", period_year=2026, period_month=10, version=1)
    db = AsyncMock()
    db.get = AsyncMock(return_value=older)
    db.scalar = AsyncMock(return_value=SimpleNamespace(id=latest.id))
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=SimpleNamespace(id=older.hospital_id)))
    with pytest.raises(HTTPException) as caught:
        await operations._load_ae_edit_context(db, older.hospital_id, older.id)
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "AE_EDIT_NOT_LATEST"
    v3 = SimpleNamespace(id=latest.id, hospital_id=older.hospital_id, report_type="MONTHLY", period_year=2026, period_month=10, version=1, content_summary={})
    db.get = AsyncMock(return_value=v3)
    db.scalar = AsyncMock(return_value=v3)
    with pytest.raises(HTTPException) as caught:
        await operations._load_ae_edit_context(db, older.hospital_id, v3.id)
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "AE_EDIT_REQUIRES_V4"


def test_task_reads_the_ae_edit_request_from_the_operation_run_payload():
    run_id = uuid.uuid4()
    db = SimpleNamespace(get=lambda _m, _pk: SimpleNamespace(request_payload={"ae_edit": {"report_id": "r", "fields": {"summary.key_lines[0]": "x"}}}))
    assert tasks._ae_edit_request_of(db, run_id) == {"report_id": "r", "fields": {"summary.key_lines[0]": "x"}}
    assert tasks._ae_edit_request_of(SimpleNamespace(get=lambda _m, _pk: SimpleNamespace(request_payload={"_dispatch": {}})), run_id) is None
    assert tasks._ae_edit_request_of(db, None) is None


@pytest.mark.asyncio
async def test_same_key_replay_returns_the_stored_run_before_the_latest_version_check():
    """응답을 잃은 재요청은 새 버전이 이미 생겨도 409가 아니라 기존 실행의 영수증이다(검토 1-P2-4)."""
    report_id = uuid.uuid4()
    run = OperationRun(id=uuid.uuid4(), hospital_id=uuid.uuid4(), operation_type="GENERATE_MONTHLY_REPORT", state=OperationRunState.SUCCEEDED, task_id="t", request_payload={"ae_edit": {"report_id": str(report_id), "fields": {"summary.key_lines[0]": "고친 줄"}}})
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=run)
    receipt = await operations._find_ae_edit_replay(db, run.hospital_id, report_id, "k", {"summary.key_lines[0]": "고친 줄"})
    assert receipt["idempotent_replay"] is True and receipt["operation_run_id"] == str(run.id)
    with pytest.raises(HTTPException) as caught:
        await operations._find_ae_edit_replay(db, run.hospital_id, report_id, "k", {"summary.key_lines[0]": "다른 줄"})
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException):
        await operations._find_ae_edit_replay(db, run.hospital_id, uuid.uuid4(), "k", {"summary.key_lines[0]": "고친 줄"})
    db.scalar = AsyncMock(return_value=None)
    assert await operations._find_ae_edit_replay(db, run.hospital_id, report_id, "k", {}) is None


def test_ae_edit_request_requires_a_human_account():
    """시스템 actor(`X-Admin-Actor-System`)로는 문구를 고칠 수 없다 — 라우트가 사람 계정을 요구한다(검토 1-P1-1)."""
    import inspect

    from app.api.admin.accounts import require_active_account

    signature = inspect.signature(operations.request_report_ae_edit)
    actor = signature.parameters["actor"].default
    assert getattr(actor, "dependency", None) is require_active_account
    assert signature.parameters["idempotency_key"].default is inspect.Parameter.empty
