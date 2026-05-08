import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from app.core.config import settings
from app.core.database import Base
import app.models  # noqa

config = context.config
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

# alembic 기본 alembic_version.version_num은 VARCHAR(32). 이 프로젝트는 슬러그가 있는
# revision id (예: 0012_add_exposure_content_link_uniqueness, 41자)를 사용하므로
# alembic이 자체 생성하는 컬럼 폭으로는 UPDATE가 실패한다. 첫 마이그레이션 전에 명시적으로
# 더 큰 컬럼으로 테이블을 만들어 두면 alembic은 기존 테이블을 그대로 사용한다.
_ALEMBIC_VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(255) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
)
"""


def _ensure_version_table(connection: Connection) -> None:
    """Pre-create alembic_version with VARCHAR(255) before alembic auto-creates VARCHAR(32)."""
    with connection.begin():
        connection.execute(text(_ALEMBIC_VERSION_TABLE_DDL))


def run_migrations_offline():
    context.configure(url=settings.SYNC_DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection):
    _ensure_version_table(connection)
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        {"sqlalchemy.url": settings.DATABASE_URL}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
