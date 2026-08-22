"""Backfill asset_kind/approved_usage for photos stored before classification existed.

Revision ID: 0052_backfill_photo_asset_kind
Revises: 0051_add_hospital_visual_identity

Photo classification was added as `source_metadata.asset_kind` without a data
migration, so every photo uploaded before it has no role.  Those rows were
readable on the public surface only because the site tolerates missing metadata,
and re-publishing one (toggling `is_public` back on) failed validation with 422.

The backfill writes the same conservative role the read path already implies:
facility photos stay hero/gallery candidates, doctor photos stay editorial and
are never promoted to a verified identity.  Rows touched here carry
`asset_kind_source = 'LEGACY_BACKFILL'` and `needs_operator_review = true` so an
AE can confirm the real classification without hunting for them.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0052_backfill_photo_asset_kind"
down_revision: str | None = "0051_add_hospital_visual_identity"
branch_labels: str | None = None
depends_on: str | None = None


PHOTO_SOURCE_TYPES = (
    "PHOTO_DOCTOR",
    "PHOTO_CLINIC_EXTERIOR",
    "PHOTO_CLINIC_INTERIOR",
    "PHOTO_TREATMENT_ROOM",
)

LEGACY_BACKFILL = "LEGACY_BACKFILL"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE hospital_source_assets
               SET source_metadata = COALESCE(source_metadata, '{}'::jsonb)
                   || jsonb_build_object(
                       'asset_kind',
                       CASE WHEN source_type::text = 'PHOTO_DOCTOR'
                            THEN 'EDITORIAL_GRAPHIC'
                            ELSE 'VERIFIED_FACILITY'
                       END,
                       'approved_usage',
                       CASE WHEN source_type::text = 'PHOTO_DOCTOR'
                            THEN jsonb_build_array('CONTENT_EDITORIAL')
                            ELSE jsonb_build_array('HERO', 'GALLERY')
                       END,
                       'asset_kind_source', :legacy_source,
                       'needs_operator_review', true
                   )
             WHERE source_type::text IN :photo_types
               AND (
                   source_metadata IS NULL
                   OR jsonb_typeof(source_metadata) <> 'object'
                   OR source_metadata->>'asset_kind' IS NULL
               )
            """
        ).bindparams(
            sa.bindparam("photo_types", value=PHOTO_SOURCE_TYPES, expanding=True),
            sa.bindparam("legacy_source", value=LEGACY_BACKFILL),
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE hospital_source_assets
               SET source_metadata = source_metadata
                   - 'asset_kind'
                   - 'approved_usage'
                   - 'asset_kind_source'
                   - 'needs_operator_review'
             WHERE source_type::text IN :photo_types
               AND jsonb_typeof(source_metadata) = 'object'
               AND source_metadata->>'asset_kind_source' = :legacy_source
            """
        ).bindparams(
            sa.bindparam("photo_types", value=PHOTO_SOURCE_TYPES, expanding=True),
            sa.bindparam("legacy_source", value=LEGACY_BACKFILL),
        )
    )
