"""Separate monthly manifest recovery from protocol supersede.

Revision ID: 0064_manifest_recovery_guard
Revises: 0063_add_monthly_sov_cohort
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_manifest_recovery_guard"
down_revision: str | None = "0063_add_monthly_sov_cohort"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_monthly_manifest_immutability() "
            "RETURNS trigger AS $$ BEGIN "
            "IF current_setting('app.monthly_manifest_recovery', true) = 'on' THEN "
            "IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL AND "
            "NEW.id IS NOT DISTINCT FROM OLD.id AND "
            "NEW.hospital_id IS NOT DISTINCT FROM OLD.hospital_id AND "
            "NEW.period_year IS NOT DISTINCT FROM OLD.period_year AND "
            "NEW.period_month IS NOT DISTINCT FROM OLD.period_month AND "
            "NEW.configured_platforms IS NOT DISTINCT FROM OLD.configured_platforms AND "
            "NEW.platform_provenance IS NOT DISTINCT FROM OLD.platform_provenance AND "
            "NEW.frozen_at IS NOT DISTINCT FROM OLD.frozen_at AND "
            "NEW.closes_at IS NOT DISTINCT FROM OLD.closes_at THEN "
            "RETURN NEW; END IF; "
            "RAISE EXCEPTION 'monthly manifest recovery may only clear close'; END IF; "
            "IF current_setting('app.monthly_manifest_supersede', true) = 'on' THEN "
            "IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL THEN "
            "RAISE EXCEPTION 'closed monthly manifest cannot be superseded'; END IF; "
            "IF NEW.id IS DISTINCT FROM OLD.id OR "
            "NEW.hospital_id IS DISTINCT FROM OLD.hospital_id OR "
            "NEW.period_year IS DISTINCT FROM OLD.period_year OR "
            "NEW.period_month IS DISTINCT FROM OLD.period_month OR "
            "NEW.closed_at IS DISTINCT FROM OLD.closed_at THEN "
            "RAISE EXCEPTION 'monthly manifest supersede may only replace freeze'; END IF; "
            "RETURN NEW; END IF; "
            "IF NEW.hospital_id IS DISTINCT FROM OLD.hospital_id OR "
            "NEW.period_year IS DISTINCT FROM OLD.period_year OR "
            "NEW.period_month IS DISTINCT FROM OLD.period_month OR "
            "NEW.configured_platforms IS DISTINCT FROM OLD.configured_platforms OR "
            "NEW.platform_provenance IS DISTINCT FROM OLD.platform_provenance OR "
            "NEW.frozen_at IS DISTINCT FROM OLD.frozen_at OR "
            "NEW.closes_at IS DISTINCT FROM OLD.closes_at THEN "
            "RAISE EXCEPTION 'monthly manifest is immutable'; END IF; "
            "IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at "
            "THEN RAISE EXCEPTION 'monthly manifest close is immutable'; END IF; "
            "IF NEW.closed_at IS NOT NULL AND NEW.closed_at < NEW.closes_at THEN "
            "RAISE EXCEPTION 'monthly manifest cannot close before cutoff'; END IF; "
            "RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_monthly_manifest_immutability() "
            "RETURNS trigger AS $$ BEGIN "
            "IF current_setting('app.monthly_manifest_supersede', true) = 'on' THEN "
            "RETURN NEW; END IF; "
            "IF NEW.hospital_id IS DISTINCT FROM OLD.hospital_id OR "
            "NEW.period_year IS DISTINCT FROM OLD.period_year OR "
            "NEW.period_month IS DISTINCT FROM OLD.period_month OR "
            "NEW.configured_platforms IS DISTINCT FROM OLD.configured_platforms OR "
            "NEW.platform_provenance IS DISTINCT FROM OLD.platform_provenance OR "
            "NEW.frozen_at IS DISTINCT FROM OLD.frozen_at OR "
            "NEW.closes_at IS DISTINCT FROM OLD.closes_at THEN "
            "RAISE EXCEPTION 'monthly manifest is immutable'; END IF; "
            "IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at "
            "THEN RAISE EXCEPTION 'monthly manifest close is immutable'; END IF; "
            "IF NEW.closed_at IS NOT NULL AND NEW.closed_at < NEW.closes_at THEN "
            "RAISE EXCEPTION 'monthly manifest cannot close before cutoff'; END IF; "
            "RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
