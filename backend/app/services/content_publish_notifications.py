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
)
from app.services.notification_milestone_rendering import (
    RenderedSlackMessage,
    action_block,
    admin_url,
    header_block,
    safe_text,
    section_block,
    validated_message,
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
    action_url = admin_url(
        settings.ADMIN_BASE_URL,
        f"/hospitals/{hospital.id}/content?content={item.id}",
    )
    hospital_name = _publish_safe_text(hospital.name, 100)
    title = _publish_safe_text(item.title or "제목 없는 콘텐츠", 180)
    details = (
        "무슨 문제인지: 콘텐츠가 공개되어 운영 확인이 필요합니다.\n"
        "고객 영향: 확인 전까지 잘못된 정보가 공개 화면에 남아 있을 수 있습니다.\n"
        "지금 할 일: Admin에서 공개된 글의 내용과 이미지를 확인해 주세요.\n"
        "처리 기한: 오늘 중"
    )
    message = validated_message(
        RenderedSlackMessage(
            "무슨 문제인지: 콘텐츠 공개 확인 필요 · "
            "고객 영향: 공개 정보 확인 전 · 지금 할 일: Admin 검토 · 처리 기한: 오늘 중",
            (
                header_block("publish_header", "콘텐츠 공개 확인"),
                section_block("publish_identity", f"*{hospital_name}*\n{title}"),
                section_block("publish_context", details),
                action_block("publish_action", action_url, "Admin에서 공개 내용 확인"),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=_publish_dedupe_key(item.id, item.published_at),
        notification_type=PUBLISH_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital.id,
        max_attempts=1,
    )


def _publish_safe_text(value: str, limit: int) -> str:
    return (
        safe_text(value, limit)
        .replace("[storage path redacted]", "[경로 숨김]")
        .replace("[email redacted]", "[이메일 숨김]")
        .replace("[phone redacted]", "[연락처 숨김]")
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
