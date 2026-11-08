"""원장용 월간 보고서 v4 — 뷰 모델 빌더·스냅샷 왕복·템플릿 갱신 대조(가상 기록만, DB 없음).

설계: docs/plans/2026-11-04-doctor-report-v4-design.md §3~§5·§8·§9.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_doctor_report_view import BANNED_IN_DOCTOR_COPY
from test_report_redesign import monthly_view

from app.services.doctor_report_v4 import (
    CAVEAT_LINES,
    MEASUREMENT_INCOMPLETE,
    NOT_MEASURED_YET,
    PLAN_ROWS_IN_BODY,
    DoctorReportV4,
    build_doctor_report_v4,
    compare_facts_v4,
    freeze_v4,
    is_v4_payload,
    pdf_tokens_v4,
    restore_v4,
    stored_facts_v4,
)
from app.services.monthly_report_snapshot import (
    REPORT_SNAPSHOT_SCHEMA_VERSION,
    FrozenAeRenderInput,
    FrozenHospitalRenderInput,
    FrozenMeasurementRenderInput,
    FrozenPeriodRenderInput,
    ReportRenderInputs,
    ReportSnapshot,
    freeze_doctor_view,
    restore_doctor_view,
)
from app.services.monthly_template_refresh import (
    _pdf_tokens,
    compare_doctor_facts,
    doctor_view_expectations,
    doctor_view_facts,
    facts_are_v4,
    pdf_fact_problems,
    stored_doctor_facts,
    stored_doctor_view_is_v4,
)
from app.services.query_target_structure import (
    INTENT_AVAILABILITY,
    INTENT_COST,
    INTENT_RECOMMENDATION,
    INTENT_SYMPTOM,
)
from app.utils.medical_filter import check_forbidden

HOSPITAL_NAME = "가상동 튼튼내과의원"
PUBLIC_URL = "https://fictional.example.invalid/tuntun"
PLATFORMS = ("chatgpt", "gemini")

# 지시문 6-2·6-3의 옛 표현 — v4 전용 금지어(§5.4). '약속드리지'는 부록 D에만 허용된다.
BANNED_IN_V4 = [
    "우리 병원", "언급 비율", "올린 글", "출처로 쓰인 답변", "다듬겠습니다", "약속드리지", "원장님,",
]
# 2026-11-06 마케팅팀이 모든 보고서에서 빼 달라고 한 부정적·단정적·어색한 문구(9개 예시).
MARKETING_REMOVED_COPY = [
    "직접 비교하지", "지난달 수치는 부록 A에", "환자 수가 아닙니다", "약속하지 않습니다", "예정 사항입니다", "측정 실패",
    "같은 방식으로 묻습니다", "고정 키워드로만", "달라질 수 있습니다", "인용 0건", "출처 확인이 가능한", "측정이 없었다는",
    "확인하지 못했습니다. 0건이라는", "참고만 합니다",
]


# ── 가상 입력 ─────────────────────────────────────────────────────────────────


def _hospital(**overrides):
    base = dict(
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        name=HOSPITAL_NAME,
        slug="tuntun",
        aeo_domain=None,
        region=["가상동"],
        hero_specialties=["심장초음파"],
        treatments=[{"name": "위내시경"}],
        specialties=["내과"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _target(
    text: str, *, treatment: str | None, intent: str, target_id: uuid.UUID | None = None,
    status: str = "ACTIVE", in_tracking_set: bool = True,
):
    return SimpleNamespace(
        id=target_id or uuid.uuid4(),
        name=text,
        variants=[SimpleNamespace(query_text=text, is_active=True)],
        treatment=treatment,
        condition_or_symptom=None,
        specialty=None,
        target_intent=intent,
        region_terms=["가상동"],
        status=status,
        in_tracking_set=in_tracking_set,
    )


# 기준 설정 달의 계획 행에 들어가면 안 되는 타깃 — 보관됐거나 추적 세트 밖이다.
EXCLUDED_TARGETS = (
    ("가상동 보관된 질문", "보관항목", "ARCHIVED", True),
    ("가상동 추적 밖 질문", "추적밖항목", "ACTIVE", False),
)


KEYWORDS = {
    # 문장: (진료 항목, 의도, chatgpt (k, n), gemini (k, n))
    "가상동 심장초음파 잘하는 병원": ("심장초음파", INTENT_RECOMMENDATION, (3, 3), (2, 3)),
    "가상동 위내시경 가능한 병원": ("위내시경", INTENT_AVAILABILITY, (0, 3), (0, 3)),
    "가슴이 답답할 때 가상동 병원": ("흉통", INTENT_SYMPTOM, (1, 3), (0, 3)),
    "가상동 건강검진 비용": ("건강검진", INTENT_COST, (0, 3), (0, 3)),
}
TARGET_IDS = {text: uuid.uuid4() for text in KEYWORDS}


def _targets():
    return [
        _target(text, treatment=treatment, intent=intent, target_id=TARGET_IDS[text])
        for text, (treatment, intent, _c, _g) in KEYWORDS.items()
    ]


def _coverage(
    *, comparison_reason="MATCHED_COHORT", prior=30.0, sov_pct=None, keywords=KEYWORDS, significance=None,
    unmatched=0, cohort_attempts=None, cohort_mentioned=None,
):
    queries, cells = [], []
    total_k = total_n = 0
    by_platform = {p: [0, 0] for p in PLATFORMS}
    for index, (text, (_t, _i, chat, gem)) in enumerate(keywords.items()):
        k = chat[0] + gem[0]
        n = chat[1] + gem[1]
        total_k += k
        total_n += n
        queries.append({
            "query_key": f"q{index}", "query_text": text, "query_intent": "LOCAL",
            "attempts_used": n, "mentioned_attempts": k, "measured_count": 2,
            "mentioned_count": int(chat[0] > 0) + int(gem[0] > 0),
        })
        for platform, (pk, pn) in zip(PLATFORMS, (chat, gem), strict=True):
            by_platform[platform][0] += pk
            by_platform[platform][1] += pn
            cells.append({
                "query_key": f"q{index}", "query_text": text, "query_intent": "LOCAL",
                "platform": platform, "state": "SUCCESS" if pn else "FAILED",
                "attempts_used": pn, "mentioned_attempts": pk,
                "mention_frequency": (pk / pn) if pn else None,
            })
    current = round(total_k / total_n * 100, 2) if total_n else None
    if sov_pct is not None:
        current = sov_pct
    comparable = comparison_reason == "MATCHED_COHORT" and current is not None
    return {
        "sov_pct": current,
        "significance": significance,
        "attempts_used": total_n,
        "mentioned_attempts": total_k,
        "planned_count": len(cells),
        "success_count": sum(c["state"] == "SUCCESS" for c in cells),
        "failed_count": sum(c["state"] == "FAILED" for c in cells),
        "excluded_count": 0,
        "platforms": [
            {
                "platform": platform,
                "mention_rate": round(k / n * 100, 2) if n else None,
                "attempts_used": n, "mentioned_attempts": k,
                "planned_count": 4, "success_count": 4, "failed_count": 0, "excluded_count": 0,
            }
            for platform, (k, n) in by_platform.items()
        ],
        "queries": queries,
        "cells": cells,
        "comparison": {
            "status": "COMPARABLE" if comparable else "NON_COMPARABLE",
            "reason": comparison_reason,
            "matched_cell_count": len(cells) if comparable else 0,
            "current_unmatched_cell_count": unmatched if comparable else 0,
            "current_sov_pct": current if comparable else None,
            "prior_sov_pct": prior if comparable else None,
            "current_attempts_used": (cohort_attempts if cohort_attempts is not None else total_n) if comparable else 0,
            "current_mentioned_attempts": (cohort_mentioned if cohort_mentioned is not None else total_k) if comparable else 0,
        },
    }


# 지난달 언급 수(키워드별). 신규 노출 = 이번 달 노출 ∧ 지난달 0, 이탈 = 이번 달 미노출 ∧ 지난달 >0.
PRIOR_MENTIONED_DEFAULT = {
    "가상동 심장초음파 잘하는 병원": 0,  # 이번 달 노출 → 신규 노출
    "가상동 위내시경 가능한 병원": 2,  # 이번 달 미노출 → 이탈
    "가슴이 답답할 때 가상동 병원": 1,  # 지난달에도 노출 → 신규 아님
    "가상동 건강검진 비용": 0,  # 지난달에도 미노출 → 이탈 아님
}
# 신규 노출도 이탈도 없는 달.
PRIOR_MENTIONED_STEADY = {
    "가상동 심장초음파 잘하는 병원": 3, "가상동 위내시경 가능한 병원": 0,
    "가슴이 답답할 때 가상동 병원": 1, "가상동 건강검진 비용": 0,
}


def _attribution(*, prior: bool, prior_mentioned=None):
    prior_mentioned = prior_mentioned or PRIOR_MENTIONED_DEFAULT
    rows = []
    for text, (_t, _i, chat, gem) in KEYWORDS.items():
        rows.append({
            "query_text": text, "prior_measured": prior, "prior_comparable": prior,
            "prior_attempts_used": 6 if prior else 0,
            "prior_mentioned_attempts": prior_mentioned[text] if prior else 0,
            "current_attempts_used": chat[1] + gem[1],
            "current_mentioned_attempts": chat[0] + gem[0],
        })
    return {"has_prior_month": prior, "question_rows": rows}


CONTENT_ID = uuid.UUID("00000000-0000-4000-8000-00000000c001")
CITATIONS = {
    "measured_cell_count": 8, "cited_cell_count": 2, "cited_content_count": 1,
    "cited_items": [{
        "content_id": str(CONTENT_ID), "title": "심장초음파 검사 안내", "cited_cell_count": 2,
        "queries": [{"query_text": "가상동 심장초음파 잘하는 병원", "platform_label": "ChatGPT"}],
    }],
}


def _content(title, *, target_text=None, day=3, keyword=None, content_id=None):
    return SimpleNamespace(
        id=content_id or uuid.uuid4(),
        hospital_id=_hospital().id,
        title=title,
        status="PUBLISHED",
        first_published_at=datetime(2026, 10, day, 1, tzinfo=timezone.utc),
        published_at=datetime(2026, 10, day, 1, tzinfo=timezone.utc),
        query_target_id=TARGET_IDS[target_text] if target_text else None,
        content_brief={"target_keyword": keyword} if keyword else {},
    )


def _contents():
    return [
        _content("심장초음파 검사 안내", target_text="가상동 심장초음파 잘하는 병원", day=3, content_id=CONTENT_ID),
        _content("위내시경 전 준비", target_text="가상동 위내시경 가능한 병원", day=10),
        _content("환절기 건강 관리", day=17, keyword="환절기"),
    ]


def _record(text, *, mentioned, platform="chatgpt", raw=None, competitors=None, matched=None, source_urls=None):
    raw_text = raw or (
        f"가상동에서 {('심장초음파' if '심장' in text else '진료')}를 받으려면 "
        f"{HOSPITAL_NAME if mentioned else '가상동 다른의원'}을 고려해 볼 수 있습니다. "
        "예약 전 진료 시간을 확인하세요."
    )
    return SimpleNamespace(
        query=SimpleNamespace(query_text=text),
        is_mentioned=mentioned,
        measurement_status="SUCCESS",
        raw_response=raw_text,
        matched_text=matched,
        ai_platform=platform,
        measured_at=datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc),
        competitor_mentions=competitors or [],
        source_urls=source_urls or [],
    )


def _records(*, competitors=True):
    named = [{"name": "가상동 다른의원", "is_mentioned": True}, {"name": "가상 중앙의원", "is_mentioned": False}]
    quiet = [{"name": "가상동 다른의원", "is_mentioned": False}, {"name": "가상 중앙의원", "is_mentioned": False}]
    return [
        # 고른 답변 자신의 출처가 발행 콘텐츠 URL일 때만 P5가 인용을 말한다(검토 7-5).
        _record("가상동 심장초음파 잘하는 병원", mentioned=True, competitors=quiet if competitors else None,
                matched=HOSPITAL_NAME, source_urls=[f"https://tuntun.reputation.motionlabs.kr/contents/{CONTENT_ID}"]),
        _record("가상동 위내시경 가능한 병원", mentioned=False, competitors=named if competitors else None),
        _record("가슴이 답답할 때 가상동 병원", mentioned=True, platform="gemini",
                competitors=quiet if competitors else None),
        _record("가상동 건강검진 비용", mentioned=False, platform="gemini",
                competitors=quiet if competitors else None),
    ]


def build(**overrides) -> DoctorReportV4:
    values = dict(
        hospital=_hospital(),
        period_year=2026,
        period_month=10,
        public_url=PUBLIC_URL,
        report_kind="MONTHLY",
        sov_coverage=_coverage(),
        attribution=_attribution(prior=True),
        citations=CITATIONS,
        published_contents=_contents(),
        query_targets=_targets(),
        records=_records(),
        plan_quota=12,
        published_count=3,
        fulfilled_count=3,
        cumulative_published_count=27,
        comparison_reason="MATCHED_COHORT",
        reference_prev_sov_pct=None,
        v0_baseline={"of_hundred": 20, "current_of_hundred": 50, "sentence": "참고"},
        trend_points=(("2026-08", 20.0), ("2026-09", 30.0)),
        platforms=PLATFORMS,
    )
    values.update(overrides)
    return build_doctor_report_v4(**values)


def with_legacy_copy(view: DoctorReportV4) -> DoctorReportV4:
    """2026-11-06 공통 문구 정리 **전** 빌더가 만들던 모양 — 10월분처럼 옛 문구를 얼린 스냅샷을 흉내 낸다(테스트 전용).

    부록 B 네 줄(실패 횟수 포함)·부록 D 세 줄·옛 범례·옛 표 설명, 비교하지 않은 달의 1면 안내 줄, '—' 첫 칸의 측정 실패 빗금,
    인용 줄의 옛 문구(인용이 없으면 '인용 0건'/'출처 미확인' 줄)를 되살린다.
    """
    from dataclasses import replace

    from app.services.doctor_report_v4 import (
        LEGACY_APPENDIX_TABLE_CAPTION,
        LEGACY_CAVEAT_LINES,
        LEGACY_CITATION_UNAVAILABLE_LINE,
        LEGACY_GLOSSARY_DEFINITIONS,
        LEGACY_HEATMAP_ASIDE,
        LEGACY_REFERENCE_NOTE_TAIL,
        METHOD_COMPARISON_LINE,
        METHOD_VARIABILITY_LINE,
        REFERENCE_NOTE_TAIL,
    )

    names = "·".join(view.platform_labels) or "ChatGPT·Gemini"
    if view.report_kind == "SETUP":
        method = (f"다음 달부터 측정 키워드를 {names}에 같은 방식으로 묻습니다.", METHOD_COMPARISON_LINE, METHOD_VARIABILITY_LINE)
    else:
        method = (
            f"측정 키워드 {len(view.appendix.keyword_rows)}개를 {names}에 매달 1일 새벽에 같은 방식으로 묻습니다.",
            "키워드·AI 서비스 조합 계획 8개 / 확인 7개 / 실패 1개",
            METHOD_COMPARISON_LINE,
            METHOD_VARIABILITY_LINE,
        )
    marked = False
    rows = []
    for row in view.exposure.heatmap:
        cells = []
        for cell in row.cells:
            if not marked and cell.state == "unmeasured" and view.report_kind != "SETUP":
                cells.append(replace(cell, state="failed"))
                marked = True
            else:
                cells.append(cell)
        rows.append(replace(row, cells=tuple(cells)))
    citation = view.exposure.citation_lines
    if view.report_kind != "SETUP":
        if view.citation_measured is not None:
            citation = (
                f"출처 확인이 가능한 AI 답변 {view.citation_measured}건 중 {view.hospital_name} 콘텐츠 인용 {view.citation_cited or 0}건",
            )
        else:
            citation = (LEGACY_CITATION_UNAVAILABLE_LINE,)
    notice = ""
    if "지난달 AI 노출률" in (view.appendix.keyword_note or ""):  # 참고 줄은 비교하지 않은 달에만 생긴다
        notice = "측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다. 지난달 수치는 부록 A에 있습니다."
    return replace(
        view,
        summary=replace(view.summary, notice=notice),
        exposure=replace(view.exposure, heatmap=tuple(rows), heatmap_aside=LEGACY_HEATMAP_ASIDE, citation_lines=citation),
        appendix=replace(
            view.appendix,
            method_lines=method,
            caveat_lines=LEGACY_CAVEAT_LINES,
        glossary=tuple((term, LEGACY_GLOSSARY_DEFINITIONS.get(term, d)) for term, d in view.appendix.glossary),
            table_caption=LEGACY_APPENDIX_TABLE_CAPTION,
            keyword_note=(view.appendix.keyword_note or "").replace(REFERENCE_NOTE_TAIL, LEGACY_REFERENCE_NOTE_TAIL),
        ),
    )


SCENARIOS = {
    "comparable": {},
    "first": dict(
        sov_coverage=_coverage(comparison_reason="NO_PRIOR_MANIFEST"),
        attribution=_attribution(prior=False), comparison_reason="NO_PRIOR_MANIFEST", trend_points=(),
    ),
    "method_reference": dict(
        sov_coverage=_coverage(comparison_reason="MEASUREMENT_POLICY_CHANGED"),
        comparison_reason="MEASUREMENT_POLICY_CHANGED", reference_prev_sov_pct=40.0, trend_points=(),
    ),
    "unavailable": dict(
        sov_coverage={
            **_coverage(comparison_reason="NO_MATCHED_CELLS", sov_pct=None), "sov_pct": None,
            "queries": [], "cells": [], "attempts_used": 0, "mentioned_attempts": 0,
            "success_count": 0, "failed_count": 8,
            "platforms": [
                {"platform": p, "mention_rate": None, "attempts_used": 0, "mentioned_attempts": 0}
                for p in PLATFORMS
            ],
        },
        attribution=None, citations={"measured_cell_count": 0, "cited_items": []},
        comparison_reason="NO_MATCHED_CELLS", records=[], trend_points=(),
    ),
    "setup": dict(
        report_kind="SETUP", sov_coverage=None, attribution=None,
        citations={"measured_cell_count": 0, "cited_items": []}, records=[],
        comparison_reason="NO_PRIOR_MANIFEST", v0_baseline=None, trend_points=(),
    ),
}


def _strings(value, path=""):
    """뷰 안의 모든 문자열을 (경로, 값)으로 — 금지어·필터 검사용."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


