"""Durable paid SoV observation checkpoints.

Each repeat has a frozen identity and two independently resumable stages. A received
answer is never purchased again merely because its judgment failed, and an ambiguous
judgment is retained as an observed outcome instead of being resampled until decisive.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from app.models.monthly_control import MeasurementObservationSlot, MonthlyMeasurementCell
from app.models.sov import SovRecord

TERMINAL_JUDGMENT_STATUSES = frozenset({"CONFIRMED", "AMBIGUOUS"})
MAX_STAGE_ATTEMPTS = 3
STAGE_LEASE_SECONDS = 15 * 60


class MeasurementSlotPolicyDrift(RuntimeError):
    pass


def canonical_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def protocol_fingerprint(protocol: Mapping[str, Any] | None) -> str:
    return canonical_fingerprint(dict(protocol or {}))


@dataclass(frozen=True, slots=True)
class ObservationAdequacy:
    planned_slots: int
    received_answers: int
    confirmed_slots: int
    ambiguous_slots: int
    answer_failed_slots: int
    judgment_failed_slots: int
    pending_slots: int
    status: str
    lineage: str = "SLOTTED"

    @property
    def terminal_slots(self) -> int:
        return self.confirmed_slots + self.ambiguous_slots

    def to_payload(self) -> dict[str, int | str]:
        return {
            "planned_slots": self.planned_slots,
            "received_answers": self.received_answers,
            "confirmed_slots": self.confirmed_slots,
            "ambiguous_slots": self.ambiguous_slots,
            "answer_failed_slots": self.answer_failed_slots,
            "judgment_failed_slots": self.judgment_failed_slots,
            "pending_slots": self.pending_slots,
            "terminal_slots": self.terminal_slots,
            "status": self.status,
            "lineage": self.lineage,
        }


def summarize_observation_slots(
    slots: Iterable[MeasurementObservationSlot], *, deadline_reached: bool
) -> ObservationAdequacy:
    rows = list(slots)
    confirmed = sum(row.judgment_status == "CONFIRMED" for row in rows)
    ambiguous = sum(row.judgment_status == "AMBIGUOUS" for row in rows)
    answer_failed = sum(row.answer_status == "FAILED" for row in rows)
    judgment_failed = sum(
        row.answer_status == "RECEIVED" and row.judgment_status == "FAILED" for row in rows
    )
    received = sum(row.answer_status == "RECEIVED" for row in rows)
    pending = sum(not slot_is_terminal(row) for row in rows)
    if rows and confirmed == len(rows):
        status = "COMPLETE"
    elif confirmed > 0 and deadline_reached:
        status = "LIMITED"
    elif deadline_reached:
        status = "UNAVAILABLE"
    else:
        status = "IN_PROGRESS"
    return ObservationAdequacy(
        planned_slots=len(rows),
        received_answers=received,
        confirmed_slots=confirmed,
        ambiguous_slots=ambiguous,
        answer_failed_slots=answer_failed,
        judgment_failed_slots=judgment_failed,
        pending_slots=pending,
        status=status,
    )


def _existing_monthly_slots(session, cell_id: uuid.UUID, protocol_hash: str):
    return list(
        session.execute(
            select(MeasurementObservationSlot)
            .where(
                MeasurementObservationSlot.scope == "MONTHLY",
                MeasurementObservationSlot.monthly_cell_id == cell_id,
            )
            .order_by(MeasurementObservationSlot.repeat_no)
        )
        .scalars()
        .all()
    )


def ensure_monthly_slots(
    session,
    *,
    cell: MonthlyMeasurementCell,
    hospital_id: uuid.UUID,
    measurement_run_id: uuid.UUID,
    repeat_count: int,
    protocol: Mapping[str, Any],
) -> list[MeasurementObservationSlot]:
    if cell.query_matrix_id is None:
        raise ValueError("monthly observation slot requires query_matrix_id")
    fingerprint = protocol_fingerprint(protocol)
    existing = _existing_monthly_slots(session, cell.id, fingerprint)
    if any(slot.protocol_hash != fingerprint for slot in existing):
        raise MeasurementSlotPolicyDrift("monthly observation slot protocol changed")
    by_repeat = {slot.repeat_no: slot for slot in existing}
    for repeat_no in range(1, repeat_count + 1):
        if repeat_no in by_repeat:
            continue
        slot = MeasurementObservationSlot(
            scope="MONTHLY",
            hospital_id=hospital_id,
            monthly_cell_id=cell.id,
            measurement_run_id=measurement_run_id,
            query_id=cell.query_matrix_id,
            platform=cell.platform,
            repeat_no=repeat_no,
            protocol_hash=fingerprint,
        )
        session.add(slot)
        by_repeat[repeat_no] = slot
    session.flush()
    return [by_repeat[number] for number in sorted(by_repeat) if number <= repeat_count]


def ensure_v0_slots(
    session,
    *,
    hospital_id: uuid.UUID,
    measurement_run_id: uuid.UUID,
    query_id: uuid.UUID,
    platform: str,
    repeat_count: int,
    protocol: Mapping[str, Any],
) -> list[MeasurementObservationSlot]:
    fingerprint = protocol_fingerprint(protocol)
    existing = list(
        session.execute(
            select(MeasurementObservationSlot)
            .where(
                MeasurementObservationSlot.scope == "V0",
                MeasurementObservationSlot.measurement_run_id == measurement_run_id,
                MeasurementObservationSlot.query_id == query_id,
                MeasurementObservationSlot.platform == platform,
            )
            .order_by(MeasurementObservationSlot.repeat_no)
        )
        .scalars()
        .all()
    )
    if any(slot.protocol_hash != fingerprint for slot in existing):
        raise MeasurementSlotPolicyDrift("V0 observation slot protocol changed")
    by_repeat = {slot.repeat_no: slot for slot in existing}
    for repeat_no in range(1, repeat_count + 1):
        if repeat_no in by_repeat:
            continue
        slot = MeasurementObservationSlot(
            scope="V0",
            hospital_id=hospital_id,
            measurement_run_id=measurement_run_id,
            query_id=query_id,
            platform=platform,
            repeat_no=repeat_no,
            protocol_hash=fingerprint,
        )
        session.add(slot)
        by_repeat[repeat_no] = slot
    session.flush()
    return [by_repeat[number] for number in sorted(by_repeat) if number <= repeat_count]


def slot_needs_answer(slot: MeasurementObservationSlot) -> bool:
    return slot.answer_status != "RECEIVED" and slot.answer_attempt_count < MAX_STAGE_ATTEMPTS


def slot_needs_judgment(slot: MeasurementObservationSlot) -> bool:
    return (
        slot.answer_status == "RECEIVED"
        and slot.judgment_status not in TERMINAL_JUDGMENT_STATUSES
        and slot.judgment_attempt_count < MAX_STAGE_ATTEMPTS
    )


def slot_is_terminal(slot: MeasurementObservationSlot) -> bool:
    if slot.judgment_status in TERMINAL_JUDGMENT_STATUSES:
        return True
    if slot.answer_status != "RECEIVED":
        return slot.answer_attempt_count >= MAX_STAGE_ATTEMPTS
    return slot.judgment_attempt_count >= MAX_STAGE_ATTEMPTS


def claim_slot_stage(
    session,
    slot_id: uuid.UUID,
    *,
    stage: str,
    now: datetime | None = None,
) -> tuple[MeasurementObservationSlot, uuid.UUID] | None:
    """Claim one answer/judgment stage under a DB row lock.

    The caller commits the claim before making the external call. A worker death
    leaves a bounded lease that a later recovery run can take over.
    """
    moment = now or datetime.now(timezone.utc)
    slot = session.execute(
        select(MeasurementObservationSlot)
        .where(MeasurementObservationSlot.id == slot_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if slot is None:
        return None
    if slot.lease_token is not None and slot.lease_expires_at is not None:
        expires = slot.lease_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > moment:
            return None
    if stage == "ANSWER":
        eligible = slot_needs_answer(slot)
    elif stage == "JUDGMENT":
        eligible = slot_needs_judgment(slot)
    else:
        raise ValueError("measurement slot stage must be ANSWER or JUDGMENT")
    if not eligible:
        return None
    token = uuid.uuid4()
    slot.lease_token = token
    slot.lease_expires_at = moment + timedelta(seconds=STAGE_LEASE_SECONDS)
    if stage == "ANSWER":
        slot.answer_attempt_count += 1
    else:
        slot.judgment_attempt_count += 1
    slot.version += 1
    session.flush()
    return slot, token


def _claimed_slot_for_checkpoint(
    session,
    slot_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> MeasurementObservationSlot | None:
    """Fence late provider returns against a newer lease owner."""
    return session.execute(
        select(MeasurementObservationSlot)
        .where(
            MeasurementObservationSlot.id == slot_id,
            MeasurementObservationSlot.lease_token == lease_token,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _release_lease(slot: MeasurementObservationSlot) -> None:
    slot.lease_token = None
    slot.lease_expires_at = None
    slot.version += 1


def checkpoint_answer(
    session,
    slot_id: uuid.UUID,
    result: Mapping[str, Any],
    *,
    lease_token: uuid.UUID,
) -> MeasurementObservationSlot | None:
    """Persist an answer only while this worker still owns the committed lease."""
    slot = _claimed_slot_for_checkpoint(session, slot_id, lease_token)
    if slot is None:
        return None
    raw_response = str(result.get("raw_response") or "")
    explicit = str(result.get("measurement_status") or "").upper()
    if explicit == "SUCCESS" and raw_response.strip():
        slot.answer_status = "RECEIVED"
        slot.raw_response = raw_response
        slot.answer_hash = hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
        slot.answer_model = str(result.get("answer_model") or "") or None
        slot.measurement_method = str(result.get("measurement_method") or "") or None
        measured_at = result.get("measured_at")
        slot.answered_at = (
            measured_at if isinstance(measured_at, datetime) else datetime.now(timezone.utc)
        )
        slot.search_calls = result.get("search_calls")
        source_urls = result.get("source_urls")
        slot.citation_urls = list(source_urls) if isinstance(source_urls, (list, tuple)) else []
        slot.input_tokens = result.get("input_tokens")
        slot.output_tokens = result.get("output_tokens")
        slot.answer_failure_reason = None
        _release_lease(slot)
        return slot
    slot.answer_status = "FAILED"
    slot.answer_failure_reason = str(result.get("failure_reason") or "answer_failed")[:500]
    _release_lease(slot)
    return slot


def answer_artifact(slot: MeasurementObservationSlot) -> dict[str, Any]:
    if slot.answer_status != "RECEIVED" or not slot.raw_response:
        raise ValueError("observation slot has no reusable answer")
    return {
        "raw_response": slot.raw_response,
        "answer_model": slot.answer_model,
        "measurement_method": slot.measurement_method,
        "measured_at": slot.answered_at,
        "search_calls": slot.search_calls,
        "source_urls": list(slot.citation_urls or []),
        "input_tokens": slot.input_tokens,
        "output_tokens": slot.output_tokens,
        "measurement_status": "SUCCESS",
    }


def checkpoint_judgment(
    session,
    slot_id: uuid.UUID,
    *,
    fingerprint: str,
    result: Mapping[str, Any],
    sov_record: SovRecord,
    lease_token: uuid.UUID,
) -> tuple[MeasurementObservationSlot, str] | None:
    """Fence and persist the judgment plus SovRecord in one transaction."""
    slot = _claimed_slot_for_checkpoint(session, slot_id, lease_token)
    if slot is None:
        return None
    session.add(sov_record)
    session.flush()
    slot.judgment_input_fingerprint = fingerprint
    status = str(result.get("measurement_status") or "").upper()
    verdict = str(result.get("verdict") or "").upper()
    if status == "SUCCESS" and verdict in {"MATCHED", "NOT_MATCHED"}:
        slot.judgment_status = "CONFIRMED"
        slot.judgment_failure_reason = None
        slot.completed_at = datetime.now(timezone.utc)
    elif status == "SUCCESS" and verdict == "AMBIGUOUS":
        slot.judgment_status = "AMBIGUOUS"
        slot.judgment_failure_reason = None
        slot.completed_at = datetime.now(timezone.utc)
    else:
        slot.judgment_status = "FAILED"
        slot.judgment_failure_reason = str(
            result.get("failure_reason") or "judgment_failed"
        )[:500]
        slot.completed_at = None
    slot.sov_record_id = sov_record.id
    _release_lease(slot)
    return slot, slot.judgment_status


def slots_for_run(session, run_id: uuid.UUID) -> list[MeasurementObservationSlot]:
    return list(
        session.execute(
            select(MeasurementObservationSlot)
            .where(MeasurementObservationSlot.measurement_run_id == run_id)
            .order_by(
                MeasurementObservationSlot.query_id,
                MeasurementObservationSlot.platform,
                MeasurementObservationSlot.repeat_no,
            )
        )
        .scalars()
        .all()
    )
