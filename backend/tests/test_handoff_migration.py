import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0040_add_hospital_handoffs.py"
TIER_MIGRATION_PATH = VERSIONS_DIR / "0039_update_content_plan_tiers.py"
PINNED_TIER_MIGRATION_SHA256 = (
    "bde3040110aeec0d59467b1c20f0aada0edd11a0ec7f522b19e6e4f9410fe908"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("handoff_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_follows_hash_pinned_tier_revision() -> None:
    # Given: the authoritative unmodified 0039 and the new migration
    migration = _load_migration()

    # When: lineage and the owned predecessor hash are read
    predecessor_hash = hashlib.sha256(TIER_MIGRATION_PATH.read_bytes()).hexdigest()

    # Then: 0040 follows exactly the pinned tier revision
    assert migration.down_revision == "0039_update_content_plan_tiers"
    assert predecessor_hash == PINNED_TIER_MIGRATION_SHA256


def test_upgrade_declares_one_to_one_checked_handoff_and_legacy_backfill(monkeypatch) -> None:
    # Given: Alembic operations captured without mutating a database
    migration = _load_migration()
    created: list[tuple[str, tuple[object, ...]]] = []
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *items: created.append((name, items)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )

    # When: schema 0040 upgrades
    migration.upgrade()

    # Then: the table and legacy-only accepted projection are explicit
    assert len(created) == 1
    table_name, items = created[0]
    assert table_name == "hospital_handoffs"
    columns = {item.name: item for item in items if isinstance(item, sa.Column)}
    constraints = [item for item in items if isinstance(item, sa.Constraint)]
    assert set(columns) == {
        "id",
        "hospital_id",
        "state",
        "sales_owner_id",
        "ae_owner_id",
        "contract_reference",
        "contract_effective_at",
        "plan",
        "sla_due_at",
        "accepted_by_id",
        "accepted_at",
        "acceptance_source",
        "version",
        "created_at",
        "updated_at",
    }
    assert columns["version"].nullable is False
    assert any(isinstance(item, sa.UniqueConstraint) for item in constraints)
    check_sql = " ".join(
        str(item.sqltext) for item in constraints if isinstance(item, sa.CheckConstraint)
    )
    assert all(
        token in check_sql
        for token in (
            "CONTRACT_PENDING",
            "CONTRACTED",
            "HANDOFF_ACCEPTED",
            "LEGACY_BACKFILL",
            "sales_owner_id IS NOT NULL",
            "contract_effective_at IS NOT NULL",
            "plan IN ('PLAN_12', 'PLAN_16', 'PLAN_20')",
            "sla_due_at IS NOT NULL",
            "version >= 1",
        )
    )
    upgrade_sql = " ".join(statements)
    assert "INSERT INTO hospital_handoffs" in upgrade_sql
    assert "SELECT id, id, 'HANDOFF_ACCEPTED'" in upgrade_sql
    assert "'LEGACY_BACKFILL'" in upgrade_sql
    assert "UPDATE hospitals" not in upgrade_sql
    assert "CREATE TRIGGER" in upgrade_sql
    foreign_keys = [item for item in constraints if isinstance(item, sa.ForeignKeyConstraint)]
    assert sum("admin_users.id" in str(item.elements[0].target_fullname) for item in foreign_keys) == 3


def test_downgrade_removes_only_handoff_artifacts(monkeypatch) -> None:
    # Given: a captured downgrade operation stream
    migration = _load_migration()
    statements: list[str] = []
    dropped_tables: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )
    monkeypatch.setattr(migration.op, "drop_table", dropped_tables.append)

    # When: schema 0040 downgrades
    migration.downgrade()

    # Then: only the new trigger/function/table are removed
    assert dropped_tables == ["hospital_handoffs"]
    assert len(statements) == 2
    assert "hospital_handoffs" in statements[0]
    assert "prevent_hospital_handoff_acceptance_change" in statements[1]
