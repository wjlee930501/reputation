"""Source-backed content operating standard engine.

원장의 톤/문체·핵심 의료 지식·가치(essence)를 자료에서 추출한다.

- ANTHROPIC_API_KEY가 있으면 Claude로 근거 노트 추출 + 철학 합성 (heart path).
- 키가 없으면(오프라인/CI) deterministic regex 폴백으로 동작해 테스트가 항상 통과한다.

어느 경로든 모든 evidence note의 source_excerpt는 raw_text/operator_note의 verbatim
부분문자열이어야 하며, 그렇지 않은 노트는 버린다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import anthropic
from sqlalchemy import select
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentItem
from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PHOTO_SOURCE_TYPES,
    PhilosophyStatus,
    SourceStatus,
)
from app.models.hospital import Hospital
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

ESSENCE_STATUS_ALIGNED = "ALIGNED"
ESSENCE_STATUS_NEEDS_REVIEW = "NEEDS_ESSENCE_REVIEW"
ESSENCE_STATUS_MISSING_APPROVED = "MISSING_APPROVED_PHILOSOPHY"

# content_engine.py와 동일한 sync Anthropic 클라이언트 패턴 — tenacity가 재시도를 관리하므로
# SDK 내부 재시도는 끈다. 키가 없으면 lazy하게 None을 유지해 deterministic 폴백으로 떨어진다.
_RAW_TEXT_FOR_LLM_LIMIT = 24_000
_VALID_NOTE_TYPES = {note_type.value for note_type in EvidenceNoteType}
_LOCAL_CONTEXT_PATTERN = re.compile(
    r"(?<![가-힣])(?:"
    r"서울|부산|대구|인천|광주|대전|울산|세종|"
    r"[가-힣]{1,10}(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리|역)"
    r")(?![가-힣])"
)


def _anthropic_client() -> anthropic.Anthropic | None:
    if not settings.ANTHROPIC_API_KEY:
        return None
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=60.0,
        max_retries=0,
    )


def llm_enabled() -> bool:
    """LLM 경로 사용 가능 여부 — 키가 있으면 True, 없으면 deterministic 폴백."""
    return bool(settings.ANTHROPIC_API_KEY)


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
            "|".join(
                [
                    str(source.id),
                    source.content_hash or "",
                    source.status.value if hasattr(source.status, "value") else str(source.status),
                    source.processed_at.isoformat() if source.processed_at else "",
                ]
            )
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


def process_source_asset(
    asset: HospitalSourceAsset, *, use_llm: bool = True
) -> list[EvidenceNotePayload]:
    """자료 원문에서 근거 노트를 추출한다.

    ANTHROPIC_API_KEY가 있으면 Claude로 추출하고, 없으면 deterministic 폴백을 쓴다.
    LLM 호출이 실패하면 deterministic 폴백으로 안전하게 떨어진다.
    어느 경로든 source_excerpt는 원문 verbatim이어야 한다.
    """
    if not asset.raw_text or not asset.raw_text.strip():
        raise ValueError("원문 텍스트가 없는 자료는 처리할 수 없습니다.")

    if use_llm and llm_enabled():
        try:
            payloads = _process_source_asset_llm(asset)
            if payloads:
                return payloads
            logger.info(
                "essence LLM source-processing returned no notes; using deterministic fallback"
            )
        except Exception as exc:  # noqa: BLE001 — LLM 실패 시 폴백으로 계속
            logger.warning(
                "essence LLM source-processing failed (%s); using deterministic fallback", exc
            )

    return _process_source_asset_deterministic(asset)


# ── LLM source-processing ────────────────────────────────────────────────
# 계약: raw_text를 주면 evidence_notes[] 배열을 돌려준다. 각 노트는
#   {note_type, claim, source_excerpt, confidence, note_metadata}
# 이며 source_excerpt는 반드시 raw_text의 verbatim 부분문자열이어야 한다.
# 9개 note_type enum 값만 허용한다. 외부 지식·창작은 금지하고, 발췌가 원문에 없으면 버린다.
_SOURCE_PROCESSING_SYSTEM = """\
당신은 병원 온보딩 자료에서 콘텐츠 운영 근거를 색인하는 분석가입니다.
주어진 원문(raw_text)에서만 근거를 뽑습니다. 외부 지식·추정·창작은 절대 금지합니다.

추출 규칙:
1. 각 근거는 원문에 실제로 존재하는 짧은 발췌(source_excerpt)에 묶입니다.
   source_excerpt는 raw_text의 글자 그대로의 부분문자열이어야 합니다(요약·수정 금지).
