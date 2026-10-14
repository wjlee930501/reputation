"""Source-backed content operating standard engine.

원장의 톤/문체·핵심 의료 지식·가치(essence)를 자료에서 추출한다.

- OPENROUTER_API_KEY가 있으면 LLM으로 근거 노트 추출 + 철학 합성 (heart path).
- 키가 없으면(오프라인/CI) deterministic regex 폴백으로 동작해 테스트가 항상 통과한다.

어느 경로든 모든 evidence note의 source_excerpt는 raw_text/operator_note의 verbatim
부분문자열이어야 하며, 그렇지 않은 노트는 버린다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable

from openai import OpenAI
from sqlalchemy import or_, select
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentItem
from app.models.essence import (
    EvidenceNoteType,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.services import llm_structured_output, openrouter
from app.services.essence_claims import (
    claim_is_supported,
    is_medical_claim,
    is_not_doctor_statement,
    is_verbatim_in,
    normalize_sentence,
    split_sentences,
    support_ratio,
    without_template_prefix,
)
from app.services.essence_sources import required_text_source_predicate
from app.services.openrouter import is_retryable_llm_error
from app.services.profile_facts_source import (
    is_profile_facts_source,
    profile_fact_note_payloads,
)
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import FORBIDDEN_EXPRESSIONS, check_forbidden

logger = logging.getLogger(__name__)

ESSENCE_STATUS_ALIGNED = "ALIGNED"
ESSENCE_STATUS_NEEDS_REVIEW = "NEEDS_ESSENCE_REVIEW"
ESSENCE_STATUS_MISSING_APPROVED = "MISSING_APPROVED_PHILOSOPHY"

# 병원 자료에서 추출하는 고유 규칙과 별개인 플랫폼 공통 안전 정책이다. 이 규칙을
# 자료 evidence에 억지로 연결하면 없는 출처를 만든 셈이 되므로 grounding 대상에서
# 제외하되, 모든 후보/승인본에는 반드시 존재하도록 검증한다.
MANDATORY_AVOID_MESSAGES = (
    "의료광고 공통 금지 표현을 홍보 문구로 사용하지 않습니다: "
    + ", ".join(FORBIDDEN_EXPRESSIONS),
)
MANDATORY_MEDICAL_AD_RISK_RULES = (
    "치료 효과·성공·완치·안전성을 단정하거나 보장하지 않습니다.",
    "비교 우위·유일성·최상급 표현은 객관적 근거와 적법한 심의 없이 사용하지 않습니다.",
    "환자 개인의 치료 결과를 일반화하지 않고 부작용·한계·개인차를 함께 검수합니다.",
)


def apply_mandatory_safety_policy(payload: dict[str, Any]) -> dict[str, Any]:
    for field_name, mandatory in (
        ("avoid_messages", MANDATORY_AVOID_MESSAGES),
        ("medical_ad_risk_rules", MANDATORY_MEDICAL_AD_RISK_RULES),
    ):
        current = [str(item) for item in payload.get(field_name) or [] if str(item).strip()]
        payload[field_name] = list(dict.fromkeys([*current, *mandatory]))
    return payload


def effective_safety_policy(payload: Any) -> dict[str, list[str]]:
    """Return the runtime safety floor without mutating an ORM model.

    Legacy approved rows may predate mandatory rules. Every consumer must see the
    same effective policy even before operators regenerate those stored rows.
    """
    return apply_mandatory_safety_policy(
        {
            "avoid_messages": list(_payload_get(payload, "avoid_messages") or []),
            "medical_ad_risk_rules": list(
                _payload_get(payload, "medical_ad_risk_rules") or []
            ),
        }
    )


def mandatory_safety_findings(payload: Any) -> list[str]:
    findings: list[str] = []
    for field_name, mandatory in (
        ("avoid_messages", MANDATORY_AVOID_MESSAGES),
        ("medical_ad_risk_rules", MANDATORY_MEDICAL_AD_RISK_RULES),
    ):
        values = {str(item) for item in (_payload_get(payload, field_name) or [])}
        missing = [item for item in mandatory if item not in values]
        if missing:
            findings.append(f"{field_name} 필드에 플랫폼 공통 의료광고 안전 규칙이 없습니다.")
    return findings

# content_engine.py와 동일한 sync Anthropic 클라이언트 패턴 — tenacity가 재시도를 관리하므로
# SDK 내부 재시도는 끈다. 키가 없으면 lazy하게 None을 유지해 deterministic 폴백으로 떨어진다.
_RAW_TEXT_FOR_LLM_LIMIT = 24_000
# Every character must be offered to extraction.  The limit above is a per-call
# provider budget, not a document truncation policy.  A small overlap keeps a
# sentence that crosses a boundary intact; exact note de-duplication below makes
# the overlap free of duplicate evidence rows.
_RAW_TEXT_CHUNK_OVERLAP = 800
_VALID_NOTE_TYPES = {note_type.value for note_type in EvidenceNoteType}
_LOCAL_CONTEXT_PATTERN = re.compile(
    r"(?<![가-힣])(?:"
    r"서울|부산|대구|인천|광주|대전|울산|세종|"
    r"[가-힣]{1,10}(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리|역)"
    r")(?![가-힣])"
)


# 클라이언트 1개 = HTTP 커넥션 풀 1개다. 호출마다 새로 만들면 재시도마다 TLS 핸드셰이크를
# 다시 하고 풀을 버린다. openrouter.sync_client가 timeout별로 같은 싱글턴을 돌려준다
# (SDK 내부 재시도는 꺼져 있고 tenacity가 재시도를 소유한다).


def _llm_client() -> OpenAI | None:
    if not openrouter.configured():
        return None
    return openrouter.sync_client(timeout=60.0)


def _reset_clients_for_tests() -> None:
    """테스트가 OPENROUTER_API_KEY/생성자를 바꿔치기한 뒤 캐시를 비우기 위한 훅."""

    openrouter.reset_clients_for_tests()


def llm_enabled() -> bool:
    """LLM 경로 사용 가능 여부 — 키가 있으면 True, 없으면 deterministic 폴백."""
    return openrouter.configured()


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


def _snapshot_datetime(value: datetime | None) -> str:
    """Serialize one instant consistently across DB/session time zones.

    PostgreSQL may return the same ``timestamptz`` using the connection's local
    offset.  Snapshot identity must follow the instant, not that presentation.
    Legacy naive values are interpreted as UTC, matching the application's
    historical timestamp convention and the old UTC-aware hash representation.
    """

    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def compute_sources_snapshot_hash(sources: Iterable[HospitalSourceAsset]) -> str:
    parts = []
    for source in sorted(sources, key=lambda item: str(item.id)):
        parts.append(
            "|".join(
                [
                    str(source.id),
                    source.content_hash or "",
                    source.status.value if hasattr(source.status, "value") else str(source.status),
                    _snapshot_datetime(source.processed_at),
                ]
            )
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


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

    OPENROUTER_API_KEY가 있으면 LLM으로 추출하고, 없으면 deterministic 폴백을 쓴다.
    LLM 호출이 실패하면 deterministic 폴백으로 안전하게 떨어진다.
    어느 경로든 source_excerpt는 원문 verbatim이어야 한다.
    """
    if not asset.raw_text or not asset.raw_text.strip():
        raise ValueError("원문 텍스트가 없는 자료는 처리할 수 없습니다.")

    if is_profile_facts_source(asset):
        # 병원 정보(자동) 자료는 줄 하나가 노트 하나다 — LLM을 부르지 않는다(Phase 4 E2).
        return [
            EvidenceNotePayload(
                note_type=note.note_type,
                claim=note.claim,
                source_excerpt=note.source_excerpt,
                excerpt_start=note.excerpt_start,
                excerpt_end=note.excerpt_end,
                confidence=1.0,
                note_metadata=dict(note.note_metadata),
            )
            for note in profile_fact_note_payloads(asset.raw_text)
        ]

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
   CONFLICT(같은 사실에 대한 서로 양립할 수 없는 서술).
   질환별 증상이 비슷하거나 두 질환이 함께 있을 수 있다는 감별진단 설명은
   자료 간 모순이 아닙니다. 이런 검사·진료 안내는 TREATMENT_SIGNAL로 분류합니다.
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

