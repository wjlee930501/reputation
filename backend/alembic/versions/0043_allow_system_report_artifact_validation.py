"""Allow system-owned validation of monthly report artifacts.

Revision ID: 0043_allow_system_report_artifact_validation
Revises: 0042_add_operations_control_plane
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043_allow_system_report_artifact_validation"
down_revision: str | None = "0042_add_operations_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_monthly_artifact_validation",
        "monthly_report_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_monthly_artifact_validation",
        "monthly_report_artifacts",
        sa.text(
            "(validated = false AND validated_at IS NULL AND validated_by_id IS NULL) OR "
            "(validated = true AND validated_at IS NOT NULL AND validation_metadata IS NOT NULL "
            "AND (validated_by_id IS NOT NULL OR "
            "validation_metadata->>'validation_source' = 'SYSTEM'))"
        ),
    )


def downgrade() -> None:
    system_validation_count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM monthly_report_artifacts "
            "WHERE validated = true AND validated_by_id IS NULL "
            "AND validation_metadata->>'validation_source' = 'SYSTEM'"
        )
    )
    if system_validation_count:
        raise RuntimeError(
            "시스템이 검증한 원장 전달용 PDF가 있어 이 버전으로 되돌릴 수 없습니다. "
            "데이터를 임의로 바꾸지 말고 현재 버전으로 다시 올린 뒤 개발팀에 문의해 주세요."
        )
    op.drop_constraint(
        "ck_monthly_artifact_validation",
        "monthly_report_artifacts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_monthly_artifact_validation",
        "monthly_report_artifacts",
        sa.text(
            "(validated = false AND validated_at IS NULL AND validated_by_id IS NULL) OR "
            "(validated = true AND validated_at IS NOT NULL AND validated_by_id IS NOT NULL "
            "AND validation_metadata IS NOT NULL)"
        ),
    )
