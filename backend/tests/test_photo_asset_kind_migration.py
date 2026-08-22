from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.models.essence import PHOTO_SOURCE_TYPES, SourceType
from app.services.photo_assets import allowed_photo_asset_kinds, legacy_photo_asset_kind

MIGRATION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "0052_backfill_photo_asset_kind.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("photo_asset_kind_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(statement: object) -> str:
    return " ".join(str(statement).split())


def test_backfill_only_touches_photos_without_a_classification(monkeypatch) -> None:
    migration = _load()
    statements: list[object] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "0051_add_hospital_visual_identity"
    assert len(statements) == 1
    sql = _sql(statements[0])
    assert "UPDATE hospital_source_assets" in sql
    assert "source_metadata->>'asset_kind' IS NULL" in sql
    assert "source_type::text IN" in sql


def test_backfill_covers_every_photo_source_type() -> None:
    migration = _load()

    assert set(migration.PHOTO_SOURCE_TYPES) == {
        source_type.value for source_type in PHOTO_SOURCE_TYPES
    }


def test_backfill_writes_the_same_roles_the_application_recovers_to() -> None:
    doctor_kind = legacy_photo_asset_kind(SourceType.PHOTO_DOCTOR)
    facility_kind = legacy_photo_asset_kind(SourceType.PHOTO_CLINIC_EXTERIOR)

    assert doctor_kind == "EDITORIAL_GRAPHIC"
    assert facility_kind == "VERIFIED_FACILITY"
    assert allowed_photo_asset_kinds(SourceType.PHOTO_DOCTOR)[doctor_kind] == [
        "CONTENT_EDITORIAL"
    ]
    assert allowed_photo_asset_kinds(SourceType.PHOTO_CLINIC_EXTERIOR)[facility_kind] == [
        "HERO",
        "GALLERY",
    ]


def test_backfill_sql_matches_the_application_recovery_roles(monkeypatch) -> None:
    migration = _load()
    statements: list[object] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    sql = _sql(statements[0])

    assert "THEN 'EDITORIAL_GRAPHIC'" in sql
    assert "ELSE 'VERIFIED_FACILITY'" in sql
    assert "jsonb_build_array('CONTENT_EDITORIAL')" in sql
    assert "jsonb_build_array('HERO', 'GALLERY')" in sql
    assert "'DOCTOR_IDENTITY'" not in sql


def test_backfilled_rows_stay_flagged_for_operator_review(monkeypatch) -> None:
    migration = _load()
    statements: list[object] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()
    sql = _sql(statements[0])

    assert "'needs_operator_review', true" in sql
    assert statements[0].compile().params["legacy_source"] == "LEGACY_BACKFILL"


def test_downgrade_only_removes_rows_this_migration_wrote(monkeypatch) -> None:
    migration = _load()
    statements: list[object] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()
    sql = _sql(statements[0])

    assert "source_metadata->>'asset_kind_source' = " in sql
    assert "- 'asset_kind'" in sql
