"""Allow an authoritative month-end protocol to supersede a monthly manifest.

The application keeps the manifest row/id because reports reference it with a
RESTRICT foreign key. A transaction-local guard allows that narrow replacement
to reset the manifest and delete its old cells/attempt links; all ordinary
manifest mutations and direct attempt deletions remain blocked.

Revision ID: 0062_allow_monthly_manifest_protocol_supersede
Revises: 0061_allow_system_incident_acknowledgement
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062_allow_monthly_manifest_protocol_supersede"
down_revision: str | None = "0061_allow_system_incident_acknowledgement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_monthly_attempt_append_only() "
            "RETURNS trigger AS $$ BEGIN "
            "IF TG_OP = 'DELETE' AND "
            "current_setting('app.monthly_manifest_supersede', true) = 'on' THEN "
            "RETURN OLD; END IF; "
            "IF TG_OP <> 'INSERT' THEN "
            "RAISE EXCEPTION 'monthly attempts are append-only'; END IF; "
            "IF EXISTS (SELECT 1 FROM monthly_measurement_cells c "
            "JOIN monthly_measurement_manifests m ON m.id=c.manifest_id "
            "WHERE c.id=NEW.cell_id AND "
            "(c.state='EXCLUDED' OR m.closed_at IS NOT NULL)) THEN "
            "RAISE EXCEPTION 'attempt cannot link to excluded or closed cell'; END IF; "
            "RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_monthly_manifest_immutability() "
            "RETURNS trigger AS $$ BEGIN "
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
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_monthly_attempt_append_only() "
            "RETURNS trigger AS $$ BEGIN "
            "IF TG_OP <> 'INSERT' THEN "
            "RAISE EXCEPTION 'monthly attempts are append-only'; END IF; "
            "IF EXISTS (SELECT 1 FROM monthly_measurement_cells c "
            "JOIN monthly_measurement_manifests m ON m.id=c.manifest_id "
            "WHERE c.id=NEW.cell_id AND "
            "(c.state='EXCLUDED' OR m.closed_at IS NOT NULL)) THEN "
            "RAISE EXCEPTION 'attempt cannot link to excluded or closed cell'; END IF; "
            "RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
