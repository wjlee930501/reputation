"""Admin API — sales lead intake review."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import SalesLead

router = APIRouter(prefix="/admin/leads", tags=["Admin — Leads"])


@router.get("")
async def list_sales_leads(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    result = await db.execute(
        select(SalesLead).order_by(SalesLead.created_at.desc()).limit(limit)
    )
    return [_serialize_lead(lead) for lead in result.scalars().all()]


def _serialize_lead(lead: SalesLead) -> dict:
    return {
        "id": str(lead.id),
        "clinic_name": lead.clinic_name,
        "clinic_type": lead.clinic_type,
        "contact": lead.contact,
        "question": lead.question,
        "privacy": lead.privacy,
        "source_path": lead.source_path,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }
