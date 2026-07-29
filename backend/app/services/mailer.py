"""이메일 발송 (Resend) — `notifier.py`는 Slack 전용이라 새로 만든다 (PRD F5-4).

## 중복 발송을 막는 방법

"메일 발송 성공 → delivery 커밋 실패 → 재시도 → 두 번 발송"은 dual-write의 전형이다.
분산 트랜잭션 없이 막는 순서는 하나뿐이다 — **의도를 부수효과보다 먼저 커밋한다.**

    1. INSERT lead_deliveries (status=SENDING)  → COMMIT
    2. Resend POST  with  Idempotency-Key: <행 id>
    3. UPDATE status=SENT                        → COMMIT

3번이 실패하면 행은 SENDING으로 남고, 스윕이 **같은 키로** 재시도한다. Resend는 같은
키의 재요청에 원래 응답을 돌려주고 메일을 다시 보내지 않는다.

## 24시간이 재시도 창을 정한다

Resend는 idempotency key를 **24시간만** 보관한다(공식 문서). 그 이후의 재시도는
멱등성이 보장되지 않으므로 **자동 재시도를 그 창 안으로 제한**하고, 넘긴 건은
"보냈는지 알 수 없는 상태"로 사람에게 넘긴다. 자동 재발송보다 낫다.

또 같은 키에 **다른 payload가 오면 409**를 준다. 그래서 본문은 재시도 때 바이트
동일해야 하고, 입력은 DB에서 오는 값(병원명·리포트 URL)만 쓴다.
"""
import html
import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 20.0


class MailNotConfigured(RuntimeError):
    """API 키가 없다. 조용히 성공 처리하지 않기 위해 예외로 올린다."""


@dataclass(frozen=True)
class MailResult:
    provider_message_id: str | None
    already_sent: bool = False   # 같은 키로 이미 처리된 요청


def is_configured() -> bool:
    return bool(settings.RESEND_API_KEY.strip())


async def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    idempotency_key: str,
    client: httpx.AsyncClient | None = None,
) -> MailResult:
    """1통 발송. `idempotency_key`는 delivery 행의 UUID다.

    attempt가 올라가도 **같은 키를 유지해야** 중복이 안 난다 — attempt별로 새 키를
    만들면 재시도가 그대로 두 번째 메일이 된다.
    """
    if not is_configured():
        raise MailNotConfigured("RESEND_API_KEY가 설정되지 않았습니다.")

    payload = {
        "from": settings.LEAD_MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    reply_to = settings.LEAD_MAIL_REPLY_TO.strip()
    if reply_to:
        payload["reply_to"] = [reply_to]

    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}",
        "Idempotency-Key": idempotency_key,
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    try:
        response = await client.post(RESEND_ENDPOINT, json=payload, headers=headers)
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 409:
        # 같은 키가 이미 처리 중이거나(concurrent) 다른 payload로 쓰였다(invalid).
        # 어느 쪽이든 지금 이 요청으로 메일이 나가지 않았다 — 재시도는 스윕에 맡긴다.
        raise RuntimeError(f"resend_idempotency_conflict:{response.text[:200]}")
    if response.status_code >= 400:
        raise RuntimeError(f"resend_error_{response.status_code}:{response.text[:200]}")

    body = response.json() if response.content else {}
    return MailResult(provider_message_id=body.get("id"))


# 제목 인젝션 방지 — 헤더에 개행이 들어가면 임의 헤더를 덧붙일 수 있다.
_HEADER_UNSAFE = re.compile(r"[\r\n\t]+")


def build_report_email_html(*, hospital_name: str, report_url: str) -> str:
    """본문은 **결정적이어야 한다** — 같은 키에 다른 payload가 가면 Resend가 409를 준다.

    그래서 생성 시각·난수 같은 것을 넣지 않는다. 입력은 DB에서 오는 두 값뿐이다.

    **병원명은 사용자 입력이므로 escape한다.** 그대로 끼워 넣으면 병원명 칸에 HTML을
    넣고 임의 이메일을 수신자로 제출해, 우리 발신 도메인으로 공격자가 만든 링크가 담긴
    메일을 보낼 수 있다 — 하루 20건이어도 피싱이자 발신 평판 훼손 경로다.
    """
    hospital_name = html.escape(hospital_name, quote=True)
    report_url = html.escape(report_url, quote=True)
    return f"""\
<!DOCTYPE html>
<html lang="ko">
<body style="margin:0;padding:24px;background:#f6f7f9;font-family:'Apple SD Gothic Neo',sans-serif;color:#1a1d21;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:10px;padding:32px;">
    <h1 style="margin:0 0 8px;font-size:19px;">{hospital_name} AI 노출 진단 결과</h1>
    <p style="margin:0 0 20px;color:#5b6672;font-size:14px;line-height:1.7;">
      신청하신 진단이 완료되었습니다. 아래에서 리포트를 확인하실 수 있습니다.
    </p>
    <p style="margin:0 0 24px;">
      <a href="{report_url}"
         style="display:inline-block;background:#1a1d21;color:#ffffff;text-decoration:none;
                padding:12px 22px;border-radius:6px;font-size:14px;">리포트 확인하기</a>
    </p>
    <p style="margin:0;color:#8a94a0;font-size:12px;line-height:1.7;">
      본 자료는 귀 병원 내부 참고용 진단 자료이며 광고물이 아닙니다.<br>
      측정과 리포트 생성에 인공지능이 사용되었습니다.<br>
      링크는 발급일로부터 30일간 유효합니다.
    </p>
  </div>
</body>
</html>"""


def build_report_email_subject(hospital_name: str) -> str:
    """제목의 개행·탭을 제거한다 — 헤더 인젝션 경로."""
    safe = _HEADER_UNSAFE.sub(" ", hospital_name).strip()
    return f"[Re:putation] {safe} AI 노출 진단 결과"
