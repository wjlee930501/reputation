"""Shared projection helpers for monthly report delivery state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.models.monthly_control import MonthlyDeliveryEvent, ReportDeliveryEventType
from app.models.report import MonthlyReport


def latest_monthly_report_subquery(year: int, month: int) -> Any:
    """Select one latest MONTHLY report per hospital for a reporting period."""

    return (
        select(
            MonthlyReport.id.label("report_id"),
            MonthlyReport.hospital_id.label("hospital_id"),
            func.row_number()
            .over(
                partition_by=MonthlyReport.hospital_id,
                order_by=(
                    MonthlyReport.version.desc(),
                    MonthlyReport.created_at.desc(),
                    MonthlyReport.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .subquery()
    )


def latest_delivery_event_subquery() -> Any:
    """Select one latest append-only delivery event per report."""

    return (
        select(
            MonthlyDeliveryEvent.report_id.label("report_id"),
            MonthlyDeliveryEvent.event_type.label("event_type"),
            MonthlyDeliveryEvent.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=MonthlyDeliveryEvent.report_id,
                order_by=(
                    MonthlyDeliveryEvent.created_at.desc(),
                    MonthlyDeliveryEvent.id.desc(),
                ),
            )
            .label("rn"),
        )
        .subquery()
    )


def effective_delivery_event(
    events: list[MonthlyDeliveryEvent],
) -> MonthlyDeliveryEvent | None:
    """Return the latest event from an ascending created_at/id event stream."""

    return events[-1] if events else None


def delivery_is_effective(
    *,
    latest_event_type: str | None,
    legacy_sent_at_present: bool,
) -> bool:
    """Apply the append-only delivery source of truth with legacy fallback."""

    if latest_event_type is None:
        return legacy_sent_at_present
    return latest_event_type != ReportDeliveryEventType.RESCINDED.value


__all__ = (
    "delivery_is_effective",
    "effective_delivery_event",
    "latest_delivery_event_subquery",
    "latest_monthly_report_subquery",
)
