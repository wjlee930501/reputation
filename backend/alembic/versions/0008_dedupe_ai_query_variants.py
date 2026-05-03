"""dedupe ai query variants and enforce uniqueness

Revision ID: 0008_dedupe_ai_query_variants
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_dedupe_ai_query_variants"
down_revision = "0007"
branch_labels = None
depends_on = None

UNIQUE_NAME = "uq_ai_query_variants_target_text_platform_language"


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == constraint_name for item in inspector.get_unique_constraints(table_name))


def upgrade() -> None:
    if not _has_table("ai_query_variants"):
        return

    # Keep the earliest variant for each strategy/platform/language/query tuple.
    # This also repairs local/dev data created by repeated button clicks before the guard existed.
    op.execute(
        """
        DELETE FROM ai_query_variants duplicate
        USING ai_query_variants keeper
        WHERE duplicate.query_target_id = keeper.query_target_id
          AND duplicate.query_text = keeper.query_text
          AND duplicate.platform = keeper.platform
          AND duplicate.language = keeper.language
          AND (
            duplicate.created_at > keeper.created_at
            OR (duplicate.created_at = keeper.created_at AND duplicate.id::text > keeper.id::text)
          )
        """
    )

    if not _has_constraint("ai_query_variants", UNIQUE_NAME):
        op.create_unique_constraint(
            UNIQUE_NAME,
            "ai_query_variants",
            ["query_target_id", "query_text", "platform", "language"],
        )


def downgrade() -> None:
    if _has_table("ai_query_variants") and _has_constraint("ai_query_variants", UNIQUE_NAME):
        op.drop_constraint(UNIQUE_NAME, "ai_query_variants", type_="unique")
