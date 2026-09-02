from dataclasses import dataclass

from app.services import sov_engine
from app.services.monthly_sov_types import (
    ComparisonSummary,
    ManifestCellInput,
    MeasurementBasis,
    MonthlySovSummary,
    PlatformSummary,
    QueryIntent,
    QuerySummary,
    SegmentCollection,
    SegmentSummary,
)
from app.services.sov_statistics import delta_significance, wilson_interval


def _scored_cells(
    cells: tuple[ManifestCellInput, ...], intent: QueryIntent
) -> tuple[ManifestCellInput, ...]:
    """점수에 실제로 들어가는 셀 — 성공 반복이 1건 이상 있는 셀만."""
    return tuple(
        cell for cell in cells if cell.query_intent == intent and cell.attempts_used > 0
    )


def _attempt_counts(
    cells: tuple[ManifestCellInput, ...], intent: QueryIntent
) -> tuple[int, int]:
    """(언급 시도 수 k, 전체 성공 시도 수 n) — 점 추정과 불확실성의 **공통 표본**."""
    scored = _scored_cells(cells, intent)
    return (
        sum(cell.mentioned_attempts for cell in scored),
        sum(cell.attempts_used for cell in scored),
    )


def _pooled_rate(
    cells: tuple[ManifestCellInput, ...],
    platforms: tuple[str, ...],
    intent: QueryIntent,
) -> float | None:
    """성공 반복 전체를 하나로 합친 언급 빈도(k/n)를 %로.

    **점 추정·95% 구간·전월 대비 델타·유의성이 모두 이 하나의 추정량 위에 선다.**
    예전에는 점 추정만 플랫폼 동일 가중 매크로(셀 빈도의 평균의 평균)였고 구간과
    유의성은 `_attempt_counts`의 풀드 k/n이었다. 두 추정량은 셀마다 반복 수·셀 수가
    다르면 서로 다른 값을 주므로, 점 추정이 자기 구간 밖에 놓이고 "12번 → 12번
    (의미 있는 상승입니다)" 같은 문장이 리포트에 나갈 수 있었다.

    플랫폼 동일 가중 자체는 버리지 않는다 — 플랫폼별 breakdown
    (`_platform_summary`)이 플랫폼마다 따로 계산해 그대로 공개한다.

    구성한 플랫폼 중 한 곳이라도 점수에 쓸 셀이 없으면 숫자를 만들지 않는다
    (기존 fail-closed 규칙 그대로 — 한 플랫폼이 통째로 실패한 달을 한 플랫폼짜리
    수치로 팔지 않는다).
    """
    scored = _scored_cells(cells, intent)
    if not scored or not platforms:
        return None
    for platform in platforms:
        if not any(cell.platform == platform for cell in scored):
            return None
    mentioned_attempts, attempts_used = _attempt_counts(cells, intent)
    if attempts_used <= 0:
        return None
    return round(mentioned_attempts / attempts_used * 100, 2)


def _segment(
    cells: tuple[ManifestCellInput, ...], platforms: tuple[str, ...], intent: QueryIntent
) -> SegmentSummary:
    scored = _scored_cells(cells, intent)
    mentioned_attempts, attempts_used = _attempt_counts(cells, intent)
    return SegmentSummary(
        measured_count=len(scored),
        # 셀 단위 "한 번이라도 언급된" 수. 셀 점수는 빈도지만 이 칸은 커버리지 공개용이다.
        mentioned_count=sum(cell.mentioned_attempts > 0 for cell in scored),
        mention_rate=_pooled_rate(cells, platforms, intent),
        attempts_used=attempts_used,
        mentioned_attempts=mentioned_attempts,
    )


