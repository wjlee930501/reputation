"""Admin API 인증 — X-Admin-Key 헤더 검증 + rate limiting"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from limits import parse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.models.admin_user import AdminUser
from app.services.audit_log import (
    UNVERIFIED_ACTOR_PREFIX,
    reset_request_actor,
    set_request_actor,
)

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_ADMIN_RATE_LIMIT = parse("100/minute")

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

# H-10 — admin API는 공개 LB로 노출되어 있고 인가는 공유 X-Admin-Key 하나다. 키가 새면
# 세션 없이 전체 조작이 가능하고, 정상 운영자도 X-Admin-Actor를 마음대로 바꿔 쓸 수 있다.
# 그래서 사람이 일으키는 변경(쓰기)은 Admin BFF가 세션 인증 뒤 서명한 단언을 요구한다.
# 형식: `v1.<base64url(json)>.<hex hmac-sha256>` — 서명 대상은 `v1.<base64url(json)>`.
# json = {"email", "role", "iat"(ms), "exp"(ms), "nonce"}. 키는 BFF_ACTOR_SECRET.
# nonce는 저장하지 않는다(재생 저장소 없음) — 재생 창은 120초 TTL로만 제한한다.
_ACTOR_ASSERTION_HEADER = "X-Admin-Actor-Assertion"
_ACTOR_SYSTEM_HEADER = "X-Admin-Actor-System"
# 배치/CLI 호출은 세션이 없다. 사람이 아니라는 사실을 헤더로 명시하고 actor에 남긴다.
_SYSTEM_ACTOR_PREFIX = "system:"
_SYSTEM_ACTOR_JOB_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# 배포 직후 이전 JS를 띄워둔 admin 탭은 단언 없이 한 번 여기에 걸린다 — 운영자가 스스로
# 복구할 수 있도록 조치를 문구로 남긴다.
_ACTOR_ASSERTION_REQUIRED_DETAIL = {
    "code": "ACTOR_ASSERTION_REQUIRED",
    "message": "관리 화면을 새로고침한 뒤 다시 시도해 주세요(로그인 세션 갱신 필요).",
}
_ACTOR_ASSERTION_INVALID_DETAIL = {
    "code": "ACTOR_ASSERTION_INVALID",
    "message": "관리자 인증 정보가 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요.",
}
# 서명된 신원과 평문 X-Admin-Actor가 어긋나면 어느 쪽을 믿어야 하는지 알 수 없다.
# BFF는 언제나 같은 세션 이메일로 둘을 채우므로, 정상 브라우저 트래픽은 여기 걸리지 않는다.
_ACTOR_ASSERTION_MISMATCH_DETAIL = {
    "code": "ACTOR_ASSERTION_MISMATCH",
    "message": "관리자 인증 정보가 요청한 운영자와 일치하지 않습니다. 다시 로그인해 주세요.",
}
# 시스템 호출에는 사람 계정이 없다 — 사람 권한(OWNER/AE 등)에 기대는 경로는 거부한다.
SYSTEM_ACTOR_NOT_ALLOWED_DETAIL = {
    "code": "SYSTEM_ACTOR_NOT_ALLOWED",
    "message": "이 작업은 로그인한 운영자만 할 수 있습니다(시스템 호출로는 수행할 수 없습니다).",
}


def verify_actor_assertion(raw: str | None, *, secret: str, now_ms: int) -> dict | None:
    """BFF 서명 actor 단언을 검증해 payload를 돌려준다(실패 시 None)."""
    token = (raw or "").strip()
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    encoded, signature_hex = parts[1], parts[2]
    expected = hmac.new(
        secret.encode("utf-8"), f"v1.{encoded}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not secrets.compare_digest(signature_hex.lower(), expected):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or now_ms > exp:
        return None
    email = payload.get("email")
    if not isinstance(email, str) or not _ADMIN_ACTOR_EMAIL_RE.match(email.strip()):
        return None
    return payload


@dataclass(frozen=True)
class RequestActor:
    """이 요청의 유효 actor. 인가와 감사는 **여기서만** actor를 가져간다.

    - `email`: 사람 actor의 이메일. 단언 강제 구간에서는 **서명으로 검증된** 값이고,
      읽기·시크릿 미설정 구간에서는 종전대로 `X-Admin-Actor` 평문 값이다.
    - `system_job`: 배치/CLI 호출의 job 이름. 이 요청에는 사람 계정이 **없다**.
    - `claimed_actor`: 채택하지 않은 `X-Admin-Actor` 값. 기록용이며 인가·감사에 쓰지 않는다.
    - `verified`: 서명 단언 또는 시스템 헤더로 확정된 요청인지.
    """

    email: str | None
    system_job: str | None
    claimed_actor: str | None
    verified: bool

    @property
    def audit_actor(self) -> str | None:
        """감사 로그 actor 후보 — 시스템 호출은 job 이름, 사람은 이메일."""
        if self.system_job:
            return f"{_SYSTEM_ACTOR_PREFIX}{self.system_job}"
        return self.email


def resolve_request_actor(request: Request) -> RequestActor:
    """요청의 유효 actor를 결정하는 유일한 함수.

    쓰기 요청이고 `BFF_ACTOR_SECRET`이 설정된 환경에서는:
    - `X-Admin-Actor-System`이 있으면 시스템 호출이다. 사람 계정은 없고, 함께 온
      `X-Admin-Actor`는 **인가에도 감사에도 채택하지 않는다**(job 이름을 사칭 수단으로
      쓸 수 없어야 한다).
    - 없으면 서명 단언이 필수이고, 단언의 `email`이 곧 actor다. 평문 `X-Admin-Actor`가
      함께 왔는데 값이 다르면 403 `ACTOR_ASSERTION_MISMATCH` — 어느 쪽이 요청자인지
      확정할 수 없는 요청을 통과시키지 않는다.

    읽기 요청과 시크릿이 없는 로컬/테스트 환경에서는 종전 동작(`X-Admin-Actor`)을 유지한다
    (프로덕션 부팅은 config에서 시크릿을 강제한다).
    """
    header_actor = (request.headers.get("X-Admin-Actor") or "").strip() or None
    secret = settings.BFF_ACTOR_SECRET.strip()
    method = (getattr(request, "method", "") or "").upper()
    if not secret or method not in _WRITE_METHODS:
        return RequestActor(
            email=header_actor, system_job=None, claimed_actor=None, verified=False
        )

    system_job = (request.headers.get(_ACTOR_SYSTEM_HEADER) or "").strip()
    if system_job:
        if not _SYSTEM_ACTOR_JOB_RE.match(system_job):
            raise HTTPException(status_code=403, detail=_ACTOR_ASSERTION_INVALID_DETAIL)
        if header_actor:
            logger.warning(
                "system admin call carried an unsigned actor header: job=%s claimed=%s path=%s",
                system_job,
                header_actor[:90],
                request.url.path,
            )
        return RequestActor(
            email=None, system_job=system_job, claimed_actor=header_actor, verified=True
        )

    raw = request.headers.get(_ACTOR_ASSERTION_HEADER)
    if not (raw or "").strip():
        raise HTTPException(status_code=403, detail=_ACTOR_ASSERTION_REQUIRED_DETAIL)
    payload = verify_actor_assertion(raw, secret=secret, now_ms=int(time.time() * 1000))
    if payload is None:
        raise HTTPException(status_code=403, detail=_ACTOR_ASSERTION_INVALID_DETAIL)
    email = str(payload["email"]).strip()
    if header_actor and header_actor.lower() != email.lower():
        raise HTTPException(status_code=403, detail=_ACTOR_ASSERTION_MISMATCH_DETAIL)
    return RequestActor(email=email, system_job=None, claimed_actor=None, verified=True)


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

    incident/outbox 기록이 요청 경로를 지연시키지 않도록 백그라운드에서 처리한다.
    그만큼 느려진다. 경보 실패가 요청을 깨뜨려서도 안 되므로 전부 best-effort로 처리한다.
    """
    from app.services.ops_incident_alerts import open_ops_incident

    try:
        task = asyncio.get_running_loop().create_task(
            open_ops_incident(
                pipeline="admin_security",
                object_type="unverified_actor",
                object_id=actor,
                incident_type="UNVERIFIED_ADMIN_ACTOR",
                safe_error_code="UNVERIFIED_ADMIN_ACTOR",
                problem="활성 관리자 계정과 일치하지 않는 actor로 쓰기 요청이 시도되었습니다.",
                customer_impact="관리자 변경 요청의 신뢰성을 확인할 때까지 운영 기록 검토가 필요합니다.",
                next_action="운영센터의 감사 기록에서 요청 경로와 계정 상태를 확인하세요.",
                source_type="ADMIN_SECURITY",
                actor="admin-security",
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
    effective = resolve_request_actor(request)
    if effective.system_job:
        # 시스템 호출은 매칭할 AdminUser가 없다 — job 이름을 그대로 감사 기록에 남긴다.
        # 함께 온 X-Admin-Actor(claimed_actor)는 여기서도 채택하지 않는다.
        actor = effective.audit_actor
    else:
        # 단언에서 온 이메일도 기존 활성 계정 매칭을 그대로 통과해야 한다.
        actor = await _resolve_admin_actor(db, effective.email)
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
