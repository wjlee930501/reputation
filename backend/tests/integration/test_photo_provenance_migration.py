"""Rollout proof: legacy public photos fail closed during migration 0052."""

import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_migration_disables_legacy_public_photos_pending_truthful_reapproval(
    pg_conn, monkeypatch
) -> None:
    # Given: an isolated pre-0052 table with one live photo and one non-photo row.
    schema = f"photo_migration_{uuid.uuid4().hex}"
    pg_conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    pg_conn.execute(
        text(
            f'CREATE TABLE "{schema}".hospital_source_assets ('
            "id integer PRIMARY KEY, "
            "source_type public.hospital_source_type NOT NULL, "
            "is_public boolean NOT NULL)"
        )
    )
    pg_conn.execute(
        text(
            f'INSERT INTO "{schema}".hospital_source_assets '
            "(id, source_type, is_public) VALUES "
            "(1, 'PHOTO_DOCTOR', true), (2, 'HOMEPAGE', true)"
        )
    )
    pg_conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0052_add_photo_asset_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("photo_provenance_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(pg_conn)),
    )

    # When: the real migration upgrade runs against the legacy shape.
    migration.upgrade()

    # Then: unverifiable photos are private, while unrelated source visibility is untouched.
    rows = pg_conn.execute(
        text(f'SELECT id, is_public FROM "{schema}".hospital_source_assets ORDER BY id')
    ).all()
    assert rows == [(1, False), (2, True)]
