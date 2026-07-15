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
        SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T00/B00/xxxx",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@db/reputation",
        SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@db/reputation",
        REDIS_URL="redis://redis.internal:6379/0",
        ALLOWED_ORIGINS="https://admin.example.com,https://reputation.co.kr",
        TRUSTED_PROXY_IPS="130.211.0.0/22,35.191.0.0/16",
        ADMIN_BASE_URL="https://admin.example.com",
        SITE_BASE_URL="https://reputation.co.kr",
        CERTIFICATE_MANAGER_AUTO_PROVISION=True,
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
        SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T00/B00/xxxx",
        DB_USER="reputation",
        DB_PASSWORD="p@ss word",
        DB_NAME="reputation",
        CLOUD_SQL_CONNECTION_NAME="project:region:instance",
        REDIS_URL="redis://redis.internal:6379/0",
        ALLOWED_ORIGINS="https://admin.example.com",
        TRUSTED_PROXY_IPS="130.211.0.0/22,35.191.0.0/16",
        ADMIN_BASE_URL="https://admin.example.com",
        SITE_BASE_URL="https://reputation.co.kr",
        CERTIFICATE_MANAGER_AUTO_PROVISION=True,
    )

    assert settings.DATABASE_URL == (
        "postgresql+asyncpg://reputation:p%40ss%20word@/reputation"
        "?host=/cloudsql/project:region:instance"
    )
    assert settings.SYNC_DATABASE_URL == (
        "postgresql://reputation:p%40ss%20word@/reputation?host=/cloudsql/project:region:instance"
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


def test_production_rejects_localhost_admin_base_url(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="ADMIN_BASE_URL"):
        Settings(**_valid_prod_kwargs(ADMIN_BASE_URL="http://localhost:3000"))


def test_production_rejects_non_https_site_base_url(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="SITE_BASE_URL"):
        Settings(**_valid_prod_kwargs(SITE_BASE_URL="http://reputation.co.kr"))


def test_production_rejects_localhost_redis_url(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(**_valid_prod_kwargs(REDIS_URL="redis://localhost:6379/0"))


def test_production_accepts_valid_secure_config(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    settings = Settings(**_valid_prod_kwargs())
    assert settings.ALLOWED_ORIGINS == ["https://admin.example.com", "https://reputation.co.kr"]
    assert settings.ADMIN_BASE_URL == "https://admin.example.com"
    assert settings.SITE_BASE_URL == "https://reputation.co.kr"


def test_production_requires_chatgpt_web_search(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="OPENAI_CHATGPT_USE_WEB_SEARCH"):
        Settings(**_valid_prod_kwargs(OPENAI_CHATGPT_USE_WEB_SEARCH=False))


def test_production_requires_automatic_certificate_provisioning(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="CERTIFICATE_MANAGER_AUTO_PROVISION"):
        Settings(**_valid_prod_kwargs(CERTIFICATE_MANAGER_AUTO_PROVISION=False))


def test_production_fails_fast_when_slack_webhook_empty(monkeypatch):
    # SLACK_WEBHOOK_URL은 critical — 모든 주요 이벤트 알림이 이 웹훅으로 나간다.
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(ValueError, match="SLACK_WEBHOOK_URL"):
        Settings(**_valid_prod_kwargs(SLACK_WEBHOOK_URL=""))


def test_production_warns_on_empty_flow_secrets_and_placeholder_buckets(monkeypatch, caplog):
    import logging

    for var in (
        "GCP_PROJECT_ID",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GCP_STORAGE_BUCKET",
        "GCS_REPORTS_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)

    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        Settings(
            **_valid_prod_kwargs(
                ANTHROPIC_API_KEY="",
                OPENAI_API_KEY="",
                GEMINI_API_KEY="",
                GCP_STORAGE_BUCKET="reputation-images",
                GCS_REPORTS_BUCKET="reputation-reports",
            )
        )

    text = caplog.text
    # 핵심 플로우 키 미설정 경고 (부팅은 계속되지만 로그에 영향 범위를 남긴다).
    assert "ANTHROPIC_API_KEY" in text
    assert "OPENAI_API_KEY" in text
    assert "GEMINI_API_KEY" in text
    # 전역 유일 제약상 placeholder 기본 버킷명 감지 경고.
    assert "GCP_STORAGE_BUCKET" in text
    assert "GCS_REPORTS_BUCKET" in text
