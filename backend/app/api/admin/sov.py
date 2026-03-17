"""
Admin API — SoV 분석
GET /admin/hospitals/{id}/sov/trend    — 주간 SoV 추이 (최근 12주)
GET /admin/hospitals/{id}/sov/queries  — 쿼리별 멘션율
"""
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import arrow
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import QueryMatrix, SovRecord

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — SoV"])


@router.get("/{hospital_id}/sov/trend")
async def get_sov_trend(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    최근 12주 주간 SoV 추이.
    Returns: [{week_start, sov_pct, mention_count, total_count}, ...]
    """
    await _get_hospital_or_404(db, hospital_id)

    now = arrow.now("Asia/Seoul")
    weeks = []
    for i in range(11, -1, -1):
        week_end = now.shift(weeks=-i)
        week_start = week_end.shift(weeks=-1)
        weeks.append((week_start.datetime, week_end.datetime, week_start.format("YYYY-MM-DD")))

    window_start = weeks[0][0]
    window_end = weeks[-1][1]
    all_rows_stmt = select(SovRecord).where(
        SovRecord.hospital_id == hospital_id,
        SovRecord.measured_at >= window_start,
        SovRecord.measured_at < window_end,
    )
    all_rows = (await db.execute(all_rows_stmt)).scalars().all()

    result = []
    for start_dt, end_dt, label in weeks:
        rows = [r for r in all_rows if start_dt <= r.measured_at < end_dt]
        total = len(rows)
        mentioned = sum(1 for r in rows if r.is_mentioned)
        sov_pct = round(mentioned / total * 100, 1) if total > 0 else 0.0
        result.append({
            "week_start": label,
            "sov_pct": sov_pct,
            "mention_count": mentioned,
            "total_count": total,
        })

    return result


@router.get("/{hospital_id}/sov/queries")
async def get_sov_queries(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    쿼리별 멘션율.
    Returns: [{query_text, mention_rate, mention_count, total_count, last_measured_at}, ...]
    """
    await _get_hospital_or_404(db, hospital_id)

    # 활성 쿼리 목록
    q_stmt = select(QueryMatrix).where(
        QueryMatrix.hospital_id == hospital_id,
        QueryMatrix.is_active == True,
    )
    queries = (await db.execute(q_stmt)).scalars().all()

    query_ids = [q.id for q in queries]
    all_records_stmt = select(SovRecord).where(
        SovRecord.hospital_id == hospital_id,
        SovRecord.query_id.in_(query_ids),
    )
    all_records_result = await db.execute(all_records_stmt)
    all_records = all_records_result.scalars().all()
    records_by_query: dict = defaultdict(list)
    for r in all_records:
        records_by_query[r.query_id].append(r)

    result = []
    for q in queries:
        records = records_by_query[q.id]
        total = len(records)
        mentioned = sum(1 for r in records if r.is_mentioned)
        mention_rate = round(mentioned / total * 100, 1) if total > 0 else 0.0
        last_measured = max((r.measured_at for r in records), default=None)
        result.append({
            "query_id": str(q.id),
            "query_text": q.query_text,
            "mention_rate": mention_rate,
            "mention_count": mentioned,
            "total_count": total,
            "last_measured_at": last_measured.isoformat() if last_measured else None,
        })

    return sorted(result, key=lambda x: x["mention_rate"], reverse=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
