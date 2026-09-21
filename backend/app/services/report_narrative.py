"""Deterministic customer narrative; no provider calls or inferred causality."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from app.services.monthly_sov_payload import MonthlySovPayload
from app.services.report_attribution import CitationSummaryPayload, ContentAttributionPayload

ReportKind = Literal["LEGACY", "MONTHLY", "INITIAL"]


@dataclass(frozen=True, slots=True)
class PublishedWork:
    title: str
    content_id: str
    url: str | None
    cited_cells: int | None
    queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MonthlyNarrative:
    title: str
    conclusion: str
    current: float | None
    previous: float | None
    comparison_note: str
    denominator: str
    priorities: tuple[str, ...]
    works: tuple[PublishedWork, ...]
    platform_details: tuple[str, ...]
    methods: tuple[str, ...]
    citation_scope: str
    citation_details: tuple[str, ...]
    fulfillment_note: str


def build_monthly_narrative(
    *,
    kind: ReportKind,
    coverage: MonthlySovPayload | None,
    attribution: ContentAttributionPayload | None,
    citations: CitationSummaryPayload | None,
    works: tuple[PublishedWork, ...],
    current: float | None,
    previous: float | None,
    comparison_reason: str | None,
    shortfall: int,
    protocol_label: str | None = None,
) -> MonthlyNarrative:
    data = coverage or {}
    comparison = data.get("comparison") or {}
    comparable = (
        kind == "MONTHLY"
        and comparison.get("status") == "COMPARABLE"
        and comparison.get("reason") == "MATCHED_COHORT"
        and comparison_reason in (None, "MATCHED_COHORT")
        and comparison.get("matched_cell_count", 0) > 0
        and all(
            value is not None and isfinite(value) and 0 <= value <= 100
            for value in (comparison.get("prior_sov_pct"), comparison.get("current_sov_pct"))
        )
    )
    prior = comparison.get("prior_sov_pct") if comparable else None
    value = comparison.get("current_sov_pct") if comparable else current
    if value is None:
        conclusion = "이번 달은 노출 변화를 판단할 수 없습니다."
    elif kind == "INITIAL" or prior is None:
        conclusion = "이번 달 결과를 다음 비교의 기준으로 남깁니다."
    elif value > prior:
        conclusion = "동일한 질문에서 병원 언급이 더 자주 확인됐습니다."
    elif value < prior:
        conclusion = "줄어든 질문부터 다음 운영을 조정합니다."
    else:
        conclusion = "같은 조건의 관측값에 변화가 없습니다."
    reason = comparison.get("reason") or comparison_reason
    note = "같은 질문·플랫폼·모델·측정 기준이 확인된 조합만 비교합니다."
    if not comparable:
        reasons = {
            "NO_PRIOR_MANIFEST": "이전 유효 측정이 없어 증감을 계산하지 않습니다. 최초 측정은 서비스 전 성과가 아닙니다.",
            "ANSWER_MODEL_CHANGED": "응답 모델이 달라 증감을 계산하지 않습니다.",
            "MEASUREMENT_POLICY_CHANGED": "측정 기준이 달라 증감을 계산하지 않습니다.",
            "QUERY_TEXT_CHANGED": "질문 문장이 달라 증감을 계산하지 않습니다.",
            "PLATFORM_COHORT_MISSING": "두 기간의 AI 플랫폼 구성이 달라 증감을 계산하지 않습니다.",
            "INTENT_SNAPSHOT_MISSING": "이전 질문 유형의 고정 기록이 없어 비교할 수 없습니다.",
            "NO_MATCHED_CELLS": "두 기간에 공통으로 확정된 질문·플랫폼 조합이 없어 비교할 수 없습니다.",
            "ANSWER_MODEL_UNKNOWN": "실제 응답 모델 기록이 없어 같은 조건인지 확인할 수 없습니다.",
            "SAMPLE_SHAPE_CHANGED": "질문별 반복 관측 구성이 달라 증감을 계산하지 않습니다.",
        }
        note = reasons.get(
            reason,
            "질문·플랫폼·모델·측정 기준의 비교 가능성을 확인하지 못해 증감을 표시하지 않습니다.",
        )
    cells = (
        comparison.get("matched_cell_count")
        if comparable
        else (data.get("measurement_basis") or {}).get("cell_count")
    )
    scope_label = (
        f"질문×플랫폼 {cells}개 조합" if cells is not None else "질문×플랫폼 조합 수 기록 없음"
    )
    attempts = comparison.get("current_attempts_used") if comparable else data.get("attempts_used")
    mentions = (
        comparison.get("current_mentioned_attempts")
        if comparable
        else data.get("mentioned_attempts")
    )
    counts = (
        f"확정 반복 {attempts}회 중 언급 {mentions}회"
        if type(attempts) is int and type(mentions) is int
        else "확정 반복 건수 기록 없음"
    )
    denominator = (
        f"{scope_label} · {counts} · 확정 반복을 합산한 언급 비율(%) · 환자 수가 아닙니다."
    )
    priorities: list[str] = []
    if value is None:
        priorities.append(
            "측정 복구를 우선합니다. 같은 질문·플랫폼의 판정 확정 후 현재 기준선을 다시 확인합니다."
        )
    for row in (attribution or {}).get("lost_mention_cells", []) if comparable else []:
        priorities.append(
            f"‘{row['query_text']}’ · {row['platform_label']}에서 빠진 언급을 재확인하고 공식 진료 자료를 보완합니다."
        )
    lost_questions = (
        {row["query_text"] for row in (attribution or {}).get("lost_mention_cells", [])}
        if comparable
        else set()
    )
    for row in (attribution or {}).get("question_rows", []):
        if row["query_text"] in lost_questions:
            continue
        if row.get("current_attempts_used", 0) and not row.get("current_mentioned_attempts", 0):
            priorities.append(
                f"‘{row['query_text']}’ · 확정 관측에서 미언급. 이 질문에 답할 공식 자료를 먼저 정리합니다."
            )
    if not priorities and comparable:
        for row in (attribution or {}).get("new_mention_cells", [])[:2]:
            priorities.append(
                f"제안 — ‘{row['query_text']}’ ({row['platform_label']}): 새 언급이 다음 관측에서도 유지되는지 같은 조건으로 확인합니다."
            )
    if not priorities:
        priorities.append(
            "제안 — 다음 회차에도 같은 질문을 확인하고, 부족한 관측이 있으면 먼저 보완합니다."
        )
    platforms: list[str] = []
    methods: list[str] = [
        f"측정 기준: {protocol_label or '기록 없음 — 현재 설정으로 대체하지 않았습니다'}"
    ]
    for row in data.get("platforms", []):
        name = "OpenAI API" if row["platform"] == "chatgpt" else "Google Gemini API"
        rate = row.get("mention_rate")
        score = f"{rate:.1f}%" if rate is not None else "측정 미완료"
        platforms.append(
            f"{name} · 질문별 평균 {score} · 확정 반복 {row.get('attempts_used', 0)}회 중 언급 {row.get('mentioned_attempts', 0)}회 · 조합 {row.get('planned_count', 0)}개 중 성공 {row.get('success_count', 0)} / 실패 {row.get('failed_count', 0)} / 제외 {row.get('excluded_count', 0)}"
        )
        slots = [
            cell["observation_slots"]
            for cell in data.get("cells", [])
            if cell["platform"] == row["platform"] and "observation_slots" in cell
        ]
        if slots:
            counts = {
                key: sum(int(slot.get(key, 0)) for slot in slots)
                for key in (
                    "planned",
                    "confirmed",
                    "ambiguous",
                    "answer_failed",
                    "judgment_failed",
                    "pending",
                )
            }
            methods.append(
                f"{name} 반복 슬롯: 계획 {counts['planned']} / 확정 {counts['confirmed']} / 판정 보류 {counts['ambiguous']} / 응답 실패 {counts['answer_failed']} / 판정 실패 {counts['judgment_failed']} / 대기 {counts['pending']}회"
            )
        methods.append(
            f"{name} 응답 모델: {', '.join(row.get('answer_models', [])) or '기록 없음'}"
        )
    adequate = data.get("observation_adequacy") or {}
    if adequate.get("lineage") == "SLOTTED":
        status_label = {
            "COMPLETE": "전체 확정",
            "LIMITED": "일부 확정",
            "UNAVAILABLE": "확정 관측 없음",
        }.get(adequate.get("status"), "충분성 확인 필요")
        methods.append(
            f"반복 관측 슬롯: 계획 {adequate.get('planned_slots', '기록 없음')}회 / 확정 {adequate.get('confirmed_slots', '기록 없음')}회 · 상태 {status_label}"
        )
    elif adequate:
        methods.append(
            "반복 관측 슬롯 이력: 일부만 확인 가능 — 아래 건수를 전체 측정으로 해석하지 않습니다."
            if adequate.get("lineage") == "MIXED"
            else "반복 관측 슬롯 이력: 기록 미확인 — 계획·확정 건수를 0으로 표시하지 않습니다."
        )
    cite = citations or {}
    scope = (
        f"소유 URL 인용: 확인한 질문×플랫폼 {cite.get('measured_cell_count', 0)}개 조합 중 {cite.get('cited_cell_count', 0)}개. "
        "제목 유사도는 인용 증거가 아니며, 인용·언급만으로 운영의 인과 효과를 증명할 수 없습니다."
    )
    details: list[str] = []
    for item in cite.get("cited_items", []):
        details.append(
            f"소유 글 인용 · {item.get('title') or '제목 없음'} · {item['cited_cell_count']}개 질문×플랫폼 조합"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    for item in cite.get("hub_pages", []):
        details.append(
            f"소유 허브 인용 · {item['label']} · {item['cited_cell_count']}개 질문×플랫폼 조합"
        )
        details.extend(
            f"{query['query_text']} · {query['platform_label']}"
            for query in item.get("queries", [])
        )
    if citations is not None and not cite.get("measured_cell_count"):
        scope = (
            "확정 관측이 없어 소유 URL 인용 여부를 확인할 수 없습니다. 미확인은 인용 0이 아닙니다."
        )
    if citations is None:
        scope = "소유 URL 인용 집계가 없습니다. 미확인은 인용 0이 아니며 운영의 인과 효과도 판단하지 않습니다."
    return MonthlyNarrative(
        title="초기 기준선 보고서" if kind == "INITIAL" else "월간 AI 노출 변화·기여 보고서",
        conclusion=conclusion,
        current=value,
        previous=prior,
        comparison_note=note,
        denominator=denominator,
        priorities=tuple(dict.fromkeys(priorities)),
        works=tuple(sorted(works, key=lambda work: -(work.cited_cells or 0))),
        platform_details=tuple(platforms),
        methods=tuple(methods),
        citation_scope=scope,
        citation_details=tuple(details),
        fulfillment_note=(
            f"약정 미이행 {shortfall}편: 차단 원인을 확인하고 안전 기준을 통과한 글부터 보충합니다."
            if shortfall
            else "발행 기록을 바탕으로 다음 운영할 질문과 자료를 준비합니다."
        ),
    )
