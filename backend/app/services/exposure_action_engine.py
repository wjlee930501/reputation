"""Deterministic exposure gap/action engine.

This module uses only local patient-question and AI mention measurement state. It never calls
external AI, Slack, or network APIs.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.monthly_control import MonthlyMeasurementAttempt, MonthlyMeasurementCell
from app.models.sov import AIQueryTarget, ExposureAction, ExposureGap, SovRecord
from app.services import sov_engine
from app.services.content_citations import hospital_surface_roots, normalize_cited_url
from app.utils.db_locks import acquire_hospital_advisory_lock

ACTIVE_TARGET_STATUSES = {"ACTIVE"}
ACTIVE_GAP_STATUSES = {"OPEN", "WATCHING"}
ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED"}

MENTION_RATE_THRESHOLD = 50.0
SOURCE_OWNED_SHARE_THRESHOLD = 50.0
MIN_COMPARABLE_OBSERVATIONS = 2
MAX_RECORDS_PER_TARGET = 100

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


@dataclass(frozen=True)
class TargetDiagnosis:
    query_target_id: uuid.UUID
    policy_fingerprint: str | None
    cohort_fingerprint: str | None
    evidence: dict[str, Any]
    observed_gap_types: frozenset[str]
    recommendations: tuple[ExposureRecommendation, ...]
    comparable: bool


async def ensure_hospital_exposure_actions(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    today: date | None = None,
    max_create: int = 12,
) -> None:
    """Reconcile open gaps/actions with the latest deterministic recommendations.

    The previous implementation only created work. Once a measurement improved, the old
    gap stayed open forever and the operations queue could only grow. Reconciliation makes
    the measurement -> diagnosis -> action loop self-closing: disappeared gaps and their
    actions are resolved automatically, while continuing gaps receive fresh evidence.
    """
    # select-then-insert 패턴이라 동시 호출(대시보드 이중 로드/프리페치)이 둘 다 존재
    # 검사를 통과해 OPEN gap/action을 중복 생성할 수 있다 — 병원 단위 advisory lock으로
    # 트랜잭션을 직렬화한다 (gap/action 테이블에는 부분 유니크 제약이 없음).
    await _acquire_hospital_advisory_lock(db, hospital_id)
    targets = await _load_targets(db, hospital_id)
    records = await _load_recent_sov_records(db, hospital_id, targets) if targets else []
    diagnoses = _build_target_diagnoses(targets, records, today=today)
    recommendations = sorted(
        (item for diagnosis in diagnoses for item in diagnosis.recommendations),
        key=_recommendation_sort_key,
    )
    active_gaps = await _load_active_gaps(db, hospital_id)

    changed = _reconcile_stale_work(
        active_gaps,
        recommendations,
        completed_at=datetime.now(UTC),
        diagnoses=diagnoses,
    )
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
        else:
            changed = _refresh_gap(gap, recommendation) or changed

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
        else:
            changed = _refresh_action(action, recommendation) or changed

    if changed:
        await db.commit()


async def _acquire_hospital_advisory_lock(db: AsyncSession, hospital_id: uuid.UUID) -> None:
    """Postgres pg_advisory_xact_lock — 키 유도는 app.utils.db_locks에 단일화 (R5)."""
    await acquire_hospital_advisory_lock(db, hospital_id)


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
    diagnosis_date = today or _kst_today()
    active_targets = [
        target
        for target in targets
        if str(getattr(target, "status", "ACTIVE")).upper() in ACTIVE_TARGET_STATUSES
    ]
    diagnoses = _build_target_diagnoses(active_targets, records, today=diagnosis_date)
    return sorted(
        (item for diagnosis in diagnoses for item in diagnosis.recommendations),
        key=_recommendation_sort_key,
    )


def _build_target_diagnoses(
    targets: Sequence[Any],
    records: Sequence[Any],
    *,
    today: date | None = None,
) -> list[TargetDiagnosis]:
    diagnosis_date = today or _kst_today()
    active_targets = [
        target
        for target in targets
        if str(getattr(target, "status", "ACTIVE")).upper() in ACTIVE_TARGET_STATUSES
    ]
    records_by_target = _group_records_by_target(active_targets, records)
    # 병원 전체의 성공 측정 수. 타깃 하나에 결과가 없다는 사실과, 병원이 아직 한 번도
    # 측정되지 않았다는 사실은 전혀 다른 진단이다 — 후자만 "첫 측정 실행"이다.
    hospital_successful_count = sum(1 for record in records if _is_successful_measurement(record))

    diagnoses: list[TargetDiagnosis] = []
    for target in sorted(active_targets, key=_target_sort_key):
        target_records = records_by_target.get(str(target.id), [])
        diagnoses.append(
            _diagnose_target(
                target,
                target_records,
                diagnosis_date,
                hospital_successful_count=hospital_successful_count,
            )
        )

    return diagnoses


async def _load_targets(db: AsyncSession, hospital_id: uuid.UUID) -> list[AIQueryTarget]:
    result = await db.execute(
        select(AIQueryTarget)
        .options(
            selectinload(AIQueryTarget.variants),
            selectinload(AIQueryTarget.hospital),
        )
        .where(
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status.in_(ACTIVE_TARGET_STATUSES),
        )
    )
    return list(result.scalars().all())


async def _load_recent_sov_records(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    targets: Sequence[Any],
) -> list[SovRecord]:
    """Load records for active targets without a hospital-wide truncation.

    A global ``LIMIT 1000`` made a heavily measured target erase another target's evidence
    from the input and falsely complete its work. A partitioned row-number window gives every
    active target its own bounded latest-record allowance; ``_group_records_by_target`` keeps
    the same cap as a defensive boundary for pure callers. Legacy rows without an explicit
    target use their query only when that query belongs to exactly one active target.
    """
    target_ids = [target.id for target in targets]
    query_to_targets: dict[uuid.UUID, set[uuid.UUID]] = {}
    for target in targets:
        for variant in getattr(target, "variants", None) or []:
            query_id = getattr(variant, "query_matrix_id", None)
            if query_id:
                query_to_targets.setdefault(query_id, set()).add(target.id)
    query_to_target = {
        query_id: next(iter(target_ids_for_query))
        for query_id, target_ids_for_query in query_to_targets.items()
        if len(target_ids_for_query) == 1
    }
    query_ids = list(query_to_target)
    ownership_filters = [SovRecord.ai_query_target_id.in_(target_ids)]
    if query_ids:
        ownership_filters.append(
            SovRecord.ai_query_target_id.is_(None) & SovRecord.query_id.in_(query_ids)
        )

    if query_to_target:
        query_target_key = case(
            {query_id: str(target_id) for query_id, target_id in query_to_target.items()},
            value=SovRecord.query_id,
            else_=None,
        )
        target_key = case(
            (
                SovRecord.ai_query_target_id.in_(target_ids),
                cast(SovRecord.ai_query_target_id, String),
            ),
            (
                SovRecord.ai_query_target_id.is_(None),
                query_target_key,
            ),
            else_=None,
        )
    else:
        target_key = cast(SovRecord.ai_query_target_id, String)
    ranked = (
        select(
            SovRecord.id.label("record_id"),
            func.row_number()
            .over(partition_by=target_key, order_by=SovRecord.measured_at.desc())
            .label("target_record_rank"),
        )
        .where(
            SovRecord.hospital_id == hospital_id,
            or_(*ownership_filters),
        )
        .subquery()
    )
    result = await db.execute(
        select(SovRecord)
        .join(ranked, ranked.c.record_id == SovRecord.id)
        .options(selectinload(SovRecord.measurement_run))
        .where(ranked.c.target_record_rank <= MAX_RECORDS_PER_TARGET)
        .order_by(SovRecord.measured_at.desc())
    )
    records = list(result.scalars().all())
    await _attach_monthly_question_snapshots(db, records)
    return records


async def _attach_monthly_question_snapshots(
    db: AsyncSession, records: Sequence[Any]
) -> None:
    record_ids = [record.id for record in records if getattr(record, "id", None)]
    if not record_ids:
        return
    result = await db.execute(
        select(
            MonthlyMeasurementAttempt.sov_record_id,
            MonthlyMeasurementCell.query_key,
            MonthlyMeasurementCell.query_text,
            MonthlyMeasurementCell.query_matrix_id,
            MonthlyMeasurementCell.query_target_id,
            MonthlyMeasurementCell.query_variant_id,
            MonthlyMeasurementCell.platform,
        )
        .join(
            MonthlyMeasurementCell,
            MonthlyMeasurementCell.id == MonthlyMeasurementAttempt.cell_id,
        )
        .where(MonthlyMeasurementAttempt.sov_record_id.in_(record_ids))
    )
    by_record_id = {
        str(row.sov_record_id): {
            "query_key": row.query_key,
            "query_text": row.query_text,
            "query_id": str(row.query_matrix_id) if row.query_matrix_id else None,
            "target_id": str(row.query_target_id) if row.query_target_id else None,
            "variant_id": str(row.query_variant_id) if row.query_variant_id else None,
            "platform": str(row.platform).lower(),
        }
        for row in result.all()
    }
    for record in records:
        snapshot = by_record_id.get(str(getattr(record, "id", "")))
        if snapshot is not None:
            # Runtime-only provenance. The immutable source remains the manifest cell.
            setattr(record, "_exposure_question_snapshot", snapshot)


async def _load_active_gaps(db: AsyncSession, hospital_id: uuid.UUID) -> list[ExposureGap]:
    result = await db.execute(
        select(ExposureGap)
        .options(selectinload(ExposureGap.actions))
        .where(
            ExposureGap.hospital_id == hospital_id,
            ExposureGap.status.in_(ACTIVE_GAP_STATUSES),
        )
    )
    return list(result.scalars().all())


def _reconcile_stale_work(
    gaps: Sequence[Any],
    recommendations: Sequence[ExposureRecommendation],
    *,
    completed_at: datetime,
    diagnoses: Sequence[TargetDiagnosis] = (),
) -> bool:
    """Close work only after a later, comparable per-target observation disproves it."""
    current_gap_keys = {
        (str(item.query_target_id), item.gap_type) for item in recommendations
    }
    current_action_keys = {
        (str(item.query_target_id), item.gap_type, item.action_type)
        for item in recommendations
    }
    diagnoses_by_target = {str(item.query_target_id): item for item in diagnoses}
    changed = False
    for gap in gaps:
        gap_key = (str(getattr(gap, "query_target_id", None)), str(gap.gap_type))
        diagnosis = diagnoses_by_target.get(gap_key[0])
        resolution_evidence = _resolution_evidence(gap, diagnosis)
        gap_resolved = (
            gap_key not in current_gap_keys
            and gap.status in ACTIVE_GAP_STATUSES
            and resolution_evidence is not None
        )
        if gap_resolved:
            existing_evidence = getattr(gap, "evidence", None)
            gap.evidence = {
                **(existing_evidence if isinstance(existing_evidence, dict) else {}),
                "resolution_observation": resolution_evidence,
            }
            gap.status = "RESOLVED"
            changed = True

        for action in getattr(gap, "actions", []) or []:
            action_key = (*gap_key, str(action.action_type))
            if (
                action.status in ACTIVE_ACTION_STATUSES
                and action_key not in current_action_keys
                and gap_resolved
            ):
                action.status = "COMPLETED"
                action.completed_at = completed_at
                changed = True
    return changed


def _resolution_evidence(
    gap: Any,
    diagnosis: TargetDiagnosis | None,
) -> dict[str, Any] | None:
    if diagnosis is None or not diagnosis.comparable:
        return None
    gap_type = str(getattr(gap, "gap_type", ""))
    if gap_type in diagnosis.observed_gap_types:
        return None

    current = diagnosis.evidence
    prior = getattr(gap, "evidence", None)
    prior = prior if isinstance(prior, dict) else {}
    if gap_type not in {"NO_SUCCESSFUL_MEASUREMENT", "TARGET_NOT_MEASURED"}:
        prior_policy = prior.get("cohort_policy_fingerprint")
        if not prior_policy or prior_policy != diagnosis.policy_fingerprint:
            return None
        prior_cohort = prior.get("cohort_comparability_fingerprint")
        if not prior_cohort or prior_cohort != diagnosis.cohort_fingerprint:
            return None
        if not _is_later_observation(
            current.get("latest_measured_at"), prior.get("latest_measured_at")
        ):
            return None

    if gap_type == "SOURCE_SIGNAL_GAP" and (
        current.get("source_evaluable_count", 0) < MIN_COMPARABLE_OBSERVATIONS
    ):
        return None

    return {
        "basis": "later_comparable_target_observation",
        "cohort_policy_fingerprint": diagnosis.policy_fingerprint,
        "cohort_comparability_fingerprint": diagnosis.cohort_fingerprint,
        "cohort_provenance": current.get("cohort_provenance"),
        "successful_measurements": current.get("successful_measurements", 0),
        "latest_measured_at": current.get("latest_measured_at"),
        "mention_rate": current.get("mention_rate"),
        "competitor_mention_count": current.get("competitor_mention_count"),
        "source_observation_counts": current.get("source_observation_counts"),
        "owned_citation_share": current.get("owned_citation_share"),
    }


def _refresh_gap(gap: Any, recommendation: ExposureRecommendation) -> bool:
    changed = False
    for field, value in (
        ("severity", recommendation.severity),
        ("evidence", recommendation.evidence),
    ):
        if getattr(gap, field) != value:
            setattr(gap, field, value)
            changed = True
    return changed


def _refresh_action(action: Any, recommendation: ExposureRecommendation) -> bool:
    changed = False
    for field, value in (
        ("title", recommendation.title),
        ("description", recommendation.description),
        ("owner", recommendation.owner),
        ("due_month", recommendation.due_month),
    ):
        if getattr(action, field) != value:
            setattr(action, field, value)
            changed = True
    return changed


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
    *,
    hospital_successful_count: int = 0,
) -> TargetDiagnosis:
    successful_records = [record for record in records if _is_successful_measurement(record)]
    failed_count = len(records) - len(successful_records)
    due_month = getattr(target, "target_month", None) or diagnosis_date.strftime("%Y-%m")
    base_evidence = _base_evidence(target, records, successful_records, failed_count)
    base_evidence["hospital_successful_measurements"] = hospital_successful_count

    if not records and hospital_successful_count > 0:
        # V0는 표본 질문만 측정하는데 타깃은 활성 질문 전체에서 시드된다. 아직 차례가
        # 오지 않은 질문에 "성공 측정값 없음"을 붙이면 같은 화면의 언급률과 정면으로
        # 충돌하고, 그 문구가 우선순위 상단을 전부 차지해 진짜 갭을 밀어낸다.
        recommendation = _recommendation(
            target,
            "TARGET_NOT_MEASURED",
            # 측정 차례를 기다리는 상태는 노출 문제가 아니다. 질문 우선순위와 무관하게
            # 가장 낮은 심각도로 두어, 실제로 관측된 갭이 항상 앞선다.
            "LOW",
            {
                **base_evidence,
                "rule": "target_not_measured_yet",
            },
            "MEASUREMENT",
            "다음 측정 대상에 이 질문 포함",
            (
                f"이 병원에는 성공한 측정이 {hospital_successful_count}건 있지만 "
                "이 환자 질문은 아직 측정된 적이 없습니다. "
                "질문 문구를 확인하고 다음 측정 대상에 포함하세요."
            ),
            due_month,
        )
        return TargetDiagnosis(
            query_target_id=target.id,
            policy_fingerprint=None,
            cohort_fingerprint=None,
            evidence=base_evidence,
            observed_gap_types=frozenset({"TARGET_NOT_MEASURED"}),
            recommendations=(recommendation,),
            comparable=False,
        )

    if not successful_records:
        recommendation = _recommendation(
            target,
            "NO_SUCCESSFUL_MEASUREMENT",
            _severity_for_target(target, severe_for_high=True),
            {
                **base_evidence,
                "rule": "no_successful_measurements",
            },
            "MEASUREMENT",
            "첫 AI 언급률 측정 실행",
            (
                "성공한 ChatGPT/Gemini 측정값이 없어 "
                "노출 상태를 판단할 수 없습니다. "
                "등록된 환자 질문 문구를 확인한 뒤 첫 측정을 실행하세요."
            ),
            due_month,
        )
        return TargetDiagnosis(
            query_target_id=target.id,
            policy_fingerprint=None,
            cohort_fingerprint=None,
            evidence=base_evidence,
            observed_gap_types=frozenset({"NO_SUCCESSFUL_MEASUREMENT"}),
            recommendations=(recommendation,),
            comparable=False,
        )

    cohort_records, policy_fingerprint, policy_summary = _latest_policy_cohort(records)
    cohort = [record for record in cohort_records if _is_successful_measurement(record)]
    cohort_fingerprint, cohort_provenance = _cohort_identity(cohort)
    if (
        not policy_fingerprint
        or not cohort_fingerprint
        or len(cohort) < MIN_COMPARABLE_OBSERVATIONS
    ):
        # One incomplete/legacy observation is not enough to create new operator work. This
        # state is deliberately represented by an empty recommendation set and an explicit
        # non-comparable diagnosis; reconciliation must not read the absence as recovery.
        insufficient_evidence = {
            **base_evidence,
            "cohort_policy_fingerprint": policy_fingerprint,
            "cohort_policy": policy_summary,
            "cohort_comparability_fingerprint": cohort_fingerprint,
            "cohort_provenance": cohort_provenance,
            "cohort_successful_measurements": len(cohort),
            "diagnosis_state": "INSUFFICIENT_COMPARABLE_EVIDENCE",
            "minimum_comparable_observations": MIN_COMPARABLE_OBSERVATIONS,
        }
        return TargetDiagnosis(
            query_target_id=target.id,
            policy_fingerprint=policy_fingerprint,
            cohort_fingerprint=cohort_fingerprint,
            evidence=insufficient_evidence,
            observed_gap_types=frozenset(),
            recommendations=(),
            comparable=False,
        )

    mention_count = sum(
        1 for record in cohort if bool(getattr(record, "is_mentioned", False))
    )
    mention_rate = round(mention_count / len(cohort) * 100, 1)
    competitor_count = _competitor_mention_count(cohort)
    source_counts = _source_observation_counts(target, cohort)
    source_evaluable_count = (
        source_counts["searched_other_citation"]
        + source_counts["searched_owned_citation"]
    )
    owned_citation_share = (
        round(source_counts["searched_owned_citation"] / source_evaluable_count * 100, 1)
        if source_evaluable_count
        else None
    )

    evidence = {
        **base_evidence,
        "all_successful_measurements": len(successful_records),
        "all_failed_measurements": failed_count,
        **_base_evidence(
            target,
            cohort_records,
            cohort,
            len(cohort_records) - len(cohort),
        ),
        # All rates below use only the newest contiguous cohort whose full measurement policy
        # is known. Keep both a stable fingerprint and a compact human-inspectable summary.
        "cohort_policy_fingerprint": policy_fingerprint,
        "cohort_policy": policy_summary,
        "cohort_comparability_fingerprint": cohort_fingerprint,
        "cohort_provenance": cohort_provenance,
        "cohort_successful_measurements": len(cohort),
        "cohort_record_ids": [str(record.id) for record in cohort if getattr(record, "id", None)],
        "diagnosis_state": "COMPARABLE",
        "mention_count": mention_count,
        "mention_rate": mention_rate,
        "competitor_mention_count": competitor_count,
        "source_observation_counts": source_counts,
        "source_evaluable_count": source_evaluable_count,
        "owned_citation_share": owned_citation_share,
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
                "환자 질문과 연결된 근거 콘텐츠 보강",
                (
                    f"같은 측정 정책의 최근 확정 관측 {len(cohort)}건에서 "
                    "병원 언급이 없습니다. 환자 질문 의도와 맞는 "
                    "자주 묻는 질문/질환/치료 안내 콘텐츠 가이드를 우선 보강하세요."
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
                "경쟁 병원 대비 병원 정보 구조 보강",
                (
                    "경쟁 병원 언급이 병원 언급과 같거나 더 많습니다. "
                    "환자 질문에서 선택 기준, 지역성, 진료 근거가 "
                    "드러나도록 병원 정보 구조를 조정하세요."
                ),
                due_month,
            )
        )

    if (
        source_evaluable_count >= MIN_COMPARABLE_OBSERVATIONS
        and owned_citation_share is not None
        and owned_citation_share < SOURCE_OWNED_SHARE_THRESHOLD
    ):
        recommendations.append(
            _recommendation(
                target,
                "SOURCE_SIGNAL_GAP",
                "MEDIUM",
                {
                    **evidence,
                    "rule": "owned_citations_below_threshold_after_observed_search",
                    "threshold": SOURCE_OWNED_SHARE_THRESHOLD,
                },
                "SOURCE",
                "AI가 참고할 공식 근거 자료 보강",
                (
                    "검색이 실행되고 출처 URL이 관측된 같은 정책 표본에서 자사 공식 "
                    f"채널 인용 비율이 {owned_citation_share:.1f}%입니다. 기존 홈페이지, "
                    "지도 정보, 공개 콘텐츠의 병원명/진료/지역 정보 연결을 점검하세요. "
                    "이 관측만으로 자료 보강이 노출 변화를 일으켰다고 판단하지 않습니다."
                ),
                due_month,
            )
        )

    return TargetDiagnosis(
        query_target_id=target.id,
        policy_fingerprint=policy_fingerprint,
        cohort_fingerprint=cohort_fingerprint,
        evidence=evidence,
        observed_gap_types=frozenset(item.gap_type for item in recommendations),
        recommendations=tuple(recommendations),
        comparable=True,
    )


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
    query_matrix_to_targets: dict[str, set[str]] = {}
    for target in targets:
        for variant in getattr(target, "variants", None) or []:
            query_matrix_id = getattr(variant, "query_matrix_id", None)
            if query_matrix_id:
                query_matrix_to_targets.setdefault(str(query_matrix_id), set()).add(
                    str(target.id)
                )
    query_matrix_to_target = {
        query_id: next(iter(target_ids))
        for query_id, target_ids in query_matrix_to_targets.items()
        if len(target_ids) == 1
    }

    for record in records:
        explicit_target_key = _uuid_key(getattr(record, "ai_query_target_id", None))
        target_key = explicit_target_key
        if explicit_target_key is None:
            query_id = getattr(record, "query_id", None)
            target_key = query_matrix_to_target.get(str(query_id)) if query_id else None
        if target_key in grouped:
            grouped[target_key].append(record)
    for target_key, target_records in grouped.items():
        grouped[target_key] = sorted(
            target_records,
            key=lambda item: _datetime_sort_key(getattr(item, "measured_at", None)),
            reverse=True,
        )[:MAX_RECORDS_PER_TARGET]
    return grouped


def _latest_policy_cohort(
    records: Sequence[Any],
) -> tuple[list[Any], str | None, dict[str, Any] | None]:
    """Return the newest target/run cohort with one recorded measurement policy.

    A run is the smallest durable group whose records were produced under one received policy.
    Using the latest run also prevents a large historical deficit from permanently diluting a
    newer observation. If run provenance is unavailable, use only the newest contiguous policy
    epoch; never skip newer unknown evidence to revive an older cohort.
    """
    ordered = sorted(
        records,
        key=lambda item: _datetime_sort_key(getattr(item, "measured_at", None)),
        reverse=True,
    )
    if not ordered:
        return [], None, None
    fingerprint, summary = _record_policy(ordered[0])
    if fingerprint is None:
        return [], None, None

    latest_run_key = _record_run_key(ordered[0])
    cohort: list[Any] = []
    for record in ordered:
        if latest_run_key is not None and _record_run_key(record) != latest_run_key:
            continue
        record_fingerprint, _ = _record_policy(record)
        if record_fingerprint != fingerprint:
            if latest_run_key is None:
                break
            return [], None, None
        cohort.append(record)
    return cohort, fingerprint, summary


def _record_policy(record: Any) -> tuple[str | None, dict[str, Any] | None]:
    run = getattr(record, "measurement_run", None)
    config = getattr(run, "config", None)
    protocol = config.get("measurement_protocol") if isinstance(config, Mapping) else None
    if not isinstance(protocol, Mapping):
        protocol = getattr(record, "measurement_protocol", None)
    if not isinstance(protocol, Mapping) or not protocol:
        return None, None

    # The received snapshot includes the frozen tracking-set identity for month-end runs.
    # Dropping it would let a changed question set masquerade as the same measurement policy.
    material = json.dumps(dict(protocol), ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(material.encode()).hexdigest()[:16]
    summary_keys = (
        "policy_version",
        "openai_model_query",
        "gemini_model",
        "prompt_fingerprint",
        "judge_prompt_fingerprint",
        "query_design_version",
        "measurement_window",
        "tracking_set_fingerprint",
        "tracking_set_size",
    )
    summary = {key: protocol[key] for key in summary_keys if key in protocol}
    return fingerprint, summary


def _cohort_identity(
    records: Sequence[Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Fingerprint frozen questions, platforms, variants, and actual response models.

    Current QueryMatrix/variant text is deliberately never consulted. It can be edited after
    the provider answered and therefore cannot establish what the historical record measured.
    """
    observations: list[dict[str, Any]] = []
    for record in records:
        question = _frozen_question_snapshot(record)
        answer_model = str(getattr(record, "answer_model", "") or "").strip()
        platform = str(
            question.get("platform") if question else getattr(record, "ai_platform", "")
        ).strip().lower()
        query_text = str(question.get("query_text") if question else "").strip()
        if question is None or not query_text or not platform or not answer_model:
            return None, None
        observations.append(
            {
                "query_key": question.get("query_key"),
                "query_text_hash": hashlib.sha256(query_text.encode()).hexdigest()[:16],
                "query_id": question.get("query_id") or _uuid_key(getattr(record, "query_id", None)),
                "target_id": question.get("target_id")
                or _uuid_key(getattr(record, "ai_query_target_id", None)),
                "variant_id": question.get("variant_id")
                or _uuid_key(getattr(record, "ai_query_variant_id", None)),
                "platform": platform,
                "answer_model": answer_model,
            }
        )
    if not observations:
        return None, None

    observations.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    material = json.dumps(observations, ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(material.encode()).hexdigest()[:16]
    provenance = {
        "query_ids": sorted(
            {item["query_id"] for item in observations if item.get("query_id")}
        ),
        "query_text_hashes": sorted({item["query_text_hash"] for item in observations}),
        "variant_ids": sorted(
            {item["variant_id"] for item in observations if item.get("variant_id")}
        ),
        "platforms": sorted({item["platform"] for item in observations}),
        "answer_models": sorted({item["answer_model"] for item in observations}),
        "observation_count": len(observations),
    }
    return fingerprint, provenance


def _frozen_question_snapshot(record: Any) -> dict[str, Any] | None:
    attached = getattr(record, "_exposure_question_snapshot", None)
    if isinstance(attached, Mapping):
        return dict(attached)

    run = getattr(record, "measurement_run", None)
    config = getattr(run, "config", None)
    snapshot = config.get("query_snapshot") if isinstance(config, Mapping) else None
    query_id = _uuid_key(getattr(record, "query_id", None))
    if not isinstance(snapshot, list) or not query_id:
        return None
    for item in snapshot:
        if not isinstance(item, Mapping) or str(item.get("query_id")) != query_id:
            continue
        query_text = item.get("query_text")
        if not isinstance(query_text, str) or not query_text.strip():
            return None
        return {
            "query_key": None,
            "query_text": query_text,
            "query_id": query_id,
            "target_id": _uuid_key(getattr(record, "ai_query_target_id", None)),
            "variant_id": _uuid_key(getattr(record, "ai_query_variant_id", None)),
            "platform": str(getattr(record, "ai_platform", "") or "").lower(),
        }
    return None


def _record_run_key(record: Any) -> str | None:
    run_id = getattr(record, "measurement_run_id", None)
    if run_id:
        return str(run_id)
    run = getattr(record, "measurement_run", None)
    run_id = getattr(run, "id", None)
    return str(run_id) if run_id else None


def _source_observation_counts(target: Any, records: Sequence[Any]) -> dict[str, int]:
    """Classify source evidence without turning missing instrumentation into a source gap."""
    counts = {
        "no_search": 0,
        "unknown_search": 0,
        "searched_missing_sources": 0,
        "searched_other_citation": 0,
        "searched_owned_citation": 0,
        "searched_citation_ownership_unknown": 0,
    }
    hospital = getattr(target, "hospital", None)
    owned_roots = _owned_source_roots(hospital)
    for record in records:
        search_calls = getattr(record, "search_calls", None)
        if search_calls is None:
            counts["unknown_search"] += 1
            continue
        if search_calls <= 0:
            counts["no_search"] += 1
            continue
        urls = _source_urls(record)
        if not urls:
            counts["searched_missing_sources"] += 1
        elif not owned_roots:
            counts["searched_citation_ownership_unknown"] += 1
        elif any(_matches_owned_source(url, owned_roots) for url in urls):
            counts["searched_owned_citation"] += 1
        else:
            counts["searched_other_citation"] += 1
    return counts


def _source_urls(record: Any) -> list[str]:
    return [
        value.strip()
        for value in (getattr(record, "source_urls", None) or [])
        if isinstance(value, str) and value.strip()
    ]


def _owned_source_roots(hospital: Any) -> tuple[tuple[str, str, bool], ...]:
    """Return (host, path, host_wide) roots for official and platform surfaces."""
    if hospital is None:
        return ()
    roots: set[tuple[str, str, bool]] = {
        (root.host, root.base_path, not root.base_path)
        for root in hospital_surface_roots(hospital)
    }
    for field in (
        "website_url",
        "blog_url",
        "kakao_channel_url",
        "google_business_profile_url",
        "google_maps_url",
        "naver_place_url",
    ):
        raw = getattr(hospital, field, None)
        normalized = normalize_cited_url(raw)
        if normalized is None:
            continue
        path = "" if normalized.path == "/" else normalized.path
        # A path identifies one hospital on shared platforms. A bare hospital-owned domain
        # safely owns the full host.
        roots.add((normalized.host, path, not path))
    return tuple(sorted(roots))


def _matches_owned_source(url: str, roots: Sequence[tuple[str, str, bool]]) -> bool:
    normalized = normalize_cited_url(url)
    if normalized is None:
        return False
    path = "" if normalized.path == "/" else normalized.path
    for root_host, root_path, host_wide in roots:
        if normalized.host != root_host:
            continue
        if host_wide:
            return True
        if path == root_path or path.startswith(f"{root_path}/"):
            return True
    return False


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
    """확정 판정만 분모다 — admin/sov.py의 동명 함수와 같은 기준이어야 한다.

    AMBIGUOUS를 남기면 `is_mentioned`의 None이 falsy라 '미언급'으로 계상되고,
    노출 액션 우선순위가 있지도 않은 미언급을 근거로 매겨진다.
    """
    return sov_engine.record_is_confirmed(record)


def _kst_today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _competitor_mention_count(records: Iterable[Any]) -> int:
    """레코드당 '경쟁사 중 하나라도 언급되면 1'(0/1 스케일).

    mention_count(레코드당 0/1)와 동일 스케일로 맞춰 비교가 왜곡되지 않게 한다 — 레코드마다
    경쟁사 언급을 모두 합산하면 등록 경쟁사 수가 많을수록 갭이 과대 판정되던 문제 해소.
    """
    count = 0
    for record in records:
        for competitor in getattr(record, "competitor_mentions", None) or []:
            if isinstance(competitor, dict) and competitor.get("is_mentioned"):
                count += 1
                break
    return count


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


# 정렬은 심각도가 먼저다. 질문 우선순위를 앞세우면 우선순위만 높고 심각도는 낮은
# 항목(예: 아직 측정 차례가 오지 않은 HIGH 질문)이 max_create 슬롯을 전부 차지해,
# 실제로 관측된 미언급 갭이 큐에서 아예 사라진다. 심각도 자체가 이미 질문 우선순위를
# 반영하므로(_severity_for_target), 우선순위는 같은 심각도 안의 동점 처리로 남긴다.
def _recommendation_sort_key(
    recommendation: ExposureRecommendation,
) -> tuple[int, int, str, int, str]:
    return (
        SEVERITY_RANK.get(recommendation.severity, 9),
        PRIORITY_RANK.get(recommendation.target_priority, 9),
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
        SEVERITY_RANK.get(severity, 9),
        PRIORITY_RANK.get(priority, 9),
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


def _datetime_sort_key(value: Any) -> float:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return float("-inf")
    else:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _is_later_observation(current: Any, prior: Any) -> bool:
    current_key = _datetime_sort_key(current)
    prior_key = _datetime_sort_key(prior)
    return current_key != float("-inf") and current_key > prior_key