_SOURCE_PROCESSING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note_type": {"type": "string", "enum": sorted(_VALID_NOTE_TYPES)},
                    "claim": {"type": "string"},
                    "source_excerpt": {"type": "string"},
                    "confidence": {"type": "number"},
                    "note_metadata": {
                        "type": "object",
                        "properties": {
                            "treatment": {"type": ["string", "null"]},
                            "patient_language": {"type": "array", "items": {"type": "string"}},
                            "violations": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["treatment", "patient_language", "violations"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "note_type",
                    "claim",
                    "source_excerpt",
                    "confidence",
                    "note_metadata",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evidence_notes"],
    "additionalProperties": False,
}


class _LlmCallCounter:
    __slots__ = ("count", "events", "logical_call_count")

    def __init__(self) -> None:
        self.count = 0
        self.logical_call_count = 0
        self.events: list[dict[str, Any]] = []

    def begin_logical_call(self) -> int:
        self.logical_call_count += 1
        return self.logical_call_count

    def tick(self, *, logical_call: int, http_attempt: int) -> dict[str, Any]:
        self.count += 1
        event: dict[str, Any] = {
            "logical_call": logical_call,
            "http_attempt": http_attempt,
            "usage": None,
            "usage_known": False,
            "provider_request_id": None,
        }
        self.events.append(event)
        return event

    def record_response(self, event: dict[str, Any], response: Any) -> None:
        usage = getattr(response, "usage", None)
        event["usage"] = usage
        # Let provider_usage infer knownness from normalized non-null fields;
        # some SDK responses expose an empty usage object.
        event["usage_known"] = None
        event["provider_request_id"] = str(getattr(response, "id", "") or "") or None


# 이 모듈의 LLM 호출은 전부 _call_llm_json 하나를 지난다. 동기 코드라
# 그 자리에서 await할 수 없으므로, 호출자가 심어 둔 카운터를 올려두고 async 경계에서
# 기록한다. asyncio.to_thread는 컨텍스트를 복사하므로 스레드 안에서도 같은 객체를 본다.
_llm_call_counter: ContextVar[_LlmCallCounter | None] = ContextVar(
    "essence_llm_call_counter", default=None
)


def _provider_token(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@asynccontextmanager
async def metered_llm_calls(
    hospital_id: uuid.UUID | str | None = None,
    *,
    workflow: str = "essence_engine",
    run_id: uuid.UUID | str | None = None,
    item_id: uuid.UUID | str | None = None,
    attempt_id: str | None = None,
) -> AsyncIterator[_LlmCallCounter]:
    """블록 안에서 나간 OpenRouter 호출을 content 예산의 '실제 호출'로 기록한다.

    운영 기준 처리(근거 추출·철학 합성)는 AE가 버튼으로 돌리는 유료 호출인데 종전에는
    예약도 계수도 없어 비용 화면에 전혀 잡히지 않았다.
    """
    counter = _LlmCallCounter()
    token = _llm_call_counter.set(counter)
    try:
        yield counter
    finally:
        _llm_call_counter.reset(token)
        if counter.count:
            from app.services import cost_guard, provider_usage

            await cost_guard.record_provider_call("content", count=counter.count)
            for event in counter.events:
                logical_call_id = (
                    f"{attempt_id}:call:{event['logical_call']}"
                    if attempt_id
                    else None
                )
                idempotency_key = (
                    f"essence:{logical_call_id}:http:{event['http_attempt']}"
                    if logical_call_id
                    else None
                )
                await provider_usage.record_attempt(
                    provider="openrouter",
                    model=event.get("model") or settings.CLAUDE_MODEL_FAST,
                    workflow=workflow,
                    cost_category="content",
                    hospital_id=hospital_id,
                    run_id=run_id,
                    item_id=item_id,
                    attempt_id=attempt_id,
                    logical_call_id=logical_call_id,
                    http_attempt=event["http_attempt"],
                    provider_request_id=event["provider_request_id"],
                    usage=event["usage"],
                    usage_known=event["usage_known"],
                    idempotency_key=idempotency_key,
                )


def _call_llm_json(
    system: str,
    user_message: str,
    *,
    max_tokens: int,
    output_schema: dict[str, Any] | None = None,
    attempts: int = 3,
    timeout_seconds: float = 60.0,
    model: str | None = None,
) -> dict[str, Any]:
    """Call one model (default: the fast model) with bounded retries and schema-constrained JSON."""
    resolved_model = model or settings.CLAUDE_MODEL_FAST
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    counter = _llm_call_counter.get()
    logical_call = counter.begin_logical_call() if counter is not None else 0
    for attempt in Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(min=1, max=4),
        # 결정적 4xx(잘못된 요청·인증·권한·모델 오타)는 재시도해도 같은 실패라
        # 유료 호출만 3배로 늘린다. 타임아웃·5xx·429·파서 실패만 재시도한다.
        retry=retry_if_exception(is_retryable_llm_error),
        reraise=True,
    ):
        with attempt:
            client = _llm_client()
            if client is None:  # pragma: no cover — llm_enabled() 가드 후에만 호출됨
                raise RuntimeError("OPENROUTER_API_KEY가 설정되어 있지 않습니다.")
            event = (
                counter.tick(
                    logical_call=logical_call,
                    http_attempt=attempt.retry_state.attempt_number,
                )
                if counter is not None
                else None
            )
            if event is not None:
                # 사용량 원장에 실제로 부른 모델을 남긴다(호출마다 모델이 다를 수 있다).
                event["model"] = resolved_model
            request: dict[str, Any] = {
                "model": resolved_model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
            }
            if output_schema is not None:
                request["response_format"] = openrouter.json_schema_format(
                    output_schema, name="structured_output"
                )
            try:
                response = client.chat.completions.create(
                    **request, timeout=timeout_seconds
                )
            except Exception:
                # The event was appended before the HTTP call, so failed provider
                # attempts remain visible with usage_known=False.
                raise
            if counter is not None and event is not None:
                counter.record_response(event, response)
            stop_reason = llm_structured_output.incomplete_reason(response)
            if stop_reason is not None:
                if stop_reason in {"length", "max_tokens"}:
                    # 잘림의 원인(추론 토큰 포함 여부·상한 대비 실제 출력)을 다음에 진단할 수 있게 남긴다.
                    logger.warning(
                        "essence_llm_output_truncated model=%s max_tokens=%s usage=%r",
                        resolved_model,
                        max_tokens,
                        getattr(response, "usage", None),
                    )
                raise ValueError(f"essence LLM incomplete structured output: {stop_reason}")
            raw = llm_structured_output.first_text(response)
            if not raw:
                raise ValueError("essence LLM returned no text JSON block")
            return _parse_json_object(raw)
    raise RuntimeError("essence LLM retry loop ended without a result")  # pragma: no cover


def source_processing_ranges(raw_text: str | None) -> tuple[tuple[int, int], ...]:
    """Return overlapping provider-call ranges whose union covers the document."""

    text_length = len(raw_text or "")
    if text_length == 0:
        return ()
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < text_length:
        end = min(start + _RAW_TEXT_FOR_LLM_LIMIT, text_length)
        ranges.append((start, end))
        if end == text_length:
            break
        start = end - _RAW_TEXT_CHUNK_OVERLAP
    return tuple(ranges)


def source_processing_coverage(raw_text: str | None) -> dict[str, Any]:
    """Serializable proof that extraction scheduled the complete source range."""

    ranges = source_processing_ranges(raw_text)
    return {
        "version": 2,
        "document_length": len(raw_text or ""),
        "ranges": [[start, end] for start, end in ranges],
        "complete": bool(ranges) and ranges[0][0] == 0 and ranges[-1][1] == len(raw_text or ""),
    }


def _llm_payloads_from_response(
    asset: HospitalSourceAsset,
    data: dict[str, Any],
    *,
    chunk_index: int,
    chunk_count: int,
    chunk_start: int,
    chunk_end: int,
) -> list[EvidenceNotePayload]:
    payloads: list[EvidenceNotePayload] = []
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
        metadata["extraction_coverage"] = {
            "version": 2,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "range_start": chunk_start,
            "range_end": chunk_end,
            "document_length": len(asset.raw_text or ""),
        }
        if note_type == EvidenceNoteType.CONFLICT and _is_differential_diagnosis_caution(excerpt):
            note_type = EvidenceNoteType.TREATMENT_SIGNAL
            metadata["classification_correction"] = "differential_diagnosis_not_source_conflict"
        violations = check_forbidden(excerpt)
        if violations:
            note_type = EvidenceNoteType.RISK_SIGNAL
            metadata.setdefault("violations", violations)

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
    return payloads


def _process_source_asset_llm(asset: HospitalSourceAsset) -> list[EvidenceNotePayload]:
    """Extract every raw-text range and de-duplicate notes from overlap."""

    raw_text = asset.raw_text or ""
    ranges = source_processing_ranges(raw_text)
    operator_note = (asset.operator_note or "").strip()
    payloads: list[EvidenceNotePayload] = []
    seen: set[tuple[str, str]] = set()
    for chunk_index, (chunk_start, chunk_end) in enumerate(ranges):
        chunk = raw_text[chunk_start:chunk_end]
        user_message = (
            f"[원문 raw_text {chunk_start}:{chunk_end} / {len(raw_text)}]\n{chunk}\n\n"
            + (
                f"[운영자 메모 operator_note]\n{operator_note}\n\n"
                if operator_note and chunk_index == 0
                else ""
            )
            + "위 범위의 원문에서만 근거 노트를 추출해 JSON으로 출력하세요."
        )
        data = _call_llm_json(
            _SOURCE_PROCESSING_SYSTEM,
            user_message,
            max_tokens=3000,
            output_schema=_SOURCE_PROCESSING_OUTPUT_SCHEMA,
            # 근거 노트는 Essence 승인의 바탕이다 — 원문에 없는 주장을 노트로 만들지 않게
            # 판단 모델을 쓴다.
            model=settings.CLAUDE_MODEL_REVIEW,
        )
        for payload in _llm_payloads_from_response(
            asset,
            data,
            chunk_index=chunk_index,
            chunk_count=len(ranges),
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        ):
            key = (payload.note_type.value, payload.source_excerpt)
            if key in seen:
                continue
            seen.add(key)
            payloads.append(payload)
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


SYNTHESIS_MODE_LLM = "LLM"
SYNTHESIS_MODE_DETERMINISTIC = "DETERMINISTIC"
#: 합성 결과에 함께 실려 오는 서버 전용 키. 저장 전에 반드시 빠진다(`strip_private_synthesis_keys`).
MUST_USE_EVIDENCE_KEY = "_must_use_evidence"


def strip_private_synthesis_keys(payload: dict[str, Any]) -> dict[str, Any]:
    for key in [key for key in payload if str(key).startswith("_")]:
        payload.pop(key, None)
    return payload


def synthesize_philosophy(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """저장된 근거 노트만으로 콘텐츠 철학 초안을 만든다.

    OPENROUTER_API_KEY가 있으면 LLM으로 합성하고, 없으면 deterministic 폴백을 쓴다.
    LLM 합성이 실패하거나 grounding 검증을 통과하지 못하면 deterministic 폴백으로 떨어진다.
    """
    payload, _mode = synthesize_philosophy_with_mode(
        hospital, sources, notes, operator_note, use_llm=use_llm
    )
    return strip_private_synthesis_keys(payload)


def synthesize_philosophy_with_mode(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
    *,
    use_llm: bool = True,
) -> tuple[dict[str, Any], str]:
    """`synthesize_philosophy`와 같되 어느 경로(LLM·결정적 폴백)로 만들었는지 함께 돌려준다.

    자동 요약(D33)은 이 값으로 "폴백 판으로 좋은 판을 덮지 않는다"를 지킨다. 결과에는 서버 전용
    키(`_must_use_evidence` — 필수 문구 항목별 근거 노트 id)가 남아 있으며 자동 정리
    (`prune_automatic_candidate`)가 읽고 지운다.
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
            payload = apply_mandatory_safety_policy(
                _synthesize_philosophy_llm(hospital, sources, notes, operator_note)
            )
            if (
                payload is not None
                and payload.get("evidence_map")  # grounded 필드가 최소 1개는 있어야 함
                and not validate_philosophy_grounding(payload, notes)
            ):
                return payload, SYNTHESIS_MODE_LLM
            logger.info("essence LLM synthesis ungrounded or empty; using deterministic fallback")
        except Exception as exc:  # noqa: BLE001 — LLM 실패 시 폴백으로 계속
            logger.warning("essence LLM synthesis failed (%s); using deterministic fallback", exc)

    # 결정적 폴백도 출처 우선순위(설문 → 병원 정보 → 홈페이지 → 블로그)로 노트를 고른다(D34).
    return (
        apply_mandatory_safety_policy(
            _synthesize_philosophy_deterministic(
                hospital, sources, order_notes_for_synthesis(notes, sources), operator_note
            )
        ),
        SYNTHESIS_MODE_DETERMINISTIC,
    )


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
5. 같은 사실에 대해 서로 양립할 수 없는 근거는 conflict_notes에 남기고 임의로 결론내리지 않습니다.
   서로 다른 질환의 증상이 비슷하거나 함께 나타날 수 있다는 감별진단·검사 안내는
   자료 간 모순이 아니므로 conflict_notes로 만들지 않습니다.
6. 제목만 있거나 문장이 중간에 잘린 발췌는 긍정 운영 기준에 사용하지 않고
   unsupported_gap으로 남깁니다. 모든 note를 억지로 출력에 포함하지 않습니다.
7. 핵심 운영 기준만 최대 14개 entry로 압축합니다. 모든 근거 노트를 억지로 반영하지 말고,
   각 entry의 patient_language와 cautions는 각각 최대 2개, evidence_note_ids는 그 문장을
   직접 뒷받침하는 최소 개수만 사용합니다.
8. 각 노트의 src는 자료 종류입니다. INTERVIEW는 원장 인터뷰 설문, PROFILE_FACTS는 병원 정보
   화면의 값(태그 [마케터 입력]은 마케터가 확인한 값), HOMEPAGE·NAVER_BLOG는 공식 채널입니다.
   doctor_voice·positioning_statement·patient_promise·treatment_narrative는 src=INTERVIEW 근거를
   최우선으로 씁니다. 같은 주제에서 INTERVIEW와 다른 자료가 다르면 INTERVIEW를 따르고
   conflict_note로 남깁니다. INTERVIEW가 없으면 PROFILE_FACTS의 [마케터 입력] → HOMEPAGE →
   NAVER_BLOG 순으로 따릅니다.
9. 주소·전화번호·진료시간·휴진일 같은 운영 사실은 어떤 필드에도 쓰지 않습니다.
10. must_use_message는 최대 3개입니다. src=INTERVIEW 발췌에 있는 원장의 문장을 한 글자도 바꾸지
    않고 그대로 옮깁니다(질문 문장은 쓰지 않습니다). INTERVIEW 근거가 없거나 쓸 만한 원장 문장이
    없을 때만 근거 노트에 묶인 짧은 문장으로 씁니다.

출력은 JSON 객체 하나만, 코드블록/설명 없이 entries 배열로 반환합니다.
모든 entry는 아래 키를 전부 가지며 사용하지 않는 문자열/배열은 빈 값으로 둡니다.
{
  "entries": [{
    "kind": "positioning_statement | doctor_voice | patient_promise | content_principle | tone_guideline | must_use_message | avoid_message | treatment_narrative | local_region_term | local_patient_context | medical_ad_risk_rule | unsupported_gap | conflict_note | synthesis_note",
    "text": "주요 문장 또는 treatment/unsupported field",
    "detail": "unsupported reason 또는 빈 문자열",
    "patient_language": ["treatment_narrative에서만 사용"],
    "cautions": ["treatment_narrative에서만 사용"],
    "evidence_note_ids": ["..."]
  }]
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

_SYNTHESIS_ENTRY_KINDS = (
    "positioning_statement",
    "doctor_voice",
    "patient_promise",
    "content_principle",
    "tone_guideline",
    "must_use_message",
    "avoid_message",
    "treatment_narrative",
    "local_region_term",
    "local_patient_context",
    "medical_ad_risk_rule",
    "unsupported_gap",
    "conflict_note",
    "synthesis_note",
)
_SYNTHESIS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(_SYNTHESIS_ENTRY_KINDS)},
                    "text": {"type": "string"},
                    "detail": {"type": "string"},
                    "patient_language": {"type": "array", "items": {"type": "string"}},
                    "cautions": {"type": "array", "items": {"type": "string"}},
                    "evidence_note_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "kind",
                    "text",
                    "detail",
                    "patient_language",
                    "cautions",
                    "evidence_note_ids",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _expand_synthesis_entries(data: dict[str, Any]) -> dict[str, Any]:
    """Convert the compact structured-output grammar into the legacy payload shape."""

    expanded: dict[str, Any] = {
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": [],
        "avoid_messages": [],
        "treatment_narratives": [],
        "local_context": {
            "region_terms": [],
            "local_patient_context": [],
            "evidence_note_ids": [],
        },
        "medical_ad_risk_rules": [],
        "unsupported_gaps": [],
        "conflict_notes": [],
        "synthesis_notes": "",
    }
    list_fields = {
        "content_principle": "content_principles",
        "tone_guideline": "tone_guidelines",
        "must_use_message": "must_use_messages",
        "avoid_message": "avoid_messages",
        "medical_ad_risk_rule": "medical_ad_risk_rules",
    }
    for entry in _as_list(data.get("entries")):
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        text = str(entry.get("text") or "").strip()
        detail = str(entry.get("detail") or "").strip()
        ids = _string_list(entry.get("evidence_note_ids"))
        structured_text = {"text": text, "evidence_note_ids": ids}

        if kind in _TEXT_FIELDS_SINGLE and kind not in expanded and text:
            expanded[kind] = structured_text
        elif kind in list_fields and text:
            expanded[list_fields[kind]].append(structured_text)
        elif kind == "treatment_narrative" and text:
            expanded["treatment_narratives"].append(
                {
                    "treatment": text,
                    "patient_language": _string_list(entry.get("patient_language")),
                    "cautions": _string_list(entry.get("cautions")),
                    "evidence_note_ids": ids,
                }
            )
        elif kind in {"local_region_term", "local_patient_context"} and text:
            local = expanded["local_context"]
            local[
                "region_terms" if kind == "local_region_term" else "local_patient_context"
            ].append(text)
            local["evidence_note_ids"].extend(ids)
        elif kind == "unsupported_gap" and (text or detail):
            expanded["unsupported_gaps"].append({"field": text, "reason": detail})
        elif kind == "conflict_note" and text:
            expanded["conflict_notes"].append(structured_text)
        elif kind == "synthesis_note" and text:
            expanded["synthesis_notes"] = text

    local_ids = expanded["local_context"]["evidence_note_ids"]
    expanded["local_context"]["evidence_note_ids"] = list(dict.fromkeys(local_ids))
    return expanded


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
    kinds = source_kinds_by_id(sources)
    notes_block = "\n".join(
        f"- id={_note_id(note)} | src={_note_source_kind(note, kinds)} | "
        f"type={_note_type_value(note)} | "
        f"excerpt={_short(note.source_excerpt, _note_excerpt_limit(note, kinds))}"
        for note in order_notes_for_synthesis(notes, sources)
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
    data = _call_llm_json(
        _SYNTHESIS_SYSTEM,
        user_message,
        max_tokens=5000,
        output_schema=_SYNTHESIS_OUTPUT_SCHEMA,
        # A safe deterministic synthesis fallback exists. Preserve the task
        # budget for the two independent safety reviews instead of spending
        # three minutes retrying a long synthesis response.
        attempts=1,
        timeout_seconds=90.0,
    )
    if isinstance(data.get("entries"), list):
        data = _expand_synthesis_entries(data)

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
        if field_name == "must_use_messages":
            payload[MUST_USE_EVIDENCE_KEY] = [
                {"text": text, "evidence_note_ids": list(item_ids)} for text, item_ids in items
            ]

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
                or ["치료 결과를 단정하거나 보장하지 않습니다."],
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



# ── 자료 종류(src) — 합성 프롬프트·자동 정리 공용 ─────────────────────────────
SOURCE_KIND_PROFILE_FACTS = "PROFILE_FACTS"
#: 서술 우선순위(D34): 설문 > 마케터 입력(병원 정보) > 홈페이지 > 블로그 > 그 밖.
_NARRATIVE_SOURCE_RANK = {
    SourceType.INTERVIEW.value: 0,
    SOURCE_KIND_PROFILE_FACTS: 1,
    SourceType.HOMEPAGE.value: 2,
    SourceType.NAVER_BLOG.value: 3,
}
_INTERVIEW_EXCERPT_LIMIT = 400
_DEFAULT_EXCERPT_LIMIT = 160


def source_kind(source: Any) -> str:
    """노트·합성 메타데이터가 쓰는 자료 종류. 병원 정보(자동) 자료는 PROFILE_FACTS다."""

    if is_profile_facts_source(source):
        return SOURCE_KIND_PROFILE_FACTS
    value = getattr(source, "source_type", None)
    return str(getattr(value, "value", value) or "OTHER")


def source_kinds_by_id(sources: Iterable[Any]) -> dict[str, str]:
    return {str(source.id): source_kind(source) for source in sources}


def _note_source_kind(note: Any, kinds: dict[str, str]) -> str:
    return kinds.get(str(getattr(note, "source_asset_id", "")), "OTHER")


def _note_excerpt_limit(note: Any, kinds: dict[str, str]) -> int:
    if _note_source_kind(note, kinds) == SourceType.INTERVIEW.value:
        return _INTERVIEW_EXCERPT_LIMIT
    return _DEFAULT_EXCERPT_LIMIT


def narrative_source_rank(kind: str) -> int:
    return _NARRATIVE_SOURCE_RANK.get(kind, len(_NARRATIVE_SOURCE_RANK))


def _is_marketer_profile_note(note: Any) -> bool:
    metadata = getattr(note, "note_metadata", None) or {}
    return metadata.get("tag") == "마케터 입력"


def order_notes_for_synthesis(notes: list[Any], sources: Iterable[Any]) -> list[Any]:
    """설문 → 병원 정보(마케터 입력 줄 먼저) → 홈페이지 → 블로그 → 그 밖 순서."""

    kinds = source_kinds_by_id(sources)
    indexed = list(enumerate(notes))
    indexed.sort(
        key=lambda pair: (
            narrative_source_rank(_note_source_kind(pair[1], kinds)),
            0 if _is_marketer_profile_note(pair[1]) else 1,
            pair[0],
        )
    )
    return [note for _index, note in indexed]


_PROFILE_FACT_PREFIX = re.compile(r"^\[[^\]]+\] ")


def _note_text(note: Any) -> str:
    """결정적 폴백이 보여 줄 발췌. 병원 정보(자동) 줄의 출처 태그는 떼어 낸다."""

    excerpt = str(getattr(note, "source_excerpt", "") or "")
    metadata = getattr(note, "note_metadata", None) or {}
    if metadata.get("system_kind") == SOURCE_KIND_PROFILE_FACTS:
        return _PROFILE_FACT_PREFIX.sub("", excerpt)
    return excerpt


def _must_use_text_from_note(note: Any) -> str | None:
    metadata = getattr(note, "note_metadata", None) or {}
    if metadata.get("system_kind") == SOURCE_KIND_PROFILE_FACTS:
        if metadata.get("label") != "진료 철학":
            return None
        text = _note_text(note).split(":", 1)[-1].strip()
        return _short(text, 160) if text else None
    return _short(_note_text(note), 160)


def _synthesize_philosophy_deterministic(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
) -> dict[str, Any]:
    """근거 노트에서 규칙 기반으로 철학 초안을 합성 — LLM 키가 없는 폴백."""
    if notes and all(
        (getattr(note, "note_metadata", None) or {}).get("system_kind") == SOURCE_KIND_PROFILE_FACTS
        for note in notes
    ):
        return _synthesize_profile_only(hospital, sources, notes)
    # 노트는 출처 우선순위 순서로 온다(`order_notes_for_synthesis`) — 종류가 순위를 덮지 않게
    # 원래 순서대로 고른다.
    grouped = notes

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
            f"자료에서 확인된 핵심 메시지: {_short(_note_text(key_notes[0]), 140)}"
        )
        evidence_map["positioning_statement"] = [_note_id(key_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "positioning_statement", "reason": "핵심 메시지 근거 note가 없습니다."}
        )

    doctor_voice = None
    if tone_notes:
        doctor_voice = f"자료 표현 기준 문체: {_short(_note_text(tone_notes[0]), 140)}"
        evidence_map["doctor_voice"] = [_note_id(tone_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "doctor_voice", "reason": "문체/톤 근거 note가 없습니다."}
        )

    patient_promise = None
    if promise_notes:
        patient_promise = f"환자에게 말할 수 있는 약속은 이 근거 범위로 제한: {_short(_note_text(promise_notes[0]), 140)}"
        evidence_map["patient_promise"] = [_note_id(promise_notes[0])]
    else:
        unsupported_gaps.append(
            {"field": "patient_promise", "reason": "환자 약속 근거 note가 없습니다."}
        )

    content_principles = [
        f"근거 문장을 벗어나지 않고 설명합니다: {_short(_note_text(note), 120)}"
        for note in key_notes[:3]
    ]
    if content_principles:
        evidence_map["content_principles"] = [_note_id(note) for note in key_notes[:3]]
    else:
        unsupported_gaps.append(
            {"field": "content_principles", "reason": "콘텐츠 원칙으로 전환할 근거가 없습니다."}
        )

    tone_guidelines = [
        f"원문 톤을 유지합니다: {_short(_note_text(note), 120)}" for note in tone_notes
    ]
    if tone_guidelines:
        evidence_map["tone_guidelines"] = [_note_id(note) for note in tone_notes]

    # 병원 정보(자동) 줄은 "원장: 홍길동" 같은 사실 표기라 필수 문구가 될 수 없다 — 진료 철학 줄만
    # 값을 문구로 쓴다.
    must_use_items = [
        (text, note)
        for note in key_notes
        if (text := _must_use_text_from_note(note))
    ]
    must_use_messages = [text for text, _note in must_use_items]
    if must_use_messages:
        evidence_map["must_use_messages"] = [_note_id(note) for _text, note in must_use_items]

    avoid_messages = [
        f"검수 필요 표현 또는 약속: {_short(_note_text(note), 120)}" for note in risk_notes
    ]
    if avoid_messages:
        evidence_map["avoid_messages"] = [_note_id(note) for note in risk_notes]

    treatment_narratives = []
    for note in treatment_notes:
        treatment_narratives.append(
            {
                "treatment": (note.note_metadata or {}).get("treatment") or "자료 기반 진료 항목",
                "angle": _short(_note_text(note), 140),
                "explanation_style": "근거 발췌에 포함된 표현만 사용합니다.",
                "cautions": ["치료 결과를 단정하거나 보장하지 않습니다."],
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
            _short(_note_text(note), 120) for note in local_notes
        ]
        local_context["evidence_note_ids"] = [_note_id(note) for note in local_notes]
        evidence_map["local_context"] = [_note_id(note) for note in local_notes]

    medical_ad_risk_rules = []
    for note in risk_notes:
        violations = (note.note_metadata or {}).get("violations") or []
        if violations:
            medical_ad_risk_rules.append(
                f"{', '.join(violations)} 표현은 근거와 별도 심의 없이 사용하지 않습니다: "
                f"{_short(_note_text(note), 120)}"
            )
        else:
            medical_ad_risk_rules.append(f"리스크 표현 검수: {_short(_note_text(note), 120)}")
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
        {"text": _short(_note_text(note), 160), "evidence_note_ids": [_note_id(note)]}
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
        MUST_USE_EVIDENCE_KEY: [
            {"text": text, "evidence_note_ids": [_note_id(note)]}
            for text, note in must_use_items
        ],
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


def _profile_value(note: Any) -> str:
    return _note_text(note).split(":", 1)[-1].strip()


def _synthesize_profile_only(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
) -> dict[str, Any]:
    """병원 정보(자동) 자료만 있을 때의 최소 자동 판 — 병원명·전문 과목·진료 항목·진료 철학.

    다른 자료가 모두 오염돼 빠졌거나 아직 없어도 생성이 기다리지 않게 하는 결정적 판이다(D33).
    문장은 노트 값을 그대로 잇기만 하므로 근거 대조를 통과한다.
    """

    by_label: dict[str, list[HospitalSourceEvidenceNote]] = {}
    for note in notes:
        if _note_type_value(note) == EvidenceNoteType.RISK_SIGNAL.value:
            continue
        label = str((note.note_metadata or {}).get("label") or "")
        by_label.setdefault(label, []).append(note)
    evidence_map: dict[str, list[str]] = {}
    unsupported_gaps: list[dict[str, str]] = []

    name_notes = by_label.get("병원명", [])
    specialty_notes = by_label.get("전문 과목", [])
    positioning_statement = None
    if name_notes:
        name = _profile_value(name_notes[0])
        specialty = _profile_value(specialty_notes[0]) if specialty_notes else ""
        positioning_statement = (
            f"{name}은 {specialty} 진료를 안내합니다." if specialty else f"{name}의 진료를 안내합니다."
        )
        evidence_map["positioning_statement"] = [
            _note_id(note) for note in [*name_notes[:1], *specialty_notes[:1]]
        ]
    else:
        unsupported_gaps.append(
            {"field": "positioning_statement", "reason": "병원명 근거 note가 없습니다."}
        )

    philosophy_notes = by_label.get("진료 철학", [])
    must_use_messages = [
        text for note in philosophy_notes[:1] if (text := _must_use_text_from_note(note))
    ]
    if must_use_messages:
        evidence_map["must_use_messages"] = [_note_id(philosophy_notes[0])]

    treatment_narratives = []
    for note in by_label.get("진료 항목", [])[:8]:
        value = _profile_value(note)
        name, _sep, description = value.partition(" — ")
        treatment_narratives.append(
            {
                "treatment": name.strip(),
                "angle": (description or name).strip(),
                "explanation_style": "근거 발췌에 포함된 표현만 사용합니다.",
                "cautions": ["치료 결과를 단정하거나 보장하지 않습니다."],
                "evidence_note_ids": [_note_id(note)],
            }
        )
    if treatment_narratives:
        evidence_map["treatment_narratives"] = [
            item["evidence_note_ids"][0] for item in treatment_narratives
        ]

    local_context: dict[str, Any] = {
        "region_terms": [],
        "local_patient_context": [],
        "avoid_region_stuffing": True,
    }
    region_notes = by_label.get("지역", [])
    if region_notes:
        local_context["region_terms"] = [
            term.strip() for term in _profile_value(region_notes[0]).split(",") if term.strip()
        ]
        local_context["evidence_note_ids"] = [_note_id(region_notes[0])]
        evidence_map["local_context"] = [_note_id(region_notes[0])]

    return {
        "positioning_statement": positioning_statement,
        "doctor_voice": None,
        "patient_promise": None,
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": must_use_messages,
        MUST_USE_EVIDENCE_KEY: [
            {"text": text, "evidence_note_ids": [_note_id(philosophy_notes[0])]}
            for text in must_use_messages
        ],
        "avoid_messages": [],
        "treatment_narratives": treatment_narratives,
        "local_context": local_context,
        "medical_ad_risk_rules": [],
        "evidence_map": evidence_map,
        "source_asset_ids": [str(source.id) for source in sources],
        "unsupported_gaps": unsupported_gaps,
        "conflict_notes": [],
        "synthesis_notes": (
            "Minimal deterministic summary from hospital profile facts only "
            "(name, specialties, treatments, philosophy)."
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

        if field_name == "avoid_messages":
            source_backed = [item for item in value if item not in MANDATORY_AVOID_MESSAGES]
            if not source_backed:
                continue
        if field_name == "medical_ad_risk_rules":
            source_backed = [
                item for item in value if item not in MANDATORY_MEDICAL_AD_RISK_RULES
            ]
            if not source_backed:
                continue

        mapped_ids = _flatten_ids(evidence_map.get(field_name))
        if not mapped_ids:
            errors.append(f"{field_name} 필드에 evidence_map이 없습니다.")
            continue

    # evidence_map에 남은 **모든** 참조를 검사한다. 운영 화면은 필드 목록과 무관하게
    # evidence_map 항목을 전부 그리므로, 위 목록만 검사하면 화면에서는 "근거 노트를 찾지
    # 못했습니다"로 보이던 연결이 승인만 통과한다 — 자료를 다시 처리해 노트 id가 바뀐
    # 초안이 정확히 그 경우다. 승인은 운영자가 확인할 수 있었던 것만 통과해야 한다.
    if isinstance(evidence_map, dict):
        for field_name, raw_ids in evidence_map.items():
            unknown = [
                note_id for note_id in _flatten_ids(raw_ids) if note_id not in valid_note_ids
            ]
            if unknown:
                errors.append(
                    f"{field_name} 필드가 존재하지 않는 근거 노트를 참조합니다: "
                    f"{', '.join(unknown)}"
                )

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


# ── 자동 요약 정리(Phase 4 D33) ─────────────────────────────────────────────
# 자동 요약은 승인 단계가 없다. 그래서 지금까지 "보류 사유"였던 것을 모두 결정적으로 **버린다**:
# 근거 노트에 묶이지 않는 주장, 의료광고 금지 표현, 차단·오류 페이지 잔재, 운영 사실(E5). 버린 것은
# 합성 메타데이터(`synthesis_metadata.dropped`)에만 남는다 — 사고·Slack 없음.
AUTOMATIC_MUST_USE_LIMIT = 3
AUTOMATIC_DROPPED_LIMIT = 50
MUST_USE_MODE_SURVEY = "SURVEY_VERBATIM"
MUST_USE_MODE_SOURCE = "SOURCE_VERBATIM"
MUST_USE_MODE_NONE = "NONE"
DROP_UNGROUNDED = "UNGROUNDED"
DROP_FORBIDDEN = "FORBIDDEN_EXPRESSION"
DROP_ERROR_PAGE = "ERROR_PAGE_RESIDUE"
DROP_OPERATIONAL_FACT = "OPERATIONAL_FACT"
DROP_MUST_USE_LIMIT = "MUST_USE_LIMIT"
DROP_MUST_USE_NOT_SOURCE_VERBATIM = "MUST_USE_NOT_SOURCE_VERBATIM"
DROP_MUST_USE_SHAPE = "MUST_USE_NOT_SENTENCE"
DROP_UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
DROP_CONFLICT_LOWER_PRIORITY = "CONFLICT_LOWER_PRIORITY"
DROP_CONFLICT_UNDECIDED = "CONFLICT_UNDECIDED"
CONFLICT_RESOLUTION_RULE = "D34_NARRATIVE_PRIORITY"
CONFLICT_FACT_RESOLUTION_RULE = "D34_FACT_PRIORITY"
_GENERIC_TREATMENT_LABEL = "자료 기반 진료 항목"

_PRUNE_POSITIVE_SINGLE = ("positioning_statement", "doctor_voice", "patient_promise")
_PRUNE_POSITIVE_LIST = ("content_principles", "tone_guidelines", "must_use_messages")
_PRUNE_POLICY_LIST = ("avoid_messages", "medical_ad_risk_rules")
_PRUNE_GROUNDED_FIELDS = (
    *_PRUNE_POSITIVE_SINGLE,
    *_PRUNE_POSITIVE_LIST,
    *_PRUNE_POLICY_LIST,
    "treatment_narratives",
    "local_context",
)
# 주소·전화·진료시간 같은 운영 사실은 Essence에 싣지 않는다(E5) — 생성은 그 값을 병원 행에서 읽는다.
_OPERATIONAL_FACT_PATTERNS = (
    re.compile(r"(?<![0-9])[0-2]?[0-9]\s*:\s*[0-5][0-9](?![0-9])"),
    re.compile(r"(?:오전|오후)\s*[0-9]{1,2}\s*시"),
    re.compile(r"(?<![0-9])0[0-9]{1,2}\s*[-.)]\s*[0-9]{3,4}\s*[-.]\s*[0-9]{4}(?![0-9])"),
    re.compile(r"(?<![0-9])1[5-9][0-9]{2}\s*-\s*[0-9]{4}(?![0-9])"),
    re.compile(r"진료\s*시간|휴진|점심\s*시간"),
)
_PROFILE_FACT_LINE = re.compile(r"^\[[^\]]+\]\s")
_PROFILE_FACT_LABEL_FORM = re.compile(
    r"^(?:원장|원장 약력|전문 과목|진료 항목|지역|핵심 키워드)\s*:"
)
_QUOTE_CHARS = "\"'“”‘’「」『』"
_MIN_MUST_USE_CHARS = 8


def contains_operational_fact(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _OPERATIONAL_FACT_PATTERNS)


def _collapse(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


@dataclass
class AutomaticPruneReport:
    dropped: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    must_use: dict[str, Any] = field(default_factory=dict)
    public_about: list[str] = field(default_factory=list)
    dropped_total: int = 0

    def add(self, field_name: str, text: Any, reason: str) -> None:
        self.dropped_total += 1
        if len(self.dropped) < AUTOMATIC_DROPPED_LIMIT:
            self.dropped.append(
                {"field": field_name, "text": _short(_collapse(text), 80), "reason": reason}
            )


def _offending_reason(text: str, *, positive: bool) -> str | None:
    if looks_like_error_page_text(text):
        return DROP_ERROR_PAGE
    if positive and check_forbidden(text):
        return DROP_FORBIDDEN
    if positive and contains_operational_fact(text):
        return DROP_OPERATIONAL_FACT
    return None


def _must_use_shape_ok(text: str) -> bool:
    return (
        len(text) >= _MIN_MUST_USE_CHARS
        and not text.endswith("?")
        and not is_not_doctor_statement(text)
        and not _PROFILE_FACT_LINE.match(text)
        and not _PROFILE_FACT_LABEL_FORM.match(text)
    )


def _note_sentences(note: Any) -> list[str]:
    """노트 발췌의 문장들(원문 그대로). 병원 정보(자동) 줄은 값 부분만 문장으로 본다."""

    text = _note_text(note)
    metadata = getattr(note, "note_metadata", None) or {}
    if metadata.get("system_kind") == SOURCE_KIND_PROFILE_FACTS:
        if metadata.get("label") != "진료 철학":
            return []
        text = text.split(":", 1)[-1]
    return [normalize_sentence(sentence) for sentence in split_sentences(text)]


def _clean_source_sentence(sentence: str) -> bool:
    """필수 문구·공개 소개로 옮길 수 있는 원문 문장인가 — 의료 필터와 의료 주장 어휘 모두 통과."""

    return (
        _MIN_MUST_USE_CHARS <= len(sentence) <= _DEFAULT_EXCERPT_LIMIT
        and _must_use_shape_ok(sentence)
        and _offending_reason(sentence, positive=True) is None
        and not is_medical_claim(sentence)
    )


def _excerpts(notes_by_id: dict[str, Any], ids: Iterable[str]) -> list[str]:
    return [
        str(getattr(notes_by_id[note_id], "source_excerpt", "") or "")
        for note_id in ids
        if note_id in notes_by_id
    ]


def _field_ids(result: dict[str, Any], field_name: str, valid: set[str]) -> list[str]:
    raw = result.get("evidence_map") if isinstance(result.get("evidence_map"), dict) else {}
    return [note_id for note_id in _flatten_ids(raw.get(field_name)) if note_id in valid]


def _sentence_kept(
    sentence: str, support: list[str], allowed: list[str], *, require_general: bool
) -> bool:
    """의료 주장 어휘가 든 문장은 근거 노트에 글자 그대로 있을 때만 남는다(fail-closed).

    그 밖의 문장은 연결된 노트 본문으로 뒷받침돼야 한다(문체 묘사는 의료 주장일 때만 대조).
    """

    body = without_template_prefix(sentence)
    if is_medical_claim(body):
        return is_verbatim_in(body, allowed)
    return claim_is_supported(body, support, require_general=require_general)


def _text_kept(text: str, support: list[str], allowed: list[str], *, require_general: bool) -> bool:
    """여러 문장이면 문장마다 본다 — 하나라도 남지 못하면 그 항목을 통째로 버린다."""

    return all(
        _sentence_kept(sentence, support, allowed, require_general=require_general)
        for sentence in split_sentences(text) or [text]
    )


def _grounded_text(
    text: str,
    support: list[str],
    allowed: list[str],
    *,
    require_general: bool,
) -> tuple[str | None, list[str]]:
    """문장마다 근거 대조해 뒷받침되는 문장만 남긴다. (남은 글, 버린 문장들)."""

    kept: list[str] = []
    dropped: list[str] = []
    for sentence in split_sentences(text) or [text]:
        if _sentence_kept(sentence, support, allowed, require_general=require_general):
            kept.append(sentence)
        else:
            dropped.append(sentence)
    return (" ".join(kept) if kept else None), dropped


# 사실 충돌 우선순위(D34): 마케터 입력 > 네이버 > 설문 > 홈페이지 > 블로그.
_FACT_TAG_RANK = {
    "마케터 입력": 0.0,
    "병원 정보": 0.5,
    "자동 수집·네이버": 1.0,
    "자동 수집·설문": 2.0,
    "자동 수집·홈페이지": 3.0,
    "자동 수집·블로그": 4.0,
}
# 서술(목소리·철학·진료 서술) 우선순위: 설문 > 마케터 입력 > 홈페이지 > 블로그 > 네이버.
_NARRATIVE_TAG_RANK = {
    "자동 수집·설문": 0.0,
    "마케터 입력": 1.0,
    "병원 정보": 1.5,
    "자동 수집·홈페이지": 2.0,
    "자동 수집·블로그": 3.0,
    "자동 수집·네이버": 4.5,
}
_FACT_KIND_RANK = {
    SourceType.INTERVIEW.value: 2.0,
    SourceType.HOMEPAGE.value: 3.0,
    SourceType.NAVER_BLOG.value: 4.0,
}
_NARRATIVE_KIND_RANK = {
    SourceType.INTERVIEW.value: 0.0,
    SourceType.HOMEPAGE.value: 2.0,
    SourceType.NAVER_BLOG.value: 3.0,
}


def _is_fact_conflict(notes: list[Any], kinds: dict[str, str]) -> bool:
    for note in notes:
        metadata = getattr(note, "note_metadata", None) or {}
        if _note_type_value(note) == EvidenceNoteType.PROOF_POINT.value:
            return True
        if (
            _note_source_kind(note, kinds) == SOURCE_KIND_PROFILE_FACTS
            and metadata.get("label") != "진료 철학"
        ):
            return True
    return False


def _priority_rank(note: Any, kinds: dict[str, str], *, fact: bool) -> float:
    kind = _note_source_kind(note, kinds)
    if kind == SOURCE_KIND_PROFILE_FACTS:
        tag = str((getattr(note, "note_metadata", None) or {}).get("tag") or "병원 정보")
        table = _FACT_TAG_RANK if fact else _NARRATIVE_TAG_RANK
        return table.get(tag, 1.5)
    return (_FACT_KIND_RANK if fact else _NARRATIVE_KIND_RANK).get(kind, 5.0)


def _best_support(text: str, linked: list[Any]) -> tuple[Any | None, float]:
    best, best_ratio = None, 0.0
    for note in linked:
        ratio = support_ratio(text, [str(getattr(note, "source_excerpt", "") or "")])
        if ratio > best_ratio:
            best, best_ratio = note, ratio
    return best, best_ratio


# 필수 문구는 원장의 목소리 노트(진료 철학·환자 약속·핵심 메시지)에서만 고른다. 검사·시술·약 설명
# (TREATMENT_SIGNAL 등)은 모든 글에 원문 그대로 들어가므로 고르지 않는다(2026-10-14 신기한속 v3).
_MUST_USE_SURVEY_NOTE_ORDER = (
    EvidenceNoteType.DOCTOR_PHILOSOPHY.value,
    EvidenceNoteType.PATIENT_PROMISE.value,
    EvidenceNoteType.KEY_MESSAGE.value,
)


def _select_must_use(
    result: dict[str, Any],
    notes: list[Any],
    sources: list[Any],
    report: AutomaticPruneReport,
    excluded_ids: set[str],
) -> list[str]:
    """필수 문구(Q2, fail-closed): 모델이 쓴 문장은 절대 쓰지 않는다.

    (a) 설문 원문에 글자 그대로 있는 원장 문장 최대 3개, 없으면 (b) 다른 근거 노트(홈페이지·블로그·
    병원 정보의 진료 철학)에 글자 그대로 있는 문장 최대 3개, 그것도 없으면 비운다. 어느 쪽이든
    의료광고 필터와 의료 주장 어휘(`essence_claims.MEDICAL_CLAIM_LEXICON`)를 통과해야 한다.
    충돌에서 진 노트는 고르지 않는다. 모델이 고른 원문 문장을 먼저 쓴다.
    """

    kinds = source_kinds_by_id(sources)
    proposed = [
        normalize_sentence(item.get("text") if isinstance(item, dict) else item)
        for item in result.get("must_use_messages") or []
    ]
    proposed = [text for text in proposed if text]
    usable_notes = [
        note
        for note in notes
        if _note_id(note) not in excluded_ids
        and _note_type_value(note) in _MUST_USE_SURVEY_NOTE_ORDER
    ]

    def pool(survey: bool) -> list[tuple[str, str]]:
        chosen = [
            note
            for note in usable_notes
            if (_note_source_kind(note, kinds) == SourceType.INTERVIEW.value) == survey
        ]
        chosen.sort(
            key=lambda note: (
                narrative_source_rank(_note_source_kind(note, kinds)),
                _MUST_USE_SURVEY_NOTE_ORDER.index(_note_type_value(note)),
            )
        )
        sentences: list[tuple[str, str]] = []
        for note in chosen:
            for sentence in _note_sentences(note):
                if _clean_source_sentence(sentence):
                    sentences.append((sentence, _note_id(note)))
        # 모델이 고른 원문 문장을 앞에 둔다(같은 문장이면 그대로 쓴다).
        sentences.sort(key=lambda item: 0 if item[0] in proposed else 1)
        return sentences

    selected: list[tuple[str, list[str]]] = []
    mode = MUST_USE_MODE_NONE
    for survey, pool_mode in ((True, MUST_USE_MODE_SURVEY), (False, MUST_USE_MODE_SOURCE)):
        for sentence, note_id in pool(survey):
            existing = next((item for item in selected if item[0] == sentence), None)
            if existing is not None:
                if note_id not in existing[1]:
                    existing[1].append(note_id)
                continue
            if len(selected) >= AUTOMATIC_MUST_USE_LIMIT:
                break
            selected.append((sentence, [note_id]))
        if selected:
            mode = pool_mode
            break
    chosen_texts = {text for text, _ids in selected}
    for text in proposed:
        if text not in chosen_texts:
            report.add("must_use_messages", text, DROP_MUST_USE_NOT_SOURCE_VERBATIM)
    report.must_use = {
        "mode": mode,
        "items": [{"text": text, "evidence_note_ids": ids} for text, ids in selected],
    }
    result["must_use_messages"] = [text for text, _ids in selected]
    return _unique_ids(note_id for _text, ids in selected for note_id in ids)


def _must_use_item_ids(
    raw: Any, must_use_evidence: Any, field_ids: list[str], valid: set[str]
) -> list[str]:
    text = str(raw.get("text") if isinstance(raw, dict) else raw or "").strip()
    for entry in must_use_evidence if isinstance(must_use_evidence, list) else []:
        if isinstance(entry, dict) and str(entry.get("text") or "").strip() == text:
            return [
                note_id for note_id in _flatten_ids(entry.get("evidence_note_ids")) if note_id in valid
            ]
    return list(field_ids)


def _apply_conflicts(
    result: dict[str, Any],
    narratives: list[dict[str, Any]],
    must_use_evidence: Any,
    notes_by_id: dict[str, Any],
    kinds: dict[str, str],
    report: AutomaticPruneReport,
) -> set[str]:
    """충돌 노트를 우선순위로 실제로 푼다(D34). 진 노트 id 집합을 돌려준다.

    충돌마다 관련 노트의 출처 우선순위를 매긴다 — 사실(약력·경력·병원 정보 값)은 마케터 입력 >
    네이버 > 설문 > 홈페이지 > 블로그, 서술(목소리·철학·진료 서술)은 설문이 먼저다. 우선순위가 낮은
    노트가 가장 잘 뒷받침하는 결과 문장은 버리고 그 노트를 근거에서 뺀다. 우열을 가릴 수 없으면
    양쪽 문장을 모두 조용히 버린다. 실제로 적용한 해결만 `report.conflicts`에 남긴다.
    """

    valid = set(notes_by_id)
    loser_reason: dict[str, str] = {}
    decided: list[dict[str, Any]] = []
    for item in result.get("conflict_notes") or []:
        text = item.get("text") if isinstance(item, dict) else item
        ids = _unique_ids(
            note_id
            for note_id in _flatten_ids(item.get("evidence_note_ids") if isinstance(item, dict) else [])
            if note_id in valid
        )
        if len(ids) < 2:
            continue
        involved = [notes_by_id[note_id] for note_id in ids]
        fact = _is_fact_conflict(involved, kinds)
        ranks = {note_id: _priority_rank(notes_by_id[note_id], kinds, fact=fact) for note_id in ids}
        best = min(ranks.values())
        losers = {note_id for note_id, rank in ranks.items() if rank > best}
        if not losers:
            for note_id in ids:
                loser_reason.setdefault(note_id, DROP_CONFLICT_UNDECIDED)
            continue
        for note_id in losers:
            loser_reason[note_id] = DROP_CONFLICT_LOWER_PRIORITY
        winners = [note_id for note_id in ids if note_id not in losers]
        decided.append(
            {
                "text": _short(_collapse(text), 200),
                "evidence_note_ids": ids,
                "kept_note_ids": winners,
                "dropped_note_ids": sorted(losers),
                "source_kinds": sorted(
                    {_note_source_kind(notes_by_id[note_id], kinds) for note_id in ids},
                    key=lambda kind: (narrative_source_rank(kind), kind),
                ),
                "preferred_source": _note_source_kind(notes_by_id[winners[0]], kinds),
                "resolved_by": CONFLICT_FACT_RESOLUTION_RULE if fact else CONFLICT_RESOLUTION_RULE,
                "dropped_sentences": 0,
            }
        )
    if not loser_reason:
        return set()
    dropped_by_note: dict[str, int] = {}

    def keep(field_name: str, text: str, linked_ids: list[str]) -> bool:
        linked = [notes_by_id[note_id] for note_id in linked_ids if note_id in notes_by_id]
        if not any(str(note.id) in loser_reason for note in linked):
            return True
        best, _ratio = _best_support(text, linked)
        if best is None:
            best = next(note for note in linked if str(note.id) in loser_reason)
        if str(best.id) not in loser_reason:
            return True
        report.add(field_name, text, loser_reason[str(best.id)])
        dropped_by_note[str(best.id)] = dropped_by_note.get(str(best.id), 0) + 1
        return False

    for field_name in _PRUNE_POSITIVE_SINGLE:
        value = result.get(field_name)
        if isinstance(value, str) and value.strip():
            linked = _field_ids(result, field_name, valid)
            kept = [
                sentence
                for sentence in split_sentences(value) or [value]
                if keep(field_name, sentence, linked)
            ]
            result[field_name] = " ".join(kept) if kept else None
    for field_name in ("content_principles", "tone_guidelines"):
        linked = _field_ids(result, field_name, valid)
        result[field_name] = [
            item
            for item in result.get(field_name) or []
            if keep(field_name, " ".join(_iter_field_texts(item)), linked)
        ]
    field_must_use = _field_ids(result, "must_use_messages", valid)
    result["must_use_messages"] = [
        item
        for item in result.get("must_use_messages") or []
        if keep(
            "must_use_messages",
            " ".join(_iter_field_texts(item)),
            _must_use_item_ids(item, must_use_evidence, field_must_use, valid),
        )
    ]
    narratives[:] = [
        item
        for item in narratives
        if keep(
            "treatment_narratives",
            " ".join(
                _iter_field_texts(
                    {key: value for key, value in item.items() if key != "evidence_note_ids"}
                )
            ),
            list(item["evidence_note_ids"]),
        )
    ]
    # 진 노트는 어떤 필드의 근거로도 남기지 않는다.
    for item in narratives:
        item["evidence_note_ids"] = [
            note_id for note_id in item["evidence_note_ids"] if note_id not in loser_reason
        ]
    narratives[:] = [item for item in narratives if item["evidence_note_ids"]]
    evidence_map = result.get("evidence_map") if isinstance(result.get("evidence_map"), dict) else {}
    result["evidence_map"] = {
        key: [note_id for note_id in _flatten_ids(raw_ids) if note_id not in loser_reason]
        for key, raw_ids in evidence_map.items()
    }
    for entry in must_use_evidence if isinstance(must_use_evidence, list) else []:
        if isinstance(entry, dict):
            entry["evidence_note_ids"] = [
                note_id
                for note_id in _flatten_ids(entry.get("evidence_note_ids"))
                if note_id not in loser_reason
            ]
    for record in decided:
        record["dropped_sentences"] = sum(
            dropped_by_note.get(note_id, 0) for note_id in record["dropped_note_ids"]
        )
        report.conflicts.append(record)
    return set(loser_reason)


def _ground_sentences(
    result: dict[str, Any],
    narratives: list[dict[str, Any]],
    notes_by_id: dict[str, Any],
    allowed: list[str],
    report: AutomaticPruneReport,
) -> None:
    """합성 문장마다 연결된 노트 본문으로 뒷받침되는지 대조하고 아니면 버린다(D33).

    포지셔닝·환자 약속은 내용어가 노트와 겹쳐야 하고, 의료 효능·결과·자격·비교·수치 주장은 그
    낱말과 수치가 노트에 그대로 있어야 한다. 목소리·원칙·톤 같은 문체 묘사는 의료 주장일 때만
    대조한다. 진료 서술은 진료 항목 이름이 노트에 있어야 하고 각 표현은 의료 주장일 때 대조한다.
    """

    valid = set(notes_by_id)
    for field_name in _PRUNE_POSITIVE_SINGLE:
        value = result.get(field_name)
        if not (isinstance(value, str) and value.strip()):
            continue
        support = _excerpts(notes_by_id, _field_ids(result, field_name, valid))
        kept, dropped = _grounded_text(
            value, support, allowed, require_general=field_name != "doctor_voice"
        )
        for sentence in dropped:
            report.add(field_name, sentence, DROP_UNSUPPORTED_CLAIM)
        result[field_name] = kept
    for field_name in ("content_principles", "tone_guidelines"):
        support = _excerpts(notes_by_id, _field_ids(result, field_name, valid))
        kept_items: list[Any] = []
        for item in result.get(field_name) or []:
            text = " ".join(_iter_field_texts(item))
            if _text_kept(text, support, allowed, require_general=False):
                kept_items.append(item)
            else:
                report.add(field_name, text, DROP_UNSUPPORTED_CLAIM)
        result[field_name] = kept_items
    grounded: list[dict[str, Any]] = []
    for item in narratives:
        support = _excerpts(notes_by_id, item["evidence_note_ids"])
        name = str(item.get("treatment") or "").strip()
        if (
            name
            and name != _GENERIC_TREATMENT_LABEL
            and not _text_kept(name, support, allowed, require_general=True)
        ):
            report.add("treatment_narratives", name, DROP_UNSUPPORTED_CLAIM)
            continue
        cleaned = dict(item)
        for key, value in item.items():
            if key in {"evidence_note_ids", "treatment"}:
                continue
            if isinstance(value, str) and value.strip():
                if not _text_kept(value, support, allowed, require_general=False):
                    report.add("treatment_narratives", value, DROP_UNSUPPORTED_CLAIM)
                    cleaned[key] = ""
            elif isinstance(value, list):
                phrases: list[Any] = []
                for phrase in value:
                    text = " ".join(_iter_field_texts(phrase))
                    if _text_kept(text, support, allowed, require_general=False):
                        phrases.append(phrase)
                    else:
                        report.add("treatment_narratives", text, DROP_UNSUPPORTED_CLAIM)
                cleaned[key] = phrases
        grounded.append(cleaned)
    narratives[:] = grounded


def prune_automatic_candidate(
    payload: dict[str, Any],
    notes: list[Any],
    sources: list[Any],
) -> tuple[dict[str, Any], AutomaticPruneReport]:
    """자동 요약 후보에서 보류 사유가 될 것을 모두 버린다(D33). 순수 함수.

    불변식: 결과에 대해 `validate_philosophy_grounding(result, notes) == []`,
    `find_error_marker_fields(result) == []`, 긍정 운영 필드에 의료광고 금지 표현·운영 사실 없음,
    남은 합성 문장은 연결된 노트 본문으로 뒷받침된다(`essence_claims`). 충돌은 우선순위로 실제로
    풀고(D34) 적용한 해결만 `report.conflicts`에 남긴다.
    """

    result = copy.deepcopy(payload)
    must_use_evidence = result.pop(MUST_USE_EVIDENCE_KEY, None)
    strip_private_synthesis_keys(result)
    report = AutomaticPruneReport()
    valid = {_note_id(note) for note in notes}
    notes_by_id = {_note_id(note): note for note in notes}
    kinds = source_kinds_by_id(sources)

    for field_name in _PRUNE_POSITIVE_SINGLE:
        value = result.get(field_name)
        if isinstance(value, str) and value.strip():
            reason = _offending_reason(value, positive=True)
            if reason:
                report.add(field_name, value, reason)
                result[field_name] = None
        elif value is not None and not isinstance(value, str):
            result[field_name] = None

    for field_name in (*_PRUNE_POSITIVE_LIST, *_PRUNE_POLICY_LIST):
        kept: list[Any] = []
        for item in result.get(field_name) or []:
            text = " ".join(_iter_field_texts(item))
            if not text.strip():
                continue
            reason = _offending_reason(text, positive=field_name not in _PRUNE_POLICY_LIST)
            if reason:
                report.add(field_name, text, reason)
                continue
            kept.append(item)
        result[field_name] = kept

    narratives: list[dict[str, Any]] = []
    for item in result.get("treatment_narratives") or []:
        if not isinstance(item, dict):
            report.add("treatment_narratives", item, DROP_UNGROUNDED)
            continue
        text = " ".join(
            _iter_field_texts({key: value for key, value in item.items() if key != "evidence_note_ids"})
        )
        reason = _offending_reason(text, positive=True)
        if reason:
            report.add("treatment_narratives", text, reason)
            continue
        ids = [note_id for note_id in _flatten_ids(item.get("evidence_note_ids")) if note_id in valid]
        if not ids:
            report.add("treatment_narratives", text, DROP_UNGROUNDED)
            continue
        narratives.append({**item, "evidence_note_ids": _unique_ids(ids)})

    excluded_ids = _apply_conflicts(
        result, narratives, must_use_evidence, notes_by_id, kinds, report
    )
    # 충돌에서 진 노트는 이후 어떤 선택(원문 대조·필수 문구·공개 소개)에도 쓰지 않는다.
    allowed = [
        str(getattr(note, "source_excerpt", "") or "")
        for note in notes
        if _note_id(note) not in excluded_ids
    ]
    _ground_sentences(result, narratives, notes_by_id, allowed, report)
    result["treatment_narratives"] = narratives

    local = result.get("local_context")
    local = dict(local) if isinstance(local, dict) else {}
    for key in ("region_terms", "local_patient_context"):
        kept_terms: list[Any] = []
        for item in local.get(key) or []:
            text = " ".join(_iter_field_texts(item))
            reason = _offending_reason(text, positive=True) if text.strip() else None
            if reason:
                report.add("local_context", text, reason)
                continue
            if text.strip():
                kept_terms.append(item)
        local[key] = kept_terms
    local_ids = [
        note_id for note_id in _flatten_ids(local.get("evidence_note_ids")) if note_id in valid
    ] or [
        note_id
        for note_id in _flatten_ids((result.get("evidence_map") or {}).get("local_context"))
        if note_id in valid
    ]
    has_local = bool(local.get("region_terms") or local.get("local_patient_context"))
    if has_local and not local_ids:
        for key in ("region_terms", "local_patient_context"):
            for item in local.get(key) or []:
                report.add("local_context", item, DROP_UNGROUNDED)
            local[key] = []
        has_local = False
    local.pop("evidence_note_ids", None)
    if has_local:
        local["evidence_note_ids"] = _unique_ids(local_ids)
    local["avoid_region_stuffing"] = True
    result["local_context"] = local

    must_use_ids = _select_must_use(result, notes, sources, report, excluded_ids)

    raw_map = result.get("evidence_map") if isinstance(result.get("evidence_map"), dict) else {}
    evidence_map: dict[str, list[str]] = {}
    for key, raw_ids in raw_map.items():
        ids = _unique_ids(note_id for note_id in _flatten_ids(raw_ids) if note_id in valid)
        if ids:
            evidence_map[str(key)] = ids
    if narratives:
        evidence_map["treatment_narratives"] = _unique_ids(
            note_id for item in narratives for note_id in item["evidence_note_ids"]
        )
    else:
        evidence_map.pop("treatment_narratives", None)
    if has_local:
        evidence_map["local_context"] = local["evidence_note_ids"]
    else:
        evidence_map.pop("local_context", None)
    if must_use_ids:
        evidence_map["must_use_messages"] = must_use_ids
    else:
        evidence_map.pop("must_use_messages", None)

    for field_name in _PRUNE_POSITIVE_SINGLE:
        if result.get(field_name) and not evidence_map.get(field_name):
            report.add(field_name, result[field_name], DROP_UNGROUNDED)
            result[field_name] = None
    for field_name in ("content_principles", "tone_guidelines"):
        if result.get(field_name) and not evidence_map.get(field_name):
            for item in result[field_name]:
                report.add(field_name, " ".join(_iter_field_texts(item)), DROP_UNGROUNDED)
            result[field_name] = []
    for field_name, mandatory in (
        ("avoid_messages", MANDATORY_AVOID_MESSAGES),
        ("medical_ad_risk_rules", MANDATORY_MEDICAL_AD_RISK_RULES),
    ):
        source_backed = [item for item in result.get(field_name) or [] if item not in mandatory]
        if source_backed and not evidence_map.get(field_name):
            for item in source_backed:
                report.add(field_name, " ".join(_iter_field_texts(item)), DROP_UNGROUNDED)
            result[field_name] = [item for item in result[field_name] if item in mandatory]

    for key in list(evidence_map):
        if key in _PRUNE_GROUNDED_FIELDS and not _field_has_source_backed_value(
            key, result.get(key)
        ):
            evidence_map.pop(key)
    result["evidence_map"] = evidence_map
    result["conflict_notes"] = []
    _scrub_conflict_losers(result, notes_by_id, excluded_ids, report)
    # 공개 소개(about)는 근거 노트에 글자 그대로 있고 의료 주장 어휘가 없는 문장만 쓴다(fail-closed).
    report.public_about = list(
        dict.fromkeys(
            body
            for field_name in ("positioning_statement", "patient_promise")
            for sentence in split_sentences(str(result.get(field_name) or ""))
            if (body := normalize_sentence(without_template_prefix(sentence)))
            and is_verbatim_in(body, allowed)
            and _clean_source_sentence(body)
        )
    )
    return result, report


def _scrub_conflict_losers(
    result: dict[str, Any],
    notes_by_id: dict[str, Any],
    excluded_ids: set[str],
    report: AutomaticPruneReport,
) -> None:
    """마지막 확인: 진 노트의 id와 문장이 판 어디에도 남지 않게 한다."""

    if not excluded_ids:
        return
    loser_sentences = [
        sentence
        for note_id in excluded_ids
        if note_id in notes_by_id
        for sentence in _note_sentences(notes_by_id[note_id])
        + [normalize_sentence(getattr(notes_by_id[note_id], "source_excerpt", ""))]
        if sentence
    ]

    def tainted(text: str) -> bool:
        normalized = normalize_sentence(text)
        return any(
            sentence in normalized or (len(normalized) >= _MIN_MUST_USE_CHARS and normalized in sentence)
            for sentence in loser_sentences
        )

    for field_name in _PRUNE_POSITIVE_SINGLE:
        value = result.get(field_name)
        if isinstance(value, str) and value.strip():
            kept = [sentence for sentence in split_sentences(value) if not tainted(sentence)]
            for sentence in split_sentences(value):
                if tainted(sentence):
                    report.add(field_name, sentence, DROP_CONFLICT_LOWER_PRIORITY)
            result[field_name] = " ".join(kept) or None
    for field_name in ("content_principles", "tone_guidelines", "must_use_messages"):
        kept_items = []
        for item in result.get(field_name) or []:
            if tainted(" ".join(_iter_field_texts(item))):
                report.add(field_name, item, DROP_CONFLICT_LOWER_PRIORITY)
            else:
                kept_items.append(item)
        result[field_name] = kept_items
    report.must_use["items"] = [
        item
        for item in report.must_use.get("items", [])
        if item["text"] in result["must_use_messages"]
        and not set(item["evidence_note_ids"]) & excluded_ids
    ]
    result["treatment_narratives"] = [
        item
        for item in result.get("treatment_narratives") or []
        if not set(_flatten_ids(item.get("evidence_note_ids"))) & excluded_ids
    ]
    evidence_map = result.get("evidence_map") or {}
    result["evidence_map"] = {
        key: [note_id for note_id in ids if note_id not in excluded_ids]
        for key, ids in evidence_map.items()
        if [note_id for note_id in ids if note_id not in excluded_ids]
    }
    for key in list(result["evidence_map"]):
        if key in _PRUNE_GROUNDED_FIELDS and not _field_has_source_backed_value(
            key, result.get(key)
        ):
            result["evidence_map"].pop(key)


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

    effective_policy = effective_safety_policy(philosophy)
    for message in _string_items(effective_policy["avoid_messages"]):
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
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            or_(
                HospitalContentPhilosophy.is_base.is_(True),
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            ),
        )
        .order_by(
            HospitalContentPhilosophy.is_base.desc(),
            HospitalContentPhilosophy.approved_at.desc().nullslast(),
            HospitalContentPhilosophy.version.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def build_monthly_essence_summary(
    db, hospital: Hospital, period_start: datetime, period_end: datetime
) -> dict[str, Any]:
    # 사진은 공개 자산 검수 대상이지 글쓰기 기준의 근거 자료가 아니다. 제외 자료와 원문이
    # 없는 URL 전용 자료 역시 현재 기준의 필수 분모가 아니다. 이 정의는 실시간 게이트와
    # 반드시 같아야 하므로 readiness·승인과 같은 predicate 하나만 쓴다.
    source_result = db.execute(
        select(HospitalSourceAsset).where(
            HospitalSourceAsset.hospital_id == hospital.id,
            required_text_source_predicate(),
        )
    )
    required_sources = list(source_result.scalars().all())
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
        if item.essence_status == ESSENCE_STATUS_ALIGNED
        and approved is not None
        and item.content_philosophy_id == approved.id
    )
    # source_stale is audit metadata only — do not demote ALIGNED into needs_review
    # solely because sources drifted after approval. Still count real NEEDS_REVIEW,
    # missing approved, and philosophy-id mismatch vs approved/base.
    needs_review_count = sum(
        1
        for item in generated_items
        if item.essence_status == ESSENCE_STATUS_NEEDS_REVIEW
        or (
            item.essence_status == ESSENCE_STATUS_ALIGNED
            and (approved is None or item.content_philosophy_id != approved.id)
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
    seen: set[str] = set()
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
            if excerpt in text and excerpt not in seen:
                seen.add(excerpt)
                excerpts.append(excerpt)
    return excerpts


def _is_differential_diagnosis_caution(excerpt: str) -> bool:
    """A source-backed caution about overlapping symptoms is not conflicting sources."""
    if _has_any(excerpt, ["자료", "홈페이지", "기록", "모순", "불일치", "반대", "상충"]):
        return False
    return (
        "증상만으로" in excerpt
        and _has_any(excerpt, ["단정", "진단"])
        and _has_any(excerpt, ["진료", "검사"])
        and _has_any(excerpt, ["함께", "비슷", "겹", "구분"])
    )


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


def _pick_notes(
    notes: list[HospitalSourceEvidenceNote],
    types: list[EvidenceNoteType],
    limit: int,
) -> list[HospitalSourceEvidenceNote]:
    """종류가 맞는 노트를 **입력 순서 그대로**(= 출처 우선순위) 고른다."""

    wanted = {getattr(note_type, "value", note_type) for note_type in types}
    return [note for note in notes if _note_type_value(note) in wanted][:limit]


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
