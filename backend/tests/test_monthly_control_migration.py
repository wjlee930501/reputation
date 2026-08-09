import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0041_add_monthly_delivery_control.py"
HANDOFF_PATH = VERSIONS_DIR / "0040_add_hospital_handoffs.py"
PINNED_HANDOFF_SHA256 = "c132ce1ca973fdc794195a2987be1249a6391bce33cb91b8594297c169b487f1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("monthly_control_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self) -> None:
        self.tables: dict[str, tuple[object, ...]] = {}
        self.sql: list[str] = []
        self.added_columns: list[tuple[str, str]] = []
        self.constraints: list[str] = []
        self.dropped_tables: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def install(self, monkeypatch, migration: ModuleType) -> None:
        monkeypatch.setattr(
            migration.op, "create_table", lambda name, *items: self.tables.setdefault(name, items)
        )
        monkeypatch.setattr(
            migration.op, "execute", lambda statement: self.sql.append(str(statement))
        )
        monkeypatch.setattr(
            migration.op,
            "add_column",
            lambda table, column: self.added_columns.append((table, column.name)),
        )
        monkeypatch.setattr(migration.op, "alter_column", lambda *_a, **_k: None)
        monkeypatch.setattr(
            migration.op,
            "create_foreign_key",
            lambda name, *_a, **_k: self.constraints.append(name),
        )
        monkeypatch.setattr(
            migration.op,
            "create_unique_constraint",
            lambda name, *_a, **_k: self.constraints.append(name),
        )
        monkeypatch.setattr(
            migration.op,
            "create_check_constraint",
            lambda name, *_a, **_k: self.constraints.append(name),
        )
        monkeypatch.setattr(migration.op, "drop_constraint", lambda *_a, **_k: None)
        monkeypatch.setattr(migration.op, "drop_table", self.dropped_tables.append)
        monkeypatch.setattr(
            migration.op,
            "drop_column",
            lambda table, column: self.dropped_columns.append((table, column)),
        )


def test_0041_is_pinned_to_unchanged_0040() -> None:
    migration = _load()
    assert migration.down_revision == "0040_add_hospital_handoffs"
    assert hashlib.sha256(HANDOFF_PATH.read_bytes()).hexdigest() == PINNED_HANDOFF_SHA256


def test_upgrade_declares_full_monthly_control_contract(monkeypatch) -> None:
    migration = _load()
    recorder = Recorder()
    recorder.install(monkeypatch, migration)

    migration.upgrade()

    assert set(recorder.tables) == {
        "monthly_measurement_manifests",
        "monthly_measurement_cells",
        "monthly_measurement_attempts",
        "hospital_service_intervals",
        "monthly_report_artifacts",
        "monthly_delivery_events",
    }
    cells = recorder.tables["monthly_measurement_cells"]
    checks = " ".join(str(item.sqltext) for item in cells if isinstance(item, sa.CheckConstraint))
    assert "SUCCESS" in checks and "FAILED" in checks and "EXCLUDED" in checks
    assert "DUPLICATE_TARGET" in checks and "LEGAL_REMOVAL" in checks
    sql = " ".join(recorder.sql)
    assert "EXCLUDE USING gist" in sql
    assert "WHERE status = 'ACTIVE'" in sql
    assert "LEGACY_CUTOVER" in sql
    assert "LEGACY_UNVERIFIED" in sql
    assert "append-only" in sql
    assert "uq_monthly_reports_period_version" in recorder.constraints
    assert "uq_monthly_reports_supersedes" in recorder.constraints
    artifact_columns = {
        item.name
        for item in recorder.tables["monthly_report_artifacts"]
        if isinstance(item, sa.Column)
    }
    assert {
        "audience",
        "sha256",
        "byte_size",
        "validated",
        "validated_at",
        "validated_by_id",
        "validation_metadata",
    } <= artifact_columns


def test_downgrade_removes_only_0041_artifacts(monkeypatch) -> None:
    migration = _load()
    recorder = Recorder()
    recorder.install(monkeypatch, migration)

    migration.downgrade()

    assert recorder.dropped_tables == [
        "monthly_delivery_events",
        "monthly_report_artifacts",
        "hospital_service_intervals",
        "monthly_measurement_attempts",
        "monthly_measurement_cells",
        "monthly_measurement_manifests",
    ]
    assert len(recorder.dropped_columns) == 11
