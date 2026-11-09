"""마감된 달 언급 재판정 운영 명령 — 저장 답변을 지금 판정 규칙(규칙 대조)으로 다시 판정한다.

    python -m app.utils.monthly_mention_rejudge precheck --year 2026 --month 10 [--hospital-id ID]
    python -m app.utils.monthly_mention_rejudge execute  --year 2026 --month 10 [--hospital-id ID] --confirm

- precheck: 읽기 전용(세션 롤백, 쓰기·공급자 호출 없음). 병원마다 측정표 답변 수·확정 답변 수, 미언급 → 언급으로
  바뀔 답변 수, 경쟁 병원 판정이 바뀔 답변 수, 이미 지금 판정 정책인지 보인다.
- execute: 병원마다 한 트랜잭션으로 기록을 고치고 `REJUDGE_MONTH_MENTIONS` 실행 기록과 감사 로그를 남긴다.
  `--confirm`이 없으면 precheck와 같다. 공급자 호출이 없다(규칙 대조만).

다시 판정한 달의 보고서 숫자는 **새 버전을 만들어야** 반영된다(일반 재생성 `rebuild=true`) — 이 명령은 보고서를
만들지 않는다. 새 버전은 다시 전달해야 원장에게 닿는다(`services/month_mention_rejudge.py`).

종료 코드: 오류 없이 끝나면 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.services import month_mention_rejudge
from app.services.monthly_period import MonthlyPeriodError, require_closed_period


def _hospitals(db, ids: Sequence[str]) -> list[Hospital]:
    stmt = select(Hospital).order_by(Hospital.created_at, Hospital.id)
    if ids:
        stmt = stmt.where(Hospital.id.in_([uuid.UUID(value) for value in ids]))
    return list(db.execute(stmt).scalars())


def run(command: str, year: int, month: int, hospital_ids: Sequence[str], *, confirm: bool) -> int:
    try:
        require_closed_period(year, month, now=datetime.now(UTC))
    except MonthlyPeriodError as exc:
        print(f"BLOCKED {exc}")
        return 1
    apply = command == "execute" and confirm
    failed = False
    with SyncSessionLocal() as db:
        hospitals = _hospitals(db, hospital_ids)
        ids = [hospital.id for hospital in hospitals]
    for hospital_id in ids:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, hospital_id)
            if apply:
                try:
                    plan = month_mention_rejudge.execute(db, hospital, year, month)
                    db.commit()
                except Exception as exc:  # noqa: BLE001 — 한 병원의 실패가 나머지를 막지 않는다
                    db.rollback()
                    print("FAILED", json.dumps({"hospital_id": str(hospital_id), "error": type(exc).__name__}))
                    failed = True
                    continue
                status = "APPLIED"
            else:
                plan = month_mention_rejudge.build_plan(db, hospital, year, month)
                db.rollback()
                status = "PLAN"
            if plan.manifest_id is None:
                continue
            if plan.already_current:
                status = "ALREADY_CURRENT"
            print(status, json.dumps(plan.summary(), ensure_ascii=False))
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("precheck", "execute"):
        command = sub.add_parser(name)
        command.add_argument("--year", type=int, required=True)
        command.add_argument("--month", type=int, required=True)
        command.add_argument("--hospital-id", action="append", default=[])
        if name == "execute":
            command.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    return run(
        args.command,
        args.year,
        args.month,
        args.hospital_id,
        confirm=bool(getattr(args, "confirm", False)),
    )


if __name__ == "__main__":
    sys.exit(main())
