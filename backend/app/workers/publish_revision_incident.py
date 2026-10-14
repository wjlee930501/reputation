"""승인 판이 만들어지지 않아 발행기가 건너뛴 글의 기록·개발 사고(2026-10-14 누락 방지 1).

발행기(`tasks._auto_publish_one`)는 모든 게이트를 통과한 글을 PUBLISHED로 바꾼 뒤 승인 판 SQL
(`reconcile_content_revisions`)이 판을 만들지 못하면 트랜잭션을 되돌리고 건너뛴다. 종전에는 로그 한
줄뿐이라 같은 글이 매시간 조용히 건너뛰어졌다 — 감사 기록·사고가 없고, 감시는 그날 발행 0건일 때만
울렸다. 되돌린 뒤 **새 트랜잭션**에서:

- 발행 보류 감사(`auto_publish_blocked`, 코드 `APPROVED_REVISION_NOT_CREATED`)를 남긴다. 외부 감시의
  발행 보류 집계(`pipeline_watchdog._publish_block_facts`)가 이 기록을 읽는다.
- 글당 개발 사고(`AUTO_PUBLISH_REVISION_NOT_CREATED`) 하나를 열거나 갱신한다. 판 SQL의 조건은 코드가
  아니라 DB 함수가 정하므로 AE가 고칠 수 없다 — 개발 대상이다. 글이 발행되거나 발행 대상에서 빠지면
  근거 정리(`incident_backlog`)가 닫는다.

기록 실패는 발행기를 멈추지 않는다(로그만 남긴다).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SyncSessionLocal, get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.audit_log import write_audit_log_sync
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_type_of,
)
from app.services.incidents import build_incident_key, open_or_touch_incident
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification
from app.services.post_publish_review_policy import AUTO_PUBLISH_BLOCKED_ACTION

logger = logging.getLogger(__name__)

REVISION_NOT_CREATED_CODE = "APPROVED_REVISION_NOT_CREATED"
REVISION_NOT_CREATED_INCIDENT_TYPE = "AUTO_PUBLISH_REVISION_NOT_CREATED"
REVISION_NOT_CREATED_MESSAGE = "발행 검사는 통과했지만 승인 판(reconcile_content_revisions)이 만들어지지 않아 발행하지 못했습니다."
_PIPELINE = "content_publication"
_OBJECT_TYPE = "content_item"


@dataclass(frozen=True, slots=True)
class RevisionNotCreated:
    content_id: uuid.UUID
    hospital_id: uuid.UUID
    hospital_name: str
    scheduled_date: date | None
    title: str | None
    actor: str
    created_count: int | None
    active_revision_set: bool


def _dedupe_key(content_id: uuid.UUID) -> str:
    return build_incident_key(
        _PIPELINE, _OBJECT_TYPE, str(content_id), IncidentFingerprint.VALIDATION_FAILED
    )


def _request(fact: RevisionNotCreated) -> IncidentOpenRequest:
    return IncidentOpenRequest(
        pipeline=_PIPELINE,
        object_type=_OBJECT_TYPE,
        object_id=str(fact.content_id),
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type=REVISION_NOT_CREATED_INCIDENT_TYPE,
        severity=IncidentSeverity.HIGH,
        customer_impact="발행 검사를 통과한 글이 승인 판을 얻지 못해 예정일에 공개되지 않고 있습니다.",
        source_type="CONTENT_ITEM",
        next_action=(
            "개발 담당자는 승인 판 SQL(reconcile_content_revisions)이 이 글을 받아 주지 않는 조건을 "
            "확인해 주세요. 발행기는 매시간 다시 시도합니다."
        ),
        admin_path=f"/hospitals/{fact.hospital_id}/content",
        hospital_id=fact.hospital_id,
        source_id=str(fact.content_id),
        safe_error_code=REVISION_NOT_CREATED_CODE,
        safe_error_message=(
            f"created_count={fact.created_count} active_revision_set={fact.active_revision_set}"
        ),
    )


def _write_audit(fact: RevisionNotCreated) -> None:
    with SyncSessionLocal() as db:
        write_audit_log_sync(
            db,
            action=AUTO_PUBLISH_BLOCKED_ACTION,
            hospital_id=fact.hospital_id,
            actor=fact.actor,
            target_type="content_item",
            target_id=fact.content_id,
            detail={
                "code": REVISION_NOT_CREATED_CODE,
                "reason": REVISION_NOT_CREATED_MESSAGE,
                "scheduled_date": str(fact.scheduled_date) if fact.scheduled_date else None,
                "created_count": fact.created_count,
                "active_revision_set": fact.active_revision_set,
            },
        )
        db.commit()


async def open_revision_not_created_incident(fact: RevisionNotCreated) -> uuid.UUID:
    """글당 개발 사고 하나를 열거나 갱신한다. 처음 열 때(또는 닫힌 뒤 다시 열 때)만 알린다."""

    observed_at = datetime.now(UTC)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        previous = await db.scalar(
            select(Incident).where(Incident.dedupe_key == _dedupe_key(fact.content_id))
        )
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            _request(fact),
            actor="system:auto-publish",
            reason="approved revision not created",
            now=observed_at,
        )
        if previous_state is None or previous_state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }:
            await enqueue_notification(
                db,
                build_open_incident_notification(
                    IncidentSlackProjection(
                        incident.id,
                        fact.hospital_name,
                        incident.severity,
                        incident.customer_impact,
                        incident.next_action,
                        incident.admin_path,
                        "개발 담당자",
                        "다음 발행 시각 전",
                        incident.hospital_id,
                        incident.operation_run_id,
                        incident.version,
                        incident.safe_error_message or REVISION_NOT_CREATED_MESSAGE,
                        incident.episode_seq,
                        incident_type=incident_type_of(incident),
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


def record_revision_not_created(fact: RevisionNotCreated, *, run_async) -> None:
    """감사 기록과 개발 사고를 남긴다. 어느 쪽이 실패해도 예외를 밖으로 내지 않는다."""

    try:
        _write_audit(fact)
    except Exception:  # noqa: BROAD_EXCEPT_OK -- 기록 실패가 발행기를 멈추지 않는다
        logger.exception("auto publish revision audit failed: content_id=%s", fact.content_id)
    try:
        run_async(open_revision_not_created_incident(fact))
    except Exception:  # noqa: BROAD_EXCEPT_OK -- 사고 기록 실패가 발행기를 멈추지 않는다
        logger.exception("auto publish revision incident failed: content_id=%s", fact.content_id)
