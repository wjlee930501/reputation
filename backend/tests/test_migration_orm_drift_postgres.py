"""마이그레이션 체인과 ORM 모델의 **컬럼 집합**이 실제로 일치하는지 확인한다.

`test_migration_orm_drift.py`는 0053/0054가 추가한 컬럼 4개를 손으로 적어 둔 목록이다.
그 방식은 다음 마이그레이션에서 같은 실수가 나면 아무것도 못 잡는다 — 목록을 사람이
갱신해야 하기 때문이다. 이 테스트는 목록을 없앤다: 실제 PostgreSQL(head까지 업그레이드된
`reputation_test`)에 붙어 alembic autogenerate 비교를 돌리고, `add_column`/`remove_column`
차이가 하나도 없어야 통과한다.

`alter_column`(타입·nullable·comment)과 인덱스 차이는 의도적으로 무시한다 — 기존에도
존재하던 잡음이고(핸드오프 문서 기재), 여기서 함께 막으면 이 테스트가 상시 실패로
꺼져 버린다. 컬럼 존재 여부만으로도 "모델에 없는 컬럼을 autogenerate가 drop하자고
제안하는" 사고는 전부 잡힌다.
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import app.models  # noqa: F401 — Base.metadata에 모든 모델을 등록시킨다
from app.core.database import Base

_SYNC_URL = os.getenv(
    "TASK19_SYNC_DATABASE_URL",
    "postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test",
)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 컬럼 존재 여부만 본다. 나머지 diff 종류는 기존 잡음이라 여기서 판정하지 않는다.
_COLUMN_DIFF_KINDS = frozenset({"add_column", "remove_column"})


def _script_head() -> str:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def _flatten(diffs) -> list[tuple]:
    """compare_metadata는 modify_* 묶음을 중첩 리스트로 돌려준다."""
    flat: list[tuple] = []
    for diff in diffs:
        if isinstance(diff, list):
            flat.extend(diff)
        else:
            flat.append(diff)
    return flat


def _describe(diff: tuple) -> str:
    # ('add_column'|'remove_column', schema, table_name, Column)
    return f"{diff[0]} {diff[2]}.{diff[3].name}"


def test_migrated_database_has_no_column_drift_against_the_orm() -> None:
    engine = create_engine(_SYNC_URL)
    try:
        with engine.connect() as connection:
            applied = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
            assert applied == [_script_head()], (
                "reputation_test가 마이그레이션 head가 아닙니다. "
                "`alembic upgrade head`를 먼저 실행하세요. "
                f"(applied={applied}, head={_script_head()})"
            )

            context = MigrationContext.configure(connection)
            diffs = _flatten(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()

    column_drift = [diff for diff in diffs if diff[0] in _COLUMN_DIFF_KINDS]
    assert column_drift == [], (
        "마이그레이션과 ORM 모델의 컬럼이 어긋납니다 — autogenerate가 이 컬럼들을 "
        "추가/삭제하자고 제안합니다: " + ", ".join(_describe(diff) for diff in column_drift)
    )
