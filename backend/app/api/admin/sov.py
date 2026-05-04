"""
Admin API — AI 답변 언급률 분석
GET /admin/hospitals/{id}/sov/trend    — 주간 AI 답변 언급률 추이 (최근 12주)
GET /admin/hospitals/{id}/sov/queries  — 쿼리별 멘션율
GET /admin/hospitals/{id}/sov/measurement-runs — 최근 측정 실행 목록
"""
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

import arrow
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — AI Answer Mention Rate"])


@router.get("/{hospital_id}/sov/measurement-runs")
async def get_sov_measurement_runs(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
):
    """최근 AI 답변 언급률 측정 실행 목록."""
    await _get_hospital_or_404(db, hospital_id)

    safe_limit = max(1, min(limit, 100))
    stmt = (
        select(MeasurementRun)
        .where(MeasurementRun.hospital_id == hospital_id)
        .order_by(MeasurementRun.created_at.desc())
        .limit(safe_limit)
    )
    runs = (await db.execute(stmt)).scalars().all()
    return [_serialize_measurement_run(run) for run in runs]


@router.get("/{hospital_id}/sov/trend")
async def get_sov_trend(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    최근 12주 주간 AI 답변 언급률 추이.
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
        successful_rows = [r for r in rows if _is_successful_measurement(r)]
        total = len(successful_rows)
        mentioned = sum(1 for r in successful_rows if r.is_mentioned)
        failure_count = sum(1 for r in rows if _is_failed_measurement(r))
        sov_pct = round(mentioned / total * 100, 1) if total > 0 else 0.0
        result.append({
            "week_start": label,
            "sov_pct": sov_pct,
            "mention_count": mentioned,
            "total_count": total,
            "failure_count": failure_count,
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
        QueryMatrix.is_active,
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
        successful_records = [r for r in records if _is_successful_measurement(r)]
        total = len(successful_records)
        mentioned = sum(1 for r in successful_records if r.is_mentioned)
        failure_count = sum(1 for r in records if _is_failed_measurement(r))
        mention_rate = round(mentioned / total * 100, 1) if total > 0 else 0.0
        last_measured = max((r.measured_at for r in records), default=None)
        result.append({
            "query_id": str(q.id),
            "query_text": q.query_text,
            "mention_rate": mention_rate,
            "mention_count": mentioned,
            "total_count": total,
            "failure_count": failure_count,
            "last_measured_at": last_measured.isoformat() if last_measured else None,
        })

    return sorted(result, key=lambda x: x["mention_rate"], reverse=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _is_successful_measurement(record: Any) -> bool:
    status = getattr(record, "measurement_status", None)
    return status is None or str(status).upper() == "SUCCESS"


def _is_failed_measurement(record: Any) -> bool:
    return not _is_successful_measurement(record)


def _serialize_measurement_run(run: MeasurementRun) -> dict[str, Any]:
    query_count = run.query_count or 0
    success_count = run.success_count or 0
    failure_count = run.failure_count or 0
    return {
        "id": str(run.id),
        "hospital_id": str(run.hospital_id),
        "run_label": run.run_label,
        "measurement_method": run.measurement_method,
        "status": run.status,
        "query_count": query_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": round(success_count / query_count * 100, 1) if query_count else 0.0,
        "failure_rate": round(failure_count / query_count * 100, 1) if query_count else 0.0,
        "started_at": _iso_or_none(run.started_at),
        "completed_at": _iso_or_none(run.completed_at),
        "model_name": run.model_name,
        "search_mode": run.search_mode,
        "config": run.config,
        "error_summary": run.error_summary,
        "created_at": _iso_or_none(run.created_at),
        "updated_at": _iso_or_none(run.updated_at),
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
