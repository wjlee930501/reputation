"""Shared operator intervention policy; automatic recovery is not a human task."""

from datetime import datetime

from app.models.operations import IncidentState


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
