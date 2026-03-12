"""공통 API 의존성"""
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital


async def get_hospital_or_404(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Hospital:
    """병원 조회 — 없으면 404"""
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h
