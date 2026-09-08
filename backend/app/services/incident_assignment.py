"""새로 열린 예외의 담당자를 정하는 공통 규칙 (H-15).

예외를 여는 경로는 셋이다 — 서비스의 `open_or_touch_incident`(async), generic Celery
실패 투영(`workers/task_incident_control`), 자동 복구가 멈춘 안전하지 않은 재실행
(`workers/autonomous_recovery`). 한 곳에서만 배정하면 나머지 두 경로로 열린 예외는
주인이 없고, 아무도 자기 일로 보지 않는다.

후보 규칙은 한 벌이다: 병원의 계약 인수 AE가 1순위이고, 없으면 활성 OWNER 한 명이
받는다. 두 후보 모두 배정 라우트·`assignable_accounts`와 같은 자격(활성 계정, 운영
점검 계정 제외)을 요구한다 — 퇴사·정지된 계정에 맡기면 담당자는 있는데 아무도 보지
않는 예외가 된다. 후보가 없으면 그대로 비워 둔다(실패 아님).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.handoff import HospitalHandoff
from app.models.operations import Incident

__all__ = (
    "UNASSIGNED_OWNER_LABEL",
    "auto_assign_owner_sync",
    "needs_auto_assign",
    "owner_label",
    "owner_label_sync",
    "resolve_auto_assign_owner",
    "resolve_auto_assign_owner_sync",
)

# Slack 투영이 담당자를 모를 때 쓰는 라벨. `notification_messages`가 이 값을
# "미지정(담당자 지정 필요)"로 다시 쓴다.
UNASSIGNED_OWNER_LABEL: Final = "미지정"


def needs_auto_assign(
    owner_id: uuid.UUID | None, first_seen_at: datetime | None, observed_at: datetime
) -> bool:
    """이번 관측이 "새 에피소드의 첫 open"인가.

    `first_seen_at`은 새 행과 재open에서만 이번 관측 시각으로 맞춰지므로, 같은
    에피소드의 반복 관측에서는 후보 조회가 아예 돌지 않는다. 이미 담당자가 있으면
    절대 덮지 않는다 — 재발로 다시 열린 건은 그 사람이 계속 본다.
    """
    return owner_id is None and first_seen_at == observed_at


def _handoff_owner_select(hospital_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return (
        select(AdminUser.id)
        .join(HospitalHandoff, HospitalHandoff.ae_owner_id == AdminUser.id)
        .where(
            HospitalHandoff.hospital_id == hospital_id,
            AdminUser.is_active.is_(True),
            AdminUser.is_operations_test.is_(False),
        )
        .limit(1)
    )


def _fallback_owner_select() -> Select[tuple[uuid.UUID]]:
    return (
        select(AdminUser.id)
        .where(
            AdminUser.role == ROLE_OWNER,
            AdminUser.is_active.is_(True),
            AdminUser.is_operations_test.is_(False),
        )
        .order_by(AdminUser.created_at.asc(), AdminUser.id.asc())
        .limit(1)
    )


async def resolve_auto_assign_owner(
    db: AsyncSession, hospital_id: uuid.UUID | None
) -> uuid.UUID | None:
    """이 병원 예외를 맡을 계정 하나. 후보가 없으면 None."""
    if hospital_id is not None:
        owner_id = await db.scalar(_handoff_owner_select(hospital_id))
        if owner_id is not None:
            return owner_id
    return await db.scalar(_fallback_owner_select())


def resolve_auto_assign_owner_sync(
    db: Session, hospital_id: uuid.UUID | None
) -> uuid.UUID | None:
    """`resolve_auto_assign_owner`의 worker(sync) 판. 같은 질의를 쓴다."""
    if hospital_id is not None:
        owner_id = db.scalar(_handoff_owner_select(hospital_id))
        if owner_id is not None:
            return owner_id
    return db.scalar(_fallback_owner_select())


def auto_assign_owner_sync(
    db: Session, incident: Incident, *, observed_at: datetime
) -> uuid.UUID | None:
    """worker가 연 예외에 담당자를 채운다. 채웠으면 그 계정 id를 돌려준다.

    아직 flush 되지 않은 새 행(자동 복구의 직접 생성)과 upsert가 돌려준 행 모두에
    같은 방식으로 쓴다 — 값만 채우고 커밋은 호출한 쪽의 트랜잭션에 맡긴다.
    """
    if not needs_auto_assign(incident.owner_id, incident.first_seen_at, observed_at):
        return None
    owner_id = resolve_auto_assign_owner_sync(db, incident.hospital_id)
    if owner_id is None:
        return None
    incident.owner_id = owner_id
    return owner_id


def owner_label_sync(db: Session, owner_id: uuid.UUID | None) -> str:
    """Slack 투영에 실을 담당자 이름. 배정된 사람이 있으면 "미지정"이라고 하지 않는다."""
    if owner_id is None:
        return UNASSIGNED_OWNER_LABEL
    return db.scalar(select(AdminUser.name).where(AdminUser.id == owner_id)) or (
        UNASSIGNED_OWNER_LABEL
    )


async def owner_label(db: AsyncSession, owner_id: uuid.UUID | None) -> str:
    """`owner_label_sync`의 async 판."""
    if owner_id is None:
        return UNASSIGNED_OWNER_LABEL
    return await db.scalar(select(AdminUser.name).where(AdminUser.id == owner_id)) or (
        UNASSIGNED_OWNER_LABEL
    )
