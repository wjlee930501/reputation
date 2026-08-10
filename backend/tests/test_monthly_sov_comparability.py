import uuid
from datetime import datetime, timedelta, timezone

from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import (
    CellAttempt,
    ManifestCellInput,
)

BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _attempt(
    index: int,
    *,
    mentioned: bool,
    succeeded: bool = True,
) -> CellAttempt:
    return CellAttempt(
        record_id=uuid.UUID(int=index + 1),
        measured_at=BASE_TIME + timedelta(minutes=index),
        succeeded=succeeded,
        is_mentioned=mentioned,
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


def test_adaptive_repetitions_do_not_change_fixed_cell_score() -> None:
    # Given: 같은 HIGH 셀에 성공 측정이 한 번인 달과 네 번인 달
    one_attempt = _cell("high", "chatgpt", mentioned=True)
    repeated_attempts = _cell(
        "high",
        "chatgpt",
        attempts=tuple(_attempt(index, mentioned=True) for index in range(4)),
    )
    stable_other_cell = _cell("normal", "chatgpt", mentioned=False)

    # When: 고정 셀 기준으로 각각 월간 점수를 계산한다
    once = build_monthly_sov((one_attempt, stable_other_cell), ("chatgpt",))
    repeated = build_monthly_sov((repeated_attempts, stable_other_cell), ("chatgpt",))

    # Then: 반복 횟수는 점수와 분모를 바꾸지 않는다
    assert once.sov_pct == repeated.sov_pct == 50.0
    assert once.segments.local.measured_count == repeated.segments.local.measured_count == 2


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
    )

    # Then: 두 플랫폼의 shared 셀만 비교하고 구성 차이는 숨기지 않는다
    assert summary.comparison.status == "COMPARABLE"
    assert summary.comparison.current_sov_pct == 100.0
    assert summary.comparison.prior_sov_pct == 0.0
    assert summary.comparison.change_pct == 100.0
    assert summary.comparison.matched_cell_count == 2
    assert summary.comparison.current_unmatched_cell_count == 1
    assert summary.comparison.prior_unmatched_cell_count == 1


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
    ).comparison

    # Then: live 분류를 과거 사실처럼 믿고 증감 숫자를 만들지 않는다
    assert comparison.status == "NON_COMPARABLE"
    assert comparison.reason == "INTENT_SNAPSHOT_MISSING"
    assert comparison.change_pct is None
