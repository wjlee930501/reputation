"""Durable OperationRun storage for Naver URL item outcomes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import (
    Incident,
    IncidentState,
    JSONValue,
    OperationRun,
    OperationRunState,
)
from app.services.naver_handoff_contracts import (
    NaverHandoffItem,
    NaverHandoffState,
    parse_item,
)


@dataclass(frozen=True, slots=True)
class NaverRunStart:
    hospital_id: uuid.UUID
    blog_id: str | None
    items: tuple[NaverHandoffItem, ...]
    operation_type: str
    parent_run_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class NaverFailedItemLookup:
    hospital_id: uuid.UUID
    parent_run_id: uuid.UUID
    url_hash: str


@dataclass(frozen=True, slots=True)
class NaverOpenFailure:
    run_id: uuid.UUID
    item: NaverHandoffItem


@dataclass(frozen=True, slots=True)
class NaverRetryConflict(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


async def start_naver_run(db: AsyncSession, start: NaverRunStart) -> OperationRun:
    now = datetime.now(UTC)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=start.hospital_id,
        operation_type=start.operation_type,
        state=OperationRunState.RUNNING,
        idempotency_key=None,
        requested_by_id=None,
        parent_run_id=start.parent_run_id,
        task_id=None,
        attempt_count=1,
        total_count=len(start.items),
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={
            "source_type": "NAVER_BLOG",
            "source_id": start.blog_id or "UNKNOWN",
            "blog_id": start.blog_id,
            "url_hashes": [item.url_hash for item in start.items],
        },
        result_summary=_summary(start.items),
        started_at=now,
        requested_at=now,
        version=1,
    )
    db.add(run)
    await db.commit()
    return run


async def save_naver_items(
    db: AsyncSession, run: OperationRun, items: tuple[NaverHandoffItem, ...]
) -> None:
    run.result_summary = _summary(items)
    run.success_count = sum(item.state == NaverHandoffState.INGESTED for item in items)
    run.failure_count = sum(item.state == NaverHandoffState.FAILED for item in items)
    run.skipped_count = sum(item.state == NaverHandoffState.SKIPPED for item in items)
    run.version += 1
    await db.commit()


async def finish_naver_run(
    db: AsyncSession,
    run: OperationRun,
    items: tuple[NaverHandoffItem, ...],
    *,
    run_error: tuple[str, str] | None = None,
) -> None:
    await save_naver_items(db, run, items)
    run.state = _terminal_state(items, run_error is not None)
    run.completed_at = datetime.now(UTC)
    if run_error is not None:
        run.safe_error_code, run.safe_error_message = run_error
    elif run.failure_count:
        run.safe_error_code = "NAVER_ITEMS_FAILED"
        run.safe_error_message = (
            "일부 네이버 블로그 글을 가져오지 못했습니다. 실패한 글만 다시 수집해 주세요."
        )
    run.version += 1
    await db.commit()


async def failed_parent_item(
    db: AsyncSession, lookup: NaverFailedItemLookup
) -> tuple[OperationRun, NaverHandoffItem]:
    parent = await db.scalar(
        select(OperationRun)
        .where(
            OperationRun.id == lookup.parent_run_id,
            OperationRun.hospital_id == lookup.hospital_id,
            OperationRun.operation_type.in_(("NAVER_SOURCE_HANDOFF", "NAVER_SOURCE_RETRY")),
        )
        .with_for_update()
    )
    if parent is None:
        raise NaverRetryConflict("NAVER_RUN_NOT_FOUND", "수집 작업을 찾을 수 없습니다.")
    for item in parse_run_items(parent):
        if item.url_hash == lookup.url_hash and item.state == NaverHandoffState.FAILED:
            return parent, item
    raise NaverRetryConflict(
        "NAVER_ITEM_NOT_RETRYABLE",
        "다시 수집할 수 있는 실패 글이 아닙니다. 화면을 새로고침해 현재 상태를 확인해 주세요.",
    )


async def list_open_naver_failures(
    db: AsyncSession, hospital_id: uuid.UUID
) -> tuple[NaverOpenFailure, ...]:
    """Return only failures whose stable incident still needs operator action."""
    rows = (
        await db.execute(
            select(Incident, OperationRun)
            .join(OperationRun, OperationRun.id == Incident.operation_run_id)
            .where(
                Incident.hospital_id == hospital_id,
                Incident.incident_type == "NAVER_SOURCE_FETCH_FAILED",
                Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
            )
            .order_by(Incident.last_seen_at.desc())
        )
    ).all()
    failures: list[NaverOpenFailure] = []
    for incident, run in rows:
        item = next(
            (
                candidate
                for candidate in parse_run_items(run)
                if candidate.url_hash == incident.source_id
                and candidate.state == NaverHandoffState.FAILED
            ),
            None,
        )
        if item is not None:
            failures.append(NaverOpenFailure(run.id, item))
    return tuple(failures)


def parse_run_items(run: OperationRun) -> tuple[NaverHandoffItem, ...]:
    summary = run.result_summary or {}
    raw_items = summary.get("items")
    if not isinstance(raw_items, list):
        return ()
    return tuple(parse_item(item) for item in raw_items if isinstance(item, dict))


def _summary(items: tuple[NaverHandoffItem, ...]) -> dict[str, JSONValue]:
    return {"items": [item.payload() for item in items]}


def _terminal_state(
    items: tuple[NaverHandoffItem, ...], has_run_error: bool
) -> OperationRunState:
    if has_run_error:
        return OperationRunState.FAILED
    failures = sum(item.state == NaverHandoffState.FAILED for item in items)
    successes = sum(item.state == NaverHandoffState.INGESTED for item in items)
    skipped = sum(item.state == NaverHandoffState.SKIPPED for item in items)
    if failures and (successes or skipped):
        return OperationRunState.PARTIAL
    if failures:
        return OperationRunState.FAILED
    return OperationRunState.SUCCEEDED
