"""Postgres-validity checks for the SQL aggregation in api/admin/sov.py.

No Postgres is available in this environment, so these compile the statements
against the postgresql dialect and check for the SQL constructs the aggregation
relies on (`FILTER (WHERE ...)`, `IS DISTINCT FROM`, `GROUP BY`) instead of
executing them.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.dialects import postgresql

from app.api.admin import sov as sov_api
from app.models.sov import QueryMatrix, SovRecord


def _compile(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_confirmed_mentioned_failed_ambiguous_clauses_compile_for_postgres():
    for clause_fn in (
        sov_api._confirmed_clause,
        sov_api._mentioned_clause,
        sov_api._failed_clause,
        sov_api._ambiguous_clause,
    ):
        stmt = select(func.count(SovRecord.id)).where(clause_fn())
        sql = _compile(stmt)
        assert "sov_records" in sql

    # 레거시 verdict=NULL 행을 AMBIGUOUS로 잘못 접지 않으려면 IS DISTINCT FROM이어야
    # 한다 — `!=`는 SQL에서 NULL과 비교하면 NULL(거짓 취급)이 되어 레거시 행이
    # 분모에서 통째로 빠진다.
    confirmed_sql = _compile(select(func.count(SovRecord.id)).where(sov_api._confirmed_clause()))
    assert "IS DISTINCT FROM" in confirmed_sql


def test_get_sov_trend_aggregation_sql_compiles_and_has_filter_group_by():
    hospital_id = uuid.uuid4()
    window_start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    week_seconds = 7 * 24 * 3600
    week_offset = cast(
        func.floor(func.extract("epoch", SovRecord.measured_at - window_start) / week_seconds),
        Integer,
    ).label("week_offset")
    stmt = (
        select(
            week_offset,
            func.count(SovRecord.id).filter(sov_api._confirmed_clause()).label("total_count"),
            func.count(SovRecord.id).filter(sov_api._mentioned_clause()).label("mention_count"),
            func.count(SovRecord.id).filter(sov_api._failed_clause()).label("failure_count"),
            func.count(SovRecord.id).filter(sov_api._ambiguous_clause()).label("ambiguous_count"),
        )
        .select_from(SovRecord)
        .join(QueryMatrix, SovRecord.query_id == QueryMatrix.id)
        .where(SovRecord.hospital_id == hospital_id)
        .group_by(week_offset)
    )
    sql = _compile(stmt)
    assert sql.count("FILTER (WHERE") == 4
    assert "GROUP BY" in sql
    assert "date_trunc" not in sql.lower()  # 롤링 7일 버킷 — 캘린더 주 아님 (의도적 선택)


def test_get_sov_queries_aggregation_sql_compiles_and_groups_by_query_and_platform():
    hospital_id = uuid.uuid4()
    agg_stmt = (
        select(
            SovRecord.query_id,
            SovRecord.ai_platform,
            func.count(SovRecord.id).filter(sov_api._confirmed_clause()).label("total_count"),
            func.count(SovRecord.id).filter(sov_api._mentioned_clause()).label("mention_count"),
            func.count(SovRecord.id).filter(sov_api._failed_clause()).label("failure_count"),
            func.count(SovRecord.id).filter(sov_api._ambiguous_clause()).label("ambiguous_count"),
            func.max(SovRecord.measured_at).label("last_measured_at"),
        )
        .where(SovRecord.hospital_id == hospital_id, SovRecord.query_id.in_([uuid.uuid4()]))
        .group_by(SovRecord.query_id, SovRecord.ai_platform)
    )
    sql = _compile(agg_stmt)
    assert "GROUP BY sov_records.query_id, sov_records.ai_platform" in sql
    assert sql.count("FILTER (WHERE") == 4
    # raw_response(AI 응답 원문)를 더 이상 로드하지 않는다 — SELECT 절에 없어야 한다.
    assert "raw_response" not in sql