# ── P1 카드·핵심 3줄 ──────────────────────────────────────────────────────────


def test_comparable_month_cards_bind_pooled_numbers_and_last_month():
    view = build()
    cards = {card.label: card for card in view.summary.cards}
    assert view.summary.title == "2026년 10월 AI 노출 성과 보고서"
    assert view.summary.subtitle == f"{HOSPITAL_NAME} · 2026년 10월"
    assert cards["발행 콘텐츠"].value == "3편"
    assert cards["발행 콘텐츠"].definition == "계획 12편 중 3편 발행 · 누적 27편"
    # 6/24 = 25% — 풀드 k/n 그대로, 비교한 달은 지난달이 정의에 붙는다(R12-3).
    assert cards["AI 노출률"].value == "25%"
    assert cards["AI 노출률"].definition == f"AI 답변 24건 중 6건에 {HOSPITAL_NAME} 노출 · 지난달 30%"
    assert cards["노출 키워드"].value == "4개 중 2개"
    assert cards["노출 키워드"].definition == "측정 키워드 4개 중 2개에서 병원명 노출"
    assert cards["노출 진료 항목"].value == "2개 항목"
    assert cards["노출 진료 항목"].names == ("심장초음파", "흉통")
    assert view.summary.notice == ""
    assert (view.current_rate, view.previous_rate, view.reference_previous_rate) == (25.0, 30.0, None)


