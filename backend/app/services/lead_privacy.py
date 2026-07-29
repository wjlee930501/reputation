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


def _apply_diagnosis_purge(diagnosis, artifacts, tokens, results, now: datetime, delete_object) -> int:
    """진단 1건의 파기 변형을 적용하고 **지워야 할 저장소 경로**를 돌려준다.

    로딩(동기/비동기)과 변형을 갈라놓는다 — 배치는 SyncSession, 즉시 파기 API는
    AsyncSession을 쓰는데, 규칙이 두 벌이 되면 반드시 어긋난다. 실제로 `/erase`가
    진단 산출물을 건드리지 않아 리포트 PDF와 활성 토큰이 남은 채 `purged_at`만
    찍히고, 이후 배치가 그 리드를 영영 건너뛰는 상태였다.
    """
    from app.models.lead_diagnosis import ReportStatus

    # **객체 삭제가 먼저, purged_at은 그 다음이다.** 반대로 하면 삭제가 실패했는데
    # 파기 기록만 남는 상태가 만들어질 수 있다. 실패는 예외로 올라가 커밋을 막는다.
    deleted = 0
    for artifact in artifacts:
        delete_object(artifact.storage_uri)
        artifact.purged_at = now
        deleted += 1
    for token in tokens:
        token.revoked_at = now
    for result in results:
        result.raw_response = ""
        # 질의 원문에는 지역·진료과·키워드가 들어 있다 — 처리방침이 파기를 약속한 항목이다.
        result.query_text = "[purged]"

    # 진단 행 자체의 식별 정보. 처리방침은 병원명·진료과·지역을 수집 항목으로 명시하고
    # 180일 또는 요청 시 파기를 약속한다 — 여기를 비우지 않으면 그 약속이 안 지켜진다.
    diagnosis.subject_hospital_name = "[purged]"
    diagnosis.subject_region = "[purged]"
    diagnosis.queries = []
    if diagnosis.report_status != ReportStatus.PURGED.value:
        diagnosis.report_status = ReportStatus.PURGED.value

    return deleted


def _purge_selects(lead_id):
    from sqlalchemy import select

    from app.models.lead_diagnosis import (
        LeadDiagnosis,
        LeadDiagnosisResult,
        LeadReportArtifact,
        LeadReportToken,
    )

    return (
        select(LeadDiagnosis).where(LeadDiagnosis.lead_id == lead_id),
        lambda did: select(LeadReportArtifact).where(
            LeadReportArtifact.diagnosis_id == did, LeadReportArtifact.purged_at.is_(None)
        ),
        lambda did: select(LeadReportToken).where(
            LeadReportToken.diagnosis_id == did, LeadReportToken.revoked_at.is_(None)
        ),
        lambda did: select(LeadDiagnosisResult).where(
            LeadDiagnosisResult.diagnosis_id == did
        ),
    )


def purge_lead_diagnosis_artifacts(db, lead_id, now: datetime) -> dict:
    """진단 산출물 파기 — **동기 세션**(보관기간 배치)용.

    **GCS 객체 삭제가 DB 커밋보다 먼저다.** 반대로 하면 `purged_at`은 찍혔는데 객체는
    살아 있는, 가장 나쁜 상태가 된다 — 파기 기록이 거짓말이 되고 아무도 모른다.
    삭제 실패는 예외로 올려 커밋을 막고 다음 날 다시 시도한다.

    **잠금 해시는 지우지 않는다.** 보관기간이 지났다고 두 번째 무료 진단을 주는 것이
    아니기 때문이다(PRD F1-6).
    """
    from app.services import lead_report

    diag_stmt, artifacts_of, tokens_of, results_of = _purge_selects(lead_id)
    diagnoses = db.execute(diag_stmt).scalars().all()

    deleted = 0
    for diagnosis in diagnoses:
        deleted += _apply_diagnosis_purge(
            diagnosis,
            db.execute(artifacts_of(diagnosis.id)).scalars().all(),
            db.execute(tokens_of(diagnosis.id)).scalars().all(),
            db.execute(results_of(diagnosis.id)).scalars().all(),
            now,
            lead_report.delete_report_pdf,   # 실패 시 예외 → 커밋 안 됨
        )

    return {"diagnoses": len(diagnoses), "artifacts": deleted}


async def purge_lead_diagnosis_artifacts_async(db, lead_id, now: datetime) -> dict:
    """같은 규칙, **비동기 세션**(즉시 파기 API)용."""
    from app.services import lead_report

    diag_stmt, artifacts_of, tokens_of, results_of = _purge_selects(lead_id)
    diagnoses = (await db.execute(diag_stmt)).scalars().all()

    deleted = 0
    for diagnosis in diagnoses:
        deleted += _apply_diagnosis_purge(
            diagnosis,
            (await db.execute(artifacts_of(diagnosis.id))).scalars().all(),
            (await db.execute(tokens_of(diagnosis.id))).scalars().all(),
            (await db.execute(results_of(diagnosis.id))).scalars().all(),
            now,
            lead_report.delete_report_pdf,
        )

    return {"diagnoses": len(diagnoses), "artifacts": deleted}


def purge_lead_completely(db, lead: SalesLead, now: datetime) -> dict:
    """정보주체 파기의 단일 진입점 — 동기 경로(보관기간 배치)."""
    anonymized = anonymize_lead(lead, now)
    return {"anonymized": anonymized, **purge_lead_diagnosis_artifacts(db, lead.id, now)}


async def purge_lead_completely_async(db, lead: SalesLead, now: datetime) -> dict:
    """정보주체 파기의 단일 진입점 — 비동기 경로(즉시 파기 API)."""
    anonymized = anonymize_lead(lead, now)
    return {
        "anonymized": anonymized,
        **await purge_lead_diagnosis_artifacts_async(db, lead.id, now),
    }


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
