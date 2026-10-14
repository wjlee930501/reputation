"""승인 단계 없는 자동 Essence(운영 요약) — 합성 → 결정적 정리 → 즉시 적용(Phase 4 D33).

Essence는 사람·AI 검수자의 승인을 기다리지 않는다. 마케터가 입력한 병원 정보(PROFILE_FACTS 자료),
연결된 홈페이지·블로그, 원장 인터뷰 설문에서 자동으로 합성하고, 근거 노트에 묶이지 않는 주장·의료광고
금지 표현·차단 페이지 잔재·운영 사실은 **버린 뒤**(보류 아님) 곧바로 적용한다. 프롬프트 주입·차단
페이지 잔재가 든 자료는 **입력에서 뺀다**. 자료 간 충돌은 고정 우선순위(D34)로 조용히 풀고 합성
메타데이터에만 남긴다. 그래서 이 경로는 사고·Slack을 만들지 않고, Essence가 콘텐츠 생성을 멈추는
일도 없다. 글 단위 안전(의료광고 필터·글마다 독립 AI 검수·참고자료 게이트)은 그대로다.

적용된 판은 `PhilosophyStatus.APPROVED` + `is_base=True`다(E1) — "APPROVED"는 이제 "현재 적용 중인
판"이라는 뜻이고, 만든 방식은 `created_by = reviewed_by = "system:essence-auto"`와
`synthesis_metadata.mode = "AUTOMATIC"`로 구분한다. 생성·발행 게이트·판 조정 SQL·필수 문구 계약은 그래서
코드 변경 없이 그대로 동작한다.

다시 합성하는 때(`essence_refresh_trigger`):

- T1 적용 중인 판이 없다.
- T2 근거 철회·수정(`authority_refresh_required`) — 즉시.
- T3 설문(INTERVIEW) 자료가 새로 처리됐거나 바뀌었다.
- T4 판이 입력으로 쓴 병원 정보(PROFILE_FACTS)가 바뀌었다.
- T5 판을 만들 때 아직 처리 중이던 자료가 처리됐다(한 번 흡수).
- T6 공급자 장애로 결정적 폴백 판이 적용됐다 — 24시간 뒤 한 번 다시 합성한다.

홈페이지·블로그의 일반 drift는 다시 합성하지 않는다(stable base — 공개 글의 판 교체 churn 방지).
이 컬럼 이전의 승인본(`synthesis_metadata`가 빈 판)은 그 판 승인 뒤 처리된 설문(T3)·근거 철회(T2)가
있을 때만 자동 판으로 넘어간다. T3~T6은 같은 병원에서 30분에 한 번만 다시 합성한다(디바운스).
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.essence import (
    AUTO_RECOVERY_CYCLE_GAP_FIELD,
    AUTO_RECOVERY_LAST_AT_GAP_FIELD,
    AUTO_RECOVERY_TERMINAL_GAP_FIELD,
    AUTO_REVIEW_GAP_FIELD,
    EXCLUDED_ERROR_SOURCE_GAP_FIELD,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.audit_log import write_audit_log_sync
from app.services.content_provenance import (
    mark_removed_source_dependency,
    removed_generation_source_ids,
)
from app.services.content_publication import apply_essence_revalidation
from app.services.enum_values import enum_value
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    SYNTHESIS_MODE_DETERMINISTIC,
    SYNTHESIS_MODE_LLM,
    apply_mandatory_safety_policy,
    compute_sources_snapshot_hash,
    prune_automatic_candidate,
    source_kind,
    synthesize_philosophy_with_mode,
)
from app.services.essence_readiness import (
    base_candidate_ordering,
    base_candidate_predicate,
)
from app.services.essence_sources import required_text_source_predicate
from app.services.evidence_noise import (
    load_evidence_noise_hash_sync,
    not_noise_note_predicate,
)
from app.services.knowledge_changes import authority_refresh_required
from app.services.profile_facts_source import is_profile_facts_source
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import check_forbidden

#: 자동 판을 만들고 적용한 주체(감사·`created_by`·`reviewed_by`).
AUTOMATIC_ESSENCE_ACTOR = "system:essence-auto"
AUTOMATIC_ESSENCE_MODE = "AUTOMATIC"
AUTOMATIC_APPROVAL_NOTE = "automatic summary (D33)"
#: 옛 AI 검수 경로가 남긴 초안·승인본의 작성자. 현황 화면이 옛 보류 초안을 읽을 때만 쓴다.
AUTO_ESSENCE_ACTOR = "SYSTEM_ESSENCE_AI_REVIEW"
# 옛 보류 초안의 자동 재검수 사다리(읽기 전용 — 이 경로는 더 이상 보류 초안을 만들지 않는다).
AUTO_ESSENCE_MAX_RECOVERY_CYCLES = 4
AUTO_ESSENCE_RECOVERY_BACKOFFS: tuple[timedelta, ...] = (
    timedelta(hours=1),
    timedelta(hours=4),
    timedelta(hours=12),
)
# 필수 자료가 6시간 넘게 ERROR이거나, 다른 필수 자료가 이미 처리됐고 1시간 유예가 지났다면
# ERROR 자료를 합성 입력과 완결성 검사에서 빼고 gap으로 남긴다(D26).
ERROR_SOURCE_EXCLUSION_AFTER = timedelta(hours=6)
ERROR_SOURCE_EXCLUSION_GRACE = timedelta(hours=1)
#: 같은 병원의 T3~T6 재합성 최소 간격 — 설문 여러 파일을 연달아 올려도 한 번만 다시 만든다.
AUTOMATIC_RESYNTHESIS_DEBOUNCE = timedelta(minutes=30)
#: 폴백 판을 LLM으로 다시 합성해 보는 시점(T6)과 횟수.
FALLBACK_UPGRADE_AFTER = timedelta(hours=24)
FALLBACK_UPGRADE_MAX_ATTEMPTS = 1
# 같은 입력(snapshot+previous)으로 반복 실패하는 병원이 15분마다 합성을 다시 사지 않도록,
# 시도 횟수에 따라 다음 claim을 미룬다.
_CLAIM_RETRY_BASE_BACKOFF = timedelta(minutes=15)
_CLAIM_RETRY_MAX_BACKOFF = timedelta(hours=24)
_ESSENCE_REFRESH_OPERATION = "ESSENCE_SNAPSHOT_REFRESH"
_ESSENCE_REFRESH_LEASE = timedelta(minutes=15)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"이전\s*(?:모든\s*)?(?:지시|명령)(?:를|을)?\s*(?:무시|잊)"),
    re.compile(r"(?:system|developer)\s*prompt", re.IGNORECASE),
    re.compile(r"자동\s*승인(?:하라|해|하세요)"),
    re.compile(r"<\/?(?:system|developer|assistant)>", re.IGNORECASE),
)

TRIGGER_NO_ESSENCE = "T1_NO_ESSENCE"
TRIGGER_AUTHORITY_CHANGE = "T2_AUTHORITY_CHANGE"
TRIGGER_SURVEY_CHANGED = "T3_SURVEY_CHANGED"
TRIGGER_PROFILE_FACTS_CHANGED = "T4_PROFILE_FACTS_CHANGED"
TRIGGER_PENDING_INPUTS_PROCESSED = "T5_PENDING_INPUTS_PROCESSED"
TRIGGER_FALLBACK_UPGRADE = "T6_FALLBACK_UPGRADE"
#: 생성이 멈춰 있는 트리거 — 비용 보류여도 결정적 폴백 판을 바로 적용한다.
_GENERATION_BLOCKING_TRIGGERS = frozenset({TRIGGER_NO_ESSENCE, TRIGGER_AUTHORITY_CHANGE})

EXCLUDED_PROMPT_INJECTION = "PROMPT_INJECTION"
EXCLUDED_ERROR_PAGE = "ERROR_PAGE"
EXCLUDED_PROCESSING_ERROR = "PROCESSING_ERROR"


class EssenceSynthesisDeferred(RuntimeError):
    """합성을 지금 살 수 없다(비용 가드 보류). 판이 있으면 기존 판을 두고 다음 틱에 다시 한다."""


class EssenceRefreshStatus(StrEnum):
    UP_TO_DATE = "UP_TO_DATE"
    WAITING_FOR_SOURCES = "WAITING_FOR_SOURCES"
    APPLIED = "APPLIED"
    SNAPSHOT_CHANGED = "SNAPSHOT_CHANGED"
    NOT_FOUND = "NOT_FOUND"
    # 같은 입력으로 방금 시도했거나(백오프), 비용 보류·폴백이라 기존 판을 두었다.
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class EssenceRefreshResult:
    status: EssenceRefreshStatus
    hospital_id: uuid.UUID
    snapshot_hash: str | None = None
    philosophy_id: uuid.UUID | None = None
    previous_philosophy_id: uuid.UUID | None = None
    trigger: str | None = None
    should_revalidate_site: bool = False
    fallback: bool = False
    reason: str | None = None
    dropped_count: int = 0


def _status_value(value: object) -> str:
    return str(enum_value(value) or "")


def _required_sources(db: Session, hospital_id: uuid.UUID) -> list[HospitalSourceAsset]:
    return list(
        db.execute(
            select(HospitalSourceAsset)
            .where(
                HospitalSourceAsset.hospital_id == hospital_id,
                required_text_source_predicate(),
            )
            .order_by(HospitalSourceAsset.id)
        )
        .scalars()
        .all()
    )


def _approved(db: Session, hospital_id: uuid.UUID) -> HospitalContentPhilosophy | None:
    return db.scalar(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            base_candidate_predicate(),
        )
        .order_by(*base_candidate_ordering())
        .limit(1)
        .with_for_update()
    )


def _approved_unlocked(db: Session, hospital_id: uuid.UUID) -> HospitalContentPhilosophy | None:
    """Read-only reconciliation lookup; correctness is rechecked under lock later."""

    return db.scalar(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            base_candidate_predicate(),
        )
        .order_by(*base_candidate_ordering())
        .limit(1)
    )


def _notes_for_sources(
    db: Session,
    hospital_id: uuid.UUID,
    source_ids: list[uuid.UUID],
) -> list[HospitalSourceEvidenceNote]:
    if not source_ids:
        return []
    return list(
        db.execute(
            select(HospitalSourceEvidenceNote)
            .where(
                HospitalSourceEvidenceNote.hospital_id == hospital_id,
                HospitalSourceEvidenceNote.source_asset_id.in_(source_ids),
                # 운영자가 노이즈로 뺀 주장은 합성·검수 입력에서 제외한다.
                not_noise_note_predicate(),
            )
            .order_by(HospitalSourceEvidenceNote.id)
        )
        .scalars()
        .all()
    )


def _iter_text(value: object):
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_text(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_text(nested)



def _has_prompt_injection_text(*parts: str | None) -> bool:
    text = "\n".join(part for part in parts if part)
    return any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS)


def partition_automatic_inputs(
    sources: list[HospitalSourceAsset],
) -> tuple[list[HospitalSourceAsset], list[dict[str, Any]]]:
    """합성 입력에서 오염된 자료를 뺀다(보류 아님). 자료 상태는 바꾸지 않는다.

    프롬프트 주입 의심 문구가 원문·메모에 있는 자료, 차단·오류 페이지 잔재인 자료는 입력에서
    빠지고 `excluded_inputs`에만 남는다. 사람이 자료를 고치면 새 hash로 다시 들어온다.
    """

    usable: list[HospitalSourceAsset] = []
    excluded: list[dict[str, Any]] = []
    for source in sources:
        reason = None
        raw_text = getattr(source, "raw_text", None)
        if _has_prompt_injection_text(raw_text, getattr(source, "operator_note", None)):
            reason = EXCLUDED_PROMPT_INJECTION
        elif looks_like_error_page_text(raw_text or ""):
            reason = EXCLUDED_ERROR_PAGE
        if reason:
            excluded.append(
                {
                    "source_id": str(source.id),
                    "reason": reason,
                    "content_hash": getattr(source, "content_hash", None),
                }
            )
            continue
        usable.append(source)
    return usable, excluded


def _automatic_recovery_terminal_reason(philosophy: HospitalContentPhilosophy) -> str | None:
    """바로 사람에게 넘긴 보류라는 서버 표식의 사유. 없으면 None."""

    for item in getattr(philosophy, "unsupported_gaps", None) or []:
        if isinstance(item, dict) and item.get("field") == AUTO_RECOVERY_TERMINAL_GAP_FIELD:
            return str(item.get("reason") or "operator_required")
    return None


def _automatic_recovery_cycles(philosophy: HospitalContentPhilosophy) -> int:
    """Read the durable retry marker stored on an escalated automatic draft."""

    cycles = 0
    for item in getattr(philosophy, "unsupported_gaps", None) or []:
        if not isinstance(item, dict) or item.get("field") != AUTO_RECOVERY_CYCLE_GAP_FIELD:
            continue
        try:
            cycles = max(cycles, int(item.get("reason") or 0))
        except (TypeError, ValueError):
            continue
    return cycles


def _automatic_recovery_last_at(philosophy: HospitalContentPhilosophy) -> datetime | None:
    """Read the timestamp of the last automatic escalation, if this draft has one."""

    latest: datetime | None = None
    for item in getattr(philosophy, "unsupported_gaps", None) or []:
        if not isinstance(item, dict) or item.get("field") != AUTO_RECOVERY_LAST_AT_GAP_FIELD:
            continue
        parsed = _as_utc(item.get("reason"))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _as_utc(value: object) -> datetime | None:
    """Normalize a stored timestamp (ISO text or column value) to aware UTC."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def effective_recovery_cycle(philosophy: HospitalContentPhilosophy) -> int:
    """이 초안이 실제로 쓴 자동 재검수 사이클 수.

    옛 배포는 "영구 정지"를 뜻하는 고정값(8)을 박았고 시각은 남기지 않았다. 그 표식을
    소진으로 읽으면 운영 중인 병원이 영원히 사람의 승인만 기다린다 — 이 버전업이 없애려는
    바로 그 상태다. 시각이 없는 상한 초과 표식은 레거시로 보고 **사이클 1**로 읽어 남은
    사다리(2·3·4)를 준다. 시각이 있는 상한 초과는 이 코드가 센 것이므로 소진 그대로다.
    """

    cycles = _automatic_recovery_cycles(philosophy)
    if cycles >= AUTO_ESSENCE_MAX_RECOVERY_CYCLES and _automatic_recovery_last_at(philosophy) is None:
        return 1
    return cycles


