"""Typed Slack projections for onboarding and monthly milestones."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import NotificationOutbox
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
)
from app.services.notification_copy import display_time
from app.services.notification_milestone_rendering import (
    MAX_BLOCKS,
    MAX_SUMMARY_ITEMS,
    RenderedSlackMessage,
    action_block,
    admin_url,
    canonical_time,
    chunk_lines,
    header_block,
    safe_text,
    section_block,
    validate_stable_id,
    validated_message,
)
from app.services.notification_outbox import enqueue_notification


class MilestoneKind(StrEnum):
    HANDOFF_OVERDUE = "HANDOFF_OVERDUE"
    HANDOFF_ACCEPTED = "HANDOFF_ACCEPTED"
    ACTIVATION_READY = "ACTIVATION_READY"
    HOSPITAL_ACTIVE = "HOSPITAL_ACTIVE"
    MONTHLY_BLOCKED = "MONTHLY_BLOCKED"
    MONTHLY_ARTIFACT_PENDING = "MONTHLY_ARTIFACT_PENDING"
    MONTHLY_CUSTOMER_READY = "MONTHLY_CUSTOMER_READY"
    DELIVERY_CORRECTED = "DELIVERY_CORRECTED"
    DELIVERY_RESCINDED = "DELIVERY_RESCINDED"
    DELIVERY_REDELIVERED = "DELIVERY_REDELIVERED"


@dataclass(frozen=True, slots=True)
class MilestoneProjection:
    stable_id: str
    kind: MilestoneKind
    hospital_id: uuid.UUID
    hospital_name: str
    status_label: str
    customer_impact: str
    next_action: str
    owner_label: str
    sla_label: str
    admin_path: str
    requires_action: bool
    is_recovery: bool
    recovery_of: str | None = None
    # 월간 관측값을 100번 기준으로 환산한 한 줄. 유의성/인과관계를 단정하지 않는다.
    # 알림만 보고 원장에게 무슨 말을 할지 알 수 있게 한다.
    headline_label: str | None = None
    period_label: str | None = None


@dataclass(frozen=True, slots=True)
class MilestoneBatch:
    milestones: tuple[MilestoneProjection, ...]
    window_start: datetime
    window_end: datetime


def build_milestone_action_notification(
    milestone: MilestoneProjection, admin_base_url: str
) -> NotificationIntent:
    if not milestone.requires_action:
        raise NotificationPayloadError("MILESTONE_ACTION_NOT_REQUIRED")
    return _single_notification(milestone, admin_base_url, recovery=False)


def build_milestone_recovery_notification(
    milestone: MilestoneProjection, admin_base_url: str
) -> NotificationIntent:
    if not milestone.is_recovery or milestone.recovery_of is None:
        raise NotificationPayloadError("MILESTONE_RECOVERY_FACT_REQUIRED")
    return _single_notification(milestone, admin_base_url, recovery=True)


def build_milestone_summary_notification(
    batch: MilestoneBatch, admin_base_url: str
) -> NotificationIntent:
    ordered = _ordered_unique(batch.milestones)
    window_start = canonical_time(batch.window_start)
    window_end = canonical_time(batch.window_end)
    if batch.window_end <= batch.window_start:
        raise NotificationPayloadError("MILESTONE_WINDOW_INVALID")
    identity = {
        "event_ids": [item.stable_id for item in ordered],
        "window_end": window_end,
        "window_start": window_start,
    }
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    url = admin_url(admin_base_url, _summary_path(ordered))
    displayed = ordered[:MAX_SUMMARY_ITEMS]
    lines = tuple(_summary_line(item) for item in displayed)
    remaining = len(ordered) - len(displayed)
    if remaining:
        lines = (*lines, f"• 그 외 {remaining}건 · 전체 내역은 관련 작업에서 확인")
    chunks = chunk_lines(lines)
    if len(chunks) > MAX_BLOCKS - 3:
        raise NotificationPayloadError("MILESTONE_SUMMARY_EXCEEDS_SLACK_LIMIT")
    blocks = (
        header_block("milestone_summary_header", f"[업무 알림] 병원 {len({item.hospital_id for item in ordered})}곳 · {len(ordered)}건"),
        section_block(
            "milestone_summary_window",
            f"{display_time(batch.window_start)} ~ {display_time(batch.window_end)} · 새로 확인된 항목만 모았습니다.",
        ),
        *(section_block(f"milestone_summary_{index}", chunk) for index, chunk in enumerate(chunks)),
        action_block("milestone_summary_action", url, "관련 작업 모아보기"),
    )
    message = validated_message(
        RenderedSlackMessage(
            f"[업무 알림] {len(ordered)}건 | " + " / ".join(
                f"{safe_text(item.hospital_name, 50)} {item.period_label or ''}: {safe_text(item.status_label, 70)}"
                for item in displayed[:5]
            ) + " | 아래 관련 작업에서 처리해 주세요.",
            blocks,
            url,
        ),
        admin_base_url,
    )
    hospital_ids = {item.hospital_id for item in ordered}
    return NotificationIntent(
        dedupe_key=f"MILESTONE_SUMMARY:{digest}",
        notification_type="MILESTONE_SUMMARY",
        message=message,
        hospital_id=next(iter(hospital_ids)) if len(hospital_ids) == 1 else None,
    )


async def enqueue_milestone_summary(
    db: AsyncSession,
    batch: MilestoneBatch,
    admin_base_url: str,
) -> NotificationOutbox:
    """Enqueue one summary inside the caller's uncommitted domain transaction."""

    return await enqueue_notification(
        db, build_milestone_summary_notification(batch, admin_base_url)
    )


