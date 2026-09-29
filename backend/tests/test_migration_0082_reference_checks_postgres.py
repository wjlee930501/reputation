"""0082 — content_items.reference_checks 추가와 안전한 downgrade를 실제 PostgreSQL로 검증한다.

- upgrade는 NULL 허용 JSONB 컬럼 하나만 더하고 기존 행을 바꾸지 않는다. 다시 실행해도 멱등하다.
- 검증 기록이 든 행이 있어도 downgrade는 컬럼만 지운다(파생 데이터 — 다시 검증하면 복원된다).
  다른 컬럼·행은 그대로 남는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_URL = os.getenv("MIGRATION_UPGRADE_DATABASE_URL")
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS = "0081_add_withheld_content_status"
_REVISION = "0082_add_content_reference_checks"

pytestmark = pytest.mark.skipif(
    not _URL, reason="MIGRATION_UPGRADE_DATABASE_URL is not configured"
)


def _sync_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


def _async_url(value: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+asyncpg://" + value[len(prefix) :]
    return value


def _alembic(command: str, revision: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = _async_url(_URL)
    env["SYNC_DATABASE_URL"] = _sync_url(_URL)
    return subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _column(connection) -> tuple[str, str] | None:
    row = connection.execute(
        text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'content_items' "
            "AND column_name = 'reference_checks'"
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


def _version(connection) -> str:
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_0082_adds_nullable_reference_checks_and_downgrades_cleanly() -> None:
    assert _URL is not None
    parsed = make_url(_sync_url(_URL))
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "reputation_autonomy_migration"
    engine = create_engine(parsed)
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    content_id = uuid.uuid4()

    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        assert _alembic("upgrade", _PREVIOUS).returncode == 0

        with engine.begin() as connection:
            assert _column(connection) is None
            connection.execute(
                text("INSERT INTO hospitals (id, name, slug) VALUES (:id, '검증병원', :slug)"),
                {"id": hospital_id, "slug": f"refcheck-{hospital_id.hex[:8]}"},
            )
            connection.execute(
                text(
                    "INSERT INTO content_schedules "
                    "(id, hospital_id, plan, publish_days, active_from) "
                    "VALUES (:id, :hid, 'PLAN_12', '[1]'::json, DATE '2026-09-01')"
                ),
                {"id": schedule_id, "hid": hospital_id},
            )
            connection.execute(
                text(
                    "INSERT INTO content_items "
                    "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
                    " scheduled_date, status, title, body, references_list) "
                    "VALUES (:id, :hid, :sid, 'FAQ', 1, 12, DATE '2026-09-30', 'DRAFT', "
                    " '검증 글', '본문', "
                    " '[{\"title\": \"치핵\", \"url\": \"https://health.kdca.go.kr/x\"}]'::jsonb)"
                ),
                {"id": content_id, "hid": hospital_id, "sid": schedule_id},
            )

        upgraded = _alembic("upgrade", _REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert _version(connection) == _REVISION
            assert _column(connection) == ("jsonb", "YES")
            # 기존 행은 건드리지 않는다 — "아직 검증 기록 없음".
            assert connection.execute(
                text("SELECT reference_checks FROM content_items WHERE id = :id"),
                {"id": content_id},
            ).scalar_one() is None
            connection.execute(
                text(
                    "UPDATE content_items SET reference_checks = "
                    "'[{\"url\": \"https://health.kdca.go.kr/x\", \"verdict\": \"pass\"}]'::jsonb "
                    "WHERE id = :id"
                ),
                {"id": content_id},
            )

        # 멱등 — 이미 적용된 상태에서 다시 올려도 오류가 없다(head는 움직이므로 이름으로 올린다).
        again = _alembic("upgrade", _REVISION)
        assert again.returncode == 0, again.stderr

        downgraded = _alembic("downgrade", _PREVIOUS)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS
            assert _column(connection) is None
            # 컬럼만 사라지고 글과 참고자료는 그대로다.
            assert connection.execute(
                text("SELECT references_list->0->>'url' FROM content_items WHERE id = :id"),
                {"id": content_id},
            ).scalar_one() == "https://health.kdca.go.kr/x"

        reupgraded = _alembic("upgrade", _REVISION)
        assert reupgraded.returncode == 0, reupgraded.stderr
        with engine.connect() as connection:
            assert _column(connection) == ("jsonb", "YES")
    finally:
        engine.dispose()
