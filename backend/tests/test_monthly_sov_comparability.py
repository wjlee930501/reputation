import uuid
from datetime import datetime, timedelta, timezone

from app.services import sov_engine
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import (
    CellAttempt,
    ManifestCellInput,
)

BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)

# 정책 게이트가 아닌 다른 게이트를 검증하는 테스트들이 정책에서 걸리지 않도록,
# 양쪽에 같은 스냅샷을 넘기는 공통 인자.
_SAME_POLICY = {
    "current_protocol": sov_engine.measurement_protocol(),
    "prior_protocol": sov_engine.measurement_protocol(),
}


def _attempt(
    index: int,
    *,
    mentioned: bool,
    succeeded: bool = True,
    answer_model: str | None = "test-answer-model",
    search_calls: int | None = 1,
) -> CellAttempt:
    return CellAttempt(
        record_id=uuid.UUID(int=index + 1),
        measured_at=BASE_TIME + timedelta(minutes=index),
        succeeded=succeeded,
        is_mentioned=mentioned,
        answer_model=answer_model,
        search_calls=search_calls,
    )


def _cell(
    query_key: str,
    platform: str,
    *,
    mentioned: bool = False,
    state: str = "SUCCESS",
    query_intent: str = "LOCAL",
    attempts: tuple[CellAttempt, ...] | None = None,
) -> ManifestCellInput:
    selected_attempts = attempts
    if selected_attempts is None:
        selected_attempts = () if state != "SUCCESS" else (_attempt(0, mentioned=mentioned),)
    return ManifestCellInput(
        query_key=query_key,
        query_text=f"환자 질문 {query_key}",
        platform=platform,
        query_intent=query_intent,
        state=state,
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
        query_intent_source="FROZEN",
        attempts=selected_attempts,
    )


def test_repeated_measurements_contribute_to_the_cell_score() -> None:
    """셀 점수는 대표 1건의 0/1이 아니라 성공 반복의 언급 빈도(k/n)다.

    예전에는 반복 5회를 결제하고 1건만 썼다(나머지 4건 폐기, tie-break가 UUID라
    사실상 무작위 1건). 그 규칙에서는 아래 두 달의 점수가 같았지만, 실제로는
    "5번 중 5번 나온 달"과 "5번 중 1번 나온 달"은 같은 달이 아니다.
    """
    # Given: 같은 셀에서 5회 중 5회 언급된 달과 5회 중 1회만 언급된 달
    always = _cell(
        "high",
        "chatgpt",
        attempts=tuple(_attempt(index, mentioned=True) for index in range(5)),
    )
    once_in_five = _cell(
        "high",
        "chatgpt",
        attempts=(
            _attempt(0, mentioned=True),
            *(_attempt(index, mentioned=False) for index in range(1, 5)),
        ),
    )
    stable_other_cell = _cell("normal", "chatgpt", mentioned=False)

    # When
    strong = build_monthly_sov((always, stable_other_cell), ("chatgpt",))
    weak = build_monthly_sov((once_in_five, stable_other_cell), ("chatgpt",))

    # Then: 빈도가 다르면 점수도 다르고, 표본 크기는 폐기되지 않고 그대로 남는다
    assert strong.sov_pct == 50.0  # (5/5 + 0/1) / 2
    assert weak.sov_pct == 10.0  # (1/5 + 0/1) / 2
    assert strong.attempts_used == 6
    assert strong.mentioned_attempts == 5
    assert weak.mentioned_attempts == 1
    # 셀 수(분모의 구조)는 그대로다 — 바뀐 것은 셀 하나가 이진값이 아니라는 것뿐이다.
    assert strong.segments.local.measured_count == weak.segments.local.measured_count == 2


def test_single_attempt_cells_still_score_as_k_over_one() -> None:
    """반복 도입 이전 manifest(셀당 성공 1건)도 그대로 계산된다 — k/1이다."""
    legacy = (
        _cell("q1", "chatgpt", mentioned=True),
        _cell("q2", "chatgpt", mentioned=False),
    )

    summary = build_monthly_sov(legacy, ("chatgpt",))

    assert summary.sov_pct == 50.0
    assert summary.attempts_used == 2
    assert summary.measurement_basis.repeat_count == 1


