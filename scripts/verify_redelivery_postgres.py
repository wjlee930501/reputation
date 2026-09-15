"""Verify real PostgreSQL behavior in a disposable loopback-only test database.

This creates ORM test tables, not migration history. Never use a production DSN.
Example: python scripts/verify_redelivery_postgres.py --port 55481 --prepare-schema
"""
import argparse
import os
import socket
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--prepare-schema", action="store_true")
args, test_args = parser.parse_known_args()
if not 49152 <= args.port <= 65535:
    parser.error("Use a dedicated high port for the disposable database")
root = Path(__file__).resolve().parents[1]
base = f"postgresql://geo_test@127.0.0.1:{args.port}/reputation_redelivery_test"
async_url = base.replace("postgresql://", "postgresql+asyncpg://")
sync_url = base.replace("postgresql://", "postgresql+psycopg2://")
os.environ.clear()
os.environ.update(PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", APP_ENV="test",
    REPUTATION_DISABLE_DOTENV="1", ADMIN_SECRET_KEY="test-admin-key",
    ANTHROPIC_API_KEY="test-anthropic-key", OPENAI_API_KEY="test-openai-key",
    GEMINI_API_KEY="", BFF_ACTOR_SECRET="test-actor-key",
    WORKER_DISPATCH_SECRET="test-dispatch-key", DATABASE_URL=async_url,
    SYNC_DATABASE_URL=sync_url, REDIS_URL="redis://127.0.0.1:1/15")
for key in ("NOTIFICATION_OUTBOX_DATABASE_URL", "OPERATION_RUN_TRANSITIONS_DATABASE_URL",
            "OPERATION_RUNS_DATABASE_URL", "OPERATION_RUN_CONCURRENCY_DATABASE_URL",
            "OPERATION_RUN_SIGNAL_DATABASE_URL", "TASK19_ASYNC_DATABASE_URL",
            "CONTENT_PUBLISH_RECOVERY_DATABASE_URL", "REDELIVERY_TEST_DATABASE_URL"):
    os.environ[key] = async_url
os.environ["OPERATION_RUN_SIGNAL_SYNC_DATABASE_URL"] = sync_url
os.environ["REDELIVERY_TEST_SYNC_DATABASE_URL"] = sync_url
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/reputation-no-cloud-credentials.json"
os.chdir(root / "backend")
sys.path.insert(0, str(root / "backend"))
original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex


def allow(address):
    return isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1") and address[1] == args.port


def guarded_connect(sock, address):
    if not allow(address):
        raise OSError("Only the disposable PostgreSQL endpoint is allowed")
    return original_connect(sock, address)


def guarded_connect_ex(sock, address):
    if not allow(address):
        raise OSError("Only the disposable PostgreSQL endpoint is allowed")
    return original_connect_ex(sock, address)

socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex

import psycopg2  # noqa: E402
original_pg_connect = psycopg2.connect


def guarded_pg_connect(dsn=None, *positional, **kwargs):
    params = psycopg2.extensions.parse_dsn(dsn or "")
    params.update({key: value for key, value in kwargs.items()
                   if key in ("host", "port", "dbname", "database")})
    if (params.get("host") != "127.0.0.1" or str(params.get("port")) != str(args.port)
            or params.get("dbname", params.get("database")) != "reputation_redelivery_test"):
        raise OSError("Only the disposable PostgreSQL database is allowed")
    return original_pg_connect(dsn, *positional, **kwargs)


psycopg2.connect = guarded_pg_connect

from sqlalchemy import create_engine, inspect, text  # noqa: E402
import app.models  # noqa: E402,F401
from app.core.database import Base  # noqa: E402

engine = create_engine(sync_url, connect_args={"connect_timeout": 5})
with engine.connect() as connection:
    database, port = connection.execute(text("SELECT current_database(), inet_server_port()")).one()
    if database != "reputation_redelivery_test" or port != args.port:
        raise RuntimeError("Unexpected database identity")
if args.prepare_schema:
    if inspect(engine).get_table_names():
        raise RuntimeError("Schema preparation requires an empty disposable database")
    Base.metadata.create_all(engine)
print(f"Verified isolated PostgreSQL: {database}, port={port}, schema=ORM (not migrations)", flush=True)
engine.dispose()
import pytest  # noqa: E402

if not any(value.startswith("tests/") for value in test_args):
    test_args = ["tests/test_history_consistency.py", "tests/test_geo_autonomy_hardening.py",
                 "tests/test_notification_outbox.py", *test_args]
raise SystemExit(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", *test_args]))
