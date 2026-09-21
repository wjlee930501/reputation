"""Safe query-specific proposals built only from the lead allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.lead_report import LeadReportPayload, QueryDisclosure

Opportunity = Literal["UNAVAILABLE", "ZERO", "PARTIAL", "HIGH"]


@dataclass(frozen=True, slots=True)
class QueryProposal:
    question: str
    state: Opportunity
    known: str
    check: str
    action: str


@dataclass(frozen=True, slots=True)
class LeadProposalView:
    headline: str
    proposals: tuple[QueryProposal, ...]


def _proposal(query: QueryDisclosure) -> QueryProposal:
    if not query.measured:
        state: Opportunity = "UNAVAILABLE"
        known = "확정 관측이 없어 언급 여부를 판단할 수 없습니다."
        action = "동일 질문의 측정을 먼저 복구하고, 확정 관측을 다음 운영의 기준선으로 남깁니다."
    elif query.mentioned == 0:
        state = "ZERO"
        known = f"이 질문의 확정 반복 {query.measured}회 중 병원 언급 0회입니다."
        action = "공식 근거를 확인해 이 질문의 안내를 공개 허브에 발행하고, 같은 질문의 언급·출처 연결을 재측정합니다."
    elif (
        query.mentioned < query.measured
        or query.failed
        or query.ambiguous
        or query.measured < query.planned
    ):
        state = "PARTIAL"
        known = f"이 질문의 확정 반복 {query.measured}회 중 언급 {query.mentioned}회입니다. 누락 관측은 별도입니다."
        action = "공식 자료의 범위와 표현을 점검해 안내를 보완하고, 같은 질문의 언급·출처 연결을 다시 확인합니다."
    else:
        state = "HIGH"
        known = f"이 질문의 확정 반복 {query.measured}회 모두에서 언급됐습니다. 다른 질문의 성과를 뜻하지 않습니다."
        action = "현재 답할 수 있는 정보를 최신 상태로 운영하고, 같은 질문의 언급 유지와 출처 연결을 추적합니다."
    focus = "진료 범위·절차·유의사항"
    if "검진" in query.text or "내시경" in query.text:
        focus = "검사 대상·예약 전 준비·결과 상담 절차"
    elif "만성" in query.text or "당뇨" in query.text or "고혈압" in query.text:
        focus = "정기 상담·추적 관리 범위·내원 전 확인 사항"
    elif "접종" in query.text:
        focus = "제공하는 예방접종·대상 확인·사전 상담 절차"
    check = f"{focus}를 설명하는 질문별 안내. 병원 공식 자료에서 확인한 범위만 담습니다."
    return QueryProposal(query.text, state, known, check, action)


def build_lead_proposal(payload: LeadReportPayload) -> LeadProposalView:
    proposals = tuple(_proposal(query) for query in payload.queries)
    order = {"UNAVAILABLE": 0, "ZERO": 1, "PARTIAL": 2, "HIGH": 3}
    selected = tuple(sorted(proposals, key=lambda item: order[item.state])[:2])
    if not payload.total_measured:
        headline = "판단을 서두르기보다, 현재 관측부터 확보할 때입니다."
    elif not payload.total_mentioned:
        headline = "우리 병원이 필요한 질문, 아직 남아 있습니다."
    elif all(item.state == "HIGH" for item in proposals) and proposals:
        headline = "확인된 언급을 유지하며 다음 질문을 넓힐 때입니다."
    else:
        headline = (
            "우리 병원이 필요한 질문, 아직 남아 있습니다."
            if any(query.measured > query.mentioned for query in payload.queries)
            else "확인된 언급을 기준으로, 남은 측정을 채웁니다."
        )
    return LeadProposalView(headline, selected)


def validate_proposal_input(payload: LeadReportPayload) -> None:
    """Reject absurd fields rather than clip evidence or silently shrink the font."""
    limits = (
        (payload.hospital_name, 200),
        (payload.region, 100),
        (payload.system_prompt, 8000),
        (payload.judge_model, 200),
    )
    if any(len(value) > limit or "\x00" in value for value, limit in limits):
        raise ValueError("LEAD_REPORT_FIELD_LIMIT")
    if len(payload.queries) > 128 or len(payload.segments) > 8:
        raise ValueError("LEAD_REPORT_RECORD_LIMIT")
    for query in payload.queries:
        if len(query.text) > 1000 or "\x00" in query.text:
            raise ValueError("LEAD_REPORT_QUERY_LIMIT")
        if (
            min(query.planned, query.measured, query.mentioned, query.failed, query.ambiguous) < 0
            or query.mentioned > query.measured
            or query.measured + query.failed + query.ambiguous > query.planned
        ):
            raise ValueError("LEAD_REPORT_COUNTS_INVALID")
    for segment in payload.segments:
        if (
            min(
                segment.planned,
                segment.measured,
                segment.mentioned,
                segment.failed,
                segment.ambiguous,
            )
            < 0
            or segment.mentioned > segment.measured
            or segment.measured + segment.failed + segment.ambiguous > segment.planned
        ):
            raise ValueError("LEAD_REPORT_COUNTS_INVALID")
