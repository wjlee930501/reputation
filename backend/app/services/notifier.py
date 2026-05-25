"""Slack 알림 — 모든 주요 이벤트 규격화"""
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


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


async def _send(text: str, blocks: list | None = None) -> bool:
    if not settings.SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook not configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                settings.SLACK_WEBHOOK_URL,
                json={"text": text, **({"blocks": blocks} if blocks else {})},
            )
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Slack failed: {e}")
        return False


async def notify_v0_report_ready(hospital_name: str, sov_pct: float, pdf_path: str) -> bool:
    """V0 리포트 생성 완료 → AE에게"""
    measurement_label = (
        "OpenAI Responses API + 웹검색 / Gemini 그라운디드"
        if settings.OPENAI_CHATGPT_USE_WEB_SEARCH
        else "OpenAI gpt-4o 응답(웹검색 미적용) / Gemini 그라운디드"
    )
    return await _send(
        text=f"🔍 [V0 리포트] {hospital_name} AI 답변 인용 진단 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"🔍 *[V0 리포트]* *{hospital_name}* AI 답변 인용 진단 리포트 생성 완료\n"
                f"AI 답변 내 병원 언급률: *{sov_pct:.1f}%*\n"
                f"측정 방식: {measurement_label}\n"
                f"파일: `{pdf_path}`\n\n"
                f"원장 보고 전 내용 확인 후 전달해 주세요."
            )},
        }],
    )


async def notify_site_built(hospital_name: str, preview_url: str) -> bool:
    """콘텐츠 허브 노출 준비 완료 → AE에게 (legacy function name)"""
    return await _send(
        text=f"🏗️ [AI 노출 콘텐츠 허브] {hospital_name} 노출 준비 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"🏗️ *[AI 노출 콘텐츠 허브]* *{hospital_name}* 병원 정보와 콘텐츠 허브 노출 준비 완료\n"
                f"미리보기: {preview_url}\n\n"
                f"Admin에서 공개 정보와 도메인 상태를 확인해 주세요."
            )},
        }],
    )


async def notify_content_draft_ready(
    hospital_name: str,
    sequence_no: int,
    total_count: int,
    content_type: str,
    scheduled_date: str,
    admin_url: str,
) -> bool:
    """콘텐츠 초안 완료 → 당일 아침 08:00 AE에게"""
    type_labels = {
        "FAQ": "FAQ", "DISEASE": "질환 가이드", "TREATMENT": "시술·치료 안내",
        "COLUMN": "원장 칼럼", "HEALTH": "건강 정보", "LOCAL": "지역 특화", "NOTICE": "병원 공지",
    }
    type_label = type_labels.get(content_type, content_type)
    return await _send(
        text=f"📝 [콘텐츠] {hospital_name} {total_count}편 중 {sequence_no}번째 초안 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"📝 *[콘텐츠]* *{hospital_name}* {total_count}편 중 {sequence_no}번째 콘텐츠 초안 저장 완료\n"
                f"유형: {type_label} | 발행 예정일: {scheduled_date}\n\n"
                f"<{admin_url}|Admin에서 검토 후 발행해 주세요.>"
            )},
        }],
    )


async def notify_content_published(hospital_name: str, title: str) -> bool:
    """콘텐츠 발행 완료"""
    return await _send(text=f"✅ [{hospital_name}] 발행 완료: {title}")


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
    """
    masked = mask_contact(contact)
    link_line = f"<{admin_url}|Admin에서 상세 확인>" if admin_url else "Admin에서 상세 확인"
    return await _send(
        text=f"📩 [무료 진단 요청] {clinic_name}",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"📩 *[무료 진단 요청]* *{clinic_name}*\n"
                f"진료과/지역: {clinic_type}\n"
                f"연락처: `{masked}`\n\n"
                f"{link_line} 후 진단 범위를 확정해 주세요."
            )},
        }],
    )


async def notify_monthly_report_ready(
    hospital_name: str, year: int, month: int, sov_pct: float, change_pct: float | None, pdf_path: str
) -> bool:
    """월간 리포트 생성 완료 → AE에게"""
    change_text = f" | 전월 대비: *{change_pct:+.1f}%p*" if change_pct is not None else ""
    measurement_label = (
        "OpenAI Responses + 웹검색 / Gemini 그라운디드"
        if settings.OPENAI_CHATGPT_USE_WEB_SEARCH
        else "OpenAI gpt-4o 응답(웹검색 미적용) / Gemini 그라운디드"
    )
    return await _send(
        text=f"📊 [월간 리포트] {hospital_name} {year}년 {month}월 AI 답변 인용 리포트 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"📊 *[월간 리포트]* *{hospital_name}* {year}년 {month}월 AI 답변 인용 리포트 생성 완료\n"
                f"AI 답변 내 병원 언급률: *{sov_pct:.1f}%*{change_text}\n"
                f"측정 방식: {measurement_label} · 측정 실패는 분모에서 제외\n"
                f"파일: `{pdf_path}`\n\n"
                f"원장 보고 자료를 확인해 주세요."
            )},
        }],
    )


async def notify_monitoring_done(total: int, success: int) -> bool:
    return await _send(text=f"📊 주간 AI 답변 언급 모니터링 완료 ({success}/{total}개 병원)")


async def notify_lead_purge_result(*, purged: int, skipped: int = 0, error: str | None = None) -> bool:
    """매일 04:00 KST 보관기간 만료 lead 자동 파기 결과.

    개인정보보호법 제21조 자동 파기 의무 이행 trail. 0건이라도 매일 송출하여
    "purge cron이 살아 있음"을 운영자가 매일 확인할 수 있게 한다.
    """
    if error:
        return await _send(
            text=f"🟥 [PII 자동 파기 실패] {error[:200]}",
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": (
                    f"🟥 *[PII 자동 파기 실패]*\n"
                    f"오류: `{error[:300]}`\n\n"
                    f"개인정보보호법 제21조 의무 이행 차질. 즉시 확인 필요."
                )},
        }],
    )


async def notify_content_generation_failed(
    hospital_name: str, content_type: str, scheduled_date: str, error: str
) -> bool:
    """콘텐츠 생성 실패 → AE에게. 수동 재생성을 유도."""
    error_snippet = error[:200]
    return await _send(
        text=f"⚠️ [콘텐츠 생성 실패] {hospital_name} {content_type} 생성 실패",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"⚠️ *[콘텐츠 생성 실패]*\n"
                f"병원: *{hospital_name}*\n"
                f"유형: {content_type} | 발행 예정일: {scheduled_date}\n\n"
                f"오류: `{error_snippet}`\n\n"
                f"Admin 콘텐츠 화면에서 수동 재생성해 주세요."
            )},
        }],
    )
    return await _send(
        text=f"🧹 [PII 자동 파기] 만료 리드 {purged}건 익명화 완료" + (f" (스킵 {skipped})" if skipped else ""),
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"🧹 *[PII 자동 파기]* 만료 lead {purged}건 익명화 완료"
                + (f" · 이미 처리된 {skipped}건 스킵" if skipped else "")
                + "\n개인정보보호법 제21조 자동 파기 cron 정상 동작 중."
            )},
        }],
    )
