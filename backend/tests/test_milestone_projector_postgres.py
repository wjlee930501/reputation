from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models.admin_user import AdminUser
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
)
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.models.report import MonthlyReport
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.notification_outbox import dispatch_notification_batch
from app.services.report_artifact_validation import DOCTOR_ARTIFACT_VALIDATION_VERSION
from app.workers.milestone_event_tasks import (
    canonical_projection_window,
    project_milestone_window,
)
from app.workers.milestone_monthly_projection import observe_monthly_milestones
from tests.milestone_projector_support import (
    ADMIN_EMAIL,
    ADMIN_ID,
    NAME,
    SLUG,
)
from tests.milestone_projector_support import (
    monthly_session_factory as _monthly_sessions_fixture,  # noqa: F401
)


def _valid_artifact_metadata(*, sha256: str, byte_size: int) -> dict[str, object]:
    return {
        "validation_version": DOCTOR_ARTIFACT_VALIDATION_VERSION,
        "validation_source": "SYSTEM",
        "page_count": 1,
        "page_size": "A4",
        "glyph_count": 500,
        "font_family": "Pretendard",
        "font_embedded": True,
        "korean_to_unicode": True,
        "link_count": 1,
        "expected_link_present": True,
        "required_text_present": True,
        "sha256": sha256,
        "byte_size": byte_size,
    }


