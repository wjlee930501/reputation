"""
Admin API — 측정 키워드 30개 세트 (docs/plans/2026-11-21-measurement-30-design.md §6)

GET  /admin/hospitals/{id}/measurement-set         지금 세트·후보·옛 측정 질문·다음 측정 시각
POST /admin/hospitals/{id}/measurement-set/draft   병원 정보로 만든 초안(저장하지 않는다)
PUT  /admin/hospitals/{id}/measurement-set         세트 저장(문형·모양 검사 → 질문 행 적용)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest
from app.services import measurement_set
from app.services.audit_log import default_actor, write_audit_log
from app.services.measurement_question_set import GROUP_PLAN
from app.services.measurement_set_apply import (
    MeasurementSetError,
    SetRow,
    apply_rows,
    draft_rows,
    load_targets,
    rows_from_targets,
    validate_rows,
)
from app.utils.db_locks import acquire_hospital_advisory_lock

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Measurement Set"])

KST = ZoneInfo("Asia/Seoul")
Group = Literal["A", "B", "C", "D"]
Role = Literal["FIXED", "ROTATING"]


class MeasurementSetRowIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    group: Group
    role: Role | None = None


class MeasurementSetSave(BaseModel):
    rows: list[MeasurementSetRowIn] = Field(max_length=60)


class MeasurementSetRowOut(BaseModel):
    target_id: uuid.UUID | None = None
    text: str
    group: Group
    role: Role | None = None
    status: str | None = None
    problems: list[str] = Field(default_factory=list)


class LegacyQuestionOut(BaseModel):
    target_id: uuid.UUID
    text: str


class MeasurementSetShape(BaseModel):
    fixed: int
    rotating: int
    candidates: int
    problems: list[str]


class MeasurementSetResponse(BaseModel):
    hospital_id: uuid.UUID
    curated: bool
    rows: list[MeasurementSetRowOut]
    legacy_questions: list[LegacyQuestionOut]
    shape: MeasurementSetShape
    plan: dict[str, dict[str, int]]
    next_measurement_at: datetime
    next_measurement_period: str
    # 1일 새벽처럼 이번 측정표를 이미 얼렸으면 지금 저장한 세트는 그다음 측정부터 쓰인다.
    current_period_frozen: bool


class MeasurementSetDraftResponse(BaseModel):
    rows: list[MeasurementSetRowOut]
    shape: MeasurementSetShape


async def _hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


def _measurement_schedule(now: datetime) -> tuple[datetime, str, tuple[int, int] | None]:
    """(다음 월간 측정 시작 시각, 그 측정이 재는 달, 지금 측정 중일 수 있는 달)."""

    local = now.astimezone(KST)
    this_month_start = local.replace(day=1, hour=0, minute=15, second=0, microsecond=0)
    if local < this_month_start:
        start = this_month_start
    else:
        first_next = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
        start = first_next.replace(hour=0, minute=15, second=0, microsecond=0)
    measured = (start - timedelta(days=1)).strftime("%Y-%m")
    in_progress = None
    if local.day == 1 and local.hour < 9:
        previous = local - timedelta(days=1)
        in_progress = (previous.year, previous.month)
    return start, measured, in_progress


def _shape(rows: list[SetRow], problems: list[dict]) -> MeasurementSetShape:
    return MeasurementSetShape(
        fixed=sum(row.role == measurement_set.ROLE_FIXED for row in rows),
        rotating=sum(row.role == measurement_set.ROLE_ROTATING for row in rows),
        candidates=sum(row.role is None for row in rows),
        problems=[
            message
            for problem in problems
            if problem.get("index") is None
            for message in problem.get("messages", [])
        ],
    )


def _row_problems(problems: list[dict]) -> dict[int, list[str]]:
    return {
        problem["index"]: list(problem.get("messages", []))
        for problem in problems
        if problem.get("index") is not None
    }


async def _payload(db: AsyncSession, hospital: Hospital) -> MeasurementSetResponse:
    targets = await load_targets(db, hospital.id)
    rows = rows_from_targets(targets)
    problems = validate_rows(hospital, rows) if rows else []
    by_row = _row_problems(problems)
    ids = {target.name: target for target in targets}
    curated = measurement_set.uses_curated_set(targets)
    start, measured, in_progress = _measurement_schedule(datetime.now(KST))
    frozen = False
    if in_progress is not None:
        frozen = (
            await db.execute(
                select(MonthlyMeasurementManifest.id).where(
                    MonthlyMeasurementManifest.hospital_id == hospital.id,
                    MonthlyMeasurementManifest.period_year == in_progress[0],
                    MonthlyMeasurementManifest.period_month == in_progress[1],
                )
            )
        ).scalar_one_or_none() is not None
    return MeasurementSetResponse(
        hospital_id=hospital.id,
        curated=curated,
        rows=[
            MeasurementSetRowOut(
                target_id=ids[row.text].id if row.text in ids else None,
                text=row.text,
                group=row.group,
                role=row.role,
                status=str(ids[row.text].status) if row.text in ids else None,
                problems=by_row.get(index, []),
            )
            for index, row in enumerate(rows)
        ],
        legacy_questions=[
            LegacyQuestionOut(target_id=target.id, text=target.name)
            for target in targets
            if not curated
            and bool(target.in_tracking_set)
            and str(target.status).upper() == "ACTIVE"
        ],
        shape=_shape(rows, problems),
        plan={group: {"fixed": fixed, "rotating": rotating} for group, (fixed, rotating) in GROUP_PLAN.items()},
        next_measurement_at=start,
        next_measurement_period=measured,
        current_period_frozen=frozen,
    )


@router.get("/{hospital_id}/measurement-set", response_model=MeasurementSetResponse)
async def get_measurement_set(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await _hospital_or_404(db, hospital_id)
    return await _payload(db, hospital)


@router.post("/{hospital_id}/measurement-set/draft", response_model=MeasurementSetDraftResponse)
async def draft_measurement_set(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await _hospital_or_404(db, hospital_id)
    rows = [SetRow(row.text, row.group, row.role) for row in draft_rows(hospital)]
    problems = validate_rows(hospital, rows)
    by_row = _row_problems(problems)
    return MeasurementSetDraftResponse(
        rows=[
            MeasurementSetRowOut(
                text=row.text, group=row.group, role=row.role, problems=by_row.get(index, [])
            )
            for index, row in enumerate(rows)
        ],
        shape=_shape(rows, problems),
    )


@router.put("/{hospital_id}/measurement-set", response_model=MeasurementSetResponse)
async def save_measurement_set(
    hospital_id: uuid.UUID,
    body: MeasurementSetSave,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _hospital_or_404(db, hospital_id)
    await acquire_hospital_advisory_lock(db, hospital.id)
    rows = [SetRow(row.text, row.group, row.role) for row in body.rows]
    try:
        summary = await apply_rows(db, hospital, rows, actor=default_actor())
    except MeasurementSetError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"code": "MEASUREMENT_SET_INVALID", "problems": exc.problems},
        ) from exc
    await write_audit_log(
        db,
        action="save_measurement_set",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital",
        target_id=hospital.id,
        detail={"source": "admin", **summary},
    )
    await db.commit()
    return await _payload(db, hospital)
