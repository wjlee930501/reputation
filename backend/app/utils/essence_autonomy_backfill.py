"""일회성 운영 작업: 승인 없는 자동 Essence(Phase 4 D33)로 넘어가는 배포 B의 백필.

배포 뒤 15분 정기 조정도 같은 일을 하지만(PROFILE_FACTS 동기화·판 없는 병원의 자동 요약), 이 CLI는
대상과 결과를 한 번에 보이고 기다리지 않게 한다.

1. 모든 병원의 병원 정보(자동) 자료(PROFILE_FACTS)를 병원 행과 맞춘다(결정적, LLM 없음). 기존 승인본이
   있는 병원은 이것만으로 다시 합성되지 않는다 — 옛 승인본은 PROFILE_FACTS를 입력으로 쓴 적이 없다(T4).
2. 대상 병원(기본: 적용 중인 판이 없는 병원 — 장보고·뉴플랜·세종다온)의 자동 요약을 그 자리에서
   합성·적용하고(워커와 같은 비용 가드 합성기), 첫 발행 일정을 기존 경로로 만든다(첫 달 안분 그대로).
   남은 옛 보류 초안은 적용과 함께 보관된다.
3. 더 이상 만들지 않는 "승인 기준 없음" 아침 요약(`MISSING_APPROVED_ESSENCE_DIGEST`)의 미발송 행을
   FAILED(`RETIRED_NOTIFICATION_TYPE`)로 종결한다(발송기는 이미 집지 않는다).

기본은 dry-run이다(아무것도 쓰지 않는다)::

    python -m app.utils.essence_autonomy_backfill                   # 대상·예상 동작 JSON 한 줄씩
    python -m app.utils.essence_autonomy_backfill --apply           # 동기화·적용·종결
    python -m app.utils.essence_autonomy_backfill --apply --ids a,b # 지정한 병원만 대상으로

dry-run은 적용 중인 판이 있는 병원마다 지금 다시 합성될 이유(`trigger`)도 보인다 — 배포 B 뒤에도
9곳 판 번호가 그대로인지 확인하는 근거다(`null`이면 그대로다).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services.essence_auto_review import (
    EssenceRefreshStatus,
    essence_refresh_trigger,
    is_automatic_essence,
    refresh_essence_snapshot,
)
from app.services.notification_store import RETIRED_NOTIFICATION_TYPES
from app.services.profile_facts_source import (
    existing_profile_facts_source,
    render_profile_facts_text,
    sync_profile_facts_source,
)
from app.utils.db_locks import acquire_hospital_advisory_lock_sync

ACTOR = "system:essence-autonomy-backfill"
RETIRED_OUTBOX_CODE = "RETIRED_NOTIFICATION_TYPE"
_OPEN_OUTBOX_STATES = ("PENDING", "RETRYING", "HOLD")
_RETIRED_INCIDENT_TYPES = (
    "ESSENCE_AUTO_REVIEW_ESCALATED",
    "ESSENCE_AUTO_REVIEW_FAILED",
    "ESSENCE_AUTO_REVIEW_COST_BLOCKED",
)


def _applied(db: Session) -> dict[uuid.UUID, HospitalContentPhilosophy]:
    rows = db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED
        )
    ).scalars()
    return {row.hospital_id: row for row in rows}


def _draft_counts(db: Session) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(HospitalContentPhilosophy.hospital_id, func.count())
        .where(HospitalContentPhilosophy.status == PhilosophyStatus.DRAFT)
        .group_by(HospitalContentPhilosophy.hospital_id)
    ).all()
    return {hospital_id: int(count) for hospital_id, count in rows}


def _profile_facts_action(db: Session, hospital: Hospital) -> str:
    rendered = render_profile_facts_text(hospital)
    existing = existing_profile_facts_source(db, hospital.id)
    if existing is None:
        return "CREATE" if rendered else "EMPTY"
    return "UNCHANGED" if existing.raw_text == rendered else "UPDATE"


def _open_retired_outbox(db: Session) -> list[NotificationOutbox]:
    return list(
        db.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.notification_type.in_(sorted(RETIRED_NOTIFICATION_TYPES)),
                NotificationOutbox.state.in_(_OPEN_OUTBOX_STATES),
            )
            .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
        )
        .scalars()
        .all()
    )


def _open_retired_incident_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Incident.incident_type, func.count())
        .where(
            Incident.incident_type.in_(_RETIRED_INCIDENT_TYPES),
            Incident.state.in_(
                (IncidentState.OPEN.value, IncidentState.RETRYING.value)
            ),
        )
        .group_by(Incident.incident_type)
    ).all()
    return {str(kind): int(count) for kind, count in rows}


def _line(out: TextIO, kind: str, payload: dict[str, Any]) -> None:
    out.write(f"{kind} " + json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _default_synthesizer() -> Callable[..., Any]:
    from app.workers.tasks import _cost_guarded_essence_synthesis

    return _cost_guarded_essence_synthesis


def _default_schedule(hospital_id: uuid.UUID) -> str:
    from app.workers.tasks import _ensure_initial_schedule_safe

    return _ensure_initial_schedule_safe(hospital_id, trigger="essence_auto_approval")


def run(
    db: Session,
    *,
    apply: bool,
    out: TextIO,
    ids: Sequence[uuid.UUID] | None = None,
    synthesizer: Callable[..., Any] | None = None,
    schedule: Callable[[uuid.UUID], str] | None = None,
) -> int:
    now = datetime.now(UTC)
    statement = select(Hospital).order_by(Hospital.name, Hospital.id)
    if ids:
        # 지정한 병원만 본다 — 동기화·적용·보고 모두 그 병원으로 좁힌다.
        statement = statement.where(Hospital.id.in_(list(ids)))
    hospitals = list(db.execute(statement).scalars())
    applied = _applied(db)
    drafts = _draft_counts(db)
    targets = [hospital for hospital in hospitals if ids or hospital.id not in applied]
    target_ids = {hospital.id for hospital in targets}
    for hospital in hospitals:
        current = applied.get(hospital.id)
        _line(
            out,
            "TARGET" if hospital.id in target_ids else "LIVE",
            {
                "hospital_id": str(hospital.id),
                "name": hospital.name,
                "status": getattr(hospital.status, "value", hospital.status),
                "applied_version": current.version if current else None,
                "applied_mode": (
                    ("AUTOMATIC" if is_automatic_essence(current) else "LEGACY")
                    if current
                    else None
                ),
                "draft_count": drafts.get(hospital.id, 0),
                "profile_facts": _profile_facts_action(db, hospital),
                # 지금(동기화 전) 다시 합성될 이유. LIVE 행이 null이면 판 번호가 그대로다.
                "trigger": essence_refresh_trigger(db, hospital.id, now=now),
            },
        )
    if ids:
        known = {hospital.id for hospital in hospitals}
        for hospital_id in ids:
            if hospital_id not in known:
                _line(out, "NOT_FOUND", {"hospital_id": str(hospital_id)})
    retired_rows = _open_retired_outbox(db)
    summary = {
        "apply": apply,
        "targets": len(targets),
        "hospitals": len(hospitals),
        "retired_outbox_open": len(retired_rows),
        "retired_incidents_open": _open_retired_incident_counts(db),
    }
    _line(out, "SUMMARY", summary)
    db.rollback()
    if not apply:
        return 0

    synced = 0
    for hospital in hospitals:
        acquire_hospital_advisory_lock_sync(db, hospital.id)
        result = sync_profile_facts_source(db, db.get(Hospital, hospital.id))
        if result.changed:
            db.commit()
            synced += 1
            _line(
                out,
                "PROFILE_FACTS",
                {
                    "hospital_id": str(hospital.id),
                    "status": result.status,
                    "notes_added": result.notes_added,
                    "notes_removed": result.notes_removed,
                },
            )
        else:
            db.rollback()

    synthesize = synthesizer or _default_synthesizer()
    make_schedule = schedule or _default_schedule
    applied_count = 0
    for hospital in targets:
        result = refresh_essence_snapshot(
            db,
            hospital.id,
            synthesizer=synthesize,
            claim_token=f"{ACTOR}:{uuid.uuid4()}",
        )
        detail: dict[str, Any] = {
            "hospital_id": str(hospital.id),
            "name": hospital.name,
            "status": result.status.value,
            "trigger": result.trigger,
            "reason": result.reason,
            "philosophy_id": str(result.philosophy_id) if result.philosophy_id else None,
            "fallback": result.fallback,
            "dropped": result.dropped_count,
        }
        if result.status == EssenceRefreshStatus.APPLIED and result.philosophy_id:
            applied_count += 1
            philosophy = db.get(HospitalContentPhilosophy, result.philosophy_id)
            metadata = dict(philosophy.synthesis_metadata or {}) if philosophy else {}
            detail.update(
                {
                    "version": philosophy.version if philosophy else None,
                    "must_use_mode": (metadata.get("must_use") or {}).get("mode"),
                    "conflicts": len(metadata.get("conflicts") or []),
                    "excluded_inputs": len(metadata.get("excluded_inputs") or []),
                    "input_sources": len(metadata.get("input_hashes") or {}),
                }
            )
            db.rollback()
            detail["initial_schedule"] = make_schedule(hospital.id)
        _line(out, "RESULT", detail)

    closed = 0
    observed = datetime.now(UTC)
    for row in _open_retired_outbox(db):
        changed = db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == row.id,
                NotificationOutbox.version == row.version,
                NotificationOutbox.state.in_(_OPEN_OUTBOX_STATES),
            )
            .values(
                state="FAILED",
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                safe_error_code=RETIRED_OUTBOX_CODE,
                safe_error_message="Phase 4(D33)로 더 이상 보내지 않는 알림 종류입니다.",
                version=NotificationOutbox.version + 1,
                updated_at=observed,
            )
        )
        closed += int(changed.rowcount or 0)
    db.commit()
    _line(
        out,
        "DONE",
        {
            "profile_facts_synced": synced,
            "applied": applied_count,
            "attempted": len(targets),
            "retired_outbox_closed": closed,
        },
    )
    return 0


def _parse_ids(value: str) -> list[uuid.UUID]:
    try:
        ids = [uuid.UUID(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"UUID가 아닌 값: {exc}") from exc
    if not ids:
        raise argparse.ArgumentTypeError("--ids가 비어 있습니다")
    return ids


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="동기화·적용·종결을 저장한다")
    parser.add_argument("--dry-run", action="store_true", help="기본 동작. 명시용")
    parser.add_argument(
        "--ids",
        type=_parse_ids,
        help=(
            "쉼표로 이은 병원 UUID — 지정한 병원만 자동 요약을 돌린다(다시 합성할 이유가 "
            "있을 때만 새 판이 생긴다)"
        ),
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply와 --dry-run은 함께 쓸 수 없습니다")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        return run(db, apply=args.apply, out=sys.stdout, ids=args.ids)


if __name__ == "__main__":
    raise SystemExit(main())
