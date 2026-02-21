"""initial

Revision ID: 0001
Revises:
Create Date: 2026-02-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # hospitals
    op.create_table(
        'hospitals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(200), unique=True, nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'ONBOARDING', 'ANALYZING', 'BUILDING', 'PENDING_DOMAIN', 'ACTIVE', 'PAUSED',
                name='hospitalstatus',
            ),
            nullable=False,
            server_default='ONBOARDING',
        ),
        sa.Column(
            'plan',
            sa.Enum('PLAN_16', 'PLAN_12', 'PLAN_8', name='plan'),
            nullable=True,
        ),
        # 연락처
        sa.Column('address', sa.String(500), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('business_hours', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        # URL 자산
        sa.Column('website_url', sa.String(500), nullable=True),
        sa.Column('blog_url', sa.String(500), nullable=True),
        sa.Column('kakao_channel_url', sa.String(500), nullable=True),
        sa.Column('aeo_domain', sa.String(200), nullable=True),
        sa.Column('aeo_site_path', sa.String(500), nullable=True),
        # 타겟
        sa.Column('region', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('specialties', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('keywords', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('competitors', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        # 원장
        sa.Column('director_name', sa.String(100), nullable=True),
        sa.Column('director_career', sa.Text(), nullable=True),
        sa.Column('director_philosophy', sa.Text(), nullable=True),
        sa.Column('director_photo_url', sa.String(500), nullable=True),
        # 진료 항목
        sa.Column('treatments', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        # 상태 플래그
        sa.Column('profile_complete', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('v0_report_done', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('site_built', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('site_live', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('schedule_set', sa.Boolean(), nullable=False, server_default=sa.false()),
        # 타임스탬프
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # content_schedules
    op.create_table(
        'content_schedules',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hospitals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan', sa.String(20), nullable=False),
        sa.Column('publish_days', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('active_from', sa.Date(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # content_items
    op.create_table(
        'content_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hospitals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('schedule_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('content_schedules.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'content_type',
            sa.Enum('FAQ', 'DISEASE', 'TREATMENT', 'COLUMN', 'HEALTH', 'LOCAL', 'NOTICE', name='contenttype'),
            nullable=False,
        ),
        sa.Column('sequence_no', sa.Integer(), nullable=False),
        sa.Column('total_count', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(300), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('meta_description', sa.String(300), nullable=True),
        sa.Column('image_url', sa.String(500), nullable=True),
        sa.Column('image_prompt', sa.Text(), nullable=True),
        sa.Column('scheduled_date', sa.Date(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('DRAFT', 'READY', 'PUBLISHED', 'REJECTED', name='contentstatus'),
            nullable=False,
            server_default='DRAFT',
        ),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # query_matrix
    op.create_table(
        'query_matrix',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hospitals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('query_text', sa.String(500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # sov_records
    op.create_table(
        'sov_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hospitals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('query_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('query_matrix.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ai_platform', sa.String(50), nullable=False),
        sa.Column('measured_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('is_mentioned', sa.Boolean(), nullable=False),
        sa.Column('mention_rank', sa.Integer(), nullable=True),
        sa.Column('mention_sentiment', sa.String(20), nullable=True),
        sa.Column('mention_context', sa.Text(), nullable=True),
        sa.Column('raw_response', sa.Text(), nullable=False),
    )

    # monthly_reports
    op.create_table(
        'monthly_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('hospital_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('hospitals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('period_year', sa.Integer(), nullable=False),
        sa.Column('period_month', sa.Integer(), nullable=False),
        sa.Column('report_type', sa.String(20), nullable=False, server_default='MONTHLY'),
        sa.Column('pdf_path', sa.String(500), nullable=True),
        sa.Column('sov_summary', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('content_summary', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    )

    # 성능 인덱스
    op.create_index('ix_content_items_hospital_date', 'content_items', ['hospital_id', 'scheduled_date'])
    op.create_index('ix_content_items_status', 'content_items', ['status'])
    op.create_index('ix_sov_records_hospital_id', 'sov_records', ['hospital_id'])
    op.create_index('ix_query_matrix_hospital_id', 'query_matrix', ['hospital_id'])
    op.create_index('ix_monthly_reports_hospital_period', 'monthly_reports', ['hospital_id', 'period_year', 'period_month'])


def downgrade() -> None:
    op.drop_index('ix_monthly_reports_hospital_period', table_name='monthly_reports')
    op.drop_index('ix_query_matrix_hospital_id', table_name='query_matrix')
    op.drop_index('ix_sov_records_hospital_id', table_name='sov_records')
    op.drop_index('ix_content_items_status', table_name='content_items')
    op.drop_index('ix_content_items_hospital_date', table_name='content_items')

    op.drop_table('monthly_reports')
    op.drop_table('sov_records')
    op.drop_table('query_matrix')
    op.drop_table('content_items')
    op.drop_table('content_schedules')
    op.drop_table('hospitals')

    op.execute('DROP TYPE IF EXISTS contentstatus')
    op.execute('DROP TYPE IF EXISTS contenttype')
    op.execute('DROP TYPE IF EXISTS hospitalstatus')
    op.execute('DROP TYPE IF EXISTS plan')
