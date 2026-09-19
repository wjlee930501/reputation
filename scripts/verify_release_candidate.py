"""Run release gates against dedicated loopback databases, never production.
Usage: python scripts/verify_release_candidate.py migrate
       python scripts/verify_release_candidate.py test [pytest arguments]
"""
import os
import socket
import sys
from pathlib import Path

from release_verification_context import load_context

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = load_context()
ARTIFACT_ROOT = Path(CONTEXT["root"])
PG_PORT, REDIS_PORT = CONTEXT["pg_port"], CONTEXT["redis_port"]
ADMIN_PORT, SITE_PORT, API_PORT = (CONTEXT[k] for k in ("admin_port", "site_port", "api_port"))
DATABASES = {"reputation_release_test", "reputation_release_aux",
             "reputation_release_upgrade", "reputation_release_ui",
             "reputation_redelivery_test", "reputation_autonomy_migration"}
BASE = f"postgresql://geo_test@127.0.0.1:{PG_PORT}/reputation_release_test"
AUX = BASE.replace("release_test", "release_aux")
UI = BASE.replace("release_test", "release_ui")
UPGRADE = BASE.replace("reputation_release_test", "reputation_autonomy_migration")
REDELIVERY = BASE.replace("reputation_release_test", "reputation_redelivery_test")

def async_url(url):
    return url.replace("postgresql://", "postgresql+asyncpg://")

def sync_url(url):
    return url.replace("postgresql://", "postgresql+psycopg2://")

def configure(database=BASE):
    os.environ.clear()
    os.environ.update(PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        APP_ENV="test", REPUTATION_DISABLE_DOTENV="1", ADMIN_SECRET_KEY="test-admin-key",
        DATABASE_URL=async_url(database), SYNC_DATABASE_URL=sync_url(database))
    os.environ.update(OPENROUTER_API_KEY="test-openrouter-key",
        IMAGE_FALLBACK_PROVIDER="",
        GOOGLE_APPLICATION_CREDENTIALS="/tmp/reputation-release-no-credentials.json",
        GCP_PROJECT_ID="", GOOGLE_CLOUD_PROJECT="", SLACK_WEBHOOK_URL="", SLACK_WEBHOOK_URL_DEV="",
        BFF_ACTOR_SECRET="test-actor-key", WORKER_DISPATCH_SECRET="test-dispatch-key",
        SITE_BFF_SECRET="test-site-bff-key", SITE_REVALIDATE_SECRET="test-revalidate-key",
        SITE_BASE_URL=f"http://127.0.0.1:{SITE_PORT}", ADMIN_BASE_URL=f"http://127.0.0.1:{ADMIN_PORT}",
        REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/0",
        COST_GUARD_REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/2",
        INTEGRATION_REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/3",
        INTEGRATION_DATABASE_URL=BASE, MIGRATION_UPGRADE_DATABASE_URL=sync_url(UPGRADE),
        REQUIRE_PDF_RENDER="1", DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib",
        HOME=str(ARTIFACT_ROOT), LC_ALL="C", LANG="C")
    for key in ("INCIDENT_TEST_DATABASE_URL", "NOTIFICATION_OUTBOX_DATABASE_URL",
                "OPERATION_RUN_TRANSITIONS_DATABASE_URL", "OPERATION_RUNS_DATABASE_URL",
                "OPERATION_RUN_CONCURRENCY_DATABASE_URL", "OPERATION_RUN_SIGNAL_DATABASE_URL",
                "ONBOARDING_PROJECTOR_DATABASE_URL", "CONTENT_PUBLISH_RECOVERY_DATABASE_URL",
                "TASK19_ASYNC_DATABASE_URL", "TASK20_DATABASE_URL", "REDELIVERY_TEST_DATABASE_URL"):
        os.environ[key] = async_url(AUX)
    for key in ("TASK13_DATABASE_URL", "TASK16_DATABASE_URL", "TASK18_DATABASE_URL",
                "TASK22_DATABASE_URL", "TASK24_DATABASE_URL"):
        os.environ[key] = AUX
    for key in ("OPERATIONS_TEST_DATABASE_URL", "TASK19_SYNC_DATABASE_URL",
                "OPERATION_RUN_SIGNAL_SYNC_DATABASE_URL", "REDELIVERY_TEST_SYNC_DATABASE_URL"):
        os.environ[key] = sync_url(AUX)
    os.environ["TASK13_DATABASE_URL"] = async_url(AUX)
    os.environ["TASK18_DATABASE_URL"] = async_url(AUX)
    os.environ["REDELIVERY_TEST_DATABASE_URL"] = async_url(REDELIVERY)
    os.environ["REDELIVERY_TEST_SYNC_DATABASE_URL"] = sync_url(REDELIVERY)
    if database != UI:
        # Production-settings tests supply their own secrets and URL kwargs.
        # Empty env overrides them in _resolve_secret; never poison those fixtures.
        for key in ("SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_URL_DEV", "BFF_ACTOR_SECRET",
                    "SITE_BFF_SECRET", "SITE_BASE_URL", "ADMIN_BASE_URL", "WORKER_DISPATCH_SECRET"):
            os.environ.pop(key, None)
    os.chdir(ROOT / "backend")
    sys.path.insert(0, str(ROOT / "backend"))


def isolate_network():
    allowed_ports = {PG_PORT, REDIS_PORT, ADMIN_PORT, SITE_PORT, API_PORT}
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_bind = socket.socket.bind

    def permitted(address):
        return (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")
                and address[1] in allowed_ports)

    def bind(sock, address):
        result = original_bind(sock, address)
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
            allowed_ports.add(sock.getsockname()[1])
        return result

    def connect(sock, address):
        if not permitted(address):
            raise OSError("Release verification forbids non-test network endpoints")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not permitted(address):
            raise OSError("Release verification forbids non-test network endpoints")
        return original_connect_ex(sock, address)

    socket.socket.bind = bind
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    import psycopg2
    original_pg = psycopg2.connect

    def connect_pg(dsn=None, *args, **kwargs):
        params = psycopg2.extensions.parse_dsn(dsn or "")
        params.update({k: v for k, v in kwargs.items() if k in ("host", "port", "dbname", "database")})
        if (params.get("host") not in ("127.0.0.1", "localhost")
                or str(params.get("port")) != str(PG_PORT)
                or params.get("dbname", params.get("database")) not in DATABASES):
            raise OSError("Release verification forbids non-test databases")
        return original_pg(dsn, *args, **kwargs)

    psycopg2.connect = connect_pg


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"
    if mode not in {"migrate", "test", "api"}:
        raise SystemExit("Choose migrate, test, or api explicitly")
    configure(UI if mode == "api" else BASE)
    isolate_network()
    if mode == "migrate":
        from alembic import command
        from alembic.config import Config
        from app.core.config import settings
        for database in (BASE, AUX, UI, REDELIVERY):
            settings.DATABASE_URL = async_url(database)
            settings.SYNC_DATABASE_URL = sync_url(database)
            print("Migrating isolated database:", database.rsplit("/", 1)[-1], flush=True)
            command.upgrade(Config("alembic.ini"), "head")
    elif mode == "test":
        import pytest
        raise SystemExit(pytest.main(["-q", "--tb=short", *sys.argv[2:]]))
    else:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=API_PORT, log_level="warning")


if __name__ == "__main__":
    main()
