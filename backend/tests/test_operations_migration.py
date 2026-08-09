import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0042_add_operations_control_plane.py"
TIER_MIGRATION_PATH = VERSIONS_DIR / "0039_update_content_plan_tiers.py"
PINNED_TIER_SHA256 = (
    "bde3040110aeec0d59467b1c20f0aada0edd11a0ec7f522b19e6e4f9410fe908"
)


def _load() -> ModuleType:
    assert MIGRATION_PATH.exists(), "0042 operations-control migration is required"
    spec = importlib.util.spec_from_file_location("operations_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self) -> None:
        self.tables: dict[str, tuple[object, ...]] = {}
        self.indexes: list[tuple[str, str, tuple[str, ...], dict[str, object]]] = []
        self.dropped_indexes: list[tuple[str, str | None]] = []
        self.dropped_tables: list[str] = []

    def install(self, monkeypatch, migration: ModuleType) -> None:
        monkeypatch.setattr(
            migration.op,
            "create_table",
            lambda name, *items: self.tables.setdefault(name, items),
        )
        monkeypatch.setattr(
            migration.op,
            "create_index",
            lambda name, table, columns, **kwargs: self.indexes.append(
                (name, table, tuple(columns), kwargs)
            ),
        )
        monkeypatch.setattr(
            migration.op,
            "drop_index",
            lambda name, table_name=None: self.dropped_indexes.append((name, table_name)),
        )
        monkeypatch.setattr(migration.op, "drop_table", self.dropped_tables.append)


def test_0042_lineage_and_authoritative_tier_pin() -> None:
    # Given: the new migration and immutable tier predecessor
    migration = _load()

    # When: lineage and predecessor bytes are inspected
    predecessor_hash = hashlib.sha256(TIER_MIGRATION_PATH.read_bytes()).hexdigest()

    # Then: the migration follows 0041 without changing 0039
    assert migration.down_revision == "0041_add_monthly_delivery_control"
    assert predecessor_hash == PINNED_TIER_SHA256


def test_upgrade_declares_minimal_control_plane_contract(monkeypatch) -> None:
    # Given: Alembic operations captured without a database mutation
    migration = _load()
    recorder = Recorder()
    recorder.install(monkeypatch, migration)

    # When: 0042 upgrades
    migration.upgrade()

    # Then: only the three durable control-plane tables are created
    assert set(recorder.tables) == {"operation_runs", "incidents", "notification_outbox"}
    checks = " ".join(
        str(item.sqltext)
        for items in recorder.tables.values()
        for item in items
        if isinstance(item, sa.CheckConstraint)
    )
    for state in (
        "OPEN",
        "ACKNOWLEDGED",
        "REQUESTED",
        "PARTIAL",
        "CANCELLED",
        "PENDING",
        "RETRYING",
        "HOLD",
        "SENT",
    ):
        assert state in checks
    assert "version >= 1" in checks
    assert "attempt_count >= 0" in checks
    assert "success_count + failure_count + skipped_count <= total_count" in checks
    index_names = {item[0] for item in recorder.indexes}
    assert {
        "uq_operation_runs_active_idempotency",
        "uq_operation_runs_idempotency_scope",
        "ix_operation_runs_claim",
        "ix_operation_runs_hospital_created",
        "ix_incidents_state_sla",
        "ix_incidents_hospital_state",
        "ix_notification_outbox_claim",
        "ix_notification_outbox_lease",
    } <= index_names
    active_unique = next(
        item for item in recorder.indexes if item[0] == "uq_operation_runs_active_idempotency"
    )
    assert active_unique[3]["unique"] is True
    assert "REQUESTED" in str(active_unique[3]["postgresql_where"])
    assert "RUNNING" in str(active_unique[3]["postgresql_where"])
    assert active_unique[3]["postgresql_nulls_not_distinct"] is True
    runs_columns = {
        item.name
        for item in recorder.tables["operation_runs"]
        if isinstance(item, sa.Column)
    }
    assert "parent_run_id" in runs_columns
    assert "retry_of_run_id" not in runs_columns
    incident_columns = {
        item.name
        for item in recorder.tables["incidents"]
        if isinstance(item, sa.Column)
    }
    assert "occurrence_count" in incident_columns
    outbox_columns = {
        item.name
        for item in recorder.tables["notification_outbox"]
        if isinstance(item, sa.Column)
    }
    assert "provider_response" in outbox_columns
    outbox_next_attempt = next(
        item
        for item in recorder.tables["notification_outbox"]
        if isinstance(item, sa.Column) and item.name == "next_attempt_at"
    )
    assert outbox_next_attempt.nullable is True
    outbox_checks = " ".join(
        str(item.sqltext)
        for item in recorder.tables["notification_outbox"]
        if isinstance(item, sa.CheckConstraint)
    )
    assert "next_attempt_at IS NOT NULL" in outbox_checks
    assert "next_attempt_at IS NULL" in outbox_checks


def test_downgrade_removes_only_control_plane_artifacts(monkeypatch) -> None:
    # Given: a captured 0042 downgrade stream
    migration = _load()
    recorder = Recorder()
    recorder.install(monkeypatch, migration)

    # When: 0042 downgrades
    migration.downgrade()

    # Then: dependency order removes outbox, incidents, then runs
    assert recorder.dropped_tables == [
        "notification_outbox",
        "incidents",
        "operation_runs",
    ]
