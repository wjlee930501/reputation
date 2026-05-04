"""Source-backed Content Essence engine.

MVP behavior is intentionally deterministic. It extracts short evidence notes
from operator-provided source text and synthesizes only fields that can point
back to saved evidence note IDs.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select

from app.models.content import ContentItem
from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
)
from app.models.hospital import Hospital
from app.utils.medical_filter import check_forbidden

ESSENCE_STATUS_ALIGNED = "ALIGNED"
ESSENCE_STATUS_NEEDS_REVIEW = "NEEDS_ESSENCE_REVIEW"
ESSENCE_STATUS_MISSING_APPROVED = "MISSING_APPROVED_PHILOSOPHY"


@dataclass(frozen=True)
class EvidenceNotePayload:
    note_type: EvidenceNoteType
    claim: str
    source_excerpt: str
    excerpt_start: int | None = None
    excerpt_end: int | None = None
    confidence: float | None = None
    note_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EssenceScreeningResult:
    status: str
    summary: dict[str, Any]


def compute_source_content_hash(
    title: str,
    url: str | None,
    raw_text: str | None,
    operator_note: str | None = None,
) -> str:
    source = "\n".join([title or "", url or "", raw_text or "", operator_note or ""])
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compute_sources_snapshot_hash(sources: Iterable[HospitalSourceAsset]) -> str:
    parts = []
    for source in sorted(sources, key=lambda item: str(item.id)):
        parts.append(
            "|".join([
                str(source.id),
                source.content_hash or "",
                source.status.value if hasattr(source.status, "value") else str(source.status),
                source.processed_at.isoformat() if source.processed_at else "",
            ])
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def get_source_text(asset: HospitalSourceAsset) -> str:
    return "\n".join(part for part in [asset.raw_text or "", asset.operator_note or ""] if part)


def find_excerpt_bounds(asset: HospitalSourceAsset, excerpt: str) -> tuple[int | None, int | None]:
    raw_text = asset.raw_text or ""
    operator_note = asset.operator_note or ""
    raw_idx = raw_text.find(excerpt)
    if raw_idx >= 0:
        return raw_idx, raw_idx + len(excerpt)
    note_idx = operator_note.find(excerpt)
    if note_idx >= 0:
        offset = len(raw_text) + (1 if raw_text else 0)
        return offset + note_idx, offset + note_idx + len(excerpt)
    return None, None


def validate_source_excerpt(asset: HospitalSourceAsset, excerpt: str) -> bool:
    start, end = find_excerpt_bounds(asset, excerpt)
    return start is not None and end is not None


def process_source_asset(asset: HospitalSourceAsset) -> list[EvidenceNotePayload]:
    """Extract source-backed evidence notes from pasted raw text/operator notes."""
    if not asset.raw_text or not asset.raw_text.strip():
        raise ValueError("원문 텍스트가 없는 자료는 처리할 수 없습니다.")

    snippets = _candidate_excerpts(asset)
    payloads: list[EvidenceNotePayload] = []
    seen: set[tuple[str, str]] = set()

    for excerpt in snippets:
        note_type = _classify_excerpt(excerpt)
        violations = check_forbidden(excerpt)
        metadata: dict[str, Any] = {}
        if violations:
            note_type = EvidenceNoteType.RISK_SIGNAL
            metadata["violations"] = violations
        if note_type == EvidenceNoteType.TREATMENT_SIGNAL:
            metadata["treatment"] = _guess_treatment_label(excerpt)

        start, end = find_excerpt_bounds(asset, excerpt)
        if start is None or end is None:
            continue

        key = (note_type.value, excerpt)
        if key in seen:
            continue
        seen.add(key)
        payloads.append(
            EvidenceNotePayload(
                note_type=note_type,
                claim=_claim_from_excerpt(note_type, excerpt),
                source_excerpt=excerpt,
                excerpt_start=start,
                excerpt_end=end,
                confidence=0.72 if note_type == EvidenceNoteType.KEY_MESSAGE else 0.78,
                note_metadata=metadata,
            )
        )

        if len(payloads) >= 20:
            break

    if payloads and not any(p.note_type == EvidenceNoteType.KEY_MESSAGE for p in payloads):
        first = payloads[0]
        payloads.insert(
            0,
            EvidenceNotePayload(
                note_type=EvidenceNoteType.KEY_MESSAGE,
                claim=_claim_from_excerpt(EvidenceNoteType.KEY_MESSAGE, first.source_excerpt),
                source_excerpt=first.source_excerpt,
                excerpt_start=first.excerpt_start,
                excerpt_end=first.excerpt_end,
                confidence=0.68,
                note_metadata={"derived_from_first_evidence": True},
            ),
        )

    return payloads


def synthesize_philosophy(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
) -> dict[str, Any]:
    """Create a draft philosophy from saved evidence notes only."""
    grouped = _group_notes(notes)

    key_notes = _pick_notes(
        grouped,
        [
            EvidenceNoteType.DOCTOR_PHILOSOPHY,
            EvidenceNoteType.KEY_MESSAGE,
            EvidenceNoteType.PATIENT_PROMISE,
            EvidenceNoteType.PROOF_POINT,
        ],
        limit=4,
    )
    tone_notes = _pick_notes(grouped, [EvidenceNoteType.TONE_SIGNAL], limit=3)
    promise_notes = _pick_notes(
        grouped,
        [EvidenceNoteType.PATIENT_PROMISE, EvidenceNoteType.KEY_MESSAGE],
        limit=2,
    )
    treatment_notes = _pick_notes(grouped, [EvidenceNoteType.TREATMENT_SIGNAL], limit=5)
    risk_notes = _pick_notes(grouped, [EvidenceNoteType.RISK_SIGNAL], limit=5)
    local_notes = _pick_notes(grouped, [EvidenceNoteType.LOCAL_CONTEXT], limit=3)
    conflict_notes = _pick_notes(grouped, [EvidenceNoteType.CONFLICT], limit=5)

    evidence_map: dict[str, list[str]] = {}
    unsupported_gaps: list[dict[str, str]] = []

    positioning_statement = None
    if key_notes:
        positioning_statement = f"자료에서 확인된 핵심 메시지: {_short(key_notes[0].source_excerpt, 140)}"
        evidence_map["positioning_statement"] = [_note_id(key_notes[0])]
    else:
        unsupported_gaps.append({"field": "positioning_statement", "reason": "핵심 메시지 근거 note가 없습니다."})

    doctor_voice = None
    if tone_notes:
        doctor_voice = f"자료 표현 기준 문체: {_short(tone_notes[0].source_excerpt, 140)}"
        evidence_map["doctor_voice"] = [_note_id(tone_notes[0])]
    else:
        unsupported_gaps.append({"field": "doctor_voice", "reason": "문체/톤 근거 note가 없습니다."})

    patient_promise = None
    if promise_notes:
        patient_promise = f"환자에게 말할 수 있는 약속은 이 근거 범위로 제한: {_short(promise_notes[0].source_excerpt, 140)}"
        evidence_map["patient_promise"] = [_note_id(promise_notes[0])]
    else:
        unsupported_gaps.append({"field": "patient_promise", "reason": "환자 약속 근거 note가 없습니다."})

    content_principles = [
        f"근거 문장을 벗어나지 않고 설명합니다: {_short(note.source_excerpt, 120)}"
        for note in key_notes[:3]
    ]
    if content_principles:
        evidence_map["content_principles"] = [_note_id(note) for note in key_notes[:3]]
    else:
        unsupported_gaps.append({"field": "content_principles", "reason": "콘텐츠 원칙으로 전환할 근거가 없습니다."})

    tone_guidelines = [
        f"원문 톤을 유지합니다: {_short(note.source_excerpt, 120)}"
        for note in tone_notes
    ]
    if tone_guidelines:
        evidence_map["tone_guidelines"] = [_note_id(note) for note in tone_notes]

    must_use_messages = [_short(note.source_excerpt, 160) for note in key_notes]
    if must_use_messages:
        evidence_map["must_use_messages"] = [_note_id(note) for note in key_notes]

    avoid_messages = [
        f"검수 필요 표현 또는 약속: {_short(note.source_excerpt, 120)}"
        for note in risk_notes
    ]
    if avoid_messages:
        evidence_map["avoid_messages"] = [_note_id(note) for note in risk_notes]

    treatment_narratives = []
    for note in treatment_notes:
        treatment_narratives.append({
            "treatment": (note.note_metadata or {}).get("treatment") or "자료 기반 진료 항목",
            "angle": _short(note.source_excerpt, 140),
            "explanation_style": "근거 발췌에 포함된 표현만 사용합니다.",
            "cautions": ["효과, 완치, 성공률을 보장하지 않습니다."],
            "evidence_note_ids": [_note_id(note)],
        })
    if treatment_narratives:
        evidence_map["treatment_narratives"] = [_note_id(note) for note in treatment_notes]
    else:
        unsupported_gaps.append({"field": "treatment_narratives", "reason": "진료/시술 설명 근거 note가 없습니다."})

    local_context = {"region_terms": [], "local_patient_context": [], "avoid_region_stuffing": True}
    if local_notes:
        local_context["local_patient_context"] = [_short(note.source_excerpt, 120) for note in local_notes]
        local_context["evidence_note_ids"] = [_note_id(note) for note in local_notes]
        evidence_map["local_context"] = [_note_id(note) for note in local_notes]

    medical_ad_risk_rules = []
    for note in risk_notes:
        violations = (note.note_metadata or {}).get("violations") or []
        if violations:
            medical_ad_risk_rules.append(
                f"{', '.join(violations)} 표현은 근거와 별도 심의 없이 사용하지 않습니다."
            )
        else:
            medical_ad_risk_rules.append(f"리스크 표현 검수: {_short(note.source_excerpt, 120)}")
    if medical_ad_risk_rules:
        evidence_map["medical_ad_risk_rules"] = [_note_id(note) for note in risk_notes]

    if not risk_notes:
        unsupported_gaps.append({
            "field": "medical_ad_risk_rules",
            "reason": "자료에서 병원별 리스크 표현이 별도로 발견되지 않았습니다.",
        })

    conflict_payload = [
        {"text": _short(note.source_excerpt, 160), "evidence_note_ids": [_note_id(note)]}
        for note in conflict_notes
    ]

    if operator_note:
        unsupported_gaps.append({
            "field": "operator_note",
            "reason": "초안 생성 메모는 참고만 했고, 저장 필드는 evidence note에 매핑된 값으로 제한했습니다.",
        })

    return {
        "positioning_statement": positioning_statement,
        "doctor_voice": doctor_voice,
        "patient_promise": patient_promise,
        "content_principles": content_principles,
        "tone_guidelines": tone_guidelines,
        "must_use_messages": must_use_messages,
        "avoid_messages": avoid_messages,
        "treatment_narratives": treatment_narratives,
        "local_context": local_context,
        "medical_ad_risk_rules": medical_ad_risk_rules,
        "evidence_map": evidence_map,
        "source_asset_ids": [str(source.id) for source in sources],
        "unsupported_gaps": unsupported_gaps,
        "conflict_notes": conflict_payload,
        "synthesis_notes": (
            "Deterministic MVP synthesis. Raw source text/operator notes were the only evidence; "
            "unsupported fields were left empty or listed in unsupported_gaps."
        ),
        "source_snapshot_hash": compute_sources_snapshot_hash(sources),
    }


def validate_philosophy_grounding(
    payload: Any,
    notes: list[HospitalSourceEvidenceNote],
    *,
    require_text_support: bool = False,
) -> list[str]:
    """Ensure non-empty philosophy fields point to existing evidence notes."""
    notes_by_id = {_note_id(note): note for note in notes}
    valid_note_ids = set(notes_by_id)
    evidence_map = _payload_get(payload, "evidence_map") or {}
    errors: list[str] = []

    field_names = [
        "positioning_statement",
        "doctor_voice",
        "patient_promise",
        "content_principles",
        "tone_guidelines",
        "must_use_messages",
        "avoid_messages",
        "treatment_narratives",
        "local_context",
        "medical_ad_risk_rules",
    ]
    for field_name in field_names:
        value = _payload_get(payload, field_name)
        if not _field_has_source_backed_value(field_name, value):
            continue

        mapped_ids = _flatten_ids(evidence_map.get(field_name))
        if not mapped_ids:
            errors.append(f"{field_name} 필드에 evidence_map이 없습니다.")
            continue
        unknown = [note_id for note_id in mapped_ids if note_id not in valid_note_ids]
        if unknown:
            errors.append(f"{field_name} 필드가 존재하지 않는 evidence note를 참조합니다: {', '.join(unknown)}")
            continue
        if require_text_support and not _value_contains_mapped_evidence(value, mapped_ids, notes_by_id):
            errors.append(f"{field_name} 필드가 매핑된 evidence note 발췌를 포함하지 않습니다.")

    return errors


def screen_content_against_philosophy(
    content_item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
) -> EssenceScreeningResult:
    if not philosophy or _status_value(getattr(philosophy, "status", None)) != PhilosophyStatus.APPROVED.value:
        return EssenceScreeningResult(
            status=ESSENCE_STATUS_MISSING_APPROVED,
            summary={
                "blocking": True,
                "findings": ["승인된 콘텐츠 철학이 없습니다."],
                "checked_at": _now_iso(),
            },
        )

    text = " ".join(
        part for part in [
            getattr(content_item, "title", None),
            getattr(content_item, "body", None),
            getattr(content_item, "meta_description", None),
        ]
        if part
    )
    findings: list[str] = []
    forbidden = check_forbidden(text)
    if forbidden:
        findings.append(f"의료광고 금지 표현: {', '.join(forbidden)}")

    for message in _string_items(philosophy.avoid_messages or []):
        direct = _strip_prefix(message)
        if direct and direct in text:
            findings.append(f"병원별 avoid message와 충돌: {_short(direct, 80)}")

    status = ESSENCE_STATUS_ALIGNED if not findings else ESSENCE_STATUS_NEEDS_REVIEW
    return EssenceScreeningResult(
        status=status,
        summary={
            "blocking": status != ESSENCE_STATUS_ALIGNED,
            "philosophy_id": str(philosophy.id),
            "philosophy_version": philosophy.version,
            "findings": findings,
            "checked_at": _now_iso(),
        },
    )


def get_approved_philosophy_sync(db, hospital_id: Any) -> HospitalContentPhilosophy | None:
    result = db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    return result.scalar_one_or_none()


def build_monthly_essence_summary(db, hospital: Hospital, period_start: datetime, period_end: datetime) -> dict[str, Any]:
    source_result = db.execute(
        select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital.id)
    )
    sources = source_result.scalars().all()
    processed_sources = [source for source in sources if source.status == SourceStatus.PROCESSED]

    approved = get_approved_philosophy_sync(db, hospital.id)
    source_snapshot_hash = compute_sources_snapshot_hash(processed_sources)
    source_stale = bool(approved and approved.source_snapshot_hash != source_snapshot_hash)

    content_result = db.execute(
        select(ContentItem).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.scheduled_date >= period_start.date(),
            ContentItem.scheduled_date <= period_end.date(),
        )
    )
    items = content_result.scalars().all()
    generated_items = [item for item in items if item.body]

    aligned_count = sum(1 for item in generated_items if item.essence_status == ESSENCE_STATUS_ALIGNED)
    needs_review_count = sum(1 for item in generated_items if item.essence_status == ESSENCE_STATUS_NEEDS_REVIEW)
    missing_count = sum(1 for item in generated_items if item.essence_status == ESSENCE_STATUS_MISSING_APPROVED)

    medical_risk_findings = []
    for item in generated_items:
        violations = check_forbidden(" ".join(part for part in [item.title, item.body, item.meta_description] if part))
        if violations:
            medical_risk_findings.append({
                "content_id": str(item.id),
                "title": item.title,
                "violations": violations,
            })

    recommended_actions = []
    if not approved:
        recommended_actions.append("승인된 콘텐츠 철학을 생성/승인하세요.")
    if not processed_sources:
        recommended_actions.append("온보딩 자료를 1개 이상 원문 텍스트 기반으로 처리하세요.")
    if source_stale:
        recommended_actions.append("처리된 자료가 승인된 운영 기준과 달라졌습니다. 새 초안을 검토하세요.")
    if needs_review_count:
        recommended_actions.append("Essence 재검수가 필요한 콘텐츠를 수정하세요.")
    if missing_count:
        recommended_actions.append("승인 철학 없이 생성된 콘텐츠를 재생성하거나 검수하세요.")
    if medical_risk_findings:
        recommended_actions.append("의료광고 리스크 표현이 있는 콘텐츠를 발행 전 수정하세요.")

    return {
        "approved_philosophy_exists": approved is not None,
        "philosophy_version": approved.version if approved else None,
        "approved_at": approved.approved_at.isoformat() if approved and approved.approved_at else None,
        "source_count": len(sources),
        "processed_source_count": len(processed_sources),
        "source_asset_ids": [str(source.id) for source in processed_sources],
        "source_stale": source_stale,
        "generated_content_count": len(generated_items),
        "aligned_content_count": aligned_count,
        "needs_review_content_count": needs_review_count,
        "missing_philosophy_content_count": missing_count,
        "off_brand_findings": [
            (item.essence_check_summary or {})
            for item in generated_items
            if item.essence_status == ESSENCE_STATUS_NEEDS_REVIEW
        ],
        "medical_risk_findings": medical_risk_findings,
        "recommended_actions": recommended_actions,
    }


def _candidate_excerpts(asset: HospitalSourceAsset) -> list[str]:
    excerpts: list[str] = []
    for text in [asset.raw_text or "", asset.operator_note or ""]:
        for match in re.finditer(r"[^.!?\n。！？]+[.!?。！？]?", text):
            excerpt = match.group(0).strip()
            if not excerpt:
                continue
            if len(excerpt) > 220:
                excerpt = excerpt[:220].strip()
            if len(excerpt) < 12 and not check_forbidden(excerpt):
                continue
            if excerpt in text and excerpt not in excerpts:
                excerpts.append(excerpt)
    return excerpts[:30]


def _classify_excerpt(excerpt: str) -> EvidenceNoteType:
    if check_forbidden(excerpt) or _has_any(excerpt, ["보장", "무조건", "부작용", "완치", "최고", "유일"]):
        return EvidenceNoteType.RISK_SIGNAL
    if _has_any(excerpt, ["철학", "원장", "진료 원칙", "중요하게 생각"]):
        return EvidenceNoteType.DOCTOR_PHILOSOPHY
    if _has_any(excerpt, ["차분", "친절", "편안", "꼼꼼", "자세히", "충분히", "쉽게"]):
        return EvidenceNoteType.TONE_SIGNAL
    if _has_any(excerpt, ["치료", "수술", "시술", "검사", "내시경", "진료", "상담"]):
        return EvidenceNoteType.TREATMENT_SIGNAL
    if _has_any(excerpt, ["약속", "안심", "회복", "일상", "선택지"]):
        return EvidenceNoteType.PATIENT_PROMISE
    if re.search(r"(구|시|동|읍|면|역|지역)", excerpt):
        return EvidenceNoteType.LOCAL_CONTEXT
    return EvidenceNoteType.KEY_MESSAGE


def _claim_from_excerpt(note_type: EvidenceNoteType, excerpt: str) -> str:
    prefix = {
        EvidenceNoteType.RISK_SIGNAL: "의료광고 또는 과장 리스크 표현 확인",
        EvidenceNoteType.TONE_SIGNAL: "문체/톤 근거 확인",
        EvidenceNoteType.TREATMENT_SIGNAL: "진료/시술 설명 근거 확인",
        EvidenceNoteType.PATIENT_PROMISE: "환자 약속 관련 근거 확인",
        EvidenceNoteType.DOCTOR_PHILOSOPHY: "진료 철학 관련 근거 확인",
        EvidenceNoteType.LOCAL_CONTEXT: "지역 맥락 근거 확인",
    }.get(note_type, "핵심 메시지 근거 확인")
    return f"{prefix}: {_short(excerpt, 120)}"


def _guess_treatment_label(excerpt: str) -> str | None:
    match = re.search(r"([가-힣A-Za-z0-9]{2,20})(?:\s*)(치료|수술|시술|검사|내시경|진료|상담)", excerpt)
    if not match:
        return None
    return "".join(match.groups())


def _group_notes(notes: list[HospitalSourceEvidenceNote]) -> dict[EvidenceNoteType, list[HospitalSourceEvidenceNote]]:
    grouped: dict[EvidenceNoteType, list[HospitalSourceEvidenceNote]] = {}
    for note in notes:
        grouped.setdefault(note.note_type, []).append(note)
    return grouped


def _pick_notes(
    grouped: dict[EvidenceNoteType, list[HospitalSourceEvidenceNote]],
    types: list[EvidenceNoteType],
    limit: int,
) -> list[HospitalSourceEvidenceNote]:
    picked: list[HospitalSourceEvidenceNote] = []
    for note_type in types:
        picked.extend(grouped.get(note_type, []))
    return picked[:limit]


def _payload_get(payload: Any, field_name: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(field_name)
    return getattr(payload, field_name, None)


def _field_has_source_backed_value(field_name: str, value: Any) -> bool:
    if field_name == "local_context":
        return bool(value and (value.get("region_terms") or value.get("local_patient_context")))
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)


def _flatten_ids(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_ids(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_flatten_ids(item))
        return result
    return [str(value)]


def _value_contains_mapped_evidence(
    value: Any,
    mapped_ids: list[str],
    notes_by_id: dict[str, HospitalSourceEvidenceNote],
) -> bool:
    text = _flatten_text(value)
    if not text.strip():
        return True
    normalized_text = _normalize_for_grounding(text)
    for note_id in mapped_ids:
        note = notes_by_id.get(note_id)
        excerpt = getattr(note, "source_excerpt", "") if note else ""
        normalized_excerpt = _normalize_for_grounding(excerpt)
        if normalized_excerpt and normalized_excerpt in normalized_text:
            return True
    return False


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    return str(value)


def _normalize_for_grounding(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _string_items(values: Iterable[Any]) -> list[str]:
    result = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            result.append(str(value.get("text") or value.get("message") or value.get("angle") or ""))
        else:
            result.append(str(value))
    return [item for item in result if item]


def _strip_prefix(message: str) -> str:
    if ":" in message:
        return message.split(":", 1)[1].strip()
    return message.strip()


def _short(text: str, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "..."


def _note_id(note: HospitalSourceEvidenceNote) -> str:
    return str(note.id)


def _status_value(status: Any) -> str | None:
    return status.value if hasattr(status, "value") else str(status) if status else None


def _has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
