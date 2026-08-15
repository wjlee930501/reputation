from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.services.monthly_sov_payload import (
    CellPayload,
    CellState,
    ComparabilityStatus,
    ComparisonPayload,
    MonthlySovPayload,
    PlatformPayload,
    QueryIntent,
    QueryIntentSource,
    QueryPayload,
    SegmentPayload,
)


def _query_intent_label(value: QueryIntent) -> str:
    return "지역·병원 선택 질문" if value == "LOCAL" else "일반 건강정보 질문"


def _platform_label(value: str) -> str:
    return {"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(value, "기타 AI 서비스")


def _state_label(value: CellState) -> str:
    return {
        "SUCCESS": "측정 완료",
        "FAILED": "측정 못함",
        "EXCLUDED": "사전 제외",
    }[value]


@dataclass(frozen=True, slots=True)
class CellAttempt:
    record_id: UUID
    measured_at: datetime
    succeeded: bool
    is_mentioned: bool
    answer_model: str | None = None
    search_calls: int | None = None


@dataclass(frozen=True, slots=True)
class ManifestCellInput:
    query_key: str
    query_text: str
    platform: str
    query_intent: QueryIntent
    state: CellState
    query_matrix_id: UUID | None
    query_target_id: UUID | None
    query_variant_id: UUID | None
    query_intent_source: QueryIntentSource
    attempts: tuple[CellAttempt, ...]

    @property
    def selected_attempt(self) -> CellAttempt | None:
        if self.state != "SUCCESS":
            return None
        successful = tuple(attempt for attempt in self.attempts if attempt.succeeded)
        if not successful:
            return None
        return min(successful, key=lambda attempt: (attempt.measured_at, attempt.record_id.int))

    def to_payload(self) -> CellPayload:
        selected = self.selected_attempt
        return {
            "query_key": self.query_key,
            "query_text": self.query_text,
            "query_intent_label": _query_intent_label(self.query_intent),
            "platform": self.platform,
            "platform_label": _platform_label(self.platform),
            "state": self.state,
            "state_label": _state_label(self.state),
            "measured": selected is not None,
            "mentioned": selected.is_mentioned if selected is not None else None,
        }


@dataclass(frozen=True, slots=True)
class SegmentSummary:
    measured_count: int
    mentioned_count: int
    mention_rate: float | None

    def to_payload(self) -> SegmentPayload:
        return {
            "measured_count": self.measured_count,
            "mentioned_count": self.mentioned_count,
            "mention_rate": self.mention_rate,
        }


@dataclass(frozen=True, slots=True)
class SegmentCollection:
    local: SegmentSummary
    info: SegmentSummary


@dataclass(frozen=True, slots=True)
class PlatformSummary:
    platform: str
    cell_count: int
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    measured_count: int
    mentioned_count: int
    mention_rate: float | None
    answer_models: tuple[str, ...]
    model_observation_complete: bool
    search_observed_count: int
    search_used_count: int

    def to_payload(self) -> PlatformPayload:
        return {
            "platform": self.platform,
            "cell_count": self.cell_count,
            "planned_count": self.planned_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "excluded_count": self.excluded_count,
            "measured_count": self.measured_count,
            "mentioned_count": self.mentioned_count,
            "mention_rate": self.mention_rate,
            "answer_models": list(self.answer_models),
            "model_observation_complete": self.model_observation_complete,
            "search_observed_count": self.search_observed_count,
            "search_used_count": self.search_used_count,
        }


@dataclass(frozen=True, slots=True)
class QuerySummary:
    query_key: str
    query_text: str
    query_intent: QueryIntent
    query_intent_source: QueryIntentSource
    cell_count: int
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    measured_count: int
    mentioned_count: int

    def to_payload(self) -> QueryPayload:
        return {
            "query_key": self.query_key,
            "query_text": self.query_text,
            "query_intent": self.query_intent,
            "query_intent_label": _query_intent_label(self.query_intent),
            "query_intent_source": self.query_intent_source,
            "cell_count": self.cell_count,
            "planned_count": self.planned_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "excluded_count": self.excluded_count,
            "measured_count": self.measured_count,
            "mentioned_count": self.mentioned_count,
        }


@dataclass(frozen=True, slots=True)
class ComparisonSummary:
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

    def to_payload(self) -> ComparisonPayload:
        return {
            "status": self.status,
            "reason": self.reason,
            "current_sov_pct": self.current_sov_pct,
            "prior_sov_pct": self.prior_sov_pct,
            "change_pct": self.change_pct,
            "matched_cell_count": self.matched_cell_count,
            "current_unmatched_cell_count": self.current_unmatched_cell_count,
            "prior_unmatched_cell_count": self.prior_unmatched_cell_count,
            "problem": self.problem,
            "customer_impact": self.customer_impact,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class MonthlySovSummary:
    sov_pct: float | None
    planned_count: int
    success_count: int
    failed_count: int
    excluded_count: int
    query_intent_snapshot: QueryIntentSource
    cells: tuple[ManifestCellInput, ...]
    platforms: tuple[PlatformSummary, ...]
    queries: tuple[QuerySummary, ...]
    segments: SegmentCollection
    comparison: ComparisonSummary

    def to_payload(self) -> MonthlySovPayload:
        return {
            "sov_pct": self.sov_pct,
            "prev_sov_pct": self.comparison.prior_sov_pct,
            "change_pct": self.comparison.change_pct,
            "planned_count": self.planned_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "excluded_count": self.excluded_count,
            "query_intent_snapshot": self.query_intent_snapshot,
            "cells": [cell.to_payload() for cell in self.cells],
            "platforms": [row.to_payload() for row in self.platforms],
            "queries": [row.to_payload() for row in self.queries],
            "segments": {
                "LOCAL": self.segments.local.to_payload(),
                "INFO": self.segments.info.to_payload(),
            },
            "comparison": self.comparison.to_payload(),
        }