def test_headline_uncertainty_comes_from_the_actual_repeat_sample() -> None:
    # Given: 질문 2개 × 반복 5회 = 성공 시도 10건, 그중 5건 언급
    cells = (
        _cell(
            "q1",
            "chatgpt",
            attempts=tuple(_attempt(index, mentioned=True) for index in range(5)),
        ),
        _cell(
            "q2",
            "chatgpt",
            attempts=tuple(_attempt(index + 5, mentioned=False) for index in range(5)),
        ),
    )

    # When
    summary = build_monthly_sov(cells, ("chatgpt",))

    # Then: 5/10의 Wilson 95% 구간이 그대로 붙는다
    assert summary.sov_pct == 50.0
    assert summary.mention_frequency == 0.5
    assert (summary.ci95_low, summary.ci95_high) == (23.66, 76.34)
    assert summary.margin_of_hundred == 26
    assert summary.measurement_basis.to_payload() == {
        "question_count": 2,
        "platform_count": 1,
        "cell_count": 2,
        "repeat_count": 5,
        "attempts_used": 10,
    }


def test_platform_macro_gives_each_configured_platform_equal_weight() -> None:
    # Given: ChatGPT는 성공 2칸, Gemini는 성공 1칸과 실패 1칸이다
    cells = (
        _cell("q1", "chatgpt", mentioned=True),
        _cell("q2", "chatgpt", mentioned=True),
        _cell("q1", "gemini", mentioned=False),
        _cell("q2", "gemini", state="FAILED"),
    )

    # When
    summary = build_monthly_sov(cells, ("chatgpt", "gemini"))

    # Then: 3개 성공행의 원시 평균 66.67%가 아니라 (100% + 0%) / 2다
    assert summary.sov_pct == 50.0
    assert [(row.platform, row.planned_count, row.success_count) for row in summary.platforms] == [
        ("chatgpt", 2, 2),
        ("gemini", 2, 1),
    ]


def test_info_cells_are_disclosed_but_do_not_change_local_headline() -> None:
    # Given
    cells = (
        _cell("local", "chatgpt", mentioned=True),
        _cell("info", "chatgpt", mentioned=False, query_intent="INFO"),
    )

    # When
    summary = build_monthly_sov(cells, ("chatgpt",))

    # Then
    assert summary.sov_pct == 100.0
    assert summary.segments.local.measured_count == 1
    assert summary.segments.info.measured_count == 1
    assert summary.segments.info.mention_rate == 0.0


