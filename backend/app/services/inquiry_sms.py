"""도입문의 접수 직후 원장 휴대전화로 나가는 안내 문자(LMS) — NHN Cloud Notification SMS.

리드가 저장된 뒤에만 부르고, 결과는 리드 행(`ack_sms_*`)에 남긴다. 발송 실패는 접수를
되돌리지 않는다 — 문자는 리드의 부속물이지 리드 자체가 아니다(CLAUDE.md 운영 철학:
저장 성공 후 외부 호출 장애로 원래 업무를 실패로 만들지 않는다).

문자 본문은 원장의 문의에 대한 답변이라 정보통신망법 제50조의 영리목적 광고성 정보에
해당하지 않는다. 본문을 프로모션으로 바꾸면 (광고) 표기와 수신거부 안내가 필요해지므로
템플릿을 고칠 때 그 점을 함께 검토한다.

NHN Cloud SMS v3.0: `POST /sms/v3.0/appKeys/{appKey}/sender/mms`, 헤더 `X-Secret-Key`.
첨부 없는 MMS가 곧 LMS다(제목 40자, 본문 2,000바이트). 발신번호는 콘솔에 사전 등록되어
있어야 하며 미등록이면 per-recipient resultCode로 거절된다.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.lead import SalesLead

logger = logging.getLogger(__name__)

ACK_SMS_TITLE = "Re:putation 도입 문의 안내"

ACK_SMS_BODY = "\n".join(
    [
        "원장님, 안녕하세요. 모션랩스 마케팅팀 팀장 김효진입니다.",
        "Re:putation 도입 문의 주셔서 감사드립니다.",
        "",
        "최근 환자들이 AI 검색으로 병원을 찾는 비중이 빠르게 늘고 있습니다.",
        "다만 AI가 추천하는 병원은 2~3곳으로 한정되어 있어, 노출 여부에 따른 차이가 큽니다.",
        "",
        "문의 주신 내용은 병원별 노출 상황에 따라 답변이 달라지는 부분입니다.",
        "일반적인 소개보다는 원장님 병원의 실제 데이터를 보시면서 판단하시는 편이 가장 정확하여, "
        "현황 분석 자료를 준비해 설명드리겠습니다.",
        "",
        "현재 노출 진단과 도입 시 진행 프로세스까지 20분이면 안내드릴 수 있습니다.",
        "진료 일정 고려하셔서 편하신 시간 회신 주시면 맞춰서 방문하겠습니다.",
    ]
)

SMS_SENT = "SENT"
SMS_FAILED = "FAILED"
SMS_SKIPPED = "SKIPPED"

_MOBILE = re.compile(r"^01[016789]\d{7,8}$")


@dataclass(frozen=True)
class AckSmsOutcome:
    status: str  # SENT | FAILED | SKIPPED
    detail: str | None = None
    request_id: str | None = None

    @property
    def sent(self) -> bool:
        return self.status == SMS_SENT


def normalize_korean_mobile(value: str | None) -> str | None:
    """`010-1234-5678`·`+82 10-1234-5678` → `01012345678`. 휴대전화가 아니면 None."""
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits if _MOBILE.fullmatch(digits) else None


def mask_mobile(digits: str) -> str:
    return f"{digits[:3]}****{digits[-2:]}" if len(digits) >= 7 else "***"


def provider_configured() -> str | None:
    """설정이 완전하면 None, 아니면 SKIPPED 사유."""
    provider = settings.INQUIRY_SMS_PROVIDER.strip().lower()
    if not provider:
        return "INQUIRY_SMS_PROVIDER is not set"
    if provider != "nhn":
        return f"unsupported INQUIRY_SMS_PROVIDER '{provider}'"
    if not settings.NHN_SMS_APP_KEY.strip() or not settings.NHN_SMS_SECRET_KEY.strip():
        return "NHN_SMS_APP_KEY / NHN_SMS_SECRET_KEY are required"
    if normalize_korean_mobile(settings.INQUIRY_SMS_SENDER_NO) is None:
        return "INQUIRY_SMS_SENDER_NO is not a Korean mobile number"
    return None


def build_nhn_request(*, to: str, title: str = ACK_SMS_TITLE, body: str = ACK_SMS_BODY) -> tuple[str, dict, dict]:
    """(url, headers, json) — 테스트가 그대로 검사할 수 있게 순수 함수로 둔다."""
    base = settings.NHN_SMS_API_BASE.rstrip("/")
    app_key = settings.NHN_SMS_APP_KEY.strip()
    url = f"{base}/sms/v3.0/appKeys/{app_key}/sender/mms"
    headers = {
        "X-Secret-Key": settings.NHN_SMS_SECRET_KEY.strip(),
        "Content-Type": "application/json;charset=UTF-8",
    }
    payload = {
        "title": title,
        "body": body,
        "sendNo": normalize_korean_mobile(settings.INQUIRY_SMS_SENDER_NO),
        "recipientList": [{"recipientNo": to}],
        "userId": "reputation-inquiry",
    }
    return url, headers, payload


def parse_nhn_response(status_code: int, payload: object) -> AckSmsOutcome:
    """HTTP 200이어도 header.isSuccessful·수신자별 resultCode가 실패일 수 있다."""
    if status_code != 200:
        return AckSmsOutcome(SMS_FAILED, f"HTTP {status_code}")
    if not isinstance(payload, dict):
        return AckSmsOutcome(SMS_FAILED, "malformed response")
    header = payload.get("header") or {}
    if not header.get("isSuccessful", False):
        return AckSmsOutcome(
            SMS_FAILED, f"{header.get('resultCode')} {header.get('resultMessage')}".strip()
        )
    data = ((payload.get("body") or {}).get("data")) or {}
    request_id = data.get("requestId")
    for result in data.get("sendResultList") or []:
        if result.get("resultCode") not in (0, "0", None):
            return AckSmsOutcome(
                SMS_FAILED,
                f"recipient {result.get('resultCode')} {result.get('resultMessage')}".strip(),
                request_id,
            )
    return AckSmsOutcome(SMS_SENT, None, request_id)


async def deliver(to: str) -> AckSmsOutcome:
    """NHN Cloud에 1건 발송. 예외를 밖으로 내지 않는다."""
    url, headers, payload = build_nhn_request(to=to)
    try:
        async with httpx.AsyncClient(timeout=settings.INQUIRY_SMS_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        # 시크릿이 섞일 수 있는 URL·본문은 기록하지 않는다. 예외 종류만으로 원인 분류는 된다.
        logger.error("inquiry SMS to %s failed: %s", mask_mobile(to), exc.__class__.__name__)
        return AckSmsOutcome(SMS_FAILED, exc.__class__.__name__)
    try:
        body = response.json()
    except ValueError:
        body = None
    outcome = parse_nhn_response(response.status_code, body)
    if outcome.sent:
        logger.info("inquiry SMS sent to %s request_id=%s", mask_mobile(to), outcome.request_id)
    else:
        logger.error("inquiry SMS to %s rejected: %s", mask_mobile(to), outcome.detail)
    return outcome


async def recently_acknowledged(db: AsyncSession, lead: SalesLead, contact: str) -> bool:
    """같은 연락처의 다른 리드가 최근에 문자를 받았으면 True. 중복 제출·재시도 보호."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.INQUIRY_SMS_DEDUP_HOURS)
    count = await db.scalar(
        select(func.count())
        .select_from(SalesLead)
        .where(
            SalesLead.id != lead.id,
            SalesLead.contact == contact,
            SalesLead.ack_sms_status == SMS_SENT,
            SalesLead.ack_sms_sent_at >= since,
        )
    )
    return bool(count)


async def acknowledge_inquiry(db: AsyncSession, lead: SalesLead) -> AckSmsOutcome:
    """접수 안내 문자를 보내고 결과를 리드 행에 기록한다. commit은 호출자 몫이다.

    순서: 설정 → 수신번호 → 중복 억제 → 발송. 앞 단계에서 끊기면 DB를 읽지 않는다.
    """
    reason = provider_configured()
    if reason is not None:
        outcome = AckSmsOutcome(SMS_SKIPPED, reason)
    else:
        to = normalize_korean_mobile(lead.contact)
        if to is None:
            outcome = AckSmsOutcome(SMS_SKIPPED, "contact is not a Korean mobile number")
        elif await recently_acknowledged(db, lead, lead.contact):
            outcome = AckSmsOutcome(SMS_SKIPPED, "same contact acknowledged recently")
        else:
            outcome = await deliver(to)

    lead.ack_sms_status = outcome.status
    lead.ack_sms_error = None if outcome.sent else outcome.detail
    lead.ack_sms_sent_at = datetime.now(timezone.utc) if outcome.sent else None
    return outcome
