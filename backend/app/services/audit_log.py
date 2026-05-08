"""Admin audit log writer.

Single-actor model (1.0): actor is the configured `ADMIN_ACTOR_NAME` setting,
not a user-supplied header. Multi-user / NextAuth integration is post-1.0; the
shape allows passing an explicit actor (e.g. body.published_by) but we never
trust untrusted client headers.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit import AdminAuditLog


def default_actor() -> str:
    return _normalize(settings.ADMIN_ACTOR_NAME)


def normalize_actor(actor: str | None) -> str:
    if actor is None:
        return default_actor()
    return _normalize(actor)


def _normalize(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned[:100] if cleaned else "AE"


async def write_audit_log(
    db: AsyncSession,
    *,
    action: str,
    hospital_id: uuid.UUID | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    detail: dict | None = None,
) -> AdminAuditLog:
    """Add an audit row. Caller is responsible for `await db.commit()`.

    Convention: write_audit_log → db.commit() → external side-effects (queue, slack, etc).
    Never enqueue a side-effecting task before the audit row is durable.
    """
    log = AdminAuditLog(
        hospital_id=hospital_id,
        actor=normalize_actor(actor),
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
    )
    db.add(log)
    return log
