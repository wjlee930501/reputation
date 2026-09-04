"""Exactly-once recovery projection for validated monthly doctor PDFs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentState
from app.services.dependency_incident_helpers import open_notice_exists
from app.services.incident_types import (
    IncidentNotFound,
    IncidentTransitionConflict,
    IncidentVersionConflict,
)
from app.services.incidents import mark_recovered, mark_retrying
from app.services.notification_messages import build_recovered_incident_notification
from app.services.notification_store import enqueue_notification
from app.workers.monthly_artifact_incident_contracts import (
    MonthlyArtifactIncidentContext,
    incident_projection,
)

_SOURCE_TYPE = "MONTHLY_REPORT_ARTIFACT"


async def recover_monthly_artifact_failures(
    context: MonthlyArtifactIncidentContext,
) -> int:
    return await recover_monthly_artifact_failure_batch((context,))


async def recover_monthly_artifact_failure_batch(
    contexts: Sequence[MonthlyArtifactIncidentContext],
) -> int:
    sessions = get_async_sessionmaker()
    recovered = 0
    async with sessions() as db:
        for context in contexts:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{_SOURCE_TYPE}:{context.period_key}"},
            )
            incidents = list(
                (
                    await db.execute(
                        select(Incident).where(
                            Incident.hospital_id == context.hospital_id,
                            Incident.source_type == _SOURCE_TYPE,
                            Incident.source_id == context.period_key,
                            Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                        )
                    )
                ).scalars()
            )
            for incident in incidents:
                current = incident
                if current.state == IncidentState.OPEN:
                    retrying = await mark_retrying(
                        db,
                        current.id,
                        expected_version=current.version,
                        actor="monthly-report-worker",
                        reason="원장 전달용 PDF 다시 만들기 시작",
                    )
                    match retrying:
                        case Incident() as changed:
                            current = changed
                        case IncidentNotFound() | IncidentVersionConflict() | IncidentTransitionConflict():
                            continue
                        case unreachable:
                            assert_never(unreachable)
                result = await mark_recovered(
                    db,
                    current.id,
                    expected_version=current.version,
                    observed_success=True,
                    actor="monthly-report-worker",
                    reason="원장 전달용 PDF 검증과 저장 완료",
                )
                match result:
                    case Incident() as recovered_incident:
                        if await open_notice_exists(db, recovered_incident.id):
                            await enqueue_notification(
                                db,
                                build_recovered_incident_notification(
                                    incident_projection(
                                        context, recovered_incident, problem="복구 완료"
                                    ),
                                    settings.ADMIN_BASE_URL,
                                ),
                            )
                        recovered += 1
                    case IncidentNotFound() | IncidentVersionConflict() | IncidentTransitionConflict():
                        continue
                    case unreachable:
                        assert_never(unreachable)
        await db.commit()
    return recovered
