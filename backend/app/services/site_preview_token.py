"""병원 공개 페이지 섹션 설정 미리보기 토큰(설계 5절).

토큰은 **병원 id + 초안 id + 초안 revision + 만료 시각**에 묶인 HMAC 서명이다. 미리보기 API는 서명·만료를
확인한 뒤, 그 초안이 지금도 DRAFT이고 revision이 같은지 DB에서 다시 본다 — 저장·공개 반영·버리기·
복원·초기화가 일어나면 옛 토큰은 모두 열리지 않는다.

비밀값은 전용 `SITE_PREVIEW_TOKEN_SECRET`이다(다른 비밀값으로 대신하지 않는다). 비어 있으면 미리보기만
꺼진다(fail-closed) — 발급은 503, 확인은 언제나 실패. 부팅·readiness는 막지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass

from app.core.config import settings

PREVIEW_TTL_SECONDS = 600
_DOMAIN = b"site-preview|v1|"
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,400}\.[A-Za-z0-9_-]{43}")


class PreviewDisabledError(RuntimeError):
    """미리보기 비밀값이 설정되지 않았다."""


@dataclass(frozen=True)
class PreviewClaim:
    hospital_id: uuid.UUID
    draft_id: uuid.UUID
    revision: int
    expires_at: int


def preview_enabled() -> bool:
    return bool((settings.SITE_PREVIEW_TOKEN_SECRET or "").strip())


def _secret() -> bytes:
    secret = (settings.SITE_PREVIEW_TOKEN_SECRET or "").strip()
    if not secret:
        raise PreviewDisabledError("SITE_PREVIEW_TOKEN_SECRET is not configured")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_secret(), _DOMAIN + body.encode(), hashlib.sha256).digest())


def issue_preview_token(
    hospital_id: uuid.UUID, draft_id: uuid.UUID, revision: int, *, now: float | None = None
) -> tuple[str, int]:
    """(토큰, 만료 epoch 초)."""

    expires_at = int((now if now is not None else time.time()) + PREVIEW_TTL_SECONDS)
    payload = json.dumps(
        {"h": str(hospital_id), "d": str(draft_id), "r": int(revision), "e": expires_at},
        separators=(",", ":"),
        sort_keys=True,
    )
    body = _b64(payload.encode())
    return f"{body}.{_sign(body)}", expires_at


def verify_preview_token(token: str | None, *, now: float | None = None) -> PreviewClaim | None:
    """서명·만료가 맞으면 내용을, 아니면 None. 비밀값이 없으면 언제나 None."""

    if not token or not preview_enabled() or not _TOKEN_SHAPE.fullmatch(token):
        return None
    body, signature = token.split(".", 1)
    try:
        expected = _sign(body)
    except PreviewDisabledError:
        return None
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(_unb64(body))
        claim = PreviewClaim(
            hospital_id=uuid.UUID(data["h"]),
            draft_id=uuid.UUID(data["d"]),
            revision=int(data["r"]),
            expires_at=int(data["e"]),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if claim.expires_at <= (now if now is not None else time.time()):
        return None
    return claim


__all__ = (
    "PREVIEW_TTL_SECONDS",
    "PreviewClaim",
    "PreviewDisabledError",
    "issue_preview_token",
    "preview_enabled",
    "verify_preview_token",
)
