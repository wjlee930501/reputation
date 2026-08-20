from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0050_add_domain_cert_job_identity.py"
)


def _load() -> ModuleType:
    assert MIGRATION_PATH.exists(), "0050 domain certificate identity migration is missing"
    spec = importlib.util.spec_from_file_location("domain_cert_job_identity", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_job_identity_and_releases_legacy_issuing_rows(monkeypatch) -> None:
    migration = _load()
    columns: list[tuple[str, str, int | None]] = []
    statements: list[str] = []

    def record_column(table: str, column) -> None:
        columns.append((table, column.name, getattr(column.type, "length", None)))

    monkeypatch.setattr(migration.op, "add_column", record_column)
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))

    migration.upgrade()

    assert migration.down_revision == "0049_add_domain_cert_job_tracking"
    assert columns == [
        ("hospitals", "domain_cert_job_token", 36),
        ("hospitals", "domain_cert_job_domain", 200),
    ]
    assert len(statements) == 1
    normalized = " ".join(statements[0].split())
    assert "domain_cert_job_state = 'FAILED'" in normalized
    assert "domain_cert_job_started_at = NULL" in normalized
    assert "domain_cert_job_token = NULL" in normalized
    assert "domain_cert_job_domain = aeo_domain" in normalized
    assert "WHERE domain_cert_job_state = 'ISSUING'" in normalized
