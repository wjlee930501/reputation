"""add reversible withheld content status

Revision ID: 0081_add_withheld_content_status
Revises: 0080_lead_diagnosis_supersede

`WITHHELD`(비공개·보존)는 공개됐던 글을 본문·참고자료·이미지·발행 이력을 그대로 둔 채
공개 사이트에서 내리는 상태다. 공개 목록·상세·이미지·sitemap·IndexNow·이미지 교체·
재생성은 모두 `status == PUBLISHED`(또는 DRAFT/READY/REJECTED)로 대상을 고르므로, 새
상태의 행은 코드를 따로 고치지 않아도 그 경로에서 빠진다(fail-closed).

Additive only — enum 값 하나를 더하고 어떤 행도 바꾸지 않는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0081_add_withheld_content_status"
down_revision: str | None = "0080_lead_diagnosis_supersede"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALUE = "WITHHELD"

# contentstatus 타입(또는 그 배열)을 쓰는 모든 컬럼. 지금은 content_items.status 하나지만
# 롤백 안전 검사는 스키마 전체를 본다 — 나중에 다른 테이블이 같은 타입을 쓰게 되면
# 그 행도 옛 코드가 읽지 못하는 값이다.
_CONTENTSTATUS_COLUMNS_SQL = sa.text(
    """
    SELECT n.nspname, c.relname, a.attname, (t.typelem <> 0) AS is_array
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
    WHERE c.relkind IN ('r', 'p')
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND (t.oid = to_regtype('contentstatus') OR t.typelem = to_regtype('contentstatus'))
    ORDER BY n.nspname, c.relname, a.attname
    """
)


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE는 새 값을 같은 트랜잭션에서 쓸 수 없고, 버전에 따라 트랜잭션
    # 블록 안에서 실행 자체가 거부된다. 자동 커밋 구간에서 단독으로 실행한다.
    # IF NOT EXISTS라 재실행·downgrade 뒤 재-upgrade도 멱등하다.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE contentstatus ADD VALUE IF NOT EXISTS '{_VALUE}'")


def _withheld_row_counts(bind) -> list[tuple[str, int]]:
    quote = bind.dialect.identifier_preparer.quote
    counts: list[tuple[str, int]] = []
    for schema, table, column, is_array in bind.execute(_CONTENTSTATUS_COLUMNS_SQL):
        qualified = f"{quote(schema)}.{quote(table)}"
        # `::text`로 비교한다. 이 값이 없는 DB(이미 되돌렸거나 0081이 적용되지 않은
        # 복제본)에서 `status = 'WITHHELD'`는 enum 리터럴 변환 오류로 실패한다.
        predicate = (
            f"{quote(column)}::text[] @> ARRAY['{_VALUE}']"
            if is_array
            else f"{quote(column)}::text = '{_VALUE}'"
        )
        count = bind.execute(
            sa.text(f"SELECT count(*) FROM {qualified} WHERE {predicate}")
        ).scalar_one()
        if count:
            counts.append((f"{schema}.{table}.{column}", int(count)))
    return counts


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "0081 downgrade는 오프라인(--sql) 모드를 지원하지 않습니다 — WITHHELD 행 수를 "
            "확인할 수 없습니다. / 0081 downgrade cannot run in offline (--sql) mode because "
            "it must verify that no WITHHELD rows remain."
        )
    counts = _withheld_row_counts(op.get_bind())
    if counts:
        detail = ", ".join(f"{column}={count}" for column, count in counts)
        raise RuntimeError(
            "WITHHELD(비공개·보존) 상태의 글이 남아 있어 0081을 되돌릴 수 없습니다 "
            f"({detail}). 먼저 restore(다시 공개) 또는 reject(반려)로 모두 정리한 뒤 다시 "
            "실행하세요. 옛 코드는 이 값을 읽으면 enum LookupError로 실패합니다. / "
            f"Cannot downgrade 0081: WITHHELD content rows remain ({detail}). Restore or "
            "reject those rows first; code before 0081 fails with an enum LookupError when "
            "it loads them."
        )
    # 남은 행이 0건이면 의도적으로 아무것도 하지 않는다(0033과 같은 선례).
    # - PostgreSQL은 enum 값을 제거하는 문법이 없다. 제거하려면 타입을 새로 만들고
    #   (rename → create → ALTER COLUMN ... TYPE ... USING status::text::contentstatus →
    #   drop) 모든 컬럼을 옮겨야 하는데, 이는 content_items 전체를 다시 쓰며 롤백 도중
    #   ACCESS EXCLUSIVE 잠금으로 공개 사이트·워커를 멈춘다. 컬럼 기본값과 이 타입을
    #   참조하는 부분 인덱스·조건식도 함께 떼었다 다시 붙여야 해 실패 지점이 늘어난다.
    # - 위 검사로 이 값을 가진 행이 없음이 확인됐으므로 남는 enum 값은 옛 코드가 만날
    #   수 없다(옛 코드는 이 값을 쓰지 않는다).
    # - upgrade가 `ADD VALUE IF NOT EXISTS`라 다시 올려도 멱등하다.
