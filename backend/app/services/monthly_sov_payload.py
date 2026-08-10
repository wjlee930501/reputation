from typing import Literal, TypedDict

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
    mentioned: bool | None


class SegmentPayload(TypedDict):
    measured_count: int
    mentioned_count: int
    mention_rate: float | None


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


class ComparisonPayload(TypedDict):
    status: ComparabilityStatus
    reason: str
    current_sov_pct: float | None
    prior_sov_pct: float | None
    change_pct: float | None
    matched_cell_count: int
    current_unmatched_cell_count: int
    prior_unmatched_cell_count: int
    problem: str | None
    customer_impact: str
    next_action: str


class MonthlySovPayload(TypedDict):
    sov_pct: float | None
    prev_sov_pct: float | None
    change_pct: float | None
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