def test_comparable_month_uses_the_matched_cohort_denominator_and_notes_unmatched_cells():
    view = build(sov_coverage=_coverage(unmatched=2, cohort_attempts=18, cohort_mentioned=5))
    cards = {card.label: card for card in view.summary.cards}
    assert cards["AI 노출률"].definition == f"AI 답변 18건 중 5건에 {HOSPITAL_NAME} 노출 · 지난달 30%"
    assert view.exposure.platform_note
    assert view.exposure.platform_note in view.required_texts()[2]
    plain = build()
    assert plain.exposure.platform_note == ""
    assert "AI 답변 24건 중 6건" in {c.label: c for c in plain.summary.cards}["AI 노출률"].definition


def test_key_lines_prefer_new_exposure_then_lost_then_focus():
    view = build()
    good, weak, focus = view.summary.key_lines
    assert good == "'가상동 심장초음파 잘하는 병원' 등 1개 키워드에서 새로 노출됐습니다."
    assert weak == "'가상동 위내시경 가능한 병원' 등 1개 키워드에서 노출이 끊겼습니다."
    # P6 1순위(이탈 키워드)는 고도화 행이라 발행 편수를 지어내지 않는다.
    assert focus == "'위내시경' 기존 콘텐츠를 고도화하고 재측정합니다."
    assert view.talking_points == list(view.summary.key_lines)


