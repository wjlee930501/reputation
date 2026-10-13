"""운영 경보 메일 — 운영 Slack이 닿지 않을 때의 두 번째 경로(2026-10 자율 운영 감사 F8).

운영 채널 하나로 운영하므로 그 웹훅이 죽거나 외부 감시가 Slack에 전하지 못하면 사람에게 닿을
곳이 없다. 그때만 `OPS_ALERT_EMAIL`로 Resend 메일을 보낸다. 정상 경로가 아니다.

중복 방지는 `mailer.send_email`과 같은 규칙이다 — 호출자가 사건(사고 episode, 감시 에피소드)마다
결정적인 `idempotency_key`와 **그 사건 동안 고정된 본문**을 주고, Resend가 같은 키·같은 본문의
재요청을 24시간 동안 한 번으로 묶는다(재요청은 원래 응답을 돌려준다).

409는 전달 확인이 아니다. `mailer.send_email`은 같은 키가 아직 처리 중인 경우(동시 요청)와 같은 키에
다른 본문이 온 경우를 같은 예외로 올리고, 어느 쪽도 이 요청으로 메일이 나갔다는 뜻이 아니다. 그래서
409는 `UNKNOWN`이다 — 호출자는 '보냈음'을 기록하지 않고 제한된 간격으로 같은 키·같은 본문으로 다시
보낸다. 처리 중이던 원래 요청이 끝났으면 그 재요청이 원래 응답(2xx)을 받아 `SENT`가 된다.
"""

from __future__ import annotations

import enum
import html
import logging
import re

import httpx

from app.core.config import settings
from app.services import mailer

logger = logging.getLogger(__name__)

_HEADER_UNSAFE = re.compile(r"[\r\n\t]+")
_CONFLICT_PREFIX = "resend_idempotency_conflict"


class OpsAlertResult(enum.Enum):
    SENT = "sent"  # 2xx — Resend가 받았다(같은 키의 재요청이면 원래 응답)
    UNKNOWN = "unknown"  # 409 — 같은 키가 처리 중이거나 다른 본문으로 쓰였다. 전달 확인이 아니다
    FAILED = "failed"  # 설정 없음·4xx/5xx·네트워크 오류

    @property
    def delivered(self) -> bool:
        return self is OpsAlertResult.SENT


def ops_alert_configured() -> bool:
    return bool(settings.OPS_ALERT_EMAIL.strip()) and mailer.is_configured()


def _subject(title: str) -> str:
    return f"[Re:putation 운영 경보] {_HEADER_UNSAFE.sub(' ', title).strip()}"


def _html(title: str, lines: tuple[str, ...]) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 10px;font-size:14px;line-height:1.7;">{html.escape(line)}</p>'
        for line in lines
        if line.strip()
    )
    return (
        '<!DOCTYPE html><html lang="ko"><body style="margin:0;padding:24px;'
        "font-family:'Apple SD Gothic Neo',sans-serif;color:#1a1d21;\">"
        f'<h1 style="margin:0 0 16px;font-size:18px;">{html.escape(title)}</h1>'
        f"{paragraphs}</body></html>"
    )


async def send_ops_alert(
    *,
    idempotency_key: str,
    title: str,
    lines: tuple[str, ...],
    client: httpx.AsyncClient | None = None,
) -> OpsAlertResult:
    """Resend가 받았으면 SENT. 409는 UNKNOWN(WARNING), 설정 없음·실패는 FAILED(ERROR 로그)."""

    to = settings.OPS_ALERT_EMAIL.strip()
    if not to or not mailer.is_configured():
        # 'ops alert email:' 접두사 — API에서는 GCP 경보 slack_path_failures, Worker에서는
        # worker_beat_errors(모든 worker ERROR)가 이 줄을 이메일로 알린다.
        logger.error("ops alert email: not configured key=%s", idempotency_key)
        return OpsAlertResult.FAILED
    try:
        await mailer.send_email(
            to=to,
            subject=_subject(title),
            html=_html(title, lines),
            idempotency_key=idempotency_key,
            client=client,
        )
    except mailer.MailNotConfigured as exc:
        logger.error(
            "ops alert email: failed key=%s error=%s",
            idempotency_key,
            exc.__class__.__name__,
        )
        return OpsAlertResult.FAILED
    except RuntimeError as exc:
        if str(exc).startswith(_CONFLICT_PREFIX):
            logger.warning(
                "ops alert email: result unknown (idempotency conflict) key=%s — 같은 키로 다시 보낸다",
                idempotency_key,
            )
            return OpsAlertResult.UNKNOWN
        logger.error(
            "ops alert email: failed key=%s error=%s",
            idempotency_key,
            str(exc)[:80],
        )
        return OpsAlertResult.FAILED
    except httpx.HTTPError as exc:
        logger.error(
            "ops alert email: failed key=%s error=%s",
            idempotency_key,
            exc.__class__.__name__,
        )
        return OpsAlertResult.FAILED
    return OpsAlertResult.SENT
