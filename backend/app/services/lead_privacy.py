"""Lead PII lifecycle — single source of truth for anonymization.

개인정보보호법 제21조(보유기간 경과 파기) + 정보주체 파기 요청 모두 동일한 익명화를
거치도록 한 곳에 모은다. 통계용 메타(clinic_type, source_path, consent_version)는 유지하고
개인 식별 가능 필드만 폐기한다.
"""
import re
import uuid
from datetime import datetime

from app.models.lead import SalesLead


def anonymize_lead(lead: SalesLead, now: datetime) -> bool:
    """Clear identifiable fields in place. Idempotent — returns False if already purged.

    conversion_note도 비운다: 과거 데이터가 연락처/문의 원문을 담고 있을 수 있기 때문(PII-3).

    무료 진단(1단) 리드는 여기서 지우는 것만으로 부족하다 — 진단 산출물이 별도 테이블에
    있으므로 `purge_lead_diagnosis_artifacts`를 함께 호출해야 파기가 사실이 된다.
    """
    if lead.purged_at is not None:
        return False
    lead.clinic_name = "[purged]"
    lead.contact = "[purged]"
    lead.question = "[purged]"
    lead.consent_ip = None
    # 무료 진단 폼으로 들어온 신규 개인정보 — 있으면 함께 지운다.
    for field_name in ("email", "contact_name", "clinic_phone"):
        if getattr(lead, field_name, None):
            setattr(lead, field_name, "[purged]")
    if getattr(lead, "conversion_note", None):
        lead.conversion_note = "[purged]"
    lead.purged_at = now
    return True


def purge_lead_diagnosis_artifacts(db, lead_id, now: datetime) -> dict:
    """진단 산출물 파기 (설계 §6-4). 동기 세션(파기 배치)에서 호출한다.

    **GCS 객체 삭제가 DB 커밋보다 먼저다.** 반대로 하면 `purged_at`은 찍혔는데 객체는
    살아 있는, 가장 나쁜 상태가 된다 — 파기 기록 자체가 거짓말이 되고, 그 사실을
    아무도 모른다. 삭제에 실패하면 예외를 올려 커밋을 막고 다음 날 다시 시도한다
    (파기 배치는 매일 04:00 반복된다).

    **잠금 해시(applicant_email_hash·subject_phone_hash)는 지우지 않는다.**
    보관기간이 지났다고 두 번째 무료 진단을 주는 것이 아니기 때문이다(PRD F1-6).
    """
    from sqlalchemy import select

    from app.models.lead_diagnosis import (
        LeadDiagnosis,
        LeadDiagnosisResult,
        LeadReportArtifact,
        LeadReportToken,
        ReportStatus,
    )
    from app.services import lead_report

    diagnoses = db.execute(
        select(LeadDiagnosis).where(LeadDiagnosis.lead_id == lead_id)
    ).scalars().all()

    deleted_objects = 0
    for diagnosis in diagnoses:
        artifacts = db.execute(
            select(LeadReportArtifact).where(
                LeadReportArtifact.diagnosis_id == diagnosis.id,
                LeadReportArtifact.purged_at.is_(None),
            )
        ).scalars().all()
        for artifact in artifacts:
            lead_report.delete_report_pdf(artifact.storage_uri)   # 실패 시 예외 → 커밋 안 됨
            artifact.purged_at = now
            deleted_objects += 1

        for token in db.execute(
            select(LeadReportToken).where(
                LeadReportToken.diagnosis_id == diagnosis.id,
                LeadReportToken.revoked_at.is_(None),
            )
        ).scalars().all():
            token.revoked_at = now

        # AI 답변 원문에는 신청자 개인정보가 들어가지 않지만(질의에 이름·연락처를 넣지
        # 않는다), 병원을 식별하는 문맥이 남으므로 함께 비운다.
        for result in db.execute(
            select(LeadDiagnosisResult).where(
                LeadDiagnosisResult.diagnosis_id == diagnosis.id
            )
        ).scalars().all():
            result.raw_response = ""

        if diagnosis.report_status != ReportStatus.PURGED.value:
            diagnosis.report_status = ReportStatus.PURGED.value

    return {"diagnoses": len(diagnoses), "artifacts": deleted_objects}


def scrub_onboarding_note(note: str | None, lead_id: uuid.UUID | str) -> str | None:
    """Erase the operator free-text from this lead's source block in onboarding_note.

    CDX-M2: 전환 시 운영자가 입력한 conversion_note가 hospital.onboarding_note에 복사돼
    lead row의 파기 라이프사이클을 벗어난다. 파기 시점에 해당 lead의 source block 안에서
    'Operator note:' 이후 텍스트(자유 입력 — 연락처가 섞일 수 있는 유일한 부분)를 지운다.
    구조화된 메타(clinic type, source path, consent version)는 통계·이력용으로 유지.
    """
    if not note:
        return note
    lead_marker = f"Source lead: {lead_id}"
    if lead_marker not in note:
        return note
    # block = 해당 lead 마커부터 다음 'Source lead:' 마커 직전(또는 끝)까지.
    block_pattern = re.compile(
        rf"({re.escape(lead_marker)}.*?)(?=\n\s*Source lead: |\Z)", re.DOTALL
    )

    def _scrub_block(match: re.Match[str]) -> str:
        block = match.group(1)
        idx = block.find("Operator note:")
        if idx == -1:
            return block
        trailing = "\n" if block.endswith("\n") else ""
        return block[:idx] + "Operator note: [purged]" + trailing

    return block_pattern.sub(_scrub_block, note)
