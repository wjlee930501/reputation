"""Durable control state for cache refreshes after committed publication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services.dependency_incident_helpers import incident_projection, open_notice_exists
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import mark_recovered, mark_retrying, open_or_touch_incident
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.notification_store import enqueue_notification

REVALIDATION_RETRY_DELAYS_SECONDS = (60, 300, 900)
_OPERATION_TYPE = "SITE_REVALIDATION"
_SOURCE_TYPE = "SITE_REVALIDATION"

# 공개 표면 캐시 갱신의 방향. 내림(UNPUBLISH)은 반려·비공개로 공개 글이 사라져야 하는
# 경우다 — 여기서 재시도가 끊기면 의료광고 위반 글이 ISR 캐시에서 계속 서빙된다.
DIRECTION_PUBLISH = "PUBLISH"
DIRECTION_UNPUBLISH = "UNPUBLISH"


@dataclass(frozen=True, slots=True)
class RevalidationRetryPlan:
    run_id: uuid.UUID
    delay_seconds: int | None
    operator_action_required: bool
    created: bool = False


def retry_delay(attempt_count: int) -> int | None:
    if 0 <= attempt_count < len(REVALIDATION_RETRY_DELAYS_SECONDS):
        return REVALIDATION_RETRY_DELAYS_SECONDS[attempt_count]
    return None


def run_revalidation_direction(request_payload: dict | None) -> str:
    """Read back the direction a durable revalidation run was opened for."""

    payload = request_payload or {}
    return (
        DIRECTION_UNPUBLISH
        if payload.get("direction") == DIRECTION_UNPUBLISH
        else DIRECTION_PUBLISH
    )


def content_is_revalidation_recoverable(content: ContentItem, *, direction: str) -> bool:
    """공개 표면에 한 번이라도 실린 아이템인가 — 현재 status는 묻지 않는다.

    반려/취소로 내려간 글도 캐시에는 그대로 남아 있으므로, 복구 계획 조회와 재시도
    컨텍스트가 **같은** 조건을 쓴다. 내림 방향 run은 반려가 published_at을 지운 뒤에도
    "발행된 적 있었다"는 사실을 payload로 증명한다.
    """

    return content.published_at is not None or direction == DIRECTION_UNPUBLISH


async def start_revalidation_failure(
    slug: str,
    content_id: uuid.UUID,
    *,
    unpublished_from: datetime | None = None,
) -> RevalidationRetryPlan | None:
    """Persist the first failed cache refresh for a committed publish **or** unpublish.

    `unpublished_from`은 반려 직전의 published_at이다. 반려 경로가 발행 메타를 지우므로
    이 값이 없으면 "내려간 글"의 캐시 판(edition)을 식별할 수 없다.
    """

    normalized_slug = slug.strip().lower()
    if not normalized_slug:
        return None
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        row = (
            await db.execute(
                select(Hospital, ContentItem)
                .join(ContentItem, ContentItem.hospital_id == Hospital.id)
                .where(
                    Hospital.slug == normalized_slug,
                    ContentItem.id == content_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        hospital, content = row
        hospital_id = hospital.id
        direction = (
            DIRECTION_PUBLISH
            if content.status == ContentStatus.PUBLISHED and content.published_at is not None
            else DIRECTION_UNPUBLISH
        )
        # 캐시에 실린 판 식별자 — 올림은 현재 발행 시각, 내림은 직전 발행 시각.
        edition = content.published_at or unpublished_from
        if edition is None:
            # 공개된 적 없는 아이템은 캐시에도 실린 적이 없다 → 복구할 대상 자체가 없다.
            return None
        # 같은 아이템의 올림/내림이 하나의 run으로 합쳐지면, 나중 내림이 예전 올림 run에
        # 흡수돼 재시도 없이 사라진다. 방향을 키에 넣어 분리한다.
        key = (
            f"site-revalidation:{content.id}:{edition.isoformat()}"
            if direction == DIRECTION_PUBLISH
            else f"site-revalidation:{content.id}:unpublish:{edition.isoformat()}"
        )
        existing = await db.scalar(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == _OPERATION_TYPE,
                OperationRun.idempotency_key == key,
            )
        )
        if existing is not None:
            return RevalidationRetryPlan(
                existing.id,
                retry_delay(existing.attempt_count)
                if existing.state == OperationRunState.RUNNING.value
                else None,
                existing.state == OperationRunState.FAILED.value,
            )

        now = datetime.now(UTC)
        run = OperationRun(
            hospital_id=hospital_id,
            operation_type=_OPERATION_TYPE,
            state=OperationRunState.RUNNING.value,
            idempotency_key=key,
            request_payload={"content_id": str(content.id), "direction": direction},
            result_summary={"publication_committed": True, "direction": direction},
            safe_error_code="CACHE_REVALIDATION_FAILED",
            safe_error_message=(
                "공개 페이지에 최신 발행 내용이 아직 반영되지 않았습니다."
                if direction == DIRECTION_PUBLISH
                else "공개 페이지에서 내린 글이 아직 사라지지 않았습니다."
            ),
            started_at=now,
            heartbeat_at=now,
            total_count=1,
        )
        db.add(run)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(OperationRun).where(
                    OperationRun.hospital_id == hospital_id,
                    OperationRun.operation_type == _OPERATION_TYPE,
                    OperationRun.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            return RevalidationRetryPlan(existing.id, None, False)
        await _touch_incident(db, run, terminal=False)
        await db.commit()
        return RevalidationRetryPlan(run.id, REVALIDATION_RETRY_DELAYS_SECONDS[0], False, True)


async def record_retry_failure(
    run_id: uuid.UUID, expected_attempt_count: int
) -> RevalidationRetryPlan | None:
    """Advance one observed retry failure and escalate only after the third retry."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        run = await db.scalar(
            select(OperationRun).where(OperationRun.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.state != OperationRunState.RUNNING.value
            or run.attempt_count != expected_attempt_count
        ):
            return None
        run.attempt_count += 1
        run.heartbeat_at = datetime.now(UTC)
        delay = retry_delay(run.attempt_count)
        terminal = delay is None
        if terminal:
            run.state = OperationRunState.FAILED.value
            run.failure_count = 1
            run.completed_at = run.heartbeat_at
        incident = await _touch_incident(db, run, terminal=terminal)
        if terminal:
            hospital = await db.get(Hospital, run.hospital_id)
            if hospital is not None:
                await enqueue_notification(
                    db,
                    build_open_incident_notification(
                        incident_projection(incident, hospital.name, run.id, "확인 필요"),
                        settings.ADMIN_BASE_URL,
                    ),
                )
        await db.commit()
        return RevalidationRetryPlan(run.id, delay, terminal)


