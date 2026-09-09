"""Internal write hooks and non-persistent BaseEssence overlays.

Call create/retire inside the caller's transaction (AsyncSession.run_sync is
supported). All activation goes through create; retired feedback is immutable.
Only trusted admin/worker callers should invoke these hooks. Example:
    with session.begin():
        create_director_delta(session, hospital_id, DirectorDeltaInput(
            source="DIRECTOR", avoid_messages=["feedback to avoid"],
        ))
Async callers use await session.run_sync(create_director_delta, hospital_id, payload).
Retire by hospital + delta ID; to revise feedback, explicitly retire then create.
No approval, synthesis, notification, or automatic retirement side effects.
"""

import uuid
from copy import deepcopy
from types import SimpleNamespace
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.director_delta import DirectorDelta, DirectorDeltaSource, DirectorDeltaStatus
from app.models.hospital import Hospital

MAX_ACTIVE_DELTAS = 50
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class DirectorDeltaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: DirectorDeltaSource
    avoid_messages: list[Message] = Field(default_factory=list, max_length=50)
    prefer_topics: list[Message] = Field(default_factory=list, max_length=50)
    prefer_messages: list[Message] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=10000)


class ActiveDirectorDeltaLimit(ValueError):
    pass


def create_director_delta(
    db: Session, hospital_id: uuid.UUID, payload: DirectorDeltaInput
) -> DirectorDelta:
    # Serialize count + insert even when there are no existing deltas to lock.
    hospital = db.execute(
        select(Hospital.id).where(Hospital.id == hospital_id).with_for_update()
    ).scalar_one_or_none()
    if hospital is None:
        raise ValueError("Hospital not found")
    count = db.scalar(
        select(func.count())
        .select_from(DirectorDelta)
        .where(
            DirectorDelta.hospital_id == hospital_id,
            DirectorDelta.status == DirectorDeltaStatus.ACTIVE,
        )
    )
    if count >= MAX_ACTIVE_DELTAS:
        raise ActiveDirectorDeltaLimit(
            "At most 50 ACTIVE director deltas per hospital; manually retire feedback first"
        )
    delta = DirectorDelta(
        hospital_id=hospital_id, status=DirectorDeltaStatus.ACTIVE, **payload.model_dump()
    )
    db.add(delta)
    db.flush()
    return delta


def retire_director_delta(
    db: Session, hospital_id: uuid.UUID, delta_id: uuid.UUID
) -> DirectorDelta:
    delta = db.execute(
        select(DirectorDelta)
        .where(
            DirectorDelta.hospital_id == hospital_id,
            DirectorDelta.id == delta_id,
        )
        .with_for_update()
    ).scalar_one()
    delta.status = DirectorDeltaStatus.RETIRED
    db.flush()
    return delta


def active_deltas_query(hospital_id):
    return (
        select(DirectorDelta)
        .where(
            DirectorDelta.hospital_id == hospital_id,
            DirectorDelta.status == DirectorDeltaStatus.ACTIVE,
        )
        .order_by(DirectorDelta.created_at.asc(), DirectorDelta.id.asc())
    )


def merge_director_deltas(base, deltas):
    """Return an unmapped snapshot. Only allowlisted fields can change.

    Oldest first, newest preferences last. Preferences remain optional and avoid
    always wins. No limit on reads: never silently discard existing restrictions.
    """
    if base is None:
        return None
    active = [
        d
        for d in deltas
        if d.status == DirectorDeltaStatus.ACTIVE and d.hospital_id == base.hospital_id
    ]
    if not active:
        return base
    fields = base.__table__.columns.keys() if hasattr(base, "__table__") else vars(base).keys()
    values = {key: deepcopy(getattr(base, key)) for key in fields}
    for field in ("avoid_messages", "prefer_topics", "prefer_messages"):
        merged = list(values.get(field) or [])
        for delta in active:
            for value in getattr(delta, field) or []:
                if value not in merged:
                    merged.append(value)
        values[field] = merged
    avoided = values["avoid_messages"]
    for field in ("prefer_topics", "prefer_messages"):
        values[field] = [value for value in values[field] if value not in avoided]
    values["director_delta_ids"] = [str(delta.id) for delta in active]
    return SimpleNamespace(**values)


async def effective_philosophy(db, base):
    if base is None:
        return None
    deltas = (await db.execute(active_deltas_query(base.hospital_id))).scalars().all()
    return merge_director_deltas(base, deltas)


def effective_philosophy_sync(db, base):
    if base is None:
        return None
    return merge_director_deltas(
        base, db.execute(active_deltas_query(base.hospital_id)).scalars().all()
    )
