"""Alembic-backed proofs for the forward repair after historical revision 0041."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0058_repair_monthly_delivery_drift.py"
)
LEGACY_UNIQUE = "uq_monthly_reports_hospital_period_type"
VERSIONED_UNIQUE = "uq_monthly_reports_period_version"


def _load_migration():
    spec = importlib.util.spec_from_file_location("monthly_delivery_repair", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target_contract(connection) -> tuple[bool, set[str]]:
    inspector = inspect(connection)
    columns = {
        column["name"]: column for column in inspector.get_columns("monthly_measurement_manifests")
    }
    uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("monthly_reports")
        if constraint.get("name") in {LEGACY_UNIQUE, VERSIONED_UNIQUE}
    }
    return not columns["closes_at"]["nullable"], uniques


@pytest.mark.parametrize(
    "has_versioned_unique",
    [False, True],
    ids=("missing-versioned-constraint", "observed-both-constraints"),
)
def test_0041_then_head_repair_matches_fresh_alembic_constraint_contract(
    pg_engine, has_versioned_unique: bool
) -> None:
    """Recreate the observed post-0041 drift, apply 0058, and compare it with head."""

    schema = f"monthly_repair_{uuid.uuid4().hex}"
    migration = _load_migration()

    with pg_engine.connect() as connection:
        try:
            # The integration database itself is provisioned by `alembic upgrade head`.
            connection.execute(text("SET search_path TO public"))
            fresh_contract = _target_contract(connection)
            assert fresh_contract == (True, {VERSIONED_UNIQUE})

            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(
                text(
                    """
                    CREATE TABLE monthly_measurement_manifests (
                        id uuid PRIMARY KEY,
                        hospital_id uuid NOT NULL,
                        period_year integer NOT NULL,
                        period_month integer NOT NULL,
                        configured_platforms jsonb NOT NULL,
                        platform_provenance jsonb NOT NULL,
                        frozen_at timestamptz NOT NULL DEFAULT now(),
                        closed_at timestamptz
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE monthly_reports (
                        id uuid PRIMARY KEY,
                        hospital_id uuid NOT NULL,
                        period_year integer NOT NULL,
                        period_month integer NOT NULL,
                        report_type varchar(20) NOT NULL,
                        version integer NOT NULL DEFAULT 1
                    )
                    """
                )
            )
            connection.execute(
                text(
                    f"ALTER TABLE monthly_reports ADD CONSTRAINT {LEGACY_UNIQUE} "
                    "UNIQUE (hospital_id, period_year, period_month, report_type)"
                )
            )
            if has_versioned_unique:
                connection.execute(
                    text(
                        f"ALTER TABLE monthly_reports ADD CONSTRAINT {VERSIONED_UNIQUE} "
                        "UNIQUE (hospital_id, period_year, period_month, report_type, version)"
                    )
                )
            manifest_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO monthly_measurement_manifests (
                        id, hospital_id, period_year, period_month,
                        configured_platforms, platform_provenance
                    ) VALUES (:id, :hospital_id, 2026, 8, '[]'::jsonb, '{}'::jsonb)
                    """
                ),
                {"id": manifest_id, "hospital_id": uuid.uuid4()},
            )

            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

            assert _target_contract(connection) == fresh_contract
            closes_at = connection.execute(
                text("SELECT closes_at FROM monthly_measurement_manifests WHERE id=:id"),
                {"id": manifest_id},
            ).scalar_one()
            assert closes_at == datetime(2026, 8, 31, 15, 15, tzinfo=timezone.utc)
        finally:
            connection.execute(text("RESET search_path"))
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.commit()


def test_repair_is_noop_on_fresh_from_head_database(pg_conn) -> None:
    migration = _load_migration()
    before = _target_contract(pg_conn)

    migration.op = Operations(MigrationContext.configure(pg_conn))
    migration.upgrade()

    assert _target_contract(pg_conn) == before == (True, {VERSIONED_UNIQUE})


def test_version_two_monthly_report_is_allowed_by_alembic_database(pg_conn) -> None:
    """This constraint claim runs on the migrated DB, never Base.metadata.create_all()."""

    hospital_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    pg_conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status) "
            "VALUES (:id, '월간 제약 테스트 병원', :slug, 'ONBOARDING')"
        ),
        {"id": hospital_id, "slug": f"monthly-version-{hospital_id.hex}"},
    )
    common = {
        "hospital_id": hospital_id,
        "period_year": 2026,
        "period_month": 8,
        "report_type": "MONTHLY",
        "quality": "BLOCKED",
        "planned_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "excluded_count": 0,
        "customer_ready": False,
        "delivery_blockers": "[]",
    }
    pg_conn.execute(
        text(
            """
            INSERT INTO monthly_reports (
                id, hospital_id, period_year, period_month, report_type, version,
                quality, planned_count, success_count, failed_count, excluded_count,
                customer_ready, delivery_blockers
            ) VALUES (
                :id, :hospital_id, :period_year, :period_month, :report_type, 1,
                :quality, :planned_count, :success_count, :failed_count, :excluded_count,
                :customer_ready, CAST(:delivery_blockers AS jsonb)
            )
            """
        ),
        {**common, "id": first_id},
    )
    pg_conn.execute(
        text(
            """
            INSERT INTO monthly_reports (
                id, hospital_id, period_year, period_month, report_type, version,
                supersedes_report_id, quality, planned_count, success_count, failed_count,
                excluded_count, customer_ready, delivery_blockers
            ) VALUES (
                :id, :hospital_id, :period_year, :period_month, :report_type, 2,
                :supersedes_report_id, :quality, :planned_count, :success_count, :failed_count,
                :excluded_count, :customer_ready, CAST(:delivery_blockers AS jsonb)
            )
            """
        ),
        {**common, "id": second_id, "supersedes_report_id": first_id},
    )

    versions = (
        pg_conn.execute(
            text(
                "SELECT version FROM monthly_reports "
                "WHERE hospital_id=:hospital_id ORDER BY version"
            ),
            {"hospital_id": hospital_id},
        )
        .scalars()
        .all()
    )
    assert versions == [1, 2]
