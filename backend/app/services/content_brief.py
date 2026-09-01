"""Deterministic content brief fallback for query-linked content slots."""
from __future__ import annotations

from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction
from app.services.essence_engine import effective_safety_policy
from app.services.query_target_structure import (
    clinical_keyword_from_query,
    natural_patient_question,
)

# 캘린더 생성 단계에서 슬롯에 남기는 결정 근거 키. 이때의 content_brief는 아직
# "콘텐츠 가이드"가 아니라 계획 메모다 — 승인·재사용 판정에서 브리프로 세면 안 된다.
PLANNING_REASON_KEY = "planning_reason"

BRIEF_STATUS_DRAFT = "DRAFT"
BRIEF_STATUS_APPROVED = "APPROVED"
BRIEF_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
BRIEF_STATUSES = {
    BRIEF_STATUS_DRAFT,
    BRIEF_STATUS_APPROVED,
    BRIEF_STATUS_NEEDS_REVIEW,
}


def is_usable_content_brief(brief: Any) -> bool:
    """실제 작성 지시가 들어 있는 브리프인가.

    격차 기반 캘린더가 심어 둔 `{"planning_reason": ...}`만 있는 슬롯은 아직 브리프가
    없는 것과 같다. 이걸 브리프로 세면 승인·재생성 경로가 빈 가이드를 통과시킨다.
    """
    return isinstance(brief, dict) and bool(str(brief.get("target_query") or "").strip())


def build_content_brief(
    *,
    hospital: Hospital,
    content_item: ContentItem,
    query_target: AIQueryTarget | None = None,
    exposure_action: ExposureAction | None = None,
    philosophy: HospitalContentPhilosophy | None = None,
) -> dict[str, Any]:
    """Build an operator-editable fallback brief without external model calls.

    This intentionally lives on ``ContentItem`` as JSON instead of a separate
    model because Slice 4 only needs one current brief per monthly slot.
    """
    target_query = _target_query(query_target, content_item)
    treatment_name = _first_present(
        getattr(query_target, "treatment", None),
        getattr(query_target, "condition_or_symptom", None),
        target_query,
    )
    treatment_narrative = _treatment_narrative(
        treatment_name=treatment_name,
        hospital=hospital,
        philosophy=philosophy,
    )
    safety_policy = effective_safety_policy(philosophy)
    region_terms = _list(getattr(query_target, "region_terms", None))

    return {
        "target_query": target_query,
        # ── 측정 질의를 프롬프트·검증이 그대로 쓸 수 있는 형태로 분해해 둔다.
        # 예전에는 target_query 한 줄만 프롬프트에 들어가서, 글이 그 질문에 답했는지
        # 아무도 확인하지 않았다(리뷰 §1.2 C).
        # - target_keyword: 제목·첫 H2·FAQ 질문에 반드시 등장해야 하는 임상 키워드
        # - target_question: FAQ의 faq_question / 다른 유형의 "첫 문단이 답할 질문"
        # - target_region_terms: LOCAL 프롬프트에 넣을 지역 (병원 keywords 전체가 아니라)
        "target_keyword": _target_keyword(query_target, target_query, region_terms),
        "target_question": natural_patient_question(target_query),
        "target_region_terms": [str(term) for term in region_terms if term],
        "patient_intent": _patient_intent(query_target, exposure_action),
        "query_target": _query_target_reference(query_target),
        "exposure_action": _exposure_action_reference(exposure_action),
        "philosophy_reference": _philosophy_reference(philosophy),
        "treatment_narrative": treatment_narrative,
        "must_use_messages": _list(getattr(philosophy, "must_use_messages", None)),
        "avoid_messages": safety_policy["avoid_messages"],
        "medical_risk_rules": safety_policy["medical_ad_risk_rules"],
        # 이 함수는 현재 슬롯 외의 콘텐츠를 조회하지 않는다. 현재 글 자신을 링크
        # 대상으로 주면 생성 모델이 자가 링크를 본문에 넣고, 독자와 크롤러 모두 같은
        # 페이지로 되돌아오게 된다. 실제 관련 글을 선택할 수 있을 때만 별도 단계에서
        # 채운다.
        "internal_link_target": None,
        "operator_notes": [],
        "source": {
            "mode": "deterministic_fallback",
            "hospital_id": str(hospital.id),
            "content_item_id": str(content_item.id),
        },
    }


def _target_query(query_target: AIQueryTarget | None, content_item: ContentItem) -> str:
    if query_target is None:
        title = getattr(content_item, "title", None)
        return title or f"{getattr(content_item, 'content_type', 'CONTENT')} content slot"

    active_variants = [
        variant
        for variant in getattr(query_target, "variants", []) or []
        if getattr(variant, "is_active", False)
    ]
    active_variants = sorted(
        active_variants,
        key=lambda variant: (
            getattr(variant, "created_at", None).isoformat()
            if getattr(variant, "created_at", None)
            else "",
            getattr(variant, "query_text", ""),
        ),
    )
    if active_variants:
        return active_variants[0].query_text

    # target.name은 측정에 실제로 쓰인 질의 원문이다(V0 시드는 QueryMatrix.query_text를
    # 그대로 넣는다). 아래 조합 문자열보다 **항상 우선**한다 — 조합은 "강남역 허리디스크
    # 증상 탐색"처럼 환자가 쓰지 않는 말이 되고, 그게 그대로 프롬프트의 대상 질문이 된다.
    name = str(getattr(query_target, "name", "") or "").strip()
    if name:
        return name

    parts = [
        _first(_list(getattr(query_target, "region_terms", None))),
        getattr(query_target, "treatment", None),
        getattr(query_target, "condition_or_symptom", None),
        getattr(query_target, "target_intent", None),
    ]
    synthesized = " ".join(part for part in parts if part)
    return synthesized or getattr(query_target, "name", "")


