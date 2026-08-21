"""Add durable provenance verification for hospital photo assets.

Rollout gate: every existing public PHOTO_* row becomes private. No truthful owner,
rights basis, evidence reference, or verifier can be inferred from legacy rows. An
active operator must reclassify and re-approve each photo after deployment.

Revision ID: 0052_add_photo_asset_provenance
Revises: 0051_add_hospital_visual_identity
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0052_add_photo_asset_provenance"
down_revision: str | None = "0051_add_hospital_visual_identity"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE hospital_source_type ADD VALUE IF NOT EXISTS 'PHOTO_BRAND'")
    op.add_column("hospital_source_assets", sa.Column("photo_source_owner", sa.String(200)))
    op.add_column("hospital_source_assets", sa.Column("photo_rights_basis", sa.String(32)))
    op.add_column(
        "hospital_source_assets",
        sa.Column("photo_evidence_reference", sa.String(500)),
    )
    op.add_column("hospital_source_assets", sa.Column("photo_verified_by", sa.String(320)))
    op.add_column(
        "hospital_source_assets",
        sa.Column("photo_verified_at", sa.DateTime(timezone=True)),
    )
    # Existing public classifications were inferred without durable evidence. They
    # must be reviewed again rather than grandfathered into the public surface.
    op.execute(
        "UPDATE hospital_source_assets SET is_public = false "
        "WHERE CAST(source_type AS VARCHAR) LIKE 'PHOTO_%'"
    )
    op.create_check_constraint(
        "ck_public_photo_requires_provenance",
        "hospital_source_assets",
        "NOT is_public OR CAST(source_type AS VARCHAR) NOT LIKE 'PHOTO_%' OR ("
        "photo_source_owner IS NOT NULL AND photo_source_owner <> '' AND "
        "photo_rights_basis IN ('LICENSE', 'OWNER_CONSENT') AND "
        "photo_evidence_reference IS NOT NULL AND photo_evidence_reference <> '' AND "
        "photo_verified_by IS NOT NULL AND photo_verified_by <> '' AND "
        "photo_verified_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_public_photo_requires_provenance",
        "hospital_source_assets",
        type_="check",
    )
    op.drop_column("hospital_source_assets", "photo_verified_at")
    op.drop_column("hospital_source_assets", "photo_verified_by")
    op.drop_column("hospital_source_assets", "photo_evidence_reference")
    op.drop_column("hospital_source_assets", "photo_rights_basis")
    op.drop_column("hospital_source_assets", "photo_source_owner")
    # PHOTO_BRAND is an additive PostgreSQL enum value; retaining it is safer than
    # rewriting the live enum and table during rollback.