def automatic_recovery_backoff(cycle: int) -> timedelta:
    """사이클 c를 쓴 보류가 다음 자동 재검수까지 기다리는 시간. 읽기·쓰기가 같은 표를 쓴다."""

    index = min(max(int(cycle), 1), len(AUTO_ESSENCE_RECOVERY_BACKOFFS)) - 1
    return AUTO_ESSENCE_RECOVERY_BACKOFFS[index]


def automatic_recovery_due_at(philosophy: HospitalContentPhilosophy) -> datetime | None:
    """다음 자동 재검수가 가능한 시각. None이면 자동 예산이 끝났다(사람의 일).

    사이클 c를 이미 쓴 초안은 마지막 시도로부터 `automatic_recovery_backoff(c)`가 지나야
    다시 시도한다. 표식이 없는 옛 초안(c=0)과 레거시 영구 정지 표식은 지금 바로 한 번 받는다.
    """

    if _automatic_recovery_terminal_reason(philosophy) is not None:
        return None
    cycles = effective_recovery_cycle(philosophy)
    last_at = _automatic_recovery_last_at(philosophy)
    if cycles >= AUTO_ESSENCE_MAX_RECOVERY_CYCLES:
        return None
    if cycles <= 0 or last_at is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return last_at + automatic_recovery_backoff(cycles)


