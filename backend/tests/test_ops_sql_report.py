"""읽기 전용 운영 조회 CLI는 쓰기 트랜잭션을 만들지 않는다(2026-10-11)."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from app.utils import ops_sql_report


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        self.statements.append(str(statement))
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: [{"n": 1, "label": "가"}])
        )

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_the_first_statement_makes_the_transaction_read_only_and_nothing_commits() -> None:
    db = _FakeSession()
    out = io.StringIO()

    ops_sql_report.run_report(db, ["q1", "q3"], out=out)

    assert db.statements[0] == "SET TRANSACTION READ ONLY"
    assert db.commits == 0
    assert db.rollbacks == 1
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["q"] for line in lines] == ["q1", "q1b", "q3", "q3b"]
    assert lines[0]["rows"] == [{"n": 1, "label": "가"}]
    assert "가" in out.getvalue()  # ensure_ascii=False


def test_a_failing_query_still_rolls_back() -> None:
    db = _FakeSession()

    def boom(statement):
        db.statements.append(str(statement))
        if len(db.statements) > 1:
            raise RuntimeError("query failed")
        return None

    db.execute = boom
    with pytest.raises(RuntimeError):
        ops_sql_report.run_report(db, ["q2"], out=io.StringIO())

    assert db.statements[0] == "SET TRANSACTION READ ONLY"
    assert db.commits == 0
    assert db.rollbacks == 1


def test_only_selects_named_queries_and_rejects_unknown_names() -> None:
    assert ops_sql_report.parse_args(["--only=q1,q6"]).only == ["q1", "q6"]
    assert ops_sql_report.parse_args([]).only == list(ops_sql_report.QUERIES)
    with pytest.raises(SystemExit):
        ops_sql_report.parse_args(["--only=q9"])


def test_every_query_is_a_read() -> None:
    for statements in ops_sql_report.QUERIES.values():
        for sql in statements:
            assert sql.lstrip().upper().startswith("SELECT")
