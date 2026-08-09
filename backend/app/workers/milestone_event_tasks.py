"""Periodic DB-truth projection of onboarding and monthly milestones."""

from __future__ import annotations

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
    milestones = (*onboarding.milestones, *monthly.milestones)
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
    """Project one completed window; Slack transport remains a separate worker."""

    result = anyio.run(_project_once, datetime.now(UTC))
    return {
        "onboarding_count": result.onboarding_count,
        "monthly_count": result.monthly_count,
        "enqueued": result.enqueued,
    }
