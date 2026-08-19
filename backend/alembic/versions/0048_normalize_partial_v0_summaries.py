"""Normalize operator guidance for successful partial V0 runs.

Revision ID: 0048_normalize_partial_v0_summaries
Revises: 0047_add_incident_episode_seq

Earlier provider classification treated some partially successful V0 runs as
fully blocked quota failures.  The measurement records themselves are correct;
this migration only repairs the operator-facing summary for runs that already
contain usable results.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0048_normalize_partial_v0_summaries"
down_revision: str | None = "0047_add_incident_episode_seq"
branch_labels: str | None = None
depends_on: str | None = None


PARTIAL_SUCCESS_CODE = "V0_PARTIAL_PROVIDER_DEGRADED"
PARTIAL_SUCCESS_MESSAGE = "일부 AI 측정 호출은 실패했지만 성공 데이터로 초기 진단을 완료했습니다."
PARTIAL_SUCCESS_NEXT_ACTION = (
    "초기 진단을 다시 실행하지 마세요. 운영센터에서 실패한 플랫폼의 상태만 확인하고 "
    "다음 정기 측정에서 회복 여부를 점검하세요."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE measurement_runs
               SET error_summary = COALESCE(error_summary, '{}'::jsonb)
                   || jsonb_build_object(
                       'safe_error_code', :safe_error_code,
                       'safe_error_message', :safe_error_message,
                       'next_action', :next_action
                   )
             WHERE status = 'PARTIAL'
               AND success_count > 0
               AND error_summary->>'safe_error_code' IN (
                   'V0_PROVIDER_QUOTA_EXHAUSTED',
                   'V0_PROVIDER_RATE_LIMITED',
                   'V0_PROVIDER_UNAVAILABLE',
                   'V0_PROVIDER_AUTH_OR_MODEL',
                   'V0_JUDGE_FAILED'
               )
            """
        ).bindparams(
            safe_error_code=PARTIAL_SUCCESS_CODE,
            safe_error_message=PARTIAL_SUCCESS_MESSAGE,
            next_action=PARTIAL_SUCCESS_NEXT_ACTION,
        )
    )


def downgrade() -> None:
    # The previous provider classification cannot be reconstructed reliably.
    # Keep the factually correct operator summary when rolling back application code.
    pass
