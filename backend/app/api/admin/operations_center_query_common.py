"""Shared typed filters for operations-center queue queries."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from fastapi import HTTPException
from sqlalchemy import or_


class SlaFilter(StrEnum):
    OVERDUE = "OVERDUE"
    DUE = "DUE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class OperationsFilters:
    owner: str | None = None
    status: str | None = None
    severity: str | None = None
    sla: SlaFilter | None = None


def normalize_filters(
    *,
    owner: str | None,
    status: str | None,
    severity: str | None,
    sla: str | None,
) -> OperationsFilters:
    try:
        parsed_sla = SlaFilter(sla.upper()) if sla else None
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_SLA_FILTER",
                "message": (
                    "처리 기한 필터 값이 올바르지 않습니다. 운영 센터에서 기한 지남, "
                    "기한 남음, 기한 없음 중 하나를 선택해 다시 조회하세요."
                ),
            },
        ) from exc
    return OperationsFilters(
        owner=owner.strip() if owner else None,
        status=status.upper() if status else None,
        severity=severity.upper() if severity else None,
        sla=parsed_sla,
    )


def owner_predicate(owner_alias, owner: str | None):
    if owner is None:
        return None
    try:
        owner_id = uuid.UUID(owner)
    except ValueError:
        needle = f"%{owner}%"
        return or_(owner_alias.name.ilike(needle), owner_alias.email.ilike(needle))
    return owner_alias.id == owner_id


def sla_predicate(column, sla: SlaFilter | None, now: datetime):
    match sla:
        case None:
            return None
        case SlaFilter.OVERDUE:
            return column < now
        case SlaFilter.DUE:
            return column >= now
        case SlaFilter.NONE:
            return column.is_(None)
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "OperationsFilters",
    "SlaFilter",
    "normalize_filters",
    "owner_predicate",
    "sla_predicate",
)