def test_key_lines_fall_back_to_risen_rate_then_citation_then_count():
    # 신규 노출·이탈 없음 + 유의한 상승 판정 없음 → 인용 문장, 미노출 문장
    cited = build(attribution=_attribution(prior=True, prior_mentioned=PRIOR_MENTIONED_STEADY))
    assert cited.summary.key_lines[0] == f"{HOSPITAL_NAME} 콘텐츠가 AI 답변 2건에 인용됐습니다."
    assert cited.summary.key_lines[1] == "'가상동 건강검진 비용' 등 2개 키워드는 아직 노출되지 않았습니다."
    # 지난달보다 높아도 측정의 유의성 판정(SIGNIFICANT_UP)이 있어야 '올랐습니다'를 쓴다.
    higher = build(
        sov_coverage=_coverage(prior=10.0),
        attribution=_attribution(prior=True, prior_mentioned=PRIOR_MENTIONED_STEADY),
    )
    assert higher.summary.key_lines[0] == f"{HOSPITAL_NAME} 콘텐츠가 AI 답변 2건에 인용됐습니다."
    up = build(
        sov_coverage=_coverage(prior=10.0, significance="SIGNIFICANT_UP"),
        attribution=_attribution(prior=True, prior_mentioned=PRIOR_MENTIONED_STEADY),
    )
    assert up.summary.key_lines[0] == "AI 노출률이 10%에서 25%로 올랐습니다."
    plain = build(
        sov_coverage=_coverage(comparison_reason="NO_PRIOR_MANIFEST"),
        attribution=_attribution(prior=False), comparison_reason="NO_PRIOR_MANIFEST",
        citations={"measured_cell_count": 0, "cited_items": []},
    )
    assert plain.summary.key_lines[0] == "측정 키워드 4개 중 2개에서 노출됐습니다."


def test_method_changed_month_keeps_last_month_out_of_the_body_and_in_the_appendix():
    view = build(**SCENARIOS["method_reference"])
    # 비교하지 않은 달도 1면에 '직접 비교하지 않았습니다' 안내 줄을 두지 않는다(2026-11-06 마케팅팀 요청) — 증감만 숨긴다.
    assert view.summary.notice == ""
    without = build(**{**SCENARIOS["method_reference"], "reference_prev_sov_pct": None})
    assert without.summary.notice == "" and without.reference_previous_rate is None
    unverified = build(
        sov_coverage=_coverage(comparison_reason="NO_MATCHED_CELLS"), comparison_reason="NO_MATCHED_CELLS",
        reference_prev_sov_pct=40.0, trend_points=(),
    )
    assert unverified.summary.notice == ""
    assert unverified.reference_previous_rate == 40.0
    cards = {card.label: card for card in view.summary.cards}
    assert "지난달" not in cards["AI 노출률"].definition
    assert (view.previous_rate, view.reference_previous_rate) == (None, 40.0)
    assert "지난달 AI 노출률 40%" in view.appendix.keyword_note
    assert "처음 측정 20%" in view.appendix.keyword_note
    assert all(row.previous == "" for row in view.appendix.keyword_rows)
    assert view.exposure.trend == ()


def test_first_month_has_no_notice_and_no_previous():
    view = build(**SCENARIOS["first"])
    assert view.summary.notice == ""
    assert (view.previous_rate, view.reference_previous_rate) == (None, None)
    assert view.appendix.keyword_note == "처음 측정 20% (참고)"


def test_unavailable_month_never_writes_zero_for_unknown():
    view = build(**SCENARIOS["unavailable"])
    cards = {card.label: card for card in view.summary.cards}
    assert cards["AI 노출률"].value == MEASUREMENT_INCOMPLETE
    assert cards["노출 키워드"].value == MEASUREMENT_INCOMPLETE
    assert cards["노출 진료 항목"].value == MEASUREMENT_INCOMPLETE
    assert cards["발행 콘텐츠"].value == "3편"
    assert view.current_rate is None
    assert view.summary.key_lines[0] == "이번 달은 측정이 충분히 이뤄지지 않았습니다."
    assert all(bar.value is None and bar.detail == MEASUREMENT_INCOMPLETE for bar in view.exposure.platform_bars)
    assert view.exposure.citation_lines == ()  # 출처를 확인하지 못한 달은 인용 줄을 두지 않는다(2026-11-06)
    facts = view.facts()
    assert "rate.current" not in facts and "card.rate" not in facts and "citation" not in facts
    assert not any(key.startswith("platform.") for key in facts)
    assert "0%" not in {card.value for card in view.summary.cards}


def test_setup_report_says_next_month_everywhere_and_invents_no_numbers():
    targets = [
        *_targets(),
        *(
            _target(text, treatment=treatment, intent=INTENT_RECOMMENDATION, status=status, in_tracking_set=tracked)
            for text, treatment, status, tracked in EXCLUDED_TARGETS
        ),
    ]
    view = build(**SCENARIOS["setup"], query_targets=targets)
    assert view.report_kind == "SETUP"
    cards = {card.label: card for card in view.summary.cards}
    assert [cards[label].value for label in ("AI 노출률", "노출 키워드", "노출 진료 항목")] == [NOT_MEASURED_YET] * 3
    assert view.summary.key_lines[:2] == ("측정 키워드와 방식을 정했습니다.", "다음 달 첫 측정으로 기준점을 만듭니다.")
    assert view.summary.key_lines[2].startswith("'") and "첫 측정 결과를 확인합니다." in view.summary.key_lines[2]
    assert all(row.rate_label == NOT_MEASURED_YET for row in (*view.plan.rows, *view.plan.continued))
    plan_keywords = [row.keyword for row in (*view.plan.rows, *view.plan.continued)]
    assert sorted(plan_keywords) == sorted(KEYWORDS)  # 보관·추적 밖 타깃은 빠진다
    assert all(row.kind == "first" for row in view.plan.rows)
    assert view.competition.empty_text and view.cases.empty_text
    assert view.appendix.keyword_rows == ()
    assert view.appendix.keyword_note == ""
    assert view.facts() == {
        "template": "v4", "card.published": "3편", "card.plan": "12편 중 3편", "card.cumulative": "27편",
    }


# ── P2·P3·P4·P5 ────────────────────────────────────────────────────────────────


def test_content_rows_join_treatment_and_keyword_from_targets_and_briefs():
    view = build()
    rows = view.content.rows
    assert [row.published_on for row in rows] == ["10-03", "10-10", "10-17"]
    # 진료 항목은 타깃 조인에서만, 타깃 키워드는 브리프에서만 온다 — 서로 대신하지 않는다.
    assert [(row.treatment, row.keyword) for row in rows] == [
        ("심장초음파", "일반"), ("위내시경", "일반"), ("일반", "환절기"),
    ]
    assert [(bar.label, bar.detail) for bar in view.content.bars] == [
        ("심장초음파", "1편"), ("위내시경", "1편"), ("일반", "1편"),
    ]
    assert view.content.empty_text == ""
    assert build(published_contents=[], published_count=0, fulfilled_count=0).content.empty_text


def test_exposure_page_pools_rates_by_treatment_platform_and_group():
    view = build()
    assert [(bar.label, bar.value, bar.detail) for bar in view.exposure.treatment_bars] == [
        ("심장초음파", 83.33, "AI 답변 6건 중 5건"),
        ("흉통", 16.67, "AI 답변 6건 중 1건"),
        ("건강검진", 0.0, "AI 답변 6건 중 0건"),
        ("위내시경", 0.0, "AI 답변 6건 중 0건"),
    ]
    assert [(bar.label, bar.value, bar.detail) for bar in view.exposure.platform_bars] == [
        ("ChatGPT", 33.33, "AI 답변 12건 중 4건"),
        ("Gemini", 16.67, "AI 답변 12건 중 2건"),
    ]
    groups = {row.group: row for row in view.exposure.group_rows}
    assert groups["A"].detail == NOT_MEASURED_YET and groups["A"].rate is None
    assert (groups["B"].keyword_count, groups["B"].detail) == (2, "AI 답변 12건 중 5건")
    assert (groups["C"].keyword_count, groups["C"].rate) == (1, 16.67)
    assert (groups["D"].keyword_count, groups["D"].rate) == (1, 0.0)
    assert [point.label for point in view.exposure.trend] == ["8월", "9월", "10월"]
    assert view.exposure.trend[-1].value == 25.0
    heat = {row.keyword: row for row in view.exposure.heatmap}
    assert [c.state for c in heat["가상동 심장초음파 잘하는 병원"].cells] == ["mentioned", "mentioned"]
    assert [c.detail for c in heat["가상동 위내시경 가능한 병원"].cells] == ["0/3", "0/3"]
    assert view.exposure.citation_lines == (
        f"출처를 확인한 AI 답변 8건 중 {HOSPITAL_NAME} 콘텐츠 인용 2건",
    )


