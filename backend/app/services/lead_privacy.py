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
    """
    if lead.purged_at is not None:
        return False
    lead.clinic_name = "[purged]"
    lead.contact = "[purged]"
    lead.question = "[purged]"
    lead.consent_ip = None
    if getattr(lead, "conversion_note", None):
        lead.conversion_note = "[purged]"
    lead.purged_at = now
    return True


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