def _platform_summary(cells: tuple[ManifestCellInput, ...], platform: str) -> PlatformSummary:
    rows = tuple(cell for cell in cells if cell.platform == platform)
    local_cells = _scored_cells(rows, "LOCAL")
    # 플랫폼 breakdown만 **셀 동일 가중**(셀 빈도의 평균)을 유지한다. 헤드라인은
    # 풀드 추정(`_pooled_rate`)이라 두 숫자가 다를 수 있고, 그건 의도된 것이다 —
    # 여기서 답하는 질문은 "이 AI 서비스에서는 어땠나"이지 "전체가 얼마인가"가 아니다.
    local_frequencies = [cell.mention_frequency or 0.0 for cell in local_cells]
    # 모델·검색 계측은 셀이 아니라 **모든 성공 시도**를 본다 — 반복 중 한 건만
    # 다른 모델로 답했어도 그 사실이 보여야 비교 게이트가 제 역할을 한다.
    scored_attempts = tuple(
        attempt for cell in rows for attempt in cell.successful_attempts
    )
    return PlatformSummary(
        platform=platform,
        cell_count=len(rows),
        planned_count=sum(cell.state != "EXCLUDED" for cell in rows),
        success_count=sum(cell.state == "SUCCESS" for cell in rows),
        failed_count=sum(cell.state == "FAILED" for cell in rows),
        excluded_count=sum(cell.state == "EXCLUDED" for cell in rows),
        measured_count=len(local_cells),
        mentioned_count=sum(cell.mentioned_attempts > 0 for cell in local_cells),
        mention_rate=(
            round(sum(local_frequencies) / len(local_frequencies) * 100, 2)
            if local_frequencies
            else None
        ),
        attempts_used=sum(cell.attempts_used for cell in local_cells),
        mentioned_attempts=sum(cell.mentioned_attempts for cell in local_cells),
        answer_models=tuple(
            sorted({attempt.answer_model for attempt in scored_attempts if attempt.answer_model})
        ),
        model_observation_complete=bool(scored_attempts)
        and all(attempt.answer_model for attempt in scored_attempts),
        search_observed_count=sum(attempt.search_calls is not None for attempt in scored_attempts),
        search_used_count=sum((attempt.search_calls or 0) > 0 for attempt in scored_attempts),
    )


def _query_summary(cells: tuple[ManifestCellInput, ...], query_key: str) -> QuerySummary:
    rows = tuple(cell for cell in cells if cell.query_key == query_key)
    scored = tuple(cell for cell in rows if cell.attempts_used > 0)
    first = rows[0]
    return QuerySummary(
        query_key=query_key,
        query_text=first.query_text,
        query_intent=first.query_intent,
        query_intent_source=first.query_intent_source,
        cell_count=len(rows),
        planned_count=sum(cell.state != "EXCLUDED" for cell in rows),
        success_count=sum(cell.state == "SUCCESS" for cell in rows),
        failed_count=sum(cell.state == "FAILED" for cell in rows),
        excluded_count=sum(cell.state == "EXCLUDED" for cell in rows),
        measured_count=len(scored),
        mentioned_count=sum(cell.mentioned_attempts > 0 for cell in scored),
        attempts_used=sum(cell.attempts_used for cell in scored),
        mentioned_attempts=sum(cell.mentioned_attempts for cell in scored),
    )


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """비교 판정과 **그 판정이 선 셀 집합**.

    헤드라인과 표본 각주가 델타와 같은 코호트를 쓰려면 매칭된 이번 달 셀이
    호출부까지 따라와야 한다. NON_COMPARABLE이면 빈 튜플이다.
    """

    summary: ComparisonSummary
    matched_current_cells: tuple[ManifestCellInput, ...]


