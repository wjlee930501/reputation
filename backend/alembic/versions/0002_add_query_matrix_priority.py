"""add query_matrix priority column

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'query_matrix',
        sa.Column('priority', sa.String(20), nullable=False, server_default='NORMAL'),
    )


def downgrade() -> None:
    op.drop_column('query_matrix', 'priority')
