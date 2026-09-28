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
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services.notification_labels import prefixed_for_event
from app.services.notification_milestone_rendering import safe_text as _slack_safe_text

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


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


# 도입문의 채널(#noti-도입문의-뉴비짓)은 뉴비짓의 여러 제품 문의가 함께 들어온다.
# 제목 줄과 fallback text 모두에 출처를 표시해 Re:putation 문의임을 바로 알 수 있게 한다.
INQUIRY_SOURCE_TAG = "[Re:putation]"


def _inquiry_webhook_url() -> str:
    """도입문의 전용 채널 웹훅. 비어 있으면 기존 운영 채널(SLACK_WEBHOOK_URL)로 보낸다."""
    return settings.SLACK_WEBHOOK_URL_INQUIRY.strip() or settings.SLACK_WEBHOOK_URL


async def _send(text: str, blocks: list | None = None, *, webhook_url: str | None = None) -> bool:
    url = webhook_url or settings.SLACK_WEBHOOK_URL
    if not url:
        logger.warning("Slack webhook not configured")
        return False
    if not _is_allowed_webhook(url):
        logger.error("Slack webhook URL rejected: host not in allowlist (SSRF guard)")
        return False
    attempts = 3
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    url,
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
    contact: str,
    admin_url: str | None = None,
    diagnosis_note: str | None = None,
    specialty: str | None = None,
    region_keyword: str | None = None,
    source_path: str | None = None,
    created_at: datetime | None = None,
) -> bool:
    """공개 도입문의 접수 → 도입문의 채널(SLACK_WEBHOOK_URL_INQUIRY, 미설정 시 운영 채널).

    신청 정보를 요약해 보내되 처리방침의 Slack 국외 이전 고지 범위(병원명·진료과/지역·
    마스킹된 연락처·운영 메타데이터)를 넘지 않는다. 고지에 없는 핵심 키워드·문의 본문(주소·
    원장 성함·홈페이지가 담긴다)·담당자 성함은 보내지 않는다 — 상세 확인은 Admin UI deep-link에서.

    자유 텍스트는 입력 검증(leads API)을 통과한 뒤에도 Slack(국외 이전)으로 그대로 나가면
    안 된다 — 검증 패턴이 놓친 식별정보가 남을 수 있고, 긴 본문으로 채널 스팸도 가능하다.
    여기서 한 번 더 마스킹·절단한다.
    """
    masked = mask_contact(contact)
    safe_clinic_name = _safe_operator_label(_safe_label(clinic_name), limit=100)
    action_path = _validated_admin_path(admin_url or settings.ADMIN_BASE_URL.rstrip("/") + "/leads")
    summary_lines = [
        "문의 유형: 일반 문의",
        f"진료과: {_safe_summary_value(specialty)}",
        f"지역: {_safe_summary_value(region_keyword)}",
        f"연락처: `{masked}`",
        f"유입 경로: {_safe_source_path(source_path)}",
    ]
    if created_at is not None:
        summary_lines.append(f"접수 시각: {created_at.astimezone(_KST):%Y-%m-%d %H:%M} KST")
    if diagnosis_note:
        summary_lines.append(f"자동 처리: {_safe_operator_label(diagnosis_note, limit=100)}")
    body = (
        f"*{INQUIRY_SOURCE_TAG} [새 문의] {safe_clinic_name}*\n"
        + "\n".join(summary_lines)
        + "\n담당자를 지정하고 상담 연락을 진행해 주세요."
    )
    return await _send(
        text=prefixed_for_event(
            "LEAD_CREATED",
            f"{INQUIRY_SOURCE_TAG} [새 문의] {safe_clinic_name} | 담당자를 정해 신청자에게 연락해 주세요.",
        ),
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": prefixed_for_event("LEAD_CREATED", body)}},
            _admin_action_block(path=action_path, label="도입 문의 확인"),
        ],
        webhook_url=_inquiry_webhook_url(),
    )


def _safe_summary_value(value: str | None) -> str:
    return _safe_operator_label(_safe_label(value), limit=100)


_SOURCE_PATH = re.compile(r"[/#A-Za-z0-9_\-]{1,60}")


def _safe_source_path(value: str | None) -> str:
    """유입 경로는 경로만 보낸다. 쿼리(광고 파라미터)는 URL 인코딩된 식별정보를 담을 수 있다."""
    path = (value or "").split("?", 1)[0].split("&", 1)[0].strip()
    if not path:
        return "(미입력)"
    return path if _SOURCE_PATH.fullmatch(path) else "(기타)"


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
        f"*[새 신청] 무료 AI 노출 진단 · {safe_clinic_name}*\n"
        f"오늘 {slot_no}번째 신청입니다.\n"
        "담당자를 정한 뒤 신청 내역의 연락처로 상담 일정을 안내해 주세요."
    )
    return await _send(
        text=prefixed_for_event("LEAD_DIAGNOSIS_RECEIVED", f"[새 신청] {safe_clinic_name} · 무료 진단 | 담당자를 정해 신청자에게 연락해 주세요."),
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": prefixed_for_event("LEAD_DIAGNOSIS_RECEIVED", body)}},
            _admin_action_block(
                path=_validated_admin_path(admin_url),
                label="무료 진단 신청 확인",
            ),
        ],
    )


async def notify_lead_purge_result(*, purged: int, skipped: int = 0, error: str | None = None) -> bool:
    """매일 04:00 KST 보관기간 만료 lead 자동 파기 결과.

    정상 파기 기록은 로그에 남기고 실패만 알린다. 실행 여부는 정기 감시로 확인한다.
    """
    if error:
        return await _send(
            text=prefixed_for_event("PRIVACY_RETENTION_FAILED", "🟥 [개인정보 자동 파기] 운영 확인 필요"),
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": (
                        prefixed_for_event("PRIVACY_RETENTION_FAILED", "🟥 *[개인정보 자동 파기]* 운영 확인 필요\n"
                        "무슨 문제인지: 보관기간이 지난 신청 정보의 파기 결과를 확정하지 못했습니다.\n"
                        "고객 영향: 일부 개인정보가 예정된 시간에 정리되지 않았을 수 있습니다.\n"
                        "지금 할 일: 운영센터에서 개인정보 보관 항목의 안전 정보를 복사한 뒤 "
                        "개발팀에 문의해 주세요.\n"
                        "개발팀 전달용 참조: `PRIVACY-RETENTION`")
                    )},
                },
                _admin_action_block(path="/operations?queue=INCIDENTS", label="운영센터에서 확인"),
            ],
        )
    logger.info("PII retention sweep completed: purged=%s skipped=%s", purged, skipped)
    return False  # Successful housekeeping is not an operator task.


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