2. note_type은 다음 9개 중 하나만 사용합니다:
   KEY_MESSAGE(반복되는 핵심 메시지),
   TONE_SIGNAL(문체/톤 — 차분, 친절, 자세히 설명 등),
   TREATMENT_SIGNAL(진료/시술 설명 — note_metadata.treatment에 진료 라벨),
   RISK_SIGNAL(의료광고·과장 리스크 표현),
   PATIENT_PROMISE(환자에게 하는 약속),
   DOCTOR_PHILOSOPHY(원장의 진료 철학·원칙),
   LOCAL_CONTEXT(지역 맥락),
   PROOF_POINT(검증 가능한 근거·실적),
   CONFLICT(상충하는 서술).
3. claim은 그 발췌가 뒷받침하는 짧은 명제(한국어 한 문장)입니다.
4. confidence는 0~1 사이 숫자입니다.
5. 의료광고 금지 표현(1등/최고/유일/완치/100%/성공률/부작용 없는 등)이 보이면
   RISK_SIGNAL로 분류하고 note_metadata.violations에 해당 표현 배열을 넣습니다.

출력은 JSON 객체 하나만, 코드블록/설명 없이:
{
  "evidence_notes": [
    {
      "note_type": "DOCTOR_PHILOSOPHY",
      "claim": "원장은 충분한 설명을 중요하게 여긴다.",
      "source_excerpt": "치료 전 충분한 설명을 드리는 것을 중요하게 생각합니다",
      "confidence": 0.86,
      "note_metadata": {"treatment": null, "patient_language": ["충분한 설명"]}
    }
  ]
}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _call_anthropic_json(system: str, user_message: str, *, max_tokens: int) -> dict[str, Any]:
    """비용을 아끼기 위해 fast 모델로 essence 추출/합성을 호출하고 JSON으로 파싱한다."""
    client = _anthropic_client()
    if client is None:  # pragma: no cover — llm_enabled() 가드 이후에만 호출됨
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되어 있지 않습니다.")
    response = client.messages.create(
        model=settings.CLAUDE_MODEL_FAST,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = response.content[0].text
    return _parse_json_object(raw)


def _process_source_asset_llm(asset: HospitalSourceAsset) -> list[EvidenceNotePayload]:
    """Claude로 근거 노트를 추출하고, 원문 verbatim 가드를 통과한 노트만 남긴다."""
    raw_text = (asset.raw_text or "")[:_RAW_TEXT_FOR_LLM_LIMIT]
    operator_note = (asset.operator_note or "").strip()
    user_message = (
        f"[원문 raw_text]\n{raw_text}\n\n"
        + (f"[운영자 메모 operator_note]\n{operator_note}\n\n" if operator_note else "")
        + "위 원문에서만 근거 노트를 추출해 JSON으로 출력하세요."
    )
    data = _call_anthropic_json(_SOURCE_PROCESSING_SYSTEM, user_message, max_tokens=3000)

    payloads: list[EvidenceNotePayload] = []
    seen: set[tuple[str, str]] = set()
    for raw_note in _as_list(data.get("evidence_notes")):
        if not isinstance(raw_note, dict):
            continue
        note_type = _coerce_note_type(raw_note.get("note_type"))
        excerpt = raw_note.get("source_excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        excerpt = excerpt.strip()
        # 차단·오류 페이지 잔재("Title: 403 Forbidden" 등)는 근거 노트로 만들지 않는다.
        if looks_like_error_page_text(excerpt):
            continue
        # CRITICAL: 원문 verbatim이 아닌 발췌는 버린다.
        start, end = find_excerpt_bounds(asset, excerpt)
        if start is None or end is None:
            continue

        metadata = raw_note.get("note_metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        violations = check_forbidden(excerpt)
        if violations:
            note_type = EvidenceNoteType.RISK_SIGNAL
            metadata.setdefault("violations", violations)

        key = (note_type.value, excerpt)
        if key in seen:
            continue
        seen.add(key)

        claim = raw_note.get("claim")
        claim = (
            claim.strip()
            if isinstance(claim, str) and claim.strip()
            else _claim_from_excerpt(note_type, excerpt)
        )
        payloads.append(
            EvidenceNotePayload(
                note_type=note_type,
                claim=claim,
                source_excerpt=excerpt,
                excerpt_start=start,
                excerpt_end=end,
                confidence=_coerce_confidence(raw_note.get("confidence")),
                note_metadata=metadata,
            )
        )
        if len(payloads) >= 20:
            break
    return payloads


def _process_source_asset_deterministic(asset: HospitalSourceAsset) -> list[EvidenceNotePayload]:
    """규칙 기반 근거 노트 추출 — LLM 키가 없는 오프라인/CI 폴백."""
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
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """저장된 근거 노트만으로 콘텐츠 철학 초안을 만든다.

    ANTHROPIC_API_KEY가 있으면 Claude로 합성하고, 없으면 deterministic 폴백을 쓴다.
    LLM 합성이 실패하거나 grounding 검증을 통과하지 못하면 deterministic 폴백으로 떨어진다.
    """
    # 차단·오류 페이지 잔재("Title: 403 Forbidden" 등)가 든 근거 노트는 철학 조립에서 제외한다.
    # (기존 오염 노트가 DB에 남아 있어도 positioning/promise 등 핵심 필드로 새지 않게 한다.)
    notes = [
        note
        for note in notes
        if not looks_like_error_page_text(getattr(note, "source_excerpt", "") or "")
    ]
    if use_llm and llm_enabled() and notes:
        try:
            payload = _synthesize_philosophy_llm(hospital, sources, notes, operator_note)
            if (
                payload is not None
                and payload.get("evidence_map")  # grounded 필드가 최소 1개는 있어야 함
                and not validate_philosophy_grounding(payload, notes)
            ):
                return payload
            logger.info("essence LLM synthesis ungrounded or empty; using deterministic fallback")
        except Exception as exc:  # noqa: BLE001 — LLM 실패 시 폴백으로 계속
            logger.warning("essence LLM synthesis failed (%s); using deterministic fallback", exc)

    return _synthesize_philosophy_deterministic(hospital, sources, notes, operator_note)


# ── LLM philosophy synthesis ─────────────────────────────────────────────
# 계약: 근거 노트들을 주면 각 철학 필드를 {text, evidence_note_ids}로 돌려준다.
# doctor_voice는 실제 문체 descriptor(verbatim 인용이 아닌 재서술)이어야 하고,
# treatment_narratives는 {treatment, patient_language[], cautions[], evidence_note_ids[]}로
# 근거 노트에 묶인 실제 서술이어야 한다. 모든 비어있지 않은 필드는 evidence_note_ids를 갖는다.
_SYNTHESIS_SYSTEM = """\
당신은 병원 콘텐츠 운영 기준(철학)을 근거 기반으로 정리하는 편집자입니다.
입력으로 받은 근거 노트(각 노트는 id와 source_excerpt를 가짐)에서만 도출합니다.
근거 없는 자격·수상·효과·비교 우위·환자 결과를 창작하지 않습니다.

작성 규칙:
1. 각 출력 필드는 그 내용을 뒷받침하는 evidence_note_ids(입력 노트의 id 배열)를 가져야 합니다.
   근거 노트가 없으면 그 필드는 비워두고 unsupported_gaps에 사유를 남깁니다.
2. doctor_voice.text는 verbatim 인용이 아니라 원장의 실제 문체를 묘사하는 문장입니다.
   예: "단정적 홍보를 피하고 과정을 차분히 설명하는 1인칭 설명형 문체".
3. treatment_narratives는 각 항목이
   {treatment, patient_language[], cautions[], evidence_note_ids[]} 형태이며,
   근거 노트에서 도출된 실제 환자 언어와 주의사항을 담습니다(상수 문구 금지).
4. 의료광고 금지 표현(1등/최고/유일/완치/100%/성공률/부작용 없는 등)은 출력에 쓰지 않습니다.
   환자에게 결과를 보장하는 약속도 만들지 않습니다.
5. 상충하는 근거는 conflict_notes에 남기고 임의로 결론내리지 않습니다.

출력은 JSON 객체 하나만, 코드블록/설명 없이. 모든 텍스트 필드는 {text, evidence_note_ids} 형태:
{
  "positioning_statement": {"text": "...", "evidence_note_ids": ["..."]},
  "doctor_voice": {"text": "...", "evidence_note_ids": ["..."]},
  "patient_promise": {"text": "...", "evidence_note_ids": ["..."]},
  "content_principles": [{"text": "...", "evidence_note_ids": ["..."]}],
  "tone_guidelines": [{"text": "...", "evidence_note_ids": ["..."]}],
  "must_use_messages": [{"text": "...", "evidence_note_ids": ["..."]}],
  "avoid_messages": [{"text": "...", "evidence_note_ids": ["..."]}],
  "treatment_narratives": [
    {"treatment": "...", "patient_language": ["..."], "cautions": ["..."], "evidence_note_ids": ["..."]}
  ],
  "local_context": {"region_terms": [], "local_patient_context": ["..."], "evidence_note_ids": ["..."]},
  "medical_ad_risk_rules": [{"text": "...", "evidence_note_ids": ["..."]}],
  "unsupported_gaps": [{"field": "...", "reason": "..."}],
  "conflict_notes": [{"text": "...", "evidence_note_ids": ["..."]}],
  "synthesis_notes": "근거 기반 요약. 외부 지식 사용 없음."
}
"""

# 텍스트 + evidence_note_ids 쌍을 받는 단일/리스트 필드.
_TEXT_FIELDS_SINGLE = ("positioning_statement", "doctor_voice", "patient_promise")
_TEXT_FIELDS_LIST = (
    "content_principles",
    "tone_guidelines",
    "must_use_messages",
    "avoid_messages",
    "medical_ad_risk_rules",
)


def _synthesize_philosophy_llm(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None,
) -> dict[str, Any] | None:
    """Claude로 철학을 합성하고, 저장 가능한 dict 페이로드로 정규화한다.

    LLM이 참조한 evidence_note_ids는 입력 노트 id 집합으로 필터링되어 환각 참조를 막는다.
    """
    valid_ids = {_note_id(note): note for note in notes}
    notes_block = "\n".join(
        f"- id={_note_id(note)} | type={_note_type_value(note)} | "
        f"excerpt={_short(note.source_excerpt, 160)}"
        for note in notes
    )
    operator_block = (
        f"\n[운영자 작성 방향 operator_note]\n{operator_note.strip()}\n" if operator_note else ""
    )
    user_message = (
        f"[병원]\n{getattr(hospital, 'name', '') or ''}\n"
        f"{operator_block}\n"
        f"[근거 노트]\n{notes_block}\n\n"
        "위 근거 노트만 사용해 콘텐츠 운영 기준을 JSON으로 합성하세요. "
        "각 필드의 evidence_note_ids는 위 id 목록 안에서만 고릅니다."
    )
    data = _call_anthropic_json(_SYNTHESIS_SYSTEM, user_message, max_tokens=4000)

    evidence_map: dict[str, list[str]] = {}
    payload: dict[str, Any] = {}

    for field_name in _TEXT_FIELDS_SINGLE:
        text, ids = _extract_text_and_ids(data.get(field_name), valid_ids)
        payload[field_name] = text if (text and ids) else None
        if text and ids:
            evidence_map[field_name] = ids

    for field_name in _TEXT_FIELDS_LIST:
        items = _extract_text_list(data.get(field_name), valid_ids)
        payload[field_name] = [text for text, _ in items]
        ids = _unique_ids(_id for _, _id_list in items for _id in _id_list)
        if ids:
            evidence_map[field_name] = ids

    treatment_narratives, treatment_ids = _extract_treatment_narratives(
        data.get("treatment_narratives"), valid_ids
    )
    payload["treatment_narratives"] = treatment_narratives
    if treatment_ids:
        evidence_map["treatment_narratives"] = treatment_ids

    local_context, local_ids = _extract_local_context(data.get("local_context"), valid_ids)
    payload["local_context"] = local_context
    if local_ids:
        evidence_map["local_context"] = local_ids

    payload["evidence_map"] = evidence_map
    payload["source_asset_ids"] = [str(source.id) for source in sources]
    payload["unsupported_gaps"] = _sanitize_gaps(data.get("unsupported_gaps"))
    payload["conflict_notes"] = _extract_text_list_payload(data.get("conflict_notes"), valid_ids)
    synthesis_notes = data.get("synthesis_notes")
    payload["synthesis_notes"] = (
        synthesis_notes.strip()
        if isinstance(synthesis_notes, str) and synthesis_notes.strip()
        else "Claude 합성. 근거 노트 기반, 외부 지식 사용 없음."
    )
    payload["source_snapshot_hash"] = compute_sources_snapshot_hash(sources)
    return payload


def _extract_text_and_ids(
    value: Any, valid_ids: dict[str, HospitalSourceEvidenceNote]
) -> tuple[str | None, list[str]]:
    """{text, evidence_note_ids} 단일 필드를 (text, 유효 id 목록)으로 정규화한다."""
    if isinstance(value, str):
        return (value.strip() or None), []
    if not isinstance(value, dict):
        return None, []
    text = value.get("text")
    text = text.strip() if isinstance(text, str) and text.strip() else None
    ids = [str(i) for i in _as_list(value.get("evidence_note_ids")) if str(i) in valid_ids]
    return text, ids


def _extract_text_list(
    value: Any, valid_ids: dict[str, HospitalSourceEvidenceNote]
) -> list[tuple[str, list[str]]]:
    """{text, evidence_note_ids} 항목 리스트를 (text, 유효 id 목록) 튜플 리스트로 정규화한다."""
    items: list[tuple[str, list[str]]] = []
    for entry in _as_list(value):
        text, ids = _extract_text_and_ids(entry, valid_ids)
        if text and ids:
            items.append((text, ids))
    return items


def _extract_text_list_payload(
    value: Any, valid_ids: dict[str, HospitalSourceEvidenceNote]
) -> list[dict[str, Any]]:
    """conflict_notes 등 — {text, evidence_note_ids} 저장 형태로 정규화."""
    return [
        {"text": text, "evidence_note_ids": ids}
        for text, ids in _extract_text_list(value, valid_ids)
    ]


def _extract_treatment_narratives(
    value: Any, valid_ids: dict[str, HospitalSourceEvidenceNote]
) -> tuple[list[dict[str, Any]], list[str]]:
    """treatment_narratives를 저장 형태로 정규화하고, 참조 id를 모은다."""
    narratives: list[dict[str, Any]] = []
    all_ids: list[str] = []
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        ids = [str(i) for i in _as_list(entry.get("evidence_note_ids")) if str(i) in valid_ids]
        if not ids:
            continue
        treatment = entry.get("treatment")
        narratives.append(
            {
                "treatment": treatment.strip()
                if isinstance(treatment, str) and treatment.strip()
                else "자료 기반 진료 항목",
                "patient_language": _string_list(entry.get("patient_language")),
                "cautions": _string_list(entry.get("cautions"))
                or ["효과, 완치, 성공률을 보장하지 않습니다."],
                "evidence_note_ids": ids,
            }
        )
        all_ids.extend(ids)
    return narratives, _unique_ids(all_ids)


def _extract_local_context(
    value: Any, valid_ids: dict[str, HospitalSourceEvidenceNote]
) -> tuple[dict[str, Any], list[str]]:
    base: dict[str, Any] = {
        "region_terms": [],
        "local_patient_context": [],
        "avoid_region_stuffing": True,
    }
    if not isinstance(value, dict):
        return base, []
    ids = [str(i) for i in _as_list(value.get("evidence_note_ids")) if str(i) in valid_ids]
    base["region_terms"] = _string_list(value.get("region_terms"))
    base["local_patient_context"] = _string_list(value.get("local_patient_context"))
    if ids and (base["region_terms"] or base["local_patient_context"]):
        base["evidence_note_ids"] = ids
        return base, ids
    return base, []


def _sanitize_gaps(value: Any) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for entry in _as_list(value):
        if isinstance(entry, dict):
            field_name = str(entry.get("field") or "")
            reason = str(entry.get("reason") or "")
            if field_name or reason:
                gaps.append({"field": field_name, "reason": reason})
    return gaps


def _coerce_note_type(value: Any) -> EvidenceNoteType:
    if isinstance(value, str) and value.strip().upper() in _VALID_NOTE_TYPES:
        return EvidenceNoteType(value.strip().upper())
    return EvidenceNoteType.KEY_MESSAGE


def _coerce_confidence(value: Any) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.75
    return min(max(conf, 0.0), 1.0)


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Claude JSON 응답을 dict로 파싱 — 마크다운 fence/주변 텍스트를 관용적으로 제거."""
    clean = (raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", clean, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        clean = fenced.group(1).strip()
    else:
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            clean = clean[start : end + 1]
    parsed = json.loads(clean)
    if not isinstance(parsed, dict):
        raise ValueError("essence LLM이 객체가 아닌 JSON을 반환했습니다.")
    return parsed


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    return [item.strip() for item in _as_list(value) if isinstance(item, str) and item.strip()]


def _unique_ids(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for note_id in ids:
        if note_id not in seen:
            seen.add(note_id)
            result.append(note_id)
    return result


def _note_type_value(note: HospitalSourceEvidenceNote) -> str:
    note_type = getattr(note, "note_type", None)
    return note_type.value if hasattr(note_type, "value") else str(note_type)


def _synthesize_philosophy_deterministic(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
) -> dict[str, Any]:
    """근거 노트에서 규칙 기반으로 철학 초안을 합성 — LLM 키가 없는 폴백."""
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
        positioning_statement = (
            f"자료에서 확인된 핵심 메시지: {_short(key_notes[0].source_excerpt, 140)}"
        )
        evidence_map["positioning_statement"] = [_note_id(key_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "positioning_statement", "reason": "핵심 메시지 근거 note가 없습니다."}
        )

    doctor_voice = None
    if tone_notes:
        doctor_voice = f"자료 표현 기준 문체: {_short(tone_notes[0].source_excerpt, 140)}"
        evidence_map["doctor_voice"] = [_note_id(tone_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "doctor_voice", "reason": "문체/톤 근거 note가 없습니다."}
        )

    patient_promise = None
    if promise_notes:
        patient_promise = f"환자에게 말할 수 있는 약속은 이 근거 범위로 제한: {_short(promise_notes[0].source_excerpt, 140)}"
        evidence_map["patient_promise"] = [_note_id(promise_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "patient_promise", "reason": "환자 약속 근거 note가 없습니다."}
        )

    content_principles = [
        f"근거 문장을 벗어나지 않고 설명합니다: {_short(note.source_excerpt, 120)}"
        for note in key_notes[:3]
    ]
    if content_principles:
        evidence_map["content_principles"] = [_note_id(note) for note in key_notes[:3]]
    else:
        unsupported_gaps.append(
            {"field": "content_principles", "reason": "콘텐츠 원칙으로 전환할 근거가 없습니다."}
        )

    tone_guidelines = [
        f"원문 톤을 유지합니다: {_short(note.source_excerpt, 120)}" for note in tone_notes
    ]
    if tone_guidelines:
        evidence_map["tone_guidelines"] = [_note_id(note) for note in tone_notes]

    must_use_messages = [_short(note.source_excerpt, 160) for note in key_notes]
    if must_use_messages:
        evidence_map["must_use_messages"] = [_note_id(note) for note in key_notes]

    avoid_messages = [
        f"검수 필요 표현 또는 약속: {_short(note.source_excerpt, 120)}" for note in risk_notes
    ]
    if avoid_messages:
        evidence_map["avoid_messages"] = [_note_id(note) for note in risk_notes]

    treatment_narratives = []
    for note in treatment_notes:
        treatment_narratives.append(
            {
                "treatment": (note.note_metadata or {}).get("treatment") or "자료 기반 진료 항목",
                "angle": _short(note.source_excerpt, 140),
                "explanation_style": "근거 발췌에 포함된 표현만 사용합니다.",
                "cautions": ["효과, 완치, 성공률을 보장하지 않습니다."],
                "evidence_note_ids": [_note_id(note)],
            }
        )
    if treatment_narratives:
        evidence_map["treatment_narratives"] = [_note_id(note) for note in treatment_notes]
    else:
        unsupported_gaps.append(
            {"field": "treatment_narratives", "reason": "진료/시술 설명 근거 note가 없습니다."}
        )

    local_context = {"region_terms": [], "local_patient_context": [], "avoid_region_stuffing": True}
    if local_notes:
        local_context["local_patient_context"] = [
            _short(note.source_excerpt, 120) for note in local_notes
        ]
        local_context["evidence_note_ids"] = [_note_id(note) for note in local_notes]
        evidence_map["local_context"] = [_note_id(note) for note in local_notes]

    medical_ad_risk_rules = []
    for note in risk_notes:
        violations = (note.note_metadata or {}).get("violations") or []
        if violations:
            medical_ad_risk_rules.append(
                f"{', '.join(violations)} 표현은 근거와 별도 심의 없이 사용하지 않습니다: "
                f"{_short(note.source_excerpt, 120)}"
            )
        else:
            medical_ad_risk_rules.append(f"리스크 표현 검수: {_short(note.source_excerpt, 120)}")
    if medical_ad_risk_rules:
        evidence_map["medical_ad_risk_rules"] = [_note_id(note) for note in risk_notes]

    if not risk_notes:
        unsupported_gaps.append(
            {
                "field": "medical_ad_risk_rules",
                "reason": "자료에서 병원별 리스크 표현이 별도로 발견되지 않았습니다.",
            }
        )

    conflict_payload = [
        {"text": _short(note.source_excerpt, 160), "evidence_note_ids": [_note_id(note)]}
        for note in conflict_notes
    ]

    if operator_note:
        unsupported_gaps.append(
            {
                "field": "operator_note",
                "reason": "초안 생성 메모는 참고만 했고, 저장 필드는 근거 노트에 매핑된 값으로 제한했습니다.",
            }
        )

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
    """비어있지 않은 철학 필드가 실제 근거 노트를 가리키는지 검증한다.

    합성된 voice/narrative descriptor는 원문 verbatim 인용이 아니라 노트에서 도출된
    재서술이므로, "verbatim 부분문자열 포함"을 요구하지 않는다. 대신 각 필드의 mapped
    evidence_note_ids가 실제로 존재하고 해당 병원의 노트를 참조하는지(derived-from)만
    요구한다. 노트 자체가 원문 verbatim임은 별도(validate_source_excerpt)로 보장된다.

    `require_text_support`는 호환을 위해 남겨두되, edit/approve 경로에서도 verbatim 포함을
    강제하지 않는다 — 진짜 합성된 doctor_voice가 가능해야 하기 때문이다.
    """
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
            errors.append(
                f"{field_name} 필드가 존재하지 않는 근거 노트를 참조합니다: {', '.join(unknown)}"
            )
            continue

    return errors


# 오류 마커 스캔 대상 핵심 필드 — 이 중 하나라도 잔재를 담으면 초안을 만들지 않는다.
_ERROR_MARKER_SCAN_FIELDS = (
    "positioning_statement",
    "doctor_voice",
    "patient_promise",
    "content_principles",
    "tone_guidelines",
    "must_use_messages",
    "avoid_messages",
    "medical_ad_risk_rules",
    "treatment_narratives",
)


def _iter_field_texts(value: Any) -> Iterable[str]:
    """단일 문자열 / 문자열 리스트 / dict 리스트 필드에서 검사할 텍스트를 순회한다."""
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_field_texts(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_field_texts(item)
        return


def find_error_marker_fields(payload: Any) -> list[str]:
    """조립된 철학 페이로드에서 차단·오류 페이지 잔재가 남은 핵심 필드명을 찾는다.

    수집(fetch)·note 추출 단계에서 이미 잔재를 걸러내지만, 어떤 경로로든 핵심 필드에
    잔재가 남으면 초안 생성을 막기 위한 조립 계층의 최종 방어다. 반환 목록이 비어있지
    않으면 호출부는 초안을 만들지 말고 명확한 실패 사유를 남긴다.
    """
    flagged: list[str] = []
    for field_name in _ERROR_MARKER_SCAN_FIELDS:
        value = _payload_get(payload, field_name)
        if any(looks_like_error_page_text(text) for text in _iter_field_texts(value)):
            flagged.append(field_name)
    return flagged


def approved_philosophy_issues(
    philosophy: HospitalContentPhilosophy | None,
    processed_sources: Iterable[HospitalSourceAsset] | None = None,
    *,
    require_fresh: bool = False,
) -> list[str]:
    """생성·발행에 사용할 승인 운영 기준의 무결성과 자료 최신성을 검사한다.

    과거에 승인된 레코드는 현재의 초안 생성 게이트를 거치지 않았을 수 있으므로
    호출 시점에도 오류 페이지 잔재를 차단한다. ``require_fresh``일 때는 현재 처리된
    자료 snapshot과 승인 당시 snapshot이 다르면 새 버전 검토 전까지 생성/발행을 막는다.
    """
    if not philosophy or _status_value(getattr(philosophy, "status", None)) != PhilosophyStatus.APPROVED.value:
        return ["승인된 콘텐츠 운영 기준이 없습니다."]

    issues: list[str] = []
    marker_fields = find_error_marker_fields(philosophy)
    if marker_fields:
        issues.append(
            "승인된 콘텐츠 운영 기준에 차단·오류 페이지 잔재가 있습니다: "
            + ", ".join(marker_fields)
        )

    if require_fresh:
        sources = [
            source
            for source in (processed_sources or [])
            if _status_value(getattr(source, "status", None)) == SourceStatus.PROCESSED.value
        ]
        current_hash = compute_sources_snapshot_hash(sources)
        if not sources:
            issues.append("처리 완료된 병원 근거 자료가 없습니다.")
        elif philosophy.source_snapshot_hash != current_hash:
            issues.append("처리된 병원 자료가 승인 이후 변경되어 운영 기준 새 버전 검토가 필요합니다.")
    return issues


def screen_content_against_philosophy(
    content_item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
) -> EssenceScreeningResult:
    if (
        not philosophy
        or _status_value(getattr(philosophy, "status", None)) != PhilosophyStatus.APPROVED.value
    ):
        return EssenceScreeningResult(
            status=ESSENCE_STATUS_MISSING_APPROVED,
            summary={
                "blocking": True,
                "findings": ["승인된 콘텐츠 운영 기준이 없습니다."],
                "checked_at": _now_iso(),
            },
        )

    integrity_issues = approved_philosophy_issues(philosophy)
    if integrity_issues:
        return EssenceScreeningResult(
            status=ESSENCE_STATUS_NEEDS_REVIEW,
            summary={
                "blocking": True,
                "philosophy_id": str(philosophy.id),
                "philosophy_version": philosophy.version,
                "findings": integrity_issues,
                "checked_at": _now_iso(),
            },
        )

    # FAQ 분리 필드도 공개 표면(FAQPage rich result)에 그대로 노출되므로
    # 운영 기준/금지 표현 검수 텍스트에 반드시 포함한다 (P1-2).
    text = " ".join(
        part
        for part in [
            getattr(content_item, "title", None),
            getattr(content_item, "body", None),
            getattr(content_item, "meta_description", None),
            getattr(content_item, "faq_question", None),
            getattr(content_item, "faq_answer_summary", None),
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


def build_monthly_essence_summary(
    db, hospital: Hospital, period_start: datetime, period_end: datetime
) -> dict[str, Any]:
    source_result = db.execute(
        select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital.id)
    )
    sources = source_result.scalars().all()
    # 사진은 공개 자산 검수 대상이지 글쓰기 기준의 근거 자료가 아니다. 제외 자료 역시
    # 현재 기준에서 명시적으로 빠졌으므로 월간 Essence 완결성 분모에 포함하지 않는다.
    # 이 정의는 services/essence_readiness.py의 실시간 게이트와 반드시 같아야 한다.
    required_sources = [
        source
        for source in sources
        if source.status != SourceStatus.EXCLUDED and source.source_type not in PHOTO_SOURCE_TYPES
    ]
    processed_sources = [
        source for source in required_sources if source.status == SourceStatus.PROCESSED
    ]

    approved = get_approved_philosophy_sync(db, hospital.id)
    source_snapshot_hash = compute_sources_snapshot_hash(processed_sources)
    source_stale = bool(
        approved
        and (
            len(processed_sources) != len(required_sources)
            or not processed_sources
            or approved.source_snapshot_hash != source_snapshot_hash
        )
    )

    content_result = db.execute(
        select(ContentItem).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.scheduled_date >= period_start.date(),
            ContentItem.scheduled_date <= period_end.date(),
        )
    )
    items = content_result.scalars().all()
    generated_items = [item for item in items if item.body]

    aligned_count = sum(
        1
        for item in generated_items
        if not source_stale
        and item.essence_status == ESSENCE_STATUS_ALIGNED
        and approved is not None
        and item.content_philosophy_id == approved.id
    )
    needs_review_count = sum(
        1
        for item in generated_items
        if item.essence_status == ESSENCE_STATUS_NEEDS_REVIEW
        or (
            item.essence_status == ESSENCE_STATUS_ALIGNED
            and (source_stale or approved is None or item.content_philosophy_id != approved.id)
        )
    )
    missing_count = sum(
        1 for item in generated_items if item.essence_status == ESSENCE_STATUS_MISSING_APPROVED
    )

    medical_risk_findings = []
    for item in generated_items:
        violations = check_forbidden(
            " ".join(
                part
                for part in [
                    item.title,
                    item.body,
                    item.meta_description,
                    item.faq_question,
                    item.faq_answer_summary,
                ]
                if part
            )
        )
        if violations:
            medical_risk_findings.append(
                {
                    "content_id": str(item.id),
                    "title": item.title,
                    "violations": violations,
                }
            )

    recommended_actions = []
    if not approved:
        recommended_actions.append("승인된 콘텐츠 운영 기준을 생성/승인하세요.")
    if not required_sources:
        recommended_actions.append("온보딩 자료를 1개 이상 원문 텍스트 기반으로 처리하세요.")
    elif len(processed_sources) != len(required_sources):
        recommended_actions.append("처리되지 않은 온보딩 자료를 처리하거나 제외하세요.")
    if source_stale:
        recommended_actions.append(
            "처리된 자료가 승인된 운영 기준과 달라졌습니다. 새 초안을 검토하세요."
        )
    if needs_review_count:
        recommended_actions.append("운영 기준 재검수가 필요한 콘텐츠를 수정하세요.")
    if missing_count:
        recommended_actions.append(
            "승인된 콘텐츠 운영 기준 없이 생성된 콘텐츠를 재생성하거나 검수하세요."
        )
    if medical_risk_findings:
        recommended_actions.append("의료광고 리스크 표현이 있는 콘텐츠를 발행 전 수정하세요.")

    return {
        "approved_philosophy_exists": approved is not None,
        "philosophy_version": approved.version if approved else None,
        "approved_at": approved.approved_at.isoformat()
        if approved and approved.approved_at
        else None,
        "source_count": len(required_sources),
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
            # RSS/Markdown 문서의 짧은 blockquote·bold 제목은 주장이 아니라
            # 목차/SEO 헤딩인 경우가 많다. 폴백 노트가 "근거 확인: > **제목"처럼
            # 오염되는 것을 막고 실제 설명 문장을 우선한다.
            if re.match(r"^>\s*(?:\*\*)?.{1,80}(?:\*\*)?[?!。！？]?$", excerpt):
                continue
            # 차단·오류 페이지 잔재("Title: 403 Forbidden" 등)는 후보 발췌에서 제외한다.
            if looks_like_error_page_text(excerpt):
                continue
            if len(excerpt) > 220:
                excerpt = excerpt[:220].strip()
            if len(excerpt) < 12 and not check_forbidden(excerpt):
                continue
            if excerpt in text and excerpt not in excerpts:
                excerpts.append(excerpt)
    return excerpts[:30]


def _classify_excerpt(excerpt: str) -> EvidenceNoteType:
    if check_forbidden(excerpt) or _has_any(
        excerpt, ["보장", "무조건", "부작용", "완치", "최고", "유일"]
    ):
        return EvidenceNoteType.RISK_SIGNAL
    if _has_any(excerpt, ["철학", "원장", "진료 원칙", "중요하게 생각"]):
        return EvidenceNoteType.DOCTOR_PHILOSOPHY
    if _has_any(excerpt, ["차분", "친절", "편안", "꼼꼼", "자세히", "충분히", "쉽게"]):
        return EvidenceNoteType.TONE_SIGNAL
    if _has_any(excerpt, ["치료", "수술", "시술", "검사", "내시경", "진료", "상담"]):
        return EvidenceNoteType.TREATMENT_SIGNAL
    if _has_any(excerpt, ["약속", "안심", "회복", "일상", "선택지"]):
        return EvidenceNoteType.PATIENT_PROMISE
    # 규칙 기반 폴백에서는 실제 행정지명과 '지역' 맥락이 함께 있을 때만
    # LOCAL_CONTEXT로 본다. 한국어 어미(생기면/합니다) 속 '면/시'를 행정구역으로
    # 오인해 의료 설명을 지역 주장으로 승격시키는 것보다 누락이 안전하다.
    if "지역" in excerpt and _LOCAL_CONTEXT_PATTERN.search(excerpt):
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
    match = re.search(
        r"([가-힣A-Za-z0-9]{2,20})(?:\s*)(치료|수술|시술|검사|내시경|진료|상담)", excerpt
    )
    if not match:
        return None
    return "".join(match.groups())


def _group_notes(
    notes: list[HospitalSourceEvidenceNote],
) -> dict[EvidenceNoteType, list[HospitalSourceEvidenceNote]]:
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


def _string_items(values: Iterable[Any]) -> list[str]:
    result = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            result.append(
                str(value.get("text") or value.get("message") or value.get("angle") or "")
            )
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
