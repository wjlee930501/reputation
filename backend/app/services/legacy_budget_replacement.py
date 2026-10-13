"""감사된 레거시 예산 교체를 행 잠금 안에서 저장한다.

교체 규칙(대상·예산·감사 기록·억제 해제)은 `generation_attempt_state.replace_legacy_budget_and_release`
한 곳에 있다. 이 모듈은 Admin 버튼(비동기)과 일회성 CLI(동기)가 같은 사고 조회와 같은 저장
순서를 쓰게 하는 얇은 층이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.operations import Incident, IncidentState
from app.services.audit_log import write_audit_log_sync
from app.workers.generation_attempt_state import (
    LEGACY_SPEND_UNKNOWN_CODE,
    GenerationBudgetExceeded,
    replace_legacy_budget_and_release,
)
from app.workers.nightly_generation_batch import generation_claim_is_active

LEGACY_BUDGET_REPLACED_ACTION = "LEGACY_BUDGET_REPLACED"
ACTIVE_LEGACY_INCIDENT_STATES = (
    IncidentState.OPEN.value,
    IncidentState.RETRYING.value,
    IncidentState.ACKNOWLEDGED.value,
)
_REPLACEABLE_STATUSES = (ContentStatus.DRAFT, ContentStatus.READY)


def legacy_incident_open_stmt(content_id: uuid.UUID, hospital_id: uuid.UUID) -> Select[Any]:
    """이 글의 열린 `LEGACY_SPEND_UNKNOWN` 사고가 있는가(게이트가 시도 기록을 덮은 경우의 근거)."""

    return select(
        exists().where(
            Incident.source_id == str(content_id),
            Incident.hospital_id == hospital_id,
            Incident.safe_error_code == LEGACY_SPEND_UNKNOWN_CODE,
            Incident.state.in_(ACTIVE_LEGACY_INCIDENT_STATES),
        )
    )


@dataclass(frozen=True, slots=True)
class LegacyReplacementResult:
    content_id: str
    outcome: str  # REPLACED | SKIPPED
    detail: str | None = None


def apply_legacy_budget_replacement_sync(
    db: Session,
    content_id: uuid.UUID,
    *,
    actor: str,
    reason: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> LegacyReplacementResult:
    """한 글의 레거시 예산을 행 잠금 안에서 교체하고 감사 로그를 더한다. 커밋은 호출부가 한다."""

    item = db.execute(
        select(ContentItem).where(ContentItem.id == content_id).with_for_update()
    ).scalar_one_or_none()
    if item is None:
        return LegacyReplacementResult(str(content_id), "SKIPPED", "NOT_FOUND")
    if item.status not in _REPLACEABLE_STATUSES:
        return LegacyReplacementResult(str(content_id), "SKIPPED", f"STATUS_{item.status}")
    if generation_claim_is_active(item, now=now or datetime.now(UTC)):
        return LegacyReplacementResult(str(content_id), "SKIPPED", "GENERATION_LEASE_ACTIVE")
    incident_open = bool(db.scalar(legacy_incident_open_stmt(item.id, item.hospital_id)))
    try:
        summary, reset_record = replace_legacy_budget_and_release(
            item.essence_check_summary,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            legacy_incident_open=incident_open,
            replaced_at=now,
        )
    except GenerationBudgetExceeded as exc:
        return LegacyReplacementResult(str(content_id), "SKIPPED", str(exc))
    item.essence_check_summary = summary
    write_audit_log_sync(
        db,
        action=LEGACY_BUDGET_REPLACED_ACTION,
        hospital_id=item.hospital_id,
        actor=actor,
        target_type="content_item",
        target_id=item.id,
        detail=reset_record,
    )
    return LegacyReplacementResult(str(content_id), "REPLACED")
