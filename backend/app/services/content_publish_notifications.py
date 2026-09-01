"""Durable publication notification intent, projection, and delivery stamp."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol, TypedDict, assert_never

from sqlalchemy import select
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
PUBLISH_DIGEST_NOTIFICATION_TYPE = "CONTENT_PUBLISH_DIGEST"
MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE = (
    "MISSING_APPROVED_ESSENCE_DIGEST"
)
GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE = "GENERATION_BLOCKED_DIGEST"
POST_PUBLISH_REVIEW_OVERDUE_TYPE = "POST_PUBLISH_REVIEW_OVERDUE"
_DEDUPE_PREFIX = f"{PUBLISH_NOTIFICATION_TYPE}:"
_DIGEST_DEDUPE_PREFIX = f"{PUBLISH_DIGEST_NOTIFICATION_TYPE}:"
_MISSING_ESSENCE_DIGEST_DEDUPE_PREFIX = (
    f"{MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE}:"
)
_GENERATION_BLOCKED_DIGEST_DEDUPE_PREFIX = (
    f"{GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE}:"
)
_REVIEW_OVERDUE_DEDUPE_PREFIX = f"{POST_PUBLISH_REVIEW_OVERDUE_TYPE}:"
# Slack Block Kit truncates long sections; keep the digest inside one readable block.
_DIGEST_MAX_HOSPITALS = 12
_DIGEST_MAX_ITEMS_PER_HOSPITAL = 5
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
        max_attempts=3,
    )


def build_content_publish_digest_intent(
    cycle_date: date,
    published_outcomes: Sequence[Mapping[str, object]],
) -> NotificationIntent:
    """Build one neutral summary for an Asia/Seoul morning publication cycle."""

    if not published_outcomes:
        raise NotificationPayloadError("PUBLISH_DIGEST_ITEMS_REQUIRED")
    hospital_ids = {
        str(outcome["hospital_id"])
        for outcome in published_outcomes
        if outcome.get("hospital_id") is not None
    }
    if not hospital_ids:
        raise NotificationPayloadError("PUBLISH_DIGEST_HOSPITALS_REQUIRED")
    hospital_count = len(hospital_ids)
    item_count = len(published_outcomes)
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=TODAY")
    summary = f"병원 {hospital_count}곳 · 글 {item_count}건"
    message = validated_message(
        RenderedSlackMessage(
            f"오늘 발행 요약 · {summary} · Admin에서 사실 확인해주세요",
            (
                header_block("publish_digest_header", "오늘 발행 요약"),
                section_block(
                    "publish_digest_summary",
                    f"*{summary}*\n오늘 자동 공개된 글을 요약했습니다. "
                    "Admin에서 공개 내용을 사실 확인해주세요.",
                ),
                action_block("publish_digest_action", action_url, "오늘 공개 내용 확인"),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=f"{_DIGEST_DEDUPE_PREFIX}{cycle_date.isoformat()}",
        notification_type=PUBLISH_DIGEST_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


def build_missing_approved_essence_digest_intent(
    cycle_date: date,
    skipped_outcomes: Sequence[Mapping[str, object]],
) -> NotificationIntent:
    """Build one neutral summary for a Seoul nightly onboarding skip cycle."""

    if not skipped_outcomes:
        raise NotificationPayloadError("MISSING_ESSENCE_DIGEST_ITEMS_REQUIRED")
    hospital_ids = {
        str(outcome["hospital_id"])
        for outcome in skipped_outcomes
        if outcome.get("hospital_id") is not None
    }
    if not hospital_ids:
        raise NotificationPayloadError("MISSING_ESSENCE_DIGEST_HOSPITALS_REQUIRED")
    hospital_count = len(hospital_ids)
    item_count = len(skipped_outcomes)
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=onboarding")
    summary = f"온보딩 병원 {hospital_count}곳 · 글 {item_count}건"
    message = validated_message(
        RenderedSlackMessage(
            f"온보딩 생성 요약 · {summary} · 승인 기준이 없어 생성을 건너뜀",
            (
                header_block("missing_essence_digest_header", "온보딩 생성 요약"),
                section_block(
                    "missing_essence_digest_summary",
                    f"*{summary}*\n승인 기준이 없어 생성을 건너뜀.",
                ),
                action_block(
                    "missing_essence_digest_action",
                    action_url,
                    "온보딩 현황 확인",
                ),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=f"{_MISSING_ESSENCE_DIGEST_DEDUPE_PREFIX}{cycle_date.isoformat()}",
        notification_type=MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


def build_generation_blocked_digest_intent(
    cycle_date: date,
    batch: str,
    blocked_outcomes: Sequence[Mapping[str, object]],
) -> NotificationIntent:
    """Build one blocked-publication summary for a Seoul morning batch.

    Per-item incidents stay in the database because they drive the Admin retry
    controls. Slack gets one grouped message instead of one page per content item.
    """

    if not blocked_outcomes:
        raise NotificationPayloadError("GENERATION_BLOCKED_DIGEST_ITEMS_REQUIRED")
    entries = [
        (
            str(outcome.get("hospital_name") or "이름 미확인 병원"),
            str(outcome.get("content_id") or ""),
            str(outcome.get("code") or "UNKNOWN"),
            str(outcome.get("cause") or "자동 생성 작업이 완료되지 않았습니다."),
            str(outcome.get("title") or "제목 없는 콘텐츠"),
        )
        for outcome in blocked_outcomes
    ]
    identity = sorted({f"{content_id}:{code}" for _, content_id, code, _, _ in entries})
    digest = hashlib.sha256("\n".join(identity).encode()).hexdigest()[:32]
    hospitals: dict[str, list[tuple[str, str, str]]] = {}
    for hospital_name, _content_id, code, cause, title in entries:
        hospitals.setdefault(hospital_name, []).append((title, code, cause))
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=incidents&status=OPEN")
    shown = sorted(hospitals.items())[:_DIGEST_MAX_HOSPITALS]
    hidden = len(hospitals) - len(shown)
    lines = []
    for hospital_name, items in shown:
        detail = " · ".join(
            f"{_publish_safe_text(title, 60)}({_publish_safe_text(cause, 80)})"
            for title, _code, cause in items[:_DIGEST_MAX_ITEMS_PER_HOSPITAL]
        )
        remainder = len(items) - min(len(items), _DIGEST_MAX_ITEMS_PER_HOSPITAL)
        if remainder > 0:
            detail = f"{detail} · 그 외 {remainder}건"
        lines.append(f"• *{_publish_safe_text(hospital_name, 100)}* 차단 {len(items)}건\n  {detail}")
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}곳")
    summary = f"병원 {len(hospitals)}곳 · 글 {len(entries)}건"
    message = validated_message(
        RenderedSlackMessage(
            f"무슨 문제인지: 자동 발행 차단 {summary} · "
            "고객 영향: 예정 글이 공개되지 않음 · "
            "지금 할 일: 운영센터에서 차단 항목 조치 · 처리 기한: 오늘 중",
            (
                header_block("generation_blocked_digest_header", "자동 발행 차단 요약"),
                section_block("generation_blocked_digest_summary", f"*{summary}*"),
                section_block("generation_blocked_digest_items", "\n".join(lines)),
                action_block(
                    "generation_blocked_digest_action", action_url, "운영센터에서 모아보기"
                ),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=(
            f"{_GENERATION_BLOCKED_DIGEST_DEDUPE_PREFIX}"
            f"{cycle_date.isoformat()}:{batch}:{digest}"
        ),
        notification_type=GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


def build_post_publish_review_overdue_intent(
    item: PublishedItem, hospital: HospitalIdentity
) -> NotificationIntent:
    """Build one overdue human-review alert for one publication episode."""

    if item.published_at is None:
        raise NotificationPayloadError("PUBLISHED_AT_REQUIRED")
    action_url = admin_url(
        settings.ADMIN_BASE_URL,
        f"/hospitals/{hospital.id}/content?content={item.id}",
    )
    hospital_name = _publish_safe_text(hospital.name, 100)
    title = _publish_safe_text(item.title or "제목 없는 콘텐츠", 180)
    details = (
        "무슨 문제인지: 자동 공개 후 표본 검수가 24시간 넘게 남아 있습니다.\n"
        "고객 영향: 자동 검수는 통과했지만 사람의 최소 사후 확인이 지연되고 있습니다.\n"
        "지금 할 일: Admin에서 공개 글과 이미지를 확인하고 검수 완료 처리해 주세요.\n"
        "처리 기한: 가능한 한 빨리"
    )
    message = validated_message(
        RenderedSlackMessage(
            "무슨 문제인지: 공개 후 표본 검수 지연 · "
            "고객 영향: 사후 확인 지연 · 지금 할 일: Admin 검수 완료 · 처리 기한: 가능한 한 빨리",
            (
                header_block("post_publish_review_header", "공개 후 검수 지연"),
                section_block("post_publish_review_identity", f"*{hospital_name}*\n{title}"),
                section_block("post_publish_review_context", details),
                action_block("post_publish_review_action", action_url, "Admin에서 검수 완료"),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=_review_overdue_dedupe_key(item.id, item.published_at),
        notification_type=POST_PUBLISH_REVIEW_OVERDUE_TYPE,
        message=message,
        hospital_id=hospital.id,
        max_attempts=3,
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

    return _enqueue_notification_sync(db, build_publish_notification_intent(item, hospital))


def enqueue_content_publish_digest_sync(
    db: Session,
    cycle_date: date,
    published_outcomes: Sequence[Mapping[str, object]],
) -> NotificationOutbox:
    """Add at most one morning publication digest for the Seoul calendar date."""

    intent = build_content_publish_digest_intent(cycle_date, published_outcomes)
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def enqueue_missing_approved_essence_digest_sync(
    db: Session,
    cycle_date: date,
    skipped_outcomes: Sequence[Mapping[str, object]],
) -> NotificationOutbox:
    """Add at most one onboarding skip digest for the Seoul calendar date."""

    intent = build_missing_approved_essence_digest_intent(cycle_date, skipped_outcomes)
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def enqueue_generation_blocked_digest_sync(
    db: Session,
    cycle_date: date,
    batch: str,
    blocked_outcomes: Sequence[Mapping[str, object]],
) -> NotificationOutbox | None:
    """Add at most one blocked-publication digest per morning batch and blocked set."""

    if not blocked_outcomes:
        return None
    intent = build_generation_blocked_digest_intent(cycle_date, batch, blocked_outcomes)
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def enqueue_post_publish_review_overdue_notification_sync(
    db: Session, item: PublishedItem, hospital: HospitalIdentity
) -> NotificationOutbox:
    """Add one overdue-review notification intent without committing."""

    intent = build_post_publish_review_overdue_intent(item, hospital)
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def _enqueue_notification_sync(db: Session, intent: NotificationIntent) -> NotificationOutbox:
    now = datetime.now(UTC)
    row = NotificationOutbox(
        hospital_id=intent.hospital_id,
        incident_id=intent.incident_id,
        operation_run_id=intent.operation_run_id,
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
            return {"state": "NOT_REQUIRED", "label": "자동 관제 중",
                    "problem": None,
                    "next_action": "문제가 감지된 항목만 예외 큐에 표시됩니다.", **common}
        case unreachable:
            assert_never(unreachable)


def _publish_dedupe_key(content_id: uuid.UUID, published_at: datetime) -> str:
    return f"{_DEDUPE_PREFIX}{content_id}:{_publication_epoch_micros(published_at)}"


def _review_overdue_dedupe_key(content_id: uuid.UUID, published_at: datetime) -> str:
    return f"{_REVIEW_OVERDUE_DEDUPE_PREFIX}{content_id}:{_publication_epoch_micros(published_at)}"


def _publication_epoch_micros(published_at: datetime) -> int:
    normalized = published_at.astimezone(UTC)
    elapsed = normalized - _EPOCH
    return ((elapsed.days * 86_400) + elapsed.seconds) * 1_000_000 + elapsed.microseconds
