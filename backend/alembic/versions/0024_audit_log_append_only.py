"""make admin_audit_logs append-only (tamper-evident)

DATA-1/AUTH-7: the application role can currently UPDATE/DELETE audit rows, so the
trail is not tamper-evident. Make the table fully immutable:

  1. Drop the hospital_id FK (was ON DELETE SET NULL). Without it, deleting a
     hospital no longer mutates audit rows — they keep the original hospital_id as
     a historical record. This removes the ONLY legitimate reason to allow an
     UPDATE, so the trigger can block ALL mutations with no exception (an earlier
     hospital_id->NULL carve-out would have let a caller orphan/hide rows).
  2. Trigger blocks every UPDATE/DELETE (row-level) and TRUNCATE (statement-level).

Note: the DB role is currently a superuser; a superuser can still bypass triggers
via session_replication_role. Reducing that grant is a Cloud SQL ops follow-up.

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
    RAISE EXCEPTION 'admin_audit_logs is append-only (tamper-evident audit trail)';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE admin_audit_logs DROP CONSTRAINT IF EXISTS admin_audit_logs_hospital_id_fkey")
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
    op.execute(
        "ALTER TABLE admin_audit_logs ADD CONSTRAINT admin_audit_logs_hospital_id_fkey "
        "FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL"
    )
