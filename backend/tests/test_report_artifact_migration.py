"""System doctor-artifact validation schema contract."""

import importlib.util
import os
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models.monthly_control import MonthlyReportArtifact

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0043_allow_system_report_artifact_validation.py"
)
_POSTGRES_URL = os.getenv(
    "TASK24_DATABASE_URL",
    "postgresql://reputation:reputation@localhost:5434/reputation_test",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("report_artifact_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_and_migration_share_closed_system_validation_rule(monkeypatch) -> None:
    migration = _load()
    created: list[str] = []
    monkeypatch.setattr(migration.op, "drop_constraint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda _name, _table, condition: created.append(str(condition)),
    )

    migration.upgrade()

    migration_rule = " ".join(created)
    model_rule = str(
        next(
            constraint.sqltext
            for constraint in MonthlyReportArtifact.__table__.constraints
            if constraint.name == "ck_monthly_artifact_validation"
        )
    )
    for rule in (migration_rule, model_rule):
        assert "validation_source" in rule
        assert "SYSTEM" in rule
        assert "validated_by_id IS NOT NULL" in rule


def test_downgrade_refuses_system_validations_before_schema_mutation(monkeypatch) -> None:
    migration = _load()
    mutations: list[str] = []

    class Bind:
        def scalar(self, _statement):
            return 1

    monkeypatch.setattr(migration.op, "get_bind", Bind)
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *_args, **_kwargs: mutations.append("drop"),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *_args, **_kwargs: mutations.append("create"),
    )

    with pytest.raises(RuntimeError, match="개발팀에 문의"):
        migration.downgrade()

    assert mutations == []


def test_postgres_rejects_unattributed_validation_and_accepts_system_source() -> None:
    engine = sa.create_engine(_POSTGRES_URL, future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    hospital_id = uuid.uuid4()
    report_id = uuid.uuid4()
    try:
        transaction = connection.begin()
        connection.execute(
            sa.text(
                "INSERT INTO hospitals (id,name,slug) VALUES (:hospital_id,:name,:slug)"
            ),
            {
                "hospital_id": hospital_id,
                "name": "검증 출처 제약 의원",
                "slug": f"artifact-source-{uuid.uuid4().hex}",
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO monthly_reports "
                "(id,hospital_id,period_year,period_month,report_type,version) "
                "VALUES (:report_id,:hospital_id,2026,7,'MONTHLY',1)"
            ),
            {"report_id": report_id, "hospital_id": hospital_id},
        )
        invalid_savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO monthly_report_artifacts "
                    "(id,report_id,audience,path,sha256,byte_size,validated,validated_at,"
                    "validation_metadata) VALUES "
                    "(:id,:report_id,'DOCTOR','gs://private/invalid.pdf',:sha,4096,true,now(),"
                    "CAST(:metadata AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "report_id": report_id,
                    "sha": "a" * 64,
                    "metadata": '{"validation_source":"MANUAL"}',
                },
            )
        invalid_savepoint.rollback()
        connection.execute(
            sa.text(
                "INSERT INTO monthly_report_artifacts "
                "(id,report_id,audience,path,sha256,byte_size,validated,validated_at,"
                "validation_metadata) VALUES "
                "(:id,:report_id,'DOCTOR','gs://private/system.pdf',:sha,4096,true,now(),"
                "CAST(:metadata AS jsonb))"
            ),
            {
                "id": uuid.uuid4(),
                "report_id": report_id,
                "sha": "b" * 64,
                "metadata": '{"validation_source":"SYSTEM"}',
            },
        )
        transaction.rollback()
    finally:
        connection.close()
        engine.dispose()
