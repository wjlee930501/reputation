"""원장용 월간 리포트 — 편집 규칙을 코드로 고정한다.

AE용 리포트와 같은 데이터를 쓰지만 편집이 다르다. 여기서 지키는 규칙(VERSIONUP §5-3):

- 헤드라인은 퍼센트가 아니라 **분수**("100번 중 47번"). 비전문가에게 검증된 최선의 설명.
- 전월 대비도 "+20.5%p"가 아니라 "전월 39 → 이번 달 47 (8개 늘었습니다)".
- 합성 점수("AI 노출 지수 78점")를 만들지 않는다 — 산식 없는 지수는 미검증 방식이다.
- 측정이 없으면 0을 만들어내지 않는다. 허위 0은 원장 보고에 들어가면 안 된다.
- 숫자는 전부 코드 바인딩 — LLM 자유 서술 금지(요약 숫자 환각이 1순위 불만).
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.report_engine import build_doctor_report_view

HOSPITAL = SimpleNamespace(name="장편한외과의원")

# 원장 화면에 나오면 안 되는 표현. 사내·업계 용어이거나 숫자를 왜곡하는 표기다.
BANNED_IN_DOCTOR_COPY = [
    "SoV", "sov", "언급률", "노출 갭", "심각도", "쿼리", "질의",
    "%p", "지수", "스코어", "AEO",
]


def _record(
    *,
    mentioned: bool,
    text: str = "환자들이 실제로 물어보는 질문",
    raw: str = "답변 원문",
    platform: str = "chatgpt",
    competitors: list | None = None,
):
    return SimpleNamespace(
        is_mentioned=mentioned,
        raw_response=raw,
        measurement_status="SUCCESS",
        ai_platform=platform,
        measured_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        competitor_mentions=competitors,
        query=SimpleNamespace(query_text=text),
        ai_query_target=None,
    )


def _view(**overrides):
    params = {
        "hospital": HOSPITAL,
        "sov_pct": 47.0,
        "prev_sov_pct": 39.0,
        "published_count": 12,
        "plan_quota": 16,
        "attribution": {
            "new_mention_count": 3,
            "first_measured_mention_count": 2,
            "non_comparable_count": 1,
            "new_mention_cells": [
                {"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"},
                {"query_text": "강남 대장내시경 병원 알려줘", "platform_label": "Gemini"},
                {"query_text": "강남 항문외과 어디가 좋아?", "platform_label": "ChatGPT"},
            ],
        },
        "records": [_record(mentioned=True), _record(mentioned=False)],
        "platforms": ["chatgpt", "gemini"],
    }
    params.update(overrides)
    return build_doctor_report_view(**params)


def _all_copy(view) -> str:
    """원장이 실제로 읽게 되는 문자열 전부."""
    parts = [view["summary"], *view["footnotes"]]
    if view["headline"]["delta_sentence"]:
        parts.append(view["headline"]["delta_sentence"])
    for tile in view["tiles"]:
        parts += [tile["label"], str(tile["value"]), tile["hint"]]
    for mention in view["new_mention_sentences"]:
        parts += [mention["query_text"], mention["platform_label"]]
    parts += view["next_actions"]["ours"] + view["next_actions"]["yours"]
    return " ".join(parts)


def test_headline_is_a_count_out_of_a_hundred_not_a_percentage():
    view = _view()

    assert view["headline"]["of_hundred"] == 47
    assert view["headline"]["prev_of_hundred"] == 39
    assert "100번 중" in view["summary"]
    assert "47" in view["summary"]
    assert "%" not in view["summary"]


def test_month_over_month_is_written_in_counts_and_plain_korean():
    """유의성 판정이 없는(구버전 payload) 경우에만 증감을 숫자로만 읽어준다."""
    assert _view()["headline"]["delta_sentence"] == "지난달 39번 → 이번 달 47번 (8개 늘었습니다)"
    assert _view(sov_pct=31.0)["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 31번 (8개 줄었습니다)"
    )
    assert _view(sov_pct=39.0)["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 39번 (변화 없습니다)"
    )


def test_delta_sentence_is_chosen_by_significance_not_by_sign():
    """+8이라는 부호가 아니라 표본이 문장을 정한다.

    셀 하나가 뒤집히면 3점이 움직이는 표본에서 "8개 늘었습니다"는 노이즈를
    성과로 판 문장이었다.
    """
    noise = _view(significance="WITHIN_NOISE")
    up = _view(significance="SIGNIFICANT_UP")
    down = _view(sov_pct=31.0, significance="SIGNIFICANT_DOWN")

    assert noise["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 47번 (정상 변동 범위 안입니다)"
    )
    assert up["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 47번 (의미 있는 상승입니다)"
    )
    assert down["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 31번 (의미 있는 하락입니다)"
    )
    assert noise["headline"]["significance"] == "WITHIN_NOISE"


def test_significance_and_error_margin_come_from_the_monthly_payload():
    """호출부가 따로 넘기지 않아도 월간 payload에 있으면 그대로 쓴다."""
    view = _view(
        sov_coverage={
            "planned_count": 30,
            "success_count": 30,
            "attempts_used": 150,
            "mention_frequency": 0.47,
            "ci95_low": 39.2,
            "ci95_high": 55.0,
            "margin_of_hundred": 8,
            "significance": "WITHIN_NOISE",
            "measurement_basis": {
                "question_count": 15,
                "platform_count": 2,
                "cell_count": 30,
                "repeat_count": 5,
                "attempts_used": 150,
            },
        }
    )

    assert view["headline"]["delta_sentence"] == (
        "지난달 39번 → 이번 달 47번 (정상 변동 범위 안입니다)"
    )
    assert view["headline"]["attempts_used"] == 150
    assert view["headline"]["ci95_low_of_hundred"] == 39
    assert view["headline"]["ci95_high_of_hundred"] == 55
    assert (
        "이번 달 수치의 오차 범위는 ±8번입니다 (질문 15개 × AI 서비스 2곳 × 반복 5회 기준)."
        in view["footnotes"]
    )


def test_an_unmeasured_month_never_reports_zero():
    """측정이 없으면 '0번'이 아니라 측정이 없었다고 말한다 — 허위 0은 해지 사유가 된다."""
    view = _view(sov_pct=None)

    assert view["measured"] is False
    assert view["headline"]["of_hundred"] is None
    assert view["headline"]["delta_sentence"] == "이번 달은 측정이 충분히 이뤄지지 않았습니다"
    assert "0번" not in view["summary"]
    assert "측정" in view["summary"]


def test_first_month_explicitly_names_the_baseline():
    view = _view(prev_sov_pct=None, comparison_reason="NO_PRIOR_MANIFEST")

    assert view["headline"]["prev_of_hundred"] is None
    assert view["headline"]["delta_sentence"] == "이번 달이 기준선입니다"


def test_incomparable_month_explicitly_names_the_policy_change():
    view = _view(comparison_reason="MEASUREMENT_POLICY_CHANGED")

    assert view["headline"]["delta"] is None
    assert view["headline"]["delta_sentence"] == "측정 기준이 바뀌어 다음 달부터 비교합니다"


@pytest.mark.parametrize("banned", BANNED_IN_DOCTOR_COPY)
def test_doctor_copy_avoids_internal_and_distorting_terms(banned):
    copy = _all_copy(_view())

    assert banned not in copy, f"원장 화면에 '{banned}'가 노출된다"


def test_publishing_progress_is_shown_against_the_contracted_volume():
    """요금제가 편수 약정이므로 이 타일이 곧 계약 이행 증명이다."""
    tile = next(t for t in _view()["tiles"] if t["label"] == "이번 달 발행한 글")

    assert tile["value"] == "16편 중 12편"


def test_publishing_tile_degrades_when_no_quota_is_known():
    tile = next(t for t in _view(plan_quota=None)["tiles"] if t["label"] == "이번 달 발행한 글")

    assert tile["value"] == "12편"


def test_attribution_copy_separates_real_change_from_missing_baseline():
    view = _view()
    copy = _all_copy(view)

    assert len(view["new_mention_sentences"]) == 3
    assert view["new_mention_sentences"][0]["query_text"] == "강남 치질 병원 추천해줘"
    assert "이번 달 처음 확인된 질문 2개" in copy
    assert "새로 좋아진 결과로 계산하지 않았습니다" in copy
    assert "지난달 측정이 끝나지 않은 질문 1개" in copy
    assert "다음 달 정상 측정 후 비교합니다" in copy


def test_citation_facts_are_exposed_to_the_view_but_not_yet_shown_to_the_doctor():
    """원장 1페이지 편집 변경은 별도 작업이다 — 지금은 값만 실어 두고 렌더하지 않는다."""
    view = _view(citations={
        "cited_cell_count": 4,
        "cited_content_count": 2,
        "cited_items": [
            {"title": "치질 수술 FAQ", "cited_cell_count": 3},
            {"title": "강남 치질 병원 안내", "cited_cell_count": 1},
            {"title": "세 번째 글", "cited_cell_count": 1},
            {"title": "네 번째 글", "cited_cell_count": 1},
        ],
    })

    assert view["cited_cells"] == 4
    assert view["cited_content_count"] == 2
    assert [row["title"] for row in view["top_cited_items"]] == [
        "치질 수술 FAQ", "강남 치질 병원 안내", "세 번째 글"
    ]
    assert "치질 수술 FAQ" not in _all_copy(view)


def test_citations_absent_defaults_to_zero_for_older_reports():
    view = _view()

    assert view["cited_cells"] == 0
    assert view["cited_content_count"] == 0
    assert view["top_cited_items"] == []


def test_doctor_tiles_do_not_include_a_competitor_story():
    view = _view(
        records=[
            _record(
                mentioned=False,
                competitors=[{"name": "다른병원", "is_mentioned": True}],
            )
        ]
    )

    assert all("다른 병원" not in tile["label"] for tile in view["tiles"])


def test_evidence_pairs_one_appearance_with_one_absence():
    view = _view(
        records=[
            _record(mentioned=False, text="강남 치질 병원", raw="A의원과 B의원을 추천합니다",
                    competitors=[{"name": "A의원", "is_mentioned": True},
                                 {"name": "B의원", "is_mentioned": True}]),
            _record(mentioned=True, text="강남 대장내시경", raw="장편한외과의원이 좋습니다"),
        ]
    )

    found = view["evidence"]["found"]
    missing = view["evidence"]["missing"]
    assert found["question"] == "강남 대장내시경"
    assert "장편한외과의원" in found["excerpt"]
    assert found["platform"] == "챗GPT"
    assert missing["question"] == "강남 치질 병원"
    assert missing["competitors"] == ["A의원", "B의원"]


def test_evidence_excerpt_centres_on_the_hospital_name_in_a_long_answer():
    """원문이 길면 병원명이 잘려나가 '나온 사례'가 증거 구실을 못 한다."""
    filler = "가" * 900
    view = _view(records=[_record(mentioned=True, raw=f"{filler} 장편한외과의원 추천 {filler}")])

    excerpt = view["evidence"]["found"]["excerpt"]
    assert "장편한외과의원" in excerpt
    assert len(excerpt) < 400


def test_failed_measurements_are_not_used_as_evidence():
    """빈 답변을 '안 나온 사례'로 보여주면 측정 실패를 성과 부진으로 오독시킨다."""
    failed = _record(mentioned=False, raw="")
    failed.measurement_status = "FAILED"
    view = _view(records=[failed])

    assert view["evidence"]["found"] is None
    assert view["evidence"]["missing"] is None


def test_next_actions_separate_what_we_do_from_what_the_doctor_does():
    actions = _view()["next_actions"]

    assert len(actions["ours"]) == 2
    assert len(actions["yours"]) == 1
    assert "원장" not in " ".join(actions["ours"])


def test_footnotes_always_carry_the_honesty_caveats():
    """나쁜 달에 이 문구가 없으면 자연 변동이 해지 대화가 된다."""
    footnotes = " ".join(_view()["footnotes"])

    # 표본 정보가 없으면 오차 범위 숫자를 지어내지 않는다 — 대신 변동 사실만 남긴다.
    assert "오르내립니다" in footnotes
    assert "보장하지 않습니다" in footnotes
    assert "챗GPT" in footnotes


def test_error_margin_footnote_is_measured_not_a_fixed_constant():
    """예전의 고정 상수(±5번)는 실측 노이즈의 1/4~1/5이라 사실이 아니었다."""
    import app.services.report_engine as report_engine

    assert not hasattr(report_engine, "NORMAL_FLUCTUATION")

    thin = _view(
        sov_coverage={
            "planned_count": 4,
            "success_count": 4,
            "margin_of_hundred": 24,
            "measurement_basis": {
                "question_count": 2,
                "platform_count": 1,
                "cell_count": 2,
                "repeat_count": 5,
                "attempts_used": 10,
            },
        }
    )

    assert (
        "이번 달 수치의 오차 범위는 ±24번입니다 (질문 2개 × 반복 5회 기준)."
        in thin["footnotes"]
    )


# ── 템플릿까지 실제로 렌더되는가 ────────────────────────────────────────
# 뷰 모델이 맞아도 템플릿이 깨지면 원장에게 나가는 PDF가 없다.
# WeasyPrint 네이티브 의존성 없이도 HTML 층은 여기서 검증한다.


def _render(view) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from app.services.report_engine import TEMPLATE_DIR

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    return env.get_template("doctor_report.html").render(view=view, period_label="2026-07")


def _body_text(html: str) -> str:
    """스타일을 제외한, 원장이 실제로 읽는 본문."""
    import re

    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", without_style)


def test_template_renders_the_headline_and_evidence():
    html = _render(
        _view(
            records=[
                _record(mentioned=True, text="강남 대장내시경", raw="장편한외과의원이 좋습니다"),
                _record(mentioned=False, text="강남 치질 병원", raw="치질 진료 정보를 확인하세요",
                        competitors=[{"name": "A의원", "is_mentioned": True}]),
            ]
        )
    )
    text = _body_text(html)

    assert "100번 중" in text and "47번" in text
    assert "지난달 39번 → 이번 달 47번" in text
    assert "강남 치질 병원 추천해줘" in text
    assert "강남 대장내시경" in text and "장편한외과의원이 좋습니다" in text
    assert "치질 진료 정보를 확인하세요" in text
    assert "그래서 이번 달 이 주제의 글을 씁니다" in text
    assert "대신 A의원이(가) 언급됐습니다" not in text
    assert "다음 달에는 무엇을 하나요?" not in text
    assert "원장님께 부탁드립니다" not in text


def test_rendered_report_never_shows_a_percent_sign_to_the_doctor():
    """퍼센트 표기는 원장 화면에서 쓰지 않는다 — 분수 프레이밍이 정본이다."""
    assert "%" not in _body_text(_render(_view()))


def test_template_handles_an_unmeasured_month_without_showing_zero():
    text = _body_text(_render(_view(sov_pct=None, records=[])))

    assert "측정이 충분히 이뤄지지 않았습니다" in text
    assert "0번" not in text