async def record_revalidation_success(run_id: uuid.UUID, expected_attempt_count: int) -> bool:
    """Close the exact cache incident only after an observed successful refresh."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        run = await db.scalar(
            select(OperationRun).where(OperationRun.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.state != OperationRunState.RUNNING.value
            or run.attempt_count != expected_attempt_count
        ):
            return False
        run.attempt_count += 1
        run.state = OperationRunState.SUCCEEDED.value
        run.success_count = 1
        run.safe_error_code = None
        run.safe_error_message = None
        run.completed_at = datetime.now(UTC)
        incident = await db.scalar(
            select(Incident).where(
                Incident.operation_run_id == run.id,
                Incident.source_type == _SOURCE_TYPE,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
        if incident is not None:
            if incident.state == IncidentState.OPEN.value:
                transition = await mark_retrying(
                    db,
                    incident.id,
                    expected_version=incident.version,
                    actor="site-revalidation-worker",
                    reason="cache refresh retry started",
                )
                if isinstance(transition, Incident):
                    incident = transition
            recovered = await mark_recovered(
                db,
                incident.id,
                expected_version=incident.version,
                observed_success=True,
                actor="site-revalidation-worker",
                reason="public cache refresh observed",
            )
            if isinstance(recovered, Incident) and await open_notice_exists(db, incident.id):
                hospital = await db.get(Hospital, run.hospital_id)
                if hospital is not None:
                    await enqueue_notification(
                        db,
                        build_recovered_incident_notification(
                            incident_projection(recovered, hospital.name, run.id, "복구됨"),
                            settings.ADMIN_BASE_URL,
                        ),
                    )
        await db.commit()
        return True


async def _touch_incident(
    db: AsyncSession,
    run: OperationRun,
    *,
    terminal: bool,
) -> Incident:
    raw_content_id = run.request_payload.get("content_id")
    hospital_scope = run.request_payload.get("scope") == "HOSPITAL"
    object_type = "hospital" if hospital_scope else "content_item"
    object_id = str(run.hospital_id) if hospital_scope else str(raw_content_id)
    unpublishing = (
        not hospital_scope
        and run_revalidation_direction(run.request_payload) == DIRECTION_UNPUBLISH
    )
    if hospital_scope:
        customer_impact = "공개 페이지에 병원 정보 변경이 늦게 반영될 수 있습니다."
    elif unpublishing:
        # 내린 글이 캐시에 남는 건 단순 지연이 아니라 "내려야 할 글이 계속 보이는" 상태다.
        customer_impact = "공개에서 내린 글이 공개 페이지에 잠시 계속 보일 수 있습니다."
    else:
        customer_impact = "콘텐츠 발행은 완료됐지만 공개 페이지에는 이전 내용이 잠시 보일 수 있습니다."
    return await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="site_revalidation",
            object_type=object_type,
            object_id=object_id,
            fingerprint=IncidentFingerprint.CACHE_REVALIDATION_FAILED,
            incident_type="CACHE_REVALIDATION_FAILED",
            severity=IncidentSeverity.HIGH if terminal else IncidentSeverity.MEDIUM,
            customer_impact=customer_impact,
            source_type=_SOURCE_TYPE,
            next_action=(
                "운영 센터에서 개발팀 문의용 정보를 복사해 전달하세요. 발행 버튼을 다시 누르지 마세요."
                if terminal
                else "시스템이 자동으로 다시 확인 중입니다. 다음 확인 시각까지 기다려 주세요."
            ),
            admin_path="/operations",
            hospital_id=run.hospital_id,
            operation_run_id=run.id,
            source_id=object_id,
            safe_error_code="CACHE_REVALIDATION_FAILED",
            safe_error_message="공개 페이지 최신화 작업을 완료하지 못했습니다.",
        ),
        actor="site-revalidation-worker",
        reason="public cache refresh failed",
    )
