from typing import Literal, TypedDict

from app.services.sov_statistics import DeltaSignificance

CellState = Literal["SUCCESS", "FAILED", "EXCLUDED"]
QueryIntent = Literal["LOCAL", "INFO"]
QueryIntentSource = Literal["FROZEN", "LEGACY_LIVE"]
ComparabilityStatus = Literal["COMPARABLE", "NON_COMPARABLE"]


class CellPayload(TypedDict):
    query_key: str
    query_text: str
    query_intent_label: str
    platform: str
    platform_label: str
    state: CellState
    state_label: str
    measured: bool
    # 대표 응답(evidence)의 판정. 셀 점수는 아래 빈도이며 이 값이 아니다.
    mentioned: bool | None
    # 이 셀에서 쓴 성공 반복 수(n)와 그중 언급 수(k). 셀 점수 = k/n.
    attempts_used: int
    mentioned_attempts: int
    mention_frequency: float | None


class SegmentPayload(TypedDict):
    measured_count: int
    mentioned_count: int
    mention_rate: float | None
    attempts_used: int
    mentioned_attempts: int


class PlatformPayload(TypedDict):
    platform: str
    cell_count: int
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    measured_count: int
    mentioned_count: int
    mention_rate: float | None
    attempts_used: int
    mentioned_attempts: int
    answer_models: list[str]
    model_observation_complete: bool
    search_observed_count: int
    search_used_count: int


class QueryPayload(TypedDict):
    query_key: str
    query_text: str
    query_intent: QueryIntent
    query_intent_label: str
    query_intent_source: QueryIntentSource
    cell_count: int
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    measured_count: int
    mentioned_count: int
    attempts_used: int
    mentioned_attempts: int


class MeasurementBasisPayload(TypedDict):
    """헤드라인이 선 표본의 모양. 각주의 "질문 N개 × 반복 M회"가 이걸 읽는다."""

    question_count: int
    platform_count: int
    cell_count: int
    # 셀당 평균 반복 수와, 그 평균이 감추는 최소·최대. 각주는 min == max일 때만
    # "반복 N회 기준"이라 쓰고 아니면 "반복 N~M회 기준"이라 쓴다.
    repeat_count: int
    repeat_min: int
    repeat_max: int
    attempts_used: int


class ComparisonPayload(TypedDict):
    status: ComparabilityStatus
    reason: str
    current_sov_pct: float | None
    prior_sov_pct: float | None
    change_pct: float | None
    matched_cell_count: int
    current_unmatched_cell_count: int
    prior_unmatched_cell_count: int
    # 매칭 코호트 위에서 센 반복 시도 수(n)와 언급 수(k). 두 달이 같은 셀 집합이다.
    current_attempts_used: int
    current_mentioned_attempts: int
    prior_attempts_used: int
    prior_mentioned_attempts: int
    current_ci95_low: float | None
    current_ci95_high: float | None
    prior_ci95_low: float | None
    prior_ci95_high: float | None
    significance: DeltaSignificance | None
    problem: str | None
    customer_impact: str
    next_action: str


class MonthlySovPayload(TypedDict):
    # 비교 가능한 달에는 **매칭 코호트** 기준 수치다(전월 수치와 같은 분모).
    sov_pct: float | None
    # 언제나 이번 달 전체 셀 기준. 분모 차이를 숨기지 않기 위한 투명성 값.
    sov_pct_all_cells: float | None
    prev_sov_pct: float | None
    change_pct: float | None
    attempts_used: int
    mentioned_attempts: int
    mention_frequency: float | None
    ci95_low: float | None
    ci95_high: float | None
    margin_of_hundred: int | None
    significance: DeltaSignificance | None
    measurement_basis: MeasurementBasisPayload
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    query_intent_snapshot: QueryIntentSource
    cells: list[CellPayload]
    platforms: list[PlatformPayload]
    queries: list[QueryPayload]
    segments: dict[str, SegmentPayload]
    comparison: ComparisonPayload
