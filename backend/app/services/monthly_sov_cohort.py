"""월간 측정 catch-up이 실제로 도는 병원인가 — 스케줄러와 공백 알림이 같은 판정을 쓴다.

두 곳이 따로 판정하면 "자동 재측정이 진행됩니다"라는 약속이 실제 복구 경로와 어긋난다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.operations import OperationRun


def hospital_requires_monthly_sov_success(db, hospital, period_key: str) -> bool:
    """월간 측정 전환 경로 병원은 해당 월 측정 SUCCEEDED 없이 리포트를 만들지 않는다.

    전환 윈도우(8/24–31)에 묶지 않는다. 9/1 직전 달 마감에서도 실패한 전환 병원은
    빈 월간 리포트를 만들지 않고, 월간 측정을 쓰지 않는 병원은 기존 마감 경로를 탄다.
    """
    if bool(getattr(hospital, "monthly_sov_cohort", False)):
        return True
    hospital_id = getattr(hospital, "id", None)
    if hospital_id is None:
        return False
    run_id = db.execute(
        select(OperationRun.id).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.idempotency_key == f"monthly-sov:{hospital_id}:{period_key}",
        )
    ).scalar_one_or_none()
    return run_id is not None
