from app.services import sov_engine
from app.services.monthly_sov_types import (
    ComparisonSummary,
    ManifestCellInput,
    MonthlySovSummary,
    PlatformSummary,
    QueryIntent,
    QuerySummary,
    SegmentCollection,
    SegmentSummary,
)


def _macro_rate(
    cells: tuple[ManifestCellInput, ...],
    platforms: tuple[str, ...],
    intent: QueryIntent,
) -> float | None:
    platform_rates: list[float] = []
    for platform in platforms:
        attempts = tuple(
            cell.selected_attempt
            for cell in cells
            if cell.platform == platform and cell.query_intent == intent
        )
        selected = tuple(attempt for attempt in attempts if attempt is not None)
        if not selected:
            return None
        platform_rates.append(sum(attempt.is_mentioned for attempt in selected) / len(selected))
    return round(sum(platform_rates) / len(platform_rates) * 100, 2) if platform_rates else None


def _segment(
    cells: tuple[ManifestCellInput, ...], platforms: tuple[str, ...], intent: QueryIntent
) -> SegmentSummary:
    selected = tuple(
        attempt
        for cell in cells
        if cell.query_intent == intent
        for attempt in (cell.selected_attempt,)
        if attempt is not None
    )
    return SegmentSummary(
        measured_count=len(selected),
        mentioned_count=sum(attempt.is_mentioned for attempt in selected),
        mention_rate=_macro_rate(cells, platforms, intent),
    )


def _platform_summary(cells: tuple[ManifestCellInput, ...], platform: str) -> PlatformSummary:
    rows = tuple(cell for cell in cells if cell.platform == platform)
    local_attempts = tuple(
        attempt
        for cell in rows
        if cell.query_intent == "LOCAL"
        for attempt in (cell.selected_attempt,)
        if attempt is not None
    )
    selected_attempts = tuple(
        attempt
        for cell in rows
        for attempt in (cell.selected_attempt,)
        if attempt is not None
    )
    return PlatformSummary(
        platform=platform,
        cell_count=len(rows),
        planned_count=sum(cell.state != "EXCLUDED" for cell in rows),
        success_count=sum(cell.state == "SUCCESS" for cell in rows),
        failed_count=sum(cell.state == "FAILED" for cell in rows),
        excluded_count=sum(cell.state == "EXCLUDED" for cell in rows),
        measured_count=len(local_attempts),
        mentioned_count=sum(attempt.is_mentioned for attempt in local_attempts),
        mention_rate=(
            round(sum(attempt.is_mentioned for attempt in local_attempts) / len(local_attempts) * 100, 2)
            if local_attempts
            else None
        ),
        answer_models=tuple(
            sorted({attempt.answer_model for attempt in selected_attempts if attempt.answer_model})
        ),
        model_observation_complete=bool(selected_attempts)
        and all(attempt.answer_model for attempt in selected_attempts),
        search_observed_count=sum(attempt.search_calls is not None for attempt in selected_attempts),
        search_used_count=sum((attempt.search_calls or 0) > 0 for attempt in selected_attempts),
    )


def _query_summary(cells: tuple[ManifestCellInput, ...], query_key: str) -> QuerySummary:
    rows = tuple(cell for cell in cells if cell.query_key == query_key)
    selected = tuple(
        attempt for cell in rows for attempt in (cell.selected_attempt,) if attempt is not None
    )
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
        measured_count=len(selected),
        mentioned_count=sum(attempt.is_mentioned for attempt in selected),
    )


def _non_comparable(reason: str, *, current_unmatched: int, prior_unmatched: int) -> ComparisonSummary:
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
    return ComparisonSummary(
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
        next_action="이번 달 현재 수치만 전달하고, 전월 대비 상승·하락 표현은 사용하지 마세요.",
    )


def _comparison(
    current: tuple[ManifestCellInput, ...],
    current_platforms: tuple[str, ...],
    prior: tuple[ManifestCellInput, ...] | None,
    prior_platforms: tuple[str, ...] | None,
    current_protocol: dict | None = None,
    prior_protocol: dict | None = None,
) -> ComparisonSummary:
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
        if current_by_key[key].selected_attempt is not None
        and prior_by_key[key].selected_attempt is not None
    )
    matched_keys = set(matched)
    if any(not any(key[1] == platform for key in matched) for platform in current_platforms):
        return _non_comparable(
            "NO_MATCHED_CELLS",
            current_unmatched=len(current_keys - matched_keys),
            prior_unmatched=len(prior_keys - matched_keys),
        )

    model_pairs = tuple(
        (
            current_by_key[key].selected_attempt.answer_model,
            prior_by_key[key].selected_attempt.answer_model,
        )
        for key in matched
    )
    if any(current_model is None or prior_model is None for current_model, prior_model in model_pairs):
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
    current_score = _macro_rate(current_matched, current_platforms, "LOCAL")
    prior_score = _macro_rate(prior_matched, current_platforms, "LOCAL")
    change = (
        round(current_score - prior_score, 1)
        if current_score is not None and prior_score is not None
        else None
    )
    return ComparisonSummary(
        status="COMPARABLE",
        reason="MATCHED_COHORT",
        current_sov_pct=current_score,
        prior_sov_pct=prior_score,
        change_pct=change,
        matched_cell_count=len(matched),
        current_unmatched_cell_count=len(current_keys - matched_keys),
        prior_unmatched_cell_count=len(prior_keys - matched_keys),
        problem=None,
        customer_impact="같은 AI 서비스와 같은 질문으로 확인한 변화만 표시합니다.",
        next_action="전월 대비 수치는 같은 질문 기준 변화로 설명하세요.",
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
    return MonthlySovSummary(
        sov_pct=_macro_rate(cells, platforms, "LOCAL"),
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
        comparison=_comparison(
            cells, platforms, prior_cells, prior_platforms, current_protocol, prior_protocol
        ),
    )
