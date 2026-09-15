"""Add hospital_physicians rows and hospitals.address_detail.

Revision ID: 0078_add_hospital_physicians
Revises: 0077_inquiry_intake_automation

공동원장 병원은 `director_name`에 "김성열 · 전상훈"처럼 한 칸으로만 들어갈 수 있었고,
사진·약력·자격은 한 명분만 남았다. 의료진을 행으로 옮기고, 기존 `director_*` 값은
대표 의료진 1행으로 백필한다 — 기존 공개 표면이 읽는 병원 단위 컬럼은 그대로 둔다.

Additive only. 재실행해도 같은 결과가 되도록 테이블·컬럼 존재를 먼저 확인한다.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0078_add_hospital_physicians"
down_revision: str | None = "0077_inquiry_intake_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in _inspector().get_columns(table))


def upgrade() -> None:
    if not _has_column("hospitals", "address_detail"):
        op.add_column("hospitals", sa.Column("address_detail", sa.String(length=200), nullable=True))

    if _inspector().has_table("hospital_physicians"):
        return

    op.create_table(
        "hospital_physicians",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=50), nullable=True),
        sa.Column("specialties", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("career", sa.Text(), nullable=True),
        sa.Column("credentials", sa.JSON(), nullable=True),
        sa.Column("photo_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_representative", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["photo_source_id"], ["hospital_source_assets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hospital_physicians_hospital_id", "hospital_physicians", ["hospital_id"]
    )

    # 기존 원장 정보를 대표 의료진 1행으로 옮긴다. 사진은 근거 자료 행과의 연결을
    # 사람이 확인해야 하므로 비워 둔다 — 공개 표면은 종전의 최신 인증 사진 선택을 계속 쓴다.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, director_name, director_career, director_credentials"
            " FROM hospitals"
            " WHERE director_name IS NOT NULL AND btrim(director_name) <> ''"
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "INSERT INTO hospital_physicians ("
                " id, hospital_id, name, title, specialties, career, credentials,"
                " photo_source_id, display_order, is_representative"
                ") VALUES ("
                " :id, :hospital_id, :name, '원장', '[]', :career, :credentials,"
                " NULL, 0, true)"
            ).bindparams(sa.bindparam("credentials", type_=sa.JSON())),
            {
                "id": uuid.uuid4(),
                "hospital_id": row["id"],
                "name": row["director_name"],
                "career": row["director_career"],
                "credentials": row["director_credentials"],
            },
        )


def downgrade() -> None:
    if _inspector().has_table("hospital_physicians"):
        op.drop_index("ix_hospital_physicians_hospital_id", table_name="hospital_physicians")
        op.drop_table("hospital_physicians")
    if _has_column("hospitals", "address_detail"):
        op.drop_column("hospitals", "address_detail")
