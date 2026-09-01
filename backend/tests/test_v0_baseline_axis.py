"""서비스 시작 시점(V0) 대비 축 — 언제 그려도 되고 언제 그리면 거짓말인가.

V0는 월간과 **비교 가능하지 않다**: 반복 프로토콜(마이크로 평균 5회)도, 측정 창(7일)도,
집계 방식도 다르다. 그래서 여기서 만드는 것은 비교가 아니라 라벨이 붙은 참고선 하나이고,
질문 세트마저 다르면 그 선은 아무 의미가 없으므로 아예 그리지 않는다.
"""

from app.workers.tasks import (
    V0_BASELINE_MIN_OVERLAP,
    build_v0_baseline,
    v0_query_overlap_ratio,
)

TRACKING = [f"질문 {index}" for index in range(10)]


def test_overlap_ratio_is_measured_against_the_v0_question_set():
    """분모는 언제나 V0 쪽이다 — 추적 세트가 커졌다고 겹침이 낮아지면 안 된다."""
    assert v0_query_overlap_ratio(TRACKING[:5], TRACKING) == 1.0
    assert v0_query_overlap_ratio(TRACKING, TRACKING[:5]) == 0.5
    assert v0_query_overlap_ratio([], TRACKING) == 0.0
    assert v0_query_overlap_ratio(TRACKING, []) == 0.0


def test_overlap_ratio_ignores_whitespace_differences_only():
    assert v0_query_overlap_ratio(["  강남  치질   병원 "], ["강남 치질 병원"]) == 1.0
    assert v0_query_overlap_ratio(["강남 치질병원"], ["강남 치질 병원"]) == 0.0


def test_baseline_is_drawn_when_the_question_sets_overlap_enough():
    baseline = build_v0_baseline(
        v0_sov_pct=31.0,
        current_sov_pct=47.0,
        v0_query_texts=TRACKING,
        tracking_query_texts=TRACKING,
    )

    assert baseline == {
        "of_hundred": 31,
        "current_of_hundred": 47,
        "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
    }


def test_baseline_is_omitted_when_the_question_sets_drifted_apart():
    """다른 질문으로 잰 두 수치를 나란히 놓는 순간 그 줄은 거짓말이 된다."""
    drifted = build_v0_baseline(
        v0_sov_pct=31.0,
        current_sov_pct=47.0,
        v0_query_texts=TRACKING,
        # 10개 중 7개만 남았다 → 0.7 < 0.8
        tracking_query_texts=TRACKING[:7],
    )

    assert drifted is None
    assert V0_BASELINE_MIN_OVERLAP == 0.8


def test_the_gate_is_inclusive_at_exactly_the_threshold():
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=47.0,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING[:8],
        )
        is not None
    )


def test_baseline_is_omitted_when_either_month_has_no_number():
    assert (
        build_v0_baseline(
            v0_sov_pct=None,
            current_sov_pct=47.0,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING,
        )
        is None
    )
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=None,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING,
        )
        is None
    )


def test_a_hospital_without_a_v0_report_gets_no_baseline():
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=47.0,
            v0_query_texts=[],
            tracking_query_texts=TRACKING,
        )
        is None
    )
