from __future__ import annotations

import os
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.admin_user import AdminUser
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incidents import (
    IncidentFilters,
    IncidentFingerprint,
    IncidentOpenRequest,
    IncidentTransitionConflict,
    IncidentVersionConflict,
    acknowledge_incident,
    assign_incident,
    build_incident_key,
    incident_filter_expressions,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
    project_incident_labels,
    sanitize_operator_text,
)

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    url = os.getenv("INCIDENT_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    required = "INCIDENT_TEST_DATABASE_URL" in os.environ
    engine = create_async_engine(url, future=True)
    try:
        try:
            connection = await engine.connect()
        except OSError as exc:
            if required:
                pytest.fail(f"required incident PostgreSQL unavailable: {exc}", pytrace=False)
            pytest.skip("local incident PostgreSQL is unavailable")
        transaction = await connection.begin()
        operations_schema_ready = await connection.scalar(
            text(
                "SELECT to_regclass('public.incidents') IS NOT NULL "
                "AND to_regclass('public.notification_outbox') IS NOT NULL"
            )
        )
        if not operations_schema_ready:
            await transaction.rollback()
            await connection.close()
            if required:
                pytest.fail(
                    "incident PostgreSQL must include the operations control schema",
                    pytrace=False,
                )
            pytest.skip("local incident PostgreSQL lacks the operations control schema")
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
            await connection.close()
    finally:
        await engine.dispose()


async def _actors_and_hospital(
    db: AsyncSession,
) -> tuple[AdminUser, AdminUser, Hospital]:
    suffix = uuid.uuid4().hex
    first = AdminUser(
        email=f"task9-first-{suffix}@example.test",
        name="Task 9 First",
        role="OWNER",
        password_hash="not-a-real-hash",
        is_active=True,
    )
    second = AdminUser(
        email=f"task9-second-{suffix}@example.test",
        name="Task 9 Second",
        role="OPERATOR",
        password_hash="not-a-real-hash",
        is_active=True,
    )
    hospital = Hospital(name=f"Task 9 {suffix}", slug=f"task-9-{suffix}")
    db.add_all([first, second, hospital])
    await db.flush()
    return first, second, hospital


def _request(
    hospital: Hospital,
    *,
    severity: IncidentSeverity = IncidentSeverity.LOW,
    fingerprint: IncidentFingerprint = IncidentFingerprint.PROVIDER_TIMEOUT,
) -> IncidentOpenRequest:
    return IncidentOpenRequest(
        pipeline="content",
        object_type="hospital",
        object_id=str(hospital.id),
        fingerprint=fingerprint,
        incident_type="CONTENT_GENERATION_FAILED",
        severity=severity,
        customer_impact="오늘 콘텐츠 초안 생성이 지연됩니다.",
        source_type="CELERY_TASK",
        source_id=f"task:{hospital.id}",
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="provider timed out for patient@example.com Bearer secret-token",
        next_action="자동 재시도 결과를 확인하세요. 010-1234-5678",
        admin_path=f"/hospitals/{hospital.id}/content",
        hospital_id=hospital.id,
    )


def test_stable_key_collapses_unknown_tokens_to_closed_failure_class() -> None:
    # Given: timestamp/request-ID tokens that look syntactically like machine codes
    first_error = "timeout-20260810"
    second_error = "timeout-20260811"
    first_request = "request-a1234567-0000-0000-0000-000000000001"
    second_request = "request-b1234567-0000-0000-0000-000000000002"
    pii_error = "patient@example.com timed out at 2026-08-10T08:00:00Z"

    # When: callers build the incident identity twice
    first = build_incident_key("content", "hospital", "hospital-17", first_error)
    second = build_incident_key("content", "hospital", "hospital-17", second_error)
    request_one = build_incident_key("content", "hospital", "hospital-17", first_request)
    request_two = build_incident_key("content", "hospital", "hospital-17", second_request)
    pii_key = build_incident_key("content", "hospital", "hospital-17", pii_error)
    provider_timeout = build_incident_key(
        "content", "hospital", "hospital-17", IncidentFingerprint.PROVIDER_TIMEOUT
    )
    provider_rejected = build_incident_key(
        "content", "hospital", "hospital-17", IncidentFingerprint.PROVIDER_REJECTED
    )

    # Then: volatile raw exceptions collapse while controlled failure classes remain distinct
    assert first == second
    assert first == request_one == request_two == pii_key
    assert first.startswith("incident:v1:content:hospital:")
    assert "2026-08-10" not in first
    assert "patient@example.com" not in first
    assert provider_timeout != provider_rejected


def test_operator_text_masks_contact_and_credentials() -> None:
    # Given: a provider error containing contact and credential material
    raw = "patient@example.com 010-1234-5678 Bearer abc token=xyz"

    # When: it crosses the durable incident boundary
    safe = sanitize_operator_text(raw)

    # Then: no source secret or PII remains
    assert safe == "[email redacted] [phone redacted] Bearer [redacted] token=[redacted]"


@pytest.mark.asyncio
async def test_empty_operator_copy_gets_actionable_customer_and_support_fallback(
    db: AsyncSession,
) -> None:
    # Given
    _, _, hospital = await _actors_and_hospital(db)
    request = replace(_request(hospital), customer_impact="", next_action="")

    # When
    incident = await open_or_touch_incident(db, request)

    # Then
    assert incident.customer_impact == "고객 영향 정보를 아직 확인하지 못했습니다."
    assert "상세 화면" in incident.next_action
    assert "개발팀 문의용 정보" in incident.next_action


@pytest.mark.asyncio
async def test_duplicate_touch_escalates_and_preserves_first_occurrence(db: AsyncSession) -> None:
    # Given: one low-severity failure assigned to an owner and SLA
    owner, _, hospital = await _actors_and_hospital(db)
    first_seen = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    first = await open_or_touch_incident(db, _request(hospital), now=first_seen)
    assigned = await assign_incident(
        db,
        first.id,
        expected_version=first.version,
        owner_id=owner.id,
        sla_due_at=first_seen + timedelta(hours=2),
        actor="AE QA",
        reason="고객 발행 일정 확인",
        now=first_seen,
    )
    assert isinstance(assigned, Incident)

    # When: the same key occurs again with higher severity
    second = await open_or_touch_incident(
        db,
        _request(hospital, severity=IncidentSeverity.HIGH),
        now=first_seen + timedelta(minutes=10),
    )

    # Then: one row accumulates the occurrence and keeps ownership/first fact
    assert second.id == first.id
    assert second.occurrence_count == 2
    assert second.episode_seq == 1
    assert second.severity == IncidentSeverity.HIGH.value
    assert second.first_seen_at == first_seen
    assert second.last_seen_at == first_seen + timedelta(minutes=10)
    assert second.owner_id == owner.id
    assert second.sla_due_at == first_seen + timedelta(hours=2)
    assert "patient@example.com" not in (second.safe_error_message or "")
    assert "010-1234-5678" not in second.next_action
    assert second.admin_path == f"/hospitals/{hospital.id}/content"


@pytest.mark.asyncio
async def test_retry_recovery_ack_and_recurrence_reopen_with_audit(db: AsyncSession) -> None:
    # Given: one open incident and a responsible operator
    owner, _, hospital = await _actors_and_hospital(db)
    started = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    incident = await open_or_touch_incident(db, _request(hospital), actor="worker", now=started)

    # When: retry succeeds, the operator acknowledges it, and it later recurs
    retrying = await mark_retrying(
        db,
        incident.id,
        expected_version=incident.version,
        actor="worker",
        reason="자동 재시도 시작",
    )
    assert isinstance(retrying, Incident)
    recovered = await mark_recovered(
        db,
        incident.id,
        expected_version=retrying.version,
        observed_success=True,
        actor="worker",
        reason="후속 작업 성공 확인",
        now=started + timedelta(minutes=2),
    )
    assert isinstance(recovered, Incident)
    acknowledged = await acknowledge_incident(
        db,
        incident.id,
        expected_version=recovered.version,
        acknowledged_by_id=owner.id,
        actor="AE QA",
        reason="고객 영향 없음 확인",
        now=started + timedelta(minutes=3),
    )
    assert isinstance(acknowledged, Incident)
    assert acknowledged.state == IncidentState.ACKNOWLEDGED.value
    assert acknowledged.recovered_at == started + timedelta(minutes=2)
    reopened = await open_or_touch_incident(
        db, _request(hospital), actor="worker", now=started + timedelta(hours=1)
    )

    # Then: recovery facts are retained until a new occurrence explicitly reopens it
    assert reopened.state == IncidentState.OPEN.value
    assert reopened.occurrence_count == 2
    assert reopened.episode_seq == 2
    assert reopened.recovered_at is None
    assert reopened.acknowledged_at is None
    assert reopened.acknowledged_by_id is None
    logs = list(
        (
            await db.scalars(
                select(AdminAuditLog).where(AdminAuditLog.target_id == str(incident.id))
            )
        ).all()
    )
    assert Counter(log.action for log in logs) == Counter(
        {
            "incident_occurrence_recorded": 2,
            "incident_retrying": 1,
            "incident_recovered": 1,
            "incident_acknowledged": 1,
        }
    )
    assert all(log.actor in {"worker", "AE QA"} for log in logs)
    assert all(log.detail and log.detail["reason"] for log in logs)


@pytest.mark.asyncio
async def test_ack_open_and_unobserved_recovery_fail_closed(db: AsyncSession) -> None:
    # Given: one OPEN incident
    owner, _, hospital = await _actors_and_hospital(db)
    incident = await open_or_touch_incident(db, _request(hospital))

    # When: ACK is attempted before recovery and recovery lacks success evidence
    invalid_ack = await acknowledge_incident(
        db,
        incident.id,
        expected_version=incident.version,
        acknowledged_by_id=owner.id,
        actor="AE QA",
        reason="잘못된 확인",
    )
    unobserved = await mark_recovered(
        db,
        incident.id,
        expected_version=incident.version,
        observed_success=False,
        actor="worker",
        reason="성공 결과 없음",
    )

    # Then: mapping-ready conflicts expose no raw exception and mutate nothing
    assert invalid_ack == IncidentTransitionConflict(
        "INCIDENT_TRANSITION_CONFLICT",
        incident.id,
        incident.version,
        IncidentState.OPEN.value,
        IncidentState.RECOVERED.value,
    )
    assert isinstance(unobserved, IncidentTransitionConflict)
    assert unobserved.code == "INCIDENT_RECOVERY_NOT_OBSERVED"
    current = await db.get(Incident, incident.id)
    assert current is not None
    assert current.state == IncidentState.OPEN.value
    assert current.version == incident.version


@pytest.mark.asyncio
async def test_unobserved_recovery_still_honors_version_cas(db: AsyncSession) -> None:
    # Given: an incident version made stale by an owner assignment
    owner, _, hospital = await _actors_and_hospital(db)
    incident = await open_or_touch_incident(db, _request(hospital))
    stale_version = incident.version
    assigned = await assign_incident(
        db,
        incident.id,
        expected_version=stale_version,
        owner_id=owner.id,
        sla_due_at=None,
        actor="AE QA",
        reason="담당자 지정",
    )
    assert isinstance(assigned, Incident)

    # When: an old caller reports that recovery was not observed
    result = await mark_recovered(
        db,
        incident.id,
        expected_version=stale_version,
        observed_success=False,
        actor="worker",
        reason="성공 결과 없음",
    )

    # Then: stale ownership wins conflict precedence for deterministic HTTP 409 mapping
    assert result == IncidentVersionConflict(
        "INCIDENT_VERSION_CONFLICT",
        incident.id,
        stale_version,
        assigned.version,
        IncidentState.OPEN.value,
    )


@pytest.mark.asyncio
async def test_stale_assignment_loses_without_changing_owner_or_state(db: AsyncSession) -> None:
    # Given: two operators hold the same incident version
    first_owner, second_owner, hospital = await _actors_and_hospital(db)
    incident = await open_or_touch_incident(db, _request(hospital))
    stale_version = incident.version

    # When: the first assignment wins and the stale assignment follows
    winner = await assign_incident(
        db,
        incident.id,
        expected_version=stale_version,
        owner_id=first_owner.id,
        sla_due_at=datetime(2026, 8, 10, 18, 0, tzinfo=UTC),
        actor="AE First",
        reason="담당 배정",
    )
    loser = await assign_incident(
        db,
        incident.id,
        expected_version=stale_version,
        owner_id=second_owner.id,
        sla_due_at=datetime(2026, 8, 10, 19, 0, tzinfo=UTC),
        actor="AE Second",
        reason="오래된 화면에서 배정",
    )

    # Then: Task 11 can map the stale result to 409 and the first facts remain
    assert isinstance(winner, Incident)
    assert winner.state == IncidentState.OPEN.value
    assert loser == IncidentVersionConflict(
        "INCIDENT_VERSION_CONFLICT",
        incident.id,
        stale_version,
        winner.version,
        IncidentState.OPEN.value,
    )
    current = await db.scalar(select(Incident).where(Incident.id == incident.id))
    assert current is not None
    assert current.owner_id == first_owner.id
    assert current.sla_due_at == datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    assert current.state == IncidentState.OPEN.value


@pytest.mark.asyncio
async def test_stale_ack_returns_version_conflict_and_retains_recovery(db: AsyncSession) -> None:
    # Given: a recovered incident whose version changes after the screen loaded
    owner, _, hospital = await _actors_and_hospital(db)
    incident = await open_or_touch_incident(db, _request(hospital))
    retrying = await mark_retrying(
        db,
        incident.id,
        expected_version=incident.version,
        actor="worker",
        reason="재시도",
    )
    assert isinstance(retrying, Incident)
    recovered = await mark_recovered(
        db,
        incident.id,
        expected_version=retrying.version,
        observed_success=True,
        actor="worker",
        reason="성공 확인",
    )
    assert isinstance(recovered, Incident)
    stale_version = recovered.version
    assigned = await assign_incident(
        db,
        incident.id,
        expected_version=stale_version,
        owner_id=owner.id,
        sla_due_at=None,
        actor="AE QA",
        reason="복구 확인 담당",
    )
    assert isinstance(assigned, Incident)

    # When: the old screen acknowledges with its stale version
    result = await acknowledge_incident(
        db,
        incident.id,
        expected_version=stale_version,
        acknowledged_by_id=owner.id,
        actor="AE QA",
        reason="오래된 화면 확인",
    )

    # Then: the API adapter can return 409 and the recovery remains unacknowledged
    assert result == IncidentVersionConflict(
        "INCIDENT_VERSION_CONFLICT",
        incident.id,
        stale_version,
        assigned.version,
        IncidentState.RECOVERED.value,
    )
    current = await db.scalar(select(Incident).where(Incident.id == incident.id))
    assert current is not None
    assert current.state == IncidentState.RECOVERED.value
    assert current.acknowledged_at is None


@pytest.mark.asyncio
async def test_filters_and_labels_are_operator_safe_and_deterministic(db: AsyncSession) -> None:
    # Given: an overdue, assigned HIGH incident
    owner, _, hospital = await _actors_and_hospital(db)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    incident = await open_or_touch_incident(
        db, _request(hospital, severity=IncidentSeverity.HIGH), now=now
    )
    assigned = await assign_incident(
        db,
        incident.id,
        expected_version=incident.version,
        owner_id=owner.id,
        sla_due_at=now - timedelta(minutes=1),
        actor="AE QA",
        reason="SLA 배정",
        now=now,
    )
    assert isinstance(assigned, Incident)

    # When: the operations center builds filters and the common row projection
    filters = IncidentFilters(
        states=(IncidentState.OPEN,),
        severities=(IncidentSeverity.HIGH,),
        owner_id=owner.id,
        overdue_only=True,
    )
    expressions = incident_filter_expressions(filters, now=now)
    matched = await db.scalar(select(Incident).where(*expressions))
    labels = project_incident_labels(assigned, now=now)

    # Then: owner/SLA filters match and labels disclose no owner PII or unsafe path query
    assert matched is not None and matched.id == incident.id
    assert labels.state_label == "조치 필요"
    assert labels.severity_label == "높음"
    assert labels.ownership_label == "담당자 배정됨"
    assert labels.sla_label == "기한 초과"
    assert labels.requires_operator_action is True
    assert "patient@example.com" not in labels.next_action
    assert labels.admin_path == f"/hospitals/{hospital.id}/content"


@pytest.mark.asyncio
async def test_admin_deep_links_allow_ui_routes_and_reject_unsafe_paths(db: AsyncSession) -> None:
    # Given: a legitimate operations-center link and several server/external path attempts
    _, _, hospital = await _actors_and_hospital(db)
    operations_request = _request(hospital)
    operations_request = replace(
        operations_request,
        fingerprint=IncidentFingerprint.CONFIGURATION_ERROR,
        admin_path="/operations",
    )

    # When: links cross the incident boundary
    operations = await open_or_touch_incident(db, operations_request)
    malicious_paths = ("/api/admin/incidents", "//evil.test/x", "/hospitals/../leads", "/operations?token=secret")
    normalized: list[str] = []
    for index, path in enumerate(malicious_paths):
        request = replace(
            operations_request,
            object_id=f"{hospital.id}-{index}",
            admin_path=path,
        )
        normalized.append((await open_or_touch_incident(db, request)).admin_path)

    # Then: real browser routes survive and unsafe/BFF paths fail to the real UI queue
    assert operations.admin_path == "/operations"
    assert normalized == ["/operations"] * len(malicious_paths)
