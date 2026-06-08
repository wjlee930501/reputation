"""make admin_audit_logs append-only (tamper-evident)

DATA-1/AUTH-7: the application role can currently UPDATE/DELETE audit rows, so the
trail is not tamper-evident. Install a trigger that blocks all DELETEs and all
content-mutating UPDATEs, while still permitting the FK ``ON DELETE SET NULL``
cascade (hospital_id -> NULL, everything else unchanged) so hospital deletion keeps
working. TRUNCATE is blocked at the statement level.

Note: the DB role is currently a superuser; a superuser can still bypass triggers by
setting session_replication_role. Reducing that grant is a Cloud SQL ops follow-up.

Revision ID: 0024_audit_log_append_only
Revises: 0023_harden_hospital_json_and_fk_indexes
Create Date: 2026-06-08
"""
from alembic import op

revision = "0024_audit_log_append_only"
down_revision = "0023_harden_hospital_json_and_fk_indexes"
branch_labels = None
depends_on = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION reject_audit_log_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'admin_audit_logs is append-only (tamper-evident audit trail)';
    END IF;
    -- UPDATE: allow ONLY the FK ON DELETE SET NULL cascade (hospital_id -> NULL).
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.actor IS DISTINCT FROM OLD.actor
        OR NEW.action IS DISTINCT FROM OLD.action
        OR NEW.target_type IS DISTINCT FROM OLD.target_type
        OR NEW.target_id IS DISTINCT FROM OLD.target_id
        OR NEW.detail::text IS DISTINCT FROM OLD.detail::text
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.hospital_id IS NOT NULL THEN
        RAISE EXCEPTION 'admin_audit_logs is append-only (tamper-evident audit trail)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute(
        "CREATE TRIGGER admin_audit_logs_block_mutation "
        "BEFORE UPDATE OR DELETE ON admin_audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_log_mutation()"
    )
    op.execute(
        "CREATE TRIGGER admin_audit_logs_block_truncate "
        "BEFORE TRUNCATE ON admin_audit_logs "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_log_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS admin_audit_logs_block_truncate ON admin_audit_logs")
    op.execute("DROP TRIGGER IF EXISTS admin_audit_logs_block_mutation ON admin_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_log_mutation()")
