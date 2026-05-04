"""Slack 알림 — 모든 주요 이벤트 규격화"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


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
    return await _send(
        text=f"🔍 [V0 리포트] {hospital_name} AI 답변 노출 진단 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"🔍 *[V0 리포트]* *{hospital_name}* AI 답변 노출 진단 리포트 생성 완료\n"
                f"현재 ChatGPT+Gemini 답변 내 병원 언급률: *{sov_pct:.1f}%*\n"
                f"파일: `{pdf_path}`\n\n"
                f"원장 보고 전 내용 확인 후 전달해 주세요."
            )},
        }],
    )


async def notify_site_built(hospital_name: str, preview_url: str) -> bool:
    """AI 노출 웹블로그 준비 완료 → AE에게"""
    return await _send(
        text=f"🏗️ [AI 노출 웹블로그] {hospital_name} 준비 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"🏗️ *[AI 노출 웹블로그]* *{hospital_name}* 병원 정보 허브 준비 완료\n"
                f"미리보기: {preview_url}\n\n"
                f"Admin에서 도메인을 연결해 주세요."
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


async def notify_monthly_report_ready(
    hospital_name: str, year: int, month: int, sov_pct: float, change_pct: float | None, pdf_path: str
) -> bool:
    """월간 리포트 생성 완료 → AE에게"""
    change_text = f" | 전월 대비: *{change_pct:+.1f}%p*" if change_pct is not None else ""
    return await _send(
        text=f"📊 [월간 리포트] {hospital_name} {year}년 {month}월 AI 답변 언급 리포트 완료",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"📊 *[월간 리포트]* *{hospital_name}* {year}년 {month}월 AI 답변 언급 리포트 생성 완료\n"
                f"AI 답변 내 병원 언급률: *{sov_pct:.1f}%*{change_text}\n"
                f"파일: `{pdf_path}`\n\n"
                f"원장 보고 자료를 확인해 주세요."
            )},
        }],
    )


async def notify_monitoring_done(total: int, success: int) -> bool:
    return await _send(text=f"📊 주간 AI 답변 언급 모니터링 완료 ({success}/{total}개 병원)")
