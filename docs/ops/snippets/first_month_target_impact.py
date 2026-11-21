"""#377(첫 달 발행 편수) 배포가 운영 약속 편수·슬롯에 주는 영향 — 읽기 전용.

migrate Job에서 base64로 감싸 실행한다(docs/ops/2026-11-22-bundle-b-admin-checks.md). DB 수준에서 쓰기를 막고
(`SET TRANSACTION READ ONLY`) 끝나면 롤백한다. 결과는 JSON 한 줄이다.

- operator_markers: 마케터가 정한 첫 달 편수 표지(`operator_month_target`)가 남은 일정 저장 감사행 수.
  배포 직후에는 0이어야 한다 — 0이면 #377의 판정은 모든 병원·달에서 옛 판정과 같다.
- months: 일정이 있는 병원마다 이번 달·다음 달의 약속 편수(새 판정 new / 옛 판정 legacy)와 살아 있는
  슬롯 수(live). 당월 조정(21:30)·월말 배치는 슬롯을 **늘리기만** 하고 줄이지 않는다 — new > live면 그 차이만큼
  채우는데, 이것은 배포 전에도 같은 동작이다(legacy와 같으면 배포 때문이 아니다).
- changed: new != legacy인 병원·달. 배포 직후에는 비어 있어야 한다.
"""

import json

import arrow
from sqlalchemy import text

from app.core.database import SyncSessionLocal
from app.models.content import monthly_quota_for_plan
from app.services import content_month_target as cmt
from app.services.schedule_reconciliation import effective_schedules_query


def legacy_target(db, hospital_id, month, plan):
    """#377 이전 판정 — 마케터 표지를 모르고, 계약 편수 표지는 하나라도 있으면 그 달을 계약 편수로 본다."""
    quota = monthly_quota_for_plan(plan)
    if quota is None:
        return None
    records = db.execute(cmt.schedule_change_records_stmt(hospital_id)).all()
    allocated = db.scalar(cmt.month_allocation_count_stmt(hospital_id, month)) or 0
    if cmt.month_is_voided(records, month):
        return min(quota, allocated)
    key = month.floor("month").format("YYYY-MM")
    if any(isinstance(d, dict) and d.get(cmt.CONTRACT_QUOTA_MONTH_KEY) == key for _c, d in records):
        return quota
    recorded = cmt.recorded_first_month_target(
        db.scalar(cmt.initial_schedule_record_stmt(hospital_id)), month
    )
    if recorded is None or recorded >= quota:
        return quota
    return min(quota, max(recorded, allocated))


with SyncSessionLocal() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    markers = db.scalar(
        text(
            "SELECT count(*) FROM admin_audit_logs "
            "WHERE action = 'set_schedule' AND detail ? 'operator_month_target'"
        )
    )
    now = arrow.now("Asia/Seoul")
    months = []
    for month in (now.floor("month"), now.shift(months=1).floor("month")):
        for schedule in db.execute(effective_schedules_query(month.ceil("month").date())).scalars():
            hid = schedule.hospital_id
            new = cmt.month_content_target_sync(db, hid, month, schedule.plan)
            old = legacy_target(db, hid, month, schedule.plan)
            live = db.scalar(cmt.month_allocation_count_stmt(hid, month)) or 0
            months.append(
                {
                    "hospital_id": str(hid),
                    "month": month.format("YYYY-MM"),
                    "plan": str(schedule.plan),
                    "new": new,
                    "legacy": old,
                    "live": live,
                }
            )
    db.rollback()

changed = [row for row in months if row["new"] != row["legacy"]]
print(
    json.dumps(
        {
            "query": "first_month_target_impact",
            "operator_markers": markers,
            "changed": changed,
            "months": months,
        },
        ensure_ascii=False,
    )
)
