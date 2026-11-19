"""Add doctor_surveys: the pre-onboarding questionnaire a doctor fills in through a link.

An AE issues the link from the hospital info tab; the doctor writes the answers on the
public site. One row per hospital. Purely additive — no existing row or page changes.

Revision ID: 0097_add_doctor_surveys
Revises: 0096_add_hospital_page_copy
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0097_add_doctor_surveys"
down_revision: str | None = "0096_add_hospital_page_copy"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "doctor_surveys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("link_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("link_issued_by", sa.String(length=255), nullable=False),
        sa.Column("new_clinic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=True),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("current_step", sa.String(length=400), nullable=True),
        sa.Column("client_session", sa.String(length=40), nullable=True),
        sa.Column("client_seq", sa.BigInteger(), nullable=True),
        sa.Column("respondent", sa.String(length=20), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_version", sa.String(length=40), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("submitted_answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("interview_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["interview_source_id"], ["hospital_source_assets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hospital_id", name="uq_doctor_surveys_hospital_id"),
        sa.UniqueConstraint("token_hash", name="uq_doctor_surveys_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("doctor_surveys")
