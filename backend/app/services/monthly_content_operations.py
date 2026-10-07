"""Monthly content operations snapshot for customer reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.models.content import ContentStatus, monthly_quota_for_plan
from app.services.enum_values import enum_value
from app.services.post_publish_review_policy import is_human_post_publish_review_sample

POST_PUBLISH_REVIEW_OVERDUE_AFTER = timedelta(hours=24)
# 사후 검수 표본은 매일 자동 스윕(`workers/post_publish_ai_review`)이 독립 AI 검수로 처리한다.
# 컷오프 시점에 갓 공개된 표본은 아직 차례가 안 온 것이지 문제가 아니라서, 이 유예가 지나도
# 남은 표본(차단 지적으로 사람 확인 대기·검수 불가 지속)만 보고서 경고로 센다.
POST_PUBLISH_REVIEW_WARNING_AFTER = timedelta(days=3)


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
    return enum_value(item.status)


def build_monthly_content_operations_snapshot(
    *,
    plan: object,
    scheduled_items: Sequence[MonthlyContentOperationItem],
    published_items: Sequence[MonthlyContentOperationItem],
    cutoff_at: datetime,
    supplementary_count: int = 0,
    contract_published_count: int | None = None,
    early_publication_count: int = 0,
    late_recovery_count: int = 0,
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

    unresolved_samples = [
        item
        for item in pending_samples
        if item.published_at is not None
        and item.published_at + POST_PUBLISH_REVIEW_WARNING_AFTER <= cutoff_at
    ]

    contracted_published_count = (
        published_count - supplementary_count
        if contract_published_count is None
        else contract_published_count
    )
    shortfall = max((plan_quota or 0) - contracted_published_count, 0) if plan_quota is not None else 0
    # 이 스냅샷은 전달을 막는 blocker를 만들지 않는다(e217e02). 닫힌 달의 운영 결과는
    # 뒤늦게 채워 지울 수 없고, 사후검수는 관찰용 표본일 뿐 두 번째 승인 큐가 아니다
    # (post_publish_review_policy.py). 전부 경고로만 남겨 AE가 원장 앞에서 설명하거나
    # 운영 센터에서 뒤이어 정리하게 한다.
    warnings: list[str] = []
    if plan_quota is None:
        warnings.append("요금제별 약정 콘텐츠 편수를 확인할 수 없습니다.")
    elif shortfall > 0:
        warnings.append(f"약정 콘텐츠 {plan_quota}편 중 {contracted_published_count}편만 발행되었습니다.")
    if unresolved_samples:
        # 사후검수는 발행을 이미 통과한 콘텐츠에 대한 관찰용 표본이지 두 번째 승인
        # 큐가 아니다(post_publish_review_policy.py 참고) — 표본 미완료로 원장 전달을
        # 막지 않는다. 미확인 표본은 병원 목록의 확인 대기 집계(/attention)와 콘텐츠 탭에만
        # 보이고 운영자 큐 행은 아니다. 자동 스윕이 처리할 시간을 준 뒤에도 남은 것만 센다.
        warnings.append(
            f"월간 리포트 필수 사후검수 샘플 {len(unresolved_samples)}건이 아직 완료되지 않았습니다."
        )

    payload = {
        "schema_version": 1,
        "plan_quota": plan_quota,
        "published_count": published_count,
        "contracted_published_count": contracted_published_count,
        "supplementary_count": supplementary_count,
        "early_publication_count": early_publication_count,
        "late_recovery_count": late_recovery_count,
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
        # 콘텐츠 운영 경고(약정 미달, 사후검수 표본 미완료)는 원장 전달을 막지 않으므로
        # 이 목록은 항상 비어 있다. 계약(리포트 payload 스키마)을 유지하기 위해 남긴다.
        "delivery_blockers": [],
        "delivery_warnings": warnings,
        "operator_copy": {
            "label": "콘텐츠 운영 증거",
            "problem": (
                warnings[0]
                if warnings
                else "약정 발행량과 필수 사후검수 샘플이 모두 닫힌 월 기준으로 확인됐습니다."
            ),
            "customer_impact": (
                "약정 미달·사후검수 대기 등 운영 경고가 있어도 리포트는 전달할 수 있으며, 원장님께 숨기지 않고 설명해야 합니다."
                if warnings
                else "운영량과 최소 검수 근거가 맞아 원장 보고 자료로 검토할 수 있습니다."
            ),
            "next_action": (
                "리포트에 표시된 운영 경고를 확인하고, 필요하면 운영 센터에서 사후검수·복구 계획을 이어서 정리해 주세요."
                if warnings
                else "측정 근거와 원장 전달용 PDF를 이어서 확인해 주세요."
            ),
        },
    }
    return MonthlyContentOperationSnapshot(
        payload=payload,
        delivery_blockers=(),
        delivery_warnings=tuple(warnings),
    )
