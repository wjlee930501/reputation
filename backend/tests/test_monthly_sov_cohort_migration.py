"""Stable monthly cohort column and one-time token backfill contract."""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0063_add_monthly_sov_cohort.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("monthly_sov_cohort", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


def test_upgrade_adds_non_null_flag_and_only_backfills_unique_active_matches(
    monkeypatch,
):
    migration = _load()
    added = []
    updates = []
    errors = []
    result_sets = iter(
        [
            [{"id": "unique-id", "name": "unique"}],
            [],
            [{"id": "a", "name": "a"}, {"id": "b", "name": "b"}],
            *([[]] * 4),
        ]
    )

    class _Bind:
        def execute(self, statement, params):
            if str(statement).startswith("UPDATE"):
                updates.append(params["id"])
                return _Result([])
            return _Result(next(result_sets))

    monkeypatch.setattr(migration.op, "add_column", lambda table, column: added.append((table, column)))
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind())
    monkeypatch.setattr(migration.logger, "error", lambda *args: errors.append(args))

    migration.upgrade()

    assert added[0][0] == "hospitals"
    column = added[0][1]
    assert column.name == "monthly_sov_cohort"
    assert column.nullable is False
    assert updates == ["unique-id"]
    assert len(errors) == 6