def _non_comparable(
    reason: str, *, current_unmatched: int, prior_unmatched: int
) -> ComparisonResult:
    messages = {
        "NO_PRIOR_MANIFEST": "지난달에 같은 기준으로 고정한 측정표가 없습니다.",
        "PLATFORM_COHORT_MISSING": "이번 달과 지난달에 사용한 AI 서비스 구성이 다릅니다.",
        "NO_MATCHED_CELLS": "두 달에 공통으로 성공한 같은 질문이 없습니다.",
        "INTENT_SNAPSHOT_MISSING": "질문 유형을 월초 기준으로 고정한 기록이 없습니다.",
        "MEASUREMENT_POLICY_CHANGED": (
            "이번 달과 지난달의 측정 기준(지시문·검색 정책)이 다릅니다. "
            "수치 변화는 병원 성과가 아니라 측정 기준 변경일 수 있습니다."
        ),
        "ANSWER_MODEL_CHANGED": (
            "이번 달과 지난달에 실제로 답변한 AI 모델 구성이 다릅니다. "
            "수치 변화는 병원 성과가 아니라 모델 변경의 영향일 수 있습니다."
        ),
        "ANSWER_MODEL_UNKNOWN": "실제 응답 모델 기록이 없어 두 달의 측정 조건을 확인할 수 없습니다.",
    }
    return ComparisonResult(
        summary=ComparisonSummary(
            status="NON_COMPARABLE",
            reason=reason,
            current_sov_pct=None,
            prior_sov_pct=None,
            change_pct=None,
            matched_cell_count=0,
            current_unmatched_cell_count=current_unmatched,
            prior_unmatched_cell_count=prior_unmatched,
            problem=messages[reason],
            customer_impact="전월 대비 증감 숫자는 표시하지 않습니다.",
            next_action=(
                "이번 달 현재 수치만 전달하고, 전월 대비 상승·하락 표현은 사용하지 마세요."
            ),
        ),
        matched_current_cells=(),
    )


