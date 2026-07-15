"""Admin audit log writer.

The Admin Next.js server verifies the operator session and forwards the account
email as request-local actor context for backend audit rows. Direct callers fall
back to `ADMIN_ACTOR_NAME`.
"""
from contextvars import ContextVar, Token
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AdminAuditLog

_request_actor: ContextVar[str | None] = ContextVar("admin_request_actor", default=None)


def set_request_actor(actor: str | None) -> Token[str | None]:
    return _request_actor.set(_normalize(actor))


def reset_request_actor(token: Token[str | None]) -> None:
    _request_actor.reset(token)


def default_actor() -> str:
    actor = _request_actor.get()
    if actor:
        return actor
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


def write_audit_log_sync(
    db: Session,
    *,
    action: str,
    hospital_id: uuid.UUID | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    detail: dict | None = None,
) -> AdminAuditLog:
    """Synchronous worker counterpart of :func:`write_audit_log`.

    The caller must commit before Slack, revalidation, or other external effects.
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
