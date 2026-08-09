"""Nightly generation batch progress persisted as one parent OperationRun."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.workers.generation_run_control import GenerationItemState, create_item_run

_SAFE_FAILURE_MESSAGE = "생성 작업이 완료되지 않았습니다. 운영 센터에서 원인을 확인해 주세요."
_NEXT_RETRY_DELAY = timedelta(minutes=15)


class GenerationBatchRecorder:
    """Mutable per-item accumulator whose purpose is durable batch progress."""

    def __init__(self, db: Session, task_id: str, window_start: date, window_end: date) -> None:
        now = datetime.now(UTC)
        self.db = db
        key = f"nightly:{task_id}"
        existing = db.scalar(
            select(OperationRun).where(
                OperationRun.operation_type == "NIGHTLY_CONTENT_GENERATION",
                OperationRun.idempotency_key == key,
            )
        )
        if existing is not None:
            self.run = existing
            self.items = _stored_items(existing.result_summary)
            existing.state = OperationRunState.RUNNING
            existing.completed_at = None
            existing.heartbeat_at = now
            existing.lease_owner = task_id[:255]
            existing.lease_expires_at = now + timedelta(hours=2)
            existing.attempt_count += 1
            existing.version += 1
            db.commit()
            return
        self.items: dict[str, JSONValue] = {}
        self.run = OperationRun(
            id=uuid.uuid4(),
            operation_type="NIGHTLY_CONTENT_GENERATION",
            state=OperationRunState.RUNNING,
            idempotency_key=key,
            task_id=task_id,
            request_payload={"window_start": str(window_start), "window_end": str(window_end)},
            attempt_count=1,
            started_at=now,
            heartbeat_at=now,
            lease_owner=task_id[:255],
            lease_expires_at=now + timedelta(hours=2),
            total_count=0,
            success_count=0,
            failure_count=0,
            skipped_count=0,
            version=1,
        )
        db.add(self.run)
        db.commit()

    def record(
        self,
        item_id: uuid.UUID,
        state: GenerationItemState,
        *,
        safe_error_code: str | None = None,
        safe_error_message: str | None = None,
    ) -> None:
        payload: dict[str, JSONValue] = {
            "state": state.value,
            "attempt_id": f"{self.run.id}:{item_id}:{self.run.attempt_count}",
        }
        if safe_error_code is not None:
            payload["safe_error_code"] = safe_error_code
            payload["safe_error_message"] = safe_error_message
            payload["next_retry_at"] = (datetime.now(UTC) + _NEXT_RETRY_DELAY).isoformat()
        self.items[str(item_id)] = payload
        self._persist(terminal=False)

    def item_run(
        self,
        item_id: uuid.UUID,
        hospital_id: uuid.UUID,
        operation_type: str,
        state: OperationRunState,
        *,
        safe_error_code: str | None = None,
        safe_error_message: str | None = None,
        attempt_kind: str = "final",
    ) -> OperationRun:
        return create_item_run(
            self.db,
            parent_run_id=self.run.id,
            item_id=item_id,
            hospital_id=hospital_id,
            operation_type=operation_type,
            state=state,
            result=self.items[str(item_id)],
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            attempt_kind=f"{attempt_kind}-{self.run.attempt_count}",
        )

    def finish(self) -> OperationRunState:
        return self._persist(terminal=True)

    def _persist(self, *, terminal: bool) -> OperationRunState:
        states = [item["state"] for item in self.items.values() if isinstance(item, dict)]
        successes = states.count(GenerationItemState.SUCCEEDED.value)
        failures = states.count(GenerationItemState.FAILED.value) + states.count(
            GenerationItemState.PARTIAL.value
        )
        skipped = states.count(GenerationItemState.SKIPPED.value) + states.count(
            GenerationItemState.DISCARDED.value
        )
        state = (
            _terminal_state(successes, failures, skipped) if terminal else OperationRunState.RUNNING
        )
        self.db.execute(
            update(OperationRun)
            .where(OperationRun.id == self.run.id)
            .values(
                state=state,
                total_count=len(states),
                success_count=successes,
                failure_count=failures,
                skipped_count=skipped,
                result_summary={"items": self.items},
                completed_at=datetime.now(UTC) if terminal else None,
                lease_owner=None if terminal else self.run.lease_owner,
                lease_expires_at=None if terminal else self.run.lease_expires_at,
                safe_error_code="CONTENT_GENERATION_PARTIAL"
                if state in (OperationRunState.PARTIAL, OperationRunState.FAILED)
                else None,
                safe_error_message=_SAFE_FAILURE_MESSAGE
                if state in (OperationRunState.PARTIAL, OperationRunState.FAILED)
                else None,
                version=OperationRun.version + 1,
            )
        )
        self.db.commit()
        return state


def _terminal_state(successes: int, failures: int, skipped: int) -> OperationRunState:
    if failures == 0 and skipped == 0:
        return OperationRunState.SUCCEEDED
    if successes == 0 and failures > 0 and skipped == 0:
        return OperationRunState.FAILED
    return OperationRunState.PARTIAL


def _stored_items(summary: dict[str, JSONValue] | None) -> dict[str, JSONValue]:
    if summary is None:
        return {}
    values = summary.get("items")
    return dict(values) if isinstance(values, dict) else {}