def automatic_recovery_owns_draft(philosophy: HospitalContentPhilosophy) -> bool:
    """자동 복구가 아직 소유한 보류인가 — 기한 도래 여부가 아니라 예산 잔량으로 판정한다.

    사람이 손대지 않은 시스템 초안이고 예산이 남아 있으면, 기다리는 중이라도 그 일은
    운영자의 할 일이 아니다(현황 예외 카드·인시던트 상태가 같은 판정을 쓴다).
    """

    # 컬럼 일부만 고른 행(목록·현황의 묶음 조회)도 같은 판정을 물어본다. 빠진 값은
    # "시스템 초안이라는 증거 없음"으로 읽어 사람의 일로 남긴다(fail-closed).
    has_auto_review_finding = any(
        isinstance(item, dict) and item.get("field") == AUTO_REVIEW_GAP_FIELD
        for item in getattr(philosophy, "unsupported_gaps", None) or []
    )
    created_at = getattr(philosophy, "created_at", None)
    updated_at = getattr(philosophy, "updated_at", None)
    return bool(
        getattr(philosophy, "created_by", None) == AUTO_ESSENCE_ACTOR
        and has_auto_review_finding
        and automatic_recovery_due_at(philosophy) is not None
        and created_at is not None
        and updated_at is not None
        and created_at == updated_at
        and getattr(philosophy, "reviewed_by", None) is None
        and getattr(philosophy, "approved_at", None) is None
    )

def _split_stale_error_sources(
    sources: list[HospitalSourceAsset],
    now: datetime,
) -> tuple[list[HospitalSourceAsset], list[HospitalSourceAsset]]:
    """ERROR 필수 자료를 합성 입력에서 분리한다.

    ERROR는 자동 재시도가 없는 종착지다. 다른 필수 자료가 이미 처리됐다면 1시간 유예 뒤에,
    ERROR 자료뿐이면 6시간 뒤에 뺀다(곧 고쳐질 자료를 위해). 자료 상태는 그대로 두고
    제외 사실만 gap으로 남는다. 안정 base 규칙상 제외된 자료는 나중에 고쳐져도 재온보딩
    경로로만 반영된다.
    세 호출부(필요 판정·진입·writeback 재검사)가 같은 `now`로 같은 답을 낸다.
    """

    has_processed = any(
        _status_value(source.status) == SourceStatus.PROCESSED.value for source in sources
    )
    usable: list[HospitalSourceAsset] = []
    stale: list[HospitalSourceAsset] = []
    for source in sources:
        if _status_value(source.status) == SourceStatus.ERROR.value:
            since = (
                _as_utc(getattr(source, "updated_at", None))
                or _as_utc(getattr(source, "processed_at", None))
                or _as_utc(getattr(source, "created_at", None))
            )
            wait = ERROR_SOURCE_EXCLUSION_GRACE if has_processed else ERROR_SOURCE_EXCLUSION_AFTER
            if since is not None and now - since >= wait:
                stale.append(source)
                continue
        usable.append(source)
    return usable, stale


