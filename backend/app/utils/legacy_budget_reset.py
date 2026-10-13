"""일회성 운영 작업: 10/9 이전 슬롯의 레거시 생성 예산을 한 번 교체한다(2026-10-13 대표 승인 D22).

10/9 런타임 체크포인트가 슬롯별 평생 예산을 들였다. 그 전에 만들어진 빈 슬롯은 예산 기록이 없어
`LEGACY_SPEND_UNKNOWN`으로 멈췄고, 실패 기록이 남긴 계수 0과 `OPERATOR_REQUIRED` 억제 때문에 스윕이
다시 집지 않는다. Admin "레거시 예산 교체 후 다시 시도" 버튼을 23번 누르는 대신, 버튼과 같은 교체
함수(`replace_legacy_budget_and_release`)를 시스템 actor로 행마다 부른다. 교체가 억제를 풀므로
다음 복구 스윕이 슬롯을 다시 쓴다. 이 CLI는 작업을 큐에 넣지 않는다.

대상(모두 만족):

- 상태 DRAFT/READY, 한 번도 공개되지 않음(`first_published_at IS NULL`)
- 제목·본문이 비어 있음, 2026-10-09 16:00 UTC 이전 생성
- 저장된 시도 원인이 `LEGACY_SPEND_UNKNOWN`이거나 같은 글의 열린 사고 코드가 그 값
- 교체 함수가 받아들이는 상태(이미 교체한 행은 빠진다 — 두 번째 실행은 0건)

기본은 dry-run이다::

    python -m app.utils.legacy_budget_reset                       # 대상 행 JSON 한 줄씩 + 건수
    python -m app.utils.legacy_budget_reset --apply --reason "사유"  # 행마다 잠금·교체·감사·커밋

`--ids`(쉼표로 이은 글 UUID)는 지정한 글만 고른다 — 생성 시각·빈 제목/본문 조건 없이, 상태
DRAFT/READY·미공개·`LEGACY_SPEND_UNKNOWN` 근거·교체 함수 수락만 본다. 2026-10-13 교정 PASS가
시도 기록을 통째로 지워 예산을 잃은 본문 있는 슬롯(10/13 hotfix)의 복구용이며 actor·멱등 키
접두가 따로다.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.operations import Incident
from app.services.legacy_budget_replacement import (
    ACTIVE_LEGACY_INCIDENT_STATES,
    apply_legacy_budget_replacement_sync,
)
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    LEGACY_SPEND_UNKNOWN_CODE,
    legacy_budget_is_replaceable,
)

ACTOR = "system:legacy-budget-reset-20261013"
CREATED_BEFORE = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
_IDEMPOTENCY_PREFIX = "legacy-budget-reset-20261013"
IDS_ACTOR = "system:budget-loss-hotfix-20261013"
_IDS_IDEMPOTENCY_PREFIX = "budget-loss-hotfix-20261013"


def _empty(column):
    return or_(column.is_(None), func.btrim(column) == "")


def _legacy_incident(item_id_column, hospital_id_column):
    return exists().where(
        Incident.source_id == cast(item_id_column, String),
        Incident.hospital_id == hospital_id_column,
        Incident.safe_error_code == LEGACY_SPEND_UNKNOWN_CODE,
        Incident.state.in_(ACTIVE_LEGACY_INCIDENT_STATES),
    )


def candidate_query(ids: Sequence[uuid.UUID] | None = None):
    attempt_reason = ContentItem.essence_check_summary[(GENERATION_ATTEMPT_KEY, "reason")].as_string()
    legacy_incident = _legacy_incident(ContentItem.id, ContentItem.hospital_id)
    if ids:
        # 지정한 글만. 본문이 있는 10/13 예산 유실 슬롯이라 생성 시각·빈 본문 조건을 걸지 않는다.
        scope = (ContentItem.id.in_(list(ids)),)
    else:
        scope = (
            ContentItem.created_at < CREATED_BEFORE,
            _empty(ContentItem.title),
            _empty(ContentItem.body),
        )
    return (
        select(ContentItem, legacy_incident.label("legacy_incident_open"))
        .where(
            ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.READY)),
            ContentItem.first_published_at.is_(None),
            *scope,
            or_(attempt_reason == LEGACY_SPEND_UNKNOWN_CODE, legacy_incident),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.id)
    )


def _attempt(item: ContentItem) -> dict[str, Any]:
    summary = item.essence_check_summary if isinstance(item.essence_check_summary, dict) else {}
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    return dict(attempt) if isinstance(attempt, dict) else {}


def find_candidates(
    db: Session, ids: Sequence[uuid.UUID] | None = None
) -> list[tuple[ContentItem, bool]]:
    """술어를 만족하고 교체 함수가 받아들이는 행만 돌려준다."""

    rows = db.execute(candidate_query(ids)).all()
    return [
        (item, bool(incident_open))
        for item, incident_open in rows
        if legacy_budget_is_replaceable(_attempt(item), legacy_incident_open=bool(incident_open))
    ]


def _row_json(item: ContentItem, incident_open: bool) -> dict[str, Any]:
    attempt = _attempt(item)
    return {
        "content_id": str(item.id),
        "hospital_id": str(item.hospital_id),
        "scheduled_date": item.scheduled_date.isoformat() if item.scheduled_date else None,
        "status": getattr(item.status, "value", item.status),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "reason": attempt.get("reason"),
        "retry_class": attempt.get("retry_class"),
        "legacy_incident_open": incident_open,
    }


def run(
    db: Session,
    *,
    apply: bool,
    reason: str | None,
    out: TextIO,
    ids: Sequence[uuid.UUID] | None = None,
) -> int:
    candidates = find_candidates(db, ids)
    for item, incident_open in candidates:
        out.write(
            "CANDIDATE " + json.dumps(_row_json(item, incident_open), ensure_ascii=False) + "\n"
        )
    if ids:
        selected = {item.id for item, _ in candidates}
        for content_id in ids:
            if content_id not in selected:
                out.write("NOT_SELECTED " + json.dumps({"content_id": str(content_id)}) + "\n")
    out.write(json.dumps({"count": len(candidates), "apply": apply}) + "\n")
    if not apply:
        db.rollback()
        return 0
    targets = [item.id for item, _ in candidates]
    db.rollback()
    replaced = 0
    for content_id in targets:
        result = apply_legacy_budget_replacement_sync(
            db,
            content_id,
            actor=IDS_ACTOR if ids else ACTOR,
            reason=str(reason),
            idempotency_key=(
                f"{_IDS_IDEMPOTENCY_PREFIX if ids else _IDEMPOTENCY_PREFIX}:{content_id}"
            ),
        )
        if result.outcome == "REPLACED":
            db.commit()
            replaced += 1
        else:
            db.rollback()
        out.write(
            "RESULT "
            + json.dumps(
                {
                    "content_id": result.content_id,
                    "outcome": result.outcome,
                    "detail": result.detail,
                }
            )
            + "\n"
        )
    out.write(json.dumps({"replaced": replaced, "attempted": len(targets)}) + "\n")
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
    parser.add_argument("--apply", action="store_true", help="교체를 저장한다(기본은 dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="기본 동작. 명시용")
    parser.add_argument("--reason", help="--apply에 필요한 감사 사유")
    parser.add_argument(
        "--ids", type=_parse_ids, help="쉼표로 이은 글 UUID — 지정한 글만 고른다(10/13 hotfix)"
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply와 --dry-run은 함께 쓸 수 없습니다")
    if args.apply and not (args.reason or "").strip():
        parser.error("--apply에는 --reason이 필요합니다")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        return run(db, apply=args.apply, reason=args.reason, out=sys.stdout, ids=args.ids)


if __name__ == "__main__":
    raise SystemExit(main())
