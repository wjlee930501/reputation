from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.services.monthly_sov_payload import (
    CellPayload,
    CellState,
    ComparabilityStatus,
    ComparisonPayload,
    MeasurementBasisPayload,
    MonthlySovPayload,
    PlatformPayload,
    QueryIntent,
    QueryIntentSource,
    QueryPayload,
    SegmentPayload,
)
from app.services.sov_statistics import DeltaSignificance


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
    # 답변에서 병원이 언급된 문맥. 대표 응답(evidence)을 고를 때만 쓰고 점수에는 쓰지 않는다.
    mention_context: str | None = None


def _representative_sort_key(attempt: CellAttempt) -> tuple[bool, int, bool, float, int]:
    """대표 응답 정렬 키. 전부 원시 스칼라라 measured_at이 None이어도 비교가 깨지지 않는다."""
    measured_at = attempt.measured_at
    return (
        not attempt.is_mentioned,
        -len(attempt.mention_context or ""),
        measured_at is None,
        measured_at.timestamp() if measured_at is not None else 0.0,
        attempt.record_id.int,
    )


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
    def successful_attempts(self) -> tuple[CellAttempt, ...]:
        """점수에 쓰는 성공 반복 전부.

        셀 상태와 실제 시도가 어긋난 손상 입력(state=SUCCESS인데 성공 시도 0건,
        state=FAILED인데 시도가 남아 있음)은 여기서 닫힌다 — 그런 셀은 점수에서
        빠지고 상태 수와 측정 수의 차이로 드러난다.
        """
        if self.state != "SUCCESS":
            return ()
        return tuple(attempt for attempt in self.attempts if attempt.succeeded)

    @property
    def attempts_used(self) -> int:
        """이 셀에서 점수에 쓴 반복 수(n). 구버전 manifest는 1일 수 있다."""
        return len(self.successful_attempts)

    @property
    def mentioned_attempts(self) -> int:
        """반복 중 병원이 언급된 횟수(k)."""
        return sum(attempt.is_mentioned for attempt in self.successful_attempts)

    @property
    def mention_frequency(self) -> float | None:
        """셀 점수 = k/n. 성공 반복이 없으면 0으로 꾸미지 않고 None이다.

        예전에는 성공 1건만 골라 이진값(0 또는 1)을 셀 점수로 썼다. 반복을 5회씩
        결제하면서 4건을 버렸고, 동시 실행이라 measured_at이 동률이면 tie-break가
        UUID였으므로 사실상 무작위 1건이었다. 이제 결제한 표본을 전부 쓴다.
        """
        used = self.successful_attempts
        if not used:
            return None
        return sum(attempt.is_mentioned for attempt in used) / len(used)

    @property
    def selected_attempt(self) -> CellAttempt | None:
        """원장 리포트의 '나온 사례/안 나온 사례'에 붙일 **대표 응답 1건**.

        점수 계산에는 더 이상 쓰지 않는다(그건 ``mention_frequency``다). 대표
        선택 규칙은 결정적이어야 한다 — 예전 규칙은 동시 실행으로 동률이 된
        measured_at을 UUID로 깨서 리포트를 다시 만들 때마다 인용문이 바뀔 수
        있었다. 규칙(우선순위 순):

        1. 언급된 시도를 언급 안 된 시도보다 먼저 — 근거로 보여줄 값이 있다.
        2. 그중 mention_context가 긴 것 — 원장이 읽을 문맥이 많은 응답.
        3. measured_at이 이른 것(없는 값은 뒤로).
        4. 마지막 동률은 record_id — 같은 입력이면 항상 같은 결과가 나오게.
        """
        used = self.successful_attempts
        if not used:
            return None
        return min(used, key=_representative_sort_key)

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
            "attempts_used": self.attempts_used,
            "mentioned_attempts": self.mentioned_attempts,
            "mention_frequency": (
                None
                if self.mention_frequency is None
                else round(self.mention_frequency, 4)
            ),
        }


