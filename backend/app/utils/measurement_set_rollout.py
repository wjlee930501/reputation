"""운영 작업: 월간 측정 코호트 병원을 측정 키워드 30개 세트로 옮긴다 (설계 §6·§8).

병원마다 결정적 초안(그룹 A~D, 고정 20 + 교체 10, 교체 후보)을 만들어 질문 행에 적용한다. 같은 문구의
옛 질문은 다시 쓰고(측정 이력 보존), 세트에서 빠지는 옛 질문은 측정에서만 빠진다(글 주제로 남는다).
이미 30개 방식인 병원은 건너뛴다 — 마케터가 고친 세트를 덮어쓰지 않는다(`--replace`로만 다시 만든다).

12/1 00:15 월간 측정 전에 실행한다. 그 시각 이후에 바꾸면 이미 얼린 이번 달 측정표는 그대로이고 바꾼
세트는 다음 달부터 쓰인다.

    python -m app.utils.measurement_set_rollout precheck                 # 대상·초안 요약 JSON 한 줄씩
    python -m app.utils.measurement_set_rollout apply --confirm          # 적용(병원마다 커밋)
    python -m app.utils.measurement_set_rollout apply --confirm --ids a,b --replace
    python -m app.utils.measurement_set_rollout show --ids a             # 초안 문장 전부
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from typing import Any, TextIO

from sqlalchemy import select

from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital, HospitalStatus
from app.services import measurement_set
from app.services.audit_log import write_audit_log
from app.services.measurement_set_apply import (
    DRAFT_ACTOR,
    MeasurementSetError,
    SetRow,
    apply_rows,
    draft_rows,
    load_targets,
    validate_rows,
)
from app.utils.db_locks import acquire_hospital_advisory_lock


def _emit(out: TextIO, payload: dict[str, Any]) -> None:
    out.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


async def _hospitals(db, ids: list[uuid.UUID] | None) -> list[Hospital]:
    stmt = select(Hospital).order_by(Hospital.created_at, Hospital.id)
    if ids:
        stmt = stmt.where(Hospital.id.in_(ids))
    else:
        stmt = stmt.where(
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.monthly_sov_cohort.is_(True),
        )
    return list((await db.execute(stmt)).scalars().all())


def _draft_set_rows(hospital: Hospital) -> list[SetRow]:
    return [SetRow(row.text, row.group, row.role) for row in draft_rows(hospital)]


def _counts(rows: Sequence[SetRow]) -> dict[str, Any]:
    return {
        group: {
            "fixed": sum(r.group == group and r.role == "FIXED" for r in rows),
            "rotating": sum(r.group == group and r.role == "ROTATING" for r in rows),
            "candidates": sum(r.group == group and r.role is None for r in rows),
        }
        for group in measurement_set.QUESTION_GROUPS
    }


async def run(
    command: str,
    *,
    ids: list[uuid.UUID] | None,
    replace: bool,
    confirm: bool,
    out: TextIO,
) -> int:
    if command == "apply" and not confirm:
        _emit(out, {"error": "apply는 --confirm이 필요합니다"})
        return 2
    session_factory = get_async_sessionmaker()
    failures = 0
    async with session_factory() as db:
        hospitals = await _hospitals(db, ids)
    for hospital in hospitals:
        async with session_factory() as db:
            hospital = await db.get(Hospital, hospital.id)
            targets = await load_targets(db, hospital.id)
            curated = measurement_set.uses_curated_set(targets)
            rows = _draft_set_rows(hospital)
            problems = validate_rows(hospital, rows)
            existing_names = {target.name for target in targets}
            base = {
                "hospital_id": str(hospital.id),
                "name": hospital.name,
                "curated_now": curated,
                "legacy_tracked": sum(bool(t.in_tracking_set) for t in targets if t.set_role is None),
                "draft": _counts(rows),
                "reused_texts": sum(row.text in existing_names for row in rows),
                "problems": problems,
            }
            if command == "show":
                _emit(
                    out,
                    {
                        **base,
                        "rows": [
                            {"group": r.group, "role": r.role, "text": r.text} for r in rows
                        ],
                    },
                )
                continue
            if command == "precheck":
                _emit(out, {**base, "action": "skip_curated" if curated and not replace else "apply"})
                continue
            if curated and not replace:
                _emit(out, {**base, "action": "skipped_curated"})
                continue
            await acquire_hospital_advisory_lock(db, hospital.id)
            try:
                summary = await apply_rows(db, hospital, rows, actor=DRAFT_ACTOR)
            except MeasurementSetError as exc:
                await db.rollback()
                failures += 1
                _emit(out, {**base, "action": "rejected", "problems": exc.problems})
                continue
            await write_audit_log(
                db,
                action="apply_measurement_set",
                hospital_id=hospital.id,
                actor=DRAFT_ACTOR,
                target_type="hospital",
                target_id=hospital.id,
                detail={"source": "draft", "replace": replace, **summary},
            )
            await db.commit()
            _emit(out, {**base, "action": "applied", "summary": summary})
    return 1 if failures else 0


def _ids(value: str) -> list[uuid.UUID]:
    try:
        parsed = [uuid.UUID(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"UUID가 아닌 값: {exc}") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("--ids가 비어 있습니다")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("precheck", "show", "apply"))
    parser.add_argument("--ids", type=_ids, default=None, help="병원 UUID(쉼표 구분)")
    parser.add_argument(
        "--replace", action="store_true", help="이미 30개 방식인 병원도 초안으로 다시 만든다"
    )
    parser.add_argument("--confirm", action="store_true", help="apply 실행 확인")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(
        run(
            args.command,
            ids=args.ids,
            replace=args.replace,
            confirm=args.confirm,
            out=sys.stdout,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