def _excluded_error_source_gaps(sources: list[HospitalSourceAsset]) -> list[dict[str, Any]]:
    return [
        {
            "field": EXCLUDED_ERROR_SOURCE_GAP_FIELD,
            "reason": (
                f"자료 '{(source.title or str(source.id))[:120]}'의 처리가 실패해 "
                "이번 근거에서 제외했습니다. 자료를 고친 뒤 반영하려면 다시 온보딩해 주세요."
            ),
            "source_asset_id": str(source.id),
        }
        for source in sources
    ]

def _critical_losses(
    previous: HospitalContentPhilosophy | None,
    payload: dict[str, Any],
) -> list[str]:
    if previous is None:
        return []
    findings: list[str] = []
    for field in (
        "positioning_statement",
        "doctor_voice",
        "patient_promise",
        "must_use_messages",
        "treatment_narratives",
    ):
        if list(_iter_text(getattr(previous, field, None))) and not list(
            _iter_text(payload.get(field))
        ):
            findings.append(f"기존 승인본의 {field} 근거가 새 후보에서 사라졌습니다.")
    return findings


def _current_grounded_ids(
    philosophy: HospitalContentPhilosophy,
    field: str,
    valid_note_ids: set[str],
) -> list[str]:
    evidence_map = philosophy.evidence_map or {}
    raw_ids = evidence_map.get(field) if isinstance(evidence_map, dict) else None
    values = raw_ids if isinstance(raw_ids, list) else [raw_ids]
    return [str(item) for item in values if item and str(item) in valid_note_ids]


def _merge_unique(previous: object, candidate: object, *, limit: int = 24) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    previous_items = previous if isinstance(previous, list) else []
    candidate_items = candidate if isinstance(candidate, list) else []
    for item in [*previous_items, *candidate_items]:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(copy.deepcopy(item))
        if len(merged) >= limit:
            break
    return merged


def _without_forbidden_positive_items(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [
        copy.deepcopy(item)
        for item in value
        if not check_forbidden(" ".join(_iter_text(item)))
    ]

def _next_version(db: Session, hospital_id: uuid.UUID) -> int:
    value = db.scalar(
        select(func.max(HospitalContentPhilosophy.version)).where(
            HospitalContentPhilosophy.hospital_id == hospital_id
        )
    )
    return int(value or 0) + 1


def _essence_refresh_key(snapshot_hash: str, previous_id: uuid.UUID | None) -> str:
    return f"{snapshot_hash}:{previous_id or 'initial'}"


def _claim_retry_due_at(run: OperationRun | None) -> datetime | None:
    """같은 입력을 다시 살 수 있는 가장 이른 시각. None이면 지금 바로.

    시도 횟수에 따라 15분 × 2^attempts(최대 24시간)로 물러선다. run의 신원에는 자료
    snapshot과 직전 승인본이 들어 있으므로, 입력이 바뀌면 새 run이 되어 백오프가 붙지
    않는다 — 결정적으로 실패하는 병원만 조용해진다.
    """

    if run is None:
        return None
    attempts = int(run.attempt_count or 0)
    if attempts <= 0:
        return None
    last_at = (
        _as_utc(run.completed_at)
        or _as_utc(run.lease_expires_at)
        or _as_utc(run.started_at)
        or _as_utc(run.requested_at)
    )
    if last_at is None:
        return None
    backoff = min(
        _CLAIM_RETRY_BASE_BACKOFF * (2 ** min(attempts, 10)),
        _CLAIM_RETRY_MAX_BACKOFF,
    )
    return last_at + backoff


def _claim_backoff_active(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    snapshot_hash: str,
    previous_id: uuid.UUID | None,
    now: datetime,
) -> bool:
    run = db.scalar(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == _ESSENCE_REFRESH_OPERATION,
            OperationRun.idempotency_key == _essence_refresh_key(snapshot_hash, previous_id),
        )
    )
    due_at = _claim_retry_due_at(run)
    return due_at is not None and now < due_at


def _claim_essence_refresh(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    snapshot_hash: str,
    previous_id: uuid.UUID | None,
    claim_token: str,
) -> str:
    """Persist an input-bound lease, then commit so provider calls hold no DB lock.

    Returns ``"CLAIMED"``, ``"ACTIVE"`` (someone else holds the lease) or
    ``"DEFERRED"`` (the same input failed recently and is still backing off).
    """

    key = _essence_refresh_key(snapshot_hash, previous_id)
    run = db.scalar(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == _ESSENCE_REFRESH_OPERATION,
            OperationRun.idempotency_key == key,
        )
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        run is not None
        and run.state
        in {
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        }
        and run.lease_expires_at is not None
        and run.lease_expires_at > now
    ):
        db.rollback()
        return "ACTIVE"
    due_at = _claim_retry_due_at(run)
    if due_at is not None and now < due_at:
        # 같은 입력으로 방금 실패했다. 백오프가 끝날 때까지 합성·검수를 사지 않는다.
        db.rollback()
        return "DEFERRED"
    if run is None:
        run = OperationRun(
            hospital_id=hospital_id,
            operation_type=_ESSENCE_REFRESH_OPERATION,
            idempotency_key=key,
            total_count=1,
            request_payload={},
            requested_at=now,
        )
        db.add(run)
    run.state = OperationRunState.RUNNING
    run.started_at = run.started_at or now
    run.completed_at = None
    run.success_count = 0
    run.failure_count = 0
    run.skipped_count = 0
    run.attempt_count = int(run.attempt_count or 0) + 1
    run.lease_owner = claim_token
    run.lease_expires_at = now + _ESSENCE_REFRESH_LEASE
    run.request_payload = {
        "source_snapshot_hash": snapshot_hash,
        "previous_philosophy_id": str(previous_id) if previous_id else None,
    }
    run.safe_error_code = None
    run.safe_error_message = None
    db.commit()
    return "CLAIMED"


def _essence_refresh_claim_matches(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    snapshot_hash: str,
    previous_id: uuid.UUID | None,
    claim_token: str,
) -> OperationRun | None:
    key = f"{snapshot_hash}:{previous_id or 'initial'}"
    run = db.scalar(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == _ESSENCE_REFRESH_OPERATION,
            OperationRun.idempotency_key == key,
        )
        .with_for_update()
    )
    if (
        run is None
        or run.state != OperationRunState.RUNNING
        or run.lease_owner != claim_token
        or (run.request_payload or {}).get("source_snapshot_hash") != snapshot_hash
        or (run.request_payload or {}).get("previous_philosophy_id")
        != (str(previous_id) if previous_id else None)
    ):
        return None
    return run


