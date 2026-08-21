import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_migration_marks_legacy_content_images_unverified(pg_conn, monkeypatch) -> None:
    schema = f"content_image_migration_{uuid.uuid4().hex}"
    pg_conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    pg_conn.execute(
        text(
            f'CREATE TABLE "{schema}".content_items ('
            "id integer PRIMARY KEY, image_url varchar(500))"
        )
    )
    pg_conn.execute(
        text(
            f'INSERT INTO "{schema}".content_items (id, image_url) '
            "VALUES (1, 'gs://bucket/legacy.webp')"
        )
    )
    pg_conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0053_add_content_image_policy_verification.py"
    )
    spec = importlib.util.spec_from_file_location("content_image_policy_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(pg_conn)))

    migration.upgrade()

    row = pg_conn.execute(
        text(
            f'SELECT image_url, image_policy_verified_at '
            f'FROM "{schema}".content_items WHERE id = 1'
        )
    ).one()
    assert row == ("gs://bucket/legacy.webp", None)
