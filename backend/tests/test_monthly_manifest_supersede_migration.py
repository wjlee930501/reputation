"""Static contract for the narrowly guarded monthly-manifest supersede migration."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0062_allow_monthly_manifest_protocol_supersede.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("monthly_manifest_supersede", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supersede_migration_is_linear_and_transaction_guarded(monkeypatch):
    migration = _load()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))

    migration.upgrade()

    sql = " ".join(statements)
    assert migration.down_revision == "0061_allow_system_incident_acknowledgement"
    assert "app.monthly_manifest_supersede" in sql
    assert "TG_OP = 'DELETE'" in sql
    assert "TG_OP <> 'INSERT'" in sql
    assert "monthly attempts are append-only" in sql
