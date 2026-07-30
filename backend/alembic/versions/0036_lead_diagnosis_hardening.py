"""리드 진단 스키마 보강 — 적대적 검토(2026-07-30) 반영

세 가지를 고친다.

**① 종결 상태가 CHECK에 걸려 파기 배치 전체를 롤백시킨다 (BLOCKER).**
`ck_lead_diagnoses_report_requires_execution`은 report_status가 PENDING이 아니면
execution이 SUCCEEDED|PARTIAL이어야 한다고 요구했다. 그런데 두 상태가 그 조건을
만족하지 않는데도 정상적으로 도달한다:

- `PURGED` — 파기는 execution이 FAILED인 진단도 종결시킨다. CHECK 위반 → 그날 만료된
  **모든** 리드의 파기가 함께 롤백되고, 같은 독성 행이 매일 다시 선택되어 영구 반복된다.
- `BLOCKED` — 설계상 `execution_status=FAILED`일 때 리포트를 BLOCKED로 표시하고
  AE에게 넘긴다. 즉 이 전이는 **처음부터 프로덕션에서 크래시**했을 것이다.

제약이 실제로 지켜야 하는 것은 하나다: **리포트를 만들거나 만든 상태(BUILDING·READY)는
쓸 만한 측정이 있어야 한다.** 나머지(PENDING·BLOCKED·PURGED)는 execution과 무관하다.

**② delivery 행이 진단당 여러 개 만들어질 수 있다 (HIGH).**
폴러가 겹치거나 Celery가 중복 발행하면 두 세션이 모두 "delivery 없음"을 읽고
각자 다른 UUID로 행을 만든다. Idempotency-Key가 행 id이므로 **서로 다른 키**가 되어
Resend가 막지 못하고 메일이 두 통 나간다. UNIQUE로 행을 하나로 못 박는다.

**③ 상태 컬럼에 허용값 CHECK가 없다 (MEDIUM).**
오타 상태(`RUNING`)가 들어가면 모든 폴러가 알려진 문자열만 조회하므로 그 행은
영원히 회수되지 않는다. 축 간 제약은 통과하므로 아무도 모른다.

Revision ID: 0036_lead_diagnosis_hardening
Revises: 0035_add_lead_diagnosis
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0036_lead_diagnosis_hardening"
down_revision: Union[str, None] = "0035_add_lead_diagnosis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EXECUTION = ("PENDING", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED")
_REPORT = ("PENDING", "BUILDING", "READY", "BLOCKED", "PURGED")
_DELIVERY = ("PENDING", "SENDING", "SENT", "FAILED")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # ① PURGED를 종결 상태로 허용한다.
    op.drop_constraint(
        "ck_lead_diagnoses_report_requires_execution", "lead_diagnoses", type_="check"
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_report_requires_execution",
        "lead_diagnoses",
        # 리포트를 만드는 중이거나 만든 상태만 쓸 만한 측정을 요구한다.
        "report_status NOT IN ('BUILDING', 'READY') "
        "OR execution_status IN ('SUCCEEDED', 'PARTIAL')",
    )

    # ② 진단당 이벤트·채널별 delivery 행은 하나뿐이다.
    #    기존 중복이 있으면 가장 오래된 것만 남긴다(운영 전이라 사실상 0건이지만,
    #    마이그레이션이 데이터 때문에 실패하지 않게 한다).
    op.execute(
        """
        DELETE FROM lead_deliveries a
         USING lead_deliveries b
         WHERE a.diagnosis_id IS NOT NULL
           AND a.diagnosis_id = b.diagnosis_id
           AND a.event = b.event
           AND a.channel = b.channel
           AND (a.created_at, a.id) > (b.created_at, b.id)
        """
    )
    op.create_unique_constraint(
        "uq_lead_deliveries_diagnosis_event_channel",
        "lead_deliveries",
        ["diagnosis_id", "event", "channel"],
    )

    # ③ 허용값 CHECK.
    op.create_check_constraint(
        "ck_lead_diagnoses_execution_status",
        "lead_diagnoses",
        _in_list("execution_status", _EXECUTION),
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_report_status", "lead_diagnoses", _in_list("report_status", _REPORT)
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_delivery_status",
        "lead_diagnoses",
        _in_list("delivery_status", _DELIVERY),
    )
    op.create_check_constraint(
        "ck_lead_deliveries_status", "lead_deliveries", _in_list("status", _DELIVERY)
    )
    op.create_check_constraint(
        "ck_lead_diagnosis_results_measurement_status",
        "lead_diagnosis_results",
        "measurement_status IN ('SUCCESS', 'FAILED')",
    )
    op.create_check_constraint(
        "ck_lead_diagnosis_results_answer_source",
        "lead_diagnosis_results",
        "answer_source IN ('LIVE', 'CACHED')",
    )

    # ④ 자리 배정을 원자적으로 하기 위한 날짜별 카운터.
    #    COUNT를 읽고 +1 하는 방식은 동시 접수 20건이 전부 같은 값을 읽어, 한 건만
    #    성공하고 나머지는 재시도 3회 후 503이 된다 — 자리가 17개 남았는데도.
    op.create_table(
        "lead_diagnosis_slot_days",
        sa.Column("slot_date", sa.Date(), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("used >= 0", name="ck_lead_diagnosis_slot_days_used_non_negative"),
    )
    # 기존 행이 있으면 카운터를 맞춘다.
    op.execute(
        """
        INSERT INTO lead_diagnosis_slot_days (slot_date, used)
        SELECT slot_date, COUNT(*) FROM lead_diagnoses GROUP BY slot_date
        ON CONFLICT (slot_date) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("lead_diagnosis_slot_days")

    for name, table in (
        ("ck_lead_diagnosis_results_answer_source", "lead_diagnosis_results"),
        ("ck_lead_diagnosis_results_measurement_status", "lead_diagnosis_results"),
        ("ck_lead_deliveries_status", "lead_deliveries"),
        ("ck_lead_diagnoses_delivery_status", "lead_diagnoses"),
        ("ck_lead_diagnoses_report_status", "lead_diagnoses"),
        ("ck_lead_diagnoses_execution_status", "lead_diagnoses"),
    ):
        op.drop_constraint(name, table, type_="check")

    op.drop_constraint(
        "uq_lead_deliveries_diagnosis_event_channel", "lead_deliveries", type_="unique"
    )

    op.drop_constraint(
        "ck_lead_diagnoses_report_requires_execution", "lead_diagnoses", type_="check"
    )
    op.create_check_constraint(
        "ck_lead_diagnoses_report_requires_execution",
        "lead_diagnoses",
        "report_status = 'PENDING' OR execution_status IN ('SUCCEEDED', 'PARTIAL')",
    )
