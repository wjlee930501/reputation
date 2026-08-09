"""Dispatch the four operations-center queues to focused set-based readers."""

from datetime import UTC, datetime
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.operations_center_incident_queries import load_incidents_queue
from app.api.admin.operations_center_onboarding_queries import load_onboarding_queue
from app.api.admin.operations_center_query_common import OperationsFilters
from app.api.admin.operations_center_report_queries import load_reports_queue
from app.api.admin.operations_center_today_queries import load_today_queue
from app.schemas.operations import OperationsQueue, OperationsQueueRow


async def load_operations_queue(
    db: AsyncSession,
    queue: OperationsQueue,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load one queue while preserving its fixed query budget."""
    now = datetime.now(UTC)
    match queue:
        case OperationsQueue.ONBOARDING:
            return await load_onboarding_queue(
                db, filters, page=page, page_size=page_size, overview=overview, now=now
            )
        case OperationsQueue.TODAY:
            return await load_today_queue(
                db, filters, page=page, page_size=page_size, overview=overview, now=now
            )
        case OperationsQueue.REPORTS:
            return await load_reports_queue(
                db, filters, page=page, page_size=page_size, overview=overview, now=now
            )
        case OperationsQueue.INCIDENTS:
            return await load_incidents_queue(
                db, filters, page=page, page_size=page_size, overview=overview, now=now
            )
        case unreachable:
            assert_never(unreachable)


__all__ = ("load_operations_queue",)