def _single_notification(
    milestone: MilestoneProjection, admin_base_url: str, *, recovery: bool
) -> NotificationIntent:
    validate_stable_id(milestone.stable_id)
    url = admin_url(admin_base_url, milestone.admin_path)
    status = safe_text(milestone.status_label, 100)
    period = (safe_text(milestone.period_label, 30) + " · ") if milestone.period_label else ""
    lines = [period + status, safe_text(milestone.customer_impact, 200)]
    if milestone.headline_label:
        lines.append(safe_text(milestone.headline_label, 140))
    lines.append("\n지금 할 일: " + safe_text(milestone.next_action, 300))
    if milestone.sla_label not in {"", "기한 없음", "확인 필요"}:
        lines.append("처리 기한: " + safe_text(milestone.sla_label, 80))
    details = "\n".join(lines)
    blocks = (
        header_block("milestone_header", status),
        section_block(
            "milestone_identity",
            f"*{safe_text(milestone.hospital_name, 100)}*",
        ),
        section_block("milestone_context", details),
        action_block("milestone_action", url, _action_label(milestone.kind)),
    )
    event = "MILESTONE_RECOVERED" if recovery else "MILESTONE_ACTION"
    message = validated_message(
        RenderedSlackMessage(
            f"[업무 알림] {safe_text(milestone.hospital_name, 80)} · {period}{status} | "
            f"{safe_text(milestone.next_action, 300)}",
            blocks,
            url,
        ),
        admin_base_url,
    )
    return NotificationIntent(
        dedupe_key=f"{event}:{milestone.stable_id}",
        notification_type=event,
        message=message,
        hospital_id=milestone.hospital_id,
    )


def _ordered_unique(
    milestones: Sequence[MilestoneProjection],
) -> tuple[MilestoneProjection, ...]:
    unique: dict[str, MilestoneProjection] = {}
    for milestone in milestones:
        validate_stable_id(milestone.stable_id)
        existing = unique.get(milestone.stable_id)
        if existing is not None and existing != milestone:
            raise NotificationPayloadError("MILESTONE_ID_CONFLICT")
        unique[milestone.stable_id] = milestone
    if not unique:
        raise NotificationPayloadError("MILESTONE_SUMMARY_REQUIRES_EVENTS")
    return tuple(sorted(unique.values(), key=lambda item: item.stable_id))


def _summary_path(milestones: Sequence[MilestoneProjection]) -> str:
    paths = {item.admin_path for item in milestones}
    return next(iter(paths)) if len(paths) == 1 else "/operations"


def _summary_line(milestone: MilestoneProjection) -> str:
    period = (safe_text(milestone.period_label, 30) + " · ") if milestone.period_label else ""
    text = f"• *{safe_text(milestone.hospital_name, 80)}* · {period}{safe_text(milestone.status_label, 90)}\n"
    text += "  " + safe_text(milestone.next_action, 230)
    if milestone.headline_label and milestone.kind is MilestoneKind.MONTHLY_CUSTOMER_READY:
        text += "\n  " + safe_text(milestone.headline_label, 140)
    return text


def _action_label(kind: MilestoneKind) -> str:
    match kind:
        case MilestoneKind.HANDOFF_OVERDUE:
            return "고객 인계 승인하기"
        case MilestoneKind.HANDOFF_ACCEPTED:
            return "병원 준비 현황 보기"
        case MilestoneKind.ACTIVATION_READY:
            return "도메인 상태 확인하기"
        case MilestoneKind.HOSPITAL_ACTIVE:
            return "병원 운영 현황 보기"
        case MilestoneKind.MONTHLY_BLOCKED:
            return "차단 사유 확인"
        case MilestoneKind.MONTHLY_ARTIFACT_PENDING:
            return "PDF 준비 상태 보기"
        case MilestoneKind.MONTHLY_CUSTOMER_READY:
            return "고객용 PDF·전달 기록"
        case MilestoneKind.DELIVERY_CORRECTED:
            return "수정된 전달 기록 확인"
        case MilestoneKind.DELIVERY_RESCINDED:
            return "무효 처리 기록 확인"
        case MilestoneKind.DELIVERY_REDELIVERED:
            return "재전달 기록 확인"
