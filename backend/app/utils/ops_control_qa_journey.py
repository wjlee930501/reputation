"""Operations records and safe Slack fixtures for the marketer QA journey."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final, TypeAlias, TypedDict, cast

from sqlalchemy import null, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.services.content_publish_notifications import build_publish_notification_intent
from app.services.notification_contracts import IncidentSlackProjection, validate_message
from app.services.notification_messages import build_open_incident_notification
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.utils.ops_control_qa_records import (
    ensure_content,
    ensure_handoff,
    ensure_hospital,
    ensure_lead_diagnosis,
    ensure_report,
    ensure_source,
)

_ADMIN_BASE_URL: Final = "http://localhost:3000"

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
SlackFixture: TypeAlias = dict[str, JsonValue]


class JourneySeedResult(TypedDict):
    hospital_ids: list[uuid.UUID]
    handoff_ids: list[uuid.UUID]
    schedule_ids: list[uuid.UUID]
    content_ids: list[uuid.UUID]
    report_ids: list[uuid.UUID]
    source_asset_ids: list[uuid.UUID]
    lead_diagnosis_ids: list[uuid.UUID]
    operation_run_ids: list[uuid.UUID]
    incident_ids: list[uuid.UUID]
    outbox_ids: list[uuid.UUID]
    slack_fixtures: list[SlackFixture]


def _one(db: Session, model: type, **filters):
    return db.execute(select(model).filter_by(**filters)).scalar_one_or_none()


def _ensure_run(db: Session, hospital: Hospital, content: ContentItem) -> OperationRun:
    key = "ops-qa-20260810:regenerate-content-image"
    run = _one(db, OperationRun, idempotency_key=key)
    if run is None:
        run = OperationRun(
            hospital_id=hospital.id,
            operation_type="REGENERATE_CONTENT_IMAGE",
            idempotency_key=key,
            state="FAILED",
            total_count=1,
            failure_count=1,
            attempt_count=1,
            safe_error_code="IMAGE_PROVIDER_UNAVAILABLE",
            safe_error_message="대표 이미지 생성 연결이 잠시 중단되었습니다.",
            request_payload=build_request_payload(
                DispatchPayload("content_item", str(content.id), "content", (str(content.id),))
            ),
            completed_at=datetime(2026, 8, 10, 2, 0, tzinfo=UTC),
        )
        db.add(run)
        db.flush()
    return run


def _ensure_incident_and_outbox(
    db: Session, hospital: Hospital, run: OperationRun, ae_id: uuid.UUID
) -> tuple[Incident, NotificationOutbox, SlackFixture]:
    incident = _one(db, Incident, dedupe_key="OPS-QA-20260810:content-image")
    if incident is None:
        incident = Incident(
            hospital_id=hospital.id,
            operation_run_id=run.id,
            dedupe_key="OPS-QA-20260810:content-image",
            incident_type="BACKGROUND_TASK_FAILED",
            state="OPEN",
            severity="HIGH",
            customer_impact="오늘 공개할 글의 대표 이미지가 준비되지 않았습니다.",
            owner_id=ae_id,
            sla_due_at=datetime.now(UTC) + timedelta(hours=4),
            source_type="CONTENT_ITEM",
            safe_error_code="IMAGE_PROVIDER_UNAVAILABLE",
            safe_error_message="대표 이미지 생성 연결이 잠시 중단되었습니다.",
            next_action="운영 센터에서 ‘작업 다시 시도’를 누르세요.",
            admin_path=f"/operations?queue=incidents&hospital_id={hospital.id}",
        )
        db.add(incident)
        db.flush()
    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital.name,
        severity="높음",
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="이수진",
        sla_label="오늘 안에",
        hospital_id=hospital.id,
        operation_run_id=run.id,
        version=incident.version,
        problem="대표 이미지 생성이 완료되지 않았습니다.",
        episode_seq=incident.episode_seq,
    )
    intent = build_open_incident_notification(projection, _ADMIN_BASE_URL)
    validate_message(intent.message, allowed_admin_base_url=_ADMIN_BASE_URL)
    outbox = _one(db, NotificationOutbox, dedupe_key=intent.dedupe_key)
    if outbox is None:
        outbox = NotificationOutbox(
            hospital_id=hospital.id,
            incident_id=incident.id,
            operation_run_id=run.id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel="SLACK",
            state="FAILED",
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            attempt_count=3,
            max_attempts=3,
            next_attempt_at=null(),
            safe_error_code="WEBHOOK_UNAVAILABLE",
            safe_error_message="Slack 전달 연결이 응답하지 않았습니다.",
        )
        db.add(outbox)
        db.flush()
    return incident, outbox, cast(SlackFixture, intent.message.payload())


def _ensure_publish_outbox(
    db: Session, hospital: Hospital, content: ContentItem, ae_id: uuid.UUID
) -> tuple[Incident, NotificationOutbox, SlackFixture]:
    incident = _one(db, Incident, dedupe_key="OPS-QA-20260810:publish-notification")
    if incident is None:
        incident = Incident(
            hospital_id=hospital.id,
            dedupe_key="OPS-QA-20260810:publish-notification",
            incident_type="PUBLISH_NOTIFICATION_FAILED",
            state="OPEN",
            severity="MEDIUM",
            customer_impact="콘텐츠는 공개됐지만 담당자가 공개 확인 알림을 받지 못했습니다.",
            owner_id=ae_id,
            sla_due_at=datetime.now(UTC) + timedelta(hours=4),
            source_type="CONTENT_ITEM",
            safe_error_code="PUBLISH_NOTIFICATION_FAILED",
            safe_error_message="콘텐츠 공개 확인 알림 전달이 중단되었습니다.",
            next_action="운영 센터에서 ‘Slack 다시 보내기’를 누르세요.",
            admin_path=f"/hospitals/{hospital.id}/content?content={content.id}",
        )
        db.add(incident)
        db.flush()
    intent = build_publish_notification_intent(content, hospital)
    validate_message(intent.message, allowed_admin_base_url=_ADMIN_BASE_URL)
    outbox = _one(db, NotificationOutbox, dedupe_key=intent.dedupe_key)
    if outbox is None:
        outbox = NotificationOutbox(
            hospital_id=hospital.id,
            incident_id=incident.id,
            dedupe_key=intent.dedupe_key,
            notification_type=intent.notification_type,
            channel="SLACK",
            state="FAILED",
            payload=intent.message.payload(),
            fallback_text=intent.message.fallback_text,
            attempt_count=1,
            max_attempts=1,
            next_attempt_at=null(),
            safe_error_code="WEBHOOK_UNAVAILABLE",
            safe_error_message="Slack 전달 연결이 응답하지 않았습니다.",
        )
        db.add(outbox)
        db.flush()
    else:
        outbox.incident_id = incident.id
    return incident, outbox, cast(SlackFixture, intent.message.payload())


def ensure_complete_journey(
    db: Session,
    lead_id: uuid.UUID,
    owner_id: uuid.UUID,
    sales_id: uuid.UUID,
    ae_id: uuid.UUID,
) -> JourneySeedResult:
    hospital = ensure_hospital(db, lead_id)
    handoff = ensure_handoff(db, hospital, owner_id, sales_id, ae_id)
    schedule, content = ensure_content(db, hospital)
    report = ensure_report(db, hospital)
    source = ensure_source(db, hospital)
    diagnosis = ensure_lead_diagnosis(db, lead_id)
    run = _ensure_run(db, hospital, content)
    incident, outbox, slack_payload = _ensure_incident_and_outbox(db, hospital, run, ae_id)
    publish_incident, publish_outbox, publish_slack_payload = _ensure_publish_outbox(
        db, hospital, content, ae_id
    )
    return {
        "hospital_ids": [hospital.id],
        "handoff_ids": [handoff.id],
        "schedule_ids": [schedule.id],
        "content_ids": [content.id],
        "report_ids": [report.id],
        "source_asset_ids": [source.id],
        "lead_diagnosis_ids": [diagnosis.id],
        "operation_run_ids": [run.id],
        "incident_ids": [incident.id, publish_incident.id],
        "outbox_ids": [outbox.id, publish_outbox.id],
        "slack_fixtures": [slack_payload, publish_slack_payload],
    }
