"""Native-Postgres behavior contract for migration 0039's plan-tier transition."""

import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0039_update_content_plan_tiers.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("plan_tier_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_tier_migration_upgrades_plan_8_and_only_supports_legacy_downgrade(pg_engine) -> None:
    """Given legacy plans, when 0039 runs, then PLAN_8 becomes PLAN_12 and downgrade stays roll-forward-only.

    The migration deliberately cannot distinguish an original PLAN_12 from one converted from PLAN_8,
    and PostgreSQL retains PLAN_20 in the enum. Do not downgrade past 0039 after tier writes.
    """
    schema_name = f"plan_tier_{uuid.uuid4().hex}"
    migration = _load_migration()

    with pg_engine.connect() as connection:
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.execute(text(f'SET search_path TO "{schema_name}"'))
            connection.execute(text("CREATE TYPE plan AS ENUM ('PLAN_16', 'PLAN_12', 'PLAN_8')"))
            connection.execute(text("CREATE TABLE hospitals (id integer primary key, plan plan not null)"))
            connection.execute(
                text("CREATE TABLE content_schedules (id integer primary key, plan plan not null)")
            )
            connection.execute(text("INSERT INTO hospitals VALUES (1, 'PLAN_8'), (2, 'PLAN_12')"))
            connection.execute(
                text("INSERT INTO content_schedules VALUES (1, 'PLAN_8'), (2, 'PLAN_12')")
            )
            connection.commit()

            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.commit()

            assert connection.execute(text("SELECT plan::text FROM hospitals ORDER BY id")).scalars().all() == [
                "PLAN_12",
                "PLAN_12",
            ]
            assert connection.execute(
                text("SELECT plan::text FROM content_schedules ORDER BY id")
            ).scalars().all() == ["PLAN_12", "PLAN_12"]

            connection.execute(text("INSERT INTO hospitals VALUES (3, 'PLAN_20')"))
            connection.execute(text("INSERT INTO content_schedules VALUES (3, 'PLAN_20')"))
            connection.commit()

            migration.downgrade()
            connection.commit()

            assert connection.execute(text("SELECT plan::text FROM hospitals ORDER BY id")).scalars().all() == [
                "PLAN_12",
                "PLAN_12",
                "PLAN_16",
            ]
            assert connection.execute(
                text("SELECT plan::text FROM content_schedules ORDER BY id")
            ).scalars().all() == ["PLAN_12", "PLAN_12", "PLAN_16"]
            assert connection.execute(
                text("SELECT enum_range(NULL::plan)::text")
            ).scalar_one() == "{PLAN_16,PLAN_12,PLAN_8,PLAN_20}"
        finally:
            connection.execute(text("RESET search_path"))
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            connection.commit()
