"""Periodic DB-truth projection of onboarding and monthly milestones."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import anyio
from celery.app.task import Task
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.services.notification_milestone_messages import (
    MilestoneBatch,
    MilestoneKind,
    MilestoneProjection,
    enqueue_milestone_summary,
)
from app.workers.milestone_monthly_projection import (
    observe_monthly_milestones,
    scan_monthly_milestones,
)
from app.workers.milestone_onboarding_projection import (
    observe_onboarding_milestones,
    scan_onboarding_milestones,
)
from app.workers.milestone_projection_cursor import (
    load_projection_cursor,
    record_projection_cursor,
)
from app.workers.milestone_projection_support import (
    ProjectionWindow,
    canonical_projection_window,
)

__all__ = (
    "ProjectionResult",
    "ProjectionWindow",
    "canonical_projection_window",
    "project_milestone_window",
    "project_milestone_events",
    "scan_monthly_milestones",
    "scan_onboarding_milestones",
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    onboarding_count: int
    monthly_count: int
    enqueued: bool


async def _project_once(now: datetime) -> ProjectionResult:
    window = canonical_projection_window(now)
    async with get_async_sessionmaker()() as db:
        result = await project_milestone_window(db, window, settings.ADMIN_BASE_URL)
        await db.commit()
    return result


async def _scan_onboarding_stalls_once(now: datetime) -> dict[str, int]:
    from app.services.onboarding_stalls import apply_onboarding_stalls, load_onboarding_facts

    async with get_async_sessionmaker()() as db:
        facts = await load_onboarding_facts(db)
    return await apply_onboarding_stalls(
        facts, now, source_fetch_alerts=settings.ONBOARDING_SOURCE_FETCH_ALERTS_ENABLED
    )


async def project_milestone_window(
    db: AsyncSession,
    window: ProjectionWindow,
    admin_base_url: str,
) -> ProjectionResult:
    """Persist one truth-snapshot transition and its optional Slack intent atomically."""

    cursor = await load_projection_cursor(db, window)
    if cursor.replayed:
        return ProjectionResult(0, 0, False)
    onboarding = await observe_onboarding_milestones(db, window.end, cursor.previous_states)
    monthly = await observe_monthly_milestones(
        db,
        window.end,
        cursor.previous_states,
        cursor.delivery_since,
    )
    milestones = tuple(item for item in (*onboarding.milestones, *monthly.milestones) if should_notify_milestone(item))
    if milestones:
        await enqueue_milestone_summary(
            db,
            MilestoneBatch(milestones, window.start, window.end),
            admin_base_url,
        )
    await record_projection_cursor(
        db,
        window,
        {**onboarding.states, **monthly.states},
        len(milestones),
    )
    return ProjectionResult(len(onboarding.milestones), len(monthly.milestones), bool(milestones))


@celery_app.task(name="app.workers.milestone_event_tasks.project_milestone_events", bind=True)
def project_milestone_events(_task: Task) -> dict[str, int | bool]:
    """Project one completed window; Slack transport remains a separate worker.

    같은 15분 주기에 온보딩 정체 사고도 맞춘다. 정체 스캔의 실패가 마일스톤 투영을
    되돌리지 않게 따로 실행한다.
    """

    now = datetime.now(UTC)
    result = anyio.run(_project_once, now)
    try:
        stalls = anyio.run(_scan_onboarding_stalls_once, now)
    except Exception:
        logger.exception("onboarding stall scan failed")
        stalls = {"opened": 0, "recovered": 0}
    return {
        "onboarding_count": result.onboarding_count,
        "monthly_count": result.monthly_count,
        "enqueued": result.enqueued,
        "onboarding_stalls_opened": stalls["opened"],
        "onboarding_stalls_recovered": stalls["recovered"],
    }


def should_notify_milestone(item: MilestoneProjection) -> bool:
    """Progress stays in the cursor. Human handoffs and delivery corrections may notify.

    Final failures still use their existing incident/gap producers. The standalone
    activation producer owns its one notice; this projector must not duplicate it.
    """
    return item.requires_action or item.kind in {
        MilestoneKind.DELIVERY_CORRECTED, MilestoneKind.DELIVERY_RESCINDED,
        MilestoneKind.DELIVERY_REDELIVERED,
    } or (item.kind is MilestoneKind.HANDOFF_ACCEPTED and item.is_recovery)
