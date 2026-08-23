import uuid
from contextvars import ContextVar, Token

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AdminAuditLog

# 활성 관리자 계정과 매칭되지 않은 actor 값에 붙는 접두어. 요청 컨텍스트를 다루는
# 이 모듈에 두고, security.py가 여기서 가져간다(security → audit_log 방향 의존을 유지).
UNVERIFIED_ACTOR_PREFIX = "unverified:"

_request_actor: ContextVar[str | None] = ContextVar("admin_request_actor", default=None)


def set_request_actor(actor: str | None) -> Token[str | None]:
    return _request_actor.set(_normalize(actor) if actor is not None else None)


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


def verified_request_actor() -> str | None:
    """활성 관리자 계정으로 확인된 요청자. 확인되지 않으면 None.

    승인 기록처럼 "누가 했는가"가 판단의 근거가 되는 값은 클라이언트가 보낸 이름이 아니라
    이 값을 써야 한다. 헤더가 없는 배치/시스템 호출과, 활성 계정과 매칭되지 않은
    `unverified:` 값은 확인된 요청자가 아니다.
    """
    actor = _request_actor.get()
    if not actor or actor.startswith(UNVERIFIED_ACTOR_PREFIX):
        return None
    return actor


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
