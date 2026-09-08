"""사이트 준비 최종 차단(SITE_BUILD_RETRIES_EXHAUSTED) 한 건을 갱신하는 공용 규칙 (H-13).

이 사고는 병원 하나당 한 건이고, 만지는 곳은 둘이다. 자동 복구 sweep이 예산을 다 쓸 때와,
운영센터 재시도가 만든 자식 실행이 실패할 때다. 두 곳이 각자 갱신 규칙을 쓰면 한쪽만 바뀌는
순간 에피소드 번호·공지·감사 기록이 어긋나고, 사람은 같은 원인을 두 가지 이야기로 보게 된다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.operations import Incident, IncidentState
from app.services.incident_assignment import auto_assign_owner_sync, owner_label_sync
from app.services.incident_safety import site_build_incident_key
from app.services.incident_types import incident_type_of
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync


def load_site_build_incident(db: Session, hospital_id: uuid.UUID) -> Incident | None:
    """이 병원의 최종 차단 한 건을 잠그고 읽는다.

    아래 갱신은 모두 읽은 값 위에 쓴다. 잠그지 않으면 그 사이에 들어온 관리자의 배정·확인
    (Incident.version CAS)을 조용히 덮어쓰거나, 이미 바뀐 주인을 실은 Slack 투영을 내보낸다.
    잠금은 tick의 병원별 commit에서 풀린다.
    """

    return (
        db.execute(
            select(Incident)
            .where(Incident.dedupe_key == site_build_incident_key(hospital_id))
            .with_for_update()
        )
        .scalars()
        .first()
    )


def touch_site_build_incident(
    db: Session, incident: Incident, *, failed_run_id: uuid.UUID, observed_at: datetime
) -> None:
    """열려 있는 에피소드에 관측 한 번을 더한다."""

    incident.last_seen_at = observed_at
    incident.occurrence_count += 1
    # 조치 버튼이 가장 최근 실패를 가리켜야 운영자가 다시 시도할 실행을 찾는다.
    incident.operation_run_id = failed_run_id
    # 관리자 화면의 낙관적 잠금이 이 변경을 알아채야 한다 — 버전을 올리지 않으면
    # 배정·확인 요청이 옛 값 위에서 성공한다.
    incident.version += 1
    incident.updated_at = observed_at
    audit_site_build_incident(db, incident)


def reopen_site_build_incident(
    db: Session,
    incident: Incident,
    *,
    hospital_name: str,
    failed_run_id: uuid.UUID,
    observed_at: datetime,
) -> None:
    """닫힌 사고를 같은 원인의 새 에피소드로 되돌린다 (`open_or_touch_incident`와 같은 규칙)."""

    incident.state = IncidentState.OPEN.value
    incident.episode_seq += 1
    incident.first_seen_at = observed_at
    incident.last_seen_at = observed_at
    incident.occurrence_count += 1
    incident.operation_run_id = failed_run_id
    incident.recovered_at = None
    incident.acknowledged_at = None
    incident.acknowledged_by_id = None
    incident.version += 1
    incident.updated_at = observed_at
    auto_assign_owner_sync(db, incident, observed_at=observed_at)
    notify_site_build_incident(
        db, incident, hospital_name=hospital_name, observed_at=observed_at
    )
    audit_site_build_incident(db, incident)


def notify_site_build_incident(
    db: Session, incident: Incident, *, hospital_name: str, observed_at: datetime
) -> None:
    """새로 열린 에피소드 하나에 운영자 채널 알림 하나. dedupe 키가 에피소드를 포함한다."""

    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital_name,
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label=owner_label_sync(db, incident.owner_id),
        sla_label="확인 필요",
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        problem=incident.safe_error_message,
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
    enqueue_notification_sync(
        db,
        build_open_incident_notification(projection, settings.ADMIN_BASE_URL),
        now=observed_at,
    )


def audit_site_build_incident(db: Session, incident: Incident) -> None:
    db.add(
        AdminAuditLog(
            hospital_id=incident.hospital_id,
            actor="system",
            action="incident_occurrence_recorded",
            target_type="incident",
            target_id=str(incident.id),
            detail={
                "state": incident.state,
                "version": incident.version,
                "occurrence_count": incident.occurrence_count,
                "episode_seq": incident.episode_seq,
            },
        )
    )
