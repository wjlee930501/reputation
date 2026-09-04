"""Static contract for the separated manifest supersede/recovery guards."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0064_monthly_manifest_recovery_guard.py"
)
SUPERSEDE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0062_allow_monthly_manifest_protocol_supersede.py"
)


def _load(path: Path = MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location("monthly_manifest_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_guard_migration_is_linear_and_separates_recovery(monkeypatch):
    migration = _load()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))

    migration.upgrade()

    sql = " ".join(statements)
    assert migration.down_revision == "0063_add_monthly_sov_cohort"
    assert "app.monthly_manifest_supersede" in sql
    assert "app.monthly_manifest_recovery" in sql
    assert "OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL" in sql
    assert "monthly manifest recovery may only clear close" in sql
    assert "closed monthly manifest cannot be superseded" in sql
    assert "monthly manifest supersede may only replace freeze" in sql


def test_attempt_delete_bypass_remains_supersede_only(monkeypatch):
    migration = _load(SUPERSEDE_MIGRATION_PATH)
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))

    migration.upgrade()

    sql = " ".join(statements)
    assert "TG_OP = 'DELETE'" in sql
    assert "app.monthly_manifest_supersede" in sql
    assert "app.monthly_manifest_recovery" not in sql
    assert "monthly attempts are append-only" in sql