def test_competition_page_lists_registered_competitors_and_replacements():
    view = build()
    assert view.competition.basis == "등록한 경쟁 병원 2곳 기준"
    assert [(row.name, row.mention_count, row.observed_count, row.rate) for row in view.competition.rows] == [
        ("가상동 다른의원", 1, 4, 25.0), ("가상 중앙의원", 0, 4, 0.0),
    ]
    assert [(row.keyword, row.competitors) for row in view.competition.replaced] == [
        ("가상동 위내시경 가능한 병원", ("가상동 다른의원",)),
    ]
    assert view.competition.unexposed_count == 2
    none = build(records=_records(competitors=False))
    assert none.competition.rows == () and none.competition.unexposed_count == 2
    assert none.competition.empty_text


def test_cases_page_excerpts_and_rule_insights():
    view = build()
    found, missing = view.cases.found, view.cases.missing
    assert found is not None and missing is not None
    assert found.keyword == "가상동 심장초음파 잘하는 병원"
    assert found.highlight == HOSPITAL_NAME and HOSPITAL_NAME in found.excerpt
    assert found.insight == "'심장초음파 검사 안내' 콘텐츠가 출처로 쓰였습니다 → 같은 형식으로 다른 항목도 발행"
    assert (found.platform, found.measured_on) == ("ChatGPT", "2026-10-01")
    assert missing.keyword == "가상동 위내시경 가능한 병원"
    assert missing.highlight == ""
    assert missing.insight == "가상동 다른의원이 대신 언급됐습니다 → '위내시경' 콘텐츠 고도화"
    assert len(found.excerpt) <= 262 and len(missing.excerpt) <= 262


# ── P6 우선순위 ────────────────────────────────────────────────────────────────


def test_plan_rows_rank_focus_treatment_then_contested_then_lowest_rate():
    view = build()
    rows = [*view.plan.rows, *view.plan.continued]
    # 위내시경: 병원 진료 항목 + 경쟁사 언급(이탈) → 1순위. 건강검진: 0% 미노출 → 2순위.
    assert [(row.priority, row.treatment, row.rate_label, row.kind) for row in rows] == [
        (1, "위내시경", "0%", "optimize"), (2, "건강검진", "0%", "publish"),
    ]
    assert rows[0].action == "'위내시경' 기존 콘텐츠 고도화 후 재측정"
    assert rows[1].action == "'가상동 건강검진' 키워드 대응 콘텐츠 1편 발행 예정"
    assert view.plan.continued == ()


def test_plan_rows_split_after_the_body_limit_without_repeating():
    many = {f"가상동 항목{i:02d} 병원": (f"항목{i:02d}", INTENT_RECOMMENDATION, (0, 3), (0, 3)) for i in range(12)}
    targets = [_target(text, treatment=t, intent=i) for text, (t, i, _c, _g) in many.items()]
    view = build(
        sov_coverage=_coverage(keywords=many), attribution=None, query_targets=targets, records=[],
        citations={"measured_cell_count": 0, "cited_items": []},
    )
    assert all(row.kind == "publish" for row in (*view.plan.rows, *view.plan.continued))
    assert len(view.plan.rows) == PLAN_ROWS_IN_BODY
    assert len(view.plan.continued) == 12 - PLAN_ROWS_IN_BODY
    keywords = [row.keyword for row in (*view.plan.rows, *view.plan.continued)]
    assert len(set(keywords)) == 12
    assert [row.priority for row in (*view.plan.rows, *view.plan.continued)] == list(range(1, 13))
    # 부록에 이어지는 행의 키워드는 검증 필수 문구에도 한 번씩 들어간다.
    appendix_texts = view.required_texts()[6]
    assert all(row.keyword in appendix_texts for row in view.plan.continued)


# ── 검증·대조 계약 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_required_texts_have_seven_pages_with_title_name_and_caveats(name):
    view = build(**SCENARIOS[name])
    texts = view.required_texts()
    assert len(texts) == 7
    assert all(isinstance(text, str) for page in texts for text in page)
    assert view.summary.title in texts[0] and HOSPITAL_NAME in texts[0]
    assert all(line in texts[6] for line in CAVEAT_LINES)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_required_texts_have_no_empty_strings(name):
    view = build(**SCENARIOS[name])
    assert all(text.strip() for page in view.required_texts() for text in page)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_view_facts_match_facts_rebuilt_from_stored_summaries(name):
    """v4 템플릿 갱신 대조: 저장 요약에서 다시 만든 사실 ⊆ 뷰 사실(같은 키·같은 값)."""
    view = build(**SCENARIOS[name])
    sov, content = _stored(view, SCENARIOS[name])
    stored = stored_facts_v4(sov, content)
    assert stored["template"] == "v4"
    assert compare_facts_v4(stored, view.facts()) == []
    for key, value in stored.items():
        if not key.startswith("appendix."):
            assert view.facts()[key] == value
    # 디스패치 경로도 같은 답을 준다.
    assert doctor_view_facts(view) == view.facts()
    assert stored_doctor_facts(sov, content) == stored
    assert doctor_view_expectations(view, sov_summary=sov, content_summary=content) == []


def test_compare_facts_v4_catches_changed_rate_keyword_count_and_appendix_rows():
    view = build()
    sov, content = _stored(view, {})
    stored = stored_facts_v4(sov, content)
    changed = {**stored, "card.rate": "26%", "card.keywords": "4개 중 3개"}
    problems = compare_facts_v4(changed, view.facts())
    assert "card.rate: 저장 26% ≠ 뷰 25%" in problems
    assert "card.keywords: 저장 4개 중 3개 ≠ 뷰 4개 중 2개" in problems
    kw = "kw.가상동 심장초음파 잘하는 병원.total"
    assert stored[kw] == "5/6"
    # 키워드 사실은 저장값 근거가 있어야 한다 — 값이 다르거나 뷰에만 있으면 차이다.
    assert f"{kw}: 저장 4/6 ≠ 뷰 5/6" in compare_facts_v4({**stored, kw: "4/6"}, view.facts())
    assert compare_facts_v4({k: v for k, v in stored.items() if k != kw}, view.facts()) == [
        f"저장값 근거 없음: {kw}=5/6"
    ]
    extra = {**view.facts(), "kw.없던 키워드.total": "1/2"}
    assert "저장값 근거 없음: kw.없던 키워드.total=1/2" in compare_facts_v4(stored, extra)
    unbacked = {k: v for k, v in stored.items() if not k.startswith("platform.")}
    assert "저장값 근거 없음: platform.chatgpt=33%" in compare_facts_v4(unbacked, view.facts())
    # 뷰에만 있어도 되는 사실(진료 항목·그룹·경쟁·누적·지난달 참고치)은 차이가 아니다.
    assert compare_facts_v4(stored, {**view.facts(), "rate.reference_previous": "40%"}) == []
    assert not any(key.startswith(("treatment.", "group.", "competitor.")) for key in stored)
    assert any(key.startswith(("treatment.", "group.", "competitor.")) for key in view.facts())


