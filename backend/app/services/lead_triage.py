"""상담 요청 목록이 운영 점검용 리드를 구분할 수 있게 하는 판별.

운영 점검(QA) 픽스처는 실제 고객 이름(`장편한외과의원`)으로 상담 요청을 만든다.
목록 API는 그 사실을 알려 주는 필드를 하나도 내보내지 않았고, 화면은 `source_path`를
`운영 점검`이라는 작은 회색 글씨로만 바꿔 `lg` 미만에서는 아예 숨겼다. 그래서 AE는
점검용 요청을 실제 신규 상담으로 보고 전화를 걸거나 병원을 만들 수 있었다.

판별 기준은 정리 스크립트(`ops_control_qa_cleanup`)가 삭제 대상을 확인할 때 쓰는 것과
같은 세 값이다. 여기서만 다른 기준을 쓰면 화면이 "점검용"이라 부른 리드를 정리
스크립트는 실데이터로 보게 된다.
"""

from __future__ import annotations

from typing import Any

# 운영 점검 픽스처가 남기는 세 값 — 정리 스크립트의 안전 확인과 같은 조합.
QA_LEAD_SOURCE_PATH = "/ops-qa"
QA_LEAD_CONSENT_VERSION = "ops-qa-v1"
QA_LEAD_NOTE_PREFIX = "[OPS-QA-"


def is_operations_test_lead(lead: Any) -> bool:
    """운영 점검용으로 만들어진 상담 요청인가."""
    if getattr(lead, "source_path", None) != QA_LEAD_SOURCE_PATH:
        return False
    if getattr(lead, "consent_version", None) != QA_LEAD_CONSENT_VERSION:
        return False
    note = getattr(lead, "conversion_note", None) or ""
    return note.startswith(QA_LEAD_NOTE_PREFIX)
