"""add FAQ split fields (faq_question, faq_answer_summary)

Revision ID: 0017_add_faq_split_fields
Revises: 0016_add_content_references_and_body_updated_at
Create Date: 2026-05-08

AEO 신호 보강:
- FAQ 콘텐츠를 Google FAQPage rich result 가이드라인에 정합되게 매핑하기 위해
  Question(짧은 질문)과 Answer(짧은 답변)를 본문(body)과 분리해 저장한다.
- 기존 매핑(title=Question, body 전체=Answer)은 Answer가 길어 rich result
  무효 처리 위험. 분리 후 faq_answer_summary를 Answer로 사용.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_add_faq_split_fields"
down_revision = "0016_add_content_references_and_body_updated_at"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("content_items"):
        return

    if not _has_column("content_items", "faq_question"):
        op.add_column(
            "content_items",
            sa.Column("faq_question", sa.String(length=300), nullable=True),
        )
    if not _has_column("content_items", "faq_answer_summary"):
        op.add_column(
            "content_items",
            sa.Column("faq_answer_summary", sa.String(length=600), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("content_items"):
        return
    for column in ("faq_answer_summary", "faq_question"):
        if _has_column("content_items", column):
            op.drop_column("content_items", column)