def _comparison(
    current: tuple[ManifestCellInput, ...],
    current_platforms: tuple[str, ...],
    prior: tuple[ManifestCellInput, ...] | None,
    prior_platforms: tuple[str, ...] | None,
    current_protocol: dict | None = None,
    prior_protocol: dict | None = None,
) -> ComparisonResult:
    current_keys = {
        (cell.query_key, cell.platform)
        for cell in current
        if cell.query_intent == "LOCAL" and cell.state != "EXCLUDED"
    }
    if prior is None or prior_platforms is None:
        return _non_comparable(
            "NO_PRIOR_MANIFEST", current_unmatched=len(current_keys), prior_unmatched=0
        )
    prior_keys = {
        (cell.query_key, cell.platform)
        for cell in prior
        if cell.query_intent == "LOCAL" and cell.state != "EXCLUDED"
    }
    if set(current_platforms) != set(prior_platforms):
        return _non_comparable(
            "PLATFORM_COHORT_MISSING",
            current_unmatched=len(current_keys - prior_keys),
            prior_unmatched=len(prior_keys - current_keys),
        )
    # 측정 정책이 바뀐 두 달은 같은 질문·같은 플랫폼이라도 비교하지 않는다 — 지시문이나
    # 검색 강제 여부가 다르면 수치 차이가 병원 성과인지 측정 기준 변경인지 가를 수 없다.
    #
    # **스냅샷이 없는 쪽이 하나라도 있으면 비교하지 않는다.** 처음에는 "둘 다 없으면 같은
    # 이전 세대"로 허용했지만, 그 규칙은 배포 이전에만 안전하다: v2 배포 후에 스냅샷 없는
    # manifest(배포 전에 동결된 이번 달)에 v2 재측정이 섞이면, 없음=없음이 "같다"로 접혀
    # v1/v2 혼합 월이 비교 가능으로 팔린다. 전환 월의 추세 단절은 의도된 비용이다.
    if not sov_engine.same_measurement_basis(
        current_protocol,
        prior_protocol,
        platforms=current_platforms,
    ):
        return _non_comparable(
            "MEASUREMENT_POLICY_CHANGED",
            current_unmatched=len(current_keys),
            prior_unmatched=sum(
                cell.query_intent == "LOCAL" and cell.state != "EXCLUDED" for cell in prior
            ),
        )
    if any(cell.query_intent_source == "LEGACY_LIVE" for cell in (*current, *prior)):
        return _non_comparable(
            "INTENT_SNAPSHOT_MISSING",
            current_unmatched=len(current_keys),
            prior_unmatched=sum(
                cell.query_intent == "LOCAL" and cell.state != "EXCLUDED" for cell in prior
            ),
        )

    current_by_key = {(cell.query_key, cell.platform): cell for cell in current}
    prior_by_key = {(cell.query_key, cell.platform): cell for cell in prior}
    matched = tuple(
        key
        for key in sorted(current_keys & prior_keys)
        if current_by_key[key].attempts_used > 0 and prior_by_key[key].attempts_used > 0
    )
    matched_keys = set(matched)
    if any(not any(key[1] == platform for key in matched) for platform in current_platforms):
        return _non_comparable(
            "NO_MATCHED_CELLS",
            current_unmatched=len(current_keys - matched_keys),
            prior_unmatched=len(prior_keys - matched_keys),
        )

    # 응답 모델은 대표 시도가 아니라 **매칭 셀의 모든 성공 시도**에서 모은다. 반복
    # 5회 중 한 건만 다른 모델로 답해도 두 달의 측정 조건은 같지 않다.
    def _models(cell: ManifestCellInput) -> tuple[str | None, ...]:
        return tuple(
            sorted(
                {attempt.answer_model for attempt in cell.successful_attempts},
                key=lambda value: (value is None, value or ""),
            )
        )

    model_pairs = tuple((_models(current_by_key[key]), _models(prior_by_key[key])) for key in matched)
    if any(
        None in current_model or None in prior_model
        for current_model, prior_model in model_pairs
    ):
        return _non_comparable(
            "ANSWER_MODEL_UNKNOWN",
            current_unmatched=len(current_keys - matched_keys),
            prior_unmatched=len(prior_keys - matched_keys),
        )
    if any(current_model != prior_model for current_model, prior_model in model_pairs):
        return _non_comparable(
            "ANSWER_MODEL_CHANGED",
            current_unmatched=len(current_keys - matched_keys),
            prior_unmatched=len(prior_keys - matched_keys),
        )

    current_matched = tuple(current_by_key[key] for key in matched)
    prior_matched = tuple(prior_by_key[key] for key in matched)
    # 두 달 모두 **같은 매칭 셀 집합** 위에서 시도를 센다. 이 대칭이 깨지면
    # 헤드라인과 델타가 다른 분모를 쓰게 되고, 그게 예전의 결함이었다.
    current_mentioned, current_attempts = _attempt_counts(current_matched, "LOCAL")
    prior_mentioned, prior_attempts = _attempt_counts(prior_matched, "LOCAL")
    # 점 추정도 구간·유의성과 **같은 풀드 표본**에서 나온다.
    current_score = _pooled_rate(current_matched, current_platforms, "LOCAL")
    prior_score = _pooled_rate(prior_matched, current_platforms, "LOCAL")
    change = (
        round(current_score - prior_score, 1)
        if current_score is not None and prior_score is not None
        else None
    )
    current_interval = wilson_interval(current_mentioned, current_attempts)
    prior_interval = wilson_interval(prior_mentioned, prior_attempts)
    return ComparisonResult(
        summary=ComparisonSummary(
            status="COMPARABLE",
            reason="MATCHED_COHORT",
            current_sov_pct=current_score,
            prior_sov_pct=prior_score,
            change_pct=change,
            matched_cell_count=len(matched),
            current_unmatched_cell_count=len(current_keys - matched_keys),
            prior_unmatched_cell_count=len(prior_keys - matched_keys),
            current_attempts_used=current_attempts,
            current_mentioned_attempts=current_mentioned,
            prior_attempts_used=prior_attempts,
            prior_mentioned_attempts=prior_mentioned,
            current_ci95_low=None if current_interval is None else current_interval.low_pct,
            current_ci95_high=None if current_interval is None else current_interval.high_pct,
            prior_ci95_low=None if prior_interval is None else prior_interval.low_pct,
            prior_ci95_high=None if prior_interval is None else prior_interval.high_pct,
            significance=delta_significance(
                current_mentioned, current_attempts, prior_mentioned, prior_attempts
            ),
            problem=None,
            customer_impact="같은 AI 서비스와 같은 질문으로 확인한 변화만 표시합니다.",
            next_action="전월 대비 수치는 같은 질문 기준 변화로 설명하세요.",
        ),
        matched_current_cells=current_matched,
    )


