"""Fail-closed verification and dependency-ordered cleanup for owned QA rows."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Protocol, TypedDict

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.content import ContentItem, ContentSchedule
from app.models.essence import HospitalSourceAsset
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDiagnosis
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.models.report import MonthlyReport


class CleanupTargets(Protocol):
    admin_user_ids: Sequence[uuid.UUID]
    lead_ids: Sequence[uuid.UUID]
    hospital_ids: Sequence[uuid.UUID]
    handoff_ids: Sequence[uuid.UUID]
    schedule_ids: Sequence[uuid.UUID]
    content_ids: Sequence[uuid.UUID]
    report_ids: Sequence[uuid.UUID]
    source_asset_ids: Sequence[uuid.UUID]
    lead_diagnosis_ids: Sequence[uuid.UUID]
    operation_run_ids: Sequence[uuid.UUID]
    incident_ids: Sequence[uuid.UUID]
    outbox_ids: Sequence[uuid.UUID]


class UnsafeCleanupTarget(RuntimeError):
    """A recorded identifier no longer resolves to the owned QA namespace."""


class CleanupCounts(TypedDict):
    admin_users: int
    leads: int
    hospitals: int
    handoffs: int
    schedules: int
    contents: int
    reports: int
    source_assets: int
    lead_diagnoses: int
    operation_runs: int
    incidents: int
    notification_outbox: int


def verify_cleanup_targets(
    db: Session,
    manifest: CleanupTargets,
    *,
    source_marker: str,
    admin_identities: Mapping[str, str],
) -> None:
    lead_ids = set(manifest.lead_ids)
    for record_id in manifest.lead_ids:
        lead = db.get(SalesLead, record_id)
        if (
            lead is None
            or lead.source_path != "/ops-qa"
            or lead.consent_version != "ops-qa-v1"
            or lead.conversion_note != source_marker
        ):
            raise UnsafeCleanupTarget(f"lead {record_id} is outside the QA fixture")

    for record_id in manifest.hospital_ids:
        hospital = db.get(Hospital, record_id)
        source_owned = hospital is not None and hospital.source_lead_id in lead_ids
        if hospital is None or not source_owned:
            raise UnsafeCleanupTarget(f"hospital {record_id} is outside the QA fixture")

    for record_id in manifest.admin_user_ids:
        account = db.get(AdminUser, record_id)
        expected_name = admin_identities.get(account.email) if account is not None else None
        if account is None or expected_name is None or account.name != expected_name:
            raise UnsafeCleanupTarget(f"admin account {record_id} is outside the QA fixture")

    hospital_ids = set(manifest.hospital_ids)
    hospital_groups = (
        (HospitalHandoff, manifest.handoff_ids),
        (ContentSchedule, manifest.schedule_ids),
        (ContentItem, manifest.content_ids),
        (MonthlyReport, manifest.report_ids),
        (HospitalSourceAsset, manifest.source_asset_ids),
        (OperationRun, manifest.operation_run_ids),
        (Incident, manifest.incident_ids),
        (NotificationOutbox, manifest.outbox_ids),
    )
    for model, record_ids in hospital_groups:
        for record_id in record_ids:
            record = db.get(model, record_id)
            if record is None or record.hospital_id not in hospital_ids:
                raise UnsafeCleanupTarget(
                    f"{model.__tablename__} {record_id} is outside the QA hospital"
                )
    for record_id in manifest.lead_diagnosis_ids:
        diagnosis = db.get(LeadDiagnosis, record_id)
        if diagnosis is None or diagnosis.lead_id not in lead_ids:
            raise UnsafeCleanupTarget(f"lead diagnosis {record_id} is outside the QA lead")


def delete_cleanup_targets(db: Session, manifest: CleanupTargets) -> None:
    """Delete only verified rows, with SET NULL dependencies removed first."""
    if manifest.outbox_ids:
        db.execute(delete(NotificationOutbox).where(NotificationOutbox.id.in_(manifest.outbox_ids)))
    if manifest.incident_ids:
        db.execute(delete(Incident).where(Incident.id.in_(manifest.incident_ids)))
    if manifest.operation_run_ids:
        db.execute(delete(OperationRun).where(OperationRun.id.in_(manifest.operation_run_ids)))
    if manifest.hospital_ids:
        db.execute(delete(Hospital).where(Hospital.id.in_(manifest.hospital_ids)))
    if manifest.lead_ids:
        db.execute(delete(SalesLead).where(SalesLead.id.in_(manifest.lead_ids)))
    if manifest.admin_user_ids:
        db.execute(delete(AdminUser).where(AdminUser.id.in_(manifest.admin_user_ids)))


def count_remaining_targets(db: Session, manifest: CleanupTargets) -> CleanupCounts:
    """Re-query every recorded identifier after commit; never infer cleanup from DELETE calls."""

    def remaining(model: type, record_ids: Sequence[uuid.UUID]) -> int:
        return sum(db.get(model, record_id) is not None for record_id in record_ids)

    return {
        "admin_users": remaining(AdminUser, manifest.admin_user_ids),
        "leads": remaining(SalesLead, manifest.lead_ids),
        "hospitals": remaining(Hospital, manifest.hospital_ids),
        "handoffs": remaining(HospitalHandoff, manifest.handoff_ids),
        "schedules": remaining(ContentSchedule, manifest.schedule_ids),
        "contents": remaining(ContentItem, manifest.content_ids),
        "reports": remaining(MonthlyReport, manifest.report_ids),
        "source_assets": remaining(HospitalSourceAsset, manifest.source_asset_ids),
        "lead_diagnoses": remaining(LeadDiagnosis, manifest.lead_diagnosis_ids),
        "operation_runs": remaining(OperationRun, manifest.operation_run_ids),
        "incidents": remaining(Incident, manifest.incident_ids),
        "notification_outbox": remaining(NotificationOutbox, manifest.outbox_ids),
    }
