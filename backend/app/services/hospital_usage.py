"""Best-effort persistence and hospital-scoped aggregation for provider usage."""

import logging
import uuid
from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_sessionmaker
from app.models.usage import HospitalUsageEvent

logger = logging.getLogger(__name__)

LEDGER_KINDS = ("onboarding", "content", "image", "sov")

# 집계 경계는 cost_guard와 같은 운영 캘린더(Asia/Seoul)를 쓴다. 가드는 KST 일/월로 세는데
# 이 원장만 UTC로 자르면 같은 호출이 두 화면에서 다른 날에 잡힌다.
_KST = ZoneInfo("Asia/Seoul")


class UsageTotals(TypedDict):
    count: int
    input_tokens: int
    output_tokens: int


class UsageWindows(TypedDict):
    daily: UsageTotals
    monthly: UsageTotals


def _zero() -> UsageTotals:
    return {"count": 0, "input_tokens": 0, "output_tokens": 0}


def _non_negative_token(value: int | None) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _window_starts(now: datetime) -> tuple[datetime, datetime]:
    """(오늘 00:00 KST, 이번 달 1일 00:00 KST)."""
    kst_now = now.astimezone(_KST)
    day_start = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    return day_start, month_start


async def record_usage(
    *,
    hospital_id: uuid.UUID | str | None,
    kind: str,
    input_tokens: int | None = 0,
    output_tokens: int | None = 0,
    db: AsyncSession | None = None,
) -> None:
    """Append one event without ever failing the provider pipeline."""
    if hospital_id is None:
        logger.warning("hospital usage skipped: missing hospital_id kind=%s", kind)
        return
    if kind not in LEDGER_KINDS:
        logger.warning("hospital usage skipped: unknown kind=%s", kind)
        return
    try:
        normalized_hospital_id = (
            hospital_id if isinstance(hospital_id, uuid.UUID) else uuid.UUID(str(hospital_id))
        )
    except (TypeError, ValueError):
        logger.warning("hospital usage skipped: invalid hospital_id kind=%s", kind)
        return

    event = HospitalUsageEvent(
        hospital_id=normalized_hospital_id,
        kind=kind,
        input_tokens=_non_negative_token(input_tokens),
        output_tokens=_non_negative_token(output_tokens),
        # 시각을 파이썬에서 찍는다. server_default에만 기대면 flush 전에는 created_at이
        # 비어 있어, 집계 창(오늘/이번 달)이 시각 없는 행을 만나 어긋난다.
        created_at=datetime.now(_KST),
    )
    try:
        if db is not None:
            db.add(event)
            return
        sessionmaker = get_async_sessionmaker()
        async with sessionmaker() as session:
            session.add(event)
            await session.commit()
    except Exception:  # noqa: BLE001 — usage observation must never break provider work.
        logger.warning(
            "hospital usage persistence failed: hospital_id=%s kind=%s",
            normalized_hospital_id,
            kind,
        )


async def _totals_since(
    db: AsyncSession, hospital_id: uuid.UUID | str, since: datetime
) -> dict[str, UsageTotals]:
    rows = (
        await db.execute(
            select(
                HospitalUsageEvent.kind,
                func.count(HospitalUsageEvent.id),
                func.coalesce(func.sum(HospitalUsageEvent.input_tokens), 0),
                func.coalesce(func.sum(HospitalUsageEvent.output_tokens), 0),
            )
            .where(HospitalUsageEvent.hospital_id == hospital_id)
            .where(HospitalUsageEvent.created_at >= since)
            .group_by(HospitalUsageEvent.kind)
        )
    ).all()
    return {
        kind: {
            "count": int(count or 0),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
        }
        for kind, count, input_tokens, output_tokens in rows
    }


async def aggregate_usage(
    db: AsyncSession, hospital_id: uuid.UUID | str, *, now: datetime | None = None
) -> dict[str, UsageWindows]:
    """Aggregate one hospital's KST day and month, always returning every kind."""
    day_start, month_start = _window_starts(now or datetime.now(_KST))
    daily = await _totals_since(db, hospital_id, day_start)
    monthly = await _totals_since(db, hospital_id, month_start)
    return {
        kind: {
            "daily": daily.get(kind, _zero()),
            "monthly": monthly.get(kind, _zero()),
        }
        for kind in LEDGER_KINDS
    }
