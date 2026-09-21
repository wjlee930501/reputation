# /// script
# requires-python = ">=3.11"
# ///
"""Explicitly fictional report inputs. No database or network access."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.services.lead_report import (
    LeadReportContact,
    LeadReportPayload,
    PlatformSegment,
    QueryDisclosure,
)
from app.services.report_engine import build_doctor_report_view

WHEN = datetime(2026, 8, 31, 3, tzinfo=UTC)
HOSPITAL_ID = UUID("00000000-0000-4000-8000-000000000001")
CONTENT_ID = UUID("00000000-0000-4000-8000-000000000002")
PUBLIC_URL = "https://fictional-clinic.example.invalid/"


def monthly_sample(state: str, *, dense: bool = False):
    hospital = SimpleNamespace(
        id=HOSPITAL_ID,
        name=(
            "검증용 가상 서울한마음가정의학과건강검진센터의원"
            if dense
            else "검증용 가상 새봄가정의학과의원"
        ),
        slug="fictional-clinic",
        aeo_domain="fictional-clinic.example.invalid",
    )
    value = {"growth": 66.7, "decline": 16.7, "flat": 33.3, "unavailable": None}[state]
    previous = 33.3
    unavailable = value is None
    questions = [
        f"가상동 검증 질문 {index + 1:03d} — {'건강검진 상담을 받을 수 있는 곳은 어디인가요?' if index % 2 else '만성질환 상담을 받을 수 있는 곳은 어디인가요?'}"
        for index in range(36 if dense else 3)
    ]
    counts = [
        (
            [6, 0, 6]
            if state == "growth"
            else [0, 0, 3]
            if state == "decline"
            else [2, 2, 2]
        )[index % 3]
        for index in range(len(questions))
    ]
    value = None if unavailable else round(sum(counts) / (len(questions) * 6) * 100, 1)
    comparison = {
        "status": "NON_COMPARABLE" if unavailable else "COMPARABLE",
        "reason": "NO_MATCHED_CELLS" if unavailable else "MATCHED_COHORT",
        "matched_cell_count": 0 if unavailable else len(questions) * 2,
        "current_sov_pct": value,
        "current_attempts_used": 0 if unavailable else len(questions) * 6,
        "current_mentioned_attempts": 0 if unavailable else sum(counts),
        "prior_attempts_used": len(questions) * 6,
        "prior_mentioned_attempts": len(questions) * 2,
        "prior_sov_pct": previous,
    }
    coverage = {
        "measurement_basis": {"cell_count": len(questions) * 2},
        "attempts_used": 0 if unavailable else len(questions) * 6,
        "mentioned_attempts": 0 if unavailable else sum(counts),
        "comparison": comparison,
        "planned_count": len(questions) * 2,
        "success_count": 0 if unavailable else len(questions) * 2,
        "platforms": [],
        "observation_adequacy": {
            "lineage": "SLOTTED",
            "planned_slots": len(questions) * 6,
            "confirmed_slots": 0 if unavailable else len(questions) * 6,
            "status": "UNAVAILABLE" if unavailable else "COMPLETE",
        },
    }
    for platform in ("chatgpt", "gemini"):
        platform_mentions = sum(
            count // 2 if platform == "chatgpt" else count - count // 2
            for count in counts
        )
        platform_rate = (
            None
            if unavailable
            else round(platform_mentions / (len(questions) * 3) * 100, 1)
        )
        coverage["platforms"].append(
            {
                "platform": platform,
                "mention_rate": platform_rate,
                "attempts_used": 0 if unavailable else len(questions) * 3,
                "mentioned_attempts": 0 if unavailable else platform_mentions,
                "planned_count": len(questions),
                "success_count": 0 if unavailable else len(questions),
                "failed_count": len(questions) if unavailable else 0,
                "excluded_count": 0,
                "answer_models": ["fictional-model-2026"],
            }
        )
    attribution = {
        "has_prior_month": True,
        "new_mention_cells": [],
        "lost_mention_cells": [],
        "question_rows": [],
        "first_measured_mention_count": 0,
        "non_comparable_count": 0,
    }
    for index, question in enumerate(questions):
        attribution["question_rows"].append(
            {
                "query_text": question,
                "prior_measured": True,
                "prior_comparable": not unavailable,
                "prior_attempts_used": 6,
                "prior_mentioned_attempts": [0, 2, 4][index % 3],
                "current_attempts_used": 0 if unavailable else 6,
                "current_mentioned_attempts": 0 if unavailable else counts[index],
            }
        )
    if not unavailable:
        for index, question in enumerate(questions):
            prior_count = [0, 2, 4][index % 3]
            if prior_count == 0 and counts[index]:
                attribution["new_mention_cells"].append(
                    {"query_text": question, "platform_label": "OpenAI API"}
                )
            if prior_count and counts[index] == 0:
                attribution["lost_mention_cells"].append(
                    {"query_text": question, "platform_label": "OpenAI API"}
                )
    content = SimpleNamespace(
        id=CONTENT_ID,
        hospital_id=HOSPITAL_ID,
        title="검증용 가상 만성질환 상담 안내",
        status="PUBLISHED",
        content_type="FAQ",
        query_target_id=None,
    )
    topics = ("건강검진 전 준비 안내", "혈압 상담 전 기록할 사항", "당뇨 추적 상담 안내", "성인 예방접종 상담 안내", "검진 결과 상담 준비", "만성질환 정기 내원 안내", "가족력과 건강검진 상담", "내원 전 진료 기록 준비", "병원 위치와 진료시간 안내", "생활습관 상담 범위 안내", "검진 이후 확인할 사항")
    contents = [content, *[SimpleNamespace(
        id=UUID(int=CONTENT_ID.int + index), hospital_id=HOSPITAL_ID,
        title=f"검증용 가상 {topic}", status="PUBLISHED", content_type="FAQ", query_target_id=None,
    ) for index, topic in enumerate(topics, 1)]]
    if state == "decline":
        contents = contents[:10]
    citations = {
        "measured_cell_count": 0 if unavailable else len(questions) * 2,
        "cited_cell_count": 0 if unavailable else 1,
        "cited_content_count": 0 if unavailable else 1,
        "cited_items": [],
    }
    if not unavailable:
        citations["cited_items"] = [
            {
                "content_id": str(CONTENT_ID),
                "title": content.title,
                "cited_cell_count": 1,
                "queries": [
                    {"query_text": questions[0], "platform_label": "OpenAI API"}
                ],
            }
        ]
    records = (
        []
        if unavailable
        else [
            SimpleNamespace(
                is_mentioned=mentioned,
                raw_response=(
                    "검증용 가상 새봄가정의학과의원에서 상담 정보를 확인할 수 있습니다."
                    if mentioned
                    else "이 가상 답변에는 대상 병원 언급이 없습니다. 진료 정보를 따로 확인하세요."
                ),
                measurement_status="SUCCESS",
                ai_platform="chatgpt",
                measured_at=WHEN,
                competitor_mentions=[],
                query=SimpleNamespace(
                    query_text=questions[
                        (2 if state == "decline" else 0) if mentioned else 1
                    ]
                ),
                ai_query_target=None,
            )
            for mentioned in (True, False)
        ]
    )
    view = build_doctor_report_view(
        hospital=hospital,
        sov_pct=value,
        prev_sov_pct=previous,
        published_count=len(contents),
        plan_quota=12,
        attribution=attribution,
        records=records,
        citations=citations,
        published_contents=contents,
        sov_coverage=coverage,
        comparison_reason=comparison["reason"],
        report_kind="MONTHLY",
        platforms=["chatgpt", "gemini"],
        v0_baseline=None
        if unavailable
        else {
            "of_hundred": 17,
            "current_of_hundred": round(value),
            "sentence": "초기 측정 참고",
        },
    )
    view["sample_label"] = "검증용 가상 사례"
    return view


def diagnosis_sample(state: str, *, internal: bool = False) -> LeadReportPayload:
    measured = 0 if state == "unavailable" else 6
    mentioned = 6 if state == "strong" else 0
    queries = tuple(
        QueryDisclosure(
            slot=index,
            kind="검증용 가상 질문",
            text=text,
            measured_at=WHEN if measured else None,
            planned=6,
            measured=measured,
            mentioned=mentioned,
            failed=0 if measured else 6,
        )
        for index, text in enumerate(
            (
                "가상동 건강검진 상담을 받을 수 있는 곳은 어디인가요?",
                "가상동 만성질환을 꾸준히 상담할 수 있는 의원은 어디인가요?",
                "가상동 예방접종 전에 무엇을 확인해야 하나요?",
            ),
            1,
        )
    )
    return LeadReportPayload(
        hospital_name=(
            "검증용 가상 서울한마음가정의학과건강검진센터의원"
            if state == "unavailable"
            else "검증용 가상 새봄가정의학과의원"
        ),
        region="가상동",
        generated_at=WHEN,
        repeat_count=3,
        system_prompt="검증용 가상 측정 지시문: 질문의 지역과 진료 주제에 맞는 공개 정보를 확인합니다.",
        judge_model="fictional-judge",
        queries=queries,
        segments=tuple(
            PlatformSegment(
                platform=platform,
                vendor_label=label,
                model="fictional-model-2026",
                planned=9,
                measured=9 if measured else 0,
                mentioned=9 if mentioned else 0,
                failed=0 if measured else 9,
            )
            for platform, label in (
                ("chatgpt", "OpenAI API"),
                ("gemini", "Google Gemini API"),
            )
        ),
        contact=LeadReportContact(
            name="검증용 가상 담당자",
            role="가상 상담",
            email="" if state == "unavailable" else "fictional@example.invalid",
            phone="",
        ),
        notices=("검증용 가상 사례 · 실제 병원이나 환자의 자료가 아닙니다.",),
        internal=internal,
        sample_label="검증용 가상 사례",
    )
