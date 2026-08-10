"""Incident and durable Slack projections for Naver source recovery."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.operations import Incident, IncidentSeverity
from app.services.incidents import (
    IncidentFingerprint,
    IncidentNotFound,
    IncidentOpenRequest,
    IncidentTransitionConflict,
    IncidentVersionConflict,
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.naver_handoff_contracts import NaverHandoffItem
from app.services.notification_outbox import (
    NotificationIntent,
    SlackMessage,
    enqueue_notification,
)


@dataclass(frozen=True, slots=True)
class NaverIncidentContext:
    hospital_id: uuid.UUID
    hospital_name: str
    operation_run_id: uuid.UUID
    item: NaverHandoffItem
    actor: str = "NAVER_SOURCE_SYNC"


async def record_naver_failure(
    db: AsyncSession, context: NaverIncidentContext
) -> Incident:
    """Create one stable, nontechnical incident for a failed public post."""
    return await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="naver_source_handoff",
            object_type="naver_post",
            object_id=_incident_object_id(context.hospital_id, context.item.url_hash),
            fingerprint=IncidentFingerprint.PROVIDER_REJECTED,
            incident_type="NAVER_SOURCE_FETCH_FAILED",
            severity=IncidentSeverity.MEDIUM,
            customer_impact=(
                "이 블로그 글은 아직 병원 근거 자료에 추가되지 않아 다음 콘텐츠 작성에 "
                "반영되지 않습니다. 이미 수집된 다른 자료와 공개 콘텐츠에는 영향이 없습니다."
            ),
            source_type="NAVER_POST",
            next_action=(
                "병원 자료 화면에서 ‘실패한 글 다시 수집’을 눌러 주세요. 다시 실패하면 "
                "작업 번호와 글 식별값을 개발팀에 전달해 주세요."
            ),
            admin_path=f"/hospitals/{context.hospital_id}/onboarding",
            hospital_id=context.hospital_id,
            operation_run_id=context.operation_run_id,
            source_id=context.item.url_hash,
            safe_error_code=context.item.safe_error_code,
            safe_error_message=context.item.safe_error_message,
        ),
        actor=context.actor,
        reason="네이버 글 본문 수집 실패",
    )


async def mark_naver_retrying(
    db: AsyncSession, context: NaverIncidentContext
) -> Incident | None:
    incident = await _find_incident(db, context.hospital_id, context.item.url_hash)
    if incident is None:
        incident = await record_naver_failure(db, context)
    result = await mark_retrying(
        db,
        incident.id,
        expected_version=incident.version,
        actor=context.actor,
        reason="운영 담당자가 실패한 글만 다시 수집",
    )
    match result:
        case Incident() as changed:
            return changed
        case IncidentNotFound() | IncidentVersionConflict() | IncidentTransitionConflict():
            return None
        case unreachable:
            assert_never(unreachable)


async def record_naver_recovery(
    db: AsyncSession,
    context: NaverIncidentContext,
    retrying_incident: Incident | None,
) -> Incident | None:
    if retrying_incident is None:
        return None
    result = await mark_recovered(
        db,
        retrying_incident.id,
        expected_version=retrying_incident.version,
        observed_success=True,
        actor=context.actor,
        reason="실패했던 네이버 글 본문과 근거 자료 저장을 확인",
    )
    match result:
        case Incident() as recovered:
            await enqueue_notification(db, _recovery_intent(context, recovered))
            return recovered
        case IncidentNotFound() | IncidentVersionConflict() | IncidentTransitionConflict():
            return None
        case unreachable:
            assert_never(unreachable)


def _incident_object_id(hospital_id: uuid.UUID, url_hash: str) -> str:
    return f"{hospital_id}:{url_hash}"


async def _find_incident(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    url_hash: str,
) -> Incident | None:
    key = build_incident_key(
        "naver_source_handoff",
        "naver_post",
        _incident_object_id(hospital_id, url_hash),
        IncidentFingerprint.PROVIDER_REJECTED,
    )
    return await db.scalar(
        select(Incident).where(
            Incident.dedupe_key == key,
            Incident.hospital_id == hospital_id,
        )
    )


def _recovery_intent(context: NaverIncidentContext, incident: Incident) -> NotificationIntent:
    admin_url = (
        f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{context.hospital_id}/onboarding"
    )
    message = SlackMessage(
        fallback_text=f"[자료 수집 복구] {context.hospital_name}",
        blocks=(
            {
                "type": "header",
                "block_id": "naver_recovered_header",
                "text": {"type": "plain_text", "text": "네이버 자료 수집 복구 완료"},
            },
            {
                "type": "section",
                "block_id": "naver_recovered_context",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{context.hospital_name}*의 실패했던 블로그 글을 다시 수집했습니다.\n"
                        "근거 자료에 정상 추가되었으며, 기존 자료는 변경하지 않았습니다."
                    ),
                },
            },
            {
                "type": "section",
                "block_id": "naver_recovered_action",
                "text": {
                    "type": "mrkdwn",
                    "text": "다음 행동: 병원 자료 화면에서 새 자료의 내용을 검토해 주세요.",
                },
            },
            {
                "type": "actions",
                "block_id": "naver_recovered_button",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "새 자료 확인"},
                        "url": admin_url,
                    }
                ],
            },
        ),
        admin_url=admin_url,
    )
    return NotificationIntent(
        dedupe_key=f"NAVER_SOURCE_RECOVERED:{incident.id}:v{incident.version}",
        notification_type="NAVER_SOURCE_RECOVERED",
        message=message,
        hospital_id=context.hospital_id,
        incident_id=incident.id,
        operation_run_id=context.operation_run_id,
    )
