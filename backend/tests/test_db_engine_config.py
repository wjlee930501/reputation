"""DB 엔진 설정 회귀 테스트 — 연결 예산 풀 분리 + sync statement timeout."""
from app.core import database
from app.core.config import Settings


def test_sync_connect_args_sets_server_statement_timeout(monkeypatch):
    # psycopg2에는 command_timeout이 없으므로 서버측 statement_timeout(ms)을 옵션으로 건다.
    monkeypatch.setattr(database.settings, "DB_CONNECT_TIMEOUT", 10)
    monkeypatch.setattr(database.settings, "DB_COMMAND_TIMEOUT", 30)
    args = database._sync_connect_args()
    assert args["connect_timeout"] == 10
    assert args["options"] == "-c statement_timeout=30000"


def test_sync_connect_args_respects_disabled_timeouts(monkeypatch):
    # 0 = disabled — 옵션을 붙이지 않는다.
    monkeypatch.setattr(database.settings, "DB_CONNECT_TIMEOUT", 0)
    monkeypatch.setattr(database.settings, "DB_COMMAND_TIMEOUT", 0)
    args = database._sync_connect_args()
    assert "connect_timeout" not in args
    assert "options" not in args


def test_db_pool_defaults_split_api_and_worker(monkeypatch):
    # API(async)와 Worker(sync) 풀이 분리돼 Cloud SQL 연결 예산을 지킨다.
    for var in (
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_WORKER_POOL_SIZE",
        "DB_WORKER_MAX_OVERFLOW",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None, APP_ENV="development")
    assert s.DB_POOL_SIZE == 3
    assert s.DB_MAX_OVERFLOW == 2
    assert s.DB_WORKER_POOL_SIZE == 2
    assert s.DB_WORKER_MAX_OVERFLOW == 2
