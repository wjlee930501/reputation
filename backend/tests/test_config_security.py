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


def _valid_prod_kwargs(**overrides):
    base = dict(
        _env_file=None,
        APP_ENV="production",
        ADMIN_SECRET_KEY="admin-secret",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@db/reputation",
        SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@db/reputation",
        ALLOWED_ORIGINS="https://admin.example.com,https://reputation.co.kr",
        TRUSTED_PROXY_IPS="130.211.0.0/22,35.191.0.0/16",
    )
    base.update(overrides)
    return base


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
        ALLOWED_ORIGINS="https://admin.example.com",
        TRUSTED_PROXY_IPS="130.211.0.0/22,35.191.0.0/16",
    )

    assert settings.DATABASE_URL == (
        "postgresql+asyncpg://reputation:p%40ss%20word@/reputation"
        "?host=/cloudsql/project:region:instance"
    )
    assert settings.SYNC_DATABASE_URL == (
        "postgresql://reputation:p%40ss%20word@/reputation"
        "?host=/cloudsql/project:region:instance"
    )


def test_production_rejects_localhost_origins(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(**_valid_prod_kwargs(ALLOWED_ORIGINS="http://localhost:3000"))


def test_production_rejects_wildcard_origin(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(**_valid_prod_kwargs(ALLOWED_ORIGINS="*"))


def test_production_rejects_localhost_only_trusted_proxies(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        Settings(**_valid_prod_kwargs(TRUSTED_PROXY_IPS="127.0.0.1,::1"))


def test_production_rejects_catch_all_trusted_proxies(monkeypatch):
    # 0.0.0.0/0 trusts every hop → makes X-Forwarded-For spoofable.
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        Settings(**_valid_prod_kwargs(TRUSTED_PROXY_IPS="0.0.0.0/0,::/0"))


def test_production_accepts_valid_secure_config(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    settings = Settings(**_valid_prod_kwargs())
    assert settings.ALLOWED_ORIGINS == ["https://admin.example.com", "https://reputation.co.kr"]