def release_essence_refresh_claim(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    claim_token: str,
    error_code: str,
    error_message: str,
) -> bool:
    """Rearm a failed provider attempt so a new retry epoch can claim it."""

    run = db.scalar(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == _ESSENCE_REFRESH_OPERATION,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == claim_token,
        )
        .with_for_update()
    )
    if run is None:
        return False
    run.state = OperationRunState.REQUESTED
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = error_code
    run.safe_error_message = error_message[:500]
    db.commit()
    return True


def _finish_essence_refresh_claim(
    run: OperationRun,
    *,
    state: OperationRunState,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    run.state = state
    run.success_count = 1 if state == OperationRunState.SUCCEEDED else 0
    run.skipped_count = 1 if state == OperationRunState.CANCELLED else 0
    run.failure_count = 1 if state == OperationRunState.FAILED else 0
    run.completed_at = datetime.now(timezone.utc)
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = error_code
    run.safe_error_message = error_message



def _carry_forward_baseline(
    previous: HospitalContentPhilosophy | None,
    payload: dict[str, Any],
    notes: list[HospitalSourceEvidenceNote],
) -> dict[str, Any]:
    """새 합성이 비운 칸만 직전 판의 근거 있는 값으로 채운다.

    새 합성이 우선이다 — 설문이 올라와 다시 만든 판의 목소리·포지셔닝을 직전 판이 덮으면 안 된다
    (D34 서술 우선순위). 목록 필드는 새 항목 뒤에 직전 판의 근거 있는 항목을 잇는다. 근거 노트가
    더 이상 없는 값은 옮기지 않는다(정리 단계가 어차피 버린다).
    """

    result = copy.deepcopy(payload)
    if previous is None:
        return result
    evidence_map = result.get("evidence_map")
    if not isinstance(evidence_map, dict):
        evidence_map = {}
        result["evidence_map"] = evidence_map
    valid_note_ids = {str(note.id) for note in notes}

    for field in ("positioning_statement", "doctor_voice", "patient_promise"):
        if str(result.get(field) or "").strip():
            continue
        ids = _current_grounded_ids(previous, field, valid_note_ids)
        value = getattr(previous, field, None)
        if value and ids and not check_forbidden(" ".join(_iter_text(value))):
            result[field] = copy.deepcopy(value)
            evidence_map[field] = ids

    for field in (
        "content_principles",
        "tone_guidelines",
        "must_use_messages",
        "treatment_narratives",
        "avoid_messages",
        "medical_ad_risk_rules",
    ):
        ids = _current_grounded_ids(previous, field, valid_note_ids)
        previous_items = (
            getattr(previous, field, None)
            if field in {"avoid_messages", "medical_ad_risk_rules"}
            else _without_forbidden_positive_items(getattr(previous, field, None))
        )
        if not previous_items or not ids:
            continue
        result[field] = _merge_unique(result.get(field), previous_items)
        candidate_ids = evidence_map.get(field)
        candidate_ids = candidate_ids if isinstance(candidate_ids, list) else []
        evidence_map[field] = list(
            dict.fromkeys([*[str(item) for item in candidate_ids], *ids])
        )

    local_ids = _current_grounded_ids(previous, "local_context", valid_note_ids)
    previous_local = previous.local_context or {}
    candidate_local = result.get("local_context") or {}
    if local_ids and isinstance(previous_local, dict) and isinstance(candidate_local, dict):
        merged_local = copy.deepcopy(candidate_local)
        for field in ("region_terms", "local_patient_context"):
            merged_local[field] = _merge_unique(
                candidate_local.get(field), previous_local.get(field), limit=20
            )
        candidate_local_ids = candidate_local.get("evidence_note_ids")
        candidate_local_ids = (
            candidate_local_ids if isinstance(candidate_local_ids, list) else []
        )
        merged_local["evidence_note_ids"] = list(
            dict.fromkeys([*[str(item) for item in candidate_local_ids], *local_ids])
        )
        result["local_context"] = merged_local
        evidence_map["local_context"] = merged_local["evidence_note_ids"]
    return result


def _metadata(philosophy: HospitalContentPhilosophy | None) -> dict[str, Any]:
    raw = getattr(philosophy, "synthesis_metadata", None) if philosophy is not None else None
    return raw if isinstance(raw, dict) else {}


def is_automatic_essence(philosophy: HospitalContentPhilosophy | None) -> bool:
    return _metadata(philosophy).get("mode") == AUTOMATIC_ESSENCE_MODE


def _processed(sources: list[HospitalSourceAsset]) -> list[HospitalSourceAsset]:
    return [
        source
        for source in sources
        if _status_value(source.status) == SourceStatus.PROCESSED.value
    ]


def _trigger_for(
    previous: HospitalContentPhilosophy | None,
    sources: list[HospitalSourceAsset],
    now: datetime,
) -> str | None:
    """다시 합성할 이유. None이면 지금 판을 그대로 쓴다(모듈 문서의 T1~T6)."""

    if previous is None:
        return TRIGGER_NO_ESSENCE
    if authority_refresh_required(previous):
        return TRIGGER_AUTHORITY_CHANGE
    approved_at = _as_utc(getattr(previous, "approved_at", None))
    if approved_at is not None and now - approved_at < AUTOMATIC_RESYNTHESIS_DEBOUNCE:
        return None
    processed = _processed(sources)
    metadata = _metadata(previous)
    automatic = metadata.get("mode") == AUTOMATIC_ESSENCE_MODE
    input_hashes = metadata.get("input_hashes") if isinstance(metadata.get("input_hashes"), dict) else {}
    excluded_hashes = {
        str(item.get("source_id")): item.get("content_hash")
        for item in metadata.get("excluded_inputs") or []
        if isinstance(item, dict)
    }
    previous_source_ids = {
        str(value) for value in (getattr(previous, "source_asset_ids", None) or [])
    }

    for source in processed:
        if source_kind(source) != SourceType.INTERVIEW.value:
            continue
        source_id = str(source.id)
        if automatic:
            if source.content_hash in {input_hashes.get(source_id), excluded_hashes.get(source_id)}:
                continue
            return TRIGGER_SURVEY_CHANGED
        # 이 컬럼 이전의 승인본: 그 판 승인 뒤에 처리된 설문만 새 자동 판을 부른다.
        if source_id in previous_source_ids:
            continue
        seen_at = _as_utc(source.processed_at) or _as_utc(source.created_at)
        if approved_at is None or (seen_at is not None and seen_at > approved_at):
            return TRIGGER_SURVEY_CHANGED

    if not automatic:
        return None
    profile_source_id = metadata.get("profile_facts_source_id")
    if profile_source_id:
        current = next(
            (source for source in processed if str(source.id) == str(profile_source_id)), None
        )
        if current is None or current.content_hash != input_hashes.get(str(profile_source_id)):
            return TRIGGER_PROFILE_FACTS_CHANGED
    pending = {str(value) for value in metadata.get("pending_inputs") or []}
    if pending and any(str(source.id) in pending for source in processed):
        return TRIGGER_PENDING_INPUTS_PROCESSED
    if (
        metadata.get("fallback")
        and int(metadata.get("fallback_upgrade_attempts") or 0) < FALLBACK_UPGRADE_MAX_ATTEMPTS
        and approved_at is not None
        and now - approved_at >= FALLBACK_UPGRADE_AFTER
    ):
        return TRIGGER_FALLBACK_UPGRADE
    return None


def essence_refresh_trigger(
    db: Session, hospital_id: uuid.UUID, *, now: datetime | None = None
) -> str | None:
    """잠금 없이 읽는 트리거 판정(정기 조정·백필 미리보기). 쓰기 경로가 잠금 아래서 다시 본다."""

    observed = now or datetime.now(timezone.utc)
    sources, _stale = _split_stale_error_sources(_required_sources(db, hospital_id), observed)
    return _trigger_for(_approved_unlocked(db, hospital_id), sources, observed)


def essence_refresh_needed(db: Session, hospital_id: uuid.UUID) -> bool:
    """정기 조정이 이 병원의 자동 요약 태스크를 디스패치해야 하는가."""

    now = datetime.now(timezone.utc)
    previous = _approved_unlocked(db, hospital_id)
    sources, _stale = _split_stale_error_sources(_required_sources(db, hospital_id), now)
    if _trigger_for(previous, sources, now) is None:
        return False
    processed = _processed(sources)
    usable, _excluded = partition_automatic_inputs(processed)
    if not usable:
        return False
    return not _claim_backoff_active(
        db,
        hospital_id=hospital_id,
        snapshot_hash=compute_sources_snapshot_hash(processed),
        previous_id=previous.id if previous else None,
        now=now,
    )


def _only_avoid_message_drift(item: ContentItem) -> bool:
    summary = getattr(item, "essence_check_summary", None) or {}
    findings = [str(finding) for finding in summary.get("findings") or []]
    return bool(findings) and all(
        finding.startswith("병원별 avoid message와 충돌") for finding in findings
    )


def _rescreen_content(
    db: Session,
    hospital_id: uuid.UUID,
    philosophy: HospitalContentPhilosophy,
) -> dict[str, int]:
    """새 판으로 병원의 글을 다시 판정한다. 판 교체가 공개 글을 숨기지 않는다(E7).

    - 근거 철회(authority_change)로 이미 내려간 글은 그대로 둔다(사람의 철회 결정).
    - 공개 글: 의존 자료가 새 입력에서 빠졌거나 병원별 avoid 문구만 걸렸다면 공개를 유지하고 그
      사실만 요약에 남긴다. 의료광고 금지 표현은 판과 무관하게 공개 검사가 숨긴다.
    - 공개 전 글: 종전대로 다시 판정하고, 의존 자료가 빠진 글은 재생성 경로로 보낸다.
    """

    items = list(
        db.execute(
            select(ContentItem)
            .where(
                ContentItem.hospital_id == hospital_id,
                ContentItem.body.isnot(None),
            )
            .with_for_update(of=ContentItem)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    observed = datetime.now(timezone.utc).isoformat()
    counts = {"total": 0, "aligned": 0, "needs_review": 0, "public_drift_kept": 0}
    for item in items:
        counts["total"] += 1
        if (item.essence_check_summary or {}).get("authority_change"):
            counts["needs_review"] += 1
            continue
        published = enum_value(getattr(item, "status", None)) == "PUBLISHED"
        removed: tuple[str, ...] = ()
        if published:
            removed = removed_generation_source_ids(item, philosophy)
        elif mark_removed_source_dependency(item, philosophy):
            counts["needs_review"] += 1
            continue
        essence_status = apply_essence_revalidation(item, philosophy)
        kept = False
        if published and essence_status != ESSENCE_STATUS_ALIGNED and _only_avoid_message_drift(item):
            summary = dict(item.essence_check_summary or {})
            summary["avoid_message_drift"] = {
                "findings": list(summary.get("findings") or []),
                "philosophy_id": str(philosophy.id),
                "observed_at": observed,
            }
            summary["blocking"] = False
            summary["findings"] = []
            item.essence_check_summary = summary
            item.essence_status = ESSENCE_STATUS_ALIGNED
            essence_status = ESSENCE_STATUS_ALIGNED
            kept = True
        if published and removed:
            summary = dict(item.essence_check_summary or {})
            summary["source_dependency_drift"] = {
                "removed_source_asset_ids": list(removed),
                "philosophy_id": str(philosophy.id),
                "observed_at": observed,
            }
            item.essence_check_summary = summary
            kept = True
        if kept:
            counts["public_drift_kept"] += 1
        counts["aligned" if essence_status == ESSENCE_STATUS_ALIGNED else "needs_review"] += 1
    return counts


def _normalize_synthesis(outcome: Any) -> tuple[dict[str, Any], str]:
    if isinstance(outcome, tuple):
        payload, mode = outcome
        return dict(payload), str(mode)
    # 주입된 합성기(테스트·도구)는 경로를 말하지 않는다 — 모델 합성으로 본다.
    return dict(outcome), SYNTHESIS_MODE_LLM


def _archive_competitors(
    db: Session,
    hospital_id: uuid.UUID,
    current_previous: HospitalContentPhilosophy | None,
) -> tuple[list[str], list[str]]:
    """새 판을 올리기 전에 직전 판과 남은 초안을 보관한다(병원당 APPROVED·base 한 행)."""

    if current_previous is not None:
        current_previous.status = PhilosophyStatus.ARCHIVED
        current_previous.is_base = False
    stale_bases = list(
        db.execute(
            select(HospitalContentPhilosophy)
            .where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.is_base.is_(True),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for stale_base in stale_bases:
        stale_base.is_base = False
    drafts = list(
        db.execute(
            select(HospitalContentPhilosophy)
            .where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.status == PhilosophyStatus.DRAFT,
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    # 승인 단계가 없으므로 남은 초안(옛 보류 초안·사람이 복사한 초안)은 갈 곳이 없다.
    for draft in drafts:
        draft.status = PhilosophyStatus.ARCHIVED
    db.flush()
    return [str(row.id) for row in stale_bases], [str(row.id) for row in drafts]


def refresh_essence_snapshot(
    db: Session,
    hospital_id: uuid.UUID,
    *,
    synthesizer: Callable[..., Any] = synthesize_philosophy_with_mode,
    claim_token: str | None = None,
) -> EssenceRefreshResult:
    """트리거가 있으면 자동 요약을 합성·정리해 곧바로 적용한다(승인 단계 없음, D33)."""

    acquire_hospital_advisory_lock_sync(db, hospital_id)
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        return EssenceRefreshResult(EssenceRefreshStatus.NOT_FOUND, hospital_id)
    previous = _approved(db, hospital_id)
    previous_id = previous.id if previous else None
    now = datetime.now(timezone.utc)
    # 제외 대상 ERROR 필수 자료(D26)는 입력과 완결성 판단에서 빠진다. 자료 상태는 그대로다.
    sources, excluded_error_sources = _split_stale_error_sources(
        _required_sources(db, hospital_id), now
    )
    processed = _processed(sources)
    snapshot_hash = compute_sources_snapshot_hash(processed)
    trigger = _trigger_for(previous, sources, now)
    if trigger is None:
        return EssenceRefreshResult(
            EssenceRefreshStatus.UP_TO_DATE,
            hospital_id,
            snapshot_hash=snapshot_hash,
            philosophy_id=previous_id,
            previous_philosophy_id=previous_id,
        )
    usable, excluded_inputs = partition_automatic_inputs(processed)
    if not usable:
        # 처리된 자료가 아직 없다(또는 모두 오염 입력). 자료가 처리되면 다음 틱이 다시 본다.
        return EssenceRefreshResult(
            EssenceRefreshStatus.WAITING_FOR_SOURCES,
            hospital_id,
            snapshot_hash=snapshot_hash,
            previous_philosophy_id=previous_id,
            trigger=trigger,
        )
    # 자료 snapshot과 같은 잠금 안에서 읽어야 CAS가 성립한다.
    noise_hash = load_evidence_noise_hash_sync(db, hospital_id)
    notes = _notes_for_sources(db, hospital_id, [source.id for source in usable])
    pending_inputs = [
        str(source.id)
        for source in sources
        if _status_value(source.status) != SourceStatus.PROCESSED.value
    ]

    refresh_claim_token = claim_token or str(uuid.uuid4())
    claim_status = _claim_essence_refresh(
        db,
        hospital_id=hospital_id,
        snapshot_hash=snapshot_hash,
        previous_id=previous_id,
        claim_token=refresh_claim_token,
    )
    if claim_status != "CLAIMED":
        return EssenceRefreshResult(
            EssenceRefreshStatus.DEFERRED
            if claim_status == "DEFERRED"
            else EssenceRefreshStatus.SNAPSHOT_CHANGED,
            hospital_id,
            snapshot_hash=snapshot_hash,
            previous_philosophy_id=previous_id,
            trigger=trigger,
            reason="CLAIM_BACKOFF" if claim_status == "DEFERRED" else "CLAIM_ACTIVE",
        )

    # 공급자 호출은 claim 커밋 뒤라 DB 잠금을 들고 있지 않다.
    fallback_reason: str | None = None
    try:
        payload, synthesis_mode = _normalize_synthesis(
            synthesizer(hospital, usable, notes, operator_note=None)
        )
    except EssenceSynthesisDeferred as exc:
        if trigger not in _GENERATION_BLOCKING_TRIGGERS:
            release_essence_refresh_claim(
                db,
                hospital_id=hospital_id,
                claim_token=refresh_claim_token,
                error_code="COST_BLOCKED",
                error_message=str(exc) or "Essence synthesis cost blocked",
            )
            return EssenceRefreshResult(
                EssenceRefreshStatus.DEFERRED,
                hospital_id,
                snapshot_hash=snapshot_hash,
                previous_philosophy_id=previous_id,
                trigger=trigger,
                reason="COST_BLOCKED",
            )
        # 판이 없거나 철회돼 생성이 멈춰 있다 — 비용 없이 만드는 결정적 판이라도 지금 적용한다.
        payload, synthesis_mode = _normalize_synthesis(
            synthesize_philosophy_with_mode(hospital, usable, notes, None, use_llm=False)
        )
        fallback_reason = "COST_BLOCKED"
    fallback = synthesis_mode == SYNTHESIS_MODE_DETERMINISTIC
    if fallback and fallback_reason is None:
        fallback_reason = "PROVIDER_FALLBACK"
    if (
        fallback
        and trigger not in _GENERATION_BLOCKING_TRIGGERS
        and not _metadata(previous).get("fallback")
    ):
        # 폴백 판으로 모델이 만든 판을 덮지 않는다. 백오프 뒤 다시 시도한다.
        release_essence_refresh_claim(
            db,
            hospital_id=hospital_id,
            claim_token=refresh_claim_token,
            error_code="ESSENCE_PROVIDER_FALLBACK",
            error_message="모델 합성이 실패해 기존 판을 유지했습니다.",
        )
        return EssenceRefreshResult(
            EssenceRefreshStatus.DEFERRED,
            hospital_id,
            snapshot_hash=snapshot_hash,
            previous_philosophy_id=previous_id,
            trigger=trigger,
            reason="PROVIDER_FALLBACK",
        )

    # 철회된 사실은 새 판으로 옮기지 않는다.
    baseline = None if trigger == TRIGGER_AUTHORITY_CHANGE else previous
    payload = _carry_forward_baseline(baseline, payload, notes)
    payload, report = prune_automatic_candidate(payload, notes, usable)
    payload = apply_mandatory_safety_policy(payload)
    payload["source_asset_ids"] = [str(source.id) for source in usable]
    payload["source_snapshot_hash"] = snapshot_hash
    if excluded_error_sources:
        gaps = list(payload.get("unsupported_gaps") or [])
        payload["unsupported_gaps"] = gaps + [
            gap for gap in _excluded_error_source_gaps(excluded_error_sources) if gap not in gaps
        ]
    lost_fields = _critical_losses(baseline, payload)

    # 조건부 writeback만을 위해 다시 잠근다.
    acquire_hospital_advisory_lock_sync(db, hospital_id)
    db.expire_all()
    claim_run = _essence_refresh_claim_matches(
        db,
        hospital_id=hospital_id,
        snapshot_hash=snapshot_hash,
        previous_id=previous_id,
        claim_token=refresh_claim_token,
    )
    if claim_run is None:
        db.rollback()
        return EssenceRefreshResult(
            EssenceRefreshStatus.SNAPSHOT_CHANGED,
            hospital_id,
            snapshot_hash=snapshot_hash,
            previous_philosophy_id=previous_id,
            trigger=trigger,
        )
    current_sources, _current_excluded = _split_stale_error_sources(
        _required_sources(db, hospital_id), now
    )
    current_previous = _approved(db, hospital_id)
    if (
        compute_sources_snapshot_hash(_processed(current_sources)) != snapshot_hash
        or load_evidence_noise_hash_sync(db, hospital_id) != noise_hash
        or (current_previous.id if current_previous else None) != previous_id
    ):
        _finish_essence_refresh_claim(
            claim_run,
            state=OperationRunState.CANCELLED,
            error_code="ESSENCE_SNAPSHOT_CHANGED",
            error_message="합성 중 자료 또는 적용 중인 판이 바뀌었습니다.",
        )
        db.commit()
        return EssenceRefreshResult(
            EssenceRefreshStatus.SNAPSHOT_CHANGED,
            hospital_id,
            snapshot_hash=snapshot_hash,
            previous_philosophy_id=previous_id,
            trigger=trigger,
        )

    stale_base_ids, archived_draft_ids = _archive_competitors(db, hospital_id, current_previous)
    applied_at = datetime.now(timezone.utc)
    profile_source = next((source for source in usable if is_profile_facts_source(source)), None)
    previous_metadata = _metadata(current_previous)
    metadata = {
        "mode": AUTOMATIC_ESSENCE_MODE,
        "trigger": trigger,
        "synthesis": synthesis_mode,
        "fallback": fallback,
        "fallback_reason": fallback_reason if fallback else None,
        "fallback_upgrade_attempts": (
            int(previous_metadata.get("fallback_upgrade_attempts") or 0) + 1
            if trigger == TRIGGER_FALLBACK_UPGRADE
            else 0
        ),
        "input_hashes": {str(source.id): source.content_hash for source in usable},
        "source_kinds": {str(source.id): source_kind(source) for source in usable},
        "profile_facts_source_id": str(profile_source.id) if profile_source else None,
        "excluded_inputs": excluded_inputs
        + [
            {
                "source_id": str(source.id),
                "reason": EXCLUDED_PROCESSING_ERROR,
                "content_hash": source.content_hash,
            }
            for source in excluded_error_sources
        ],
        "pending_inputs": pending_inputs,
        "dropped": report.dropped,
        "dropped_total": report.dropped_total,
        "conflicts": report.conflicts,
        "must_use": report.must_use,
        "lost_fields": lost_fields,
        "previous_philosophy_id": str(current_previous.id) if current_previous else None,
        "applied_at": applied_at.isoformat(),
    }
    candidate = HospitalContentPhilosophy(
        hospital_id=hospital_id,
        version=_next_version(db, hospital_id),
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        created_by=AUTOMATIC_ESSENCE_ACTOR,
        reviewed_by=AUTOMATIC_ESSENCE_ACTOR,
        approved_at=applied_at,
        approval_note=AUTOMATIC_APPROVAL_NOTE,
        evidence_noise_hash=noise_hash,
        synthesis_metadata=metadata,
        **payload,
    )
    db.add(candidate)
    db.flush()
    rescreened = _rescreen_content(db, hospital_id, candidate)
    write_audit_log_sync(
        db,
        action="auto_apply_philosophy",
        hospital_id=hospital_id,
        actor=AUTOMATIC_ESSENCE_ACTOR,
        target_type="philosophy",
        target_id=candidate.id,
        detail={
            "trigger": trigger,
            "previous_philosophy_id": metadata["previous_philosophy_id"],
            "previous_version": current_previous.version if current_previous else None,
            "new_philosophy_id": str(candidate.id),
            "new_version": candidate.version,
            "source_snapshot_hash": snapshot_hash,
            "source_asset_ids": payload["source_asset_ids"],
            "synthesis": synthesis_mode,
            "fallback": fallback,
            "excluded_inputs": metadata["excluded_inputs"],
            "dropped_total": report.dropped_total,
            "conflicts": len(report.conflicts),
            "must_use_mode": report.must_use.get("mode"),
            "archived_draft_ids": archived_draft_ids,
            "cleared_stale_base_ids": stale_base_ids,
            "content_rescreened": rescreened,
        },
    )
    enqueue_public_surface_intent(db, hospital)
    _finish_essence_refresh_claim(claim_run, state=OperationRunState.SUCCEEDED)
    db.commit()
    return EssenceRefreshResult(
        EssenceRefreshStatus.APPLIED,
        hospital_id,
        snapshot_hash=snapshot_hash,
        philosophy_id=candidate.id,
        previous_philosophy_id=current_previous.id if current_previous else None,
        trigger=trigger,
        should_revalidate_site=(
            hospital.status == HospitalStatus.ACTIVE and bool(hospital.site_live)
        ),
        fallback=fallback,
        dropped_count=report.dropped_total,
    )


__all__ = (
    "AUTOMATIC_ESSENCE_ACTOR",
    "AUTOMATIC_ESSENCE_MODE",
    "AUTO_ESSENCE_ACTOR",
    "AUTO_ESSENCE_MAX_RECOVERY_CYCLES",
    "AUTO_ESSENCE_RECOVERY_BACKOFFS",
    "AUTOMATIC_RESYNTHESIS_DEBOUNCE",
    "ERROR_SOURCE_EXCLUSION_AFTER",
    "ERROR_SOURCE_EXCLUSION_GRACE",
    "EssenceRefreshResult",
    "EssenceRefreshStatus",
    "EssenceSynthesisDeferred",
    "automatic_recovery_backoff",
    "automatic_recovery_due_at",
    "automatic_recovery_owns_draft",
    "effective_recovery_cycle",
    "essence_refresh_needed",
    "essence_refresh_trigger",
    "is_automatic_essence",
    "partition_automatic_inputs",
    "refresh_essence_snapshot",
    "release_essence_refresh_claim",
)
