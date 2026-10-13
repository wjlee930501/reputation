"""운영 조회 Q1~Q6과 배포 전 확인 Q7이 실제 스키마(head)에서 오류 없이 돈다(2026-10-11)."""

from __future__ import annotations

import io
import json

from sqlalchemy.orm import Session

from app.utils import ops_sql_report


def test_all_named_queries_run_read_only_against_the_migrated_schema(pg_engine) -> None:
    out = io.StringIO()
    with Session(bind=pg_engine) as db:
        ops_sql_report.run_report(db, list(ops_sql_report.QUERIES), out=out)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["q"] for line in lines] == [
        "q1", "q1b", "q2", "q3", "q3b", "q4", "q5", "q6", "q7"
    ]
    assert all(isinstance(line["rows"], list) for line in lines)
    [outbox] = next(line["rows"] for line in lines if line["q"] == "q7")
    assert set(outbox) == {"outbox_rows", "open_notice_rows"}
