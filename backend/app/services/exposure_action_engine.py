"""Deterministic exposure gap/action engine.

This module uses only local patient-question and AI mention measurement state. It never calls
external AI, Slack, or network APIs.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sov import AIQueryTarget, ExposureAction, ExposureGap, SovRecord

ACTIVE_TARGET_STATUSES = {"ACTIVE"}
ACTIVE_GAP_STATUSES = {"OPEN", "WATCHING"}
ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED"}

MENTION_RATE_THRESHOLD = 50.0

PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
ACTION_TYPE_RANK = {"MEASUREMENT": 0, "CONTENT": 1, "SOURCE": 2, "WEBBLOG_IA": 3}


@dataclass(frozen=True)
class ExposureRecommendation:
    hospital_id: uuid.UUID
    query_target_id: uuid.UUID
    gap_type: str
    severity: str
    evidence: dict[str, Any]
    action_type: str
    title: str
    description: str
    owner: str
    due_month: str
    target_priority: str
    target_name: str


async def ensure_hospital_exposure_actions(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    today: date | None = None,
    max_create: int = 12,
) -> None:
    """Create missing open gaps/actions for current deterministic recommendations."""
    targets = await _load_targets(db, hospital_id)
    if not targets:
        return

    records = await _load_recent_sov_records(db, hospital_id)
    recommendations = build_exposure_recommendations(targets, records, today=today)
    if not recommendations:
        return

    changed = False
    for recommendation in recommendations[:max_create]:
        gap = await _find_active_gap(db, recommendation)
        if gap is None:
            gap = ExposureGap(
                hospital_id=recommendation.hospital_id,
                query_target_id=recommendation.query_target_id,
                gap_type=recommendation.gap_type,
                severity=recommendation.severity,
                evidence=recommendation.evidence,
                status="OPEN",
            )
            db.add(gap)
            await db.flush()
            changed = True

        action = await _find_active_action(db, recommendation, gap.id)
        if action is None:
            db.add(
                ExposureAction(
                    hospital_id=recommendation.hospital_id,
                    query_target_id=recommendation.query_target_id,
                    gap_id=gap.id,
                    action_type=recommendation.action_type,
                    title=recommendation.title,
                    description=recommendation.description,
                    owner=recommendation.owner,
                    due_month=recommendation.due_month,
                    status="OPEN",
                )
            )
            changed = True

    if changed:
        await db.commit()


async def list_top_exposure_actions(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    limit: int = 3,
) -> list[ExposureAction]:
    safe_limit = max(1, min(limit, 20))
    result = await db.execute(
        select(ExposureAction)
        .options(
            selectinload(ExposureAction.query_target),
            selectinload(ExposureAction.gap),
            selectinload(ExposureAction.linked_content),
        )
        .where(
            ExposureAction.hospital_id == hospital_id,
            ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
        )
    )
    actions = result.scalars().all()
    return sorted(actions, key=_action_sort_key)[:safe_limit]


def build_exposure_recommendations(
    targets: Sequence[Any],
    records: Sequence[Any],
    *,
    today: date | None = None,
) -> list[ExposureRecommendation]:
    diagnosis_date = today or date.today()
    active_targets = [
        target
        for target in targets
        if str(getattr(target, "status", "ACTIVE")).upper() in ACTIVE_TARGET_STATUSES
    ]
    records_by_target = _group_records_by_target(active_targets, records)

    recommendations: list[ExposureRecommendation] = []
    for target in sorted(active_targets, key=_target_sort_key):
        target_records = records_by_target.get(str(target.id), [])
        recommendations.extend(_diagnose_target(target, target_records, diagnosis_date))

    return sorted(recommendations, key=_recommendation_sort_key)


async def _load_targets(db: AsyncSession, hospital_id: uuid.UUID) -> list[AIQueryTarget]:
    result = await db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status.in_(ACTIVE_TARGET_STATUSES),
        )
    )
    return list(result.scalars().all())


async def _load_recent_sov_records(db: AsyncSession, hospital_id: uuid.UUID) -> list[SovRecord]:
    result = await db.execute(
        select(SovRecord)
        .where(SovRecord.hospital_id == hospital_id)
        .order_by(SovRecord.measured_at.desc())
        .limit(1000)
    )
    return list(result.scalars().all())


async def _find_active_gap(
    db: AsyncSession,
    recommendation: ExposureRecommendation,
) -> ExposureGap | None:
    result = await db.execute(
        select(ExposureGap).where(
            ExposureGap.hospital_id == recommendation.hospital_id,
            ExposureGap.query_target_id == recommendation.query_target_id,
            ExposureGap.gap_type == recommendation.gap_type,
            ExposureGap.status.in_(ACTIVE_GAP_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def _find_active_action(
    db: AsyncSession,
    recommendation: ExposureRecommendation,
    gap_id: uuid.UUID,
) -> ExposureAction | None:
    result = await db.execute(
        select(ExposureAction).where(
            ExposureAction.hospital_id == recommendation.hospital_id,
            ExposureAction.query_target_id == recommendation.query_target_id,
            ExposureAction.gap_id == gap_id,
            ExposureAction.action_type == recommendation.action_type,
            ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
        )
    )
    return result.scalar_one_or_none()


def _diagnose_target(
    target: Any,
    records: Sequence[Any],
    diagnosis_date: date,
) -> list[ExposureRecommendation]:
    successful_records = [record for record in records if _is_successful_measurement(record)]
    failed_count = len(records) - len(successful_records)
    due_month = getattr(target, "target_month", None) or diagnosis_date.strftime("%Y-%m")
    base_evidence = _base_evidence(target, records, successful_records, failed_count)

    if not successful_records:
        return [
            _recommendation(
                target,
                "NO_SUCCESSFUL_MEASUREMENT",
                _severity_for_target(target, severe_for_high=True),
                {
                    **base_evidence,
                    "rule": "no_successful_measurements",
                },
                "MEASUREMENT",
                "baseline AI 노출 측정 실행",
                (
                    "성공한 ChatGPT/Gemini 측정값이 없어 "
                    "노출 상태를 판단할 수 없습니다. "
                    "활성 질의 변형을 확인한 뒤 baseline 측정을 실행하세요."
                ),
                due_month,
            )
        ]

    mention_count = sum(
        1 for record in successful_records if bool(getattr(record, "is_mentioned", False))
    )
    mention_rate = round(mention_count / len(successful_records) * 100, 1)
    competitor_count = _competitor_mention_count(successful_records)
    source_missing_count = sum(1 for record in successful_records if not _has_source_urls(record))

    evidence = {
        **base_evidence,
        "mention_count": mention_count,
        "mention_rate": mention_rate,
        "competitor_mention_count": competitor_count,
        "source_missing_count": source_missing_count,
    }

    recommendations: list[ExposureRecommendation] = []
    if mention_count == 0:
        recommendations.append(
            _recommendation(
                target,
                "MISSING_MENTION",
                _severity_for_target(target, severe_for_high=True),
                {
                    **evidence,
                    "rule": "zero_hospital_mentions",
                },
                "CONTENT",
                "타깃 질의와 연결된 근거 콘텐츠 보강",
                (
                    f"최근 성공 측정 {len(successful_records)}건에서 "
                    "병원 언급이 없습니다. 타깃 질의 의도와 맞는 "
                    "FAQ/질환/치료 콘텐츠 가이드를 우선 보강하세요."
                ),
                due_month,
            )
        )
    elif mention_rate < MENTION_RATE_THRESHOLD:
        recommendations.append(
            _recommendation(
                target,
                "LOW_MENTION_SHARE",
                _severity_for_target(target, severe_for_high=False),
                {
                    **evidence,
                    "rule": "mention_rate_below_threshold",
                    "threshold": MENTION_RATE_THRESHOLD,
                },
                "CONTENT",
                "낮은 AI 언급률 개선 콘텐츠 보강",
                (
                    f"현재 언급률이 {mention_rate:.1f}%로 "
                    f"기준 {MENTION_RATE_THRESHOLD:.0f}%보다 낮습니다. "
                    "환자 선택 기준을 반영한 콘텐츠와 "
                    "내부 링크를 보강하세요."
                ),
                due_month,
            )
        )

    if competitor_count > 0 and competitor_count >= max(mention_count, 1):
        recommendations.append(
            _recommendation(
                target,
                "COMPETITOR_VISIBILITY",
                _severity_for_target(target, severe_for_high=True),
                {
                    **evidence,
                    "rule": "competitor_mentions_match_or_exceed_hospital_mentions",
                },
                "WEBBLOG_IA",
                "경쟁 병원 대비 웹블로그 정보 구조 보강",
                (
                    "경쟁 병원 언급이 병원 언급과 같거나 더 많습니다. "
                    "타깃 질의에서 선택 기준, 지역성, 진료 근거가 "
                    "드러나도록 웹블로그 IA를 조정하세요."
                ),
                due_month,
            )
        )

    if source_missing_count > 0 and source_missing_count / len(successful_records) >= 0.5:
        recommendations.append(
            _recommendation(
                target,
                "SOURCE_SIGNAL_GAP",
                "MEDIUM",
                {
                    **evidence,
                    "rule": "source_urls_missing_for_majority_of_successful_measurements",
                },
                "SOURCE",
                "AI가 인용할 공식 출처 신호 보강",
                (
                    "성공한 측정의 과반에서 AI가 참고할 URL 근거가 비어 있습니다. "
                    "기존 홈페이지, Google Business Profile, 공개 콘텐츠의 "
                    "병원명/진료/지역 신호를 정리하세요."
                ),
                due_month,
            )
        )

    return recommendations


def _recommendation(
    target: Any,
    gap_type: str,
    severity: str,
    evidence: dict[str, Any],
    action_type: str,
    title: str,
    description: str,
    due_month: str,
) -> ExposureRecommendation:
    return ExposureRecommendation(
        hospital_id=target.hospital_id,
        query_target_id=target.id,
        gap_type=gap_type,
        severity=severity,
        evidence=evidence,
        action_type=action_type,
        title=title,
        description=description,
        owner="MotionLabs Ops",
        due_month=due_month,
        target_priority=str(getattr(target, "priority", "NORMAL")).upper(),
        target_name=str(getattr(target, "name", "")),
    )


def _group_records_by_target(
    targets: Sequence[Any],
    records: Sequence[Any],
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {str(target.id): [] for target in targets}
    query_matrix_to_target: dict[str, str] = {}
    for target in targets:
        for variant in getattr(target, "variants", None) or []:
            query_matrix_id = getattr(variant, "query_matrix_id", None)
            if query_matrix_id:
                query_matrix_to_target[str(query_matrix_id)] = str(target.id)

    for record in records:
        target_key = _uuid_key(getattr(record, "ai_query_target_id", None))
        if target_key not in grouped:
            query_id = getattr(record, "query_id", None)
            target_key = query_matrix_to_target.get(str(query_id)) if query_id else None
        if target_key in grouped:
            grouped[target_key].append(record)
    return grouped


def _base_evidence(
    target: Any,
    records: Sequence[Any],
    successful_records: Sequence[Any],
    failed_count: int,
) -> dict[str, Any]:
    measured_at_values = [
        measured_at
        for measured_at in (_to_iso(getattr(record, "measured_at", None)) for record in records)
        if measured_at
    ]
    latest_measured_at = max(measured_at_values, default=None)
    return {
        "query_target_name": getattr(target, "name", None),
        "target_priority": str(getattr(target, "priority", "NORMAL")).upper(),
        "total_measurements": len(records),
        "successful_measurements": len(successful_records),
        "failed_measurements": failed_count,
        "latest_measured_at": latest_measured_at,
    }


def _is_successful_measurement(record: Any) -> bool:
    status = getattr(record, "measurement_status", None)
    return status is None or str(status).upper() == "SUCCESS"


def _competitor_mention_count(records: Iterable[Any]) -> int:
    count = 0
    for record in records:
        for competitor in getattr(record, "competitor_mentions", None) or []:
            if isinstance(competitor, dict) and competitor.get("is_mentioned"):
                count += 1
    return count


def _has_source_urls(record: Any) -> bool:
    source_urls = getattr(record, "source_urls", None)
    return bool(source_urls)


def _severity_for_target(target: Any, *, severe_for_high: bool) -> str:
    priority = str(getattr(target, "priority", "NORMAL")).upper()
    if priority == "HIGH":
        return "HIGH" if severe_for_high else "MEDIUM"
    if priority == "LOW":
        return "LOW"
    return "MEDIUM"


def _target_sort_key(target: Any) -> tuple[int, str, str]:
    priority = str(getattr(target, "priority", "NORMAL")).upper()
    target_month = getattr(target, "target_month", None) or ""
    name = getattr(target, "name", "") or ""
    return (PRIORITY_RANK.get(priority, 9), target_month, name)


def _recommendation_sort_key(
    recommendation: ExposureRecommendation,
) -> tuple[int, int, str, int, str]:
    return (
        PRIORITY_RANK.get(recommendation.target_priority, 9),
        SEVERITY_RANK.get(recommendation.severity, 9),
        recommendation.due_month,
        ACTION_TYPE_RANK.get(recommendation.action_type, 9),
        recommendation.target_name,
    )


def _action_sort_key(action: ExposureAction) -> tuple[int, int, str, int, str]:
    target = getattr(action, "query_target", None)
    gap = getattr(action, "gap", None)
    priority = str(getattr(target, "priority", "NORMAL")).upper()
    severity = str(getattr(gap, "severity", "MEDIUM")).upper()
    due_month = getattr(action, "due_month", None) or "9999-99"
    created_at = _to_iso(getattr(action, "created_at", None)) or ""
    return (
        PRIORITY_RANK.get(priority, 9),
        SEVERITY_RANK.get(severity, 9),
        due_month,
        ACTION_TYPE_RANK.get(str(getattr(action, "action_type", "")).upper(), 9),
        created_at,
    )


def _uuid_key(value: Any) -> str | None:
    return str(value) if value else None


def _to_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value:
        return str(value)
    return None
