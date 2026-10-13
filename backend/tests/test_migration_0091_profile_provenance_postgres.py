"""0091 — 프로필 출처 원장·Essence 합성 메타데이터 컬럼을 실제 PostgreSQL로 검증한다.

- upgrade는 NOT NULL DEFAULT '{}' JSONB 컬럼 두 개만 더한다. 기존 병원·판 행은 빈 객체가 된다.
- 다시 올려도 멱등하다.
- downgrade는 두 컬럼만 지우고 병원 프로필 값과 판은 그대로 둔다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "MIGRATION_UPGRADE_DATABASE_URL"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PREVIOUS = "0090_allow_fallback_treatment_narrative"
_REVISION = "0091_add_profile_provenance_and_essence_metadata"
_COLUMNS = (
    ("hospitals", "profile_field_provenance"),
    ("hospital_content_philosophies", "synthesis_metadata"),
)


def _database_url() -> str:
    return require_db_url(_URL_ENV)


def _sync_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


def _async_url(value: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+asyncpg://" + value[len(prefix) :]
    return value


def _alembic(command: str, revision: str) -> subprocess.CompletedProcess:
    database_url = _database_url()
    env = os.environ.copy()
    env["DATABASE_URL"] = _async_url(database_url)
    env["SYNC_DATABASE_URL"] = _sync_url(database_url)
    return subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _column(connection, table: str, column: str) -> tuple[str, str, str | None] | None:
    row = connection.execute(
        text(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).one_or_none()
    return (row[0], row[1], row[2]) if row is not None else None


def _version(connection) -> str:
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_0091_adds_default_empty_jsonb_columns_and_downgrades_cleanly() -> None:
    parsed = make_url(_sync_url(_database_url()))
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "reputation_autonomy_migration"
    engine = create_engine(parsed)
    hospital_id = uuid.uuid4()
    philosophy_id = uuid.uuid4()

    try:
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        except OperationalError as exc:
            fail_unreachable(_URL_ENV, exc)
        previous = _alembic("upgrade", _PREVIOUS)
        assert previous.returncode == 0, previous.stderr

        with engine.begin() as connection:
            for table, column in _COLUMNS:
                assert _column(connection, table, column) is None
            connection.execute(
                text(
                    "INSERT INTO hospitals (id, name, slug, director_name) "
                    "VALUES (:id, '출처원장병원', :slug, '김원장')"
                ),
                {"id": hospital_id, "slug": f"prov-{hospital_id.hex[:8]}"},
            )
            connection.execute(
                text(
                    "INSERT INTO hospital_content_philosophies (id, hospital_id, version, status) "
                    "VALUES (:id, :hid, 1, 'APPROVED')"
                ),
                {"id": philosophy_id, "hid": hospital_id},
            )

        upgraded = _alembic("upgrade", _REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert _version(connection) == _REVISION
            for table, column in _COLUMNS:
                data_type, nullable, default = _column(connection, table, column)
                assert (data_type, nullable) == ("jsonb", "NO")
                assert default is not None and "'{}'" in default
            # 기존 행은 빈 객체 — 옛 코드·새 코드 모두 "기록 없음"으로 읽는다.
            assert connection.execute(
                text("SELECT profile_field_provenance FROM hospitals WHERE id = :id"),
                {"id": hospital_id},
            ).scalar_one() == {}
            assert connection.execute(
                text("SELECT synthesis_metadata FROM hospital_content_philosophies WHERE id = :id"),
                {"id": philosophy_id},
            ).scalar_one() == {}
            # 옛 코드의 INSERT(컬럼을 모름)도 서버 기본값으로 통과한다.
            other_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO hospitals (id, name, slug) VALUES (:id, '옛코드병원', :slug)"),
                {"id": other_id, "slug": f"prov-{other_id.hex[:8]}"},
            )
            connection.execute(
                text(
                    "UPDATE hospitals SET profile_field_provenance = "
                    "'{\"address\": {\"origin\": \"OPERATOR\"}}'::jsonb WHERE id = :id"
                ),
                {"id": hospital_id},
            )

        again = _alembic("upgrade", _REVISION)
        assert again.returncode == 0, again.stderr

        downgraded = _alembic("downgrade", _PREVIOUS)
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS
            for table, column in _COLUMNS:
                assert _column(connection, table, column) is None
            assert connection.execute(
                text("SELECT director_name FROM hospitals WHERE id = :id"), {"id": hospital_id}
            ).scalar_one() == "김원장"
            assert connection.execute(
                text("SELECT status::text FROM hospital_content_philosophies WHERE id = :id"),
                {"id": philosophy_id},
            ).scalar_one() == "APPROVED"

        reupgraded = _alembic("upgrade", _REVISION)
        assert reupgraded.returncode == 0, reupgraded.stderr
    finally:
        engine.dispose()