def _target_keyword(
    query_target: AIQueryTarget | None,
    target_query: str,
    region_terms: list,
) -> str | None:
    """이 글이 반드시 다뤄야 하는 임상 키워드.

    구조 필드(질환·시술)를 먼저 쓰고, 비어 있으면 질의에서 가장 긴 비지역 토큰을
    쓴다. 지역명(질의의 첫 토큰)이 주제어로 잡히지 않게 하는 것이 요점이다.
    """
    for field_name in ("condition_or_symptom", "treatment", "specialty"):
        value = getattr(query_target, field_name, None) if query_target else None
        if value and str(value).strip():
            return str(value).strip()
    return clinical_keyword_from_query(target_query, region_terms)


def _patient_intent(
    query_target: AIQueryTarget | None,
    exposure_action: ExposureAction | None,
) -> str:
    if query_target is not None and getattr(query_target, "target_intent", None):
        return str(query_target.target_intent)
    if exposure_action is not None:
        return str(getattr(exposure_action, "description", "") or getattr(exposure_action, "title", ""))
    return "환자가 AI 검색에서 신뢰할 수 있는 진료 선택 기준을 확인하려는 의도"


def _query_target_reference(query_target: AIQueryTarget | None) -> dict[str, Any] | None:
    if query_target is None:
        return None
    return {
        "id": str(query_target.id),
        "name": query_target.name,
        "target_intent": query_target.target_intent,
        "priority": query_target.priority,
        "target_month": query_target.target_month,
        "treatment": query_target.treatment,
        "condition_or_symptom": query_target.condition_or_symptom,
        "region_terms": _list(getattr(query_target, "region_terms", None)),
        "specialty": getattr(query_target, "specialty", None),
        "decision_criteria": _list(query_target.decision_criteria),
    }


def _exposure_action_reference(exposure_action: ExposureAction | None) -> dict[str, Any] | None:
    if exposure_action is None:
        return None
    return {
        "id": str(exposure_action.id),
        "query_target_id": str(exposure_action.query_target_id)
        if exposure_action.query_target_id
        else None,
        "action_type": exposure_action.action_type,
        "title": exposure_action.title,
        "description": exposure_action.description,
        "due_month": exposure_action.due_month,
        "status": exposure_action.status,
    }


def _philosophy_reference(philosophy: HospitalContentPhilosophy | None) -> dict[str, Any] | None:
    if philosophy is None:
        return None
    return {
        "id": str(philosophy.id),
        "version": getattr(philosophy, "version", None),
        "positioning_statement": getattr(philosophy, "positioning_statement", None),
        "doctor_voice": getattr(philosophy, "doctor_voice", None),
        "patient_promise": getattr(philosophy, "patient_promise", None),
        "content_principles": _list(getattr(philosophy, "content_principles", None)),
        "tone_guidelines": _list(getattr(philosophy, "tone_guidelines", None)),
    }


def _treatment_narrative(
    *,
    treatment_name: str | None,
    hospital: Hospital,
    philosophy: HospitalContentPhilosophy | None,
) -> dict[str, Any]:
    normalized = (treatment_name or "").strip().lower()
    for narrative in _list(getattr(philosophy, "treatment_narratives", None)):
        if not isinstance(narrative, dict):
            continue
        candidate = str(narrative.get("treatment") or narrative.get("name") or "").strip()
        if normalized and normalized in candidate.lower():
            return {
                "source": "approved_philosophy",
                "treatment": candidate or treatment_name,
                "angle": narrative.get("angle") or narrative.get("narrative") or "",
                "details": narrative,
            }

    for treatment in _list(getattr(hospital, "treatments", None)):
        if not isinstance(treatment, dict):
            continue
        candidate = str(treatment.get("name") or "").strip()
        if not normalized or normalized in candidate.lower():
            return {
                "source": "hospital_profile",
                "treatment": candidate,
                "angle": treatment.get("description") or "",
                "details": treatment,
            }

    return {
        "source": "fallback",
        "treatment": treatment_name,
        "angle": "증상, 진단, 치료 선택지, 회복 과정과 주의사항을 환자 언어로 설명합니다.",
        "details": {},
    }


def _medical_risk_rules(philosophy: HospitalContentPhilosophy | None) -> list[str]:
    return effective_safety_policy(philosophy)["medical_ad_risk_rules"]


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first(values: list) -> str | None:
    return next((str(value) for value in values if value), None)


def _first_present(*values: Any) -> str | None:
    return next((str(value) for value in values if value), None)
