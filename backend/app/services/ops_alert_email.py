"""운영 경보 메일 — 운영 Slack이 닿지 않을 때의 두 번째 경로(2026-10 자율 운영 감사 F8).

운영 채널 하나로 운영하므로 그 웹훅이 죽거나 외부 감시가 Slack에 전하지 못하면 사람에게 닿을
곳이 없다. 그때만 `OPS_ALERT_EMAIL`로 Resend 메일을 보낸다. 정상 경로가 아니다.

중복 방지는 `mailer.send_email`과 같은 규칙이다 — 호출자가 사건(사고 episode, 감시 에피소드)마다
결정적인 `idempotency_key`를 주고, Resend가 같은 키의 재요청을 24시간 동안 한 번으로 묶는다. 같은
키에 다른 본문이 와서 409가 나면 그 키로 이미 보냈다는 뜻이므로 보낸 것으로 본다.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from app.core.config import settings
from app.services import mailer

logger = logging.getLogger(__name__)

_HEADER_UNSAFE = re.compile(r"[\r\n\t]+")
_CONFLICT_PREFIX = "resend_idempotency_conflict"


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
) -> bool:
    """보냈거나 같은 키로 이미 보냈으면 True. 설정이 없거나 실패하면 False와 ERROR 로그."""

    to = settings.OPS_ALERT_EMAIL.strip()
    if not to or not mailer.is_configured():
        # 2-6b의 GCP 로그 경보 필터("pipeline watchdog")가 이 줄을 잡는다.
        logger.error("pipeline watchdog: ops alert email not configured key=%s", idempotency_key)
        return False
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
            "pipeline watchdog: ops alert email failed key=%s error=%s",
            idempotency_key,
            exc.__class__.__name__,
        )
        return False
    except RuntimeError as exc:
        if str(exc).startswith(_CONFLICT_PREFIX):
            return True
        logger.error(
            "pipeline watchdog: ops alert email failed key=%s error=%s",
            idempotency_key,
            str(exc)[:80],
        )
        return False
    except httpx.HTTPError as exc:
        logger.error(
            "pipeline watchdog: ops alert email failed key=%s error=%s",
            idempotency_key,
            exc.__class__.__name__,
        )
        return False
    return True
