"""월간 측정 catch-up이 실제로 도는 병원인가 — 스케줄러와 공백 알림이 같은 판정을 쓴다.

두 곳이 따로 판정하면 "자동 재측정이 진행됩니다"라는 약속이 실제 복구 경로와 어긋난다.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.operations import OperationRun


def hospital_requires_monthly_sov_success(db, hospital, period_key: str) -> bool:
    """Return whether this service period must use the frozen monthly measurement path.

    The caller decides readiness from its manifest facts. OperationRun existence only
    identifies an enrolled legacy hospital; its SUCCEEDED state is not a delivery gate.
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
