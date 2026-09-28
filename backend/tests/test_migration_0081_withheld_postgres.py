"""0081 — WITHHELD enum 값 추가와 안전한 downgrade를 실제 PostgreSQL로 검증한다.

- upgrade는 행을 바꾸지 않고 enum 값만 더하며, 다시 실행해도 멱등하다.
- downgrade는 WITHHELD 행이 하나라도 남아 있으면 실패한다(옛 코드는 그 행을 읽지 못한다).
- 0건이면 enum 값을 남긴 채 아무것도 하지 않는다(PostgreSQL은 enum 값 제거가 없다).
"""

from __future__ import annotations

import importlib.util
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
_PREVIOUS = "0080_lead_diagnosis_supersede"
_REVISION = "0081_add_withheld_content_status"

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


def _migration_module():
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0081", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _labels(connection) -> set[str]:
    return set(
        connection.execute(
            text("SELECT enumlabel FROM pg_enum WHERE enumtypid = 'contentstatus'::regtype")
        ).scalars()
    )


def _version(connection) -> str:
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_0081_adds_withheld_and_refuses_to_downgrade_while_rows_use_it() -> None:
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

        with engine.connect() as connection:
            assert "WITHHELD" not in _labels(connection)
            # 롤백 검사 쿼리는 값이 없는 DB에서도 enum 변환 오류 없이 돈다(::text 비교).
            assert _migration_module()._withheld_row_counts(connection) == []

        upgraded = _alembic("upgrade", _REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert "WITHHELD" in _labels(connection)
            assert _version(connection) == _REVISION
            connection.execute(
                text("INSERT INTO hospitals (id, name, slug) VALUES (:id, '보존병원', :slug)"),
                {"id": hospital_id, "slug": f"withheld-{hospital_id.hex[:8]}"},
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
                    " scheduled_date, status, title, body, published_at) "
                    "VALUES (:id, :hid, :sid, 'FAQ', 1, 12, DATE '2026-09-10', 'WITHHELD', "
                    " '보존 글', '본문', now())"
                ),
                {"id": content_id, "hid": hospital_id, "sid": schedule_id},
            )

        refused = _alembic("downgrade", _PREVIOUS)
        assert refused.returncode != 0
        assert "public.content_items.status=1" in refused.stderr
        assert "restore" in refused.stderr
        with engine.connect() as connection:
            assert _version(connection) == _REVISION
            assert connection.execute(
                text("SELECT status::text FROM content_items WHERE id = :id"), {"id": content_id}
            ).scalar_one() == "WITHHELD"

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE content_items SET status = 'PUBLISHED' WHERE id = :id"),
                {"id": content_id},
            )
        downgraded = _alembic("downgrade", _PREVIOUS)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS
            # 의도한 no-op: 값은 남지만 어떤 행도 쓰지 않는다.
            assert "WITHHELD" in _labels(connection)

        # IF NOT EXISTS — 남은 값 위로 다시 올려도 멱등하다.
        again = _alembic("upgrade", "head")
        assert again.returncode == 0, again.stderr
        with engine.connect() as connection:
            assert _version(connection) == _REVISION
    finally:
        engine.dispose()
