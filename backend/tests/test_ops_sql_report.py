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
        self.events: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        self.statements.append(str(statement))
        self.events.append(str(statement))
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: [{"n": 1, "label": "가"}])
        )

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("ROLLBACK")


def test_the_first_statement_makes_the_transaction_read_only_and_nothing_commits() -> None:
    db = _FakeSession()
    out = io.StringIO()

    ops_sql_report.run_report(db, ["q1", "q3"], out=out)

    # 이미 열린 트랜잭션이 있어도 새로 시작한 트랜잭션의 첫 문장이 READ ONLY다.
    assert db.events[:2] == ["ROLLBACK", "SET TRANSACTION READ ONLY"]
    assert db.events[-1] == "ROLLBACK"
    assert db.commits == 0
    assert db.rollbacks == 2
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
    assert db.events[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], list(ops_sql_report.QUERIES)),
        (["--only=q1,q6"], ["q1", "q6"]),
        # gcloud `--args`는 쉼표로 나뉘므로 반복 인자로 넘긴다.
        (["--only=q1", "--only=q3"], ["q1", "q3"]),
        (["--only=q3", "--only=q1,q3"], ["q3", "q1"]),
    ],
)
def test_only_accepts_repeats_and_comma_lists(argv, expected) -> None:
    assert ops_sql_report.parse_args(argv).only == expected


@pytest.mark.parametrize("argv", [["--only=q99"], ["--only=q1", "--only=q99"], ["--only="]])
def test_unknown_query_names_are_rejected(argv) -> None:
    with pytest.raises(SystemExit):
        ops_sql_report.parse_args(argv)


def test_the_docstring_example_uses_one_token_per_gcloud_arg() -> None:
    assert "--args=python,-m,app.utils.ops_sql_report,--only=q1,--only=q3" in (
        ops_sql_report.__doc__ or ""
    )


def test_every_query_is_a_read() -> None:
    for statements in ops_sql_report.QUERIES.values():
        for sql in statements:
            assert sql.lstrip().upper().startswith("SELECT")


def test_gate_measurement_queries_are_registered_read_only_selects() -> None:
    """E5(2026-10-13): 사후 검수 FLAGGED 분포(q9)와 생성 거절 사유(q10)."""

    assert [len(ops_sql_report.QUERIES[name]) for name in ("q9", "q10")] == [2, 2]
    for name in ("q9", "q10"):
        for sql in ops_sql_report.QUERIES[name]:
            assert sql.strip().upper().startswith("SELECT")
    assert "structured_findings" in ops_sql_report.QUERIES["q9"][0]
    assert "rejection_kind" in ops_sql_report.QUERIES["q10"][0]
    assert "generation_rejections" in ops_sql_report.QUERIES["q10"][1]
    assert ops_sql_report.parse_args(["--only=q9", "--only=q10"]).only == ["q9", "q10"]


def test_fallback_narrative_query_is_a_registered_read() -> None:
    """0090(2026-10-13): 범용 서술 각도 글의 검수 hash·승인 판 상태."""

    [sql] = ops_sql_report.QUERIES["q11"]
    assert sql.strip().upper().startswith("SELECT")
    assert "'fallback'" in sql
    assert "candidate_sha256" in sql
    assert ops_sql_report.parse_args(["--only=q11"]).only == ["q11"]


def test_full_automatic_impact_query_lists_newly_publishable_and_visible_posts() -> None:
    """0091(2026-10-14 D36·D37): 배포 전 영향 — 참고자료 0개·검수 장애로 보류된 발행 전 글과
    공개 표면에서 숨은 공개 글."""

    drafts, deferred, published, backlog = ops_sql_report.QUERIES["q16"]
    assert "post_publish_critical_alert" in backlog and "FLAGGED" in backlog
    for sql in (drafts, deferred, published, backlog):
        assert sql.strip().upper().startswith("SELECT")
    assert "jsonb_array_length(c.references_list) = 0" in drafts
    assert "c.status IN ('DRAFT', 'READY')" in drafts
    assert "'UNAVAILABLE'" in deferred
    assert "'PROVIDER_ERROR', 'INVALID_RESPONSE'" in deferred
    assert "c.status = 'PUBLISHED'" in published
    assert ops_sql_report.parse_args(["--only=q16"]).only == ["q16"]