def _printed(view: DoctorReportV4) -> str:
    """새 PDF 본문을 흉내 낸다 — 사실 값 전부와 부록 A 행(키워드 · 그룹 · 진료 항목 · 칸 · 합계 · 지난달)."""
    parts = [f"{key} {value}" for key, value in view.facts().items() if not key.startswith(("kw.", "citation"))]
    parts.extend(view.exposure.citation_lines)  # 인용은 문장 꼴('답변 N건 중 … 인용 M건')로 찍힌다
    for row in view.appendix.keyword_rows:
        parts.append(f"{row.keyword} {row.group} {row.treatment} {' '.join(row.cells)} {row.total} {row.previous}")
    return "\n".join(parts)


def test_pdf_tokens_v4_require_each_printed_number_once():
    view = build()
    facts = view.facts()
    tokens = _pdf_tokens(facts)
    keys = {key for key, _ in tokens}
    assert not keys & {"template", "rate.current", "rate.previous"}
    assert tokens == pdf_tokens_v4(facts)
    # 인용 사실은 문장 속 두 숫자를 따로 요구한다.
    assert [p.pattern for k, p in tokens if k == "citation"] == ["답변8건중", r"인용2건(?!\d)"]
    # 키워드 행은 합계 키 하나로 묶여 '키워드 … 칸·합계·지난달' 순서를 요구한다.
    kw_keys = sorted(k for k in keys if k.startswith("kw."))
    assert kw_keys == sorted(f"kw.{text}.total" for text in KEYWORDS)
    assert {"treatment.심장초음파.rate", "group.B.rate", "competitor.가상동 다른의원.rate", "trend.8월", "trend.10월"} <= keys
    assert facts["treatment.심장초음파.rate"] == "83%" and facts["trend.10월"] == "25%"
    printed = _printed(view)
    assert "가상동 심장초음파 잘하는 병원 B 심장초음파 3/3 2/3 5/6 0/6" in printed
    assert pdf_fact_problems(printed, facts) == []
    # 같은 값(25%)이 카드·경쟁사 비율·추이 10월에 함께 찍힌다 — 모두 사라지면 셋 다 잡힌다.
    assert sorted(pdf_fact_problems(printed.replace("25%", "26%"), facts)) == [
        "새 PDF에서 찾지 못함: card.rate=25%",
        "새 PDF에서 찾지 못함: competitor.가상동 다른의원.rate=25%",
        "새 PDF에서 찾지 못함: trend.10월=25%",
    ]
    assert pdf_fact_problems(printed.replace("인용 2건", "인용 3건"), facts) == ["새 PDF에서 찾지 못함: citation=8건 중 2건"]
    # 키워드 행 안의 분수가 바뀌면 잡는다.
    changed = printed.replace("3/3 2/3 5/6 0/6", "3/3 1/3 5/6 0/6")
    assert pdf_fact_problems(changed, facts) == ["새 PDF에서 찾지 못함: kw.가상동 심장초음파 잘하는 병원.total=5/6"]
    # 두 행의 분수가 서로 바뀌어도(집합은 같다) 행에 묶였으므로 잡는다.
    swapped = printed.replace("3/3 2/3 5/6 0/6", "@@").replace("0/3 0/3 0/6 2/6", "3/3 2/3 5/6 0/6").replace("@@", "0/3 0/3 0/6 2/6")
    assert swapped != printed
    problems = pdf_fact_problems(swapped, facts)
    assert "새 PDF에서 찾지 못함: kw.가상동 위내시경 가능한 병원.total=0/6" in problems
    # 행 토큰의 80자 창이 짧은 다음 행의 칸까지 닿을 수 있어 첫 행은 못 잡을 수 있다 — 행 경계를
    # 더 좁히면 아래 줄도 함께 잡힌다(뷰 모델 `pdf_tokens_v4`의 몫).
    assert problems and all(problem.startswith("새 PDF에서 찾지 못함: kw.") for problem in problems)
    assert pdf_fact_problems(printed.replace("treatment.심장초음파.rate 83%", "treatment.심장초음파.rate 80%"), facts) == [
        "새 PDF에서 찾지 못함: treatment.심장초음파.rate=83%"
    ]


def test_percent_prints_one_hundred_with_a_decimal_so_the_filter_passes():
    full = {text: (t, i, (3, 3), (3, 3)) for text, (t, i, _c, _g) in KEYWORDS.items()}
    view = build(sov_coverage=_coverage(keywords=full, prior=50.0), attribution=None)
    cards = {card.label: card for card in view.summary.cards}
    assert cards["AI 노출률"].value == "100.0%" and view.facts()["card.rate"] == "100.0%"
    assert check_forbidden(cards["AI 노출률"].value) == []


def test_v3_template_refresh_path_is_unchanged():
    view = monthly_view(cumulative_published_count=40)
    facts = doctor_view_facts(view)
    assert not facts_are_v4(facts) and "template" not in facts
    assert facts["highlight.cumulative_published"] == "40"
    assert "tile.contract" in facts
    content = {"published_count": 0, "operations": {"plan_quota": 12}, "contract_timing": {}}
    assert not stored_doctor_view_is_v4(content)
    assert "tile.contract" in stored_doctor_facts({"sov_pct": 40.0}, content)
    assert compare_doctor_facts({"tile.contract": "12편 중 0편"}, {"tile.contract": "12편 중 1편"}) == [
        "tile.contract: 저장 12편 중 0편 ≠ 뷰 12편 중 1편"
    ]


# ── 금지어·의료광고 필터 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_string_in_the_view_avoids_old_terms_and_internal_words(name):
    view = build(**SCENARIOS[name])
    frozen = freeze_v4(view)
    for path, text in _strings(frozen):
        if path == "public_url":
            continue
        for banned in (*BANNED_IN_DOCTOR_COPY, *BANNED_IN_V4, *MARKETING_REMOVED_COPY):
            assert banned not in text, f"{path}: {banned!r} in {text!r}"
    # 부록 B(측정 방법)·D(유의사항)는 기본 문구가 없다(2026-11-06).
    assert view.appendix.method_lines == () and view.appendix.caveat_lines == ()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_string_in_the_view_passes_the_medical_filter(name):
    view = build(**SCENARIOS[name])
    for path, text in _strings(freeze_v4(view)):
        if path == "public_url":
            continue
        assert check_forbidden(text) == [], f"{path}: {text!r}"


# ── 스냅샷 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_snapshot_round_trip_restores_the_identical_v4_view(name):
    view = build(**SCENARIOS[name])
    frozen = freeze_doctor_view(view)
    assert is_v4_payload(frozen) and frozen["template_version"] == "v4"
    assert all(not isinstance(value, tuple) for _p, value in _leaves(frozen))
    restored = restore_doctor_view(frozen)
    assert isinstance(restored, DoctorReportV4)
    assert restored == view
    assert restore_v4(freeze_v4(view)) == view
    assert freeze_doctor_view(restored) == frozen
    assert restored.facts() == view.facts() and restored.required_texts() == view.required_texts()


