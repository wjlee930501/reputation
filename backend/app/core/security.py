"""Admin API 인증 — X-Admin-Key 헤더 검증 + rate limiting"""
import asyncio
from collections.abc import AsyncGenerator
import logging
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from limits import parse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.models.admin_user import AdminUser
from app.services.audit_log import reset_request_actor, set_request_actor

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_ADMIN_RATE_LIMIT = parse("100/minute")

UNVERIFIED_ACTOR_PREFIX = "unverified:"
# 인가는 공유 X-Admin-Key로 이뤄지므로 계정 비활성화만으로는 백엔드 권한이 끊기지 않는다.
# 최소한 "검증되지 않은 actor가 상태를 바꾸는" 순간은 반드시 드러나야 하므로, 쓰기 메서드는
# 로그 + Slack 경보 대상으로 삼는다 (읽기는 소음이 커 로그만 남긴다).
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 위조 헤더를 반복 전송하면 Slack 채널이 그대로 flood된다 — actor별로 창을 두고 억제한다.
_UNVERIFIED_ALERT_WINDOW_SECONDS = 600.0
_unverified_alert_sent_at: dict[str, float] = {}
# create_task 결과를 강참조하지 않으면 GC가 실행 중인 알림 태스크를 회수할 수 있다.
_pending_alert_tasks: set[asyncio.Task] = set()

# X-Admin-Actor는 Admin BFF가 세션 인증 후 전달하는 운영자 이메일이다. 헤더 자체는
# X-Admin-Key만 알면 위조할 수 있으므로, 값이 실제 활성 AdminUser.email과 매칭될 때만
# 채택하고, 형식이 다르거나 매칭되지 않으면 'unverified:{value}'로 표시해 감사 로그에서
# 위조 가능성을 드러낸다 (#5).
_ADMIN_ACTOR_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def verify_admin_key(key: str | None = Security(api_key_header)) -> str:
    admin_secret = settings.ADMIN_SECRET_KEY.strip()
    if not key or not admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    if not secrets.compare_digest(key.encode("utf-8"), admin_secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return key


async def verify_admin_rate_limit(request: Request) -> None:
    """Rate limit admin API calls. Relies on slowapi limiter being mounted at app.state."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is None or not getattr(limiter, "enabled", True):
        return
    strategy = limiter.limiter
    limit_key = f"admin:{get_request_ip(request) or 'unknown'}"
    if not strategy.hit(_ADMIN_RATE_LIMIT, limit_key):
        raise HTTPException(status_code=429, detail="Too many requests")


async def _resolve_admin_actor(db: AsyncSession, raw: str | None) -> str | None:
    """X-Admin-Actor 헤더를 검증해 감사 로그 actor로 채택할 값을 결정한다.

    - 헤더 없음 → None (audit_log가 ADMIN_ACTOR_NAME으로 폴백).
    - 이메일 형식이 아니거나 활성 AdminUser와 매칭 안 됨 → 'unverified:{value}'.
    - 활성 AdminUser.email과 매칭 → 정규 email 채택.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    if not _ADMIN_ACTOR_EMAIL_RE.match(cleaned):
        return f"{UNVERIFIED_ACTOR_PREFIX}{cleaned[:90]}"
    try:
        result = await db.execute(
            select(AdminUser.email).where(
                func.lower(AdminUser.email) == cleaned.lower(),
                AdminUser.is_active.is_(True),
            )
        )
        matched = result.scalar_one_or_none()
    except Exception:
        # DB 조회 실패 시 공유 세션이 실패 상태로 남으면 이후 엔드포인트 쿼리가
        # PendingRollbackError로 500이 된다 — 먼저 롤백해 세션을 회복시킨 뒤,
        # 헤더는 그대로 신뢰하지 않고 위조 가능성으로 표시한다.
        try:
            await db.rollback()
        except Exception:
            # 롤백 자체 실패(연결 끊김 등)도 actor 판정을 막지 않는다 — best-effort.
            pass
        return f"{UNVERIFIED_ACTOR_PREFIX}{cleaned[:90]}"
    if matched:
        return matched
    return f"{UNVERIFIED_ACTOR_PREFIX}{cleaned[:90]}"


def _should_alert_unverified(actor: str, *, now: float | None = None) -> bool:
    """actor별 억제 창 안에서는 한 번만 경보한다(위조 헤더 반복 전송 → Slack flood 방지)."""
    current = time.monotonic() if now is None else now
    last = _unverified_alert_sent_at.get(actor)
    if last is not None and current - last < _UNVERIFIED_ALERT_WINDOW_SECONDS:
        return False
    _unverified_alert_sent_at[actor] = current
    return True


def _alert_unverified_actor(actor: str, method: str, path: str) -> None:
    """Slack 경보를 백그라운드로 던진다.

    notifier._send는 재시도 포함 수 초가 걸릴 수 있어 요청 경로에서 await하면 Admin 응답이
    그만큼 느려진다. 경보 실패가 요청을 깨뜨려서도 안 되므로 전부 best-effort로 처리한다.
    """
    from app.services import notifier  # 지연 import — core가 services에 시작 시점 의존하지 않게.

    try:
        task = asyncio.get_running_loop().create_task(
            notifier.notify_ops_alert(
                title="검증되지 않은 관리자 actor의 쓰기 요청",
                message=(
                    f"actor: `{actor}`\n요청: `{method} {path}`\n"
                    "활성 AdminUser와 매칭되지 않는 X-Admin-Actor로 상태 변경이 시도되었습니다. "
                    "비활성화·퇴사 계정이거나 헤더 위조일 수 있으니 감사 로그를 확인해 주세요."
                ),
            )
        )
    except RuntimeError:
        # 실행 중인 루프가 없으면(동기 컨텍스트) 로그만으로 충분하다 — 경보는 부가 신호다.
        return
    _pending_alert_tasks.add(task)
    task.add_done_callback(_pending_alert_tasks.discard)


async def capture_admin_actor(
    request: Request, db: AsyncSession = Depends(get_db)
) -> AsyncGenerator[None, None]:
    actor = await _resolve_admin_actor(db, request.headers.get("X-Admin-Actor"))
    # actor is None = 헤더 미전송(배치/시스템 호출). default_actor 폴백 경로라 건드리지 않는다.
    if actor is not None and actor.startswith(UNVERIFIED_ACTOR_PREFIX):
        method = (request.method or "").upper()
        path = request.url.path
        is_write = method in _WRITE_METHODS
        logger.warning(
            "admin actor not verified: actor=%s method=%s path=%s write=%s",
            actor,
            method,
            path,
            is_write,
        )
        if is_write:
            if settings.ADMIN_REJECT_UNVERIFIED_ACTOR:
                raise HTTPException(status_code=403, detail="Admin actor is not verified")
            if _should_alert_unverified(actor):
                _alert_unverified_actor(actor, method, path)
    token = set_request_actor(actor)
    try:
        yield
    finally:
        reset_request_actor(token)
