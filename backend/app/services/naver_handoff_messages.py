"""Grouped weekly Naver handoff Slack message."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.config import settings
from app.models.operations import JSONValue
from app.services.notification_outbox import NotificationIntent, SlackMessage


@dataclass(frozen=True, slots=True)
class NaverWeeklyEntry:
    hospital_name: str
    created: int
    requested: int
    failed: int


def build_naver_weekly_digest(
    entries: tuple[NaverWeeklyEntry, ...], observed_on: date
) -> NotificationIntent:
    hospitals = len(entries)
    created = sum(entry.created for entry in entries)
    requested = sum(entry.requested for entry in entries)
    failed = sum(entry.failed for entry in entries)
    admin_url = f"{settings.ADMIN_BASE_URL.rstrip('/')}/operations"
    status = "확인할 글이 있습니다" if failed else "정상 완료"
    action = (
        "운영 센터에서 실패한 글을 열고 ‘실패한 글 다시 수집’을 눌러 주세요. "
        "다시 실패하면 작업 번호와 글 식별값을 개발팀에 전달해 주세요."
        if failed
        else "새로 추가된 근거 자료의 내용을 병원 자료 화면에서 검토해 주세요."
    )
    blocks: tuple[dict[str, JSONValue], ...] = (
        {
            "type": "header",
            "block_id": "naver_weekly_header",
            "text": {"type": "plain_text", "text": f"네이버 자료 수집 · {status}"},
        },
        {
            "type": "section",
            "block_id": "naver_weekly_summary",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"대상 병원: *{hospitals}개*\n확인한 글: *{requested}개* · "
                    f"새 자료: *{created}개* · 수집 실패: *{failed}개*"
                ),
            },
        },
        {
            "type": "section",
            "block_id": "naver_weekly_action",
            "text": {"type": "mrkdwn", "text": f"다음 행동: {action}"},
        },
        {
            "type": "actions",
            "block_id": "naver_weekly_button",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "운영 센터에서 확인"},
                    "url": admin_url,
                }
            ],
        },
    )
    message = SlackMessage(
        fallback_text=f"[네이버 자료 수집] 새 자료 {created}개 · 실패 {failed}개",
        blocks=blocks,
        admin_url=admin_url,
    )
    iso_year, iso_week, _weekday = observed_on.isocalendar()
    return NotificationIntent(
        dedupe_key=f"NAVER_WEEKLY_HANDOFF:{iso_year}-W{iso_week:02d}",
        notification_type="NAVER_WEEKLY_HANDOFF",
        message=message,
    )