def test_snapshot_survives_the_pydantic_render_inputs_model():
    view = build()
    inputs = _render_inputs(view)
    parsed = ReportSnapshot.model_validate(
        ReportSnapshot(schema_version=REPORT_SNAPSHOT_SCHEMA_VERSION, render_inputs=inputs).model_dump(mode="json")
    ).render_inputs
    assert restore_doctor_view(parsed.doctor_view) == view
    assert parsed.doctor_metric_facts == view.facts()


def test_v3_payload_still_restores_as_a_dict():
    view = monthly_view(cumulative_published_count=40)
    frozen = freeze_doctor_view(view)
    assert not is_v4_payload(frozen)
    restored = restore_doctor_view(frozen)
    assert isinstance(restored, dict) and restored["hospital_name"] == view["hospital_name"]


# ── 템플릿 갱신 CLI(사전·사후 확인)의 v4 분기 — DB 없이 ───────────────────────


def _fake_report(view: DoctorReportV4, scenario: dict):
    sov, content = _stored(view, scenario)
    return SimpleNamespace(
        version=1, hospital_id="h-1", period_year=2026, period_month=10,
        sov_summary=sov, content_summary=content,
    )


def _precheck(monkeypatch, view: DoctorReportV4, scenario: dict, *, prior_reported: float | None):
    from app.utils import monthly_template_refresh as cli
    from app.workers import tasks

    latest = _fake_report(view, scenario)
    plan = SimpleNamespace(verdict=cli.RefreshVerdict(), superseded=latest, doctor_view=view)
    monkeypatch.setattr(tasks, "build_monthly_template_refresh_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: PUBLIC_URL)
    monkeypatch.setattr(tasks, "reported_monthly_sov_pct", lambda *_a: prior_reported)
    monkeypatch.setattr(cli, "_stored_doctor_text", lambda _db, _report, _verdict: "옛 PDF")
    monkeypatch.setattr(
        cli, "render_validated_doctor_pdf", lambda **_k: SimpleNamespace(pdf_bytes=b"%PDF")
    )
    # 새 PDF 본문은 뷰 사실이 전부 찍힌 것으로 본다(렌더는 다른 테스트가 맡는다).
    monkeypatch.setattr(cli, "_pdf_text", lambda _data: _printed(view))
    hospital = SimpleNamespace(id="h-1", name=HOSPITAL_NAME, aeo_domain=None, slug="tuntun")
    anchor = __import__("arrow").get(2026, 10, 31, 23, 59, tzinfo="Asia/Seoul")
    return cli.precheck_hospital(None, hospital, anchor)


def test_precheck_passes_for_a_comparable_v4_month_even_with_a_stored_prior_report(monkeypatch):
    """비교한 달은 `rate.previous`를 쓰고 참고치를 적지 않는다 — 지난달 보고서가 있어도 요구하지 않는다."""
    view = build()
    assert "rate.reference_previous" not in view.facts() and "rate.previous" in view.facts()
    result = _precheck(monkeypatch, view, {}, prior_reported=30.0)
    assert result.verdict.status == "PASS", result.verdict.summary()


def test_precheck_still_binds_the_reference_value_when_the_v4_view_prints_one(monkeypatch):
    scenario = SCENARIOS["method_reference"]
    view = build(**scenario)
    assert view.facts()["rate.reference_previous"] == "40%"
    assert _precheck(monkeypatch, view, scenario, prior_reported=40.0).verdict.status == "PASS"
    mismatch = _precheck(monkeypatch, view, scenario, prior_reported=41.0)
    assert "rate.reference_previous: 저장 41% ≠ 뷰 40%" in mismatch.verdict.summary()
    unbacked = _precheck(monkeypatch, view, scenario, prior_reported=None)
    assert "저장값 근거 없음: rate.reference_previous=40%" in unbacked.verdict.summary()


def test_reference_fact_formats_one_hundred_like_the_v4_view(monkeypatch):
    from app.utils import monthly_template_refresh as cli
    from app.workers import tasks

    monkeypatch.setattr(tasks, "reported_monthly_sov_pct", lambda *_a: 100.0)
    report = SimpleNamespace(hospital_id="h-1", period_year=2026, period_month=10)
    assert cli._prior_reference_fact(None, report, v4=True) == {"rate.reference_previous": "100.0%"}
    assert cli._prior_reference_fact(None, report) == {"rate.reference_previous": "100.0%"}


def test_postcheck_facts_for_v4_include_the_frozen_view_only_facts():
    """사후 확인은 저장 요약 사실에 얼린 뷰 사실(진료 항목·그룹·경쟁)을 더해 새 PDF와 맞춘다."""
    from app.utils import monthly_template_refresh as cli

    view = build()
    latest = _fake_report(view, {})
    frozen = cli._frozen_view_facts(latest)
    assert frozen == view.facts()
    stored = stored_doctor_facts(latest.sov_summary, latest.content_summary)
    merged = {**cli._secondary_facts(stored, latest.sov_summary), **frozen}
    assert "treatment.심장초음파" in merged and "competitor.가상동 다른의원" in merged
    assert "ci.low" not in merged and "adequacy.planned_slots" not in merged
    printed = _printed(view)
    assert pdf_fact_problems(printed, merged) == []
    # 진료 항목 비율이 바뀐 PDF는 저장 요약만으로는 못 잡지만 얼린 사실이 잡는다.
    changed = printed.replace("AI 답변 6건 중 5건", "AI 답변 6건 중 4건")
    assert pdf_fact_problems(changed, cli._secondary_facts(stored, latest.sov_summary)) == []
    assert "새 PDF에서 찾지 못함: treatment.심장초음파=AI 답변 6건 중 5건" in pdf_fact_problems(changed, merged)
    assert cli._frozen_view_facts(SimpleNamespace(content_summary={})) == {}


# ── 도우미 ──────────────────────────────────────────────────────────────────


def _leaves(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def _render_inputs(view: DoctorReportV4, scenario: dict | None = None) -> ReportRenderInputs:
    scenario = scenario or {}
    coverage = scenario.get("sov_coverage", _coverage()) or {}
    return ReportRenderInputs(
        hospital=FrozenHospitalRenderInput(name=HOSPITAL_NAME, slug="tuntun", plan="PLAN_12", region=["가상동"], specialties=["내과"]),
        period=FrozenPeriodRenderInput(
            year=2026, month=10,
            starts_at=datetime(2026, 9, 30, 15, tzinfo=timezone.utc),
            ends_at=datetime(2026, 10, 31, 15, tzinfo=timezone.utc),
            cutoff_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        ),
        public_url=PUBLIC_URL,
        doctor_view=freeze_doctor_view(view),
        doctor_metric_facts=view.facts(),
        ae_report=FrozenAeRenderInput(
            sov_pct=view.current_rate, published_count=3, repeat_count=3,
            attribution=scenario.get("attribution", _attribution(prior=True)),
            strategy={}, sov_coverage=coverage, content_operations={},
            citations=scenario.get("citations", CITATIONS), talking_points=view.talking_points,
        ),
        measurement=FrozenMeasurementRenderInput(
            protocol=None, platforms=list(PLATFORMS), cells=[], availability=None,
            comparison=coverage.get("comparison") if coverage else None,
        ),
    )


def _stored(view: DoctorReportV4, scenario: dict) -> tuple[dict, dict]:
    """tasks가 저장하는 모양 그대로의 sov_summary·content_summary(뷰를 스냅샷에 얼린다)."""
    coverage = dict(scenario.get("sov_coverage", _coverage()) or {})
    if view.report_kind == "SETUP":
        coverage["report_kind"] = "SETUP"
    snapshot = ReportSnapshot(
        schema_version=REPORT_SNAPSHOT_SCHEMA_VERSION, render_inputs=_render_inputs(view, scenario)
    ).model_dump(mode="json")
    content = {
        "published_count": 3,
        "operations": {"plan_quota": 12},
        "contract_timing": {"published_for_contract_count": 3, "observed_at": "2026-11-01T00:00:00+00:00"},
        "attribution": scenario.get("attribution", _attribution(prior=True)),
        "citations": scenario.get("citations", CITATIONS),
        "talking_points": view.talking_points,
        "report_snapshot": snapshot,
    }
    assert stored_doctor_view_is_v4(content)
    return coverage, content


def test_fixture_targets_cover_every_keyword():
    assert {t.name for t in _targets()} == set(KEYWORDS)
    assert is_dataclass(DoctorReportV4) and {f.name for f in fields(DoctorReportV4)} >= {"summary", "appendix"}
    assert re.fullmatch(r"\d{4}-\d{2}", build().period_ym)


# ── 2026-11-06 공통 문구 정리(마케팅팀 요청) ─────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_copy_policy_turns_old_snapshots_into_the_current_view_and_keeps_numbers(name):
    from app.services.doctor_report_v4 import apply_report_copy_policy, without_hidden_citation

    view = build(**SCENARIOS[name])
    assert apply_report_copy_policy(view) is view  # 지금 빌더의 뷰에는 아무것도 하지 않는다
    legacy = with_legacy_copy(view)
    assert legacy != view
    assert apply_report_copy_policy(legacy) == view
    # 숫자 사실은 그대로다 — 옛 판에서 빠지는 것은 인용 0건 사실뿐이다.
    assert without_hidden_citation(legacy.facts()) == view.facts()
    assert restore_v4(freeze_v4(apply_report_copy_policy(restore_v4(freeze_v4(legacy))))) == view


def test_zero_citation_month_prints_no_citation_line_or_fact():
    from app.services.doctor_report_v4 import (
        apply_report_copy_policy,
        stored_facts_v4,
        without_hidden_citation,
    )

    zero = {**CITATIONS, "cited_cell_count": 0, "cited_content_count": 0, "cited_items": []}
    view = build(citations=zero)
    assert view.exposure.citation_lines == ()
    assert "citation" not in view.facts()
    assert "citation" not in stored_facts_v4(_coverage(), {"citations": zero, "attribution": _attribution(prior=True)})
    legacy = with_legacy_copy(view)
    assert legacy.exposure.citation_lines == (f"출처 확인이 가능한 AI 답변 8건 중 {HOSPITAL_NAME} 콘텐츠 인용 0건",)
    assert legacy.facts()["citation"] == "8건 중 0건"
    assert apply_report_copy_policy(legacy).exposure.citation_lines == ()
    assert "citation" not in without_hidden_citation(legacy.facts())
    # 인용이 있으면 사실은 그대로 찍힌다.
    assert build().facts()["citation"] == "8건 중 2건"


def test_copy_policy_leaves_ae_written_lines_alone():
    from dataclasses import replace

    from app.services.doctor_report_v4 import apply_report_copy_policy

    legacy = with_legacy_copy(build())
    custom = replace(
        legacy,
        summary=replace(legacy.summary, notice="원장님 요청으로 이번 달은 검사 키워드를 늘렸습니다."),
        appendix=replace(legacy.appendix, caveat_lines=(*legacy.appendix.caveat_lines, "AE가 직접 쓴 줄입니다.")),
    )
    cleaned = apply_report_copy_policy(custom)
    assert cleaned.summary.notice == "원장님 요청으로 이번 달은 검사 키워드를 늘렸습니다."
    assert cleaned.appendix.caveat_lines == ("AE가 직접 쓴 줄입니다.",)
    assert cleaned.appendix.method_lines == ()


def test_stored_ae_edits_on_removed_lines_are_dropped_and_the_base_is_cleaned():
    from app.services.doctor_report_ae_edit import base_view_of, stored_ae_overrides
    from app.services.doctor_report_v4 import AE_DELETE

    view = build()
    legacy = with_legacy_copy(view)
    render_inputs = {
        "doctor_view": freeze_v4(legacy),
        "ae_overrides": {
            "base_view": freeze_v4(legacy),
            "fields": {
                "appendix.caveat_lines[1]": AE_DELETE,
                "appendix.method_lines[1]": "계획 150회 / 확인 90회",
                "summary.key_lines[0]": "고친 첫 줄입니다.",
            },
        },
    }
    assert stored_ae_overrides(render_inputs)["fields"] == {"summary.key_lines[0]": "고친 첫 줄입니다."}
    assert base_view_of(render_inputs) == view
    # 걷어 낸 줄만 가리키던 수정이면 수정 판이 아닌 것으로 읽는다.
    only_removed = {**render_inputs, "ae_overrides": {**render_inputs["ae_overrides"], "fields": {"appendix.caveat_lines[0]": AE_DELETE}}}
    assert stored_ae_overrides(only_removed) is None


def test_copy_policy_also_clears_slots_an_ae_emptied_on_removed_lines():
    from dataclasses import replace

    from app.services.doctor_report_v4 import apply_report_copy_policy

    zero = {**CITATIONS, "cited_cell_count": 0, "cited_content_count": 0, "cited_items": []}
    view = build(citations=zero)
    legacy = with_legacy_copy(view)
    emptied = replace(
        legacy,
        exposure=replace(legacy.exposure, citation_lines=("",)),
        appendix=replace(legacy.appendix, caveat_lines=("", *legacy.appendix.caveat_lines[1:]), method_lines=("",)),
    )
    assert apply_report_copy_policy(emptied) == view
    # 인용이 있는 달에 AE가 지운 인용 자리는 그 수정이 계속 소유한다(경로가 살아 있다).
    cited = build()
    deleted = replace(cited, exposure=replace(cited.exposure, citation_lines=("",)))
    assert apply_report_copy_policy(deleted) is deleted


# ── 발행 콘텐츠 귀속(2026-11-08): 이전 달 예정 글이 이번 달에 실리면 계획 이행과의 차이를 밝힌다 ──────


def test_published_card_names_carried_in_publications_only_when_present():
    view = build(published_count=13, fulfilled_count=12, cumulative_published_count=40, carried_in_count=1)
    card = view.summary.cards[0]
    assert card.value == "13편"
    assert card.definition == "계획 12편 중 12편 발행 · 이전 달 예정분 1편 · 누적 40편"
    facts = view.facts()
    assert facts["card.published"] == "13편"
    assert facts["card.plan"] == "12편 중 12편"
    assert facts["card.cumulative"] == "40편"
    plain = build(published_count=12, fulfilled_count=12, cumulative_published_count=40)
    assert plain.summary.cards[0].definition == "계획 12편 중 12편 발행 · 누적 40편"


def test_stored_facts_read_the_doctor_publication_count_when_the_snapshot_has_it():
    from app.services.doctor_report_v4 import stored_facts_v4

    content = {"published_count": 11, "operations": {"plan_quota": 12}, "contract_timing": {"published_for_contract_count": 12}}
    assert stored_facts_v4({}, content)["card.published"] == "11편"  # 옛 판: 운영 집계가 1면 편수였다
    content["doctor_publications"] = {"published_count": 12, "carried_in_count": 0}
    assert stored_facts_v4({}, content)["card.published"] == "12편"
