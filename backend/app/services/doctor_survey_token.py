"""원장 사전 설문 링크 토큰.

`lead_report_token`과 같은 방식이다 — 불투명 난수처럼 보이는 결정적 HMAC 원문 + pepper를 섞은 sha256만
저장한다. 원문을 저장하지 않아도 AE가 언제든 같은 링크를 다시 복사할 수 있도록 `(병원 id, 판 번호)`에서
다시 만든다. 새 링크를 만들면 판 번호가 올라 옛 링크의 해시는 DB에 없으므로 열리지 않는다.

비밀값은 리포트 링크와 같은 `LEAD_REPORT_TOKEN_SECRET`을 쓰되, 재료와 해시에 서로 다른 접두어
(``doctor-survey``)를 넣어 두 링크가 서로를 만들 수 없게 한다. 새 비밀값을 만들지 않은 이유는 운영 env를
바꾸지 않기 위해서다(배포 env 정본 규칙).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

from app.core.config import settings

_DOMAIN = "doctor-survey"
SURVEY_PATH = "/survey"


def derive_survey_token(hospital_id: uuid.UUID | str, token_version: int) -> str:
    digest = hmac.new(
        settings.LEAD_REPORT_TOKEN_SECRET.encode(),
        f"{_DOMAIN}|{hospital_id}|{int(token_version)}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def hash_survey_token(raw: str) -> str:
    pepper = settings.LEAD_REPORT_TOKEN_SECRET
    return hashlib.sha256(f"{_DOMAIN}|{raw}|{pepper}".encode()).hexdigest()


def issue_survey_token(hospital_id: uuid.UUID | str, token_version: int) -> tuple[str, str]:
    """(원문, 해시)."""

    raw = derive_survey_token(hospital_id, token_version)
    return raw, hash_survey_token(raw)


def survey_url(raw_token: str) -> str:
    return f"{settings.SITE_BASE_URL.rstrip('/')}{SURVEY_PATH}/{raw_token}"


__all__ = (
    "SURVEY_PATH",
    "derive_survey_token",
    "hash_survey_token",
    "issue_survey_token",
    "survey_url",
)