@dataclass(frozen=True, slots=True)
class SegmentSummary:
    measured_count: int
    mentioned_count: int
    mention_rate: float | None
    attempts_used: int = 0
    mentioned_attempts: int = 0

    def to_payload(self) -> SegmentPayload:
        return {
            "measured_count": self.measured_count,
            "mentioned_count": self.mentioned_count,
            "mention_rate": self.mention_rate,
            "attempts_used": self.attempts_used,
            "mentioned_attempts": self.mentioned_attempts,
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
    attempts_used: int
    mentioned_attempts: int
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
            "attempts_used": self.attempts_used,
            "mentioned_attempts": self.mentioned_attempts,
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
    attempts_used: int = 0
    mentioned_attempts: int = 0

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
            "attempts_used": self.attempts_used,
            "mentioned_attempts": self.mentioned_attempts,
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
    # 매칭 코호트 위의 반복 표본. 두 달이 **같은 셀 집합**을 세므로 분모가 어긋나지 않는다.
    current_attempts_used: int = 0
    current_mentioned_attempts: int = 0
    prior_attempts_used: int = 0
    prior_mentioned_attempts: int = 0
    current_ci95_low: float | None = None
    current_ci95_high: float | None = None
    prior_ci95_low: float | None = None
    prior_ci95_high: float | None = None
    significance: DeltaSignificance | None = None

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
            "current_attempts_used": self.current_attempts_used,
            "current_mentioned_attempts": self.current_mentioned_attempts,
            "prior_attempts_used": self.prior_attempts_used,
            "prior_mentioned_attempts": self.prior_mentioned_attempts,
            "current_ci95_low": self.current_ci95_low,
            "current_ci95_high": self.current_ci95_high,
            "prior_ci95_low": self.prior_ci95_low,
            "prior_ci95_high": self.prior_ci95_high,
            "significance": self.significance,
            "problem": self.problem,
            "customer_impact": self.customer_impact,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class MeasurementBasis:
    """헤드라인이 선 표본의 모양(질문 수 × AI 서비스 수 × 반복 수)."""

    question_count: int
    platform_count: int
    cell_count: int
    repeat_count: int
    attempts_used: int
    # 셀별 반복 수의 최소·최대. `repeat_count`(평균)만으로는 부분 측정된 달을
    # 설명할 수 없어 각주가 존재하지 않은 표본("반복 3회")을 말하게 된다.
    # 구버전 payload에는 없으므로 기본값 0이고, 읽는 쪽이 0을 "모름"으로 다룬다.
    repeat_min: int = 0
    repeat_max: int = 0

    def to_payload(self) -> MeasurementBasisPayload:
        return {
            "question_count": self.question_count,
            "platform_count": self.platform_count,
            "cell_count": self.cell_count,
            "repeat_count": self.repeat_count,
            "repeat_min": self.repeat_min,
            "repeat_max": self.repeat_max,
            "attempts_used": self.attempts_used,
        }


@dataclass(frozen=True, slots=True)
class MonthlySovSummary:
    # 비교 가능한 달에는 매칭 코호트 기준, 아니면 전체 셀 기준. `prev_sov_pct`와
    # 언제나 같은 분모를 쓰기 위한 것이다 — 예전에는 헤드라인만 전 셀이었다.
    sov_pct: float | None
    sov_pct_all_cells: float | None
    attempts_used: int
    mentioned_attempts: int
    ci95_low: float | None
    ci95_high: float | None
    margin_of_hundred: int | None
    measurement_basis: MeasurementBasis
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

    @property
    def mention_frequency(self) -> float | None:
        """헤드라인을 0~1 빈도로. attempts_used가 0이면 0으로 꾸미지 않는다."""
        if not self.attempts_used:
            return None
        return self.mentioned_attempts / self.attempts_used

    @property
    def significance(self) -> DeltaSignificance | None:
        return self.comparison.significance

    def to_payload(self) -> MonthlySovPayload:
        return {
            "sov_pct": self.sov_pct,
            "sov_pct_all_cells": self.sov_pct_all_cells,
            "prev_sov_pct": self.comparison.prior_sov_pct,
            "change_pct": self.comparison.change_pct,
            "attempts_used": self.attempts_used,
            "mentioned_attempts": self.mentioned_attempts,
            "mention_frequency": (
                None if self.mention_frequency is None else round(self.mention_frequency, 4)
            ),
            "ci95_low": self.ci95_low,
            "ci95_high": self.ci95_high,
            "margin_of_hundred": self.margin_of_hundred,
            "significance": self.significance,
            "measurement_basis": self.measurement_basis.to_payload(),
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
