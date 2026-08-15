"""Monthly content operations snapshot for customer reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.models.content import ContentStatus, monthly_quota_for_plan
from app.services.post_publish_review_policy import is_human_post_publish_review_sample

POST_PUBLISH_REVIEW_OVERDUE_AFTER = timedelta(hours=24)


class MonthlyContentOperationItem(Protocol):
    status: ContentStatus | str
    scheduled_date: Any
    published_at: datetime | None
    post_publish_reviewed_at: datetime | None
    post_publish_reviewed_by: str | None


@dataclass(frozen=True, slots=True)
class MonthlyContentOperationSnapshot:
    payload: dict[str, Any]
    delivery_blockers: tuple[str, ...]
    delivery_warnings: tuple[str, ...]


def _status_value(item: MonthlyContentOperationItem) -> str:
    return getattr(item.status, "value", item.status)


def build_monthly_content_operations_snapshot(
    *,
    plan: object,
    scheduled_items: Sequence[MonthlyContentOperationItem],
    published_items: Sequence[MonthlyContentOperationItem],
    cutoff_at: datetime,
) -> MonthlyContentOperationSnapshot:
    """Summarize closed-month content operations for report delivery control."""
    plan_quota = monthly_quota_for_plan(plan)
    published_count = len(published_items)
    slot_counts = Counter(_status_value(item) for item in scheduled_items)
    actionable_unpublished = [
        item
        for item in scheduled_items
        if _status_value(item) not in {ContentStatus.PUBLISHED.value, ContentStatus.CANCELLED.value}
    ]
    required_samples = [
        item for item in published_items if is_human_post_publish_review_sample(item)  # type: ignore[arg-type]
    ]
    reviewed_samples = [
        item
        for item in required_samples
        if item.post_publish_reviewed_at is not None and item.post_publish_reviewed_at <= cutoff_at
    ]
    pending_samples = [
        item
        for item in required_samples
        if item.post_publish_reviewed_at is None or item.post_publish_reviewed_at > cutoff_at
    ]
    overdue_samples = [
        item
        for item in pending_samples
        if item.published_at is not None
        and item.published_at + POST_PUBLISH_REVIEW_OVERDUE_AFTER <= cutoff_at
    ]

    shortfall = max((plan_quota or 0) - published_count, 0) if plan_quota is not None else 0
    blockers: list[str] = []
    warnings: list[str] = []
    if plan_quota is None:
        warnings.append("요금제별 약정 콘텐츠 편수를 확인할 수 없습니다.")
    elif shortfall > 0:
        warnings.append(f"약정 콘텐츠 {plan_quota}편 중 {published_count}편만 발행되었습니다.")
    if pending_samples:
        blockers.append(
            f"월간 리포트 필수 사후검수 샘플 {len(pending_samples)}건이 아직 완료되지 않았습니다."
        )

    payload = {
        "schema_version": 1,
        "plan_quota": plan_quota,
        "published_count": published_count,
        "shortfall_count": shortfall,
        "scheduled_slot_count": len(scheduled_items),
        "scheduled_slot_state_counts": dict(sorted(slot_counts.items())),
        "unpublished_due_slot_count": len(actionable_unpublished),
        "post_publish_review": {
            "required_sample_count": len(required_samples),
            "reviewed_count": len(reviewed_samples),
            "pending_count": len(pending_samples),
            "overdue_count": len(overdue_samples),
            "cutoff_at": cutoff_at.isoformat(),
        },
        "delivery_blockers": blockers,
        # 이미 닫힌 달의 약정 미달은 뒤늦게 채워서 지울 수 있는 전달 차단 사유가 아니다.
        # 실패한 운영 결과도 숨기지 않고 보고해야 하므로 경고로 보존한다.
        "delivery_warnings": warnings,
        "operator_copy": {
            "label": "콘텐츠 운영 증거",
            "problem": (
                "약정 발행량과 필수 사후검수 샘플이 모두 닫힌 월 기준으로 확인됐습니다."
                if not blockers and not warnings
                else (blockers or warnings)[0]
            ),
            "customer_impact": (
                "운영량과 최소 검수 근거가 맞아 원장 보고 자료로 검토할 수 있습니다."
                if not blockers and not warnings
                else (
                    "필수 사후검수가 끝나지 않아 원장님께 월간 결과를 전달할 수 없습니다."
                    if blockers
                    else "약정 대비 실제 발행 결과를 숨기지 않고 원장님께 설명해야 합니다."
                )
            ),
            "next_action": (
                "측정 근거와 원장 전달용 PDF를 이어서 확인해 주세요."
                if not blockers and not warnings
                else (
                    "운영 센터에서 사후검수 대기 항목을 완료한 뒤 리포트를 다시 만들어 주세요."
                    if blockers
                    else "리포트에 표시된 약정 미달 수를 확인하고 원인과 다음 달 복구 계획을 함께 설명해 주세요."
                )
            ),
        },
    }
    return MonthlyContentOperationSnapshot(
        payload=payload,
        delivery_blockers=tuple(blockers),
        delivery_warnings=tuple(warnings),
    )
