# allow: SIZE_OK -- Slack message registry keeps every event behind one allowlisted delivery seam.
"""Slack 알림 — 모든 주요 이벤트 규격화

실발송 경로는 대부분 `onboarding_notifications.py`·notification outbox로 이전됐다.
여기 남은 함수는 그 경로가 쓰는 전송 프리미티브(`_send`, `_is_allowed_webhook`)와
직접 호출되는 실시간 리드 알림(`notify_lead_created`, `notify_lead_diagnosis_received`,
`notify_lead_purge_result`)뿐이다.
"""
import asyncio
import logging
import re
from typing import TypedDict
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services.notification_milestone_rendering import safe_text as _slack_safe_text

logger = logging.getLogger(__name__)


def _is_allowed_webhook(url: str) -> bool:
    """SSRF/exfil 방어 — webhook은 https + 허용 호스트만(V-013).

    SLACK_WEBHOOK_URL이 잘못 설정되거나 변조되어 내부 메타데이터 주소
    (169.254.169.254 등)나 임의 호스트로 PII가 빠져나가는 것을 차단한다.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname:
        return False
    allowed = {h.strip().lower() for h in settings.SLACK_WEBHOOK_ALLOWED_HOSTS.split(",") if h.strip()}
    return parts.hostname.lower() in allowed


def mask_contact(contact: str) -> str:
    """Mask phone/email PII before sending to Slack.

    개인정보보호법 + 국외이전(Slack=US) 측면에서 평문 PII 송출 금지.
    상세는 Admin UI(권한 있는 운영자만)에서 확인.
    """
    if not contact:
        return "***"
    text = contact.strip()
    if "@" in text:
        local, _, domain = text.partition("@")
        if not domain:
            return "***"
        head = local[:2] if len(local) >= 2 else local
        return f"{head}***@{domain}"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 7:
        return f"{digits[:3]}-****-{digits[-4:]}"
    return "***"


_SAFE_LABEL_MAX_CHARS = 60


def _safe_label(value: str | None) -> str:
    """사용자 입력 자유 텍스트를 Slack 라벨로 쓰기 전에 마스킹·절단한다.

    개행은 Slack 블록 구조를 깨뜨려 다른 필드로 위장할 수 있으므로 공백으로 접는다.
    """
    text = mask_contact_free((value or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "(미입력)"
    return text if len(text) <= _SAFE_LABEL_MAX_CHARS else f"{text[:_SAFE_LABEL_MAX_CHARS]}…"


def _safe_operator_label(value: str | None, *, limit: int = 100) -> str:
    """Render user-controlled Slack copy without contact or filesystem details."""

    return (
        _slack_safe_text(value or "", limit)
        .replace("[storage path redacted]", "[경로 숨김]")
        .replace("[email redacted]", "[이메일 숨김]")
        .replace("[phone redacted]", "[연락처 숨김]")
    )


def _validated_admin_path(candidate_url: str) -> str:
    """Convert a same-origin Admin URL to an allowlisted action path."""

    fallback = "/operations?queue=INCIDENTS"
    try:
        candidate = urlsplit(candidate_url)
        base = urlsplit(settings.ADMIN_BASE_URL)
        candidate_origin = (candidate.scheme, candidate.hostname, candidate.port)
        base_origin = (base.scheme, base.hostname, base.port)
    except ValueError:
        return fallback
    allowed_root = any(
        candidate.path == root or candidate.path.startswith(f"{root}/")
        for root in ("/operations", "/hospitals", "/leads")
    )
    if (
        candidate_origin != base_origin
        or candidate.username is not None
        or candidate.password is not None
        or not allowed_root
        or "\\" in candidate.path
        or any(segment == ".." for segment in candidate.path.split("/"))
    ):
        return fallback
    return candidate.path


async def _send(text: str, blocks: list | None = None) -> bool:
    if not settings.SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook not configured")
        return False
    if not _is_allowed_webhook(settings.SLACK_WEBHOOK_URL):
        logger.error("Slack webhook URL rejected: host not in allowlist (SSRF guard)")
        return False
    attempts = 3
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    settings.SLACK_WEBHOOK_URL,
                    json={"text": text, **({"blocks": blocks} if blocks else {})},
                )
                r.raise_for_status()
                return True
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            if retryable and attempt < attempts - 1:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            # 응답 본문/웹훅 URL은 시크릿이 섞일 수 있어 기록하지 않는다. 상태 코드는
            # revoked webhook(404/410), rate limit(429), Slack 장애(5xx)를 구분하는 데 필요하다.
            logger.error("Slack delivery failed: HTTPStatusError status=%s", status_code)
            return False
        except Exception as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
            if retryable and attempt < attempts - 1:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            logger.error("Slack delivery failed: %s", exc.__class__.__name__)
            return False
    return False


class _LegacyButtonText(TypedDict):
    type: str
    text: str


class _LegacyButton(TypedDict):
    type: str
    text: _LegacyButtonText
    url: str


class _LegacyActionBlock(TypedDict):
    type: str
    elements: list[_LegacyButton]


def _admin_action_block(*, path: str, label: str) -> _LegacyActionBlock:
    """Build the one allowlisted Admin action used by legacy Slack messages."""
    return {
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": label},
            "url": f"{settings.ADMIN_BASE_URL.rstrip('/')}{path}",
        }],
    }


async def notify_lead_created(
    *,
    clinic_name: str,
    clinic_type: str,
    contact: str,
    admin_url: str | None = None,
) -> bool:
    """무료 진단 요청 접수 → AE에게.

    PII 보호: 연락처는 마스킹, 환자 질문 본문은 Slack 채널로 송출하지 않음.
    상세 확인은 Admin UI deep-link에서.

    clinic_name/clinic_type도 공개 폼의 자유 텍스트라 입력 검증(leads API)을 통과한 뒤에도
    Slack(국외 이전)으로 그대로 나가면 안 된다 — 검증 패턴이 놓친 식별정보가 남을 수 있고,
    긴 본문을 병원명 칸에 밀어넣는 채널 스팸도 가능하다. 여기서 한 번 더 마스킹·절단한다.
    """
    masked = mask_contact(contact)
    safe_clinic_name = _safe_label(clinic_name)
    safe_clinic_type = _safe_label(clinic_type)
    link_line = f"<{admin_url}|Admin에서 상세 확인>" if admin_url else "Admin에서 상세 확인"
    return await _send(
        text=f"📩 [무료 진단 요청] {safe_clinic_name}",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"📩 *[무료 진단 요청]* *{safe_clinic_name}*\n"
                f"진료과/지역: {safe_clinic_type}\n"
                f"연락처: `{masked}`\n\n"
                f"{link_line} 후 진단 범위를 확정해 주세요."
            )},
        }],
    )


async def notify_lead_diagnosis_received(
    *,
    clinic_name: str,
    clinic_type: str,
    region: str,
    keywords: list[str],
    contact: str,
    email: str,
    slot_no: int,
    admin_url: str,
) -> bool:
    """무료 AI 노출 진단 접수를 한 건만 즉시 알린다."""
    del clinic_type, region, keywords, contact, email
    safe_clinic_name = _safe_operator_label(clinic_name)
    body = (
        f"📩 *[무료 AI 노출 진단 접수]* *{safe_clinic_name}* · 오늘 {slot_no}번째 접수\n"
        "무슨 문제인지: 새로운 무료 진단 신청이 접수됐습니다.\n"
        "고객 영향: 접수 확인이 늦어지면 상담 연락과 진단 일정이 지연될 수 있습니다.\n"
        "지금 할 일: Admin에서 신청 정보를 확인하고 담당자를 지정해 주세요.\n"
        "처리 기한: 접수 당일"
    )
    return await _send(
        text=(
            f"무슨 문제인지: {safe_clinic_name} 진단 신청 접수 · "
            "고객 영향: 상담 연락 대기 · 지금 할 일: Admin 확인 · "
            "처리 기한: 접수 당일"
        ),
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            _admin_action_block(
                path=_validated_admin_path(admin_url),
                label="무료 진단 신청 확인",
            ),
        ],
    )


async def notify_lead_purge_result(*, purged: int, skipped: int = 0, error: str | None = None) -> bool:
    """매일 04:00 KST 보관기간 만료 lead 자동 파기 결과.

    개인정보보호법 제21조 자동 파기 의무 이행 trail. 0건이라도 매일 송출하여
    "purge cron이 살아 있음"을 운영자가 매일 확인할 수 있게 한다.
    """
    if error:
        return await _send(
            text="🟥 [개인정보 자동 파기] 운영 확인 필요",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": (
                        "🟥 *[개인정보 자동 파기]* 운영 확인 필요\n"
                        "무슨 문제인지: 보관기간이 지난 신청 정보의 파기 결과를 확정하지 못했습니다.\n"
                        "고객 영향: 일부 개인정보가 예정된 시간에 정리되지 않았을 수 있습니다.\n"
                        "지금 할 일: 운영센터에서 개인정보 보관 항목의 안전 정보를 복사한 뒤 "
                        "개발팀에 문의해 주세요.\n"
                        "개발팀 전달용 참조: `PRIVACY-RETENTION`"
                    )},
                },
                _admin_action_block(path="/operations?queue=INCIDENTS", label="운영센터에서 확인"),
            ],
        )
    if purged == 0 and skipped == 0:
        logger.info("PII retention sweep completed with no expired leads")
        return False
    return await _send(
        text=f"🧹 [개인정보 자동 파기] 만료 신청 정보 {purged}건 정리 완료"
        + (f" (재처리 제외 {skipped}건)" if skipped else ""),
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": (
                    "🧹 *[개인정보 자동 파기]* 처리 완료\n"
                    f"무슨 문제인지: 보관기간이 지난 신청 정보 {purged}건을 안전하게 정리했습니다."
                    + (f" 이미 처리된 {skipped}건은 다시 변경하지 않았습니다." if skipped else "")
                    + "\n고객 영향: 보관기간이 지난 개인정보가 운영 화면에 남지 않도록 정리되었습니다.\n"
                    "지금 할 일: 추가 조치는 없습니다. 필요하면 운영센터에서 처리 상태를 확인해 주세요.\n"
                    "개발팀 전달용 참조: `PRIVACY-RETENTION`"
                )},
            },
            _admin_action_block(path="/operations?queue=INCIDENTS", label="운영센터에서 확인"),
        ],
    )


def mask_contact_free(text: str) -> str:
    """자유 텍스트 안의 주민등록번호/이메일/전화 PII를 마스킹(로그·알림 송출 안전).

    주민등록번호를 이메일·전화보다 먼저 치환한다 — 전화 패턴이 주민번호 뒷자리를 먼저
    삼키면 앞 6자리(생년월일)가 평문으로 남는다.
    """
    if not text:
        return ""
    text = re.sub(r"\b\d{6}[-\s]?[1-4]\d{6}\b", "[id]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    text = re.sub(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}", "[phone]", text)
    return text
