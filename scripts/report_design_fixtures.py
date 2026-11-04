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


def monthly_sample(
    state: str, *, dense: bool = False, reason: str | None = None, kind: str = "MONTHLY"
):
    """`reason` makes a measured month non-comparable (first month, method change)."""
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
    comparable = not unavailable and reason is None
    comparison = {
        "status": "COMPARABLE" if comparable else "NON_COMPARABLE",
        "reason": "NO_MATCHED_CELLS" if unavailable else reason or "MATCHED_COHORT",
        "matched_cell_count": 0 if unavailable else len(questions) * 2,
        "current_sov_pct": value,
        "current_attempts_used": 0 if unavailable else len(questions) * 6,
        "current_mentioned_attempts": 0 if unavailable else sum(counts),
        "prior_attempts_used": len(questions) * 6,
        "prior_mentioned_attempts": len(questions) * 2,
        "prior_sov_pct": previous if comparable or unavailable else None,
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
        "has_prior_month": reason != "NO_PRIOR_MANIFEST",
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
                "prior_comparable": comparable,
                "prior_attempts_used": 6,
                "prior_mentioned_attempts": [0, 2, 4][index % 3],
                "current_attempts_used": 0 if unavailable else 6,
                "current_mentioned_attempts": 0 if unavailable else counts[index],
            }
        )
    if comparable:
        for index, question in enumerate(questions):
            prior_count = [0, 2, 4][index % 3]
            if prior_count == 0 and counts[index]:
                attribution["new_mention_cells"].append(
                    {"query_text": question, "platform_label": "ChatGPT"}
                )
            if prior_count and counts[index] == 0:
                attribution["lost_mention_cells"].append(
                    {"query_text": question, "platform_label": "ChatGPT"}
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
                    {"query_text": questions[0], "platform_label": "ChatGPT"}
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
        prev_sov_pct=previous if comparable else None,
        published_count=len(contents),
        plan_quota=12,
        attribution=attribution,
        records=records,
        citations=citations,
        published_contents=contents,
        sov_coverage=coverage,
        comparison_reason=comparison["reason"],
        report_kind=kind,
        platforms=["chatgpt", "gemini"],
        cumulative_published_count=None if unavailable else len(contents) + 28,
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


# ── 원장용 v4 (2026-11-04 설계) ────────────────────────────────────────────────

_V4_TREATMENTS = ("위내시경", "대장내시경", "심장초음파", "고혈압", "당뇨", "건강검진")
_V4_INTENTS = ("추천 탐색", "증상 탐색", "비용 확인", "진료 가능 확인")
_V4_COMPETITORS = ("검증용 가상 가온내과의원", "검증용 가상 바른속내과의원", "검증용 가상 한빛검진센터")
_V4_FORMS = ("잘하는 병원", "받을 수 있는 병원", "비용은 어느 정도인가요", "진료 가능한 병원")


def monthly_v4_sample(
    state: str,
    *,
    dense: bool = False,
    reason: str | None = None,
    kind: str = "MONTHLY",
    long_name: bool = False,
):
    """원장용 v4 뷰 — 가상 병원·가상 키워드. state: growth · first · method-changed · setup ·
    dense · unavailable. `reason`·`kind`는 state가 정하지 않을 때만 쓴다."""
    from app.services.doctor_report_v4 import build_doctor_report_v4

    if state == "first":
        reason = reason or "NO_PRIOR_MANIFEST"
    elif state == "method-changed":
        reason = reason or "MEASUREMENT_POLICY_CHANGED"
    elif state == "setup":
        kind = "SETUP"
    elif state == "dense":
        dense = True
    unavailable = state == "unavailable"
    setup = kind == "SETUP"
    comparable = state in ("growth", "dense") and reason is None
    name = "검증용 가상 새봄내과건강검진의원"
    if long_name:
        name = ("검증용 가상 서울한마음가정의학과건강검진센터내과의원 " * 3)[:60]
    hospital = SimpleNamespace(
        id=HOSPITAL_ID, name=name, slug="fictional-clinic",
        aeo_domain="fictional-clinic.example.invalid", region=["가상시 가상구"],
        hero_specialties=["위내시경"], treatments=[{"name": "심장초음파"}],
    )
    count = 30 if dense else 8
    keywords = [
        f"가상구 {_V4_TREATMENTS[i % len(_V4_TREATMENTS)]} {_V4_FORMS[i % len(_V4_FORMS)]} {i + 1:02d}"
        for i in range(count)
    ]
    targets = [
        SimpleNamespace(
            id=UUID(int=0x1000 + i), name=keywords[i], variants=[],
            condition_or_symptom=_V4_TREATMENTS[i % len(_V4_TREATMENTS)], treatment=None,
            specialty=None, target_intent=_V4_INTENTS[i % len(_V4_INTENTS)], region_terms=["가상구"],
        )
        for i in range(count)
    ]
    # 플랫폼별 (상태, 노출, 시도): 키워드 순번으로 노출·미노출·실패를 섞는다.
    def cell(i: int, platform: str) -> tuple[str, int, int]:
        if unavailable:
            return ("FAILED", 0, 0)
        if i % 7 == 6 and platform == "gemini":
            return ("FAILED", 0, 0)
        mentioned = [3, 0, 1, 0, 2][i % 5] if platform == "chatgpt" else [2, 0, 0, 0, 1][i % 5]
        return ("SUCCESS", mentioned, 3)

    queries, cells, question_rows = [], [], []
    total_attempts = total_mentioned = 0
    platform_totals = {"chatgpt": [0, 0], "gemini": [0, 0]}
    for i, text in enumerate(keywords):
        attempts = mentioned = 0
        for platform in ("chatgpt", "gemini"):
            cstate, m, a = cell(i, platform)
            cells.append({"query_text": text, "platform": platform, "state": cstate, "mentioned_attempts": m, "attempts_used": a})
            attempts += a
            mentioned += m
            platform_totals[platform][0] += m
            platform_totals[platform][1] += a
        queries.append({"query_text": text, "query_intent": "LOCAL", "attempts_used": attempts, "mentioned_attempts": mentioned})
        total_attempts += attempts
        total_mentioned += mentioned
        prior_mentioned = [1, 2, 0, 0, 1, 3, 2][i % 7]
        question_rows.append({
            "query_text": text, "prior_measured": comparable, "prior_comparable": comparable,
            "prior_attempts_used": 6 if comparable else 0, "prior_mentioned_attempts": prior_mentioned if comparable else 0,
            "current_attempts_used": attempts, "current_mentioned_attempts": mentioned,
        })
    sov = None if unavailable else round(total_mentioned / total_attempts * 100, 2)
    prior_rate = 31.0
    comparison = {
        "status": "COMPARABLE" if comparable else "NON_COMPARABLE",
        "reason": "NO_MATCHED_CELLS" if unavailable else (reason or "MATCHED_COHORT"),
        "matched_cell_count": count * 2 if comparable else 0,
        "current_sov_pct": sov,
        "prior_sov_pct": prior_rate if comparable else None,
    }
    coverage = {} if setup else {
        "sov_pct": sov, "attempts_used": total_attempts, "mentioned_attempts": total_mentioned,
        "comparison": comparison, "planned_count": count * 2,
        "success_count": 0 if unavailable else sum(1 for c in cells if c["state"] == "SUCCESS"),
        "failed_count": sum(1 for c in cells if c["state"] == "FAILED"),
        "platforms": [
            {"platform": p, "mention_rate": (None if not a else round(m / a * 100, 2)), "attempts_used": a, "mentioned_attempts": m}
            for p, (m, a) in platform_totals.items()
        ],
        "queries": queries, "cells": cells,
    }
    attribution = {
        "has_prior_month": comparable, "question_rows": question_rows,
        "new_mention_cells": [{"query_text": keywords[2], "platform_label": "ChatGPT"}] if comparable else [],
        "lost_mention_cells": [{"query_text": keywords[3], "platform_label": "Gemini"}] if comparable else [],
    }
    content_count = 20 if dense else 6
    contents = [
        SimpleNamespace(
            id=UUID(int=CONTENT_ID.int + i), hospital_id=HOSPITAL_ID,
            title=f"검증용 가상 {_V4_TREATMENTS[i % len(_V4_TREATMENTS)]} 안내 {i + 1:02d} — 준비와 확인 사항",
            status="PUBLISHED", content_type="FAQ", query_target_id=targets[i % count].id,
            first_published_at=datetime(2026, 10, 1 + i, 2, tzinfo=UTC),
            content_brief={"target_keyword": f"가상구 {_V4_TREATMENTS[i % len(_V4_TREATMENTS)]}"},
        )
        for i in range(content_count)
    ]
    citations = None if setup else {
        "measured_cell_count": 0 if unavailable else count * 2,
        "cited_cell_count": 0 if unavailable else 2,
        "cited_content_count": 0 if unavailable else 1,
        "cited_items": [] if unavailable else [{
            "content_id": str(contents[0].id), "title": contents[0].title, "cited_cell_count": 2,
            "queries": [{"query_text": keywords[0], "platform_label": "ChatGPT"}],
        }],
    }
    records = []
    if not setup and not unavailable:
        for i, text in enumerate(keywords):
            for platform in ("chatgpt", "gemini"):
                cstate, m, a = cell(i, platform)
                if cstate != "SUCCESS":
                    continue
                mentioned = m > 0
                competitor_mentioned = (i + (platform == "gemini")) % 3 == 0
                records.append(SimpleNamespace(
                    is_mentioned=mentioned, measurement_status="SUCCESS", ai_platform=platform, measured_at=WHEN,
                    matched_text=name if mentioned else None,
                    raw_response=(
                        f"가상구에서 {_V4_TREATMENTS[i % len(_V4_TREATMENTS)]}을 받을 수 있는 곳으로 {name}이 있습니다. "
                        "예약 전에 검사 준비 사항과 진료 시간을 병원에 확인하는 것이 좋습니다. "
                        "검사 전날 식사 제한이 있는지도 미리 물어보시기 바랍니다."
                        if mentioned
                        else f"가상구에서 {_V4_TREATMENTS[i % len(_V4_TREATMENTS)]}을 받으려면 {_V4_COMPETITORS[i % 3]} 등을 살펴보실 수 있습니다. "
                        "진료 시간과 검사 준비 사항은 병원마다 다르니 방문 전에 확인하시기 바랍니다."
                    ),
                    competitor_mentions=[
                        {"name": _V4_COMPETITORS[(i + j) % 3], "is_mentioned": competitor_mentioned and j == 0}
                        for j in range(3)
                    ],
                    query=SimpleNamespace(query_text=text),
                ))
    trend = [("2026-08", 18.0), ("2026-09", 24.0)] if comparable else []
    view = build_doctor_report_v4(
        hospital=hospital, period_year=2026, period_month=10, public_url=PUBLIC_URL,
        report_kind=kind, sov_coverage=coverage, attribution=None if setup else attribution,
        citations=citations, published_contents=contents, query_targets=targets, records=records,
        plan_quota=16 if not setup else None, published_count=content_count, fulfilled_count=content_count,
        cumulative_published_count=content_count + 28, comparison_reason=None if setup else comparison["reason"],
        reference_prev_sov_pct=prior_rate if state == "method-changed" else None,
        v0_baseline={"of_hundred": 17, "current_of_hundred": round(sov or 0), "sentence": "초기 측정 참고"} if state == "method-changed" else None,
        trend_points=trend, platforms=["chatgpt", "gemini"], sample_label="검증용 가상 사례",
    )
    return view
