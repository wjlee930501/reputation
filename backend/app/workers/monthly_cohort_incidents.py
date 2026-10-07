"""월간 측정 코호트의 구성 공백·비용 상한 초과를 알리는 사고(2026-10).

코호트 편입은 자동이다. 자동으로 편입되지 못한 병원(기록 없음·고정 세트 부족)과 상한을 넘은
코호트만 사람의 일이다. 둘 다 한 기간에 한 건으로 묶어 반복 알림을 만들지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.database import get_async_sessionmaker
from app.models.operations import IncidentSeverity
from app.services.incident_safety import sanitize_operator_text
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident

COHORT_GAP_INCIDENT_TYPE = "MONTHLY_SOV_COHORT_GAP"
COHORT_OVER_LIMIT_INCIDENT_TYPE = "MONTHLY_SOV_COHORT_OVER_LIMIT"
_SOURCE_TYPE = "MONTHLY_SOV_COHORT"


async def open_monthly_cohort_gap(
    *, hospital_id: uuid.UUID, hospital_name: str, period_key: str, reason: str
) -> uuid.UUID:
    """월간 측정 창이 열렸는데 코호트에 못 들어간 병원 한 곳당 기간별 한 건."""

    message = sanitize_operator_text(
        f"{hospital_name}: 월간 측정 대상에 들어가지 못했습니다 ({reason})"
    )
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="monthly_sov_cohort",
                object_type="hospital_month",
                object_id=f"{hospital_id}:{period_key}",
                fingerprint=IncidentFingerprint.MISSING_PREREQUISITE,
                incident_type=COHORT_GAP_INCIDENT_TYPE,
                severity=IncidentSeverity.HIGH,
                customer_impact=(
                    "이번 달 AI 노출 측정이 실행되지 않아 월간 리포트를 전달할 수 없게 됩니다."
                ),
                source_type=_SOURCE_TYPE,
                next_action=(
                    "병원 정보 탭에서 지역 질문(LOCAL·ACTIVE) 10개 이상을 갖추고 "
                    "첫 노출 측정이 끝났는지 확인하세요. 갖춰지면 다음 측정 때 자동으로 포함됩니다."
                ),
                admin_path=f"/hospitals/{hospital_id}/reports",
                hospital_id=hospital_id,
                source_id=f"{hospital_id}:{period_key}",
                safe_error_code=COHORT_GAP_INCIDENT_TYPE,
                safe_error_message=message,
            ),
            actor="monthly-sov-worker",
            reason="monthly cohort enrollment gap",
            now=datetime.now(UTC),
        )
        await db.commit()
        return incident.id


async def open_monthly_cohort_over_limit(
    *, period_key: str, cohort_size: int, limit: int
) -> uuid.UUID:
    """코호트가 비용 경고 기준을 넘었다. 측정은 줄이지 않고 한 건만 올린다."""

    message = (
        f"월간 측정 병원이 {cohort_size}곳으로 경고 기준 {limit}곳을 넘었습니다. "
        "전원 측정은 그대로 진행됩니다."
    )
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="monthly_sov_cohort",
                object_type="month",
                object_id=period_key,
                fingerprint=IncidentFingerprint.COST_BLOCKED,
                incident_type=COHORT_OVER_LIMIT_INCIDENT_TYPE,
                severity=IncidentSeverity.MEDIUM,
                customer_impact=message,
                source_type=_SOURCE_TYPE,
                next_action=(
                    "측정 비용 한도(COST_GUARD_MONTHLY_SOV_QUERIES)가 병원 수를 감당하는지 "
                    "확인하고 SOV_MONTHLY_COHORT_LIMIT을 함께 조정하세요."
                ),
                admin_path="/operations",
                hospital_id=None,
                source_id=period_key,
                safe_error_code=COHORT_OVER_LIMIT_INCIDENT_TYPE,
                safe_error_message=message,
            ),
            actor="monthly-sov-worker",
            reason="monthly cohort exceeds cost warning threshold",
            now=datetime.now(UTC),
        )
        await db.commit()
        return incident.id
