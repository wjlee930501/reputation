"""Durable publication notification intent, projection, and delivery stamp."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypedDict, assert_never

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
    SlackMessage,
    validate_message,
)
from app.services.notification_store import enqueue_notification

PUBLISH_NOTIFICATION_TYPE = "CONTENT_PUBLISHED"
_DEDUPE_PREFIX = f"{PUBLISH_NOTIFICATION_TYPE}:"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PublishedItem(Protocol):
    id: uuid.UUID
    hospital_id: uuid.UUID
    title: str | None
    published_at: datetime | None


class HospitalIdentity(Protocol):
    id: uuid.UUID
    name: str


class PublishNotificationProjection(TypedDict):
    state: str
    label: str
    problem: str | None
    publication_impact: str
    next_action: str
    notification_id: str | None
    safe_error_code: str | None


@dataclass(frozen=True, slots=True)
class PublishNotificationIdentity:
    content_id: uuid.UUID
    published_at: datetime


PublishNotificationState = Literal[
    "PENDING", "SENDING", "RETRYING", "HOLD", "SENT", "FAILED"
]


def build_publish_notification_intent(
    item: PublishedItem, hospital: HospitalIdentity
) -> NotificationIntent:
    """Build one publication-cycle intent with one safe Admin action."""

    if item.published_at is None:
        raise NotificationPayloadError("PUBLISHED_AT_REQUIRED")
    admin_url = (
        f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{hospital.id}/content"
        f"?content={item.id}"
    )
    title = (item.title or "제목 없는 콘텐츠")[:180]
    message = SlackMessage(
        fallback_text=f"[콘텐츠 공개 완료] {hospital.name} · {title}",
        blocks=(
            {
                "type": "header",
                "block_id": "publish_header",
                "text": {"type": "plain_text", "text": "콘텐츠 공개 완료"},
            },
            {
                "type": "section",
                "block_id": "publish_identity",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{hospital.name}*\n{title}",
                },
            },
            {
                "type": "section",
                "block_id": "publish_next_action",
                "text": {
                    "type": "mrkdwn",
                    "text": "콘텐츠는 이미 공개되었습니다. 공개된 글에 문제가 없는지 확인해 주세요.",
                },
            },
            {
                "type": "actions",
                "block_id": "publish_action",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Admin에서 확인"},
                        "url": admin_url,
                    }
                ],
            },
        ),
        admin_url=admin_url,
    )
    validate_message(message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    return NotificationIntent(
        dedupe_key=_publish_dedupe_key(item.id, item.published_at),
        notification_type=PUBLISH_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital.id,
        max_attempts=1,
    )


async def enqueue_publish_notification(
    db: AsyncSession, item: PublishedItem, hospital: HospitalIdentity
) -> NotificationOutbox:
    return await enqueue_notification(db, build_publish_notification_intent(item, hospital))


def enqueue_publish_notification_sync(
    db: Session, item: PublishedItem, hospital: HospitalIdentity
) -> NotificationOutbox:
    """Add the intent to the same sync transaction as automatic publication."""

    intent = build_publish_notification_intent(item, hospital)
    now = datetime.now(UTC)
    row = NotificationOutbox(
        hospital_id=intent.hospital_id,
        dedupe_key=intent.dedupe_key,
        notification_type=intent.notification_type,
        channel=intent.channel,
        state=NotificationOutboxState.PENDING.value,
        payload=intent.message.payload(),
        fallback_text=intent.message.fallback_text,
        max_attempts=intent.max_attempts,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row


def parse_publish_notification_identity(key: str) -> PublishNotificationIdentity | None:
    if not key.startswith(_DEDUPE_PREFIX):
        return None
    parts = key.split(":")
    if len(parts) != 3:
        return None
    try:
        return PublishNotificationIdentity(
            content_id=uuid.UUID(parts[1]),
            published_at=_EPOCH + timedelta(microseconds=int(parts[2])),
        )
    except (ValueError, OverflowError):
        return None


def project_publish_notification(
    state: PublishNotificationState | None,
    *,
    notification_id: uuid.UUID | None,
    safe_error_code: str | None,
) -> PublishNotificationProjection:
    identity = str(notification_id) if notification_id else None
    common = {
        "publication_impact": "콘텐츠 발행에는 영향이 없습니다.",
        "notification_id": identity,
        "safe_error_code": safe_error_code,
    }
    match state:
        case NotificationOutboxState.SENT.value:
            return {"state": "SENT", "label": "Slack 전달 완료", "problem": None,
                    "next_action": "공개된 글에 문제가 없는지 확인해 주세요.", **common}
        case NotificationOutboxState.FAILED.value:
            return {"state": "FAILED", "label": "Slack 전달 실패",
                    "problem": "Slack 운영 알림 전송에 실패했습니다.",
                    "next_action": "운영센터에서 실패 원인을 확인하고 알림을 다시 시도해 주세요.", **common}
        case NotificationOutboxState.HOLD.value:
            return {"state": "HOLD", "label": "전송 결과 확인 필요",
                    "problem": "Slack 수신 여부를 자동으로 확정하지 못했습니다.",
                    "next_action": "Slack 수신 내역을 확인한 뒤 운영센터에서 다음 조치를 선택해 주세요.", **common}
        case NotificationOutboxState.RETRYING.value:
            return {"state": "RETRYING", "label": "Slack 재시도 예정", "problem": None,
                    "next_action": "자동 재시도를 기다려 주세요.", **common}
        case NotificationOutboxState.PENDING.value | NotificationOutboxState.SENDING.value:
            return {"state": state, "label": "Slack 전달 대기", "problem": None,
                    "next_action": "잠시 후 자동으로 전달됩니다.", **common}
        case None:
            return {"state": "MISSING", "label": "Slack 알림 준비 중",
                    "problem": "발행 알림 작업이 아직 준비되지 않았습니다.",
                    "next_action": "다음 아침 복구 작업이 자동으로 준비합니다.", **common}
        case unreachable:
            assert_never(unreachable)


def _publish_dedupe_key(content_id: uuid.UUID, published_at: datetime) -> str:
    normalized = published_at.astimezone(UTC)
    elapsed = normalized - _EPOCH
    micros = ((elapsed.days * 86_400) + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    return f"{_DEDUPE_PREFIX}{content_id}:{micros}"