@pytest.mark.asyncio
async def test_durable_cursor_catches_late_readiness_and_slack_failure_preserves_truth(
    monthly_sessions,
) -> None:
    # Given: COMPLETE coverage starts with an unvalidated doctor artifact
    first_window = canonical_projection_window(datetime(2026, 8, 10, 2, 47, tzinfo=UTC))
    later_window = canonical_projection_window(datetime(2026, 8, 10, 3, 17, tzinfo=UTC))
    hospital_id = uuid.UUID("b1340000-0000-0000-0000-000000000001")
    report_id = uuid.UUID("c1340000-0000-0000-0000-000000000001")
    async with monthly_sessions() as db:
        hospital = Hospital(id=hospital_id, name=NAME, slug=SLUG)
        admin = AdminUser(
            id=ADMIN_ID,
            email=ADMIN_EMAIL,
            name="Task 13 Validator",
            role="OPERATOR",
            password_hash="not-a-login-credential",
        )
        source = HospitalSourceAsset(
            id=uuid.UUID("e1340000-0000-0000-0000-000000000001"),
            hospital_id=hospital_id,
            source_type=SourceType.INTERVIEW,
            title="QA source",
            raw_text="병원 진료 철학",
            content_hash="b" * 64,
            status=SourceStatus.PROCESSED,
            processed_at=first_window.start,
        )
        philosophy = HospitalContentPhilosophy(
            hospital_id=hospital_id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            source_snapshot_hash=compute_sources_snapshot_hash([source]),
            approved_at=first_window.start,
        )
        manifest = MonthlyMeasurementManifest(
            id=uuid.UUID("f1340000-0000-0000-0000-000000000001"),
            hospital_id=hospital_id,
            period_year=2026,
            period_month=7,
            configured_platforms=["chatgpt", "gemini"],
            platform_provenance={"source": "qa"},
            closes_at=first_window.start,
            closed_at=first_window.start,
        )
        report = MonthlyReport(
            id=report_id,
            hospital_id=hospital_id,
            period_year=2026,
            period_month=7,
            report_type="MONTHLY",
            manifest_id=manifest.id,
            cutoff_at=first_window.start,
            quality="COMPLETE",
            planned_count=20,
            success_count=20,
            failed_count=0,
            excluded_count=0,
            pdf_path="gs://qa-private/ae.pdf",
            doctor_pdf_path="gs://qa-private/doctor.pdf",
            sov_summary={"sov_pct": 20.0},
            content_summary={"published_count": 20},
            essence_summary={
                "approved_philosophy_exists": True,
                "source_stale": False,
                "source_count": 1,
                "processed_source_count": 1,
                "needs_review_content_count": 0,
                "missing_philosophy_content_count": 0,
                "medical_risk_findings": [],
                "philosophy_version": 1,
            },
            delivery_blockers=[],
            created_at=first_window.start + timedelta(minutes=2),
        )
        artifact = MonthlyReportArtifact(
            id=uuid.UUID("d1340000-0000-0000-0000-000000000001"),
            report_id=report_id,
            audience="DOCTOR",
            path=report.doctor_pdf_path,
            sha256="a" * 64,
            byte_size=1024,
            validated=False,
            validated_at=None,
            validated_by_id=None,
            validation_metadata=None,
        )
        db.add_all((admin, hospital))
        await db.flush()
        db.add_all((source, philosophy, manifest))
        await db.flush()
        db.add(report)
        await db.flush()
        db.add(artifact)
        await db.commit()

    # And: the first durable snapshot records artifact-pending truth
    async with monthly_sessions() as db:
        first = await project_milestone_window(db, first_window, "http://localhost:3000")
        await db.commit()
        assert (first.monthly_count, first.enqueued) == (1, True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="ok"))
    ) as client:
        sent = await dispatch_notification_batch(
            monthly_sessions,
            client,
            webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id="task13-first",
            now=datetime.now(UTC) + timedelta(minutes=1),
            throttle=lambda: _no_pause(),
        )
    assert sent.sent == 1

    # When: validation occurs in a missed interval and a later window runs
    async with monthly_sessions() as db:
        artifact = await db.get(
            MonthlyReportArtifact,
            uuid.UUID("d1340000-0000-0000-0000-000000000001"),
        )
        assert artifact is not None
        artifact.validated = True
        artifact.validated_at = first_window.end + timedelta(minutes=5)
        artifact.validated_by_id = ADMIN_ID
        artifact.validation_metadata = _valid_artifact_metadata(
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
        )
        await db.commit()
    async with monthly_sessions() as db:
        later = await project_milestone_window(db, later_window, "http://localhost:3000")
        replay = await project_milestone_window(db, later_window, "http://localhost:3000")
        ready_outbox = await db.scalar(
            select(NotificationOutbox)
            .where(NotificationOutbox.state == NotificationOutboxState.PENDING.value)
            .order_by(NotificationOutbox.created_at.desc())
        )
        assert ready_outbox is not None
        ready_outbox.max_attempts = 1
        await db.commit()
        assert (later.monthly_count, later.enqueued) == (1, True)
        assert (replay.monthly_count, replay.enqueued) == (0, False)

    # And: Slack exhausts after the durable CUSTOMER_READY projection
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, text="secret"))
    ) as client:
        result = await dispatch_notification_batch(
            monthly_sessions,
            client,
            webhook_url="https://hooks.slack.com/services/T/B/X",
            worker_id="task13-qa",
            now=datetime.now(UTC) + timedelta(minutes=2),
            throttle=lambda: _no_pause(),
        )

    # Then: cursor caught the late transition and transport failure did not falsify readiness
    assert result.failed == 1
    async with monthly_sessions() as db:
        current = await observe_monthly_milestones(
            db,
            later_window.end + timedelta(minutes=15),
            {},
            later_window.end,
        )
        outbox = await db.scalar(
            select(NotificationOutbox)
            .where(NotificationOutbox.hospital_id == hospital_id)
            .order_by(NotificationOutbox.created_at.desc())
        )
        incident = await db.scalar(select(Incident).where(Incident.hospital_id == hospital_id))
        report = await db.get(MonthlyReport, report_id)
        assert [
            item.kind.value for item in current.milestones if item.hospital_id == hospital_id
        ] == ["MONTHLY_CUSTOMER_READY"]
        assert outbox is not None and outbox.state == NotificationOutboxState.FAILED
        assert incident is not None and incident.state == IncidentState.OPEN
        assert report is not None and (report.quality, report.success_count) == ("COMPLETE", 20)


async def _no_pause() -> None:
    return None
