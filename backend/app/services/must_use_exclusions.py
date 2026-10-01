"""원문 요구에서 뺀 승인본 필수 문구의 경고·운영자 기록.

승인본의 필수 문구에 의료광고 금지 표현이 있으면 원문 그대로 쓸 수 없다 — 쓰면 저장·발행
게이트가 막는다. 그래서 `must_use_verbatim.must_use_requirement`가 그 문구를 작가 요구·
프롬프트·검수 면제에서 모두 뺀다. 빼는 것만으로 끝나면 승인된 문구가 글에서 조용히
사라지므로 여기서 두 가지를 남긴다.

- 제외할 때마다 구조화 경고 로그(병원 id, 승인본 id·버전, 문구 위치, 걸린 금지 표현).
- 운영자 할 일로 기존 인시던트 한 건(`open_ops_incident`). 중복 키는 승인본 id이므로
  **승인본 버전 하나에 한 건**이다. 이미 한 번 기록한 버전은 상태와 무관하게 다시 열지
  않는다(운영자가 확인 처리한 뒤 같은 버전으로 또 알리지 않는다). 운영자가 문구를 고쳐
  새 버전을 승인하면 그 버전은 새 키다.

기록 실패는 생성·검수를 되돌리지 않는다 — 다음 생성이 다시 시도한다.
"""

from __future__ import annotations

import logging
import uuid

from app.services.must_use_verbatim import (
    ExcludedMustUseMessage,
    MustUseRequirement,
    approved_philosophy,
    must_use_requirement,
)

logger = logging.getLogger(__name__)

MUST_USE_EXCLUSION_PIPELINE = "essence_must_use"
MUST_USE_EXCLUSION_OBJECT_TYPE = "content_philosophy"
MUST_USE_EXCLUSION_INCIDENT_TYPE = "ESSENCE_MUST_USE_EXCLUDED"


def _problem(version: object, excluded: tuple[ExcludedMustUseMessage, ...]) -> str:
    positions = ", ".join(f"{item.index + 1}번째" for item in excluded)
    expressions = sorted(
        {expression for item in excluded for expression in item.forbidden_expressions}
    )
    reason = (
        f"의료광고 금지 표현({', '.join(expressions)})"
        if expressions
        else "문장이 아닌 값"
    )
    return (
        f"승인된 운영 기준 v{version}의 필수 문구 {positions}에 {reason}이 있어 "
        "새 글에 원문 그대로 넣지 않습니다."
    )


def _log_exclusions(
    hospital_id: object, philosophy: object, requirement: MustUseRequirement
) -> None:
    for item in requirement.excluded:
        fields = {
            "event": "must_use_message_excluded",
            "hospital_id": str(hospital_id),
            "philosophy_id": str(getattr(philosophy, "id", "")),
            "essence_version": getattr(philosophy, "version", None),
            "must_use_index": item.index,
            "reason": item.reason,
            "forbidden_expressions": list(item.forbidden_expressions),
        }
        logger.warning(
            "must_use message excluded from verbatim requirement "
            "hospital_id=%s philosophy_id=%s essence_version=%s must_use_index=%s "
            "reason=%s forbidden_expressions=%s",
            fields["hospital_id"],
            fields["philosophy_id"],
            fields["essence_version"],
            item.index,
            item.reason,
            ",".join(item.forbidden_expressions),
            extra=fields,
        )


async def record_must_use_exclusions(hospital: object, philosophy: object | None) -> bool:
    """제외 문구가 있으면 경고 로그를 남기고, 이 승인본 버전의 운영자 기록을 한 번 연다.

    새로 기록했으면 True. 제외 문구가 없거나 이미 기록된 버전이면 False.
    """

    approved = approved_philosophy(philosophy)
    if approved is None:
        return False
    requirement = must_use_requirement(approved)
    if not requirement.excluded:
        return False
    hospital_id = getattr(hospital, "id", None)
    _log_exclusions(hospital_id, approved, requirement)
    philosophy_id = getattr(approved, "id", None)
    if hospital_id is None or philosophy_id is None:
        return False
    try:
        return await _open_once(
            hospital_id=hospital_id,
            hospital_name=str(getattr(hospital, "name", "") or "이름 미확인 병원"),
            philosophy_id=philosophy_id,
            problem=_problem(getattr(approved, "version", "?"), requirement.excluded),
        )
    except Exception:
        # 기록 실패가 생성·검수를 막지 않는다. 다음 생성이 같은 키로 다시 시도한다.
        logger.exception(
            "Failed to record excluded must_use messages for philosophy %s", philosophy_id
        )
        return False


async def _open_once(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    philosophy_id: uuid.UUID,
    problem: str,
) -> bool:
    # 무거운 인시던트·알림 모듈은 제외 문구가 실제로 있을 때만 불러온다.
    from sqlalchemy import select

    from app.core.database import get_async_sessionmaker
    from app.models.operations import Incident, IncidentSeverity
    from app.services.incident_safety import build_incident_key
    from app.services.incident_types import IncidentFingerprint
    from app.services.ops_incident_alerts import open_ops_incident

    fingerprint = IncidentFingerprint.VALIDATION_FAILED
    key = build_incident_key(
        MUST_USE_EXCLUSION_PIPELINE,
        MUST_USE_EXCLUSION_OBJECT_TYPE,
        str(philosophy_id),
        fingerprint,
    )
    async with get_async_sessionmaker()() as db:
        if await db.scalar(select(Incident.id).where(Incident.dedupe_key == key)) is not None:
            return False
    await open_ops_incident(
        pipeline=MUST_USE_EXCLUSION_PIPELINE,
        object_type=MUST_USE_EXCLUSION_OBJECT_TYPE,
        object_id=str(philosophy_id),
        incident_type=MUST_USE_EXCLUSION_INCIDENT_TYPE,
        safe_error_code=MUST_USE_EXCLUSION_INCIDENT_TYPE,
        problem=problem,
        customer_impact="이 문구는 새 글에 들어가지 않습니다. 다른 필수 문구와 발행은 그대로 진행됩니다.",
        next_action="병원 정보 탭에서 운영 기준의 해당 필수 문구를 금지 표현 없이 고쳐 다시 승인해 주세요.",
        source_type="HOSPITAL_CONTENT_PHILOSOPHY",
        hospital_name=hospital_name,
        hospital_id=hospital_id,
        admin_path=f"/hospitals/{hospital_id}/info",
        fingerprint=fingerprint,
        severity=IncidentSeverity.MEDIUM,
    )
    return True


async def approved_must_use_messages_with_record(
    hospital: object, philosophy: object | None
) -> list[str]:
    """원문 그대로 요구할 필수 문구. 뺀 문구가 있으면 경고·운영자 기록을 남긴다."""

    await record_must_use_exclusions(hospital, philosophy)
    return list(must_use_requirement(philosophy).messages)


__all__ = (
    "MUST_USE_EXCLUSION_INCIDENT_TYPE",
    "MUST_USE_EXCLUSION_OBJECT_TYPE",
    "MUST_USE_EXCLUSION_PIPELINE",
    "approved_must_use_messages_with_record",
    "record_must_use_exclusions",
)
