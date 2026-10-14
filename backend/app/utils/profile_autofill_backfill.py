"""일회성 운영 작업(Phase 4, D30): 기능 이전 병원의 프로필 출처 원장을 만들고 자동 입력을 적용한다.

병원마다 순서대로:

1. **출처 원장 백필** — 감사 로그 ``update_profile``의 사람 actor가 바꾼 필드는 OPERATOR(AUTOFILL·LEGACY
   기록보다 우선), 값은 있는데 기록이 없는 자동 입력 대상 필드는 LEGACY(멱등). 적용 서비스도 같은
   잠금 안에서 매번 이 복원부터 하므로, dry-run의 계획도 실제 적용과 같은 원장으로 나온다.
2. **초안이 있는 병원** — 마지막 PROFILE_AUTOFILL 초안으로 **적용만** 한다(재수집 없음, 비용 0).
   E4 이전 초안의 네이버 값은 확인되지 않은 것으로 본다(초안의 place id가 병원 행의 저장 id와 같을
   때만 예외). 이미 적용된 초안은 건너뛴다.
3. **초안이 없는 병원**(세종다온 등) — ``backfill`` 키로 수집+적용을 워커에 보낸다(유료 1회).

공개 중인 병원(ACTIVE·site_live)은 ``--include-live`` 없이는 **적용하지 않는다** — 수집만 하고(초안
저장) 적용 예정 필드를 출력한다. 빈 칸이 공개 페이지에 새로 나타나기 때문이다. 대표가 dry-run 결과를
본 뒤 ``--include-live --apply``로 적용한다. 일시정지 병원은 언제나 건너뛴다.

기본은 dry-run이다(아무것도 쓰지 않고 디스패치하지 않는다)::

    python -m app.utils.profile_autofill_backfill                      # 계획 JSON 한 줄씩 + 합계
    python -m app.utils.profile_autofill_backfill --apply              # 원장 백필·적용·수집 디스패치
    python -m app.utils.profile_autofill_backfill --ids=<uuid>,<uuid>  # 지정 병원만(반복 가능)
    python -m app.utils.profile_autofill_backfill --include-live --apply

운영에서는 migrate Job으로 돌린다(gcloud ``--args``는 쉼표로 나뉘므로 병원마다 ``--hospital``)::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.profile_autofill_backfill,--hospital=<uuid>
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import Any, TextIO

import anyio
from sqlalchemy import select

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun
from app.services import profile_provenance
from app.services.profile_autofill_apply import apply_autofill_draft
from app.utils.db_locks import acquire_hospital_advisory_lock

PROFILE_AUTOFILL_OPERATION = "PROFILE_AUTOFILL"
#: 이미 적용이 끝난 초안 — 다시 적용하지 않는다(사람이 그 뒤 비운 칸을 되살리지 않게).
_SETTLED_APPLY_STATUSES = frozenset({"APPLIED", "NOTHING_TO_APPLY"})


def _is_live(hospital: Hospital) -> bool:
    return hospital.status == HospitalStatus.ACTIVE and bool(hospital.site_live)


async def _latest_run(db, hospital_id: uuid.UUID) -> OperationRun | None:
    return await db.scalar(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == PROFILE_AUTOFILL_OPERATION,
        )
        .order_by(OperationRun.created_at.desc())
        .limit(1)
    )


async def _backfill_one(
    sessions: Callable[[], Any],
    hospital_id: uuid.UUID,
    *,
    apply: bool,
    include_live: bool,
    dispatch: Callable[..., bool],
) -> dict[str, Any]:
    async with sessions() as db:
        await acquire_hospital_advisory_lock(db, hospital_id)
        hospital = await db.get(Hospital, hospital_id, populate_existing=True)
        if hospital is None:
            return {"hospital_id": str(hospital_id), "action": "NOT_FOUND"}
        row: dict[str, Any] = {
            "hospital_id": str(hospital.id),
            "name": hospital.name,
            "status": getattr(hospital.status, "value", hospital.status),
            "live": _is_live(hospital),
            "profile_complete": bool(hospital.profile_complete),
        }
        if hospital.status == HospitalStatus.PAUSED:
            await db.rollback()
            return {**row, "action": "SKIPPED_PAUSED"}
        row["provenance_backfill"] = await profile_provenance.backfill_provenance_from_audit(
            db, hospital
        )
        if apply:
            await db.commit()
        else:
            await db.rollback()

    async with sessions() as db:
        run = await _latest_run(db, hospital_id)
        summary = dict(run.result_summary or {}) if run is not None else {}
        draft = summary.get("draft") if isinstance(summary.get("draft"), dict) else {}
        if run is None or not draft:
            if run is not None and not draft:
                # 빈 초안은 자동 입력 스윕이 1·3·7일 재시도로 다시 모은다.
                return {**row, "action": "EMPTY_DRAFT_LEFT_TO_RETRY_SWEEP", "run_id": str(run.id)}
            action = "DISPATCH_COLLECTION"
            if apply:
                ok = dispatch(hospital_id, "backfill", allow_live=include_live)
                action = "DISPATCHED_COLLECTION" if ok else "DISPATCH_FAILED"
            return {**row, "action": action, "apply_live": include_live or not row["live"]}
        run_id = run.id
        previous = summary.get("apply_status")
        if previous in _SETTLED_APPLY_STATUSES:
            return {
                **row,
                "action": "ALREADY_APPLIED",
                "run_id": str(run_id),
                "apply_status": previous,
            }
        # dry-run도 적용과 같은 트랜잭션에서 감사 로그로 원장을 복원한 뒤 계획하고, 끝에 되돌린다.
        outcome = await apply_autofill_draft(
            db,
            hospital_id,
            summary,
            run_id=run_id,
            allow_live=include_live,
            dry_run=not apply,
        )
    return {
        **row,
        "action": "APPLY_STORED_DRAFT" if apply else "WOULD_APPLY_STORED_DRAFT",
        "run_id": str(run_id),
        "naver_identity": summary.get("naver_identity", "LEGACY_DRAFT"),
        "apply_status": outcome.status,
        "applied_fields": outcome.applied_fields,
        "would_apply_fields": outcome.would_apply_fields,
        "skipped_operator_fields": outcome.skipped_operator_fields,
        "skipped_unverified_fields": outcome.skipped_unverified_fields,
        "dropped_fields": outcome.dropped_fields,
        "profile_conflicts": [item["field"] for item in outcome.profile_conflicts],
        "completion": outcome.completion,
    }


async def run_backfill(
    *,
    apply: bool,
    include_live: bool,
    hospital_ids: Sequence[uuid.UUID] | None,
    out: TextIO = sys.stdout,
    sessions: Callable[[], Any] | None = None,
    dispatch: Callable[..., bool] | None = None,
) -> dict[str, int]:
    if sessions is None:
        from app.core.database import get_async_sessionmaker

        sessions = get_async_sessionmaker()
    if dispatch is None:
        from app.workers.profile_autofill_tasks import dispatch_profile_autofill

        dispatch = dispatch_profile_autofill
    async with sessions() as db:
        stmt = select(Hospital.id).order_by(Hospital.created_at, Hospital.id)
        if hospital_ids:
            stmt = stmt.where(Hospital.id.in_(list(hospital_ids)))
        targets = list((await db.execute(stmt)).scalars().all())

    totals: dict[str, int] = {}
    for hospital_id in targets:
        row = await _backfill_one(
            sessions, hospital_id, apply=apply, include_live=include_live, dispatch=dispatch
        )
        totals[row["action"]] = totals.get(row["action"], 0) + 1
        print(json.dumps(row, ensure_ascii=False, default=str), file=out, flush=True)
    summary = {"mode": "apply" if apply else "dry-run", "include_live": include_live, **totals}
    print(json.dumps({"summary": summary}, ensure_ascii=False), file=out, flush=True)
    return totals


def _uuid_list(raw: str) -> list[uuid.UUID]:
    try:
        values = [uuid.UUID(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"UUID가 아닌 값: {exc}") from exc
    if not values:
        raise argparse.ArgumentTypeError("병원 id가 비어 있습니다")
    return values


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.utils.profile_autofill_backfill",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="쓰고 디스패치한다(없으면 dry-run).")
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="공개 중인 병원의 빈 칸에도 적용한다(대표가 dry-run을 본 뒤에만).",
    )
    parser.add_argument(
        "--ids",
        "--hospital",
        dest="ids",
        type=_uuid_list,
        action="append",
        default=None,
        help="대상 병원 UUID. 쉼표 목록이나 반복(--hospital=a --hospital=b).",
    )
    args = parser.parse_args(argv)
    args.ids = (
        list(dict.fromkeys(item for chunk in args.ids for item in chunk)) if args.ids else None
    )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    async def _run() -> None:
        await run_backfill(apply=args.apply, include_live=args.include_live, hospital_ids=args.ids)

    anyio.run(_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
