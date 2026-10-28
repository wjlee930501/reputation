"""Recover the production-stamped 0094 schema and rehearse the 0094 -> 0093 path."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "MIGRATION_UPGRADE_DATABASE_URL"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BEFORE = "0092_publish_without_references_or_verdict"
_OPERATING = "0094_add_hospital_operating_changes"
_HEAD = "0093_add_hospital_search_verification"
_SLUG = "sejongdaonsingyeonggwayiweon"
_NAVER_TOKEN = "bb6f7e8f49eb178c06769cb81c16174c8a07a69a"
_DAUM_PHRASE = (
    "#DaumWebMasterTool:"
    "ec0bfd6a69d68c57e1f24bfe8b935880a1f5d4d1cecb8c30e23ffab73ddb9c47:"
    "WoYFtMaR84eZ7MaX1Xjw9A=="
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


def _version(connection) -> str:
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_0094_restores_schema_and_existing_production_shape_upgrades_to_0093() -> None:
    parsed = make_url(_sync_url(_database_url()))
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "reputation_autonomy_migration"
    engine = create_engine(parsed)
    hospital_id = uuid.uuid4()

    try:
        try:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        except OperationalError as exc:
            fail_unreachable(_URL_ENV, exc)

        before = _alembic("upgrade", _BEFORE)
        assert before.returncode == 0, before.stderr

        upgraded = _alembic("upgrade", _OPERATING)
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as connection:
            assert _version(connection) == _OPERATING
            inspector = inspect(connection)
            assert "operating_policy_version" in {
                column["name"] for column in inspector.get_columns("hospitals")
            }
            assert set(inspector.get_table_names()) >= {
                "hospital_operating_changes",
                "operating_change_adjustments",
            }

            change_columns = {
                column["name"] for column in inspector.get_columns("hospital_operating_changes")
            }
            assert change_columns == {
                "id",
                "hospital_id",
                "version",
                "idempotency_key",
                "request_sha256",
                "request_text",
                "conversation_reference",
                "items",
                "requested_by_id",
                "requested_by_label",
                "effective_at",
                "operation_run_id",
                "created_at",
            }
            adjustment_columns = {
                column["name"]
                for column in inspector.get_columns("operating_change_adjustments")
            }
            assert adjustment_columns == {
                "id",
                "hospital_id",
                "change_id",
                "content_id",
                "source_revision",
                "before_snapshot",
                "desired_brief",
                "candidate",
                "state",
                "reason",
                "created_at",
                "updated_at",
            }
            assert {
                tuple(index["column_names"])
                for index in inspector.get_indexes("hospital_operating_changes")
                if not index["unique"]
            } >= {("hospital_id",)}
            assert {
                tuple(index["column_names"])
                for index in inspector.get_indexes("operating_change_adjustments")
                if not index["unique"]
            } >= {("hospital_id", "state")}

            connection.execute(
                text("INSERT INTO hospitals (id, name, slug) VALUES (:id, '세종다온', :slug)"),
                {"id": hospital_id, "slug": _SLUG},
            )
            connection.execute(
                text(
                    "ALTER TABLE hospitals "
                    "ADD COLUMN naver_site_verification VARCHAR(255) NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE hospitals SET naver_site_verification = :naver WHERE id = :id"
                ),
                {"id": hospital_id, "naver": _NAVER_TOKEN},
            )

        # Re-run 0094 against the already-present production schema. Every object must
        # remain intact, and Alembic must return the database to the production stamp.
        stamped_back = _alembic("stamp", _BEFORE)
        assert stamped_back.returncode == 0, stamped_back.stderr
        rerun = _alembic("upgrade", _OPERATING)
        assert rerun.returncode == 0, rerun.stderr

        head = _alembic("upgrade", "head")
        assert head.returncode == 0, head.stderr
        with engine.connect() as connection:
            assert _version(connection) == _HEAD
            seeded = connection.execute(
                text(
                    "SELECT naver_site_verification, daum_webmaster_verification "
                    "FROM hospitals WHERE id = :id"
                ),
                {"id": hospital_id},
            ).one()
            assert seeded == (_NAVER_TOKEN, _DAUM_PHRASE)
    finally:
        engine.dispose()