def test_a_measurement_policy_change_suppresses_monthly_delta() -> None:
    """측정 정책(지시문·검색 강제)이 바뀐 두 달은 성과 변화로 붙여 팔 수 없다."""
    from app.services import sov_engine

    cells = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", mentioned=False),)
    v2 = sov_engine.measurement_protocol()
    v1 = {**v2, "policy_version": "v1", "openai_tool_choice": "required"}

    summary = build_monthly_sov(
        cells, ("chatgpt",), prior_cells=prior, prior_platforms=("chatgpt",),
        current_protocol=v2, prior_protocol=v1,
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "MEASUREMENT_POLICY_CHANGED"
    assert summary.comparison.change_pct is None


def test_unused_provider_model_change_does_not_break_single_platform_comparison() -> None:
    cells = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", mentioned=False),)
    current_protocol = sov_engine.measurement_protocol()
    prior_protocol = {**current_protocol, "gemini_model": "unused-prior-model"}

    summary = build_monthly_sov(
        cells,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        current_protocol=current_protocol,
        prior_protocol=prior_protocol,
    )

    assert summary.comparison.status == "COMPARABLE"
    assert summary.comparison.reason == "MATCHED_COHORT"


def test_a_missing_prior_protocol_snapshot_suppresses_monthly_delta() -> None:
    """스냅샷 도입 이전 달(v1, 기록 없음)과 v2를 비교하면 안 된다."""
    from app.services import sov_engine

    cells = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", mentioned=False),)

    summary = build_monthly_sov(
        cells, ("chatgpt",), prior_cells=prior, prior_platforms=("chatgpt",),
        current_protocol=sov_engine.measurement_protocol(), prior_protocol=None,
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "MEASUREMENT_POLICY_CHANGED"


def test_two_snapshotless_months_are_not_comparable() -> None:
    """스냅샷이 둘 다 없어도 비교하지 않는다.

    처음에는 "같은 이전 세대"로 허용했지만, v2 배포 후 스냅샷 없는 manifest(배포 전
    동결된 이번 달)에 v2 재측정이 섞이면 없음=없음이 "같다"로 접혀 v1/v2 혼합 월이
    비교 가능으로 팔린다. 전환 월의 추세 단절은 의도된 비용이다.
    """
    cells = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", mentioned=False),)

    summary = build_monthly_sov(
        cells, ("chatgpt",), prior_cells=prior, prior_platforms=("chatgpt",),
        current_protocol=None, prior_protocol=None,
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "MEASUREMENT_POLICY_CHANGED"


def test_missing_platform_cohort_suppresses_monthly_delta() -> None:
    # Given: 이번 달은 두 플랫폼, 지난달은 ChatGPT만 고정했다
    current = (
        _cell("q1", "chatgpt", mentioned=True),
        _cell("q1", "gemini", mentioned=False),
    )
    prior = (_cell("q1", "chatgpt", mentioned=False),)

    # When
    summary = build_monthly_sov(
        current,
        ("chatgpt", "gemini"),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    # Then: 서로 다른 플랫폼 구성을 증감 숫자로 포장하지 않는다
    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "PLATFORM_COHORT_MISSING"
    assert summary.comparison.prior_sov_pct is None
    assert summary.comparison.change_pct is None
    assert summary.comparison.customer_impact
    assert summary.comparison.next_action


def test_delta_uses_only_query_cells_matched_within_every_platform() -> None:
    # Given: 양 플랫폼은 유지됐지만 각 달에만 있는 질문이 하나씩 있다
    current = (
        _cell("shared", "chatgpt", mentioned=True),
        _cell("current-only", "chatgpt", mentioned=False),
        _cell("shared", "gemini", mentioned=True),
    )
    prior = (
        _cell("shared", "chatgpt", mentioned=False),
        _cell("prior-only", "chatgpt", mentioned=True),
        _cell("shared", "gemini", mentioned=False),
    )

    # When
    summary = build_monthly_sov(
        current,
        ("chatgpt", "gemini"),
        prior_cells=prior,
        prior_platforms=("chatgpt", "gemini"),
        **_SAME_POLICY,
    )

    # Then: 두 플랫폼의 shared 셀만 비교하고 구성 차이는 숨기지 않는다
    assert summary.comparison.status == "COMPARABLE"
    assert summary.comparison.current_sov_pct == 100.0
    assert summary.comparison.prior_sov_pct == 0.0
    assert summary.comparison.change_pct == 100.0
    assert summary.comparison.matched_cell_count == 2
    assert summary.comparison.current_unmatched_cell_count == 1
    assert summary.comparison.prior_unmatched_cell_count == 1


def test_headline_and_prior_share_the_matched_cohort_denominator() -> None:
    """헤드라인·전월·증감이 같은 셀 집합 위에 있어야 한다.

    예전에는 헤드라인만 전 셀(current-only 포함), 전월은 매칭 셀만 써서
    30셀 대 29셀을 나란히 놓고 증감이라 불렀다.
    """
    current = (
        _cell("shared", "chatgpt", mentioned=True),
        _cell("current-only", "chatgpt", mentioned=False),
    )
    prior = (_cell("shared", "chatgpt", mentioned=False),)

    summary = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    # 헤드라인 = 매칭 코호트(shared 1셀) = 100%, 전 셀 기준 50%는 따로 공개한다
    assert summary.comparison.status == "COMPARABLE"
    assert summary.sov_pct == 100.0
    assert summary.sov_pct_all_cells == 50.0
    assert summary.comparison.prior_sov_pct == 0.0
    assert summary.comparison.change_pct == 100.0
    # 두 달의 표본 수도 같은 셀 집합에서 세므로 대칭이다
    assert summary.comparison.current_attempts_used == 1
    assert summary.comparison.prior_attempts_used == 1
    assert summary.attempts_used == summary.comparison.current_attempts_used
    payload = summary.to_payload()
    assert payload["sov_pct"] == 100.0
    assert payload["sov_pct_all_cells"] == 50.0
    # 표본 각주도 같은 코호트를 설명해야 한다 — current-only 셀은 세지 않는다
    assert payload["measurement_basis"]["cell_count"] == 1
    assert payload["measurement_basis"]["attempts_used"] == 1


def test_a_one_cell_flip_is_reported_as_normal_variation() -> None:
    """셀 하나가 뒤집힌 정도의 변화는 노이즈로 판정돼야 한다."""
    current = tuple(
        _cell(
            f"q{index}",
            "chatgpt",
            attempts=tuple(
                _attempt(index * 5 + repeat, mentioned=index < 5)
                for repeat in range(5)
            ),
        )
        for index in range(10)
    )
    prior = tuple(
        _cell(
            f"q{index}",
            "chatgpt",
            attempts=tuple(
                _attempt(100 + index * 5 + repeat, mentioned=index < 4)
                for repeat in range(5)
            ),
        )
        for index in range(10)
    )

    summary = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    # 50% → 40%, 10점 차이지만 50개 시도 표본에서는 구간이 겹친다
    assert summary.comparison.current_sov_pct == 50.0
    assert summary.comparison.prior_sov_pct == 40.0
    assert summary.comparison.significance == "WITHIN_NOISE"
    assert summary.to_payload()["significance"] == "WITHIN_NOISE"


def test_a_large_move_on_the_same_cohort_is_reported_as_significant() -> None:
    current = tuple(
        _cell(
            f"q{index}",
            "chatgpt",
            attempts=tuple(
                _attempt(index * 5 + repeat, mentioned=index < 9) for repeat in range(5)
            ),
        )
        for index in range(10)
    )
    prior = tuple(
        _cell(
            f"q{index}",
            "chatgpt",
            attempts=tuple(
                _attempt(200 + index * 5 + repeat, mentioned=index < 1)
                for repeat in range(5)
            ),
        )
        for index in range(10)
    )

    summary = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    assert summary.comparison.significance == "SIGNIFICANT_UP"
    assert summary.comparison.current_ci95_low is not None
    assert summary.comparison.current_ci95_low > summary.comparison.prior_ci95_high


def test_a_non_comparable_month_carries_no_significance_verdict() -> None:
    summary = build_monthly_sov(
        (_cell("q1", "chatgpt", mentioned=True),),
        ("chatgpt",),
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.significance is None
    assert summary.to_payload()["significance"] is None


def test_representative_attempt_is_deterministic_and_prefers_mention_context() -> None:
    """대표 응답(evidence 인용문)은 같은 입력이면 항상 같아야 한다."""
    rich = CellAttempt(
        record_id=uuid.UUID(int=90),
        measured_at=BASE_TIME + timedelta(minutes=9),
        succeeded=True,
        is_mentioned=True,
        answer_model="test-answer-model",
        mention_context="장편한외과의원은 대장항문 전문으로 자주 추천됩니다",
    )
    thin = CellAttempt(
        record_id=uuid.UUID(int=1),
        measured_at=BASE_TIME,
        succeeded=True,
        is_mentioned=True,
        answer_model="test-answer-model",
        mention_context="언급",
    )
    unmentioned = _attempt(0, mentioned=False)
    cell = _cell("q1", "chatgpt", attempts=(unmentioned, thin, rich))

    # 언급된 시도 우선 → 문맥이 긴 시도 → (동률이면) 이른 시각 → record_id
    assert cell.selected_attempt is rich
    assert cell.mention_frequency == 2 / 3


def test_actual_answer_model_change_suppresses_monthly_delta() -> None:
    current = _cell(
        "q1",
        "chatgpt",
        attempts=(_attempt(0, mentioned=True, answer_model="gpt-current"),),
    )
    prior = _cell(
        "q1",
        "chatgpt",
        attempts=(_attempt(1, mentioned=False, answer_model="gpt-prior"),),
    )

    summary = build_monthly_sov(
        (current,),
        ("chatgpt",),
        prior_cells=(prior,),
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "ANSWER_MODEL_CHANGED"
    assert summary.comparison.change_pct is None


def test_missing_actual_answer_model_suppresses_monthly_delta() -> None:
    current = _cell(
        "q1",
        "chatgpt",
        attempts=(_attempt(0, mentioned=True, answer_model=None),),
    )
    prior = _cell("q1", "chatgpt", mentioned=False)

    summary = build_monthly_sov(
        (current,),
        ("chatgpt",),
        prior_cells=(prior,),
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    )

    assert summary.comparison.status == "NON_COMPARABLE"
    assert summary.comparison.reason == "ANSWER_MODEL_UNKNOWN"
    assert summary.comparison.change_pct is None


def test_payload_counts_and_query_breakdown_equal_fixed_cells() -> None:
    # Given
    cells = (
        _cell("q1", "chatgpt", mentioned=True),
        _cell("q1", "gemini", state="FAILED"),
        _cell("q2", "chatgpt", state="EXCLUDED"),
        _cell("q2", "gemini", mentioned=False, query_intent="INFO"),
    )

    # When
    payload = build_monthly_sov(cells, ("chatgpt", "gemini")).to_payload()

    # Then: 계획 수는 제외 셀을 빼고, 전체 셀 구성은 제외 셀까지 모두 공개한다
    assert payload["planned_count"] == 3
    assert payload["success_count"] == 2
    assert payload["failed_count"] == 1
    assert payload["excluded_count"] == 1
    assert sum(row["cell_count"] for row in payload["queries"]) == 4
    assert sum(row["cell_count"] for row in payload["platforms"]) == 4
    chatgpt = next(row for row in payload["platforms"] if row["platform"] == "chatgpt")
    assert chatgpt["answer_models"] == ["test-answer-model"]
    assert chatgpt["model_observation_complete"] is True
    assert chatgpt["search_observed_count"] == 1
    assert chatgpt["search_used_count"] == 1
    assert len(payload["cells"]) == 4
    assert payload["cells"][0] == {
        "query_key": "q1",
        "query_text": "환자 질문 q1",
        "query_intent_label": "지역·병원 선택 질문",
        "platform": "chatgpt",
        "platform_label": "ChatGPT",
        "state": "SUCCESS",
        "state_label": "측정 완료",
        "measured": True,
        "mentioned": True,
        "attempts_used": 1,
        "mentioned_attempts": 1,
        "mention_frequency": 1.0,
    }
    assert not ({"query_matrix_id", "query_target_id", "record_id", "raw_response"} & payload["cells"][0].keys())


def test_success_state_without_success_attempt_fails_closed_and_is_disclosed() -> None:
    # Given: 셀 상태는 성공인데 연결된 성공 측정이 없는 손상된 입력
    inconsistent = _cell("q1", "chatgpt", state="SUCCESS", attempts=())

    # When
    summary = build_monthly_sov((inconsistent,), ("chatgpt",))

    # Then: 0%로 꾸미지 않고 상태 수와 실제 측정 수의 차이를 그대로 남긴다
    assert summary.sov_pct is None
    assert summary.success_count == 1
    assert summary.segments.local.measured_count == 0


def test_failed_state_with_success_attempt_also_fails_closed() -> None:
    inconsistent = _cell(
        "q1",
        "chatgpt",
        state="FAILED",
        attempts=(_attempt(0, mentioned=True),),
    )

    summary = build_monthly_sov((inconsistent,), ("chatgpt",))

    assert summary.sov_pct is None
    assert summary.failed_count == 1
    assert summary.segments.local.measured_count == 0
    assert summary.to_payload()["cells"][0]["measured"] is False


def test_failed_matched_identity_is_counted_as_unused_comparison_cell() -> None:
    # Given: 같은 질문 키는 있지만 지난달 측정이 실패했다
    current = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", state="FAILED"),)

    # When
    comparison = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    ).comparison

    # Then: 구조적 키가 같아도 실제 비교에 쓰지 못한 양쪽 셀을 숨기지 않는다
    assert comparison.status == "NON_COMPARABLE"
    assert comparison.matched_cell_count == 0
    assert comparison.current_unmatched_cell_count == 1
    assert comparison.prior_unmatched_cell_count == 1


def test_legacy_manifest_without_intent_snapshot_suppresses_delta() -> None:
    # Given: 질문 유형 동결 정보가 없던 구버전 두 달
    current = (_cell("q1", "chatgpt", mentioned=True),)
    prior = (_cell("q1", "chatgpt", mentioned=False),)
    current = tuple(
        ManifestCellInput(
            query_key=cell.query_key,
            query_text=cell.query_text,
            platform=cell.platform,
            query_intent=cell.query_intent,
            state=cell.state,
            query_matrix_id=cell.query_matrix_id,
            query_target_id=cell.query_target_id,
            query_variant_id=cell.query_variant_id,
            query_intent_source="LEGACY_LIVE",
            attempts=cell.attempts,
        )
        for cell in current
    )
    prior = tuple(
        ManifestCellInput(
            query_key=cell.query_key,
            query_text=cell.query_text,
            platform=cell.platform,
            query_intent=cell.query_intent,
            state=cell.state,
            query_matrix_id=cell.query_matrix_id,
            query_target_id=cell.query_target_id,
            query_variant_id=cell.query_variant_id,
            query_intent_source="LEGACY_LIVE",
            attempts=cell.attempts,
        )
        for cell in prior
    )

    # When
    comparison = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        **_SAME_POLICY,
    ).comparison

    # Then: live 분류를 과거 사실처럼 믿고 증감 숫자를 만들지 않는다
    assert comparison.status == "NON_COMPARABLE"
    assert comparison.reason == "INTENT_SNAPSHOT_MISSING"
    assert comparison.change_pct is None
