"""원장용 월간 리포트 — 편집 규칙을 코드로 고정한다.

AE용 리포트와 같은 데이터를 쓰지만 편집이 다르다. 여기서 지키는 규칙(VERSIONUP §5-3):

- 헤드라인은 퍼센트가 아니라 **분수**("100번 중 47번"). 비전문가에게 검증된 최선의 설명.
- 전월 대비도 "+20.5%p"가 아니라 "전월 39 → 이번 달 47 (8개 늘었습니다)".
- 합성 점수("AI 노출 지수 78점")를 만들지 않는다 — 산식 없는 지수는 미검증 방식이다.
- 측정이 없으면 0을 만들어내지 않는다. 허위 0은 원장 보고에 들어가면 안 된다.
- 숫자는 전부 코드 바인딩 — LLM 자유 서술 금지(요약 숫자 환각이 1순위 불만).
- 페이지는 3막이다: ① 이번 달 저희가 한 일 ② 무엇이 달라졌나 ③ 다음 달 계획.
  1페이지가 넘칠 때 무엇을 뺄지는 **뷰가 결정적인 순서로** 정한다(CSS overflow 금지).
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.report_engine import build_doctor_report_view

HOSPITAL = SimpleNamespace(name="장편한외과의원")

# 원장 화면에 나오면 안 되는 표현. 사내·업계 용어이거나 숫자를 왜곡하는 표기다.
BANNED_IN_DOCTOR_COPY = [
    "SoV", "sov", "언급률", "노출 갭", "심각도", "쿼리", "질의",
    "%p", "지수", "스코어", "AEO",
]


def _content(title: str, *, content_type: str = "DISEASE", targeted: bool = False):
    return SimpleNamespace(
        title=title,
        content_type=content_type,
        query_target_id=uuid4() if targeted else None,
    )


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
            "lost_mention_count": 0,
            "has_prior_month": True,
            "new_mention_cells": [
                {"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"},
                {"query_text": "강남 대장내시경 병원 알려줘", "platform_label": "Gemini"},
                {"query_text": "강남 항문외과 어디가 좋아?", "platform_label": "ChatGPT"},
            ],
            "lost_mention_cells": [],
            "question_rows": [],
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
    for mention in (*view["new_mention_sentences"], *view["lost_mention_sentences"]):
        parts += [mention["query_text"], mention["platform_label"]]
    parts += [item["title"] for item in view["published_items"]]
    if view["citation_line"]:
        parts.append(view["citation_line"])
    if view["v0_baseline"]:
        parts.append(view["v0_baseline"]["sentence"])
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


# ── 막 1: 이번 달 저희가 한 일 ─────────────────────────────────────────


def _citations(**overrides):
    payload = {
        "measured_cell_count": 30,
        "cited_cell_count": 4,
        "cited_content_count": 2,
        "cited_items": [
            {
                "title": "치질 수술 FAQ",
                "cited_cell_count": 3,
                "queries": [{"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"}],
            },
            {"title": "강남 치질 병원 안내", "cited_cell_count": 1, "queries": []},
            {"title": "세 번째 글", "cited_cell_count": 1, "queries": []},
            {"title": "네 번째 글", "cited_cell_count": 1, "queries": []},
        ],
    }
    payload.update(overrides)
    return payload


def test_act_one_names_the_articles_and_puts_cited_ones_first():
    """편수 타일만으로는 "무엇을 했나"에 답이 안 된다 — 원장이 제목을 읽어야 한다."""
    view = _view(
        citations=_citations(),
        published_contents=[
            _content("계절 건강 정보", content_type="HEALTH"),
            _content("대장내시경 준비 안내", content_type="TREATMENT", targeted=True),
            _content("치질 수술 FAQ", content_type="FAQ"),
            _content("병원 공지", content_type="NOTICE"),
        ],
    )

    assert [item["title"] for item in view["published_items"]] == [
        "치질 수술 FAQ", "대장내시경 준비 안내", "계절 건강 정보"
    ]
    assert view["published_items"][0]["cited"] is True
    assert view["published_items"][1]["cited"] is False


def test_act_one_citation_line_counts_answers_not_invented_questions():
    view = _view(citations=_citations())

    assert view["citation_line"] == (
        "AI 답변이 저희 병원 글·페이지를 인용한 횟수: 4건(확인한 답변 30개 중)"
    )
    assert view["cited_cells"] == 4
    assert view["cited_content_count"] == 2
    assert [row["title"] for row in view["top_cited_items"]] == [
        "치질 수술 FAQ", "강남 치질 병원 안내", "세 번째 글"
    ]


def test_act_one_citation_line_is_omitted_for_older_reports_without_citations():
    """인용 귀속 이전에 만들어진 리포트는 0건이 아니라 '모른다'이다."""
    view = _view()

    assert view["citation_line"] is None
    assert view["published_items"] == []
    assert view["cited_cells"] == 0
    assert view["cited_content_count"] == 0
    assert view["top_cited_items"] == []


def test_act_one_citation_line_is_omitted_when_nothing_was_measured():
    view = _view(citations=_citations(measured_cell_count=0, cited_cell_count=0))

    assert view["citation_line"] is None


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


def test_next_action_copy_passes_the_medical_ad_filter():
    """막 3은 원장이 읽는 공개 성격의 문구다 — 공개 경로와 같은 필터를 통과해야 한다."""
    from app.utils.medical_filter import check_forbidden

    actions = _view()["next_actions"]

    for line in (*actions["ours"], *actions["yours"]):
        assert check_forbidden(line) == []


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


def test_error_margin_footnote_writes_a_range_when_repeats_were_uneven():
    """`repeat_count`는 평균이다 — 부분 측정된 달에 평균만 쓰면 없던 표본을 말한다.

    질문마다 5회·1회로 측정된 달의 평균은 3회지만, 실제로 3회 측정된 질문은 하나도
    없을 수 있다. 최소·최대가 다르면 범위로 적는다.
    """
    uneven = _view(
        sov_coverage={
            "planned_count": 4,
            "success_count": 4,
            "margin_of_hundred": 24,
            "measurement_basis": {
                "question_count": 2,
                "platform_count": 1,
                "cell_count": 2,
                "repeat_count": 3,
                "repeat_min": 1,
                "repeat_max": 5,
                "attempts_used": 6,
            },
        }
    )

    assert (
        "이번 달 수치의 오차 범위는 ±24번입니다 (질문 2개 × 반복 1~5회 기준)."
        in uneven["footnotes"]
    )


def test_error_margin_footnote_keeps_the_single_number_for_legacy_payloads():
    """repeat_min/max가 없던 구버전 payload는 예전처럼 평균 하나로 적는다."""
    legacy = _view(
        sov_coverage={
            "planned_count": 4,
            "success_count": 4,
            "margin_of_hundred": 24,
            "measurement_basis": {
                "question_count": 2,
                "platform_count": 2,
                "cell_count": 4,
                "repeat_count": 5,
                "attempts_used": 20,
            },
        }
    )

    assert (
        "이번 달 수치의 오차 범위는 ±24번입니다 (질문 2개 × AI 서비스 2곳 × 반복 5회 기준)."
        in legacy["footnotes"]
    )


# ── 막 2: 빠진 질문 · 시작 시점 대비 ───────────────────────────────────


def _lost(*texts):
    return [
        {"query_text": text, "platform_label": "ChatGPT", "classification": "LOST_MENTION"}
        for text in texts
    ]


def _attribution(**overrides):
    payload = {
        "new_mention_count": 0,
        "first_measured_mention_count": 0,
        "non_comparable_count": 0,
        "new_mention_cells": [],
        "has_prior_month": True,
        "lost_mention_count": 0,
        "lost_mention_cells": [],
        "question_rows": [],
    }
    payload.update(overrides)
    return payload


def test_lost_mentions_are_shown_so_a_down_month_has_a_page_to_answer_from():
    """헤드라인이 내려간 달에 "어디서 빠졌나"를 답할 곳이 없으면 해지 대화가 된다."""
    view = _view(
        attribution=_attribution(
            lost_mention_count=4, lost_mention_cells=_lost("가", "나", "다", "라")
        )
    )

    assert [row["query_text"] for row in view["lost_mention_sentences"]] == ["가", "나", "다"]


def test_lost_mentions_are_omitted_when_there_is_no_prior_month():
    """지난달 manifest가 없으면 "빠졌다"는 말 자체가 성립하지 않는다."""
    view = _view(
        attribution=_attribution(
            has_prior_month=False, lost_mention_count=2, lost_mention_cells=_lost("가", "나")
        )
    )

    assert view["lost_mention_sentences"] == []


def test_lost_mentions_steer_next_month_copy():
    view = _view(
        attribution=_attribution(
            lost_mention_count=1, lost_mention_cells=_lost("강남 치질 병원 추천해줘")
        )
    )

    assert "이번 달 빠진 질문을 먼저 확인해" in " ".join(view["next_actions"]["ours"])


def test_v0_baseline_line_is_rendered_only_when_the_caller_supplies_it():
    baseline = {
        "of_hundred": 31,
        "current_of_hundred": 47,
        "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
    }
    with_v0 = _view(v0_baseline=baseline)

    assert with_v0["v0_baseline"] == baseline
    assert "참고용 값입니다" in " ".join(with_v0["footnotes"])
    assert _view()["v0_baseline"] is None
    assert not any("V0" in note for note in _view()["footnotes"])


# ── 1페이지 트리밍 순서 ────────────────────────────────────────────────


def _crowded(**overrides):
    long_answer = "환자분이 이해하기 쉬운 말로 검사 과정과 준비 방법을 안내합니다. " * 8
    params = {
        "citations": {
            "measured_cell_count": 30,
            "cited_cell_count": 6,
            "cited_content_count": 3,
            "cited_items": [],
        },
        "published_contents": [
            _content("치질 수술 뒤 회복 기간에 대해 자주 묻는 질문", content_type="FAQ"),
            _content("대장내시경 검사 전날 준비 안내", content_type="TREATMENT"),
            _content("겨울철 항문 질환 예방 생활 습관", content_type="HEALTH"),
        ],
        "attribution": _attribution(
            new_mention_count=3,
            first_measured_mention_count=2,
            non_comparable_count=1,
            new_mention_cells=[
                {"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"},
                {"query_text": "강남 대장내시경 병원 알려줘", "platform_label": "Gemini"},
                {"query_text": "강남 항문외과 어디가 좋아?", "platform_label": "ChatGPT"},
            ],
            lost_mention_count=3,
            lost_mention_cells=_lost(
                "치질 수술 후 회복 기간이 얼마나 되나요",
                "대장내시경은 몇 년마다 받아야 하나요",
                "항문 통증이 계속되면 어디로 가야 하나요",
            ),
        ),
        "records": [
            _record(mentioned=True, text="강남 대장내시경", raw=f"장편한외과의원 {long_answer}"),
            _record(mentioned=False, text="강남 치질 병원", raw=long_answer),
        ],
        "v0_baseline": {
            "of_hundred": 31,
            "current_of_hundred": 47,
            "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
        },
    }
    params.update(overrides)
    return _view(**params)


def test_a_roomy_page_keeps_both_examples_and_full_lists():
    view = _view(records=[_record(mentioned=True), _record(mentioned=False)])

    assert view["trimmed"] == []
    assert view["evidence"]["found"] is not None
    assert view["evidence"]["missing"] is not None


def test_page_one_trimming_drops_the_not_shown_example_before_shortening_lists():
    """넘칠 때 무엇을 버릴지는 편집 결정이다 — CSS overflow는 조용히 잘라 버린다."""
    view = _crowded()

    assert view["trimmed"][0] == "EVIDENCE_MISSING"
    assert view["evidence"]["missing"] is None
    assert view["evidence"]["found"] is not None


def test_page_one_trimming_follows_a_fixed_order_and_records_what_it_dropped():
    view = _crowded()
    order = [
        "EVIDENCE_MISSING",
        "NEW_MENTIONS",
        "LOST_MENTIONS",
        "PUBLISHED_TITLES",
        "EVIDENCE_FOUND",
    ]

    assert view["trimmed"] == order[: len(view["trimmed"])]
    assert view["trimmed"]
    if "NEW_MENTIONS" in view["trimmed"]:
        assert len(view["new_mention_sentences"]) == 2
    if "LOST_MENTIONS" in view["trimmed"]:
        assert len(view["lost_mention_sentences"]) == 2
    if "PUBLISHED_TITLES" in view["trimmed"]:
        assert len(view["published_items"]) == 2


# ── 사다리 아랫칸(⑥~⑫) ────────────────────────────────────────────────
#
# `_crowded()`는 ⑤까지만 닿는다. 아래 헬퍼는 같은 뷰를 밀도만 키워 ⑥~⑫를
# 실제로 밟게 한다 — 이 칸들은 밟히지 않으면 아무 테스트도 지키지 못한다.

V0_BASELINE = {
    "of_hundred": 31,
    "current_of_hundred": 47,
    "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
}


def _long_cells(count: int, chars: int):
    filler = "환자분들이 실제로 검색창에 입력하는 아주 긴 질문 문장 예시 " * 60
    return [
        {
            "query_text": filler[:chars] + str(index),
            "platform_label": "ChatGPT" if index % 2 == 0 else "Gemini",
        }
        for index in range(count)
    ]


def _dense(*, count: int, chars: int, hospital=HOSPITAL, extra_footnotes: bool = True):
    """예산을 크게 넘겨 사다리 아랫칸까지 내려가는 뷰.

    `extra_footnotes=True`면 부가 각주 두 개(첫 측정·비교 제외)가 V0 각주 뒤에 붙어
    ⑥이 무엇을 먼저 버리는지 드러난다. `False`면 각주 목록의 **마지막이 곧 V0 각주**인
    평범한 달이 된다 — ⑥이 무조건 pop하던 시절 기준선만 남고 설명이 사라지던 조합이다.
    """
    title_filler = "아주 긴 콘텐츠 제목 예시 문장을 여기에 반복해서 넣는다 " * 40
    long_answer = "환자분이 이해하기 쉬운 말로 검사 과정과 준비 방법을 안내합니다. " * 8
    return _view(
        hospital=hospital,
        citations={
            "measured_cell_count": 30,
            "cited_cell_count": 6,
            "cited_content_count": 3,
            "cited_items": [],
        },
        published_contents=[
            _content(title_filler[:chars] + str(index)) for index in range(count)
        ],
        attribution=_attribution(
            new_mention_count=count,
            first_measured_mention_count=2 if extra_footnotes else 0,
            non_comparable_count=1 if extra_footnotes else 0,
            new_mention_cells=_long_cells(count, chars),
            lost_mention_count=count,
            lost_mention_cells=_long_cells(count, chars),
        ),
        records=[
            _record(mentioned=True, text="강남 대장내시경", raw=f"{hospital.name} {long_answer}"),
            _record(mentioned=False, text="강남 치질 병원", raw=long_answer),
        ],
        v0_baseline=dict(V0_BASELINE),
    )


DENSITY_SWEEP = [
    (count, chars, extras)
    for count in (1, 2, 3, 5)
    for chars in (20, 60, 120, 200, 300, 600, 1400)
    for extras in (True, False)
]


def _near_budget(*, title_chars: int, extra_footnotes: bool):
    """딱 한 개의 손잡이(대표 글 제목 길이)만 1글자씩 움직이는 뷰.

    사다리 앞칸들은 한 번에 여러 줄을 덜어내서, 굵은 눈금으로 훑으면 "각주 한 줄만
    빼면 예산에 들어맞는" 좁은 구간(제목 한 줄 = 46자 폭)을 통째로 건너뛴다.
    ⑥의 V0 각주 버그가 사는 곳이 정확히 그 구간이라 손잡이를 하나로 줄였다.
    """
    filler = "대장내시경 검사를 앞두고 환자분들이 가장 많이 물어보시는 준비 과정과 주의사항 정리 " * 20
    long_answer = "환자분이 이해하기 쉬운 말로 검사 과정과 준비 방법을 안내합니다. " * 8
    return _view(
        citations={
            "measured_cell_count": 30,
            "cited_cell_count": 6,
            "cited_content_count": 3,
            "cited_items": [],
        },
        published_contents=[_content(filler[:title_chars] or "대장내시경 검사 전날 준비 안내")],
        attribution=_attribution(
            new_mention_count=3,
            first_measured_mention_count=2 if extra_footnotes else 0,
            non_comparable_count=1 if extra_footnotes else 0,
            new_mention_cells=[
                {"query_text": f"강남 치질 병원 추천해줘 {index}", "platform_label": "ChatGPT"}
                for index in range(3)
            ],
            lost_mention_count=3,
            lost_mention_cells=_lost(
                "치질 수술 후 회복 기간이 얼마나 되나요",
                "대장내시경은 몇 년마다 받아야 하나요",
                "항문 통증이 계속되면 어디로 가야 하나요",
            ),
        ),
        records=[
            _record(mentioned=True, text="강남 대장내시경", raw=f"장편한외과의원 {long_answer}"),
            _record(mentioned=False, text="강남 치질 병원", raw=long_answer),
        ],
        v0_baseline=dict(V0_BASELINE),
    )


def test_the_v0_caveat_never_outlives_the_v0_baseline_line():
    """⑥이 마지막 각주를 무조건 pop하면, 부가 각주가 없는 평범한 달에는 그 마지막이
    곧 V0 각주다 — "31번 → 47번"은 본문에 남고 그 값이 참고용이라는 설명만 사라진다
    (한 줄을 뺀 것으로 예산이 채워져 ⑦ V0_BASELINE까지 내려가지도 않는다).
    기준선과 각주는 같이 살거나 같이 죽는다.
    """
    from app.services.report_engine import _v0_footnote

    kept_both = 0
    for extra_footnotes in (False, True):
        for title_chars in range(0, 900):
            view = _near_budget(title_chars=title_chars, extra_footnotes=extra_footnotes)
            has_baseline = bool(view["v0_baseline"])
            has_caveat = _v0_footnote() in view["footnotes"]
            assert has_baseline == has_caveat, (
                f"title_chars={title_chars} extra_footnotes={extra_footnotes} "
                f"trimmed={view['trimmed']}: 기준선={has_baseline} 각주={has_caveat}"
            )
            if "FOOTNOTES" in view["trimmed"] and has_baseline:
                kept_both += 1

    # 스윕이 위험 구간(⑥이 각주를 덜어냈는데 기준선은 살아 있는 상태)을 실제로
    # 지났는지 확인한다 — 지나지 않았다면 위 단언은 아무것도 지키지 않은 것이다.
    assert kept_both > 0


def test_footnote_trimming_drops_the_extras_and_keeps_the_v0_caveat():
    """⑥은 부가 각주(첫 측정·비교 제외)부터 버리고 V0 각주는 건너뛴다."""
    from app.services.report_engine import _v0_footnote

    view = _dense(count=2, chars=120)

    assert "FOOTNOTES" in view["trimmed"]
    assert "V0_BASELINE" not in view["trimmed"]
    assert view["v0_baseline"] == V0_BASELINE
    assert _v0_footnote() in view["footnotes"]
    assert not any("처음 확인된 질문" in note for note in view["footnotes"])
    assert not any("비교에서 제외" in note for note in view["footnotes"])


def test_dropping_the_v0_baseline_takes_its_caveat_with_it():
    """⑦까지 내려가면 기준선과 각주가 함께 사라진다."""
    from app.services.report_engine import _v0_footnote

    view = _dense(count=2, chars=200)

    assert "V0_BASELINE" in view["trimmed"]
    assert view["v0_baseline"] is None
    assert _v0_footnote() not in view["footnotes"]


def test_citation_line_is_dropped_after_the_v0_baseline():
    view = _dense(count=2, chars=200)

    assert view["trimmed"].index("CITATION_LINE") > view["trimmed"].index("V0_BASELINE")
    assert view["citation_line"] is None


def test_the_all_rungs_clear_the_lists_lost_first_then_new_then_titles():
    lost_only = _dense(count=2, chars=200)

    assert "LOST_MENTIONS_ALL" in lost_only["trimmed"]
    assert lost_only["lost_mention_sentences"] == []
    assert lost_only["new_mention_sentences"]  # 아직 남아 있다 — 순서가 지켜졌다

    everything = _dense(count=3, chars=1400)

    assert everything["trimmed"][-3:] == [
        "LOST_MENTIONS_ALL",
        "NEW_MENTIONS_ALL",
        "PUBLISHED_TITLES_ALL",
    ]
    assert everything["new_mention_sentences"] == []
    assert everything["published_items"] == []


def test_the_last_rung_shortens_next_month_plan_to_one_line():
    """사다리를 다 써도 남으면 마지막으로 다음 달 계획을 한 줄로 줄인다.

    고정 문구만으로 예산을 넘기려면 병원 이름이 비상식적으로 길어야 한다 — 이
    칸이 실제로 동작하는지 확인하는 것이 목적이다.
    """
    view = _dense(
        count=3,
        chars=1400,
        hospital=SimpleNamespace(name="장편한" * 400 + "외과의원"),
    )

    assert view["trimmed"][-1] == "NEXT_ACTIONS"
    assert len(view["next_actions"]["ours"]) == 1
    assert len(view["next_actions"]["yours"]) == 1  # 원장이 할 일은 끝까지 남는다


def test_every_trimming_rung_is_reachable_by_some_real_input():
    """사다리 12칸 전부가 어떤 입력에서는 실제로 밟힌다.

    밟히지 않는 칸은 검증되지 않는 코드다 — ⑥의 V0 각주 버그도 ⑥~⑫를 밟는
    테스트가 하나도 없어서 통과했다.
    """
    seen: set[str] = set()
    for count, chars, extras in DENSITY_SWEEP:
        seen.update(_dense(count=count, chars=chars, extra_footnotes=extras)["trimmed"])
    seen.update(
        _dense(count=3, chars=1400, hospital=SimpleNamespace(name="장편한" * 400 + "외과의원"))[
            "trimmed"
        ]
    )

    assert seen == {
        "EVIDENCE_MISSING",
        "NEW_MENTIONS",
        "LOST_MENTIONS",
        "PUBLISHED_TITLES",
        "EVIDENCE_FOUND",
        "FOOTNOTES",
        "V0_BASELINE",
        "CITATION_LINE",
        "LOST_MENTIONS_ALL",
        "NEW_MENTIONS_ALL",
        "PUBLISHED_TITLES_ALL",
        "NEXT_ACTIONS",
    }


# ── 2쪽 부록 ──────────────────────────────────────────────────────────


def _question_row(key, text, *, current=(10, 6), prior=(10, 4), prior_measured=True):
    return {
        "query_key": key,
        "query_text": text,
        "current_attempts_used": current[0],
        "current_mentioned_attempts": current[1],
        "prior_attempts_used": prior[0],
        "prior_mentioned_attempts": prior[1],
        "prior_measured": prior_measured,
    }


def test_appendix_lists_every_tracking_question_with_both_months():
    view = _view(
        attribution=_attribution(
            question_rows=[
                _question_row("q1", "강남 치질 병원 추천해줘"),
                _question_row("q2", "강남 대장내시경 병원", current=(10, 0), prior=(10, 3)),
                _question_row("q3", "새로 추가된 질문", prior=(0, 0), prior_measured=False),
            ]
        ),
        citations={
            "measured_cell_count": 30,
            "cited_cell_count": 1,
            "cited_content_count": 1,
            "cited_items": [
                {
                    "title": "치질 수술 FAQ",
                    "cited_cell_count": 1,
                    "queries": [
                        {"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"}
                    ],
                }
            ],
        },
        records=[
            _record(
                mentioned=False,
                text="강남 치질 병원 추천해줘",
                platform="chatgpt",
                competitors=[{"name": "가나다외과", "is_mentioned": True}],
            ),
            _record(
                mentioned=False,
                text="강남 치질 병원 추천해줘",
                platform="gemini",
                competitors=[{"name": "가나다외과", "is_mentioned": True}],
            ),
            _record(
                mentioned=False,
                text="강남 대장내시경 병원",
                platform="chatgpt",
                competitors=[{"name": "한번만외과", "is_mentioned": True}],
            ),
        ],
    )

    rows = view["appendix_rows"]
    assert [row["query_text"] for row in rows] == [
        "강남 치질 병원 추천해줘",
        "강남 대장내시경 병원",
        "새로 추가된 질문",
    ]
    assert rows[0]["prev_label"] == "10번 중 4번"
    assert rows[0]["current_label"] == "10번 중 6번"
    assert rows[0]["cited_title"] == "치질 수술 FAQ"
    assert rows[1]["current_label"] == "안 나옴"
    assert rows[2]["prev_label"] == "측정 없음"
    # 관측 2회 이상인 경쟁 병원만 이름을 적는다. 숫자는 절대 쓰지 않는다.
    assert rows[0]["competitor"] == "가나다외과"
    assert rows[1]["competitor"] == "—"
    assert not any(char.isdigit() for char in rows[0]["competitor"])


def test_appendix_is_empty_when_no_question_rows_exist():
    """부록이 비면 PDF는 1쪽으로 남는다 — 빈 2쪽을 만들지 않는다."""
    assert _view()["appendix_rows"] == []


def test_appendix_caps_rows_at_the_tracking_set_size():
    view = _view(
        attribution=_attribution(
            question_rows=[
                _question_row(f"q{index}", f"질문 {index}") for index in range(20)
            ]
        )
    )

    assert len(view["appendix_rows"]) == 15


# ── AE 토킹 포인트 ────────────────────────────────────────────────────


def test_talking_points_are_three_number_bound_sentences_in_the_report_order():
    view = _view(
        citations=_citations(),
        published_contents=[_content("치질 수술 FAQ", content_type="FAQ")],
    )
    points = view["talking_points"]

    assert len(points) == 3
    assert "16편 중 12편" in points[0]
    assert "치질 수술 FAQ" in points[0]
    assert "4건" in points[0]
    assert "47번" in points[1]
    assert "지난달 39번 → 이번 달 47번" in points[1]
    assert points[2].startswith("다음 달 계획")


def test_talking_points_never_report_a_number_for_an_unmeasured_month():
    points = _view(sov_pct=None)["talking_points"]

    assert "측정이 충분히 이뤄지지 않아" in points[1]
    assert "0번" not in " ".join(points)


def test_talking_points_pass_the_medical_ad_filter():
    from app.utils.medical_filter import check_forbidden

    for line in _view(citations=_citations())["talking_points"]:
        assert check_forbidden(line) == []


def test_talking_points_are_not_shown_to_the_doctor():
    """토킹 포인트는 AE 준비물이다 — 원장 PDF에 들어가면 내부 문구가 새는 것이다."""
    view = _view(citations=_citations())

    for line in view["talking_points"]:
        assert line not in _body_text(_render(view))


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
    # 3막이 전부 렌더된다 — 예전에는 막 2만 있었다.
    assert "이번 달 저희가 한 일" in text
    assert "무엇이 달라졌나요?" in text
    assert "다음 달에는 무엇을 하나요?" in text
    assert "원장님께 부탁드릴 한 가지" in text


def test_rendered_report_never_shows_a_percent_sign_to_the_doctor():
    """퍼센트 표기는 원장 화면에서 쓰지 않는다 — 분수 프레이밍이 정본이다."""
    assert "%" not in _body_text(_render(_view()))


def test_template_handles_an_unmeasured_month_without_showing_zero():
    text = _body_text(_render(_view(sov_pct=None, records=[])))

    assert "측정이 충분히 이뤄지지 않았습니다" in text
    assert "0번" not in text
