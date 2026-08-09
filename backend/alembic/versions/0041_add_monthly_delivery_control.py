"""add immutable monthly delivery control

Revision ID: 0041_add_monthly_delivery_control
Revises: 0040_add_hospital_handoffs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041_add_monthly_delivery_control"
down_revision: str | None = "0040_add_hospital_handoffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    op.create_table(
        "monthly_measurement_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("configured_platforms", postgresql.JSONB(), nullable=False),
        sa.Column("platform_provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("period_month BETWEEN 1 AND 12", name="ck_monthly_manifest_month"),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "hospital_id", "period_year", "period_month", name="uq_monthly_manifest_period"
        ),
    )
    op.create_table(
        "monthly_measurement_cells",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_key", sa.String(100), nullable=False),
        sa.Column("query_text", sa.String(500), nullable=False),
        sa.Column("query_matrix_id", postgresql.UUID(as_uuid=True)),
        sa.Column("query_target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("query_variant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), server_default="FAILED", nullable=False),
        sa.Column("exclusion_reason", sa.String(50)),
        sa.Column("excluded_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("excluded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("platform IN ('chatgpt', 'gemini')", name="ck_monthly_cell_platform"),
        sa.CheckConstraint(
            "state IN ('SUCCESS', 'FAILED', 'EXCLUDED')", name="ck_monthly_cell_state"
        ),
        sa.CheckConstraint(
            "(state = 'EXCLUDED' AND exclusion_reason IN ('DUPLICATE_TARGET', 'RETIRED_BEFORE_MEASUREMENT', 'LEGAL_REMOVAL') AND excluded_by_id IS NOT NULL AND excluded_at IS NOT NULL) OR (state <> 'EXCLUDED' AND exclusion_reason IS NULL AND excluded_by_id IS NULL AND excluded_at IS NULL)",
            name="ck_monthly_cell_exclusion",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["monthly_measurement_manifests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["query_matrix_id"], ["query_matrix.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["query_target_id"], ["ai_query_targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["query_variant_id"], ["ai_query_variants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["excluded_by_id"], ["admin_users.id"]),
        sa.UniqueConstraint(
            "manifest_id", "query_key", "platform", name="uq_monthly_cell_key_platform"
        ),
    )
    op.create_table(
        "monthly_measurement_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sov_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["cell_id"], ["monthly_measurement_cells.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sov_record_id"], ["sov_records.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("sov_record_id", name="uq_monthly_attempt_sov_record"),
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION enforce_monthly_manifest_immutability() RETURNS trigger AS $$ "
            "BEGIN IF NEW.hospital_id IS DISTINCT FROM OLD.hospital_id OR NEW.period_year IS DISTINCT FROM OLD.period_year OR NEW.period_month IS DISTINCT FROM OLD.period_month OR NEW.configured_platforms IS DISTINCT FROM OLD.configured_platforms OR NEW.platform_provenance IS DISTINCT FROM OLD.platform_provenance OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at OR NEW.closes_at IS DISTINCT FROM OLD.closes_at THEN RAISE EXCEPTION 'monthly manifest is immutable'; END IF; IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at THEN RAISE EXCEPTION 'monthly manifest close is immutable'; END IF; IF NEW.closed_at IS NOT NULL AND NEW.closed_at < NEW.closes_at THEN RAISE EXCEPTION 'monthly manifest cannot close before cutoff'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_monthly_manifest_immutable BEFORE UPDATE ON monthly_measurement_manifests FOR EACH ROW EXECUTE FUNCTION enforce_monthly_manifest_immutability()"
        )
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION enforce_monthly_cell_transition() RETURNS trigger AS $$ "
            "BEGIN IF NEW.manifest_id IS DISTINCT FROM OLD.manifest_id OR NEW.query_key IS DISTINCT FROM OLD.query_key OR NEW.query_text IS DISTINCT FROM OLD.query_text OR NEW.query_matrix_id IS DISTINCT FROM OLD.query_matrix_id OR NEW.query_target_id IS DISTINCT FROM OLD.query_target_id OR NEW.query_variant_id IS DISTINCT FROM OLD.query_variant_id OR NEW.platform IS DISTINCT FROM OLD.platform THEN RAISE EXCEPTION 'monthly cell identity is immutable'; END IF; IF OLD.state = 'EXCLUDED' AND NEW.state <> 'EXCLUDED' THEN RAISE EXCEPTION 'excluded cell is terminal'; END IF; IF NEW.state = 'EXCLUDED' AND OLD.state <> 'EXCLUDED' THEN IF EXISTS (SELECT 1 FROM monthly_measurement_attempts WHERE cell_id = OLD.id) THEN RAISE EXCEPTION 'attempted cell cannot be excluded'; END IF; IF EXISTS (SELECT 1 FROM monthly_measurement_manifests WHERE id = OLD.manifest_id AND closed_at IS NOT NULL) THEN RAISE EXCEPTION 'closed manifest cannot be excluded'; END IF; IF NOT EXISTS (SELECT 1 FROM admin_users WHERE id = NEW.excluded_by_id AND role = 'OWNER') THEN RAISE EXCEPTION 'monthly exclusion requires OWNER'; END IF; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_monthly_cell_transition BEFORE UPDATE ON monthly_measurement_cells FOR EACH ROW EXECUTE FUNCTION enforce_monthly_cell_transition()"
        )
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION enforce_monthly_attempt_append_only() RETURNS trigger AS $$ "
            "BEGIN IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'monthly attempts are append-only'; END IF; IF EXISTS (SELECT 1 FROM monthly_measurement_cells c JOIN monthly_measurement_manifests m ON m.id=c.manifest_id WHERE c.id=NEW.cell_id AND (c.state='EXCLUDED' OR m.closed_at IS NOT NULL)) THEN RAISE EXCEPTION 'attempt cannot link to excluded or closed cell'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_monthly_attempt_append_only BEFORE INSERT OR UPDATE OR DELETE ON monthly_measurement_attempts FOR EACH ROW EXECUTE FUNCTION enforce_monthly_attempt_append_only()"
        )
    )
    op.create_table(
        "hospital_service_intervals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("provenance", sa.String(30), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at > started_at", name="ck_service_interval_order"
        ),
        sa.CheckConstraint(
            "provenance IN ('LEGACY_CUTOVER', 'ACTIVATION', 'RESUME')",
            name="ck_service_interval_provenance",
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"], ondelete="CASCADE"),
    )
    op.execute(
        sa.text(
            "ALTER TABLE hospital_service_intervals ADD CONSTRAINT "
            "excl_service_intervals_no_overlap EXCLUDE USING gist "
            "(hospital_id WITH =, tstzrange(started_at, ended_at, '[)') WITH &&)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO hospital_service_intervals (id, hospital_id, started_at, provenance) SELECT id, id, now(), 'LEGACY_CUTOVER' FROM hospitals WHERE status = 'ACTIVE'"
        )
    )

    op.add_column("monthly_reports", sa.Column("manifest_id", postgresql.UUID(as_uuid=True)))
    op.add_column("monthly_reports", sa.Column("version", sa.Integer()))
    op.add_column(
        "monthly_reports", sa.Column("supersedes_report_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("monthly_reports", sa.Column("cutoff_at", sa.DateTime(timezone=True)))
    op.add_column("monthly_reports", sa.Column("quality", sa.String(30)))
    for name in ("planned_count", "success_count", "failed_count", "excluded_count"):
        op.add_column(
            "monthly_reports", sa.Column(name, sa.Integer(), server_default="0", nullable=False)
        )
    op.add_column(
        "monthly_reports",
        sa.Column("customer_ready", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "monthly_reports",
        sa.Column(
            "delivery_blockers",
            postgresql.JSONB(),
            server_default=sa.text("'[\"LEGACY_REPORT_UNVERIFIED\"]'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "WITH ranked AS (SELECT id, row_number() OVER (PARTITION BY hospital_id, period_year, period_month, report_type ORDER BY created_at, id) AS v FROM monthly_reports) UPDATE monthly_reports r SET version = ranked.v, quality = 'LEGACY_UNVERIFIED' FROM ranked WHERE r.id = ranked.id"
        )
    )
    op.alter_column("monthly_reports", "version", nullable=False, server_default="1")
    op.alter_column(
        "monthly_reports", "quality", nullable=False, server_default="LEGACY_UNVERIFIED"
    )
    op.drop_constraint("uq_monthly_reports_hospital_period_type", "monthly_reports", type_="unique")
    op.create_foreign_key(
        "fk_monthly_reports_manifest",
        "monthly_reports",
        "monthly_measurement_manifests",
        ["manifest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monthly_reports_supersedes",
        "monthly_reports",
        "monthly_reports",
        ["supersedes_report_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_monthly_reports_period_version",
        "monthly_reports",
        ["hospital_id", "period_year", "period_month", "report_type", "version"],
    )
    op.create_unique_constraint(
        "uq_monthly_reports_supersedes", "monthly_reports", ["supersedes_report_id"]
    )
    op.create_check_constraint(
        "ck_monthly_reports_quality",
        "monthly_reports",
        "quality IN ('LEGACY_UNVERIFIED', 'COMPLETE', 'DEGRADED', 'BLOCKED')",
    )
    op.create_check_constraint(
        "ck_monthly_reports_counts",
        "monthly_reports",
        "planned_count >= 0 AND success_count >= 0 AND failed_count >= 0 AND excluded_count >= 0 AND success_count + failed_count = planned_count",
    )
    op.create_check_constraint(
        "ck_monthly_reports_customer_ready",
        "monthly_reports",
        "customer_ready = false OR quality = 'COMPLETE'",
    )
    op.create_check_constraint(
        "ck_monthly_reports_version_chain",
        "monthly_reports",
        "(version = 1 AND supersedes_report_id IS NULL) OR "
        "(version > 1 AND supersedes_report_id IS NOT NULL)",
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION enforce_monthly_report_version_chain() RETURNS trigger AS $$ "
            "DECLARE parent monthly_reports%ROWTYPE; BEGIN IF NEW.version > 1 THEN "
            "SELECT * INTO parent FROM monthly_reports WHERE id=NEW.supersedes_report_id; "
            "IF NOT FOUND OR parent.hospital_id<>NEW.hospital_id OR parent.period_year<>NEW.period_year OR parent.period_month<>NEW.period_month OR parent.report_type<>NEW.report_type OR NEW.version<>parent.version+1 THEN RAISE EXCEPTION 'invalid monthly report supersedes chain'; END IF; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_monthly_report_version_chain BEFORE INSERT OR UPDATE ON monthly_reports FOR EACH ROW EXECUTE FUNCTION enforce_monthly_report_version_chain()"
        )
    )

    op.create_table(
        "monthly_report_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("validated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("validated_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("validation_metadata", postgresql.JSONB()),
        *_timestamps(),
        sa.CheckConstraint("audience IN ('AE', 'DOCTOR')", name="ck_monthly_artifact_audience"),
        sa.CheckConstraint(
            "byte_size > 0 AND length(sha256) = 64", name="ck_monthly_artifact_integrity"
        ),
        sa.CheckConstraint(
            "(validated = false AND validated_at IS NULL AND validated_by_id IS NULL) OR (validated = true AND validated_at IS NOT NULL AND validated_by_id IS NOT NULL AND validation_metadata IS NOT NULL)",
            name="ck_monthly_artifact_validation",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["monthly_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["validated_by_id"], ["admin_users.id"]),
        sa.UniqueConstraint("report_id", "audience", name="uq_monthly_report_artifact_audience"),
    )
    op.create_table(
        "monthly_delivery_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("recipient", sa.String(255)),
        sa.Column("metadata", postgresql.JSONB()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["report_id"], ["monthly_reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["monthly_report_artifacts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["admin_users.id"]),
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_monthly_delivery_event_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'monthly delivery events are append-only'; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_monthly_delivery_events_append_only BEFORE UPDATE OR DELETE ON monthly_delivery_events FOR EACH ROW EXECUTE FUNCTION prevent_monthly_delivery_event_mutation()"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_monthly_delivery_events_append_only ON monthly_delivery_events"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_monthly_delivery_event_mutation()"))
    op.drop_table("monthly_delivery_events")
    op.drop_table("monthly_report_artifacts")
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_monthly_report_version_chain ON monthly_reports")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_monthly_report_version_chain()"))
    for name in (
        "ck_monthly_reports_version_chain",
        "ck_monthly_reports_customer_ready",
        "ck_monthly_reports_counts",
        "ck_monthly_reports_quality",
    ):
        op.drop_constraint(name, "monthly_reports", type_="check")
    for name in ("uq_monthly_reports_supersedes", "uq_monthly_reports_period_version"):
        op.drop_constraint(name, "monthly_reports", type_="unique")
    op.create_unique_constraint(
        "uq_monthly_reports_hospital_period_type",
        "monthly_reports",
        ["hospital_id", "period_year", "period_month", "report_type"],
    )
    for name in ("fk_monthly_reports_supersedes", "fk_monthly_reports_manifest"):
        op.drop_constraint(name, "monthly_reports", type_="foreignkey")
    for name in (
        "delivery_blockers",
        "customer_ready",
        "excluded_count",
        "failed_count",
        "success_count",
        "planned_count",
        "quality",
        "cutoff_at",
        "supersedes_report_id",
        "version",
        "manifest_id",
    ):
        op.drop_column("monthly_reports", name)
    op.drop_table("hospital_service_intervals")
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_monthly_attempt_append_only ON monthly_measurement_attempts"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_monthly_attempt_append_only()"))
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_monthly_cell_transition ON monthly_measurement_cells")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_monthly_cell_transition()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_monthly_manifest_immutable ON monthly_measurement_manifests"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_monthly_manifest_immutability()"))
    op.drop_table("monthly_measurement_attempts")
    op.drop_table("monthly_measurement_cells")
    op.drop_table("monthly_measurement_manifests")
