import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = None
AsyncSessionLocal = None


def _async_connect_args():
    """Return asyncpg-specific connect_args for connection-level timeouts."""
    args = {}
    if settings.DB_CONNECT_TIMEOUT > 0:
        args["timeout"] = settings.DB_CONNECT_TIMEOUT
    if settings.DB_COMMAND_TIMEOUT > 0:
        args["command_timeout"] = settings.DB_COMMAND_TIMEOUT
    return args


def _sync_connect_args():
    """Return psycopg2 connect_args: TCP connect timeout + server statement timeout.

    psycopg2에는 asyncpg의 command_timeout 대응이 없으므로, 서버측 statement_timeout(ms)을
    연결 옵션으로 건다. DB_COMMAND_TIMEOUT=0이면(비활성) 옵션을 붙이지 않는다.
    """
    args = {}
    if settings.DB_CONNECT_TIMEOUT > 0:
        args["connect_timeout"] = settings.DB_CONNECT_TIMEOUT
    if settings.DB_COMMAND_TIMEOUT > 0:
        args["options"] = f"-c statement_timeout={settings.DB_COMMAND_TIMEOUT * 1000}"
    return args


def get_async_sessionmaker():
    global engine, AsyncSessionLocal
    if AsyncSessionLocal is None:
        worker_process = os.getenv("SERVICE", "").strip().lower() in {"worker", "beat"}
        engine_kwargs = {
            "echo": settings.APP_ENV == "development",
            "pool_pre_ping": True,
            "connect_args": _async_connect_args(),
        }
        if worker_process:
            # Celery task families own different event loops in one prefork process.
            # A pooled asyncpg connection cannot cross those loops, so worker async
            # connections live only for the session/loop that opened them.
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs.update(
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_timeout=settings.DB_POOL_TIMEOUT,
            )
        engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
        AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return AsyncSessionLocal

# Sync engine for Celery workers (Celery does not support async)
# Lazily initialized to avoid sharing engine/pool across Celery prefork children.
_sync_engine = None
_sync_sessionmaker = None


def get_sync_engine():
    """Lazily build the per-process sync engine (Celery prefork children must not share)."""
    global _sync_engine
    if _sync_engine is None:
        # 워커 전용 풀 크기 — sync 엔진은 prefork 자식마다 생성되므로 API(async)보다
        # 작게 잡아 Cloud SQL max_connections 예산을 지킨다 (config.py 연결 예산 주석 참조).
        _sync_engine = create_engine(
            settings.SYNC_DATABASE_URL,
            echo=settings.APP_ENV == "development",
            pool_pre_ping=True,
            pool_size=settings.DB_WORKER_POOL_SIZE,
            max_overflow=settings.DB_WORKER_MAX_OVERFLOW,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            connect_args=_sync_connect_args(),
        )
    return _sync_engine


def _get_sync_sessionmaker():
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        _sync_sessionmaker = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
    return _sync_sessionmaker


def SyncSessionLocal():
    """Return a sync SQLAlchemy Session. Drop-in for `with SyncSessionLocal() as db:`.

    Uses lazy initialization to create a fresh engine + sessionmaker per process,
    critical for Celery prefork workers that should not share connections across children.
    """
    return _get_sync_sessionmaker()()


@contextmanager
def SyncSessionPinnedConnection() -> Iterator[Session]:
    """커넥션을 고정한 sync Session — 세션 advisory 락을 쓰는 태스크 전용.

    기본 Session은 commit/rollback마다 커넥션을 풀에 반환하고 다음 문장에서 다시
    체크아웃한다. 풀에 커넥션이 둘 이상이면 그 다음 문장이 **다른 커넥션**에서 돈다.
    `pg_advisory_lock`은 커넥션에 붙는 락이라, 그 경우 unlock이 엉뚱한 커넥션에서
    실행돼 false를 반환하고 락은 풀 안의 커넥션에 남아 그 병원의 모든 쓰기를 막는다.
    커넥션을 직접 들고 세션을 그 위에 얹으면 락과 unlock이 항상 같은 커넥션에서 돈다.
    """
    connection = get_sync_engine().connect()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
    finally:
        connection.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    """Yield an async DB session. Callers MUST call ``await db.commit()``
    explicitly in write endpoints — the session does NOT auto-commit."""
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
