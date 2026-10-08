"""Shared operator intervention policy; automatic recovery is not a human task."""

from datetime import datetime

from app.models.operations import IncidentState
from app.services.incident_types import IncidentAudience, incident_audience, incident_is_quiet


def requires_operator_action(state: str, sla_due_at: datetime | None, now: datetime) -> bool:
    """지금 사람이 손대야 풀리는 인시던트인가 — 큐 행·현황 카드·목록 건수의 유일한 정의.

    RETRYING is automatic recovery while its promised window remains. Once that
    deadline passes, the unresolved episode becomes operator work even though the
    last recorded transition still says retrying. 화면마다 상태 집합을 새로 쓰면
    자동 복구 중인 작업이 운영자의 할 일로 새어 나간다.
    """
    if state == IncidentState.OPEN.value:
        return True
    return state == IncidentState.RETRYING.value and sla_due_at is not None and sla_due_at < now


def is_operator_todo(
    incident_type: str | None, state: str, sla_due_at: datetime | None, now: datetime
) -> bool:
    """운영 담당자의 할 일 한 건 — 일일 요약·운영센터 큐·현황 카드·병원 목록이 쓰는 유일한 술어.

    사람의 일(`requires_operator_action`)이어도 개발 담당만 고칠 수 있는 종류(인프라)나 조용한
    종류(사후검수 지적처럼 계약상 운영자 큐에 올리지 않는 표본 확인)는 운영 담당의 할 일이
    아니다. 화면마다 이 두 조건을 따로 빼면 같은 날 요약은 65건, 큐는 14건이라고 말한다.
    """
    return (
        requires_operator_action(state, sla_due_at, now)
        and incident_audience(incident_type) is IncidentAudience.OPERATOR
        and not incident_is_quiet(incident_type)
    )
