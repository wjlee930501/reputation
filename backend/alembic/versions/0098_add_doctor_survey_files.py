"""Add doctor_surveys.files: files a doctor uploads in the pre-onboarding survey.

Photos, logo and certificates the doctor attaches are kept in the survey's own file list
(storage reference, kind, sniffed type). They are not photo sources yet — an AE classifies
and registers them. Additive with a default, so no existing row changes.

Revision ID: 0098_add_doctor_survey_files
Revises: 0097_add_doctor_surveys
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0098_add_doctor_survey_files"
down_revision: str | None = "0097_add_doctor_surveys"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "doctor_surveys",
        sa.Column(
            "files",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("doctor_surveys", "files")
