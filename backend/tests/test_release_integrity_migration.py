import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0028_release_blocker_integrity_constraints.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("release_integrity_constraints", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Bind:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, statement):
        self.sql = str(statement)
        return _Result(self.rows)


def test_content_slot_preflight_reports_duplicate_schedule_slots():
    migration = _load_migration()
    duplicates = migration._duplicate_schedule_slots(_Bind([("sched-1", "2026-07-01", 1)]))
    assert duplicates == [("sched-1", "2026-07-01", 1)]


def test_content_slot_preflight_matches_postgres_unique_null_behavior():
    migration = _load_migration()
    bind = _Bind([])
    assert migration._duplicate_schedule_slots(bind) == []
    assert "schedule_id IS NOT NULL" in bind.sql
    assert "scheduled_date IS NOT NULL" in bind.sql
    assert "sequence_no IS NOT NULL" in bind.sql
