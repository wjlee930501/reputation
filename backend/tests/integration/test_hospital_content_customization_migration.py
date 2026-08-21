import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_migration_adds_customization_fields_and_applies_nowon_request(
    pg_conn, monkeypatch
) -> None:
    schema = f"hospital_customization_{uuid.uuid4().hex}"
    pg_conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    pg_conn.execute(
        text(
            f'CREATE TABLE "{schema}".hospitals ('
            "id integer PRIMARY KEY, slug varchar(255) NOT NULL, hero_description varchar(320))"
        )
    )
    pg_conn.execute(
        text(
            f'CREATE TABLE "{schema}".content_items ('
            "id integer PRIMARY KEY)"
        )
    )
    pg_conn.execute(
        text(
            f'INSERT INTO "{schema}".hospitals (id, slug, hero_description) VALUES '
            "(1, 'noweontab365yiweon', NULL), (2, 'another-clinic', '기존 문구')"
        )
    )
    pg_conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0054_add_hospital_content_customization.py"
    )
    spec = importlib.util.spec_from_file_location("hospital_content_customization", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(pg_conn)))

    migration.upgrade()

    rows = pg_conn.execute(
        text(
            f'SELECT slug, hero_specialties, content_focus_topics, hero_description '
            f'FROM "{schema}".hospitals ORDER BY id'
        )
    ).all()
    assert rows == [
        (
            "noweontab365yiweon",
            ["정형외과", "통증의학과", "외상치료"],
            ["정형외과", "신경외과", "통증의학과", "외상"],
            "매일 365 야간진료",
        ),
        ("another-clinic", [], [], "기존 문구"),
    ]
