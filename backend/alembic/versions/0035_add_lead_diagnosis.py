"""리드마그넷 무료 진단 (1단) 스키마

설계 정본: docs/plans/2026-07-29-lead-diagnosis-funnel-design.md

핵심 제약 셋은 **부분 유니크 인덱스**로 건다 (Postgres 전용이라 raw DDL):

1. 전화번호 잠금 — 한 병원 평생 1회. lock_released_at이 채워지면 인덱스에서 빠져
   AE가 잘못 잠긴 건을 풀 수 있다. 이 해제 경로가 없으면 제3자가 먼저 신청해
   원장의 기회를 소진시키는 리드 차단 장치가 된다.
2. 이메일 잠금 — 한 사람 평생 1회. 1·2를 함께 걸어야 우회에 새 번호와 새 메일이
   동시에 필요해진다. 이 인덱스가 제출 버튼 연타 방지도 겸한다.
3. 폴러용 부분 인덱스 — PENDING만 훑는다. DB가 큐이므로(outbox 없음) 이 인덱스가
   드레인 경로의 전부다.

Revision ID: 0035_add_lead_diagnosis
Revises: 0034_add_query_intent
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_add_lead_diagnosis"
down_revision: Union[str, None] = "0034_add_query_intent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb():
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # ── sales_leads 확장 ────────────────────────────────────────────────
    op.add_column("sales_leads", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("sales_leads", sa.Column("contact_name", sa.String(length=100), nullable=True))
    op.add_column("sales_leads", sa.Column("clinic_phone", sa.String(length=40), nullable=True))
    op.add_column("sales_leads", sa.Column("region_keyword", sa.String(length=100), nullable=True))
    op.add_column("sales_leads", sa.Column("core_keywords", _jsonb(), nullable=True))
    op.add_column(
        "sales_leads",
        sa.Column("source", sa.String(length=40), nullable=False, server_default="INQUIRY"),
    )
    # AI 진단 신청은 자유 문의가 아니라 폼이므로 문의 내용이 없을 수 있다.
    # 기존 행은 전부 값이 있으므로 무손실이다.
    op.alter_column("sales_leads", "question", existing_type=sa.Text(), nullable=True)

    # ── lead_diagnoses ─────────────────────────────────────────────────
    op.create_table(
        "lead_diagnoses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applicant_email_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_phone_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_hospital_name", sa.String(length=200), nullable=False),
        sa.Column("subject_region", sa.String(length=100), nullable=False),
        sa.Column("slot_date", sa.Date(), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("queries", _jsonb(), nullable=False),
        sa.Column("requested_models", _jsonb(), nullable=False),
        sa.Column("repeat_count", sa.Integer(), nullable=False),
        sa.Column("execution_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("report_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("lock_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_released_by", sa.String(length=100), nullable=True),
        sa.Column("lock_release_reason", sa.String(length=200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("slot_date", "slot_no", name="uq_lead_diagnoses_slot"),
        # 자리는 1..N. 상한값 자체는 설정으로 바뀌므로 여기서는 하한만 못 박는다.
        sa.CheckConstraint("slot_no >= 1", name="ck_lead_diagnoses_slot_no_positive"),
        # 축 간 진입 조건 (설계 §4-3). "측정 없이 리포트", "리포트 없이 발송"을
        # 스키마 수준에서 불가능하게 만든다.
        sa.CheckConstraint(
            "report_status = 'PENDING' OR execution_status IN ('SUCCEEDED', 'PARTIAL')",
            name="ck_lead_diagnoses_report_requires_execution",
        ),
        sa.CheckConstraint(
            "delivery_status = 'PENDING' OR report_status IN ('READY', 'PURGED')",
            name="ck_lead_diagnoses_delivery_requires_report",
        ),
    )
    op.create_index("ix_lead_diagnoses_lead_id", "lead_diagnoses", ["lead_id"])

    # 잠금 — 해제되지 않은 진단끼리만 유일하다.
    op.execute(
        "CREATE UNIQUE INDEX uq_lead_diagnoses_email_lock "
        "ON lead_diagnoses (applicant_email_hash) WHERE lock_released_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_lead_diagnoses_phone_lock "
        "ON lead_diagnoses (subject_phone_hash) WHERE lock_released_at IS NULL"
    )
    # 폴러 — 오래된 것부터 집는다.
    op.execute(
        "CREATE INDEX ix_lead_diagnoses_drain "
        "ON lead_diagnoses (created_at) WHERE execution_status = 'PENDING'"
    )

    # ── lead_diagnosis_results ─────────────────────────────────────────
    op.create_table(
        "lead_diagnosis_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diagnosis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("query_slot", sa.Integer(), nullable=False),
        sa.Column("repeat_no", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.String(length=500), nullable=False),
        sa.Column("requested_model", sa.String(length=100), nullable=False),
        sa.Column("answer_model", sa.String(length=100), nullable=True),
        sa.Column("is_mentioned", sa.Boolean(), nullable=True),
        sa.Column("mention_verdict", sa.String(length=20), nullable=True),
        sa.Column("measurement_status", sa.String(length=20), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_urls", _jsonb(), nullable=True),
        sa.Column("search_calls", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("answer_source", sa.String(length=10), nullable=False, server_default="LIVE"),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["lead_diagnoses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "diagnosis_id",
            "platform",
            "query_slot",
            "repeat_no",
            "attempt_no",
            name="uq_lead_diagnosis_results_measurement",
        ),
    )
    op.create_index(
        "ix_lead_diagnosis_results_diagnosis_id", "lead_diagnosis_results", ["diagnosis_id"]
    )

    # ── lead_query_answers (질의 단위 공유 캐시) ────────────────────────
    op.create_table(
        "lead_query_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("repeat_no", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.String(length=500), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("requested_model", sa.String(length=100), nullable=False),
        sa.Column("answer_model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("source_urls", _jsonb(), nullable=True),
        sa.Column("search_calls", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("query_hash", "repeat_no", name="uq_lead_query_answers_hash_repeat"),
    )
    op.create_index("ix_lead_query_answers_expires_at", "lead_query_answers", ["expires_at"])

    # ── lead_report_tokens ─────────────────────────────────────────────
    op.create_table(
        "lead_report_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diagnosis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["lead_diagnoses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_lead_report_tokens_token_hash"),
    )
    op.create_index("ix_lead_report_tokens_diagnosis_id", "lead_report_tokens", ["diagnosis_id"])

    # ── lead_report_artifacts ──────────────────────────────────────────
    op.create_table(
        "lead_report_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diagnosis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["lead_diagnoses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("diagnosis_id", "version", name="uq_lead_report_artifacts_version"),
    )

    # ── lead_deliveries ────────────────────────────────────────────────
    op.create_table(
        "lead_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diagnosis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["lead_diagnoses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_lead_deliveries_diagnosis_id", "lead_deliveries", ["diagnosis_id"])
    # 발송 후 커밋이 실패해 SENDING으로 남은 행을 스윕이 찾는 경로.
    op.execute(
        "CREATE INDEX ix_lead_deliveries_stuck "
        "ON lead_deliveries (created_at) WHERE status = 'SENDING'"
    )

    # ── sov_records.answer_model (2단 요구사항, PRD F3-2) ───────────────
    # 컬럼 1개이고 기록 규약이 1단과 같아야 두 경로의 리포트가 어긋나지 않으므로
    # 같은 마이그레이션에 넣는다. MeasurementRun.model_name은 설정값 복사라
    # 별칭 해석 변경을 탐지하지 못한다 — 공급자 응답의 실제 모델을 따로 남긴다.
    op.add_column("sov_records", sa.Column("answer_model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("sov_records", "answer_model")

    op.execute("DROP INDEX IF EXISTS ix_lead_deliveries_stuck")
    op.drop_index("ix_lead_deliveries_diagnosis_id", table_name="lead_deliveries")
    op.drop_table("lead_deliveries")

    op.drop_table("lead_report_artifacts")

    op.drop_index("ix_lead_report_tokens_diagnosis_id", table_name="lead_report_tokens")
    op.drop_table("lead_report_tokens")

    op.drop_index("ix_lead_query_answers_expires_at", table_name="lead_query_answers")
    op.drop_table("lead_query_answers")

    op.drop_index("ix_lead_diagnosis_results_diagnosis_id", table_name="lead_diagnosis_results")
    op.drop_table("lead_diagnosis_results")

    op.execute("DROP INDEX IF EXISTS ix_lead_diagnoses_drain")
    op.execute("DROP INDEX IF EXISTS uq_lead_diagnoses_phone_lock")
    op.execute("DROP INDEX IF EXISTS uq_lead_diagnoses_email_lock")
    op.drop_index("ix_lead_diagnoses_lead_id", table_name="lead_diagnoses")
    op.drop_table("lead_diagnoses")

    # question NOT NULL 복원은 AI 진단 리드(question IS NULL)를 먼저 채워야 가능하다.
    # 되돌릴 때 데이터를 잃지 않도록 placeholder를 넣고 제약을 복원한다.
    op.execute("UPDATE sales_leads SET question = '' WHERE question IS NULL")
    op.alter_column("sales_leads", "question", existing_type=sa.Text(), nullable=False)

    op.drop_column("sales_leads", "source")
    op.drop_column("sales_leads", "core_keywords")
    op.drop_column("sales_leads", "region_keyword")
    op.drop_column("sales_leads", "clinic_phone")
    op.drop_column("sales_leads", "contact_name")
    op.drop_column("sales_leads", "email")