def build_monthly_sov(
    cells: tuple[ManifestCellInput, ...],
    configured_platforms: tuple[str, ...],
    *,
    prior_cells: tuple[ManifestCellInput, ...] | None = None,
    prior_platforms: tuple[str, ...] | None = None,
    current_protocol: dict | None = None,
    prior_protocol: dict | None = None,
) -> MonthlySovSummary:
    platforms = tuple(dict.fromkeys(configured_platforms))
    queries = tuple(
        _query_summary(cells, query_key) for query_key in sorted({cell.query_key for cell in cells})
    )
    result = _comparison(
        cells, platforms, prior_cells, prior_platforms, current_protocol, prior_protocol
    )
    comparison = result.summary
    all_cells_rate = _pooled_rate(cells, platforms, "LOCAL")
    # **헤드라인과 델타는 같은 분모를 쓴다.** 비교가 성립하는 달에는 헤드라인·표본·
    # 오차 범위가 모두 매칭 코호트 기준이고, 전 셀 기준 수치는 sov_pct_all_cells로
    # 따로 공개한다. 예전에는 헤드라인만 전 셀이라 30셀과 29셀을 나란히 놓고 증감이라 불렀다.
    headline_on_cohort = (
        comparison.status == "COMPARABLE" and comparison.current_sov_pct is not None
    )
    headline_rate = comparison.current_sov_pct if headline_on_cohort else all_cells_rate
    headline_cells = result.matched_current_cells if headline_on_cohort else cells
    mentioned_attempts, attempts_used = _attempt_counts(headline_cells, "LOCAL")
    interval = wilson_interval(mentioned_attempts, attempts_used)
    scored = _scored_cells(headline_cells, "LOCAL")
    return MonthlySovSummary(
        sov_pct=headline_rate,
        sov_pct_all_cells=all_cells_rate,
        attempts_used=attempts_used,
        mentioned_attempts=mentioned_attempts,
        ci95_low=None if interval is None else interval.low_pct,
        ci95_high=None if interval is None else interval.high_pct,
        margin_of_hundred=None if interval is None else interval.margin_of_hundred,
        measurement_basis=MeasurementBasis(
            question_count=len({cell.query_key for cell in scored}),
            platform_count=len({cell.platform for cell in scored}),
            cell_count=len(scored),
            # 셀당 평균 반복 수. 구버전 manifest(1회)는 1, 현행은 5다.
            repeat_count=(
                round(sum(cell.attempts_used for cell in scored) / len(scored)) if scored else 0
            ),
            # 평균만으로는 부분 측정된 달을 설명할 수 없다 — 5회와 1회가 섞인 달도
            # 각주에는 "반복 3회 기준"으로 나가 실제로 존재하지 않은 표본을 말한다.
            # 최소·최대를 함께 남겨 각주가 "3~5회"라고 정직하게 쓰게 한다.
            repeat_min=min((cell.attempts_used for cell in scored), default=0),
            repeat_max=max((cell.attempts_used for cell in scored), default=0),
            attempts_used=sum(cell.attempts_used for cell in scored),
        ),
        planned_count=sum(cell.state != "EXCLUDED" for cell in cells),
        success_count=sum(cell.state == "SUCCESS" for cell in cells),
        failed_count=sum(cell.state == "FAILED" for cell in cells),
        excluded_count=sum(cell.state == "EXCLUDED" for cell in cells),
        query_intent_snapshot=(
            "LEGACY_LIVE"
            if any(cell.query_intent_source == "LEGACY_LIVE" for cell in cells)
            else "FROZEN"
        ),
        cells=cells,
        platforms=tuple(_platform_summary(cells, platform) for platform in platforms),
        queries=queries,
        segments=SegmentCollection(
            local=_segment(cells, platforms, "LOCAL"),
            info=_segment(cells, platforms, "INFO"),
        ),
        comparison=comparison,
    )
