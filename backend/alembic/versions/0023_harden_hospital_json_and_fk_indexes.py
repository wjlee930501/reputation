"""harden hospital JSON list columns (NOT NULL) and add covering FK indexes

DATA-2: region/specialties/keywords/competitors/treatments were nullable; a NULL
(creatable via raw SQL/partial write) crashes the public /site render path. Backfill
to '[]', then SET DEFAULT '[]' + SET NOT NULL.

DATA-3: add covering indexes for four FK columns that drive cascade-delete and
hospital-scoped scans.

Revision ID: 0023_harden_hospital_json_and_fk_indexes
Revises: 0022_add_admin_users
Create Date: 2026-06-08
"""
from alembic import op

revision = "0023_harden_hospital_json_and_fk_indexes"
down_revision = "0022_add_admin_users"
branch_labels = None
depends_on = None

_JSON_LIST_COLUMNS = ("region", "specialties", "keywords", "competitors", "treatments")
_FK_INDEXES = (
    ("ix_content_schedules_hospital_id", "content_schedules", "hospital_id"),
    ("ix_content_items_schedule_id", "content_items", "schedule_id"),
    ("ix_query_matrix_hospital_id", "query_matrix", "hospital_id"),
    ("ix_exposure_actions_linked_report_id", "exposure_actions", "linked_report_id"),
)


def upgrade() -> None:
    # DATA-2 — backfill, then enforce NOT NULL + server default. Rewrite SQL NULL,
    # the JSON literal 'null', and any non-array value to '[]' so the public /site
    # render (which assumes arrays) cannot crash on a stray value.
    for col in _JSON_LIST_COLUMNS:
        op.execute(
            f"UPDATE hospitals SET {col} = '[]'::json "
            f"WHERE {col} IS NULL OR json_typeof({col}) <> 'array'"
        )
        op.execute(f"ALTER TABLE hospitals ALTER COLUMN {col} SET DEFAULT '[]'::json")
        op.execute(f"ALTER TABLE hospitals ALTER COLUMN {col} SET NOT NULL")

    # DATA-3 — covering FK indexes (idempotent guard for partially-migrated envs).
    for name, table, col in _FK_INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON {table} ("{col}")')


def downgrade() -> None:
    for name, table, _col in _FK_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS "{name}"')

    for col in _JSON_LIST_COLUMNS:
        op.execute(f"ALTER TABLE hospitals ALTER COLUMN {col} DROP NOT NULL")
        op.execute(f"ALTER TABLE hospitals ALTER COLUMN {col} DROP DEFAULT")
