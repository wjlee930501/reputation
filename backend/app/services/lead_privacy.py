"""Lead PII lifecycle — single source of truth for anonymization.

개인정보보호법 제21조(보유기간 경과 파기) + 정보주체 파기 요청 모두 동일한 익명화를
거치도록 한 곳에 모은다. 통계용 메타(clinic_type, source_path, consent_version)는 유지하고
개인 식별 가능 필드만 폐기한다.
"""
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
