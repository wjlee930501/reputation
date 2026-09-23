"""Manual monthly SoV remasure gate.

Monthly measurement recovery belongs to the automatic catch-up (``monthly-sov:`` run and
its manifest). A manual remasure is locked by default and opens exactly once per
hospital×period, only when that automatic path has left manifest work unfinished with no
progress for ``MANUAL_REMASURE_STALL_THRESHOLD``. This stall rule is deliberately
independent of the one-hour coverage-recovery staleness used by the report reconciler.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.monthly_control import MonthlyMeasurementCell, MonthlyMeasurementManifest
from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.services.measurement_manifest_policy import manifest_slot_repeat_count
from app.services.measurement_slots import slot_is_terminal

MANUAL_REMASURE_STALL_THRESHOLD = timedelta(hours=12)
MANUAL_REMASURE_KEY_PREFIX = "monthly-sov-remasure"
_STALL_UNLOCK_SUFFIX = "stall-unlock"

LOCKED = "MONTHLY_REMASURE_LOCKED"
ALREADY_USED = "MONTHLY_REMASURE_ALREADY_USED"
STALL_UNLOCKED = "MONTHLY_REMASURE_STALL_UNLOCKED"


def _period_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def manual_remasure_key_prefix(hospital_id: uuid.UUID, year: int, month: int) -> str:
    return f"{MANUAL_REMASURE_KEY_PREFIX}:{hospital_id}:{_period_key(year, month)}:"


def automatic_monthly_sov_key(hospital_id: uuid.UUID, year: int, month: int) -> str:
    return f"monthly-sov:{hospital_id}:{_period_key(year, month)}"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MonthlyRecoveryProgress:
    automatic_run: OperationRun | None
    remaining_work: int | None
    last_progress_at: datetime | None


@dataclass(frozen=True, slots=True)
class RemasureGateDecision:
    allowed: bool
    code: str
    message: str
    stalled_since: datetime | None = None
    remaining_work: int | None = None


@dataclass(frozen=True, slots=True)
class RemasureAuthorization:
    operation_key: str
    request_payload_extra: dict[str, JSONValue] | None


class ManualRemasureLocked(Exception):
    def __init__(self, decision: RemasureGateDecision):
        super().__init__(decision.message)
        self.decision = decision


def manifest_remaining_work(manifest: MonthlyMeasurementManifest) -> int:
    """Units the automatic path can still execute; exhausted slots are not remaining."""
    cells = [
        cell for cell in (getattr(manifest, "cells", ()) or ()) if cell.state != "EXCLUDED"
    ]
    repeat_count = manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return sum(cell.state == "FAILED" for cell in cells)
    remaining = 0
    for cell in cells:
        slots = list(getattr(cell, "observation_slots", ()) or ())
        remaining += max(0, repeat_count - len(slots))
        remaining += sum(not slot_is_terminal(slot) for slot in slots)
    return remaining


def manifest_last_progress_at(manifest: MonthlyMeasurementManifest) -> datetime | None:
    """Latest moment manifest work actually advanced.

    A lease claim also bumps ``updated_at`` on a pending slot, so only received answers,
    terminal slot transitions and legacy attempt links count as progress.
    """
    moments: list[datetime | None] = [getattr(manifest, "frozen_at", None)]
    for cell in getattr(manifest, "cells", ()) or ():
        for slot in getattr(cell, "observation_slots", ()) or ():
            moments.append(slot.answered_at)
            moments.append(slot.completed_at)
            if slot_is_terminal(slot):
                moments.append(slot.updated_at)
        moments.extend(attempt.linked_at for attempt in getattr(cell, "attempts", ()) or ())
    aware = [moment for moment in (_aware(value) for value in moments) if moment is not None]
    return max(aware) if aware else None


def decide_manual_remasure(
    progress: MonthlyRecoveryProgress, *, now: datetime
) -> RemasureGateDecision:
    run = progress.automatic_run
    if run is None:
        return RemasureGateDecision(
            False,
            LOCKED,
            "월간 측정 자동 복구가 아직 이 기간을 시작하지 않았습니다. "
            "수동 재측정은 잠겨 있으며 자동 복구가 이어서 처리합니다.",
        )
    if run.state == OperationRunState.SUCCEEDED:
        return RemasureGateDecision(
            False, LOCKED, "이 기간의 월간 측정은 자동 복구로 이미 완료되었습니다."
        )
    if (run.safe_error_code or "").endswith("COST_GUARD_BLOCKED"):
        return RemasureGateDecision(
            False,
            LOCKED,
            "비용 한도 때문에 자동 측정이 대기 중입니다. "
            "한도가 회복되면 자동으로 이어서 측정하므로 수동 재측정은 열리지 않습니다.",
        )
    lease_expires_at = _aware(run.lease_expires_at)
    if run.state == OperationRunState.RUNNING and lease_expires_at and lease_expires_at > now:
        return RemasureGateDecision(
            False, LOCKED, "월간 측정 자동 복구가 지금 실행 중입니다."
        )
    if not progress.remaining_work:
        return RemasureGateDecision(
            False,
            LOCKED,
            "자동 복구가 더 실행할 월간 측정 항목이 없어 수동 재측정 대상이 없습니다.",
            remaining_work=progress.remaining_work,
        )
    last_progress_at = _aware(progress.last_progress_at)
    if last_progress_at is None or last_progress_at > now - MANUAL_REMASURE_STALL_THRESHOLD:
        return RemasureGateDecision(
            False,
            LOCKED,
            "월간 측정 자동 복구가 진행 중입니다. 남은 항목이 12시간 넘게 줄지 않을 때만 "
            "수동 재측정을 한 번 열 수 있습니다.",
            stalled_since=last_progress_at,
            remaining_work=progress.remaining_work,
        )
    return RemasureGateDecision(
        True,
        STALL_UNLOCKED,
        "월간 측정 자동 복구가 12시간 넘게 진척이 없어 수동 재측정을 한 번 허용합니다.",
        stalled_since=last_progress_at,
        remaining_work=progress.remaining_work,
    )


def _never_reached_broker(run: OperationRun) -> bool:
    """A publish rejected by the broker is terminal and bought nothing.

    REQUESTED runs still count: the autonomous recovery sweep redispatches them.
    """
    return run.state == OperationRunState.FAILED and run.safe_error_code == "BROKER_UNAVAILABLE"


def _replayed_run(
    runs: Iterable[OperationRun], *, legacy_key: str, request_fingerprint: str
) -> OperationRun | None:
    for run in runs:
        if run.idempotency_key == legacy_key:
            return run
        payload = run.request_payload if isinstance(run.request_payload, dict) else {}
        manual = payload.get("manual_remasure")
        if isinstance(manual, dict) and manual.get("request_fingerprint") == request_fingerprint:
            return run
    return None


async def _prior_manual_remasure_runs(
    db: AsyncSession, hospital_id: uuid.UUID, year: int, month: int
) -> list[OperationRun]:
    prefix = manual_remasure_key_prefix(hospital_id, year, month)
    return list(
        (
            await db.execute(
                select(OperationRun).where(
                    OperationRun.hospital_id == hospital_id,
                    OperationRun.operation_type == "RUN_SOV",
                    OperationRun.idempotency_key.startswith(prefix, autoescape=True),
                )
            )
        )
        .scalars()
        .all()
    )


async def load_monthly_recovery_progress(
    db: AsyncSession, hospital_id: uuid.UUID, year: int, month: int
) -> MonthlyRecoveryProgress:
    automatic_run = await db.scalar(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.idempotency_key == automatic_monthly_sov_key(hospital_id, year, month),
        )
    )
    manifest = await db.scalar(
        select(MonthlyMeasurementManifest)
        .options(
            selectinload(MonthlyMeasurementManifest.cells).selectinload(
                MonthlyMeasurementCell.observation_slots
            ),
            selectinload(MonthlyMeasurementManifest.cells).selectinload(
                MonthlyMeasurementCell.attempts
            ),
        )
        .where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    )
    if manifest is None:
        return MonthlyRecoveryProgress(automatic_run, None, None)
    return MonthlyRecoveryProgress(
        automatic_run,
        manifest_remaining_work(manifest),
        manifest_last_progress_at(manifest),
    )


async def authorize_manual_remasure(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
    request_fingerprint: str,
    now: datetime | None = None,
) -> RemasureAuthorization:
    """Return the operation key for an allowed or replayed remasure, else raise.

    A browser retry with the same client key replays its own run. Any other earlier manual
    remasure for the period means the single allowance is spent, except a run the broker
    rejected: it never queued, so the next request re-evaluates the stall under a fresh key.
    """
    moment = _aware(now) or datetime.now(UTC)
    prefix = manual_remasure_key_prefix(hospital_id, year, month)
    prior_runs = await _prior_manual_remasure_runs(db, hospital_id, year, month)
    spent_runs = [run for run in prior_runs if not _never_reached_broker(run)]
    replay = _replayed_run(
        spent_runs,
        legacy_key=f"{prefix}{request_fingerprint}",
        request_fingerprint=request_fingerprint,
    )
    if replay is not None and replay.idempotency_key:
        return RemasureAuthorization(replay.idempotency_key, None)
    if spent_runs:
        raise ManualRemasureLocked(
            RemasureGateDecision(
                False,
                ALREADY_USED,
                "이 기간의 수동 재측정은 이미 한 번 사용했습니다. "
                "남은 항목은 자동 복구가 이어서 처리합니다.",
            )
        )
    decision = decide_manual_remasure(
        await load_monthly_recovery_progress(db, hospital_id, year, month), now=moment
    )
    if not decision.allowed:
        raise ManualRemasureLocked(decision)
    # Every earlier key in this family belongs to a broker-rejected run, so the count of
    # those runs names the next unused key; concurrent requests still collapse on it.
    broker_rejected = len(prior_runs) - len(spent_runs)
    attempt_suffix = f":{broker_rejected + 1}" if broker_rejected else ""
    return RemasureAuthorization(
        f"{prefix}{_STALL_UNLOCK_SUFFIX}{attempt_suffix}",
        {
            "manual_remasure": {
                "request_fingerprint": request_fingerprint,
                "stalled_since": (
                    decision.stalled_since.isoformat() if decision.stalled_since else None
                ),
                "remaining_work": decision.remaining_work,
            }
        },
    )
