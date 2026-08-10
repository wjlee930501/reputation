"""Deterministic customer records for the complete marketer QA journey."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.lead_diagnosis import DeliveryStatus, ExecutionStatus, LeadDiagnosis, ReportStatus
from app.models.report import MonthlyReport

QA_HOSPITAL_NAME: Final = "장편한외과의원"
QA_HOSPITAL_SLUG: Final = "jangpyeonhan-surgery-20260810"
QA_CONTENT_TITLE: Final = "대장내시경 검사 전 준비사항 안내"
QA_SOURCE_TITLE: Final = "병원 블로그 진료 안내"


def _one(db: Session, model: type, **filters):
    return db.execute(select(model).filter_by(**filters)).scalar_one_or_none()


def ensure_hospital(db: Session, lead_id: uuid.UUID) -> Hospital:
    hospital = _one(db, Hospital, slug=QA_HOSPITAL_SLUG)
    if hospital is None:
        hospital = Hospital(name=QA_HOSPITAL_NAME, slug=QA_HOSPITAL_SLUG)
        db.add(hospital)
    hospital.source_lead_id = lead_id
    hospital.onboarding_note = "공개 준비 상태를 확인하고 있습니다."
    hospital.status = HospitalStatus.PENDING_DOMAIN
    hospital.plan = Plan.PLAN_12
    hospital.profile_complete = True
    hospital.v0_report_done = True
    hospital.site_built = True
    hospital.schedule_set = True
    hospital.site_live = False
    hospital.address = "서울특별시 강남구 테헤란로 100"
    hospital.phone = "02-555-1234"
    hospital.region = ["서울 강남"]
    hospital.specialties = ["외과"]
    hospital.keywords = ["대장내시경"]
    hospital.competitors = ["강남튼튼외과의원"]
    hospital.director_name = "김도현 원장"
    hospital.director_career = "외과 전문의"
    hospital.director_philosophy = "환자가 이해할 수 있는 설명을 우선합니다."
    hospital.treatments = [{"name": "대장내시경", "description": "검사 안내"}]
    db.flush()
    return hospital


def ensure_handoff(
    db: Session, hospital: Hospital, owner_id: uuid.UUID, sales_id: uuid.UUID, ae_id: uuid.UUID
) -> HospitalHandoff:
    handoff = _one(db, HospitalHandoff, hospital_id=hospital.id)
    if handoff is None:
        handoff = HospitalHandoff(hospital_id=hospital.id)
        db.add(handoff)
    handoff.state = HandoffState.HANDOFF_ACCEPTED
    handoff.acceptance_source = HandoffSource.LEAD_CONVERSION
    handoff.sales_owner_id = sales_id
    handoff.ae_owner_id = ae_id
    handoff.contract_reference = "계약-2026-0810"
    handoff.contract_effective_at = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    handoff.plan = Plan.PLAN_12
    handoff.sla_due_at = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    handoff.accepted_by_id = owner_id
    handoff.accepted_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    db.flush()
    return handoff


def ensure_content(db: Session, hospital: Hospital) -> tuple[ContentSchedule, ContentItem]:
    schedule = _one(db, ContentSchedule, hospital_id=hospital.id, active_from=date(2026, 8, 1))
    if schedule is None:
        schedule = ContentSchedule(
            hospital_id=hospital.id,
            plan="PLAN_12",
            publish_days=[1, 4],
            active_from=date(2026, 8, 1),
            is_active=True,
        )
        db.add(schedule)
        db.flush()
    content = _one(db, ContentItem, hospital_id=hospital.id, title=QA_CONTENT_TITLE)
    if content is None:
        content = ContentItem(
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=ContentType.HEALTH,
            sequence_no=1,
            total_count=12,
            scheduled_date=date(2026, 8, 10),
            title=QA_CONTENT_TITLE,
        )
        db.add(content)
    content.status = ContentStatus.PUBLISHED
    content.body = "환자가 이해하기 쉬운 검사 준비 안내입니다."
    content.meta_description = "검사 전 준비 사항을 확인하세요."
    content.references_list = [{"title": "병원 공식 안내", "url": "https://example.test"}]
    content.published_at = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    content.published_by = "이수진"
    content.post_publish_notified_at = None
    db.flush()
    return schedule, content


def ensure_report(db: Session, hospital: Hospital) -> MonthlyReport:
    report = _one(
        db,
        MonthlyReport,
        hospital_id=hospital.id,
        period_year=2026,
        period_month=7,
        report_type="MONTHLY",
        version=1,
    )
    if report is None:
        report = MonthlyReport(
            hospital_id=hospital.id,
            period_year=2026,
            period_month=7,
            report_type="MONTHLY",
            version=1,
        )
        db.add(report)
    report.quality = "DEGRADED"
    report.planned_count = 2
    report.success_count = 1
    report.failed_count = 1
    report.customer_ready = False
    report.delivery_blockers = ["질문별 측정 1건을 완료해야 합니다."]
    report.sov_summary = {"sov_pct": 50.0, "cells": []}
    report.content_summary = {"published": 1}
    db.flush()
    return report


def ensure_source(db: Session, hospital: Hospital) -> HospitalSourceAsset:
    source = _one(db, HospitalSourceAsset, hospital_id=hospital.id, title=QA_SOURCE_TITLE)
    if source is None:
        source = HospitalSourceAsset(
            hospital_id=hospital.id,
            source_type=SourceType.NAVER_BLOG,
            title=QA_SOURCE_TITLE,
        )
        db.add(source)
    source.url = "https://blog.naver.com/jangpyeonhan_clinic/223000000001"
    source.raw_text = "대장내시경 검사 전 준비 방법을 안내하는 글입니다."
    source.status = SourceStatus.ERROR
    source.process_error = "가져오기 연결이 중단되었습니다."
    source.created_by = "이수진"
    source.source_metadata = {"qa_fixture": True}
    db.flush()
    return source


def ensure_lead_diagnosis(db: Session, lead_id: uuid.UUID) -> LeadDiagnosis:
    diagnosis = _one(db, LeadDiagnosis, lead_id=lead_id)
    if diagnosis is None:
        diagnosis = LeadDiagnosis(
            lead_id=lead_id,
            applicant_email_hash="a" * 64,
            subject_phone_hash="b" * 64,
            subject_hospital_name=QA_HOSPITAL_NAME,
            subject_region="서울 강남",
            slot_date=date(2099, 8, 10),
            slot_no=99,
            queries=["서울 강남 외과 추천"],
            requested_models={"chatgpt": "qa-model", "gemini": "qa-model"},
            repeat_count=1,
        )
        db.add(diagnosis)
    diagnosis.execution_status = ExecutionStatus.FAILED.value
    diagnosis.execution_attempts = 3
    diagnosis.report_status = ReportStatus.BLOCKED.value
    diagnosis.report_attempts = 3
    diagnosis.delivery_status = DeliveryStatus.PENDING.value
    diagnosis.finished_at = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
    diagnosis.error = "측정 연결이 완료되지 않았습니다."
    db.flush()
    return diagnosis
