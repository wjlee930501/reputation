"""저장된 생성 시도 기록을 **읽기 전용**으로 해석하는 한 곳.

`essence_check_summary["generation_attempt"]`는 야간 생성·복구 스윕이 쓰는 내구 조각이다.
발행 게이트(`services/content_publication`)는 그 조각을 보고 "대표 이미지를 아직
자동 복구가 소유하고 있는가"를 판정해야 하는데, 쓰기 경로(`workers/tasks`)를 import하면
서비스 계층이 워커 전체를 끌어온다. 그래서 해석만 여기에 둔다 — 쓰기는 그대로 워커가 한다.

저장된 조각에 모르는 필드가 더 있어도 무시한다. 이 파일은 스키마를 강제하지 않는다.

공개 API (호출부가 의존해도 되는 것):

- ``GENERATION_ATTEMPT_KEY`` — `essence_check_summary` 안의 조각 키.
- ``read_generation_attempt(item)`` — 조각의 사본(dict). 없거나 모양이 다르면 빈 dict.
- ``image_attempts_exhausted_today(item, now=None)`` — 오늘(KST) 이 글의 대표 이미지
  자동 시도가 더 남아 있지 않은가. 야간 생성이 "이미지를 포기하고 같은 병원의 인증된
  이미지를 빌릴 시점인가"를 이 값으로 판정한다.
- ``IMAGE_ATTEMPT_REASONS`` / ``IMAGE_ATTEMPT_TERMINAL_REASONS`` — 위 판정이 쓰는 원인 집합.
- ``GENERATION_LADDER_KEYS`` / ``released_generation_attempt(summary)`` — 저장된 본문이
  사라졌을 때 억제만 풀고 예산 사다리는 남기는 변환. 순수 함수라 워커와 Admin API가
  같은 규칙을 쓴다.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    environment_attempt_period,
    stored_attempt_period,
)

GENERATION_ATTEMPT_KEY = "generation_attempt"
GENERATION_BUDGET_KEY = "budget"
GENERATION_BUDGET_VERSION = 2
WRITER_RESULT_LIMIT = 6
REVIEWER_RESULT_LIMIT = 6
TOPIC_LIMIT = 2
TOPIC_SWAP_LIMIT = 1
SEMANTIC_TRANSPORT_LIMIT = 3
TEXT_HTTP_ATTEMPT_LIMIT = 36
IMAGE_HTTP_ATTEMPT_LIMIT = 12

AcquisitionKind = Literal["WRITER", "REVIEWER", "IMAGE"]
AcquisitionOutcome = Literal["RESULT", "ERROR", "UNKNOWN"]


class GenerationBudgetUnknown(RuntimeError):
    """Raised when a legacy slot has no trustworthy lifetime-spend counters."""


class GenerationBudgetExceeded(RuntimeError):
    """Raised before provider I/O when a slot lifetime ceiling is exhausted."""


@dataclass(frozen=True, slots=True)
class GenerationBudgetSnapshot:
    version: int
    legacy_state: str
    writer_results: int
    reviewer_results: int
    topics: tuple[str, ...]
    topic_swaps: int
    text_http_attempts: int
    text_http_reserved: int
    image_http_attempts: int
    image_http_reserved: int
    reset_record: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AcquisitionReservation:
    acquisition_id: str
    kind: AcquisitionKind
    cached_payload: dict[str, Any] | None


def _zero_budget(topic_id: str | None = None) -> dict[str, Any]:
    return {
        "version": GENERATION_BUDGET_VERSION,
        "legacy_state": "KNOWN_ZERO",
        "writer_results": 0,
        "reviewer_results": 0,
        "topics": [topic_id] if topic_id else [],
        "topic_swaps": 0,
        "text_http_attempts": 0,
        "text_http_reserved": 0,
        "image_http_attempts": 0,
        "image_http_reserved": 0,
        "acquisitions": [],
        "reset_record": None,
    }


def fresh_generation_attempt(*, topic_id: str | None = None) -> dict[str, Any]:
    """Return the only valid known-zero marker for a genuinely new content slot."""

    return {GENERATION_BUDGET_KEY: _zero_budget(topic_id)}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _legacy_budget(attempt: dict[str, Any]) -> dict[str, Any]:
    raw_count = attempt.get("provider_attempt_count", attempt.get("attempt_count"))
    count = _nonnegative_int(raw_count)
    if count is None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    reason = str(attempt.get("reason") or "")
    budget = _zero_budget(None)
    budget["legacy_state"] = "KNOWN_COUNTERS"
    if reason in IMAGE_ATTEMPT_REASONS:
        budget["image_http_attempts"] = min(count, IMAGE_HTTP_ATTEMPT_LIMIT)
    else:
        # The old counter did not distinguish writer, reviewer, or transport. Charging
        # it to both semantic lanes is deliberately conservative and never grants a
        # legacy slot more allowance than the stored evidence supports.
        budget["writer_results"] = min(count, WRITER_RESULT_LIMIT)
        budget["reviewer_results"] = min(count, REVIEWER_RESULT_LIMIT)
        budget["text_http_attempts"] = min(count, TEXT_HTTP_ATTEMPT_LIMIT)
    budget["legacy_baseline"] = {
        "source": "LEGACY_COUNTERS",
        "writer_results": budget["writer_results"],
        "reviewer_results": budget["reviewer_results"],
        "text_http_attempts": budget["text_http_attempts"],
        "image_http_attempts": budget["image_http_attempts"],
    }
    return budget


_VERSIONED_COUNTER_KEYS = (
    "writer_results",
    "reviewer_results",
    "topic_swaps",
    "text_http_attempts",
    "text_http_reserved",
    "image_http_attempts",
    "image_http_reserved",
)
_LEGACY_STATES = frozenset({"KNOWN_ZERO", "KNOWN_COUNTERS", "REPLACED"})
_ACQUISITION_KINDS = frozenset({"WRITER", "REVIEWER", "IMAGE"})
_ACQUISITION_STATUSES = frozenset({"RESERVED", "RESULT", "ERROR", "UNKNOWN"})


def _strict_nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _valid_reset_record(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("event") == "LEGACY_BUDGET_REPLACED"
        and all(
            isinstance(value.get(key), str) and bool(value[key])
            for key in ("actor", "reason", "idempotency_key", "replaced_at")
        )
        and isinstance(value.get("previous_snapshot"), dict)
    )


def _validate_versioned_budget(raw: dict[str, Any]) -> dict[str, Any]:
    """Reject partial or malformed v2 state before it can mint fresh allowance."""

    if raw.get("version") != GENERATION_BUDGET_VERSION:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    if raw.get("legacy_state") not in _LEGACY_STATES:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    for key in _VERSIONED_COUNTER_KEYS:
        if _strict_nonnegative_int(raw.get(key)) is None:
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    topics = raw.get("topics")
    if (
        not isinstance(topics, list)
        or len(topics) > TOPIC_LIMIT
        or any(not isinstance(topic, str) or not topic for topic in topics)
        or len(set(topics)) != len(topics)
    ):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    acquisitions = raw.get("acquisitions")
    if not isinstance(acquisitions, list):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    acquisition_ids: set[str] = set()
    computed_writer_results = 0
    computed_reviewer_results = 0
    computed_text_attempts = 0
    computed_text_reserved = 0
    computed_image_attempts = 0
    computed_image_reserved = 0
    for entry in acquisitions:
        if not isinstance(entry, dict):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
        acquisition_id = entry.get("id")
        kind = entry.get("kind")
        status = entry.get("status")
        transport_reserved = _strict_nonnegative_int(entry.get("transport_reserved"))
        transport_attempts = entry.get("transport_attempts", 0)
        if (
            not isinstance(acquisition_id, str)
            or not acquisition_id
            or acquisition_id in acquisition_ids
            or kind not in _ACQUISITION_KINDS
            or status not in _ACQUISITION_STATUSES
            or transport_reserved is None
            or transport_reserved > SEMANTIC_TRANSPORT_LIMIT
            or _strict_nonnegative_int(transport_attempts) is None
            or transport_attempts > SEMANTIC_TRANSPORT_LIMIT
            or not isinstance(entry.get("reserved_at"), str)
            or ("payload" in entry and not isinstance(entry["payload"], dict))
        ):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
        acquisition_ids.add(acquisition_id)
        if status in {"RESULT", "ERROR"} and kind == "WRITER":
            computed_writer_results += 1
        if status in {"RESULT", "ERROR"} and kind == "REVIEWER":
            computed_reviewer_results += 1
        if kind == "IMAGE":
            computed_image_attempts += transport_attempts
            if status == "RESERVED":
                computed_image_reserved += transport_reserved - transport_attempts
        else:
            computed_text_attempts += transport_attempts
            if status == "RESERVED":
                computed_text_reserved += transport_reserved - transport_attempts

    baseline = raw.get("legacy_baseline")
    if raw["legacy_state"] == "KNOWN_COUNTERS":
        if (
            not isinstance(baseline, dict)
            or baseline.get("source") != "LEGACY_COUNTERS"
            or any(
                _strict_nonnegative_int(baseline.get(key)) is None
                for key in (
                    "writer_results",
                    "reviewer_results",
                    "text_http_attempts",
                    "image_http_attempts",
                )
            )
        ):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    elif baseline is not None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    else:
        baseline = {
            "writer_results": 0,
            "reviewer_results": 0,
            "text_http_attempts": 0,
            "image_http_attempts": 0,
        }

    if (
        raw["writer_results"] != baseline["writer_results"] + computed_writer_results
        or raw["reviewer_results"]
        != baseline["reviewer_results"] + computed_reviewer_results
        or raw["text_http_attempts"]
        != baseline["text_http_attempts"] + computed_text_attempts
        or raw["text_http_reserved"] != computed_text_reserved
        or raw["image_http_attempts"]
        != baseline["image_http_attempts"] + computed_image_attempts
        or raw["image_http_reserved"] != computed_image_reserved
    ):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    reset_record = raw.get("reset_record")
    if reset_record is not None and not isinstance(reset_record, dict):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    if raw["legacy_state"] == "REPLACED":
        if not _valid_reset_record(reset_record):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    elif reset_record is not None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    return dict(raw)


def _budget_dict(attempt: dict[str, Any]) -> dict[str, Any]:
    raw = attempt.get(GENERATION_BUDGET_KEY)
    if not isinstance(raw, dict):
        return _legacy_budget(attempt)
    return _validate_versioned_budget(raw)


def read_generation_budget(attempt: dict[str, Any]) -> GenerationBudgetSnapshot:
    """Parse a versioned budget or conservatively translate known legacy counters."""

    budget = _budget_dict(attempt)
    return _snapshot_from_budget(budget)


def legacy_generation_budget_replaced(summary: Any) -> bool:
    """Match the mutation gate that rejects a second authenticated replacement."""

    if not isinstance(summary, dict):
        return False
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(attempt, dict):
        return False
    budget = attempt.get(GENERATION_BUDGET_KEY)
    return isinstance(budget, dict) and _valid_reset_record(budget.get("reset_record"))


def _snapshot_from_budget(budget: dict[str, Any]) -> GenerationBudgetSnapshot:
    topics = budget["topics"]
    topic_values = tuple(topics)
    reset = budget.get("reset_record")
    return GenerationBudgetSnapshot(
        version=GENERATION_BUDGET_VERSION,
        legacy_state=str(budget["legacy_state"]),
        writer_results=budget["writer_results"],
        reviewer_results=budget["reviewer_results"],
        topics=topic_values,
        topic_swaps=budget["topic_swaps"],
        text_http_attempts=budget["text_http_attempts"],
        text_http_reserved=budget["text_http_reserved"],
        image_http_attempts=budget["image_http_attempts"],
        image_http_reserved=budget["image_http_reserved"],
        reset_record=dict(reset) if isinstance(reset, dict) else None,
    )


def _active_acquisitions(budget: dict[str, Any], kind: AcquisitionKind) -> int:
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return 0
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("kind") == kind
        and entry.get("status") == "RESERVED"
    )


def reserve_acquisition(
    attempt: dict[str, Any], *, acquisition_id: str, kind: AcquisitionKind
) -> dict[str, Any]:
    """Reserve worst-case provider attempts before I/O; safe under redelivery."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    prior = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if prior is not None:
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    snapshot = _snapshot_from_budget(budget)
    if kind == "WRITER" and (
        snapshot.writer_results + _active_acquisitions(budget, kind) >= WRITER_RESULT_LIMIT
    ):
        raise GenerationBudgetExceeded("WRITER_RESULTS_EXHAUSTED")
    if kind == "REVIEWER" and (
        snapshot.reviewer_results + _active_acquisitions(budget, kind)
        >= REVIEWER_RESULT_LIMIT
    ):
        raise GenerationBudgetExceeded("REVIEWER_RESULTS_EXHAUSTED")
    attempt_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    limit = IMAGE_HTTP_ATTEMPT_LIMIT if kind == "IMAGE" else TEXT_HTTP_ATTEMPT_LIMIT
    spent = _nonnegative_int(budget.get(spent_key)) or 0
    reserved = _nonnegative_int(budget.get(attempt_key)) or 0
    if spent + reserved + SEMANTIC_TRANSPORT_LIMIT > limit:
        raise GenerationBudgetExceeded(
            "IMAGE_HTTP_ATTEMPTS_EXHAUSTED" if kind == "IMAGE" else "TEXT_HTTP_ATTEMPTS_EXHAUSTED"
        )
    budget[attempt_key] = reserved + SEMANTIC_TRANSPORT_LIMIT
    entries.append(
        {
            "id": acquisition_id,
            "kind": kind,
            "status": "RESERVED",
            "transport_reserved": SEMANTIC_TRANSPORT_LIMIT,
            "reserved_at": datetime.now(UTC).isoformat(),
        }
    )
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def acquisition_completed(
    attempt: dict[str, Any],
    *,
    acquisition_id: str,
    transport_attempts: int,
    outcome: AcquisitionOutcome,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Checkpoint a reserved acquisition without treating ambiguous calls as free."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    index = next(
        (
            position
            for position, entry in enumerate(entries)
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if index is None:
        raise GenerationBudgetExceeded("ACQUISITION_NOT_RESERVED")
    entry = entries[index]
    if entry.get("status") != "RESERVED":
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    used = max(0, min(int(transport_attempts), SEMANTIC_TRANSPORT_LIMIT))
    kind = str(entry.get("kind") or "")
    reserved_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    recorded = _nonnegative_int(entry.get("transport_attempts")) or 0
    final_transport_attempts = max(recorded, used)
    remaining_reservation = max(0, SEMANTIC_TRANSPORT_LIMIT - recorded)
    budget[reserved_key] = max(
        0, (_nonnegative_int(budget.get(reserved_key)) or 0) - remaining_reservation
    )
    budget[spent_key] = (
        _nonnegative_int(budget.get(spent_key)) or 0
    ) + max(0, used - recorded)
    if outcome in {"RESULT", "ERROR"} and kind == "WRITER":
        budget["writer_results"] = (_nonnegative_int(budget.get("writer_results")) or 0) + 1
    if outcome in {"RESULT", "ERROR"} and kind == "REVIEWER":
        budget["reviewer_results"] = (_nonnegative_int(budget.get("reviewer_results")) or 0) + 1
    entries[index] = {
        **entry,
        "status": outcome,
        "transport_attempts": final_transport_attempts,
        "completed_at": datetime.now(UTC).isoformat(),
        **({"payload": payload} if payload is not None else {}),
    }
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def transport_attempt_started(
    attempt: dict[str, Any], *, acquisition_id: str
) -> dict[str, Any]:
    """Move one reserved transport attempt to consumed before provider I/O."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    index = next(
        (
            position
            for position, entry in enumerate(entries)
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if index is None or entries[index].get("status") != "RESERVED":
        raise GenerationBudgetExceeded("ACQUISITION_NOT_RESERVED")
    entry = entries[index]
    recorded = _nonnegative_int(entry.get("transport_attempts")) or 0
    if recorded >= SEMANTIC_TRANSPORT_LIMIT:
        raise GenerationBudgetExceeded("ACQUISITION_TRANSPORT_EXHAUSTED")
    kind = str(entry.get("kind") or "")
    reserved_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    budget[reserved_key] = max(
        0, (_nonnegative_int(budget.get(reserved_key)) or 0) - 1
    )
    budget[spent_key] = (_nonnegative_int(budget.get(spent_key)) or 0) + 1
    entries[index] = {**entry, "transport_attempts": recorded + 1}
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def record_topic_swap(
    attempt: dict[str, Any], *, from_topic_id: str | None, to_topic_id: str
) -> dict[str, Any]:
    """Record the sole lifetime topic swap while preserving all provider spend."""

    budget = _budget_dict(attempt)
    snapshot = _snapshot_from_budget(budget)
    if snapshot.topic_swaps >= TOPIC_SWAP_LIMIT:
        raise GenerationBudgetExceeded("TOPIC_SWAP_EXHAUSTED")
    topics = list(snapshot.topics)
    for topic in (from_topic_id, to_topic_id):
        if topic and topic not in topics:
            topics.append(topic)
    if len(topics) > TOPIC_LIMIT:
        raise GenerationBudgetExceeded("TOPIC_LIMIT_EXHAUSTED")
    budget["topics"] = topics
    budget["topic_swaps"] = snapshot.topic_swaps + 1
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def replace_unknown_legacy_budget(
    attempt: dict[str, Any],
    *,
    actor: str,
    reason: str,
    idempotency_key: str,
    replaced_at: datetime | None = None,
) -> dict[str, Any]:
    """Mint one replacement lifetime after an authenticated, audited decision."""

    raw_budget = attempt.get(GENERATION_BUDGET_KEY)
    if isinstance(raw_budget, dict):
        if _valid_reset_record(raw_budget.get("reset_record")):
            raise GenerationBudgetExceeded("LEGACY_BUDGET_ALREADY_REPLACED")
        try:
            _validate_versioned_budget(raw_budget)
        except GenerationBudgetUnknown:
            legacy_spend_is_unknown = True
        else:
            raise GenerationBudgetExceeded("LEGACY_BUDGET_ALREADY_REPLACED")
    else:
        try:
            _legacy_budget(attempt)
        except GenerationBudgetUnknown:
            legacy_spend_is_unknown = True
        else:
            raise GenerationBudgetExceeded("LEGACY_BUDGET_IS_KNOWN")
    if not legacy_spend_is_unknown:  # pragma: no cover - the else branch raises
        raise GenerationBudgetExceeded("LEGACY_BUDGET_IS_KNOWN")
    budget = _zero_budget(None)
    budget["legacy_state"] = "REPLACED"
    budget["reset_record"] = {
        "event": "LEGACY_BUDGET_REPLACED",
        "actor": actor,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "replaced_at": (replaced_at or datetime.now(UTC)).isoformat(),
        "previous_snapshot": copy.deepcopy(attempt),
    }
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def acquisition_payload(
    attempt: dict[str, Any], acquisition_id: str
) -> dict[str, Any] | None:
    """Return a durable semantic result so redelivery does not repurchase it."""

    budget = _budget_dict(attempt)
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return None
    entry = next(
        (
            value
            for value in entries
            if isinstance(value, dict)
            and value.get("id") == acquisition_id
            and value.get("status") == "RESULT"
        ),
        None,
    )
    payload = entry.get("payload") if isinstance(entry, dict) else None
    return dict(payload) if isinstance(payload, dict) else None


def _acquisition_status(attempt: dict[str, Any], acquisition_id: str) -> str | None:
    budget = _budget_dict(attempt)
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return None
    entry = next(
        (
            value
            for value in entries
            if isinstance(value, dict) and value.get("id") == acquisition_id
        ),
        None,
    )
    return str(entry.get("status")) if isinstance(entry, dict) else None


class GenerationBudgetLedger:
    """Durably reserve and checkpoint one slot's provider acquisitions.

    The synchronous worker session owns the content row lease. Each method commits
    before returning so a worker crash or redelivery sees the reservation/result.
    """

    def __init__(self, db: Any, item: Any) -> None:
        self._db = db
        self._item = item

    def _attempt(self) -> dict[str, Any]:
        item_id = getattr(self._item, "id", None)
        if item_id is not None and isinstance(self._db, Session):
            current = self._db.execute(
                select(ContentItem)
                .where(ContentItem.id == item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if current is not None:
                self._item = current
        return read_generation_attempt(self._item)

    def _write(self, attempt: dict[str, Any]) -> None:
        summary = getattr(self._item, "essence_check_summary", None)
        updated = dict(summary) if isinstance(summary, dict) else {}
        updated[GENERATION_ATTEMPT_KEY] = attempt
        self._item.essence_check_summary = updated
        self._db.commit()

    def reserve(
        self, *, acquisition_id: str, kind: AcquisitionKind
    ) -> AcquisitionReservation:
        attempt = self._attempt()
        cached = acquisition_payload(attempt, acquisition_id)
        if cached is not None:
            return AcquisitionReservation(acquisition_id, kind, cached)
        status = _acquisition_status(attempt, acquisition_id)
        if status in {"RESULT", "ERROR", "UNKNOWN"}:
            raise GenerationBudgetExceeded("DURABLE_ACQUISITION_ALREADY_COMPLETED")
        reserved = reserve_acquisition(attempt, acquisition_id=acquisition_id, kind=kind)
        self._write(reserved)
        return AcquisitionReservation(acquisition_id, kind, None)

    def complete(
        self,
        reservation: AcquisitionReservation,
        *,
        transport_attempts: int,
        outcome: AcquisitionOutcome,
        payload: dict[str, Any] | None = None,
    ) -> None:
        completed = acquisition_completed(
            self._attempt(),
            acquisition_id=reservation.acquisition_id,
            transport_attempts=transport_attempts,
            outcome=outcome,
            payload=payload,
        )
        self._write(completed)

    def transport_started(self, reservation: AcquisitionReservation) -> None:
        started = transport_attempt_started(
            self._attempt(), acquisition_id=reservation.acquisition_id
        )
        self._write(started)

    def snapshot(self) -> GenerationBudgetSnapshot:
        return read_generation_budget(self._attempt())

    def next_sequence(self, kind: AcquisitionKind) -> int:
        budget = _budget_dict(self._attempt())
        entries = budget.get("acquisitions")
        matching = [
            entry
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and entry.get("kind") == kind
        ]
        if matching and matching[-1].get("status") == "RESERVED":
            return len(matching)
        return len(matching) + 1

    def latest_payload(self, acquisition_prefix: str) -> dict[str, Any] | None:
        budget = _budget_dict(self._attempt())
        entries = budget.get("acquisitions")
        if not isinstance(entries, list):
            return None
        for entry in reversed(entries):
            if (
                isinstance(entry, dict)
                and str(entry.get("id") or "").startswith(acquisition_prefix)
                and entry.get("status") == "RESULT"
                and isinstance(entry.get("payload"), dict)
            ):
                return dict(entry["payload"])
        return None

    def acquisition_status(self, acquisition_id: str) -> str | None:
        return _acquisition_status(self._attempt(), acquisition_id)

# 대표 이미지 한 건의 시도로 계수되는 원인. 본문 실패와 섞지 않는다 — 본문이 막힌 글은
# 이미지가 없어서가 아니라 본문 게이트에서 이미 걸린다.
IMAGE_ATTEMPT_REASONS = frozenset(
    {
        "IMAGE_GENERATION_FAILED",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)

# 그날 안에서 더 살 것이 남지 않은 종착. 예산 계수와 무관하게 소진으로 읽는다.
IMAGE_ATTEMPT_TERMINAL_REASONS = frozenset(
    {
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
    }
)

# 비용 가드 보류는 공급자에게 한 번도 묻지 못한 상태다. 예산을 쓴 적이 없으므로
# 소진으로 읽으면 "가드가 잠깐 막았다"가 "이미지 없이 발행"으로 굳는다.
_NEVER_EXHAUSTED_REASONS = frozenset({"COST_BLOCKED"})

# 억제를 만드는 것은 `reason`·`retry_class`·`next_retry_at`이다. 예산 사다리는 그 셋이
# 아니라 아래 계수들이 가진다. 둘을 갈라 두면 "다시 한 번 시도할 자격"만 돌려주면서
# 하루 예산·소진 일수·최초 관측 시각은 그대로 이어갈 수 있다.
GENERATION_LADDER_KEYS = (
    GENERATION_BUDGET_KEY,
    "context",
    "attempt_period",
    "exhausted_days",
    "attempt_count",
    "provider_attempt_count",
    "guard_deferral_count",
    "first_observed_at",
    "approved_facts",
)


def released_generation_attempt(summary: Any) -> dict:
    """억제만 푼 `essence_check_summary`. 예산 계수는 그대로 남긴다.

    기록을 통째로 지우면 하루 예산과 소진 일수가 0에서 다시 시작해 3일 소진도,
    그 소진이 여는 주제 교체도 영영 오지 않는다. 반대로 그대로 두면 저장된 사유가
    이미 사라진 본문을 계속 설명하며 다음 시도를 막는다.
    """

    updated = dict(summary) if isinstance(summary, dict) else {}
    previous = updated.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(previous, dict):
        return updated
    carried = {key: previous[key] for key in GENERATION_LADDER_KEYS if key in previous}
    if carried:
        updated[GENERATION_ATTEMPT_KEY] = carried
    else:
        updated.pop(GENERATION_ATTEMPT_KEY, None)
    return updated


def read_generation_attempt(item: Any) -> dict:
    """저장된 시도 조각의 사본. 없거나 모양이 다르면 빈 dict."""

    summary = getattr(item, "essence_check_summary", None)
    if not isinstance(summary, dict):
        return {}
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(attempt, dict):
        return {}
    return dict(attempt)


def _provider_attempt_count(attempt: dict) -> int:
    raw = attempt.get("provider_attempt_count", attempt.get("attempt_count"))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def image_attempts_exhausted_today(item: Any, now: datetime | None = None) -> bool:
    """오늘(KST) 이 글의 대표 이미지 자동 시도가 더 남아 있지 않은가.

    남아 있다면 발행 게이트는 이미지를 기다린다 — 아직 야간 스윕이 소유한 상태를
    사람의 일이나 영구 무이미지 발행으로 바꾸지 않기 위해서다.
    """

    attempt = read_generation_attempt(item)
    reason = str(attempt.get("reason") or "")
    if not reason or reason in _NEVER_EXHAUSTED_REASONS:
        return False
    if reason not in IMAGE_ATTEMPT_REASONS:
        return False
    if reason in IMAGE_ATTEMPT_TERMINAL_REASONS:
        return True
    if stored_attempt_period(attempt) != environment_attempt_period(now):
        # 날이 바뀌면 하루 예산은 다시 열린다. 어제 소진은 오늘의 소진이 아니다.
        return False
    return _provider_attempt_count(attempt) >= ENVIRONMENT_ATTEMPT_BUDGET
