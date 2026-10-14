"""일회성 운영 작업: 조치할 것이 없는 오래된 사고를 조용히 닫는다(Full-Automatic W9, 설계 §3.6).

대상(열린 OPEN·RETRYING 사고):

- 조용한 종류(`incident_types.incident_is_quiet`)이고 7일 동안 다시 관측되지 않음 → 닫는다.
  아직 새로운 조용한 사고는 닫지 않고 등급만 LOW로 내린다(조용한 사고는 OPEN/HIGH가 아니다).
- `BACKGROUND_TASK_FAILED`(레거시, 더 이상 열리지 않음)·리드 진단 실패 — 15분 근거 정리와 같은
  resolver(`incident_backlog`)가 근거를 내면 닫는다.

닫기는 OPEN→RETRYING→RECOVERED→ACKNOWLEDGED(시스템 확인)이고, Slack '해결' 알림을 넣지 않는다.
행마다 감사 로그(actor `system:quiet-incident-settle`)를 남긴다. 기본은 dry-run이다::

    python -m app.utils.quiet_incident_settle            # 대상 JSON 한 줄씩 + 종류별 건수
    python -m app.utils.quiet_incident_settle --apply    # 닫기·LOW 내리기·감사·커밋
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident, IncidentState
from app.services.incident_types import incident_is_quiet
from app.workers.incident_backlog import RESOLVERS, SILENT_RESOLUTION_TYPES
from app.workers.task_incident_control import _transition_incident

ACTOR = "system:quiet-incident-settle"
QUIET_STALE_AFTER = timedelta(days=7)
CLOSE = "CLOSE"
DEMOTE = "DEMOTE_LOW"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def decide(db: Session, incident: Incident, now: datetime) -> tuple[str, str] | None:
    """(행동, 근거) 또는 None(그대로 둔다)."""

    kind = incident.incident_type or ""
    if incident_is_quiet(kind):
        last_seen = _aware(incident.last_seen_at)
        if last_seen is not None and last_seen < now - QUIET_STALE_AFTER:
            return CLOSE, "quiet_no_recurrence_7d"
        if (incident.severity or "") != "LOW":
            return DEMOTE, "quiet_incident_is_low"
        return None
    if kind in SILENT_RESOLUTION_TYPES and kind in RESOLVERS:
        evidence = RESOLVERS[kind](db, incident, now)
        if evidence is not None:
            return CLOSE, evidence
    return None


def _audit(db: Session, incident: Incident, action: str, evidence: str) -> None:
    db.add(
        AdminAuditLog(
            hospital_id=incident.hospital_id,
            actor=ACTOR,
            action=action,
            target_type="incident",
            target_id=str(incident.id),
            detail={
                "incident_type": incident.incident_type,
                "evidence": evidence,
                "slack_suppressed": True,
                "version": incident.version,
            },
        )
    )


def _close(db: Session, incident: Incident, evidence: str) -> bool:
    current: Incident | None = incident
    if incident.state == IncidentState.OPEN.value:
        current = _transition_incident(
            db, incident, expected_state=IncidentState.OPEN, next_state=IncidentState.RETRYING
        )
    if current is None:
        return False
    recovered = _transition_incident(
        db,
        current,
        expected_state=IncidentState.RETRYING,
        next_state=IncidentState.RECOVERED,
        recovered=True,
    )
    if recovered is None:
        return False
    closed = _transition_incident(
        db,
        recovered,
        expected_state=IncidentState.RECOVERED,
        next_state=IncidentState.ACKNOWLEDGED,
        acknowledged=True,
    )
    _audit(db, closed or recovered, "incident_settled_quietly", evidence)
    return True


def run(
    db: Session,
    *,
    apply: bool,
    out: TextIO,
    now: datetime | None = None,
    incident_ids: Sequence[uuid.UUID] | None = None,
) -> dict[str, int]:
    observed = now or datetime.now(UTC)
    statement = select(Incident).where(
        Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value))
    )
    if incident_ids:
        statement = statement.where(Incident.id.in_(list(incident_ids)))
    incidents = list(
        db.execute(statement.order_by(Incident.first_seen_at, Incident.id)).scalars().all()
    )
    planned: list[tuple[Incident, str, str]] = []
    for incident in incidents:
        decision = decide(db, incident, observed)
        if decision is None:
            continue
        action, evidence = decision
        planned.append((incident, action, evidence))
        out.write(
            "CANDIDATE "
            + json.dumps(
                {
                    "incident_id": str(incident.id),
                    "incident_type": incident.incident_type,
                    "state": incident.state,
                    "severity": incident.severity,
                    "action": action,
                    "evidence": evidence,
                    "first_seen_at": _aware(incident.first_seen_at).isoformat()
                    if incident.first_seen_at
                    else None,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    counts = Counter(f"{action}:{incident.incident_type}" for incident, action, _ in planned)
    if not apply:
        db.rollback()
        summary = {"apply": False, **dict(sorted(counts.items()))}
        out.write(json.dumps(summary, ensure_ascii=False) + "\n")
        return dict(counts)
    done: Counter[str] = Counter()
    for incident, action, evidence in planned:
        if action == CLOSE:
            if _close(db, incident, evidence):
                done[f"{action}:{incident.incident_type}"] += 1
        else:
            incident.severity = "LOW"
            incident.version += 1
            _audit(db, incident, "incident_severity_lowered", evidence)
            done[f"{action}:{incident.incident_type}"] += 1
        db.commit()
    out.write(json.dumps({"apply": True, **dict(sorted(done.items()))}, ensure_ascii=False) + "\n")
    return dict(done)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="실제로 닫고 커밋한다(기본 dry-run)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    from app.core.database import SyncSessionLocal

    args = parse_args(argv)
    with SyncSessionLocal() as db:
        run(db, apply=args.apply, out=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
