"""Deterministic content brief fallback for query-linked content slots."""
from __future__ import annotations

from typing import Any

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction

BRIEF_STATUS_DRAFT = "DRAFT"
BRIEF_STATUS_APPROVED = "APPROVED"
BRIEF_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
BRIEF_STATUSES = {
    BRIEF_STATUS_DRAFT,
    BRIEF_STATUS_APPROVED,
    BRIEF_STATUS_NEEDS_REVIEW,
}


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
        getattr(content_item, "content_type", None).value
        if hasattr(getattr(content_item, "content_type", None), "value")
        else str(getattr(content_item, "content_type", "")),
    )
    treatment_narrative = _treatment_narrative(
        treatment_name=treatment_name,
        hospital=hospital,
        philosophy=philosophy,
    )

    return {
        "target_query": target_query,
        "patient_intent": _patient_intent(query_target, exposure_action),
        "query_target": _query_target_reference(query_target),
        "exposure_action": _exposure_action_reference(exposure_action),
        "philosophy_reference": _philosophy_reference(philosophy),
        "treatment_narrative": treatment_narrative,
        "must_use_messages": _list(getattr(philosophy, "must_use_messages", None)),
        "avoid_messages": _list(getattr(philosophy, "avoid_messages", None)),
        "medical_risk_rules": _medical_risk_rules(philosophy),
        "internal_link_target": {
            "type": "content_item",
            "content_id": str(content_item.id),
            "path": f"/{hospital.slug}/contents/{content_item.id}",
        },
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

    parts = [
        _first(_list(getattr(query_target, "region_terms", None))),
        getattr(query_target, "treatment", None),
        getattr(query_target, "condition_or_symptom", None),
        getattr(query_target, "target_intent", None),
    ]
    synthesized = " ".join(part for part in parts if part)
    return synthesized or getattr(query_target, "name", "")


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
        "version": philosophy.version,
        "positioning_statement": philosophy.positioning_statement,
        "doctor_voice": philosophy.doctor_voice,
        "patient_promise": philosophy.patient_promise,
        "content_principles": _list(philosophy.content_principles),
        "tone_guidelines": _list(philosophy.tone_guidelines),
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
    rules = _list(getattr(philosophy, "medical_ad_risk_rules", None))
    if rules:
        return rules
    return [
        "치료 효과를 보장하거나 100%로 표현하지 않습니다.",
        "최고, 유일, 1등 등 비교 우위 표현을 사용하지 않습니다.",
        "환자 상태에 따라 진료·치료 계획이 달라질 수 있음을 분명히 합니다.",
    ]


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first(values: list) -> str | None:
    return next((str(value) for value in values if value), None)


def _first_present(*values: Any) -> str | None:
    return next((str(value) for value in values if value), None)
