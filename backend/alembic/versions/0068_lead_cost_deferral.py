"""Add durable automatic cost deferral for free lead diagnosis.

Revision ID: 0068_lead_cost_deferral
Revises: 0067_measurement_slots
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068_lead_cost_deferral"
down_revision: str | None = "0067_measurement_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE lead_diagnoses "
            "ADD COLUMN IF NOT EXISTS cost_deferred_until TIMESTAMP WITH TIME ZONE NULL"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE lead_diagnoses "
            "ADD COLUMN IF NOT EXISTS cost_defer_reason VARCHAR(100) NULL"
        )
    )
    # Only the exact legacy pre-provider cost-block signature is safe to re-arm. Other FAILED
    # rows may reflect bad policy, exhausted provider retries, or invalid measurements and must
    # not be guessed into a retry. The old path consumed one claim despite making zero calls.
    op.execute(
        sa.text(
            """
            UPDATE lead_diagnoses
               SET execution_status = 'PENDING',
                   execution_attempts = GREATEST(execution_attempts - 1, 0),
                   running_since = NULL,
                   finished_at = NULL,
                   cost_deferred_until = NOW(),
                   cost_defer_reason = 'legacy_cost_guard',
                   error = '비용 안전장치로 자동 재개 대기 중: 레거시 비용 차단'
             WHERE execution_status = 'FAILED'
               AND report_status = 'PENDING'
               AND delivery_status = 'PENDING'
               AND error LIKE '호출 예산 초과로 측정을 중단했습니다:%'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("lead_diagnoses", "cost_defer_reason")
    op.drop_column("lead_diagnoses", "cost_deferred_until")
