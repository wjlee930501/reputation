import pytest

from app.core.config import Settings


def test_production_fails_fast_when_admin_secret_empty(monkeypatch):
    monkeypatch.delenv("ADMIN_SECRET_KEY", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)

    with pytest.raises(ValueError, match="ADMIN_SECRET_KEY"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/reputation",
            SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost/reputation",
            ADMIN_SECRET_KEY="",
        )


def test_production_builds_database_urls_from_secret_parts(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SYNC_DATABASE_URL", raising=False)

    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        ADMIN_SECRET_KEY="admin-secret",
        DB_USER="reputation",
        DB_PASSWORD="p@ss word",
        DB_NAME="reputation",
        CLOUD_SQL_CONNECTION_NAME="project:region:instance",
    )

    assert settings.DATABASE_URL == (
        "postgresql+asyncpg://reputation:p%40ss%20word@/reputation"
        "?host=/cloudsql/project:region:instance"
    )
    assert settings.SYNC_DATABASE_URL == (
        "postgresql://reputation:p%40ss%20word@/reputation"
        "?host=/cloudsql/project:region:instance"
    )
