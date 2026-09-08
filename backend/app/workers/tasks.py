# allow: SIZE_OK -- Celery task registry keeps legacy task import names; release-critical helpers are split by task family.
"""
Celery 태스크 전체
- trigger_v0_report: 프로파일 완료 시 V0 분석 트리거
- build_aeo_site: 콘텐츠 허브 공개 노출 상태 준비 (legacy task name)
- nightly_content_generation: 매일 밤 내일 콘텐츠 생성
- morning_content_auto_publish: 매일 아침 오늘 콘텐츠 자동 검증·발행 + 예외 요약
- run_sov_for_hospital: 단일 병원 AI 답변 언급률 측정
- run_weekly_monitoring: 월간 코호트를 제외한 ACTIVE 병원 주간 측정
- adjust_query_priorities: AI 답변 언급 결과 기반 질문 우선순위 조정
- run_monthly_reports: 전체 병원 월간 리포트
"""

import asyncio
import hashlib
import logging
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import arrow
import httpx
from billiard.exceptions import SoftTimeLimitExceeded, WorkerLostError
from celery import current_task
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal, SyncSessionPinnedConnection
from app.models.content import (
    ContentItem,
    ContentSchedule,
    ContentStatus,
    monthly_quota_for_plan,
)
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import (
    MeasurementObservationSlot,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
)
from app.models.operations import IncidentSeverity, OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.models.sov import (
    AIQueryTarget,
    AIQueryVariant,
    ExposureAction,
    ExposureGap,
    MeasurementRun,
    QueryMatrix,
    SovRecord,
)
from app.services import cost_guard, indexnow, notifier, operation_run_payloads, sov_engine
from app.services import published_image_recertification as recertification
from app.services.asset_extractor import evidence_text_is_acceptable
from app.services.audit_log import write_audit_log_sync
from app.services.content_ai_review import (
    ContentAiReviewStatus,
    review_generated_content,
)
from app.services.content_engine import EXISTING_TITLE_PROMPT_LIMIT, generate_content
from app.services.content_provenance import build_generation_provenance
from app.services.content_publication import (
    apply_publication_assessment,
    assess_content_publication,
    image_certification_current,
    record_publication_identity,
)
from app.services.content_publish_notifications import (
    enqueue_generation_blocked_digest_sync,
)
from app.services.content_target_planner import prepare_automatic_content_brief_sync
from app.services.doctor_pdf_contracts import DoctorV0Baseline
from app.services.doctor_report_artifact import generate_doctor_pdf_report
from app.services.domain_health_control import record_domain_health_check
from app.services.domain_live_status import LiveDomainCheck, apply_live_domain_check
from app.services.essence_auto_review import (
    AUTO_ESSENCE_ACTOR,
    EssenceAiReview,
    EssenceRefreshStatus,
    essence_refresh_needed,
    refresh_essence_snapshot,
    release_essence_refresh_claim,
    review_essence_candidate,
)
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    ESSENCE_STATUS_MISSING_APPROVED,
    ESSENCE_STATUS_NEEDS_REVIEW,
    build_monthly_essence_summary,
    compute_source_content_hash,
    llm_enabled,
    metered_llm_calls,
    process_source_asset,
    screen_content_against_philosophy,
    source_processing_coverage,
    source_processing_ranges,
    synthesize_philosophy,
    validate_source_excerpt,
)
from app.services.essence_readiness import (
    get_current_approved_philosophy_sync,
    get_essence_readiness_sync,
)
from app.services.essence_sources import required_text_source_predicate
from app.services.hospital_activation import (
    AUTO_ACTIVATE_ACTOR,
    activate_hospital_sync,
    blocker_reason,
    evaluate_auto_activation,
    public_site_url,
)
from app.services.image_direction import hospital_image_direction
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    certify_existing_image,
    generate_image,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.image_policy import ImagePolicyRejectedError
from app.services.incident_types import IncidentFingerprint
from app.services.measurement_slots import (
    answer_artifact,
    checkpoint_answer,
    checkpoint_judgment,
    claim_slot_stage,
    ensure_monthly_slots,
    ensure_v0_slots,
    slot_is_terminal,
    slot_needs_answer,
    slot_needs_judgment,
    slots_for_run,
    summarize_observation_slots,
)
from app.services.monthly_content_operations import (
    build_monthly_content_operations_snapshot,
)
from app.services.monthly_events import MonthlyRunStage
from app.services.monthly_manifest import (
    ManifestError,
    ManifestPolicyDrift,
    apply_manifest_to_report,
    close_manifest,
    freeze_dispatch_manifest,
    link_attempt,
    reopen_incomplete_manifest_for_recovery,
    summarize_manifest,
)
from app.services.monthly_period import (
    MonthlyPeriodError,
    ReportBuildReason,
    eligible_hospital_ids,
    is_monthly_recovery_window,
    lock_report_version_plan,
    reporting_period,
    require_closed_period,
    scheduled_report_period,
)
from app.services.monthly_report_delivery import (
    monthly_doctor_artifact_is_valid,
    monthly_report_delivery_gate,
)
from app.services.monthly_report_gap_notifications import (
    enqueue_monthly_report_gap_summary_sync,
)
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest
from app.services.monthly_sov_types import ManifestCellInput
from app.services.onboarding_notifications import (
    build_hospital_activated_notification,
    build_site_built_notification,
    build_v0_ready_notification,
    enqueue_onboarding_notification_sync,
)
from app.services.ops_incident_alerts import (
    open_ops_incident,
    recover_ops_incident,
    recover_ops_incidents_for_hospital,
)
from app.services.post_publish_review_policy import (
    AUTO_PUBLISHABLE_STATUSES,
    auto_publish_catchup_start,
    auto_publish_due_predicate,
    publicly_operational_hospital_predicate,
)
from app.services.report_artifact_validation import DoctorPdfValidationError
from app.services.report_attribution import (
    CitationAttributionInput,
    ContentAttributionInput,
    build_citation_attribution,
    build_content_attribution_summary,
)
from app.services.report_engine import (
    build_doctor_report_view,
    build_strategy_summary,
    generate_pdf_report,
)
from app.services.site_revalidate import (
    content_site_paths,
    ensure_site_revalidate_configured,
    hospital_site_paths,
    trigger_content_site_revalidate_safe,
    trigger_hospital_site_revalidate_safe,
    trigger_site_revalidate,
)
from app.services.site_revalidation_control import (
    content_is_revalidation_recoverable,
    record_retry_failure,
    record_revalidation_success,
    run_revalidation_direction,
)
from app.services.source_processing_runs import (
    SOURCE_PROCESSING_OPERATION,
    SOURCE_PROCESSING_RETRY_DELAY_SECONDS,
    processing_claim,
    processing_input_hash,
    run_dispatch_matches,
    source_processing_reservation_id,
    source_run_key,
    with_processing_claim,
    without_processing_claim,
)
from app.services.sov_engine import (
    MENTION_RATE_INTENTS,
    calculate_sov,
    classify_query_intent,
    fetch_answer,
    generate_query_matrix_specs,
    judge_answer,
    judgment_input_fingerprint,
    run_single_query,
)
from app.services.sov_tracking_set import (
    MEASUREMENT_WINDOW_MONTH_END,
    hospital_in_monthly_cohort,
    iter_monthly_sov_cohort,
    register_convertible_tracking_sets,
    tracking_set_fingerprint,
    tracking_set_members,
)
from app.services.v0_claim import v0_claim_is_alive_sync
from app.utils.db_locks import (
    acquire_hospital_advisory_lock_sync,
    acquire_hospital_advisory_session_lock_sync,
    release_hospital_advisory_session_lock_sync,
)
from app.workers.content_publication_block_control import ensure_publication_block_run
from app.workers.dispatch_auth import (
    DispatchAuthorizationError,
    build_dispatch_headers,
    require_dispatch,
)
from app.workers.generation_batch_run import GenerationBatchRecorder
from app.workers.generation_incident_control import (
    PREPUBLISH_MORNING_BATCH,
    PUBLISH_MORNING_BATCH,
    generation_block_digest_due,
    generation_notify_requested,
    generation_safe_cause,
    open_generation_incident,
    recover_generation_incidents,
)
from app.workers.generation_retry_policy import (
    GenerationRetryClass,
    next_recovery_sweep,
    retry_class_for,
    retry_is_due,
)
from app.workers.generation_run_control import (
    GenerationItemState,
    classify_generation_failure,
    create_item_run,
    explicit_run_context,
    explicit_run_matches,
    finish_explicit_run,
)
from app.workers.monthly_artifact_incident_control import (
    MonthlyArtifactIncidentContext,
    record_monthly_artifact_failure,
)
from app.workers.monthly_artifact_recovery_control import recover_monthly_artifact_failures
from app.workers.monthly_slot_incident_control import (
    open_monthly_slot_failure,
    recover_monthly_slot_failure,
)
from app.workers.monthly_slots import create_next_month_slots_for_schedule
from app.workers.nightly_generation_batch import (
    NIGHTLY_GENERATION_CAP,
    _load_nightly_generation_batch,
    _nightly_generation_stmt,  # noqa: F401 — test_tasks_nightly가 tasks 경유로 참조하는 re-export
    _stuck_claims_stmt,  # noqa: F401 — test_tasks_nightly가 tasks 경유로 참조하는 re-export
    claim_generation_lease,
    load_stuck_claims,
    release_generation_claim,
    release_unfinished_claims,
    write_back_generated_content,
    write_back_generated_image,
    write_back_published_image_certificate,
)
from app.workers.nowon_august_backfill import backfill_nowon_august_2026_slots
from app.workers.nowon_orthopedic_faq_regenerate import regenerate_nowon_orthopedic_faq
from app.workers.v0_checkpoint import (
    find_resumable_v0_measurement_run,
    find_reusable_v0_measurement_run,
    load_v0_checkpoint,
    v0_measurement_run_config,
)
from app.workers.weekly_sov_incident_control import (
    open_monthly_sov_failure,
    open_weekly_sov_capacity_digest,
    open_weekly_sov_failure,
    recover_monthly_sov_failure,
    recover_weekly_sov_failure,
)

# 격주 측정 주차 판정 — **절대 기준 경과 주 수**로 계산한다.
#
# `isocalendar()[1] % 2`를 쓰면 ISO 53주 연도 경계에서 패리티 연속성이 깨진다.
# 2026년이 53주 연도라 52주(짝=측정) → 53주(홀=스킵) → 1주(홀=스킵)로 이어져
# NORMAL 우선순위 쿼리가 3주 공백을 갖고, 12월/1월 표본이 절반이 되어 전월 대비
# 변화가 "표본 수 변화"로 오염된다.
_MEASUREMENT_WEEK_EPOCH = date(2026, 1, 5)  # 2026-W02 월요일 (임의의 고정 기준점)


def _is_even_measurement_week(today: date) -> bool:
    """격주 측정에서 이번 주가 '측정하는 주'인가."""
    return ((today - _MEASUREMENT_WEEK_EPOCH).days // 7) % 2 == 0


logger = logging.getLogger(__name__)

AUTO_PUBLISH_ACTOR = "SYSTEM_AUTO_PUBLISH"
AUTO_REMEDIATION_MAX_GENERATIONS = 2
MORNING_CLOSE_START = time(7, 45)


def _generation_philosophy_sync(db, hospital_id: uuid.UUID) -> HospitalContentPhilosophy | None:
    """Use only an approval for the complete current processed-source snapshot.

    Source ingestion automatically processes and reviews a new snapshot. During
    that bounded refresh, generation pauses without a per-item notification and
    resumes when the new snapshot is auto-approved.
    """

    readiness = get_essence_readiness_sync(db, hospital_id)
    return readiness.current


def _morning_close_due(item: ContentItem, *, now_kst=None) -> bool:
    """Return whether this slot is in its final pre-publication close window."""

    observed = now_kst or arrow.now("Asia/Seoul")
    scheduled_date = getattr(item, "scheduled_date", None)
    return bool(
        scheduled_date is not None
        and scheduled_date <= observed.date()
        and observed.time().replace(tzinfo=None) >= MORNING_CLOSE_START
    )


_GENERATION_ATTEMPT_KEY = "generation_attempt"
_STORED_EMPTY_CONTENT_BLOCK_CODES = frozenset(
    {"MISSING_APPROVED_ESSENCE", "COST_BLOCKED", "GENERATION_REJECTED"}
)
_AUTOMATIC_BODY_REPAIR_CODES = frozenset(
    {
        "FAQ_FIELDS_MISSING",
        "MISSING_REFERENCES",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "CONTENT_AI_HARD_FINDING",
        "CONTENT_AI_REVIEW_STALE",
    }
)


def _generation_attempt_context(
    item: ContentItem, philosophy: HospitalContentPhilosophy | None
) -> str:
    """Fingerprint inputs whose change can justify one more body attempt."""

    philosophy_id = str(getattr(philosophy, "id", "") or "MISSING")
    content_type = str(getattr(getattr(item, "content_type", None), "value", "") or "")
    scheduled_date = str(getattr(item, "scheduled_date", "") or "")
    query_target_id = str(getattr(item, "query_target_id", "") or "")
    return (
        f"philosophy={philosophy_id};content_type={content_type};"
        f"scheduled_date={scheduled_date};query_target={query_target_id}"
    )


def _generation_summary(
    db,
    hospital_id: uuid.UUID,
    screening: Any,
    philosophy: Any,
    approved_brief: dict | None,
) -> dict:
    summary = dict(screening.summary or {})
    summary["generation_provenance"] = build_generation_provenance(
        db,
        hospital_id=hospital_id,
        philosophy=philosophy,
        approved_brief=approved_brief,
    )
    return summary


def _stored_generation_attempt(item: ContentItem) -> dict[str, Any]:
    summary = getattr(item, "essence_check_summary", None)
    if not isinstance(summary, dict):
        return {}
    attempt = summary.get(_GENERATION_ATTEMPT_KEY)
    if not isinstance(attempt, dict):
        return {}
    return dict(attempt)


def _publication_block_details(item: ContentItem, assessment: Any) -> tuple[str, str]:
    """Prefer a persisted generation cause over the empty-content symptom."""

    code = assessment.code or "GENERATION_FAILED"
    message = assessment.message or "자동 발행 준비 검사를 통과하지 못했습니다."
    if code != "CONTENT_NOT_GENERATED":
        return code, message

    stored_code = _stored_generation_attempt(item).get("reason")
    if stored_code in _STORED_EMPTY_CONTENT_BLOCK_CODES:
        return stored_code, generation_safe_cause(stored_code)
    return code, message


def _generation_attempt_is_unchanged(
    item: ContentItem, philosophy: HospitalContentPhilosophy | None
) -> bool:
    previous = _stored_generation_attempt(item)
    unchanged = bool(
        previous.get("reason")
        and previous.get("context") == _generation_attempt_context(item, philosophy)
    )
    if not unchanged:
        return False
    return not retry_is_due(previous)


def _remember_generation_attempt(
    db,
    item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
    reason: str,
) -> None:
    """Persist one no-body outcome without adding a schema column."""

    summary = getattr(item, "essence_check_summary", None)
    updated = dict(summary) if isinstance(summary, dict) else {}
    context = _generation_attempt_context(item, philosophy)
    previous = _stored_generation_attempt(item)
    retry_class = retry_class_for(reason)
    same_context = previous.get("context") == context
    previous_provider_attempts = int(
        previous.get(
            "provider_attempt_count",
            (
                previous.get("attempt_count", 0)
                if previous.get("reason") != "COST_BLOCKED"
                else 0
            ),
        )
        or 0
    ) if same_context else 0
    previous_guard_deferrals = int(previous.get("guard_deferral_count") or 0) if same_context else 0
    provider_attempt_count = previous_provider_attempts
    guard_deferral_count = previous_guard_deferrals
    if reason == "COST_BLOCKED":
        guard_deferral_count += 1
    elif retry_class == GenerationRetryClass.ENVIRONMENT_RECOVERABLE:
        provider_attempt_count += 1
    attempt = {
        "context": context,
        "reason": reason,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "retry_class": retry_class.value,
        # ``attempt_count`` remains for rolling readers, but it now represents
        # paid/provider failures only. Cost-guard deferrals are separately visible
        # and never exhaust the provider recovery budget before a day/month reset.
        "attempt_count": provider_attempt_count,
        "provider_attempt_count": provider_attempt_count,
        "guard_deferral_count": guard_deferral_count,
    }
    if retry_class == GenerationRetryClass.ENVIRONMENT_RECOVERABLE:
        attempt["next_retry_at"] = next_recovery_sweep().isoformat()
    updated[_GENERATION_ATTEMPT_KEY] = attempt
    item.essence_check_summary = updated
    db.commit()


def _clear_generation_attempt(db, item: ContentItem) -> None:
    summary = getattr(item, "essence_check_summary", None)
    if not isinstance(summary, dict) or _GENERATION_ATTEMPT_KEY not in summary:
        return
    updated = dict(summary)
    updated.pop(_GENERATION_ATTEMPT_KEY, None)
    item.essence_check_summary = updated
    db.commit()


def _recover_missing_content_image(
    db,
    item: ContentItem,
    hospital: Hospital,
    philosophy: HospitalContentPhilosophy,
) -> GenerationItemState:
    """Fill a missing or legacy-unverified image without rewriting stored text."""

    from app.services.content_publication import image_certification_current

    if image_certification_current(item):
        _clear_generation_attempt(db, item)
        return GenerationItemState.SUCCEEDED
    try:
        image_source_title = item.title
        expected_revision = (
            int(getattr(item, "content_revision", 1) or 1)
            if hasattr(item, "content_revision")
            else None
        )
        expected_claim_token = getattr(item, "generation_claim_token", None)
        guard_kwargs = {}
        if expected_revision is not None:
            guard_kwargs["expected_revision"] = expected_revision
        if expected_claim_token is not None:
            guard_kwargs["expected_claim_token"] = expected_claim_token
        if getattr(item, "image_url", None):
            try:
                content_hash, subject_hash = _run_async(
                    certify_existing_image(
                        item.image_url,
                        content_type=item.content_type,
                        topic=image_source_title,
                        hospital_id=hospital.id,
                    )
                )
                image_written = write_back_generated_image(
                    db,
                    item_id=item.id,
                    expected_title=image_source_title,
                    **guard_kwargs,
                    values={
                        "image_content_hash": content_hash,
                        "image_subject_hash": subject_hash,
                        "image_policy_version": IMAGE_POLICY_VERSION,
                        "image_policy_verified_at": datetime.now(timezone.utc),
                    },
                )
                if image_written == 0:
                    db.rollback()
                    return GenerationItemState.DISCARDED
                db.commit()
                db.refresh(item)
                _clear_generation_attempt(db, item)
                return GenerationItemState.SUCCEEDED
            except ImagePolicyRejectedError:
                # The stored bytes no longer fit the changed subject. Generate one
                # fresh candidate below; transient reviewer failures stay recoverable.
                pass
        image_url, image_prompt = _run_async(
            generate_image(
                item.content_type,
                hospital.slug,
                topic=image_source_title,
                direction=hospital_image_direction(hospital),
                hospital_id=hospital.id,
            )
        )
        if not image_url:
            logger.warning("Image generation returned no URL for %s (text saved)", item.id)
            _remember_generation_attempt(db, item, philosophy, "IMAGE_GENERATION_FAILED")
            return GenerationItemState.PARTIAL
        image_values = {
            "image_url": image_url,
            "image_prompt": image_prompt,
            "image_policy_verified_at": datetime.now(timezone.utc),
        }
        if hasattr(item, "image_content_hash"):
            image_values.update(
                {
                    "image_content_hash": image_content_hash_from_url(image_url),
                    "image_subject_hash": image_subject_hash(
                        item.content_type, image_source_title
                    ),
                    "image_policy_version": IMAGE_POLICY_VERSION,
                }
            )
        image_written = write_back_generated_image(
            db,
            item_id=item.id,
            expected_title=image_source_title,
            **guard_kwargs,
            values=image_values,
        )
        if image_written == 0:
            db.rollback()
            logger.warning(
                "Image write-back skipped for %s — status changed during image generation",
                item.id,
            )
            return GenerationItemState.DISCARDED
        db.commit()
        db.refresh(item)
        _clear_generation_attempt(db, item)
        return GenerationItemState.SUCCEEDED
    except Exception as error:
        logger.warning(
            "Image generation failed for %s (text saved): %s",
            item.id,
            type(error).__name__,
        )
        db.rollback()
        db.refresh(item)
        _remember_generation_attempt(db, item, philosophy, "IMAGE_GENERATION_FAILED")
        return GenerationItemState.PARTIAL


def _record_generation_batch_outcome(
    db,
    recorder: GenerationBatchRecorder,
    item: ContentItem,
    hospital: Hospital,
    state: GenerationItemState,
    code: str | None,
    message: str | None,
    *,
    notify: bool | None = None,
) -> None:
    """Persist one batch outcome and its existing incident lifecycle."""

    if state == GenerationItemState.FAILED:
        code = code or "GENERATION_FAILED"
        message = message or "자동 발행 준비 검사를 통과하지 못했습니다."
        recorder.record(
            item.id,
            state,
            safe_error_code=code,
            safe_error_message=message,
        )
        failed_run = recorder.item_run(
            item.id,
            hospital.id,
            "REGENERATE_CONTENT",
            OperationRunState.FAILED,
            safe_error_code=code,
            safe_error_message=message,
        )
        _run_async(
            open_generation_incident(
                item_id=item.id,
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                run_id=failed_run.id,
                code=code,
                message=message,
                notify=generation_notify_requested(code) if notify is None else notify,
            )
        )
        return
    if state == GenerationItemState.PARTIAL:
        code = code or "IMAGE_GENERATION_FAILED"
        message = message or "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다."
        recorder.record(
            item.id,
            state,
            safe_error_code=code,
            safe_error_message=message,
        )
        text_run = create_item_run(
            db,
            parent_run_id=recorder.run.id,
            item_id=item.id,
            hospital_id=hospital.id,
            operation_type="REGENERATE_CONTENT",
            state=OperationRunState.SUCCEEDED,
            result={"state": "SUCCEEDED", "artifact": "text"},
            attempt_kind="text",
        )
        _run_async(
            recover_generation_incidents(
                item.id,
                hospital.id,
                hospital.name,
                text_run.id,
                include_image=False,
            )
        )
        image_run = recorder.item_run(
            item.id,
            hospital.id,
            "REGENERATE_CONTENT_IMAGE",
            OperationRunState.FAILED,
            safe_error_code=code,
            safe_error_message=message,
        )
        _run_async(
            open_generation_incident(
                item_id=item.id,
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                run_id=image_run.id,
                code=code,
                message=message,
                notify=generation_notify_requested(code) if notify is None else notify,
            )
        )
        return
    if state == GenerationItemState.DISCARDED:
        recorder.record(item.id, state)
        recorder.item_run(
            item.id,
            hospital.id,
            "REGENERATE_CONTENT_IMAGE",
            OperationRunState.CANCELLED,
        )
        return
    if state == GenerationItemState.SKIPPED:
        code = code or "GENERATION_SKIPPED"
        message = message or "생성 조건이 이전 시도와 달라지지 않아 건너뛰었습니다."
        recorder.record(
            item.id,
            state,
            safe_error_code=code,
            safe_error_message=message,
        )
        return

    recorder.record(item.id, GenerationItemState.SUCCEEDED)
    success_run = recorder.item_run(
        item.id,
        hospital.id,
        "REGENERATE_CONTENT",
        OperationRunState.SUCCEEDED,
    )
    _run_async(
        recover_generation_incidents(
            item.id,
            hospital.id,
            hospital.name,
            success_run.id,
        )
    )


def _review_findings(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return []
    findings = summary.get("findings")
    if not isinstance(findings, list):
        return []
    return [str(finding) for finding in findings if str(finding).strip()][:5]


def _screening_probe(content_data: dict) -> ContentItem:
    return ContentItem(
        title=content_data["title"],
        body=content_data["body"],
        meta_description=content_data.get("meta_description"),
        faq_question=content_data.get("faq_question"),
        faq_answer_summary=content_data.get("faq_answer_summary"),
    )


async def _generate_with_auto_review(
    *,
    hospital: Hospital,
    item: ContentItem,
    existing_titles: list[str],
    philosophy: HospitalContentPhilosophy,
    approved_brief: dict | None,
):
    """Generate, independently review, and rewrite without bypassing hard gates."""

    findings = _review_findings(getattr(item, "essence_check_summary", None))
    automatic_rewrites = int(bool(findings))
    reviewer_driven_rewrites = 0
    # 측정 질의 키워드 미반영은 **한 번만** 보완 재작성을 부른다. 하드 게이트로 올리면
    # 한국어 형태 변화 때문에 정상 글이 버려지고, 무제한 재시도로 두면 비용만 늘어난다.
    alignment_remediation_used = False
    last_content: dict | None = None
    last_screening = None
    last_ai_review = None
    last_generation_error: Exception | None = None

    for generation_index in range(AUTO_REMEDIATION_MAX_GENERATIONS):
        if generation_index > 0:
            decision = await cost_guard.check_and_increment("content")
            if not decision.allowed:
                logger.info(
                    "Automatic content remediation stopped by cost guard: hospital=%s",
                    hospital.id,
                )
                break
            automatic_rewrites += 1

        try:
            last_content = await generate_content(
                hospital,
                item.content_type,
                existing_titles,
                philosophy,
                approved_brief,
                remediation_findings=findings,
            )
        except ValueError as exc:
            last_generation_error = exc
            findings = [f"생성 안전검사 실패: {' '.join(str(exc).split())[:300]}"]
            if generation_index + 1 < AUTO_REMEDIATION_MAX_GENERATIONS:
                continue
            if last_content is not None and last_screening is None:
                # 앞선 회차가 만들어 둔(이미 결제된) 후보가 있다. 보완 재작성이 실패했다고
                # 그 후보까지 버리면 정상 글 한 편을 돈만 쓰고 폐기하는 셈이다.
                break
            raise
        last_generation_error = None

        # 이 글이 원래 답하기로 한 측정 질문을 실제로 다뤘는가.
        # (content_engine._validate_target_alignment가 채운다)
        alignment_findings = list(last_content.get("target_alignment_findings") or [])
        if (
            alignment_findings
            and not alignment_remediation_used
            and generation_index + 1 < AUTO_REMEDIATION_MAX_GENERATIONS
        ):
            alignment_remediation_used = True
            findings = alignment_findings
            continue

        last_ai_review = None
        last_screening = screen_content_against_philosophy(
            _screening_probe(last_content), philosophy
        )
        if last_screening.status != ESSENCE_STATUS_ALIGNED:
            findings = _review_findings(last_screening.summary)
            if not findings:
                break
            continue

        last_ai_review = await review_generated_content(
            hospital=hospital,
            philosophy=philosophy,
            content=last_content,
            content_brief=approved_brief,
        )
        if last_ai_review.status in {
            ContentAiReviewStatus.PASS,
            ContentAiReviewStatus.UNAVAILABLE,
        }:
            # UNAVAILABLE never grants approval: it merely leaves the candidate to
            # the deterministic generation and publication gates below.
            break
        # Style-only feedback is advisory and must not create a manual gate or spend
        # another writer call. Fact/safety uncertainty remains blocking by content,
        # independent of the reviewer's confidence number.
        if not last_ai_review.blocking_findings:
            break
        findings = list(last_ai_review.remediation_messages)
        if generation_index + 1 < AUTO_REMEDIATION_MAX_GENERATIONS:
            reviewer_driven_rewrites += 1

    if last_content is not None and last_screening is None:
        # 키워드 보완을 위해 재작성으로 넘어갔지만 그 재작성이 비용 가드·생성 실패로
        # 끝난 경우다. 남은 후보는 심사만 받지 않았을 뿐 이미 결제된 정상 후보이므로
        # 여기서 심사해 살린다(잔여 키워드 지적은 아래 summary에 그대로 기록된다).
        last_screening = screen_content_against_philosophy(
            _screening_probe(last_content), philosophy
        )
        last_generation_error = None

    if last_content is None or last_screening is None:
        if last_generation_error is not None:
            raise last_generation_error
        raise RuntimeError("automatic content review produced no candidate")

    if last_ai_review and (
        last_ai_review.blocking_findings
        or last_ai_review.status == ContentAiReviewStatus.UNAVAILABLE
    ):
        summary = dict(last_screening.summary or {})
        summary.update(
            {
                "blocking": True,
                "findings": (
                    list(last_ai_review.remediation_messages)
                    if last_ai_review.blocking_findings
                    else ["독립 AI 검수를 완료하지 못해 자동 재검수가 필요합니다."]
                ),
            }
        )
        last_screening = type(last_screening)(
            status=ESSENCE_STATUS_NEEDS_REVIEW,
            summary=summary,
        )

    summary = dict(last_screening.summary or {})
    # 보완 재작성 후에도 키워드가 주제 위치에 없으면 글은 살리고 기록만 남긴다.
    # Admin의 콘텐츠 상세가 essence_check_summary를 그대로 보여주므로 AE가 확인할 수 있다.
    residual_alignment = list(last_content.get("target_alignment_findings") or [])
    if residual_alignment:
        summary["target_alignment_findings"] = residual_alignment
    if automatic_rewrites > 0:
        summary["automatic_remediation_attempts"] = automatic_rewrites
    if reviewer_driven_rewrites > 0:
        summary["reviewer_driven_rewrites"] = reviewer_driven_rewrites
    if last_ai_review is not None:
        summary["ai_review"] = last_ai_review.payload()
    reviewed_screening = type(last_screening)(
        status=last_screening.status,
        summary=summary,
    )
    return last_content, reviewed_screening


class MonthlyBatchIncompleteError(RuntimeError):
    """Raised after durable per-hospital failures so Celery schedules a retry."""


SOV_REPEAT_WEEKLY = min(settings.SOV_REPEAT_COUNT_WEEKLY, 20)  # 주간 측정용
SOV_CHUNK_STOP_SECONDS = 1650
V0_REPEAT_COUNT = 5  # V0 첫 측정 쿼리당 반복 횟수
V0_CHUNK_STOP_SECONDS = 480
V0_CONTINUATION_MAX_RETRIES = 200
# V0 첫 측정에 쓰는 질문 개수.
# 5였을 때: 플랫폼당 25개 관측이라 1건 차이로 언급률이 4%p씩 튀었다(±8%p 수준).
# 원장에게 처음 보여주는 '진단서'의 오차로는 너무 크다. 타임아웃 수정과 luna 전환으로
# 측정 표본은 15 × 5회 × 2플랫폼 = 150개다. 각 슬롯은 답변과 판정을 durable
# checkpoint한 뒤 순차 실행하고, 짧은 청크마다 같은 lineage로 이어간다. 세마포어
# 용량은 동시 실행을 스스로 만들지 않으므로 전체를 한 Celery 시도에 맞춘다고
# 가정하지 않는다.
V0_QUERY_SAMPLE_COUNT = 15

# V0 첫 측정 실행에 붙는 라벨. 월간 리포트가 "서비스 시작 시점" 축을 그릴 때
# 그때 실제로 물어본 질문 세트를 이 라벨로 되찾는다.
V0_MEASUREMENT_RUN_LABEL = "V0 first measurement"

# V0와 월간의 질문 세트가 이 비율 이상 겹칠 때만 "시작 시점 대비" 한 줄을 쓴다.
# V0는 반복 프로토콜·측정 창이 달라 완전한 비교 대상이 아니다 — 질문마저 다르면
# 두 수치를 나란히 놓는 것 자체가 거짓말이 된다.
V0_BASELINE_MIN_OVERLAP = 0.8


def _normalized_query_text(value: object) -> str:
    return " ".join(str(value or "").split())


def v0_query_overlap_ratio(
    v0_query_texts: Iterable[object], tracking_query_texts: Iterable[object]
) -> float:
    """V0 질문 중 지금 추적 세트에도 있는 비율. 분모는 언제나 V0 쪽이다."""
    v0 = {text for value in v0_query_texts if (text := _normalized_query_text(value))}
    if not v0:
        return 0.0
    tracking = {
        text for value in tracking_query_texts if (text := _normalized_query_text(value))
    }
    return len(v0 & tracking) / len(v0)


def build_v0_baseline(
    *,
    v0_sov_pct: float | None,
    current_sov_pct: float | None,
    v0_query_texts: Iterable[object],
    tracking_query_texts: Iterable[object],
) -> DoctorV0Baseline | None:
    """"서비스 시작 시점(V0) 대비" 한 줄. 겹침이 부족하면 아무 말도 하지 않는다.

    V0를 월간과 **비교 가능하게 만들지 않는다** — 반복 횟수·측정 창·집계 방식이
    다르다. 여기서 만드는 것은 라벨이 붙은 참고선 하나이고, 각주가 그 한계를
    같은 페이지에서 밝힌다.
    """
    if v0_sov_pct is None or current_sov_pct is None:
        return None
    if (
        v0_query_overlap_ratio(v0_query_texts, tracking_query_texts)
        < V0_BASELINE_MIN_OVERLAP
    ):
        return None
    started = round(v0_sov_pct)
    current = round(current_sov_pct)
    return {
        "of_hundred": started,
        "current_of_hundred": current,
        "sentence": f"서비스 시작 시점(V0) 대비: {started}번 → {current}번",
    }


def _v0_query_snapshot(queries: Iterable[QueryMatrix]) -> list[dict[str, str]]:
    """Freeze the exact questions a V0 run will send before provider calls start."""
    return [
        {
            "query_id": str(query.id),
            "query_text": query.query_text,
            "query_intent": query.query_intent,
        }
        for query in queries
    ]


def _v0_judgment_context(hospital: Hospital) -> dict[str, Any]:
    """Freeze every hospital field that can change a saved answer's verdict."""
    return {
        "hospital_identity": str(hospital.id),
        "hospital_name": hospital.name,
        "region": str((hospital.region or [""])[0]),
        "competitors": [str(name) for name in (hospital.competitors or [])],
    }


def _parse_v0_judgment_context(
    value: object, *, hospital_id: uuid.UUID
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    identity = value.get("hospital_identity")
    hospital_name = value.get("hospital_name")
    region = value.get("region")
    competitors = value.get("competitors")
    if (
        identity != str(hospital_id)
        or not isinstance(hospital_name, str)
        or not hospital_name.strip()
        or not isinstance(region, str)
        or not isinstance(competitors, list)
        or any(not isinstance(name, str) for name in competitors)
    ):
        return None
    return {
        "hospital_identity": identity,
        "hospital_name": hospital_name,
        "region": region,
        "competitors": list(competitors),
    }


def _v0_platforms_from_run(run: MeasurementRun) -> list[str] | None:
    """Read the provider set frozen by `_start_measurement_run`."""
    config = run.config if isinstance(run.config, dict) else {}
    model_names = config.get("model_names")
    if not isinstance(model_names, dict):
        return None
    platforms = [name for name in ("chatgpt", "gemini") if name in model_names]
    if not platforms or set(model_names) != set(platforms):
        return None
    return platforms


def _v0_resume_judgment_context(
    run: MeasurementRun,
    hospital: Hospital,
    slots: Sequence[MeasurementObservationSlot],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Load the frozen context, safely upgrading pre-snapshot in-flight runs."""
    config = run.config if isinstance(run.config, dict) else {}
    frozen = _parse_v0_judgment_context(
        config.get("judgment_context"), hospital_id=hospital.id
    )
    if frozen is not None:
        return frozen

    # Rows created before judgment_context was introduced can be upgraded only
    # when every already-attempted judgment proves the current profile produces
    # its exact saved fingerprint. Otherwise mixing identities would corrupt one
    # MeasurementRun, so fail before another provider call.
    candidate = _v0_judgment_context(hospital)
    for slot in slots:
        if slot.judgment_input_fingerprint is None:
            continue
        expected = sov_engine.judgment_input_fingerprint(
            hospital_identity=candidate["hospital_identity"],
            hospital_name=candidate["hospital_name"],
            response_text=slot.raw_response or "",
            region=candidate["region"],
            competitors=candidate["competitors"],
            policy=dict(protocol),
        )
        if slot.judgment_input_fingerprint != expected:
            raise RuntimeError("resumable V0 judgment context changed")
    run.config = {**config, "judgment_context": candidate}
    return candidate


def _local_v0_query_texts(snapshot: object) -> dict[uuid.UUID, str] | None:
    """Parse an immutable V0 query snapshot; malformed lineage is unusable."""
    if not isinstance(snapshot, list) or not snapshot:
        return None
    local_queries: dict[uuid.UUID, str] = {}
    seen_ids: set[uuid.UUID] = set()
    for raw in snapshot:
        if not isinstance(raw, dict):
            return None
        try:
            query_id = uuid.UUID(str(raw.get("query_id")))
        except (TypeError, ValueError):
            return None
        query_text = raw.get("query_text")
        query_intent = raw.get("query_intent")
        if query_id in seen_ids or not isinstance(query_text, str) or not query_text.strip():
            return None
        if not isinstance(query_intent, str):
            return None
        seen_ids.add(query_id)
        if query_intent == sov_engine.QUERY_INTENT_LOCAL:
            local_queries[query_id] = query_text
    return local_queries or None


def _v0_queries_from_snapshot(
    db, snapshot: object, *, hospital_id: uuid.UUID
) -> list[QueryMatrix] | None:
    """Reload frozen provider inputs while verifying their live FK ownership."""
    if not isinstance(snapshot, list) or not snapshot:
        return None
    ordered_ids: list[uuid.UUID] = []
    expected: dict[uuid.UUID, tuple[str, str]] = {}
    for raw in snapshot:
        if not isinstance(raw, dict):
            return None
        try:
            query_id = uuid.UUID(str(raw.get("query_id")))
        except (TypeError, ValueError):
            return None
        query_text = raw.get("query_text")
        query_intent = raw.get("query_intent")
        if (
            query_id in expected
            or not isinstance(query_text, str)
            or not query_text.strip()
            or not isinstance(query_intent, str)
        ):
            return None
        ordered_ids.append(query_id)
        expected[query_id] = (query_text, query_intent)
    live_rows = {
        row.id: row
        for row in db.execute(
            select(QueryMatrix).where(QueryMatrix.id.in_(ordered_ids))
        ).scalars()
    }
    if set(live_rows) != set(ordered_ids) or any(
        row.hospital_id != hospital_id for row in live_rows.values()
    ):
        return None
    # Query text/intent may be edited after the first chunk. The immutable snapshot,
    # not the mutable tracking row, remains the provider input for this lineage.
    return [
        QueryMatrix(
            id=query_id,
            hospital_id=hospital_id,
            query_text=expected[query_id][0],
            query_intent=expected[query_id][1],
        )
        for query_id in ordered_ids
    ]


def _load_v0_baseline(
    db,
    hospital_id,
    *,
    current_sov_pct: float | None,
    tracking_query_texts: Iterable[object],
    current_platforms: tuple[str, ...],
    current_protocol: dict | None,
    current_cells: tuple[ManifestCellInput, ...],
) -> DoctorV0Baseline | None:
    """검증 가능한 동일 실행 조건의 V0만 참고선으로 읽는다."""
    v0_report = db.execute(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital_id,
            MonthlyReport.report_type == "V0",
        )
        .order_by(MonthlyReport.created_at)
        .limit(1)
    ).scalars().first()
    if v0_report is None:
        return None
    v0_summary = v0_report.sov_summary or {}
    v0_sov_pct = v0_summary.get("sov_pct")
    if not isinstance(v0_sov_pct, (int, float)):
        return None
    basis = v0_summary.get("baseline_basis")
    if not isinstance(basis, dict):
        # 구버전 V0는 어떤 MeasurementRun이 수치를 만들었는지 알 수 없다. 같은 병원의
        # 다른 V0 실행에서 질문만 가져와 붙이면 근거가 섞이므로 표시하지 않는다.
        return None
    try:
        measurement_run_id = uuid.UUID(str(basis.get("measurement_run_id")))
    except (TypeError, ValueError):
        return None
    frozen_query_texts = _local_v0_query_texts(basis.get("query_snapshot"))
    if frozen_query_texts is None:
        return None
    v0_platforms = tuple(
        value for value in basis.get("platforms", ()) if isinstance(value, str)
    )
    v0_protocol = basis.get("measurement_protocol")
    if set(v0_platforms) != set(current_platforms) or not sov_engine.same_execution_policy(
        v0_protocol if isinstance(v0_protocol, dict) else None,
        current_protocol,
        platforms=current_platforms,
    ):
        return None
    rows = db.execute(
        select(
            SovRecord.query_id,
            SovRecord.ai_platform,
            SovRecord.answer_model,
            SovRecord.measurement_status,
            SovRecord.mention_verdict,
            SovRecord.is_mentioned,
        )
        .where(
            SovRecord.hospital_id == hospital_id,
            SovRecord.measurement_run_id == measurement_run_id,
            SovRecord.measurement_status == "SUCCESS",
            SovRecord.is_mentioned.is_not(None),
            or_(
                SovRecord.mention_verdict.is_(None),
                SovRecord.mention_verdict != sov_engine.VERDICT_AMBIGUOUS,
            ),
        )
    ).all()
    # SQLAlchemy enforces this in production; the shared predicate is repeated here so
    # alternate/fake result sources cannot make the baseline denominator diverge.
    local_rows = [
        row
        for row in rows
        if row.query_id in frozen_query_texts and sov_engine.record_is_confirmed(row)
    ]
    if not local_rows or any(row.answer_model is None for row in local_rows):
        return None
    current_shape_lists: dict[tuple[str, str], list[str]] = {}
    for cell in current_cells:
        if cell.query_intent != "LOCAL" or not cell.successful_attempts:
            continue
        observed_models = tuple(
            attempt.answer_model for attempt in cell.successful_attempts
        )
        if any(model is None for model in observed_models):
            return None
        key = (_normalized_query_text(cell.query_text), cell.platform)
        current_shape_lists.setdefault(key, []).extend(
            model for model in observed_models if model is not None
        )
    current_shapes = {
        key: tuple(sorted(models)) for key, models in current_shape_lists.items()
    }
    v0_shape_lists: dict[tuple[str, str], list[str]] = {}
    for row in local_rows:
        key = (_normalized_query_text(frozen_query_texts[row.query_id]), row.ai_platform)
        v0_shape_lists.setdefault(key, []).append(row.answer_model)
    v0_shapes = {key: tuple(sorted(models)) for key, models in v0_shape_lists.items()}
    if v0_shapes != current_shapes:
        return None
    v0_query_texts = list(frozen_query_texts.values())
    normalized_v0 = {
        text for value in v0_query_texts if (text := _normalized_query_text(value))
    }
    normalized_tracking = {
        text for value in tracking_query_texts if (text := _normalized_query_text(value))
    }
    if normalized_v0 != normalized_tracking:
        return None
    return build_v0_baseline(
        v0_sov_pct=float(v0_sov_pct),
        current_sov_pct=current_sov_pct,
        v0_query_texts=v0_query_texts,
        tracking_query_texts=tracking_query_texts,
    )


def sov_budget_units(*, query_count: int, platform_count: int, repeat_count: int) -> int:
    """비용 가드에 예약할 SoV 공급자 호출 수.

    run_single_query는 (질의 × 플랫폼) 하나당 repeat_count번 **실제 공급자 호출**을 낸다.
    예약할 때 반복 횟수를 빼면 가드가 실제 지출의 1/repeat_count만 세고, 상한이 사실상
    repeat_count배로 열린다. 예약 단위와 실제 호출 단위는 반드시 같아야 한다.

    이 함수가 존재하는 이유는 곱셈이 어려워서가 아니라, 그 불변식에 이름을 붙여
    테스트로 고정하기 위해서다. 과거 V0·주간 경로 모두 반복 횟수를 빠뜨렸다.
    """
    return query_count * platform_count * repeat_count


def v0_sample_query_stmt(hospital_id):
    """V0 표본 질의를 **결정론적으로** 고르는 SELECT.

    tiebreaker가 id면 결정론적이지 않다: 매트릭스는 한 트랜잭션에서 삽입되고
    created_at의 server_default now()는 트랜잭션 시각이라 모든 행이 동일해진다.
    그러면 순서를 랜덤 UUID인 id가 정하게 되어 같은 프로파일이라도 병원마다,
    재시도마다 질의 세트가 달라진다. query_text를 tiebreaker로 써서 삽입 순서와
    무관하게 고정한다.
    """
    return (
        select(QueryMatrix)
        .where(
            QueryMatrix.hospital_id == hospital_id,
            # INFO 질문은 언급률 분모에서 빠지므로 진단 표본으로 쓰면 호출만 쓰고
            # 헤드라인에는 기여하지 않는다.
            QueryMatrix.query_intent.in_(tuple(MENTION_RATE_INTENTS)),
            # 주간·월간 경로와 같은 필터. 이게 없으면 폐기한 질의가 V0 재생성에서
            # 되살아나, 비활성화가 한 경로에서만 지켜지는 상태가 된다.
            QueryMatrix.is_active,
        )
        .order_by(QueryMatrix.created_at, QueryMatrix.query_text)
        .limit(V0_QUERY_SAMPLE_COUNT)
    )


# 주간 측정에서 HIGH 우선순위 쿼리 spec 상한 — target 자동 시드로 매트릭스가 폭증해도
# 매주 전량 측정되며 API 비용이 무한정 늘지 않도록 태스크 측에서 잘라낸다.
SOV_HIGH_PRIORITY_CAP = settings.SOV_HIGH_PRIORITY_CAP
SOV_TOTAL_SPEC_CAP = settings.SOV_TOTAL_SPEC_CAP

_tls = threading.local()


def _run_async(coro):
    """Run an async coroutine safely in a sync Celery task.

    Reuses a single event loop per thread to avoid connection pool corruption
    in async clients (OpenAI, httpx) that are bound to a specific loop.
    """
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop.run_until_complete(coro)


def _auto_publish_block_alert_key(
    content_id: uuid.UUID, scheduled_date: str, code: str, reason: str
) -> str:
    """동일 원인은 묶되, 수정 후 달라진 차단 원인은 다시 알린다."""
    reason_fingerprint = hashlib.sha256(reason.encode()).hexdigest()[:12]
    return f"auto_publish_blocked:{content_id}:{scheduled_date}:{code}:{reason_fingerprint}"


def _record_locked_generation_items(
    recorder: GenerationBatchRecorder,
    items: list[ContentItem],
) -> None:
    """Persist due work hidden behind another live ContentItem lease."""
    code = "GENERATION_LEASE_ACTIVE"
    message = "이전 생성 작업의 lease가 아직 유효합니다. 만료 후 다시 시도해 주세요."
    for item in items:
        recorder.record(
            item.id,
            GenerationItemState.FAILED,
            safe_error_code=code,
            safe_error_message=message,
        )
        failed_run = recorder.item_run(
            item.id,
            item.hospital_id,
            "REGENERATE_CONTENT",
            OperationRunState.FAILED,
            safe_error_code=code,
            safe_error_message=message,
            attempt_kind="lease-active",
        )
        _run_async(
            open_generation_incident(
                item_id=item.id,
                hospital_id=item.hospital_id,
                hospital_name=item.hospital.name,
                run_id=failed_run.id,
                code=code,
                message=message,
                notify=generation_notify_requested(code),
            )
        )


def _reset_v0_analyzing_status(hospital_id: str, prior_status: str | None) -> None:
    """V0 실패 시 ANALYZING 상태를 이전 상태로 되돌린다 (P2-15).

    되돌리지 않으면 재시도/수동 재트리거가 in-progress 가드에 걸려 병원이 영원히
    ANALYZING에 갇힌다.
    """
    if not prior_status:
        return
    try:
        with SyncSessionLocal() as db:
            # 사이트 빌드가 동시에 ACTIVE로 전진할 수 있다. 잠금 없는 ANALYZING
            # 스냅샷을 기준으로 복원하면 대기하던 UPDATE가 새 ACTIVE/PAUSED를 덮는다.
            hospital = db.get(
                Hospital,
                uuid.UUID(hospital_id),
                populate_existing=True,
                with_for_update=True,
            )
            if hospital and hospital.status == HospitalStatus.ANALYZING:
                hospital.status = HospitalStatus(prior_status)
                db.commit()
    except Exception:
        logger.exception("Failed to reset ANALYZING status for hospital %s", hospital_id)


def _v0_claim_is_alive(db, hospital_id: uuid.UUID) -> bool:
    """ANALYZING 클레임이 아직 살아 있는 실행의 것인가. 정의는 v0_claim 모듈."""
    return v0_claim_is_alive_sync(db, hospital_id)


class V0MeasurementUnavailable(RuntimeError):
    def __init__(self, summary: dict[str, Any]):
        self.summary = summary
        super().__init__(
            f"V0 리포트를 만들 수 있는 성공 측정 결과가 없습니다 (실패 {summary.get('failed_count', 0)}건)"
        )


class V0MeasurementPolicyDrift(RuntimeError):
    """A continuation cannot mix provider or judge policies in one run."""

    summary = {
        "safe_error_code": "V0_MEASUREMENT_POLICY_DRIFT",
        "safe_error_message": "초기 진단 도중 측정 기준이 변경되어 안전하게 중단했습니다.",
        "next_action": "기존 측정 근거를 보존한 상태에서 운영 배포 기준을 확인해 주세요.",
    }


class V0CostDeferred(RuntimeError):
    """The measurement remains resumable and must not become an unavailable result."""


class V0MeasurementResumable(RuntimeError):
    """At least one bounded slot stage has attempts remaining."""


def _v0_chunk_deadline_reached(started_at: float) -> bool:
    """Leave task-limit headroom for the last already-started provider slot."""
    return monotonic() - started_at >= V0_CHUNK_STOP_SECONDS


def _require_v0_execution_policy(
    protocol: Mapping[str, Any], platforms: Sequence[str]
) -> None:
    if not sov_engine.same_execution_policy(
        dict(protocol),
        sov_engine.measurement_protocol(),
        platforms=tuple(platforms),
    ):
        raise V0MeasurementPolicyDrift("resumable V0 execution policy changed")


def _checkpoint_v0_progress(
    run: MeasurementRun, slots: Sequence[MeasurementObservationSlot]
) -> None:
    """Persist partial progress before handing the lineage to the next chunk."""
    summary = summarize_observation_slots(slots, deadline_reached=False)
    run.query_count = summary.planned_slots
    run.success_count = summary.confirmed_slots
    run.failure_count = summary.answer_failed_slots + summary.judgment_failed_slots
    run.error_summary = {
        **summary.to_payload(),
        "safe_error_code": "V0_MEASUREMENT_IN_PROGRESS",
    }


def _v0_retry_kwargs(task, failure_retry_count: int) -> dict[str, Any]:
    """Keep workflow failure budget separate from normal chunk count."""
    kwargs = dict(getattr(task.request, "kwargs", None) or {})
    kwargs["failure_retry_count"] = failure_retry_count
    return kwargs


def _v0_workflow_failure_count(run: MeasurementRun | None) -> int:
    config = run.config if run is not None and isinstance(run.config, dict) else {}
    if "workflow_failure_count" not in config:
        return 0
    value = config["workflow_failure_count"]
    if type(value) is int and 0 <= value <= V0_CONTINUATION_MAX_RETRIES:
        return value
    # Corrupt durable budget must never reopen retries indefinitely.
    return V0_CONTINUATION_MAX_RETRIES


def _persist_v0_workflow_failure_count(
    measurement_run_id: uuid.UUID | None, failure_count: int
) -> None:
    """Keep generic failure budget across a hard-kill OperationRun redispatch."""
    if measurement_run_id is None:
        return
    with SyncSessionLocal() as db:
        run = db.get(MeasurementRun, measurement_run_id, with_for_update=True)
        if run is None:
            return
        current = _v0_workflow_failure_count(run)
        if failure_count <= current:
            return
        config = run.config if isinstance(run.config, dict) else {}
        run.config = {**config, "workflow_failure_count": failure_count}
        db.commit()


def _seconds_until_next_kst_cost_window(now: datetime | None = None) -> int:
    observed = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul"))
    next_day = datetime.combine(
        observed.date() + timedelta(days=1),
        time(hour=0, minute=5),
        tzinfo=ZoneInfo("Asia/Seoul"),
    )
    return max(60, int((next_day - observed).total_seconds()))


def _classify_v0_failure(
    failure_reasons: Counter[str],
    platform_counts: dict[str, Counter[str]],
    *,
    planned_counts: dict[str, int] | None = None,
    blocked_platforms: dict[str, str] | None = None,
) -> dict[str, Any]:
    reason_text = " ".join(failure_reasons).lower()
    success_count = sum(counts["SUCCESS"] for counts in platform_counts.values())
    if success_count > 0 and failure_reasons:
        code = "V0_PARTIAL_PROVIDER_DEGRADED"
        message = "일부 AI 측정 호출은 실패했지만 성공 데이터로 초기 진단을 완료했습니다."
        next_action = (
            "초기 진단을 다시 실행하지 마세요. 운영센터에서 실패한 플랫폼의 상태만 확인하고 "
            "다음 정기 측정에서 회복 여부를 점검하세요."
        )
    elif any(token in reason_text for token in ("credit_balance_exhausted", "quota")):
        code = "V0_PROVIDER_QUOTA_EXHAUSTED"
        message = "AI 측정 공급자의 크레딧 또는 호출 쿼터가 소진되었습니다."
        next_action = "OpenAI 크레딧과 Gemini 쿼터를 복구한 뒤 초기 진단을 한 번만 재실행하세요."
    elif any(token in reason_text for token in ("http_429", "ratelimit")):
        code = "V0_PROVIDER_RATE_LIMITED"
        message = "AI 측정 공급자가 일시적으로 호출 속도를 제한했습니다."
        next_action = "잠시 후 초기 진단을 한 번만 재실행하세요."
    elif any(
        token in reason_text
        for token in (
            "http_400",
            "http_401",
            "http_403",
            "http_404",
            "authentication",
            "permission",
            "notfound",
            "badrequest",
        )
    ):
        code = "V0_PROVIDER_AUTH_OR_MODEL"
        message = "AI 측정 공급자의 인증 또는 모델 설정을 확인해야 합니다."
        next_action = "배포 환경의 공급자 API 키·모델명을 확인한 뒤 초기 진단을 재실행하세요."
    elif failure_reasons and all(
        reason.endswith("mention_parse_failed") for reason in failure_reasons
    ):
        code = "V0_JUDGE_FAILED"
        message = "AI 답변은 받았지만 공통 언급 판정 단계에서 처리하지 못했습니다."
        next_action = "공통 판정 모델 설정과 파싱 로그를 확인한 뒤 초기 진단을 재실행하세요."
    else:
        code = "V0_PROVIDER_UNAVAILABLE"
        message = "외부 AI 측정 서비스가 응답하지 않거나 일시적으로 제한되었습니다."
        next_action = (
            "공급자 상태와 호출 제한을 확인하고 장애가 해소된 뒤 초기 진단을 재실행하세요."
        )

    platforms: dict[str, dict[str, Any]] = {}
    for platform, counts in sorted(platform_counts.items()):
        prefix = f"{platform}:"
        attempted_count = counts["SUCCESS"] + counts["FAILED"]
        planned_count = (planned_counts or {}).get(platform, attempted_count)
        platforms[platform] = {
            "success_count": counts["SUCCESS"],
            "failure_count": counts["FAILED"],
            "attempted_count": attempted_count,
            "planned_count": planned_count,
            "skipped_count": max(0, planned_count - attempted_count),
            "blocked_reason": (blocked_platforms or {}).get(platform),
            "failure_reasons": {
                key[len(prefix) :]: count
                for key, count in sorted(failure_reasons.items())
                if key.startswith(prefix)
            },
        }
    return {
        "safe_error_code": code,
        "safe_error_message": message,
        "next_action": next_action,
        "failed_count": sum(counts["FAILED"] for counts in platform_counts.values()),
        "platforms": platforms,
    }


def _provider_batch_outage_reason(results: Sequence[Mapping[str, Any]]) -> str | None:
    """반복 측정 전체가 공급자 호출 단계에서 실패하면 남은 질의를 중단한다.

    한 질문에 대해 반복 5회가 모두 공급자 자체 재시도까지 소진했다면 같은 V0 실행에서
    나머지 14개 질문을 보내도 회복 가능성이 낮다. 파싱 실패는 답변을 받은 상태이므로
    여기서 회로를 열지 않는다.
    """
    reasons = [str(result.get("failure_reason") or "") for result in results]
    if not reasons or not all(reason.startswith("provider_query_failed:") for reason in reasons):
        return None
    return Counter(reasons).most_common(1)[0][0]


def _ensure_v0_has_successful_measurements(
    success_count: int,
    failure_count: int,
    failure_summary: dict[str, Any] | None = None,
) -> None:
    if success_count <= 0:
        raise V0MeasurementUnavailable({**(failure_summary or {}), "failed_count": failure_count})


def _raise_if_monthly_report_failures(failures: list[tuple[str, Exception]]) -> None:
    if not failures:
        return
    names = ", ".join(name for name, _exc in failures[:5])
    suffix = "" if len(failures) <= 5 else f" 외 {len(failures) - 5}건"
    raise RuntimeError(f"월간 리포트 실패: {names}{suffix}")


def _mark_source_processing_error(
    source_id: uuid.UUID,
    error: Exception,
    *,
    claim_token: str | None = None,
    input_hash: str | None = None,
) -> bool:
    """Persist a terminal source-processing error without masking the original failure."""
    try:
        with SyncSessionLocal() as db:
            source = db.get(HospitalSourceAsset, source_id)
            if source and source.status != SourceStatus.EXCLUDED:
                acquire_hospital_advisory_lock_sync(db, source.hospital_id)
                db.refresh(source)
                if source.status == SourceStatus.EXCLUDED:
                    return False
                if claim_token is not None:
                    current_claim = processing_claim(source.source_metadata)
                    if (
                        not current_claim
                        or current_claim.get("token") != claim_token
                        or current_claim.get("input_hash") != input_hash
                    ):
                        return False
                source.status = SourceStatus.ERROR
                source.process_error = str(error)[:2000]
                source.source_metadata = without_processing_claim(source.source_metadata)
                db.commit()
                return True
    except Exception:
        logger.exception("Failed to persist source-processing error for %s", source_id)
    return False


def _release_source_processing_claim_for_recovery(
    source_id: uuid.UUID,
    *,
    claim_token: str,
    input_hash: str | None,
    reason: str,
) -> bool:
    with SyncSessionLocal() as db:
        source = db.get(HospitalSourceAsset, source_id)
        if source is None:
            return False
        acquire_hospital_advisory_lock_sync(db, source.hospital_id)
        db.refresh(source)
        current_claim = processing_claim(source.source_metadata)
        if (
            not current_claim
            or current_claim.get("token") != claim_token
            or current_claim.get("input_hash") != input_hash
        ):
            return False
        source.source_metadata = without_processing_claim(source.source_metadata)
        source.status = SourceStatus.PENDING
        source.process_error = reason[:2000]
        db.commit()
        return True


# ══════════════════════════════════════════════════════════════════
# 온보딩 근거 자료 비동기 처리
# ══════════════════════════════════════════════════════════════════
class _SourceProcessingCostBlocked(RuntimeError):
    pass


async def _metered_process_source_asset(
    source,
    *,
    reservation_id: str,
    operation_run_id: uuid.UUID | None,
):
    """Reserve once at the shared worker boundary, then meter actual provider calls."""

    reserved_units = max(1, len(source_processing_ranges(source.raw_text)) * 3) if llm_enabled() else 0
    decision = await cost_guard.reserve(
        category="content",
        count=reserved_units,
        reservation_id=reservation_id,
    )
    if not decision.allowed:
        raise _SourceProcessingCostBlocked(
            decision.reason or "Source processing cost blocked"
        )
    counter = None
    try:
        async with metered_llm_calls(
            source.hospital_id,
            workflow="SOURCE_EVIDENCE_EXTRACT",
            run_id=operation_run_id,
            item_id=source.id,
            attempt_id=reservation_id,
        ) as counter:
            return await asyncio.to_thread(process_source_asset, source)
    finally:
        await cost_guard.settle_reservation(
            decision.receipt,
            consumed_units=min(reserved_units, counter.count if counter is not None else 0),
        )


def _claim_source_processing(
    source_id: uuid.UUID,
    *,
    claim_token: str,
) -> tuple[HospitalSourceAsset | None, str, str | None]:
    """Claim and detach one immutable provider input under a short transaction lock."""

    with SyncSessionLocal() as db:
        source = db.get(HospitalSourceAsset, source_id)
        if source is None:
            return None, "NOT_FOUND", None
        acquire_hospital_advisory_lock_sync(db, source.hospital_id)
        db.refresh(source)
        if source.status == SourceStatus.EXCLUDED:
            return None, SourceStatus.EXCLUDED.value, None
        current_content_hash = compute_source_content_hash(
            source.title, source.url, source.raw_text, source.operator_note
        )
        current_input_hash = processing_input_hash(source, current_content_hash)
        extraction_input_hash = (source.source_metadata or {}).get("extraction_input_hash")
        if (
            source.status == SourceStatus.PROCESSED
            and source.content_hash == current_content_hash
            and extraction_input_hash == current_input_hash
        ):
            return None, SourceStatus.PROCESSED.value, current_input_hash
        if not source.raw_text or not source.raw_text.strip():
            raise ValueError("자료 본문이 없는 URL 전용 자료는 처리할 수 없습니다.")
        existing_claim = processing_claim(source.source_metadata)
        if existing_claim and existing_claim.get("input_hash") == current_input_hash:
            claimed_at = None
            try:
                claimed_at = datetime.fromisoformat(str(existing_claim.get("claimed_at")))
            except (TypeError, ValueError):
                pass
            claim_is_live = bool(
                claimed_at
                and datetime.now(timezone.utc) - claimed_at.astimezone(timezone.utc)
                < timedelta(minutes=10)
            )
            if claim_is_live:
                return None, "PROCESSING", current_input_hash
        source.source_metadata = with_processing_claim(
            source.source_metadata,
            token=claim_token,
            input_hash=current_input_hash,
            claimed_at=datetime.now(timezone.utc),
        )
        source.status = SourceStatus.PENDING
        source.process_error = None
        db.commit()
        db.expunge(source)
        return source, "CLAIMED", current_input_hash


def _write_source_processing_result(
    source_id: uuid.UUID,
    *,
    claim_token: str,
    input_hash: str,
    payloads: list,
) -> tuple[str, int, uuid.UUID | None, str | None, str | None, bool]:
    """Conditionally write results only if the claimed source input is still current."""

    with SyncSessionLocal() as db:
        source = db.get(HospitalSourceAsset, source_id)
        if source is None:
            return "NOT_FOUND", 0, None, None, None, False
        acquire_hospital_advisory_lock_sync(db, source.hospital_id)
        db.refresh(source)
        current_claim = processing_claim(source.source_metadata)
        current_content_hash = compute_source_content_hash(
            source.title, source.url, source.raw_text, source.operator_note
        )
        if (
            source.status == SourceStatus.EXCLUDED
            or not current_claim
            or current_claim.get("token") != claim_token
            or current_claim.get("input_hash") != input_hash
            or processing_input_hash(source, current_content_hash) != input_hash
        ):
            return "SUPERSEDED", 0, source.hospital_id, None, None, False
        for payload in payloads:
            if not validate_source_excerpt(source, payload.source_excerpt):
                raise ValueError(
                    f"source_excerpt가 원문에 존재하지 않습니다: {payload.source_excerpt[:80]}"
                )
        db.execute(
            delete(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        notes = [
            HospitalSourceEvidenceNote(
                hospital_id=source.hospital_id,
                source_asset_id=source.id,
                note_type=payload.note_type,
                claim=payload.claim,
                source_excerpt=payload.source_excerpt,
                excerpt_start=payload.excerpt_start,
                excerpt_end=payload.excerpt_end,
                confidence=payload.confidence,
                note_metadata=payload.note_metadata,
            )
            for payload in payloads
        ]
        db.add_all(notes)
        metadata = without_processing_claim(source.source_metadata)
        metadata["extraction_coverage"] = source_processing_coverage(source.raw_text)
        metadata["extraction_input_hash"] = input_hash
        source.source_metadata = metadata
        source.status = SourceStatus.PROCESSED
        source.process_error = None
        source.processed_at = datetime.now(timezone.utc)
        source.content_hash = current_content_hash
        hospital = db.get(Hospital, source.hospital_id)
        hospital_slug = hospital.slug if hospital else None
        hospital_name = hospital.name if hospital else None
        should_revalidate = bool(
            hospital
            and hospital.status == HospitalStatus.ACTIVE
            and hospital.site_live
        )
        hospital_id = source.hospital_id
        db.commit()
        return (
            SourceStatus.PROCESSED.value,
            len(notes),
            hospital_id,
            hospital_slug,
            hospital_name,
            should_revalidate,
        )


def _dispatch_next_source_processing_run_item(run_id: uuid.UUID) -> bool:
    """Publish at most one item from the durable snapshot."""

    source_id: str | None = None
    dispatch_token: str | None = None
    with SyncSessionLocal() as db:
        run = db.scalar(select(OperationRun).where(OperationRun.id == run_id).with_for_update())
        if run is None or run.operation_type != SOURCE_PROCESSING_OPERATION:
            return False
        if run.state in {
            OperationRunState.SUCCEEDED,
            OperationRunState.PARTIAL,
            OperationRunState.FAILED,
            OperationRunState.CANCELLED,
        }:
            return False
        payload = dict(run.request_payload or {})
        if int(payload.get("in_flight") or 0) > 0:
            return False
        source_ids = [str(item) for item in payload.get("source_ids") or []]
        cursor = int(payload.get("cursor") or 0)
        if cursor >= len(source_ids):
            run.state = (
                OperationRunState.SUCCEEDED
                if int(run.failure_count or 0) == 0
                else OperationRunState.PARTIAL
            )
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return False
        source_id = source_ids[cursor]
        dispatch_token = str(uuid.uuid4())
        payload["cursor"] = cursor + 1
        payload["in_flight"] = 1
        payload["in_flight_source_id"] = source_id
        payload["in_flight_dispatch_token"] = dispatch_token
        payload.pop("next_retry_at", None)
        run.request_payload = payload
        run.state = OperationRunState.QUEUED
        run.queued_at = datetime.now(timezone.utc)
        run.safe_error_code = None
        run.safe_error_message = None
        db.commit()
    try:
        celery_app.send_task(
            "app.workers.tasks.process_source_asset_task",
            args=[source_id, str(run_id), dispatch_token],
            queue="default",
            headers=build_dispatch_headers(
                "app.workers.tasks.process_source_asset_task", source_id
            ),
        )
        return True
    except Exception as exc:
        with SyncSessionLocal() as db:
            run = db.scalar(
                select(OperationRun).where(OperationRun.id == run_id).with_for_update()
            )
            if run is not None:
                payload = dict(run.request_payload or {})
                source_ids = [str(item) for item in payload.get("source_ids") or []]
                cursor = int(payload.get("cursor") or 0)
                if cursor > 0 and run_dispatch_matches(
                    payload,
                    source_id=source_id,
                    dispatch_token=dispatch_token,
                ):
                    payload["cursor"] = cursor - 1
                    payload["in_flight"] = 0
                    payload.pop("in_flight_source_id", None)
                    payload.pop("in_flight_dispatch_token", None)
                    run.request_payload = payload
                run.state = OperationRunState.REQUESTED
                run.safe_error_code = "BROKER_UNAVAILABLE"
                run.safe_error_message = str(exc)[:500]
                db.commit()
        logger.exception("Failed to continue source-processing run %s", run_id)
        return False


def _complete_source_processing_run_item(
    run_id: uuid.UUID | None,
    source_id: uuid.UUID,
    outcome: str,
    *,
    dispatch_token: str | None,
) -> bool:
    if run_id is None:
        return False
    with SyncSessionLocal() as db:
        run = db.scalar(select(OperationRun).where(OperationRun.id == run_id).with_for_update())
        if run is None or run.operation_type != SOURCE_PROCESSING_OPERATION:
            return False
        payload = dict(run.request_payload or {})
        if not run_dispatch_matches(
            payload,
            source_id=source_id,
            dispatch_token=dispatch_token,
        ):
            return False
        results = dict(payload.get("item_results") or {})
        source_key = str(source_id)
        if source_key in results:
            return False
        results[source_key] = outcome
        payload["item_results"] = results
        payload["in_flight"] = 0
        payload.pop("in_flight_source_id", None)
        payload.pop("in_flight_dispatch_token", None)
        run.request_payload = payload
        if outcome == SourceStatus.PROCESSED.value:
            run.success_count = int(run.success_count or 0) + 1
        elif outcome in {"NOT_FOUND", SourceStatus.EXCLUDED.value, "SUPERSEDED"}:
            run.skipped_count = int(run.skipped_count or 0) + 1
        else:
            run.failure_count = int(run.failure_count or 0) + 1
        run.state = OperationRunState.RUNNING
        run.started_at = run.started_at or datetime.now(timezone.utc)
        source_ids = [str(item) for item in payload.get("source_ids") or []]
        if len(results) >= len(source_ids):
            run.state = (
                OperationRunState.SUCCEEDED
                if int(run.failure_count or 0) == 0
                else OperationRunState.PARTIAL
            )
            run.completed_at = datetime.now(timezone.utc)
        db.commit()
    _dispatch_next_source_processing_run_item(run_id)
    return True


def _defer_source_processing_run_item(
    run_id: uuid.UUID | None,
    source_id: uuid.UUID,
    *,
    dispatch_token: str | None,
    reason: str,
) -> bool:
    """Return a recovery-dependent item to the durable cursor without counting failure."""

    if run_id is None or dispatch_token is None:
        return False
    with SyncSessionLocal() as db:
        run = db.scalar(select(OperationRun).where(OperationRun.id == run_id).with_for_update())
        if run is None or run.operation_type != SOURCE_PROCESSING_OPERATION:
            return False
        payload = dict(run.request_payload or {})
        cursor = int(payload.get("cursor") or 0)
        source_ids = [str(item) for item in payload.get("source_ids") or []]
        if (
            cursor <= 0
            or source_ids[cursor - 1] != str(source_id)
            or not run_dispatch_matches(
                payload,
                source_id=source_id,
                dispatch_token=dispatch_token,
            )
        ):
            return False
        payload["cursor"] = cursor - 1
        payload["in_flight"] = 0
        payload.pop("in_flight_source_id", None)
        payload.pop("in_flight_dispatch_token", None)
        payload["next_retry_at"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=SOURCE_PROCESSING_RETRY_DELAY_SECONDS)
        ).isoformat()
        run.request_payload = payload
        run.state = OperationRunState.REQUESTED
        run.safe_error_code = reason
        run.safe_error_message = "환경이 회복되면 자료 처리를 자동으로 다시 시도합니다."
        db.commit()
        return True


@celery_app.task(
    name="app.workers.tasks.process_source_asset_task",
    bind=True,
    max_retries=2,
    soft_time_limit=300,
    time_limit=330,
)
def process_source_asset_task(
    self,
    source_id: str,
    operation_run_id: str | None = None,
    run_dispatch_token: str | None = None,
) -> dict[str, object]:
    """Extract evidence notes for one pending onboarding source.

    Admin의 일괄 처리 엔드포인트가 이 태스크 이름을 그대로 큐잉한다. 구현을 워커
    모듈에 두어야 Celery가 기동 시 등록하고, 중복 전달에도 안전하게 복귀한다.
    """
    source_uuid = uuid.UUID(source_id)
    run_uuid = uuid.UUID(operation_run_id) if operation_run_id else None
    claim_token = (
        f"{str(self.request.id or uuid.uuid4())}:retry:{int(self.request.retries or 0)}"
    )
    input_hash: str | None = None

    try:
        source, claim_status, input_hash = _claim_source_processing(
            source_uuid, claim_token=claim_token
        )
        if source is None:
            if claim_status == "NOT_FOUND":
                logger.warning("Source asset not found; skipping: %s", source_uuid)
            if claim_status != "PROCESSING":
                _complete_source_processing_run_item(
                    run_uuid,
                    source_uuid,
                    claim_status,
                    dispatch_token=run_dispatch_token,
                )
            return {"source_id": source_id, "status": claim_status}

        reservation_id = source_processing_reservation_id(
            source_id=source_id,
            input_hash=input_hash,
            dispatch_token=run_dispatch_token,
            task_id=claim_token,
            retry=int(self.request.retries or 0),
        )
        payloads = _run_async(
            _metered_process_source_asset(
                source,
                reservation_id=reservation_id,
                operation_run_id=run_uuid,
            )
        )
        payloads = [
            payload
            for payload in payloads
            if evidence_text_is_acceptable(payload.claim, payload.source_excerpt)
        ]
        (
            outcome,
            note_count,
            hospital_id_for_review,
            hospital_slug,
            hospital_name,
            should_revalidate,
        ) = _write_source_processing_result(
            source_uuid,
            claim_token=claim_token,
            input_hash=input_hash,
            payloads=payloads,
        )

        if should_revalidate and hospital_slug:
            _run_async(
                trigger_hospital_site_revalidate_safe(
                    hospital_slug,
                    hospital_name=hospital_name,
                )
            )
        if outcome == SourceStatus.PROCESSED.value and hospital_name and hospital_id_for_review:
            try:
                auto_review_essence_snapshot.apply_async(
                    args=[str(hospital_id_for_review)],
                    queue="content",
                    headers=build_dispatch_headers(
                        "auto-review-essence-snapshot", str(hospital_id_for_review)
                    ),
                )
            except Exception:
                # Source truth is already durable. The periodic reconciler is the
                # recovery path when this immediate publish is lost.
                logger.exception(
                    "Failed to enqueue Essence snapshot review for hospital %s",
                    hospital_id_for_review,
                )
        _complete_source_processing_run_item(
            run_uuid,
            source_uuid,
            outcome,
            dispatch_token=run_dispatch_token,
        )
        return {
            "source_id": source_id,
            "status": outcome,
            "evidence_note_count": note_count,
        }
    except _SourceProcessingCostBlocked as exc:
        # Budget/provider recovery uses the same durable PENDING input.  Mark this
        # run item terminal so a 120-item snapshot keeps draining; a later run can
        # retry it after the guard resets without losing the source.
        _release_source_processing_claim_for_recovery(
            source_uuid,
            claim_token=claim_token,
            input_hash=input_hash,
            reason=str(exc),
        )
        _defer_source_processing_run_item(
            run_uuid,
            source_uuid,
            dispatch_token=run_dispatch_token,
            reason="COST_BLOCKED",
        )
        return {"source_id": source_id, "status": "COST_BLOCKED", "error": str(exc)}
    except ValueError as exc:
        _mark_source_processing_error(
            source_uuid,
            exc,
            claim_token=claim_token,
            input_hash=input_hash,
        )
        _complete_source_processing_run_item(
            run_uuid,
            source_uuid,
            SourceStatus.ERROR.value,
            dispatch_token=run_dispatch_token,
        )
        logger.warning("Source processing rejected for %s: %s", source_uuid, exc)
        return {"source_id": source_id, "status": SourceStatus.ERROR.value, "error": str(exc)}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            _release_source_processing_claim_for_recovery(
                source_uuid,
                claim_token=claim_token,
                input_hash=input_hash,
                reason=str(exc),
            )
            raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))
        _release_source_processing_claim_for_recovery(
            source_uuid,
            claim_token=claim_token,
            input_hash=input_hash,
            reason=str(exc),
        )
        _defer_source_processing_run_item(
            run_uuid,
            source_uuid,
            dispatch_token=run_dispatch_token,
            reason="SOURCE_PROVIDER_UNAVAILABLE",
        )
        logger.exception("Source processing deferred after provider retries: %s", source_uuid)
        return {
            "source_id": source_id,
            "status": "DEFERRED",
            "error": str(exc),
        }


class _EssenceReviewCostBlocked(RuntimeError):
    pass


def _cost_guarded_essence_synthesis(
    hospital: Hospital,
    sources: list[HospitalSourceAsset],
    notes: list[HospitalSourceEvidenceNote],
    operator_note: str | None = None,
) -> dict[str, Any]:
    decision = _run_async(cost_guard.check_and_increment("content"))
    if not decision.allowed:
        raise _EssenceReviewCostBlocked(decision.reason or "Essence synthesis cost blocked")

    async def _call():
        async with metered_llm_calls(hospital.id):
            return await asyncio.to_thread(
                synthesize_philosophy,
                hospital,
                sources,
                notes,
                operator_note=operator_note,
            )

    return _run_async(_call())


def _cost_guarded_essence_review(
    hospital: Hospital,
    previous: HospitalContentPhilosophy | None,
    candidate: dict[str, Any],
    notes: list[HospitalSourceEvidenceNote],
) -> EssenceAiReview:
    decision = _run_async(cost_guard.check_and_increment("content"))
    if not decision.allowed:
        raise _EssenceReviewCostBlocked(decision.reason or "Essence review cost blocked")

    async def _call():
        async with metered_llm_calls(hospital.id):
            return await asyncio.to_thread(
                review_essence_candidate,
                hospital,
                previous,
                candidate,
                notes,
            )

    return _run_async(_call())


@celery_app.task(
    name="app.workers.tasks.auto_review_essence_snapshot",
    bind=True,
    max_retries=2,
    # Two complete remediation passes can each spend up to ~332 seconds on
    # synthesis + primary review + adjudication. Leave enough margin to persist
    # the final approval/escalation and recovery marker after provider timeouts.
    soft_time_limit=780,
    time_limit=840,
)
def auto_review_essence_snapshot(self, hospital_id: str) -> dict[str, object]:
    """Build and independently review an initial or changed Essence snapshot."""

    require_dispatch(self, "auto-review-essence-snapshot", hospital_id)
    hospital_uuid = uuid.UUID(hospital_id)
    hospital_name = "병원"
    hospital_slug: str | None = None
    hospital_status: HospitalStatus | None = None
    refresh_claim_token = (
        f"{str(self.request.id or uuid.uuid4())}:retry:{int(self.request.retries or 0)}"
    )
    try:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, hospital_uuid)
            if hospital is not None:
                hospital_name = hospital.name
                hospital_slug = hospital.slug
                hospital_status = getattr(hospital, "status", None)
            result = refresh_essence_snapshot(
                db,
                hospital_uuid,
                synthesizer=_cost_guarded_essence_synthesis,
                reviewer=_cost_guarded_essence_review,
                claim_token=refresh_claim_token,
            )
    except _EssenceReviewCostBlocked as exc:
        with SyncSessionLocal() as db:
            release_essence_refresh_claim(
                db,
                hospital_id=hospital_uuid,
                claim_token=refresh_claim_token,
                error_code="COST_BLOCKED",
                error_message=str(exc),
            )
        _run_async(
            open_ops_incident(
                pipeline="essence_auto_review",
                object_type="hospital",
                object_id=hospital_id,
                incident_type="ESSENCE_AUTO_REVIEW_COST_BLOCKED",
                safe_error_code="COST_BLOCKED",
                problem="AI 운영 기준 자동 검수가 비용 가드로 보류되었습니다.",
                customer_impact="새 자료의 자동 검수가 끝날 때까지 콘텐츠 생성과 발행이 일시 중지됩니다.",
                next_action="비용 가드가 해제되면 정기 복구가 자동으로 다시 시도합니다.",
                source_type="ESSENCE_AUTO_REVIEW",
                hospital_name=hospital_name,
                hospital_id=hospital_uuid,
                admin_path=f"/hospitals/{hospital_id}/essence",
                fingerprint=IncidentFingerprint.COST_BLOCKED,
                actor=AUTO_ESSENCE_ACTOR,
            )
        )
        return {"status": "COST_BLOCKED", "hospital_id": hospital_id, "reason": str(exc)}
    except Exception as exc:
        with SyncSessionLocal() as db:
            release_essence_refresh_claim(
                db,
                hospital_id=hospital_uuid,
                claim_token=refresh_claim_token,
                error_code="ESSENCE_PROVIDER_UNAVAILABLE",
                error_message=str(exc),
            )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
        _run_async(
            open_ops_incident(
                pipeline="essence_auto_review",
                object_type="hospital",
                object_id=hospital_id,
                incident_type="ESSENCE_AUTO_REVIEW_FAILED",
                safe_error_code="ESSENCE_AUTO_REVIEW_FAILED",
                problem="AI 운영 기준 자동 검수를 완료하지 못했습니다.",
                customer_impact="새 자료의 자동 검수가 끝날 때까지 콘텐츠 생성과 발행이 일시 중지됩니다.",
                next_action="운영센터에서 자동 생성된 초안과 근거 자료를 확인해 주세요.",
                source_type="ESSENCE_AUTO_REVIEW",
                hospital_name=hospital_name,
                hospital_id=hospital_uuid,
                admin_path=f"/hospitals/{hospital_id}/essence",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                actor=AUTO_ESSENCE_ACTOR,
            )
        )
        raise

    if result.status in {
        EssenceRefreshStatus.UP_TO_DATE,
        EssenceRefreshStatus.AUTO_APPROVED,
        EssenceRefreshStatus.ESCALATED,
    }:
        for fingerprint in (
            IncidentFingerprint.COST_BLOCKED,
            IncidentFingerprint.VALIDATION_FAILED,
        ):
            _run_async(
                recover_ops_incident(
                    pipeline="essence_auto_review",
                    object_type="hospital",
                    object_id=hospital_id,
                    fingerprint=fingerprint,
                    hospital_name=hospital_name,
                    actor=AUTO_ESSENCE_ACTOR,
                )
            )

    if result.requires_operator and result.previous_philosophy_id is None:
        snapshot = result.snapshot_hash or "unknown"
        _run_async(
            open_ops_incident(
                pipeline="essence_auto_review",
                object_type="essence_snapshot",
                object_id=f"{hospital_id}:{snapshot}",
                incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
                safe_error_code="ESSENCE_AUTO_REVIEW_ESCALATED",
                problem=(
                    result.findings[0]
                    if result.findings
                    else "AI 근거 검수가 자동 승인을 보류했습니다."
                ),
                customer_impact="승인 운영 기준이 없어 콘텐츠 생성과 발행이 중지되며 초안은 검토 대기 상태로 보관됩니다.",
                next_action="운영센터에서 새 운영 기준 초안의 근거와 충돌 항목만 확인해 주세요.",
                source_type="ESSENCE_AUTO_REVIEW",
                hospital_name=hospital_name,
                hospital_id=hospital_uuid,
                admin_path=f"/hospitals/{hospital_id}/essence",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                actor=AUTO_ESSENCE_ACTOR,
            )
        )
    elif result.status == EssenceRefreshStatus.ESCALATED and hospital_status == HospitalStatus.ACTIVE:
        # 자동 심사로 해결되지 않은 활성 병원의 예외만 스냅샷 해시 단위로 1건 연다.
        snapshot = result.snapshot_hash or "unknown"
        _run_async(
            open_ops_incident(
                pipeline="essence_auto_review",
                object_type="essence_snapshot",
                object_id=f"{hospital_id}:{snapshot}",
                incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
                safe_error_code="ESSENCE_AUTO_REVIEW_ESCALATED",
                problem=(
                    result.findings[0]
                    if result.findings
                    else "AI 근거 검수가 새 자료 반영을 보류했습니다."
                ),
                customer_impact="이 예외가 해결될 때까지 콘텐츠 생성과 발행이 일시 중지됩니다.",
                next_action="운영센터 Essence 페이지에서 보류된 예외를 확인해 주세요.",
                source_type="ESSENCE_AUTO_REVIEW",
                hospital_name=hospital_name,
                hospital_id=hospital_uuid,
                admin_path=f"/hospitals/{hospital_id}/essence",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                actor=AUTO_ESSENCE_ACTOR,
                severity=IncidentSeverity.MEDIUM,
            )
        )
    elif result.status in {
        EssenceRefreshStatus.ESCALATED,
        EssenceRefreshStatus.AUTO_APPROVED,
        EssenceRefreshStatus.UP_TO_DATE,
    }:
        _run_async(
            recover_ops_incidents_for_hospital(
                hospital_id=hospital_uuid,
                pipeline="essence_auto_review",
                incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
                hospital_name=hospital_name,
                actor=AUTO_ESSENCE_ACTOR,
                reason="current Essence snapshot is approved for generation",
                notify=False,
            )
        )
        if result.status == EssenceRefreshStatus.AUTO_APPROVED and result.should_revalidate_site:
            _run_async(
                trigger_hospital_site_revalidate_safe(
                    hospital_slug or "",
                    hospital_name=hospital_name,
                )
            )

    return {
        "status": result.status.value,
        "hospital_id": hospital_id,
        "snapshot_hash": result.snapshot_hash,
        "philosophy_id": str(result.philosophy_id) if result.philosophy_id else None,
        "findings": list(result.findings),
        "synthesis_attempts": result.synthesis_attempts,
    }


def _essence_reconcile_offset(
    total: int,
    now: datetime,
    *,
    batch_size: int = 200,
) -> int:
    """Rotate deterministic pages every beat interval so no hospital starves."""

    if total <= 0 or batch_size <= 0:
        return 0
    page_count = (total + batch_size - 1) // batch_size
    beat_slot = int(now.timestamp()) // (15 * 60)
    return (beat_slot % page_count) * batch_size


def _resume_source_processing_runs(now: datetime, *, limit: int = 200) -> int:
    """Recover broker loss and workers that died after persisting an in-flight cursor."""

    active_states = (
        OperationRunState.REQUESTED,
        OperationRunState.QUEUED,
        OperationRunState.RUNNING,
    )
    with SyncSessionLocal() as db:
        runs = list(
            db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
                    OperationRun.state.in_(active_states),
                )
                .order_by(OperationRun.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        resumable_ids: list[uuid.UUID] = []
        for run in runs:
            payload = dict(run.request_payload or {})
            retry_at = payload.get("next_retry_at")
            if retry_at:
                try:
                    if datetime.fromisoformat(str(retry_at)) > now:
                        continue
                except (TypeError, ValueError):
                    pass
            in_flight = int(payload.get("in_flight") or 0)
            stale = bool(
                in_flight
                and run.queued_at
                and now - run.queued_at.astimezone(timezone.utc) >= timedelta(minutes=10)
            )
            if stale:
                cursor = int(payload.get("cursor") or 0)
                if cursor > 0:
                    payload["cursor"] = cursor - 1
                payload["in_flight"] = 0
                payload.pop("in_flight_source_id", None)
                payload.pop("in_flight_dispatch_token", None)
                run.request_payload = payload
                run.state = OperationRunState.REQUESTED
                run.safe_error_code = "SOURCE_PROCESSING_LEASE_EXPIRED"
                run.safe_error_message = "자료 처리 실행이 중단되어 같은 입력으로 자동 재개합니다."
                in_flight = 0
            if not in_flight:
                resumable_ids.append(run.id)
        db.commit()
    return sum(_dispatch_next_source_processing_run_item(run_id) for run_id in resumable_ids)


def _create_runs_for_orphan_pending_sources(*, limit: int = 200) -> list[uuid.UUID]:
    """Adopt legacy/Naver PENDING rows that were queued without a durable run."""

    with SyncSessionLocal() as db:
        active_runs = list(
            db.execute(
                select(OperationRun).where(
                    OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
                    OperationRun.state.in_(
                        (
                            OperationRunState.REQUESTED,
                            OperationRunState.QUEUED,
                            OperationRunState.RUNNING,
                        )
                    ),
                )
            )
            .scalars()
            .all()
        )
        owned_ids = {
            str(source_id)
            for run in active_runs
            for source_id in (run.request_payload or {}).get("source_ids", [])
        }
        pending = list(
            db.execute(
                select(HospitalSourceAsset)
                .where(
                    HospitalSourceAsset.status == SourceStatus.PENDING,
                    # 본문 없는 행을 LIMIT 뒤 Python에서 걸러내면, 앞줄이 전부 URL 전용일 때
                    # 뒤의 처리 가능한 자료가 영원히 굶는다. 필수 판정은 SQL에서 한 번에 한다.
                    required_text_source_predicate(),
                )
                .order_by(HospitalSourceAsset.created_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        created_ids: list[uuid.UUID] = []
        for source in pending:
            if str(source.id) in owned_ids:
                continue
            # Multiple beat instances may overlap during rollout. Serialize the
            # active-run check and insert so random retry suffixes cannot create
            # two provider attempts for the same current source input.
            acquire_hospital_advisory_lock_sync(db, source.hospital_id)
            content_hash = compute_source_content_hash(
                source.title, source.url, source.raw_text, source.operator_note
            )
            identity = f"{source.id}:{processing_input_hash(source, content_hash)}"
            idempotency_key = source_run_key([identity])
            active_run = db.scalar(
                select(OperationRun.id).where(
                    OperationRun.hospital_id == source.hospital_id,
                    OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
                    OperationRun.idempotency_key.startswith(idempotency_key),
                    OperationRun.state.in_(
                        (
                            OperationRunState.REQUESTED,
                            OperationRunState.QUEUED,
                            OperationRunState.RUNNING,
                        )
                    ),
                )
            )
            if active_run is not None:
                continue
            prior_run = db.scalar(
                select(OperationRun.id).where(
                    OperationRun.hospital_id == source.hospital_id,
                    OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
                    OperationRun.idempotency_key.startswith(idempotency_key),
                )
            )
            attempt_key = (
                idempotency_key
                if prior_run is None
                else f"{idempotency_key}:retry:{uuid.uuid4()}"
            )
            run = OperationRun(
                hospital_id=source.hospital_id,
                operation_type=SOURCE_PROCESSING_OPERATION,
                state=OperationRunState.REQUESTED,
                idempotency_key=attempt_key,
                total_count=1,
                request_payload={
                    "source_ids": [str(source.id)],
                    "cursor": 0,
                    "in_flight": 0,
                    "batch_size": 1,
                    "item_results": {},
                },
            )
            db.add(run)
            db.flush()
            created_ids.append(run.id)
        db.commit()
        return created_ids


@celery_app.task(
    name="app.workers.tasks.reconcile_essence_snapshots",
    bind=True,
)
def reconcile_essence_snapshots(self) -> dict[str, int]:
    """Recover lost immediate dispatches for initial and changed snapshots."""

    require_dispatch(self, "reconcile-essence-snapshots")
    created_source_runs = _create_runs_for_orphan_pending_sources()
    resumed_source_runs = _resume_source_processing_runs(datetime.now(timezone.utc))
    with SyncSessionLocal() as db:
        total = int(db.scalar(select(func.count()).select_from(Hospital)) or 0)
        offset = _essence_reconcile_offset(total, datetime.now(timezone.utc))
        candidate_ids = list(
            db.execute(select(Hospital.id).order_by(Hospital.id).offset(offset).limit(200))
            .scalars()
            .all()
        )
        hospital_ids = [
            hospital_id for hospital_id in candidate_ids if essence_refresh_needed(db, hospital_id)
        ]
    queued = 0
    for hospital_uuid in hospital_ids:
        hospital_id = str(hospital_uuid)
        auto_review_essence_snapshot.apply_async(
            args=[hospital_id],
            queue="content",
            headers=build_dispatch_headers("auto-review-essence-snapshot", hospital_id),
        )
        queued += 1
    return {
        "queued": queued,
        "source_runs_created": len(created_source_runs),
        "source_runs_resumed": resumed_source_runs,
    }


# ══════════════════════════════════════════════════════════════════
# V0 리포트
# ══════════════════════════════════════════════════════════════════
def release_v0_session_lock(db, hospital_uuid: uuid.UUID) -> None:
    """세션 advisory 락을 반드시 푼 상태로 커넥션을 풀에 돌려준다.

    락 구간에서 SQLAlchemy 오류가 나면 세션이 pending-rollback 상태라 unlock 쿼리
    자체가 실패한다. 세션 락은 트랜잭션 롤백으로 풀리지 않으므로 커넥션이 풀에
    반환된 뒤에도 락이 살아남고, 같은 키를 쓰는 그 병원의 모든 쓰기(프로파일 저장,
    자료 업로드, V0 재시도)가 알림 없이 무한 대기한다. 깨끗한 세션에서도 무해한
    rollback을 먼저 돌리고, 그래도 해제를 확인하지 못하면 락을 들고 있을 수 있는
    커넥션을 풀에 돌려주지 않고 폐기한다.
    """
    hospital_id = str(hospital_uuid)
    try:
        db.rollback()
        if release_hospital_advisory_session_lock_sync(db, hospital_uuid) is False:
            logger.error(
                "V0 session lock for %s was not held by this connection; discarding it",
                hospital_id,
            )
            db.invalidate()
        return
    except Exception:
        logger.exception("failed to release V0 session lock for %s", hospital_id)
    try:
        db.invalidate()
    except Exception:
        logger.exception("failed to discard the V0 lock connection for %s", hospital_id)


@celery_app.task(
    name="app.workers.tasks.trigger_v0_report",
    bind=True,
    max_retries=2,
    soft_time_limit=1800,
    time_limit=2100,
)
def trigger_v0_report(self, hospital_id: str, failure_retry_count: int = 0):
    """프로파일 완료 후 V0 분석 즉시 실행"""
    require_dispatch(self, "trigger-v0-report", hospital_id)
    if type(failure_retry_count) is not int or failure_retry_count < 0:
        raise ValueError("failure_retry_count must be a non-negative integer")
    task_started_at = monotonic()
    failure_checkpoint_run_id: uuid.UUID | None = None
    prior_status: str | None = None  # ANALYZING 전환 전 상태 — 실패 시 복원용 (P2-15)
    # 이 V0 요청의 lineage. Celery의 retry는 request.headers를 그대로 재발행하므로
    # 재시도 전 실행이 남긴 측정을 정확히 지목하는 열쇠가 된다 (v0_checkpoint 참조).
    v0_operation_run_id = _operation_run_id_from_task(self)
    claimed_measurement_run = None
    try:
        # 세션 락은 커넥션에 붙는다 — 중간 commit이 커넥션을 풀에 반환해 버리면 unlock이
        # 다른 커넥션에서 돌아 조용히 실패한다. 그래서 이 태스크만 커넥션을 고정한다.
        with SyncSessionPinnedConnection() as db:
            hospital_uuid = uuid.UUID(hospital_id)
            # 세션 락은 ANALYZING commit ~ MeasurementRun RUNNING commit 사이만 잡는다.
            # 워커 커넥션은 QueuePool이라 해제하지 않으면 락이 풀 커넥션에 영구 잔류하고,
            # 같은 키의 xact 락(자료 저장·운영 기준)까지 막는다.
            acquire_hospital_advisory_session_lock_sync(db, hospital_uuid)
            run = None
            checkpoint = None
            try:
                acquire_hospital_advisory_lock_sync(db, hospital_uuid)
                # 사이트 빌드 전환과 같은 Hospital 행에서 직렬화한다. identity map에 남은
                # ONBOARDING을 강제로 다시 읽지 않으면, build가 막 ACTIVE를 커밋한 뒤
                # 이 UPDATE가 그 상태를 ANALYZING으로 되돌리는 lost update가 생긴다.
                hospital = db.get(
                    Hospital,
                    hospital_uuid,
                    populate_existing=True,
                    with_for_update=True,
                )
                if not hospital:
                    return

                # Idempotency: 이미 V0가 완료된 병원은 재트리거/재배달 시 중복 리포트를 만들지 않는다.
                if hospital.v0_report_done:
                    logger.info("V0 report already done for %s; skipping re-trigger", hospital.name)
                    return {
                        "skipped": "already_done",
                        "message": "이미 초기 진단 리포트가 있어 다시 만들지 않습니다.",
                    }

                # in-progress 가드: 상태와 무관하게 다른 실행의 살아 있는 측정이 있으면
                # 중복 측정 금지. V0는 공개 라이프사이클과 독립이므로 병원이 이미
                # ACTIVE·PAUSED여도 유료 측정 클레임은 살아 있을 수 있다.
                #
                # 단, ANALYZING만 보고 판단하면 안 된다. 실패 경로는 _reset_v0_analyzing_status로
                # 상태를 복원하지만 **하드 종료(SIGKILL·OOM·Cloud Run scale-in)에서는 except가
                # 실행되지 않는다**. 그러면 재배달된 실행이 ANALYZING을 보고 조용히 return하고,
                # v0_report_done은 영원히 False로 남아 초기 진단만 완료되지 않는다.
                # 그래서 클레임의 생존 여부를 측정 실행 기록으로 확인해 만료된 클레임은 탈취한다.
                claim_alive = _v0_claim_is_alive(db, hospital.id)
                if claim_alive and v0_operation_run_id is not None:
                    claimed_measurement_run = find_resumable_v0_measurement_run(
                        db, hospital.id, operation_run_id=v0_operation_run_id
                    )
                if claim_alive and claimed_measurement_run is None:
                    logger.info(
                        "V0 report already in progress for %s; skipping duplicate",
                        hospital.name,
                    )
                    return {
                        "skipped": "already_in_progress",
                        "message": "이미 초기 진단을 백그라운드에서 만들고 있습니다.",
                    }
                if hospital.status == HospitalStatus.ANALYZING and not claim_alive:
                    logger.warning(
                        "Reclaiming a stale V0 ANALYZING claim for %s — the previous run died "
                        "without releasing it",
                        hospital.name,
                    )

                prior_status = (
                    hospital.status.value
                    if hasattr(hospital.status, "value")
                    else str(hospital.status)
                )
                # ANALYZING은 온보딩 초입의 진행 표시일 뿐이다. 이미 허브 준비·도메인
                # 대기·운영·일시 정지로 전진한 상태를 V0가 되돌리지 않는다.
                if hospital.status in (
                    HospitalStatus.ONBOARDING,
                    HospitalStatus.ANALYZING,
                ):
                    hospital.status = HospitalStatus.ANALYZING
                db.commit()
                # commit이 xact 락을 풀므로, RUNNING 행을 커밋할 때까지 세션 락 + 새 xact 락을 유지한다.
                acquire_hospital_advisory_lock_sync(db, hospital_uuid)

                # 쿼리 매트릭스 생성 — 멱등: 측정/PDF 단계 실패 후 재시도 시(v0_report_done은
                # 아직 False) 이미 커밋된 매트릭스를 통째로 중복 생성하지 않는다. 중복되면
                # 주간 SoV 측정 볼륨·API 비용이 영구히 부풀려진다.
                existing_count = db.execute(
                    select(func.count())
                    .select_from(QueryMatrix)
                    .where(QueryMatrix.hospital_id == hospital.id)
                ).scalar_one()
                if existing_count == 0:
                    specs = generate_query_matrix_specs(
                        hospital.region, hospital.specialties, hospital.keywords
                    )
                    for q_text, q_intent in specs:
                        db.add(
                            QueryMatrix(
                                hospital_id=hospital.id,
                                query_text=q_text,
                                query_intent=q_intent,
                            )
                        )
                    db.flush()
                else:
                    logger.info(
                        "Query matrix already exists for %s (%d rows); reusing on retry",
                        hospital.name,
                        existing_count,
                    )

                # 측정 체크포인트: 이 요청이 이미 150건을 측정해 두었다면 다시 사지 않는다.
                # PDF·GCS·커밋 실패로 인한 재시도가 가장 비싼 단계를 되풀이하던 문제를
                # 여기서 끊는다 (아키텍처 리뷰 §2-3). 판정 규칙은 v0_checkpoint 모듈.
                reusable_run = find_reusable_v0_measurement_run(
                    db, hospital.id, operation_run_id=v0_operation_run_id
                )
                sample_queries = []
                if reusable_run is not None:
                    checkpoint = load_v0_checkpoint(
                        db, reusable_run, default_repeat_count=V0_REPEAT_COUNT
                    )
                    logger.warning(
                        "Reusing completed V0 measurement %s for %s — retry skips %d "
                        "paid provider calls",
                        reusable_run.id,
                        hospital.name,
                        checkpoint.success_count + checkpoint.failure_count,
                    )
                else:
                    run = claimed_measurement_run or find_resumable_v0_measurement_run(
                        db, hospital.id, operation_run_id=v0_operation_run_id
                    )
                    if run is not None:
                        run_config = run.config if isinstance(run.config, dict) else {}
                        sample_queries = _v0_queries_from_snapshot(
                            db,
                            run_config.get("query_snapshot"),
                            hospital_id=hospital.id,
                        )
                        if sample_queries is None:
                            raise RuntimeError("resumable V0 run has invalid query snapshot")
                        run.status = "RUNNING"
                        run.completed_at = None
                        logger.warning(
                            "Resuming V0 measurement %s for %s from durable repeat slots",
                            run.id,
                            hospital.name,
                        )
                    else:
                        # AI 답변 언급률 측정 (V0: 쿼리 수 제한, 빠른 실행)
                        sample_queries = db.execute(
                            v0_sample_query_stmt(hospital.id)
                        ).scalars().all()
                        run_config = v0_measurement_run_config(
                            repeat_count=V0_REPEAT_COUNT,
                            operation_run_id=v0_operation_run_id,
                        )
                        run_config["query_snapshot"] = _v0_query_snapshot(sample_queries)
                        run_config["judgment_context"] = _v0_judgment_context(hospital)
                        run = _start_measurement_run(
                            db,
                            hospital,
                            run_label=V0_MEASUREMENT_RUN_LABEL,
                            config=run_config,
                        )
                db.commit()
            finally:
                release_v0_session_lock(db, hospital_uuid)
            if run is None and checkpoint is None:
                return
            if checkpoint is not None:
                failure_checkpoint_run_id = reusable_run.id
                failure_retry_count = max(
                    failure_retry_count,
                    _v0_workflow_failure_count(reusable_run),
                )
                # 체크포인트 경로: 측정 루프도, 비용 가드 예약도 건너뛴다. 이미 낸 호출을
                # 두 번 예약하면 가드가 실제 지출의 2배를 세고 상한이 조기 소진된다.
                all_records = checkpoint.records
                success_count = checkpoint.success_count
                failure_count = checkpoint.failure_count
                failure_summary = checkpoint.failure_summary
                platforms = checkpoint.platforms
                v0_repeat_count = checkpoint.repeat_count
            else:
                failure_checkpoint_run_id = run.id
                failure_retry_count = max(
                    failure_retry_count,
                    _v0_workflow_failure_count(run),
                )
                all_records = []
                platforms = _v0_platforms_from_run(run)
                if platforms is None:
                    raise RuntimeError("V0 measurement platform snapshot is invalid")
                protocol = (
                    run.config.get("measurement_protocol")
                    if isinstance(run.config, dict)
                    else None
                )
                if not isinstance(protocol, dict):
                    raise RuntimeError("V0 measurement protocol is missing")
                _require_v0_execution_policy(protocol, platforms)

                # Freeze every repeat before the first provider call. A hard kill can
                # therefore resume the exact answer or judgment stage without changing
                # the sample, platform, repeat number, or execution policy.
                slot_groups: list[tuple[QueryMatrix, str, list[MeasurementObservationSlot]]] = []
                for q in sample_queries:
                    for platform in platforms:
                        slots = ensure_v0_slots(
                            db,
                            hospital_id=hospital.id,
                            measurement_run_id=run.id,
                            query_id=q.id,
                            platform=platform,
                            repeat_count=V0_REPEAT_COUNT,
                            protocol=protocol,
                        )
                        slot_groups.append((q, platform, slots))
                db.commit()
                all_slots = [slot for _q, _platform, slots in slot_groups for slot in slots]
                judgment_context = _v0_resume_judgment_context(
                    run, hospital, all_slots, protocol
                )
                competitors = judgment_context["competitors"]
                db.commit()

                for q, _platform, slots in slot_groups:
                    for slot in slots:
                        if not slot_is_terminal(slot) and _v0_chunk_deadline_reached(
                            task_started_at
                        ):
                            _checkpoint_v0_progress(run, all_slots)
                            db.commit()
                            raise V0MeasurementResumable(
                                "V0 measurement chunk completed with durable progress"
                            )
                        result = _execute_paid_observation_slot(
                            db,
                            slot=slot,
                            hospital=hospital,
                            hospital_name=judgment_context["hospital_name"],
                            region=judgment_context["region"],
                            query_text=q.query_text,
                            competitors=competitors,
                            protocol=protocol,
                        )
                        if result.get("failure_reason") == "cost_guard_blocked":
                            run.error_summary = {
                                **summarize_observation_slots(
                                    all_slots, deadline_reached=False
                                ).to_payload(),
                                "safe_error_code": "V0_COST_DEFERRED",
                            }
                            db.commit()
                            raise V0CostDeferred("V0 measurement deferred by cost guard")
                        if slot.sov_record_id is not None:
                            all_records.append({**result, "query_intent": q.query_intent})

                # Retry only incomplete provider stages. Ambiguous judgments are
                # terminal observations and are never replaced by a new answer.
                deadline_reached = all(slot_is_terminal(slot) for slot in all_slots)
                if not deadline_reached:
                    _checkpoint_v0_progress(run, all_slots)
                    db.commit()
                    raise V0MeasurementResumable(
                        "V0 observation slots remain resumable"
                    )

                adequacy = summarize_observation_slots(
                    all_slots, deadline_reached=True
                )
                success_count = adequacy.confirmed_slots
                failure_count = adequacy.planned_slots - success_count
                failure_summary = {
                    "safe_error_code": f"V0_MEASUREMENT_{adequacy.status}",
                    "safe_error_message": (
                        "초기 진단은 확인된 일부 표본만 사용했습니다."
                        if adequacy.status == "LIMITED"
                        else "확정 가능한 표본이 없어 수치 없이 초기 진단을 완료했습니다."
                    ),
                    "next_action": "다음 정기 측정에서 같은 정책으로 새 표본을 수집합니다.",
                    **adequacy.to_payload(),
                }
                _finish_measurement_run(
                    run,
                    success_count,
                    failure_count,
                    error_summary=failure_summary if adequacy.status != "COMPLETE" else None,
                )
                db.commit()
                v0_repeat_count = V0_REPEAT_COUNT

            if checkpoint is not None:
                checkpoint_slots = slots_for_run(db, checkpoint.run_id)
                adequacy = (
                    summarize_observation_slots(checkpoint_slots, deadline_reached=True)
                    if checkpoint_slots
                    else None
                )

            adequacy_payload = (
                adequacy.to_payload()
                if adequacy is not None
                else {
                    "status": "LEGACY_UNKNOWN",
                    "lineage": "LEGACY",
                    "planned_slots": 0,
                    "received_answers": 0,
                    "confirmed_slots": 0,
                    "ambiguous_slots": 0,
                    "answer_failed_slots": 0,
                    "judgment_failed_slots": 0,
                    "pending_slots": 0,
                    "terminal_slots": 0,
                }
            )

            # Confirmed results alone form the numeric denominator. A completed
            # diagnostic with no confirmed sample is truthful and has no baseline.
            sov_pct = calculate_sov(all_records) if success_count > 0 else None

            # PDF 리포트 생성
            now = arrow.now("Asia/Seoul")
            pdf_artifact = generate_pdf_report(
                hospital=hospital,
                period_start=now.shift(days=-7).datetime,
                period_end=now.datetime,
                report_type="V0",
                sov_pct=sov_pct,
                # 체크포인트를 재사용하면 그 측정이 실제로 쓴 반복 횟수를 적는다 —
                # 리포트가 "5회 반복"이라고 말하려면 그 숫자가 사실이어야 한다.
                repeat_count=v0_repeat_count,
                sov_coverage=adequacy_payload,
                return_artifact=True,
            )
            if isinstance(pdf_artifact, str):
                raise RuntimeError("V0 PDF artifact metadata was not returned")

            # DB 저장
            basis_run = reusable_run if checkpoint is not None else run
            basis_config = basis_run.config if basis_run is not None else None
            basis_protocol = (
                basis_config.get("measurement_protocol")
                if isinstance(basis_config, dict)
                else None
            )
            basis_query_snapshot = (
                basis_config.get("query_snapshot")
                if isinstance(basis_config, dict)
                else None
            )
            baseline_basis = (
                {
                    "measurement_run_id": str(basis_run.id),
                    "measurement_protocol": basis_protocol,
                    "platforms": platforms,
                    "query_snapshot": basis_query_snapshot,
                }
                if (
                    basis_run is not None
                    and isinstance(basis_protocol, dict)
                    and _local_v0_query_texts(basis_query_snapshot) is not None
                    and adequacy is not None
                    and adequacy.status == "COMPLETE"
                )
                else None
            )
            report = MonthlyReport(
                hospital_id=hospital.id,
                period_year=now.year,
                period_month=now.month,
                report_type="V0",
                pdf_path=pdf_artifact.path,
                doctor_pdf_path=pdf_artifact.path,
                quality=(
                    "COMPLETE"
                    if adequacy_payload["status"] == "COMPLETE"
                    else "DEGRADED"
                    if adequacy_payload["status"] == "LIMITED"
                    else "BLOCKED"
                ),
                planned_count=int(adequacy_payload["planned_slots"]),
                success_count=int(adequacy_payload["confirmed_slots"]),
                failed_count=(
                    int(adequacy_payload["planned_slots"])
                    - int(adequacy_payload["confirmed_slots"])
                ),
                sov_summary={
                    "sov_pct": sov_pct,
                    "platforms": platforms,
                    "baseline_basis": baseline_basis,
                    "observation_adequacy": adequacy_payload,
                },
            )
            db.add(report)
            db.flush()
            db.add(
                MonthlyReportArtifact(
                    report_id=report.id,
                    audience="DOCTOR",
                    path=pdf_artifact.path,
                    sha256=pdf_artifact.sha256,
                    byte_size=pdf_artifact.byte_size,
                    validated=True,
                    validated_at=datetime.now(timezone.utc),
                    validation_metadata=pdf_artifact.validation_metadata,
                )
            )
            # 긴 V0 실행 중 다른 세션이 허브를 ACTIVE/PAUSED로 전환했을 수 있다.
            # pinned 세션 identity-map의 옛 ANALYZING 값을 쓰지 말고 최신 행을 잠근다.
            db.refresh(hospital, with_for_update=True)
            hospital.v0_report_done = True
            if hospital.status in (HospitalStatus.ONBOARDING, HospitalStatus.ANALYZING):
                hospital.status = HospitalStatus.BUILDING
            enqueue_onboarding_notification_sync(
                db,
                build_v0_ready_notification(
                    hospital_id=hospital.id,
                    hospital_name=hospital.name,
                    report_id=report.id,
                    sov_pct=sov_pct,
                    platforms=platforms,
                ),
            )
            db.commit()

            # V0 QueryMatrix → AIQueryTarget 자동 시드 (노출 보완 탭 즉시 활성화)
            # V0 리포트·Slack outbox가 이미 함께 커밋된 뒤 실행하므로, 시드 실패는
            # V0 결과를 롤백하지 않고 로그만 남긴다 (post-commit side effect 격리).
            _seed_query_targets_from_matrix_sync(hospital.id)

            # 콘텐츠 허브 준비는 프로필 완료 시 이미 독립 디스패치된다. 이 호출은 V0가
            # 끝났을 때 한 번 더 보내는 멱등 복구 신호이며, 실패는 V0 결과를 되돌리지 않는다.
            try:
                build_aeo_site.apply_async(
                    args=[hospital_id],
                    queue="default",
                    headers=build_dispatch_headers("build-aeo-site", hospital_id),
                )
            except Exception:
                logger.exception(
                    "build_aeo_site enqueue failed post-V0 (STEP4 deferred): %s", hospital_id
                )
                try:
                    _run_async(
                        open_ops_incident(
                            pipeline="site_build_dispatch",
                            object_type="hospital",
                            object_id=hospital_id,
                            incident_type="SITE_BUILD_DISPATCH_FAILED",
                            safe_error_code="SITE_BUILD_DISPATCH_FAILED",
                            problem="초기 진단 완료 후 콘텐츠 허브 준비 작업을 큐에 넣지 못했습니다.",
                            customer_impact="콘텐츠 허브 공개 준비가 늦어질 수 있습니다.",
                            next_action="자동 복구 결과를 확인하고 계속 실패하면 운영센터에서 사이트 준비를 재시도하세요.",
                            source_type="SITE_BUILD",
                            hospital_name=hospital.name,
                            hospital_id=hospital.id,
                            actor="v0-report-worker",
                            notify=False,
                        )
                    )
                except Exception:
                    logger.exception("build_aeo_site enqueue-failure ops alert delivery failed")

    except V0CostDeferred as exc:
        _reset_v0_analyzing_status(hospital_id, prior_status)
        raise self.retry(
            exc=exc,
            countdown=_seconds_until_next_kst_cost_window(),
            kwargs=_v0_retry_kwargs(self, failure_retry_count),
            max_retries=V0_CONTINUATION_MAX_RETRIES,
        )
    except V0MeasurementResumable as exc:
        _reset_v0_analyzing_status(hospital_id, prior_status)
        raise self.retry(
            exc=exc,
            countdown=120,
            kwargs=_v0_retry_kwargs(self, failure_retry_count),
            max_retries=V0_CONTINUATION_MAX_RETRIES,
        )
    except SoftTimeLimitExceeded as exc:
        _reset_v0_analyzing_status(hospital_id, prior_status)
        raise self.retry(
            exc=exc,
            countdown=120,
            kwargs=_v0_retry_kwargs(self, failure_retry_count),
            max_retries=V0_CONTINUATION_MAX_RETRIES,
        )
    except Exception as exc:
        logger.error(f"trigger_v0_report failed: {exc}")
        # 이 실행이 ANALYZING을 클레임했다면 복원 — 그래야 재시도/수동 재트리거가
        # in-progress 가드를 통과한다 (P2-15).
        _reset_v0_analyzing_status(hospital_id, prior_status)
        terminal_measurement_failure = isinstance(
            exc, (V0MeasurementUnavailable, V0MeasurementPolicyDrift)
        )
        if terminal_measurement_failure or failure_retry_count >= self.max_retries:
            # 공급자별 재시도와 회로 차단까지 끝난 측정 실패는 150건 전체를 Celery가
            # 다시 돌려도 회복되지 않는다. 즉시 사람 확인 대상으로 넘긴다. 그 밖의
            # 일시적 작업 오류만 task-level 재시도를 사용한다.
            try:
                _run_async(
                    open_ops_incident(
                        pipeline="v0_report",
                        object_type="hospital",
                        object_id=hospital_id,
                        incident_type="V0_REPORT_FAILED",
                        safe_error_code=getattr(exc, "summary", {}).get(
                            "safe_error_code", "V0_REPORT_RETRIES_EXHAUSTED"
                        ),
                        problem=getattr(exc, "summary", {}).get(
                            "safe_error_message",
                            "초기 진단 리포트 생성 재시도가 모두 실패했습니다.",
                        ),
                        customer_impact="초기 진단 리포트를 준비할 수 없습니다.",
                        next_action=getattr(exc, "summary", {}).get(
                            "next_action",
                            "운영센터에서 원인을 확인하고 초기 진단 리포트를 수동 재실행하세요.",
                        ),
                        source_type="V0_REPORT",
                        hospital_name="병원 초기 진단",
                        hospital_id=uuid.UUID(hospital_id),
                        actor="v0-report-worker",
                    )
                )
            except Exception:
                logger.exception("V0 final-failure ops alert delivery failed (non-fatal)")
            raise exc
        next_failure_count = failure_retry_count + 1
        _persist_v0_workflow_failure_count(
            failure_checkpoint_run_id, next_failure_count
        )
        raise self.retry(
            exc=exc,
            countdown=120,
            kwargs=_v0_retry_kwargs(self, next_failure_count),
            max_retries=V0_CONTINUATION_MAX_RETRIES,
        )


# ══════════════════════════════════════════════════════════════════
# 콘텐츠 허브 공개 노출 상태 준비
# ══════════════════════════════════════════════════════════════════
def _public_site_url(aeo_domain: str | None, slug: str | None) -> str:
    """실제 접근 가능한 공개 허브 URL. 활성화 서비스와 같은 규칙을 쓴다."""
    return public_site_url(aeo_domain, slug)


def _site_build_prerequisites_met(hospital: Hospital) -> bool:
    return bool(hospital.profile_complete)


@celery_app.task(
    name="app.workers.tasks.build_aeo_site",
    bind=True,
    # 일시 장애(DB/Slack)로 STEP4 허브 준비가 통째로 누락되지 않도록 자동 재시도.
    # site_built 전환은 멱등이라 재실행해도 안전하다.
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def build_aeo_site(self, hospital_id: str):
    """콘텐츠 허브 노출 상태 전환 + 기본 주소 자동 운영 시작 (legacy task name)

    STEP5의 프로필·허브 게이트는 모두 시스템 플래그다. 마지막 전환만 사람 클릭으로 남기면
    AE는 자기가 만들지 않은 사실을 확인하는 클릭 하나 때문에 Slack 두 건을 받는다.
    자기 도메인이 없는 병원은 여기서 그대로 ACTIVE가 되고, 자기 도메인이 지정된 병원만
    수동 경로로 남는다 — DNS는 병원 것이라 시점을 시스템이 정할 수 없다.
    """
    require_dispatch(self, "build-aeo-site", hospital_id)
    activated_slug: str | None = None
    activated_name: str | None = None
    activated_treatments: list | None = None
    with SyncSessionLocal() as db:
        # 판정(evaluate_auto_activation)과 전환이 같은 행 잠금 안에서 일어나야 한다.
        # 잠금 없이 읽은 스냅샷으로 판정하면, 재배달·복구 재디스패치가 그 사이 커밋된
        # `/pause`를 덮어 PAUSED 병원을 되살리고(STEP5 위반), 동시에 도는 두 build가
        # 둘 다 게이트를 통과해 감사행·Slack 인텐트가 중복된다.
        hospital = db.get(Hospital, uuid.UUID(hospital_id), with_for_update=True)
        if not hospital:
            return
        if not _site_build_prerequisites_met(hospital):
            logger.warning(
                "Skipping site build before profile gate: hospital_id=%s profile_complete=%s",
                hospital.id,
                hospital.profile_complete,
            )
            return

        newly_built = not hospital.site_built
        if newly_built:
            hospital.site_built = True
            # ACTIVE/PAUSED 병원을 강등하지 않는다 — admin의 "허브 재준비"나 도메인 재저장이
            # 라이브 공개 허브를 PENDING_DOMAIN으로 떨어뜨려 공개 표면 전체가 404 되는 것 방지.
            # (공개 엔드포인트는 status==ACTIVE && site_live 필수.) 도메인이 실제로 바뀐 경우의
            # 강등은 connect_domain이 검증 무효화와 함께 명시적으로 수행한다.
            if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PAUSED):
                hospital.status = HospitalStatus.PENDING_DOMAIN

        # 재실행(acks_late·자율 복구)에서도 같은 답이 나온다: 이미 ACTIVE면 ALREADY_ACTIVE로
        # 끝나 감사행도 알림도 늘지 않고, PAUSED는 어떤 경우에도 자동으로 되살리지 않는다.
        blocker = evaluate_auto_activation(hospital)
        activated = False
        if blocker is None:
            result = activate_hospital_sync(
                db,
                hospital,
                actor=AUTO_ACTIVATE_ACTOR,
                reason="SITE_BUILD_AUTO_ACTIVATION",
            )
            activated = result.activated_now

        if activated:
            enqueue_onboarding_notification_sync(
                db,
                build_hospital_activated_notification(
                    hospital_id=hospital.id,
                    hospital_name=hospital.name,
                    public_url=_public_site_url(hospital.aeo_domain, hospital.slug),
                ),
            )
            activated_slug = hospital.slug
            activated_name = hospital.name
            activated_treatments = hospital.treatments
        elif newly_built:
            # 자동 시작이 불가능했을 때만 재촉 알림이 남고, 왜 사람이 필요한지 함께 말한다.
            enqueue_onboarding_notification_sync(
                db,
                build_site_built_notification(
                    hospital_id=hospital.id,
                    hospital_name=hospital.name,
                    blocked_reason=blocker_reason(blocker) if blocker is not None else None,
                ),
            )

        if newly_built or activated:
            db.commit()

    # 커밋 이후 — revalidate 실패는 활성화를 되돌리지 않는다 (R4, activate 엔드포인트와 동일).
    if activated_slug:
        _run_async(
            trigger_hospital_site_revalidate_safe(
                activated_slug, activated_treatments, hospital_name=activated_name
            )
        )


# ══════════════════════════════════════════════════════════════════
# 야간 콘텐츠 자동 생성 (매일 밤 23:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.nightly_content_generation",
    bind=True,
    # 50개 슬롯 × (Claude+Imagen) 배치는 전역 900s를 초과하므로 상향. 멱등(body-null 필터)
    # 하므로 acks_late로 워커 크래시 시 안전하게 재배달.
    soft_time_limit=3000,
    time_limit=3300,
    acks_late=True,
)
def nightly_content_generation(self):
    """At 23:00, generate only tomorrow's missing content fragments."""
    require_dispatch(self, "nightly-content-generation")
    now_kst = arrow.now("Asia/Seoul")
    tomorrow = now_kst.shift(days=1).date()
    window_start = tomorrow

    with SyncSessionLocal() as db:
        task_id = str(getattr(self.request, "id", None) or uuid.uuid4())
        recorder = GenerationBatchRecorder(db, task_id, window_start, tomorrow)
        items, truncated_count = _load_nightly_generation_batch(db, window_start, tomorrow)

        if truncated_count:
            # 상한 밖 슬롯은 상태에 남아 다음 주기에 다시 회수된다.
            # 발행 시각까지 해결되지 않을 때만 08시 예외 요약으로 올린다.
            logger.warning(
                "nightly_content_generation cap reached: %d items deferred beyond cap %d",
                truncated_count,
                NIGHTLY_GENERATION_CAP,
            )

        if not items:
            # 빈손 종료가 "할 일이 없음"인지 "직전 실행이 죽어 claim이 잠김"인지 구분한다.
            # 구분하지 않으면 한 달치 유실도 조용히 성공으로 보고된다.
            stuck_items = load_stuck_claims(db, window_start, tomorrow)
            if stuck_items:
                logger.warning(
                    "nightly_content_generation found nothing: %d slots are still locked by "
                    "a previous run's claim",
                    len(stuck_items),
                )
                _record_locked_generation_items(recorder, stuck_items)
            else:
                logger.info(f"No content to generate for {window_start}~{tomorrow}")
            recorder.finish()
            _page_morning_stored_publication_gates(db, now_kst=now_kst)
            return

        claimed_item_ids = [item.id for item in items]
        missing_essence_hospitals: set[uuid.UUID] = set()

        for item in items:
            item_id = item.id
            hospital = item.hospital
            hospital_id = hospital.id
            hospital_name = hospital.name
            claim_time = item.generation_claimed_at
            claim_token = getattr(item, "generation_claim_token", None)
            item_state = GenerationItemState.SUCCEEDED
            philosophy = None

            if getattr(item, "_generation_reclaimed_stale", False):
                stale_code = "STALE_GENERATION_CLAIM"
                stale_message = "이전 생성 작업의 lease가 만료되어 안전하게 다시 인수했습니다."
                recorder.record(
                    item.id,
                    GenerationItemState.FAILED,
                    safe_error_code=stale_code,
                    safe_error_message=stale_message,
                )
                stale_run = recorder.item_run(
                    item.id,
                    hospital_id,
                    "REGENERATE_CONTENT",
                    OperationRunState.FAILED,
                    safe_error_code=stale_code,
                    safe_error_message=stale_message,
                    attempt_kind="stale-claim",
                )
                _run_async(
                    open_generation_incident(
                        item_id=item.id,
                        hospital_id=hospital_id,
                        hospital_name=hospital_name,
                        run_id=stale_run.id,
                        code=stale_code,
                        message=stale_message,
                        notify=generation_notify_requested(stale_code),
                    )
                )

            try:
                if getattr(item, "body", None):
                    state, code, message = _generate_single_content_item(db, item, hospital)
                    _record_generation_batch_outcome(
                        db, recorder, item, hospital, state, code, message
                    )
                    continue

                # 기존 제목 목록 (중복 방지). 상한 없이 전부 실으면 1년 운영한
                # 병원에서 수백 개 제목이 매 호출 프롬프트에 들어간다. 중복 회피에
                # 필요한 것은 최근 무엇을 썼는지이므로 최신 N개만 가져온다.
                existing = db.execute(
                    select(ContentItem.title)
                    .where(
                        ContentItem.hospital_id == hospital.id,
                        ContentItem.title.isnot(None),
                    )
                    .order_by(
                        ContentItem.scheduled_date.desc().nullslast(),
                        ContentItem.published_at.desc().nullslast(),
                    )
                    .limit(EXISTING_TITLE_PROMPT_LIMIT)
                )
                existing_titles = [r[0] for r in existing.all()]

                philosophy = _generation_philosophy_sync(db, hospital.id)
                if _generation_attempt_is_unchanged(item, philosophy):
                    previous = _stored_generation_attempt(item)
                    recorder.record(
                        item.id,
                        GenerationItemState.SKIPPED,
                        safe_error_code=previous["reason"],
                        safe_error_message=(
                            "직전 생성 차단 원인이 달라지지 않아 비용 재시도를 건너뛰었습니다."
                        ),
                    )
                    continue
                if not philosophy:
                    item.content_philosophy_id = None
                    item.essence_status = ESSENCE_STATUS_MISSING_APPROVED
                    item.essence_check_summary = {
                        "blocking": True,
                        "findings": [
                            "승인된 콘텐츠 운영 기준이 없어 자동 생성/발행 품질을 통과할 수 없습니다."
                        ],
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _remember_generation_attempt(
                        db, item, philosophy, "MISSING_APPROVED_ESSENCE"
                    )
                    first_gate_observation = hospital_id not in missing_essence_hospitals
                    missing_essence_hospitals.add(hospital_id)
                    if first_gate_observation:
                        logger.warning(
                            "Skipping content generation without approved clinic writing "
                            "standard: %s",
                            hospital.name,
                        )
                    code = "MISSING_APPROVED_ESSENCE"
                    message = "콘텐츠 운영 기준의 시스템 자동 승인이 아직 완료되지 않았습니다."
                    recorder.record(
                        item.id,
                        GenerationItemState.SKIPPED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    failed_run = recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT",
                        OperationRunState.FAILED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    if first_gate_observation:
                        _run_async(
                            open_generation_incident(
                                item_id=item.id,
                                hospital_id=hospital_id,
                                hospital_name=hospital_name,
                                run_id=failed_run.id,
                                code=code,
                                message=message,
                                notify=generation_notify_requested(code),
                            )
                        )
                    continue

                # 비용 가드: Claude 호출 예산 확인. 차단 시 예외로 배치를 죽이지 않고 이 아이템만
                # 스킵한다(다음 야간 배치에서 body-null 필터로 재시도됨).
                cost_decision = _run_async(cost_guard.check_and_increment("content"))
                if not cost_decision.allowed:
                    logger.warning(
                        "콘텐츠 생성이 비용 가드로 차단됨: %s — %s",
                        hospital.name,
                        cost_decision.reason,
                    )
                    code = "COST_BLOCKED"
                    message = "비용 가드가 생성을 보류했습니다. 운영 센터에서 한도를 확인해 주세요."
                    _remember_generation_attempt(db, item, philosophy, code)
                    recorder.record(
                        item.id,
                        GenerationItemState.SKIPPED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    failed_run = recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT",
                        OperationRunState.FAILED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    _run_async(
                        open_generation_incident(
                            item_id=item.id,
                            hospital_id=hospital_id,
                            hospital_name=hospital_name,
                            run_id=failed_run.id,
                            code=code,
                            message=message,
                            notify=generation_notify_requested(code),
                        )
                    )
                    continue

                # Claude Sonnet 콘텐츠 생성
                approved_brief = prepare_automatic_content_brief_sync(
                    db,
                    item=item,
                    hospital=hospital,
                    philosophy=philosophy,
                )
                # 플래너는 추적 객체(item.query_target_id / content_brief / brief_* 등)를
                # 직접 변경한다. 그대로 두면 아래 조건부 UPDATE의 db.execute()가 autoflush를
                # 먼저 돌려 **status 술어가 없는 UPDATE**를 emit하고, "추적 객체를 건드리지
                # 않는다"는 가드의 전제가 깨진다. 여기서 확정해 item을 clean 상태로 만든다.
                # (기획 메타데이터라 생성이 실패해도 남는 편이 맞고, claim 커밋과 같은 취급이다.)
                db.commit()
                expected_revision = int(getattr(item, "content_revision", 1) or 1)
                content_data, screening = _run_async(
                    _generate_with_auto_review(
                        hospital=hospital,
                        item=item,
                        existing_titles=existing_titles,
                        philosophy=philosophy,
                        approved_brief=approved_brief,
                    )
                )
                now = datetime.now(timezone.utc)

                # 생성 결과는 **추적 객체를 건드리지 않고** 별도 payload에 담는다.
                #
                # claim 커밋 시점에 행 잠금이 풀리므로, 생성이 도는 동안(최대 soft_time_limit)
                # AE가 Admin에서 이 항목을 취소(CANCELLED)할 수 있다. 여기서
                # `item.status = DRAFT`처럼 추적 객체를 먼저 변경하면 SQLAlchemy가 다음
                # execute/commit 앞에서 autoflush로 그 값을 먼저 써버려 취소가 되살아난다
                # (세션은 expire_on_commit=False). 그래서 조건부 UPDATE 한 방으로만 쓰고,
                # 0행이면 운영자의 취소가 이긴 것으로 보고 결과를 버린다.
                written = write_back_generated_content(
                    db,
                    item_id=item.id,
                    expected_revision=expected_revision,
                    expected_claim_token=claim_token,
                    values={
                        "title": content_data["title"],
                        "body": content_data["body"],
                        "meta_description": content_data.get("meta_description"),
                        "references_list": content_data.get("references") or [],
                        "faq_question": content_data.get("faq_question"),
                        "faq_answer_summary": content_data.get("faq_answer_summary"),
                        "image_url": None,
                        "image_prompt": None,
                        "image_policy_verified_at": None,
                        "image_content_hash": None,
                        "image_subject_hash": None,
                        "image_policy_version": None,
                        "generated_at": now,
                        "body_updated_at": now,
                        "status": ContentStatus.DRAFT,
                        "content_philosophy_id": philosophy.id,
                        "generation_philosophy_id": philosophy.id,
                        "last_reviewed_philosophy_id": philosophy.id,
                        "essence_status": screening.status,
                        "essence_check_summary": _generation_summary(
                            db, hospital.id, screening, philosophy, approved_brief
                        ),
                    },
                )
                if written == 0:
                    # 운영자가 생성 도중 상태를 바꿨다(취소/발행 등). 배치 결과보다
                    # 운영자 의도가 우선이므로 생성물을 버리고 다음 항목으로 넘어간다.
                    db.rollback()
                    db.expire(item)
                    logger.info(
                        "Discarding generated content for %s — status changed during generation",
                        item.id,
                    )
                    recorder.record(item.id, GenerationItemState.DISCARDED)
                    recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT",
                        OperationRunState.CANCELLED,
                    )
                    continue

                # 텍스트 콘텐츠 먼저 커밋 (이미지 실패가 텍스트를 롤백하지 않도록)
                db.commit()
                db.refresh(item)  # expire_on_commit=False — 조건부 UPDATE 결과를 다시 읽어온다
                logger.info(f"Content generated: {hospital.name} — {item.title}")

                # 대표 이미지는 비어 있을 때만 채운다. 기존 이미지가 있으면 공급자 파이프를
                # 절대 다시 호출하지 않는다.
                image_state = _recover_missing_content_image(db, item, hospital, philosophy)
                if image_state != GenerationItemState.SUCCEEDED:
                    item_state = image_state

                readiness_failure = None
                if item_state != GenerationItemState.DISCARDED:
                    readiness_failure = _persist_publication_readiness(db, item, philosophy)
                    if (
                        readiness_failure is not None
                        and item_state == GenerationItemState.SUCCEEDED
                    ):
                        item_state = GenerationItemState.FAILED

                if item_state == GenerationItemState.FAILED:
                    code, message = readiness_failure or (
                        "GENERATION_FAILED",
                        "자동 발행 준비 검사를 통과하지 못했습니다.",
                    )
                    recorder.record(
                        item.id,
                        item_state,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    failed_run = recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT",
                        OperationRunState.FAILED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    _run_async(
                        open_generation_incident(
                            item_id=item.id,
                            hospital_id=hospital_id,
                            hospital_name=hospital_name,
                            run_id=failed_run.id,
                            code=code,
                            message=message,
                            notify=generation_notify_requested(code),
                        )
                    )
                elif item_state == GenerationItemState.PARTIAL:
                    code = "IMAGE_GENERATION_FAILED"
                    message = "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다."
                    recorder.record(
                        item.id,
                        item_state,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    text_run = create_item_run(
                        db,
                        parent_run_id=recorder.run.id,
                        item_id=item.id,
                        hospital_id=hospital_id,
                        operation_type="REGENERATE_CONTENT",
                        state=OperationRunState.SUCCEEDED,
                        result={"state": "SUCCEEDED", "artifact": "text"},
                        attempt_kind="text",
                    )
                    _run_async(
                        recover_generation_incidents(
                            item.id,
                            hospital_id,
                            hospital_name,
                            text_run.id,
                            include_image=False,
                        )
                    )
                    image_run = recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT_IMAGE",
                        OperationRunState.FAILED,
                        safe_error_code=code,
                        safe_error_message=message,
                    )
                    _run_async(
                        open_generation_incident(
                            item_id=item.id,
                            hospital_id=hospital_id,
                            hospital_name=hospital_name,
                            run_id=image_run.id,
                            code=code,
                            message=message,
                            notify=generation_notify_requested(code),
                        )
                    )
                elif item_state == GenerationItemState.DISCARDED:
                    recorder.record(item.id, item_state)
                    recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT_IMAGE",
                        OperationRunState.CANCELLED,
                    )
                else:
                    recorder.record(item.id, GenerationItemState.SUCCEEDED)
                    success_run = recorder.item_run(
                        item.id,
                        hospital_id,
                        "REGENERATE_CONTENT",
                        OperationRunState.SUCCEEDED,
                    )
                    _run_async(
                        recover_generation_incidents(
                            item.id, hospital_id, hospital_name, success_run.id
                        )
                    )

            except Exception as e:
                code, message = classify_generation_failure(e)
                logger.error("Content generation failed for item %s: %s", item.id, type(e).__name__)
                db.rollback()
                db.expire_all()
                if not getattr(item, "body", None):
                    _remember_generation_attempt(db, item, philosophy, code)
                recorder.record(
                    item.id,
                    GenerationItemState.FAILED,
                    safe_error_code=code,
                    safe_error_message=message,
                )
                failed_run = recorder.item_run(
                    item.id,
                    hospital_id,
                    "REGENERATE_CONTENT",
                    OperationRunState.FAILED,
                    safe_error_code=code,
                    safe_error_message=message,
                )
                _run_async(
                    open_generation_incident(
                        item_id=item.id,
                        hospital_id=hospital_id,
                        hospital_name=hospital_name,
                        run_id=failed_run.id,
                        code=code,
                        message=message,
                        notify=generation_notify_requested(code),
                    )
                )
            finally:
                released = release_unfinished_claims(
                    db,
                    [item_id],
                    expected_claimed_at=claim_time,
                    expected_claim_token=claim_token,
                )
                if released:
                    db.commit()

        # 다른 워커가 보유한 live lease도 이 실행의 미처리 결과다. 일부만 생성한 경우
        # PARTIAL, 전부 잠긴 경우 FAILED로 남겨 빈 성공으로 오인되지 않게 한다.
        stuck_items = load_stuck_claims(db, window_start, tomorrow)
        if stuck_items:
            _record_locked_generation_items(recorder, stuck_items)
        recorder.finish()
        _page_morning_stored_publication_gates(db, now_kst=now_kst)

        # 성공과 자동 복구 중간 상태는 Slack으로 보내지 않는다. 인시던트/실행 기록이 다음
        # 배치의 입력이 되고, 01·04·07·07:45 재시도가 스스로 복구한다. 사람만 해결할 수 있는
        # 승인 기준 누락은 위에서 병원별 상태만 남기고 이 실행의 요약을 한 번 보낸다. 재시도
        # 소진 후 남은 최종 발행 차단은 08시 배치에서 한 번의 요약으로만 알린다.
        logger.info("Nightly generation finalized %d item claims", len(claimed_item_ids))


@celery_app.task(
    name="app.workers.tasks.overnight_content_generation_recovery",
    bind=True,
    soft_time_limit=3000,
    time_limit=3300,
    acks_late=True,
)
def overnight_content_generation_recovery(self):
    """At 01/04/07, fill missing fragments without rewriting a stored body."""

    require_dispatch(self, "overnight-content-generation-recovery")
    today = arrow.now("Asia/Seoul").date()
    with SyncSessionLocal() as db:
        task_id = str(getattr(self.request, "id", None) or uuid.uuid4())
        recorder = GenerationBatchRecorder(db, task_id, today, today)
        items, truncated_count = _load_nightly_generation_batch(db, today, today)
        if truncated_count:
            logger.warning(
                "overnight fragment recovery cap reached: %d items deferred beyond cap %d",
                truncated_count,
                NIGHTLY_GENERATION_CAP,
            )
        for item in items:
            claim_time = item.generation_claimed_at
            claim_token = getattr(item, "generation_claim_token", None)
            philosophy = None
            try:
                philosophy = _generation_philosophy_sync(db, item.hospital.id)
                # _generate_single_content_item repeats this lookup so all callers
                # share one policy.  The inexpensive duplicate read is preferable
                # to allowing an exception path to lose the context fingerprint.
                state, code, message = _generate_single_content_item(
                    db, item, item.hospital
                )
                _record_generation_batch_outcome(
                    db, recorder, item, item.hospital, state, code, message, notify=False
                )
            except Exception as error:
                code, message = classify_generation_failure(error)
                logger.error(
                    "Overnight fragment recovery failed for item %s: %s",
                    item.id,
                    type(error).__name__,
                )
                db.rollback()
                db.expire_all()
                if not getattr(item, "body", None):
                    _remember_generation_attempt(db, item, philosophy, code)
                _record_generation_batch_outcome(
                    db,
                    recorder,
                    item,
                    item.hospital,
                    GenerationItemState.FAILED,
                    code,
                    message,
                    notify=False,
                )
            finally:
                released = release_unfinished_claims(
                    db,
                    [item.id],
                    expected_claimed_at=claim_time,
                    expected_claim_token=claim_token,
                )
                if released:
                    db.commit()
        recorder.finish()


@celery_app.task(
    name="app.workers.tasks.prepublish_content_generation_recovery",
    bind=True,
)
def prepublish_content_generation_recovery(self):
    """At 07:45, page only blockers left after the automated recovery sweeps."""

    require_dispatch(self, "prepublish-content-generation-recovery")
    now_kst = arrow.now("Asia/Seoul")
    with SyncSessionLocal() as db:
        _page_morning_stored_publication_gates(db, now_kst=now_kst)


@celery_app.task(name="app.workers.tasks.regenerate_content_item", bind=True, max_retries=1)
def regenerate_content_item(self, content_id: str):
    """Generate a single unpublished content item on operator request."""
    item_id = uuid.UUID(content_id)
    if explicit_run_context(self) is None:
        require_dispatch(self, "regenerate-content", str(item_id))
    with SyncSessionLocal() as db:
        item = db.get(ContentItem, item_id)
        if not item:
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        if explicit_run_context(self) is not None and not explicit_run_matches(
            db, self, item_id, item.hospital_id
        ):
            raise PermissionError("operation run does not authorize this content target")
        if item.status in (ContentStatus.PUBLISHED, ContentStatus.CANCELLED):
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        hospital = db.get(Hospital, item.hospital_id)
        if not hospital:
            finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code="HOSPITAL_NOT_FOUND",
                safe_error_message="병원 정보를 찾을 수 없어 생성 작업을 중단했습니다.",
            )
            return
        try:
            outcome, code, message = _generate_single_content_item(db, item, hospital)
        except Exception as exc:
            db.rollback()
            code, message = classify_generation_failure(exc)
            run_id = finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code=code,
                safe_error_message=message,
            )
            if run_id is not None:
                _run_async(
                    open_generation_incident(
                        item_id=item_id,
                        hospital_id=hospital.id,
                        hospital_name=hospital.name,
                        run_id=run_id,
                        code=code,
                        message=message,
                    )
                )
            logger.error(
                "regenerate_content_item failed for %s: %s", content_id, type(exc).__name__
            )
            raise

        if outcome == GenerationItemState.DISCARDED:
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        if outcome == GenerationItemState.PARTIAL:
            parent_id = finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
            if parent_id is not None and code is not None and message is not None:
                _run_async(
                    recover_generation_incidents(
                        item_id,
                        hospital.id,
                        hospital.name,
                        parent_id,
                        include_image=False,
                    )
                )
                image_run = create_item_run(
                    db,
                    parent_run_id=parent_id,
                    item_id=item_id,
                    hospital_id=hospital.id,
                    operation_type="REGENERATE_CONTENT_IMAGE",
                    state=OperationRunState.FAILED,
                    result={"state": "FAILED", "safe_error_code": code},
                    safe_error_code=code,
                    safe_error_message=message,
                )
                _run_async(
                    open_generation_incident(
                        item_id=item_id,
                        hospital_id=hospital.id,
                        hospital_name=hospital.name,
                        run_id=image_run.id,
                        code=code,
                        message=message,
                    )
                )
            return
        if outcome in (GenerationItemState.SKIPPED, GenerationItemState.FAILED):
            run_id = finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code=code,
                safe_error_message=message,
            )
            if run_id is not None and code is not None and message is not None:
                _run_async(
                    open_generation_incident(
                        item_id=item_id,
                        hospital_id=hospital.id,
                        hospital_name=hospital.name,
                        run_id=run_id,
                        code=code,
                        message=message,
                    )
                )
            return
        run_id = finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
        if run_id is not None:
            _run_async(recover_generation_incidents(item_id, hospital.id, hospital.name, run_id))


def _recertify_runs(db, item_id: uuid.UUID, hospital_id: uuid.UUID) -> list[OperationRun]:
    """이 글의 재인증 실행 이력. 예산·차단 판정의 유일한 근거다.

    색인된 컬럼(hospital_id, operation_type)으로 좁힌 뒤 payload로 이 글의 실행만 고른다.
    """
    runs = (
        db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == recertification.RECERTIFY_OPERATION,
            )
        )
        .scalars()
        .all()
    )
    return [run for run in runs if recertification.payload_source_id(run) == str(item_id)]


def _open_published_recertify_incident(
    *,
    item_id: uuid.UUID,
    hospital: Hospital,
    run_id: uuid.UUID,
    revision: int,
    code: str,
) -> None:
    """자동 복구가 더 진행하지 않는 재인증 차단을 (글, 판) 한 건의 incident로 남긴다."""
    _run_async(
        open_generation_incident(
            item_id=item_id,
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            run_id=run_id,
            code=code,
            message=generation_safe_cause(code),
            revision=revision,
        )
    )


def _recover_published_recertify_incidents(
    item_id: uuid.UUID, hospital_id: uuid.UUID, hospital_name: str, run_id: uuid.UUID | None
) -> None:
    """재인증이 성공하면 같은 글의 보류 incident를 사람 개입 없이 닫는다."""
    if run_id is None:
        return
    _run_async(
        recover_generation_incidents(
            item_id,
            hospital_id,
            hospital_name,
            run_id,
            safe_error_codes=tuple(sorted(recertification.OPERATOR_REQUIRED_CODES)),
        )
    )


def _finish_recertify_block(
    db,
    task,
    *,
    item_id: uuid.UUID,
    hospital: Hospital | None,
    revision: int,
    code: str,
) -> None:
    """유료 호출 없이 끝나는 차단 하나를 닫는다.

    실행을 FAILED로 종결하고, 같은 (글, 판)의 incident 하나를 열거나 갱신하고(세 코드가
    지문을 공유하므로 두 번째 Slack은 나가지 않는다), sweep이 이 행을 다시 후보로 집지
    않게 표시를 남긴다.
    """
    run_id = finish_explicit_run(
        db,
        task,
        item_id,
        OperationRunState.FAILED,
        safe_error_code=code,
        safe_error_message=recertification.SAFE_MESSAGES[code],
    )
    recertification.mark_blocked(db, item_id=item_id, revision=revision, code=code)
    db.commit()
    if run_id is not None and hospital is not None:
        _open_published_recertify_incident(
            item_id=item_id,
            hospital=hospital,
            run_id=run_id,
            revision=revision,
            code=code,
        )


@celery_app.task(
    name="app.workers.tasks.recertify_published_content_image", bind=True, max_retries=0
)
def recertify_published_content_image(self, content_id: str):
    """공개 글의 제목 편집이 지운 이미지 인증을 저장된 바이트 재검수로 복구한다 (H-01).

    한 실행은 공급자를 많아야 한 번 부른다 — 태스크 자체의 재시도는 없고, 재실행은
    쿨다운을 둔 복구 sweep만 만든다. 시작할 때 사람의 결정을 기다리는 판이거나 예산이
    끝났으면 돈을 쓰지 않고 FAILED로 끝내며, 예산 소진은 (글, 판) 하나의 incident가 된다.
    성공하면 공개 페이지가 다시 글을 내보내므로 IndexNow·사이트 캐시를 갱신하고 보류
    incident를 닫는다. 공개 글의 이미지를 임의로 새로 생성하지 않는다.
    """
    item_id = uuid.UUID(content_id)
    if explicit_run_context(self) is None:
        require_dispatch(self, "recertify-published-image", str(item_id))
    with SyncSessionLocal() as db:
        # 유료 재검수 구간 전체를 이 행 잠금 안에서 돈다. 같은 (글, 판)에 PATCH가 건
        # 기본 키 실행과 sweep의 재실행이 겹치면 뒤에 온 쪽은 여기서 기다렸다가 이미
        # 복구된 인증을 보고 돈을 쓰지 않고 끝난다. update_content도 같은 행을
        # FOR UPDATE로 잡으므로 편집과도 같은 잠금에서 직렬화된다.
        item = db.execute(
            select(ContentItem).where(ContentItem.id == item_id).with_for_update()
        ).scalar_one_or_none()
        if not item or item.status != ContentStatus.PUBLISHED:
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        if explicit_run_context(self) is not None and not explicit_run_matches(
            db,
            self,
            item_id,
            item.hospital_id,
            operation_type=recertification.RECERTIFY_OPERATION,
        ):
            raise PermissionError("operation run does not authorize this content target")
        hospital = db.get(Hospital, item.hospital_id)
        expected_title = item.title
        expected_revision = int(getattr(item, "content_revision", 1) or 1)
        if image_certification_current(item):
            # 중복 디스패치·재배달. 유효한 인증을 다시 사지 않는다.
            recertification.clear_marker(db, item_id=item_id, revision=expected_revision)
            db.commit()
            run_id = finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
            if hospital is not None:
                _recover_published_recertify_incidents(
                    item_id, hospital.id, hospital.name, run_id
                )
            return
        if hospital is None or not item.image_url:
            _finish_recertify_block(
                db,
                self,
                item_id=item_id,
                hospital=hospital,
                revision=expected_revision,
                code=recertification.PUBLISHED_IMAGE_MISSING,
            )
            return
        runs = _recertify_runs(db, item_id, hospital.id)
        blocked = recertification.pending_operator_code(runs, expected_revision)
        if blocked is not None:
            # 이 판은 이미 사람의 결정을 기다린다. 같은 답을 다시 사지 않는다.
            _finish_recertify_block(
                db,
                self,
                item_id=item_id,
                hospital=hospital,
                revision=expected_revision,
                code=blocked,
            )
            return
        if (
            recertification.attempts_spent(runs, expected_revision)
            >= recertification.ATTEMPT_BUDGET
        ):
            # 예산 소진. 태스크가 시작조차 못한 실패(TASK_FAILED·BROKER_UNAVAILABLE 등)도
            # 이 실행이 하나의 사고로 닫는다.
            _finish_recertify_block(
                db,
                self,
                item_id=item_id,
                hospital=hospital,
                revision=expected_revision,
                code=recertification.PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED,
            )
            return
        try:
            content_hash, subject_hash = _run_async(
                certify_existing_image(
                    item.image_url,
                    content_type=item.content_type,
                    topic=expected_title,
                    hospital_id=hospital.id,
                )
            )
        except ImagePolicyRejectedError:
            db.rollback()
            _finish_recertify_block(
                db,
                self,
                item_id=item_id,
                hospital=hospital,
                revision=expected_revision,
                code=recertification.PUBLISHED_IMAGE_RECERTIFY_REJECTED,
            )
            return
        except Exception as exc:
            # 공급자·저장소 일시 오류. 이 실행은 예산 한 번을 쓰고 끝나고, 쿨다운을 둔
            # sweep이 남은 예산 안에서 이어받는다.
            db.rollback()
            code, message = classify_generation_failure(exc)
            finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code=code,
                safe_error_message=message,
            )
            logger.error(
                "recertify_published_content_image failed for %s: %s",
                content_id,
                type(exc).__name__,
            )
            raise
        written = write_back_published_image_certificate(
            db,
            item_id=item.id,
            expected_title=expected_title,
            expected_revision=expected_revision,
            values={
                "image_content_hash": content_hash,
                "image_subject_hash": subject_hash,
                "image_policy_version": IMAGE_POLICY_VERSION,
                "image_policy_verified_at": datetime.now(timezone.utc),
            },
        )
        if written == 0:
            # 재검수 중 편집이 또 일어났다. 그 편집이 다시 재인증을 요청한다.
            db.rollback()
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        recertification.clear_marker(db, item_id=item_id, revision=expected_revision)
        indexnow.enqueue_content_published_sync(
            db,
            slug=hospital.slug,
            content_id=item.id,
            aeo_domain=hospital.aeo_domain,
            treatments=hospital.treatments,
            revision=expected_revision,
        )
        db.commit()
        run_id = finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
        hospital_id, slug = hospital.id, hospital.slug
        hospital_name, treatments = hospital.name, hospital.treatments
    _recover_published_recertify_incidents(item_id, hospital_id, hospital_name, run_id)
    _run_async(
        trigger_content_site_revalidate_safe(
            slug, item_id, hospital_name=hospital_name, treatments=treatments
        )
    )

@celery_app.task(name="app.workers.tasks.generate_content_image", bind=True, max_retries=1)
def generate_content_image(self, content_id: str):
    """Regenerate only the cover image while preserving operator-reviewed text."""
    item_id = uuid.UUID(content_id)
    with SyncSessionLocal() as db:
        item = db.get(ContentItem, item_id)
        if not item:
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        if item.status in (ContentStatus.PUBLISHED, ContentStatus.CANCELLED):
            finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
            return
        hospital = db.get(Hospital, item.hospital_id)
        if not hospital:
            finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code="HOSPITAL_NOT_FOUND",
                safe_error_message="병원 정보를 찾을 수 없어 이미지 생성을 중단했습니다.",
            )
            return
        try:
            philosophy = _generation_philosophy_sync(db, hospital.id)
            if philosophy is None:
                finish_explicit_run(
                    db,
                    self,
                    item_id,
                    OperationRunState.FAILED,
                    safe_error_code="MISSING_APPROVED_ESSENCE",
                    safe_error_message="최신 콘텐츠 운영 기준의 자동 승인이 아직 완료되지 않았습니다.",
                )
                return
            # Share the same durable lease as nightly text/image generation. The
            # claim is committed before the paid call so concurrent admin jobs or
            # a nightly owner observe it and perform zero duplicate provider work.
            claimed = claim_generation_lease(db, item_id)
            if claimed is None:
                finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
                logger.info("Image generation deferred for %s — active generation lease", item_id)
                return
            item, claim_token = claimed
            image_source_title = item.title or "병원 의료 정보"
            expected_revision = int(getattr(item, "content_revision", 1) or 1)
            image_url, image_prompt = _run_async(
                generate_image(
                    item.content_type,
                    hospital.slug,
                    topic=image_source_title,
                    direction=hospital_image_direction(hospital),
                    hospital_id=hospital.id,
                )
            )
            if not image_url:
                release_generation_claim(db, item_id, claim_token)
                db.commit()
                code = "IMAGE_GENERATION_FAILED"
                message = "대표 이미지 생성이 완료되지 않았습니다."
                run_id = finish_explicit_run(
                    db,
                    self,
                    item_id,
                    OperationRunState.FAILED,
                    safe_error_code=code,
                    safe_error_message=message,
                )
                if run_id is not None:
                    _run_async(
                        open_generation_incident(
                            item_id=item_id,
                            hospital_id=hospital.id,
                            hospital_name=hospital.name,
                            run_id=run_id,
                            code=code,
                            message=message,
                        )
                    )
                return
            written = write_back_generated_image(
                db,
                item_id=item.id,
                expected_title=item.title,
                expected_revision=expected_revision,
                expected_claim_token=claim_token,
                values={
                    "image_url": image_url,
                    "image_prompt": image_prompt,
                    "image_policy_verified_at": datetime.now(timezone.utc),
                    "image_content_hash": image_content_hash_from_url(image_url),
                    "image_subject_hash": image_subject_hash(
                        item.content_type, image_source_title
                    ),
                    "image_policy_version": IMAGE_POLICY_VERSION,
                },
            )
            if written == 0:
                db.rollback()
                finish_explicit_run(db, self, item_id, OperationRunState.CANCELLED)
                logger.warning(
                    "Image write-back skipped for %s — status changed during regeneration",
                    content_id,
                )
                return
            db.commit()
            db.refresh(item)
            _persist_publication_readiness(db, item, philosophy)
            run_id = finish_explicit_run(db, self, item_id, OperationRunState.SUCCEEDED)
            if run_id is not None:
                _run_async(
                    recover_generation_incidents(
                        item_id,
                        hospital.id,
                        hospital.name,
                        run_id,
                        safe_error_codes=(
                            "IMAGE_GENERATION_FAILED",
                            "CONTENT_IMAGE_NOT_READY",
                            "CONTENT_IMAGE_NOT_VERIFIED",
                        ),
                    )
                )
        except Exception as exc:
            db.rollback()
            if "claim_token" in locals():
                release_generation_claim(db, item_id, claim_token)
                db.commit()
            code, message = classify_generation_failure(exc)
            run_id = finish_explicit_run(
                db,
                self,
                item_id,
                OperationRunState.FAILED,
                safe_error_code=code,
                safe_error_message=message,
            )
            if run_id is not None:
                _run_async(
                    open_generation_incident(
                        item_id=item_id,
                        hospital_id=hospital.id,
                        hospital_name=hospital.name,
                        run_id=run_id,
                        code=code,
                        message=message,
                    )
                )
            logger.error("generate_content_image failed for %s: %s", content_id, type(exc).__name__)
            raise


def _generate_single_content_item(
    db, item: ContentItem, hospital: Hospital
) -> tuple[GenerationItemState, str | None, str | None]:
    philosophy = _generation_philosophy_sync(db, hospital.id)
    if not philosophy:
        item.content_philosophy_id = None
        item.essence_status = ESSENCE_STATUS_MISSING_APPROVED
        item.essence_check_summary = {
            "blocking": True,
            "findings": [
                "승인된 콘텐츠 운영 기준이 없어 자동 생성/발행 품질을 통과할 수 없습니다."
            ],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        _remember_generation_attempt(db, item, philosophy, "MISSING_APPROVED_ESSENCE")
        return (
            GenerationItemState.SKIPPED,
            "MISSING_APPROVED_ESSENCE",
            "콘텐츠 운영 기준의 시스템 자동 승인이 아직 완료되지 않았습니다.",
        )

    # A body generated under this exact Essence remains immutable in scheduled
    # sweeps. Once automated source refresh approves a different snapshot, the
    # old body gets one normal regeneration attempt against the new standard.
    body_uses_current_philosophy = bool(
        getattr(item, "body", None)
        and getattr(item, "content_philosophy_id", None) == philosophy.id
    )
    if body_uses_current_philosophy:
        stored_assessment = assess_content_publication(item, philosophy)
        if stored_assessment.code in {
            "CONTENT_AI_REVIEW_STALE",
            "CONTENT_AI_REVIEW_UNAVAILABLE",
        }:
            previous_attempt = _stored_generation_attempt(item)
            if (
                previous_attempt.get("reason") == "CONTENT_AI_REVIEW_UNAVAILABLE"
                and _generation_attempt_is_unchanged(item, philosophy)
            ):
                return (
                    GenerationItemState.SKIPPED,
                    "CONTENT_AI_REVIEW_UNAVAILABLE",
                    "독립 검수 공급자 복구 시각까지 자동 재검수를 보류합니다.",
                )
            candidate = {
                field: getattr(item, field, None)
                for field in (
                    "title",
                    "body",
                    "meta_description",
                    "faq_question",
                    "faq_answer_summary",
                    "references_list",
                )
            }
            refreshed_review = _run_async(
                review_generated_content(
                    hospital=hospital,
                    philosophy=philosophy,
                    content=candidate,
                    content_brief=getattr(item, "content_brief", None),
                )
            )
            if refreshed_review.status == ContentAiReviewStatus.UNAVAILABLE:
                _remember_generation_attempt(
                    db, item, philosophy, "CONTENT_AI_REVIEW_UNAVAILABLE"
                )
                return (
                    GenerationItemState.PARTIAL,
                    "CONTENT_AI_REVIEW_UNAVAILABLE",
                    "독립 검수 공급자 복구 후 자동 재검수를 다시 시도합니다.",
                )
            summary = (
                dict(item.essence_check_summary)
                if isinstance(item.essence_check_summary, dict)
                else {}
            )
            summary["ai_review"] = refreshed_review.payload()
            item.essence_check_summary = summary
            _clear_generation_attempt(db, item)
            stored_assessment = assess_content_publication(item, philosophy)
            if stored_assessment.code not in _AUTOMATIC_BODY_REPAIR_CODES:
                image_state = _recover_missing_content_image(db, item, hospital, philosophy)
                if image_state != GenerationItemState.SUCCEEDED:
                    return image_state, "IMAGE_GENERATION_FAILED", (
                        "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다."
                    )
                readiness_failure = _persist_publication_readiness(db, item, philosophy)
                return (
                    (GenerationItemState.FAILED, *readiness_failure)
                    if readiness_failure is not None
                    else (GenerationItemState.SUCCEEDED, None, None)
                )
        if stored_assessment.code in _AUTOMATIC_BODY_REPAIR_CODES:
            logger.info(
                "Regenerating repairable stored content %s: %s",
                item.id,
                stored_assessment.code,
            )
        else:
            # Image candidates are individually bounded and semantic failures are
            # fail-closed before upload. Scheduled sweeps may therefore try a fresh
            # candidate again; the cost guard bounds provider spend and incidents
            # stay deduplicated by item/cause/attempt context.
            image_state = _recover_missing_content_image(db, item, hospital, philosophy)
            if image_state == GenerationItemState.PARTIAL:
                _persist_publication_readiness(db, item, philosophy)
                return (
                    image_state,
                    "IMAGE_GENERATION_FAILED",
                    "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다.",
                )
            if image_state == GenerationItemState.DISCARDED:
                return image_state, None, None
            readiness_failure = _persist_publication_readiness(db, item, philosophy)
            if readiness_failure is not None:
                return GenerationItemState.FAILED, *readiness_failure
            return GenerationItemState.SUCCEEDED, None, None

    # The same empty slot and unchanged generation context gets no second writer
    # call.  A philosophy/context change removes this suppression exactly once.
    if _generation_attempt_is_unchanged(item, philosophy):
        previous = _stored_generation_attempt(item)
        return (
            GenerationItemState.SKIPPED,
            previous["reason"],
            "직전 생성 차단 원인이 달라지지 않아 비용 재시도를 건너뛰었습니다.",
        )

    # 최신 N개만. 상한 없는 전량 주입은 프롬프트 입력을 병원 연차에 비례해 부풀린다.
    existing = db.execute(
        select(ContentItem.title)
        .where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.id != item.id,
            ContentItem.title.isnot(None),
        )
        .order_by(
            ContentItem.scheduled_date.desc().nullslast(),
            ContentItem.published_at.desc().nullslast(),
        )
        .limit(EXISTING_TITLE_PROMPT_LIMIT)
    )
    existing_titles = [row[0] for row in existing.all()]

    # 비용 가드: Claude 호출 예산 확인. 차단 시 생성을 건너뛴다(item은 DRAFT/본문 없음 유지 —
    # 다음 야간 배치의 생성 재시도가 커버한다). 하드 상한 알림은 가드가 자체 발송한다.
    cost_decision = _run_async(cost_guard.check_and_increment("content"))
    if not cost_decision.allowed:
        logger.warning(
            "단일 콘텐츠 재생성이 비용 가드로 차단됨: %s — %s", hospital.name, cost_decision.reason
        )
        _remember_generation_attempt(db, item, philosophy, "COST_BLOCKED")
        return (
            GenerationItemState.SKIPPED,
            "COST_BLOCKED",
            "비용 가드가 생성을 보류했습니다. 운영 센터에서 한도를 확인해 주세요.",
        )

    approved_brief = prepare_automatic_content_brief_sync(
        db,
        item=item,
        hospital=hospital,
        philosophy=philosophy,
    )
    # 야간 배치와 동일한 이유로, 긴 생성 호출 전에 플래너 변경을 확정해 item을 clean으로 만든다.
    db.commit()
    expected_revision = int(getattr(item, "content_revision", 1) or 1)
    content_data, screening = _run_async(
        _generate_with_auto_review(
            hospital=hospital,
            item=item,
            existing_titles=existing_titles,
            philosophy=philosophy,
            approved_brief=approved_brief,
        )
    )
    now = datetime.now(timezone.utc)

    # 배치 경로와 같은 상태 가드를 쓴다. 재생성이 도는 동안 AE가 이 슬롯을 종료(CANCELLED)할
    # 수 있고, 가드 없이 쓰면 종료된 슬롯에 미검수 본문이 들어간다(실제 DB에서 재현됨).
    written = write_back_generated_content(
        db,
        item_id=item.id,
        expected_revision=expected_revision,
        expected_claim_token=getattr(item, "generation_claim_token", None),
        values={
            "title": content_data["title"],
            "body": content_data["body"],
            "meta_description": content_data.get("meta_description"),
            "references_list": content_data.get("references") or [],
            "faq_question": content_data.get("faq_question"),
            "faq_answer_summary": content_data.get("faq_answer_summary"),
            "image_url": None,
            "image_prompt": None,
            "image_policy_verified_at": None,
            "image_content_hash": None,
            "image_subject_hash": None,
            "image_policy_version": None,
            "generated_at": now,
            "body_updated_at": now,
            "status": ContentStatus.DRAFT,
            "content_philosophy_id": philosophy.id,
            "generation_philosophy_id": philosophy.id,
            "last_reviewed_philosophy_id": philosophy.id,
            "essence_status": screening.status,
            "essence_check_summary": _generation_summary(
                db, hospital.id, screening, philosophy, approved_brief
            ),
        },
    )
    if written == 0:
        db.rollback()
        logger.info(
            "Discarding regenerated content for %s — status changed during generation", item.id
        )
        return GenerationItemState.DISCARDED, None, None
    db.commit()
    db.refresh(item)

    image_state = _recover_missing_content_image(db, item, hospital, philosophy)
    if image_state == GenerationItemState.PARTIAL:
        _persist_publication_readiness(db, item, philosophy)
        return (
            GenerationItemState.PARTIAL,
            "IMAGE_GENERATION_FAILED",
            "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다.",
        )
    if image_state == GenerationItemState.DISCARDED:
        return image_state, None, None
    readiness_failure = _persist_publication_readiness(db, item, philosophy)
    if readiness_failure is not None:
        _remember_generation_attempt(db, item, philosophy, readiness_failure[0])
        return GenerationItemState.FAILED, *readiness_failure
    return GenerationItemState.SUCCEEDED, None, None


def _persist_publication_readiness(
    db, item: ContentItem, philosophy: HospitalContentPhilosophy | None
) -> tuple[str, str] | None:
    """Persist the exact morning publication verdict immediately after generation."""
    assessment = assess_content_publication(item, philosophy)
    apply_publication_assessment(item, assessment)
    db.commit()
    if assessment.publishable:
        return None
    return (
        assessment.code or "GENERATION_FAILED",
        assessment.message or "자동 발행 준비 검사를 통과하지 못했습니다.",
    )


def _page_morning_stored_publication_gates(db, *, now_kst=None) -> int:
    """At 07:45, record the persisted blockers and summarize them in one Slack message.

    Every blocked slot keeps its own incident because the Admin retry controls act
    on incidents. Slack gets one digest per batch instead of one page per content
    item, and blockers the 01·04·07 recovery still owns are left out until 08:00.
    """

    observed = now_kst or arrow.now("Asia/Seoul")
    observed_time = observed.time().replace(tzinfo=None)
    if not MORNING_CLOSE_START <= observed_time < time(8, 0):
        return 0

    items = list(
        db.execute(
            select(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                auto_publish_due_predicate(observed.date()),
                publicly_operational_hospital_predicate(),
            )
            .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
            .options(joinedload(ContentItem.hospital))
        )
        .scalars()
        .all()
    )
    paged = 0
    blocked_outcomes: list[dict[str, object]] = []
    for item in items:
        hospital = item.hospital
        philosophy = get_current_approved_philosophy_sync(db, hospital.id)
        assessment = assess_content_publication(item, philosophy)
        if assessment.publishable:
            continue

        apply_publication_assessment(item, assessment)
        code, message = _publication_block_details(item, assessment)
        blocked_run = ensure_publication_block_run(
            db,
            item=item,
            hospital=hospital,
            code=code,
            message=message,
        )
        # The async incident transaction must be able to reference this run.
        db.commit()
        _run_async(
            open_generation_incident(
                item_id=item.id,
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                run_id=blocked_run.id,
                code=code,
                message=message,
                notify=False,
            )
        )
        if generation_block_digest_due(code, batch=PREPUBLISH_MORNING_BATCH):
            blocked_outcomes.append(
                {
                    "hospital_id": hospital.id,
                    "hospital_name": hospital.name,
                    "content_id": item.id,
                    "scheduled_date": str(item.scheduled_date),
                    "title": item.title,
                    "code": code,
                    "cause": generation_safe_cause(code),
                    "attempt_fingerprint": _stored_generation_attempt(item).get(
                        "context"
                    ),
                }
            )
        paged += 1
    if blocked_outcomes:
        enqueue_generation_blocked_digest_sync(
            db, observed.date(), PREPUBLISH_MORNING_BATCH, blocked_outcomes
        )
        db.commit()
    return paged


# ══════════════════════════════════════════════════════════════════
# 아침 자동 발행 + 예외 관제 (매일 08:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.morning_content_auto_publish",
    bind=True,
    max_retries=3,
)
def morning_content_auto_publish(self):
    """Publish verified content silently and summarize only exhausted blockers."""
    require_dispatch(self, "morning-content-auto-publish")
    today = arrow.now("Asia/Seoul").date()

    try:
        with SyncSessionLocal() as db:
            due_ids = list(db.execute(_auto_publish_due_stmt(today)).scalars().all())

        blocked_outcomes: list[dict[str, object]] = []
        for content_id in due_ids:
            outcome = _auto_publish_one(content_id)
            if outcome is None:
                continue
            if outcome["kind"] == "blocked":
                # The incident stays per item (it drives the Admin retry control);
                # Slack gets one digest for the whole batch below.
                _run_async(
                    open_generation_incident(
                        item_id=content_id,
                        hospital_id=outcome["hospital_id"],
                        hospital_name=outcome["hospital_name"],
                        run_id=outcome["run_id"],
                        code=outcome["code"],
                        message=outcome["message"],
                        notify=False,
                    )
                )
                if generation_block_digest_due(
                    outcome["code"], batch=PUBLISH_MORNING_BATCH
                ):
                    blocked_outcomes.append(
                        {
                            "hospital_id": outcome["hospital_id"],
                            "hospital_name": outcome["hospital_name"],
                            "content_id": content_id,
                            "scheduled_date": outcome.get("scheduled_date"),
                            "title": outcome.get("title"),
                            "code": outcome["code"],
                            "cause": generation_safe_cause(outcome["code"]),
                            "attempt_fingerprint": outcome.get("attempt_fingerprint"),
                        }
                    )
                continue

            _run_async(
                recover_generation_incidents(
                    content_id,
                    outcome["hospital_id"],
                    outcome["hospital_name"],
                    None,
                )
            )

            revalidated = _run_async(
                trigger_content_site_revalidate_safe(
                    outcome["slug"],
                    content_id,
                    hospital_name=outcome["hospital_name"],
                    treatments=outcome["treatments"],
                )
            )
            if not revalidated and settings.APP_ENV.lower() == "production":
                logger.warning("Auto-published content revalidation failed: %s", content_id)

        if blocked_outcomes:
            with SyncSessionLocal() as digest_db:
                enqueue_generation_blocked_digest_sync(
                    digest_db, today, PUBLISH_MORNING_BATCH, blocked_outcomes
                )
                digest_db.commit()

        # 정상 발행은 Slack을 아예 보내지 않는다 — DB 상태·감사 로그·공개 표면
        # 재검증이 기록이다. Slack에 나가는 것은 바로 위의 차단 요약 한 건뿐이며,
        # 그것도 07:45 복구를 넘겨 소진된 차단만 담는다
        # (docs/ops/slack-notification-policy.md).
    except Exception as exc:
        logger.exception("morning_content_auto_publish failed")
        raise self.retry(exc=exc, countdown=300)


def _auto_publish_due_stmt(today):
    return (
        select(ContentItem.id)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            auto_publish_due_predicate(today),
            publicly_operational_hospital_predicate(),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
    )


def _admin_content_url(hospital_id: object, content_id: object) -> str:
    return (
        f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{hospital_id}/content"
        f"?content={content_id}"
    )


def _auto_publish_one(content_id: uuid.UUID) -> dict | None:
    with SyncSessionLocal() as db:
        item = db.execute(
            select(ContentItem)
            .where(ContentItem.id == content_id)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if not item or item.status not in AUTO_PUBLISHABLE_STATUSES:
            return None
        today_kst = arrow.now("Asia/Seoul").date()
        if hasattr(item, "content_revision") and not (
            auto_publish_catchup_start(today_kst) <= item.scheduled_date <= today_kst
        ):
            # The candidate list is only a hint. A concurrent reschedule wins once
            # this row lock is held and the authoritative date is re-read.
            return None
        # 콘텐츠 검사와 동시에 병원이 PAUSED/비공개로 전환되는 경합을 막는다. 병원 행을
        # 같은 트랜잭션에서 잠근 뒤 ACTIVE/LIVE를 재확인해야 공개 중지 요청 이후 새 글이
        # 튀어나오는 TOCTOU가 없다.
        hospital = db.execute(
            select(Hospital).where(Hospital.id == item.hospital_id).with_for_update()
        ).scalar_one_or_none()
        if not hospital:
            return None
        if hospital.status != HospitalStatus.ACTIVE or not hospital.site_live:
            return None

        philosophy = get_current_approved_philosophy_sync(db, hospital.id)
        assessment = assess_content_publication(item, philosophy)
        apply_publication_assessment(item, assessment)
        admin_url = _admin_content_url(hospital.id, item.id)
        if not assessment.publishable:
            code, message = _publication_block_details(item, assessment)
            findings = assessment.essence_summary.get("findings") or []
            operator_reason = (
                str(findings[0])
                if findings
                else message
            )
            write_audit_log_sync(
                db,
                action="auto_publish_blocked",
                hospital_id=hospital.id,
                actor=AUTO_PUBLISH_ACTOR,
                target_type="content_item",
                target_id=item.id,
                detail={
                    "code": code,
                    "reason": message,
                    "scheduled_date": str(item.scheduled_date),
                },
            )
            blocked_run = ensure_publication_block_run(
                db,
                item=item,
                hospital=hospital,
                code=code,
                message=message,
            )
            db.commit()
            return {
                "kind": "blocked",
                "code": code,
                "message": message,
                "reason": operator_reason,
                "hospital_id": hospital.id,
                "hospital_name": hospital.name,
                "title": item.title,
                "scheduled_date": str(item.scheduled_date),
                "admin_url": admin_url,
                "run_id": blocked_run.id,
                "attempt_fingerprint": _stored_generation_attempt(item).get("context"),
            }

        # Publishing without a working cache invalidation path can leave a successful DB
        # transaction invisible. Check only after blocker projection so a missing body/image
        # still reaches Operations Center even when the revalidation dependency is unavailable.
        ensure_site_revalidate_configured()
        item.status = ContentStatus.PUBLISHED
        record_publication_identity(
            item,
            published_at=datetime.now(timezone.utc),
            published_by=AUTO_PUBLISH_ACTOR,
        )
        item.post_publish_notified_at = None
        item.post_publish_reviewed_at = None
        item.post_publish_reviewed_by = None
        write_audit_log_sync(
            db,
            action="auto_publish_content",
            hospital_id=hospital.id,
            actor=AUTO_PUBLISH_ACTOR,
            target_type="content_item",
            target_id=item.id,
            detail={
                "title": item.title,
                "content_type": item.content_type.value,
                "scheduled_date": str(item.scheduled_date),
                "essence_status": assessment.essence_status,
            },
        )
        if isinstance(item, ContentItem):
            indexnow.enqueue_content_published_sync(
                db,
                slug=hospital.slug,
                content_id=item.id,
                aeo_domain=hospital.aeo_domain,
                treatments=hospital.treatments,
                revision=int(getattr(item, "content_revision", 1) or 1),
            )
        payload = _publication_notification_payload(item, hospital)
        db.commit()
        return payload


def _publication_notification_payload(item: ContentItem, hospital: Hospital) -> dict:
    public_base = _public_site_url(hospital.aeo_domain, hospital.slug).rstrip("/")
    return {
        "kind": "published",
        "hospital_id": hospital.id,
        "hospital_name": hospital.name,
        "slug": hospital.slug,
        "aeo_domain": hospital.aeo_domain,  # IndexNow 제출 호스트 결정용
        "treatments": hospital.treatments,
        "title": item.title or "",
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "content_type": item.content_type.value,
        "scheduled_date": str(item.scheduled_date),
        "public_url": f"{public_base}/contents/{item.id}",
        "admin_url": _admin_content_url(hospital.id, item.id),
        "carried_over": bool(item.carried_over_from),
        "automatic_remediation_attempts": int(
            (item.essence_check_summary or {}).get("automatic_remediation_attempts", 0)
            if isinstance(item.essence_check_summary, dict)
            else 0
        ),
        "content_revision": int(getattr(item, "content_revision", 1) or 1),
    }


# ══════════════════════════════════════════════════════════════════
# AI 답변 언급률 측정
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.run_sov_for_hospital",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
    time_limit=2100,
)
def run_sov_for_hospital(
    self,
    hospital_id: str,
    measurement_mode: str | None = None,
    measurement_year: int | None = None,
    measurement_month: int | None = None,
):
    task_started_at = monotonic()
    reserved_units = 0
    try:
        require_dispatch(self, "run-sov", hospital_id)
        if not _operation_run_claimed_or_legacy(self):
            logger.info(
                "Skipping duplicate RUN_SOV delivery without OperationRun claim: hospital=%s",
                hospital_id,
            )
            return
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital or hospital.status not in (
                HospitalStatus.ACTIVE,
                HospitalStatus.PENDING_DOMAIN,
            ):
                return

            measurement_mode = measurement_mode or _sov_measurement_mode_from_operation_run(
                db, self
            )
            monthly = measurement_mode == "monthly"
            if monthly and not hospital_in_monthly_cohort(
                db,
                hospital.id,
                limit=settings.SOV_MONTHLY_COHORT_LIMIT,
            ):
                logger.info(
                    "Hospital %s is no longer in the monthly measurement cohort", hospital_id
                )
                return

            # priority 기반 쿼리 필터링 — beat은 월요일 02:00 KST(=일요일 UTC)에 발화하므로
            # UTC date.today()를 쓰면 ISO 주차 짝/홀이 뒤집히고 월초 판정도 어긋난다 (P1-5).
            now_kst = arrow.now("Asia/Seoul")
            today_kst = now_kst.date()
            week_key = _weekly_measurement_key(today_kst)
            if monthly:
                resolved_period = _resolve_monthly_measurement_period(
                    today_kst,
                    measurement_year,
                    measurement_month,
                    observed_at=now_kst.datetime,
                )
                if resolved_period is None:
                    _finish_sov_operation_run(
                        db,
                        self,
                        OperationRunState.FAILED,
                        "MONTHLY_SOV_OUTSIDE_RECOVERY_WINDOW",
                        "월간 측정 복구 기간이 아니어서 외부 호출을 시작하지 않았습니다.",
                    )
                    return
                period_year, period_month = resolved_period
                period_key = f"{period_year:04d}-{period_month:02d}"
            else:
                period_year, period_month = today_kst.year, today_kst.month
                period_key = week_key
            failure_prefix = "MONTHLY_SOV" if monthly else "WEEKLY_SOV"
            is_even_week = _is_even_measurement_week(today_kst)
            current_month_day = today_kst.day
            is_month_start = current_month_day <= 7  # 월초 첫째 주

            stmt = select(QueryMatrix).where(
                QueryMatrix.hospital_id == hospital.id,
                QueryMatrix.is_active,
            )
            result = db.execute(stmt)
            all_queries = result.scalars().all()
            target_result = db.execute(
                select(AIQueryTarget)
                .options(
                    selectinload(AIQueryTarget.variants).selectinload(
                        AIQueryVariant.query_matrix
                    )
                )
                .where(
                    AIQueryTarget.hospital_id == hospital.id,
                    AIQueryTarget.status == "ACTIVE",
                )
            )
            query_targets = target_result.scalars().all()

            if monthly:
                query_targets = tracking_set_members(query_targets)

            # priority 필터 적용 (HIGH 항상 / NORMAL 짝수주 / LOW 월초) — 동일 규칙을
            # target/variant 유래 spec에도 적용하기 위해 _priority_included 헬퍼로 단일화한다.
            queries = (
                []
                if monthly
                else [
                    q
                    for q in all_queries
                    if _priority_included(q.priority, is_even_week, is_month_start)
                ]
            )

            measurement_specs, trimmed_high = _build_measurement_specs(
                db=db,
                hospital=hospital,
                query_targets=query_targets,
                fallback_queries=queries,
                is_even_week=is_even_week,
                is_month_start=is_month_start,
                high_priority_cap=-1 if monthly else SOV_HIGH_PRIORITY_CAP,
                total_spec_cap=-1 if monthly else SOV_TOTAL_SPEC_CAP,
                measurement_mode=measurement_mode,
            )

            if trimmed_high and not monthly:
                # HIGH 상한 절단은 조용히 쿼리를 버리는 것과 같다 — 로그 + ops 알림 (P?-7).
                logger.warning(
                    "HIGH priority query cap reached for %s: %d specs trimmed (cap %d)",
                    hospital.name,
                    trimmed_high,
                    SOV_HIGH_PRIORITY_CAP,
                )
                _run_async(
                    open_weekly_sov_capacity_digest(
                        week_key=week_key,
                        operation_run_id=_operation_run_id_from_task(self),
                        hospital_name=hospital.name,
                        trimmed_count=trimmed_high,
                    )
                )

            if not measurement_specs:
                logger.info(
                    "No %s-eligible queries for hospital %s in %s",
                    measurement_mode,
                    hospital_id,
                    period_key,
                )
                _run_async(
                    _recover_sov_failure(
                        hospital_id=hospital.id,
                        period_key=period_key,
                        measurement_mode=measurement_mode,
                    )
                )
                return

            selected_weekly_specs = measurement_specs
            if monthly:
                frozen_specs = measurement_specs
                protocol_kwargs = {
                    "measurement_window": MEASUREMENT_WINDOW_MONTH_END,
                    "tracking_set_fingerprint": tracking_set_fingerprint(query_targets),
                    "tracking_set_size": len(query_targets),
                }
            else:
                frozen_specs, _ = _build_measurement_specs(
                    db=db,
                    hospital=hospital,
                    query_targets=query_targets,
                    fallback_queries=all_queries,
                    is_even_week=True,
                    is_month_start=True,
                    high_priority_cap=-1,
                    total_spec_cap=-1,
                )
                protocol_kwargs = None
            try:
                manifest = freeze_dispatch_manifest(
                    db,
                    hospital.id,
                    period_year,
                    period_month,
                    frozen_specs,
                    gemini_configured=bool(settings.GEMINI_API_KEY),
                    measurement_protocol_kwargs=protocol_kwargs,
                )
            except ManifestPolicyDrift as exc:
                logger.warning(
                    "Monthly measurement manifest drift blocks provider calls for hospital %s: %s",
                    hospital_id,
                    exc,
                )
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    error_code := f"{failure_prefix}_MEASUREMENT_POLICY_DRIFT",
                    _operation_run_id_from_task(self),
                    measurement_mode=measurement_mode,
                )
                _finish_sov_operation_run(
                    db,
                    self,
                    OperationRunState.FAILED,
                    error_code,
                    _sov_operation_error_message(error_code),
                )
                return
            except ManifestError:
                logger.info("No queries available to freeze for hospital %s", hospital_id)
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    error_code := f"{failure_prefix}_NO_MEASUREMENT_MANIFEST",
                    _operation_run_id_from_task(self),
                    measurement_mode=measurement_mode,
                )
                _finish_sov_operation_run(
                    db,
                    self,
                    OperationRunState.FAILED,
                    error_code,
                    _sov_operation_error_message(error_code),
                )
                return
            if monthly:
                reopen_incomplete_manifest_for_recovery(
                    db, manifest, now=datetime.now(timezone.utc)
                )
            db.commit()
            measurement_specs = _pending_weekly_manifest_specs(
                manifest, selected_weekly_specs
            )
            if not measurement_specs:
                logger.info("Monthly manifest has no pending cells for hospital %s", hospital_id)
                if _selected_weekly_manifest_is_resolved(
                    manifest, selected_weekly_specs
                ):
                    if monthly:
                        completed = _complete_monthly_measurement_and_dispatch_report(
                            db, self, hospital, manifest, period_year, period_month
                        )
                        if not completed:
                            return
                    _run_async(
                        _recover_sov_failure(
                            hospital_id=hospital.id,
                            period_key=period_key,
                            measurement_mode=measurement_mode,
                        )
                    )
                    return
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    error_code := f"{failure_prefix}_UNRESOLVED_MANIFEST_STATE",
                    _operation_run_id_from_task(self),
                    measurement_mode=measurement_mode,
                )
                _finish_sov_operation_run(
                    db,
                    self,
                    OperationRunState.FAILED,
                    error_code,
                    _sov_operation_error_message(error_code),
                )
                return

            if not _manifest_execution_policy_matches(manifest):
                logger.warning(
                    "Monthly measurement protocol drift blocks provider calls for hospital %s",
                    hospital_id,
                )
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    error_code := f"{failure_prefix}_MEASUREMENT_POLICY_DRIFT",
                    _operation_run_id_from_task(self),
                    measurement_mode=measurement_mode,
                )
                _finish_sov_operation_run(
                    db,
                    self,
                    OperationRunState.FAILED,
                    error_code,
                    _sov_operation_error_message(error_code),
                )
                return

            competitors = hospital.competitors or []
            run = _start_measurement_run(
                db,
                hospital,
                run_label=f"{measurement_mode}_sov_{today_kst.isoformat()}",
                config={
                    "source": "run_sov_for_hospital",
                    "measurement_mode": measurement_mode,
                    "repeat_count": SOV_REPEAT_WEEKLY,
                    "spec_count": len(measurement_specs),
                },
                measurement_protocol_kwargs=protocol_kwargs,
            )
            raw_run_config = getattr(run, "config", None)
            run_protocol = (
                raw_run_config.get("measurement_protocol")
                if isinstance(raw_run_config, dict)
                else None
            )
            slotted_execution = _manifest_slot_repeat_count(manifest) is not None
            if slotted_execution and not isinstance(run_protocol, dict):
                raise RuntimeError("measurement run protocol is missing")
            slots_by_cell: dict[uuid.UUID, list[MeasurementObservationSlot]] = {}
            if slotted_execution:
                for spec in measurement_specs:
                    slots_by_cell[spec["manifest_cell"].id] = ensure_monthly_slots(
                        db,
                        cell=spec["manifest_cell"],
                        hospital_id=hospital.id,
                        measurement_run_id=run.id,
                        repeat_count=SOV_REPEAT_WEEKLY,
                        protocol=run_protocol,
                    )
                # Persist the whole selected repeat plan before the first paid call.
                db.commit()
            success_count = 0
            failure_count = 0
            for spec_index, spec in enumerate(measurement_specs):
                if _sov_chunk_deadline_reached(task_started_at):
                    _finish_measurement_run(run, success_count, failure_count)
                    db.commit()
                    error_code = f"{failure_prefix}_MEASUREMENT_PARTIAL"
                    _record_weekly_sov_failure(
                        hospital,
                        period_key,
                        error_code,
                        _operation_run_id_from_task(self),
                        measurement_mode=measurement_mode,
                    )
                    _finish_sov_operation_run(
                        db,
                        self,
                        OperationRunState.PARTIAL,
                        error_code,
                        _sov_operation_error_message(error_code),
                    )
                    return

                if not slotted_execution:
                    # Explicit compatibility lane for manifests frozen before the
                    # repeat-slot contract. Their repeat identity cannot be backfilled.
                    reserved_units = SOV_REPEAT_WEEKLY
                    sov_decision = _run_async(
                        cost_guard.check_and_increment("sov", count=reserved_units)
                    )
                    if not sov_decision.allowed:
                        reserved_units = 0
                        _finish_measurement_run(run, success_count, failure_count)
                        db.commit()
                        error_code = f"{failure_prefix}_COST_GUARD_BLOCKED"
                        _record_weekly_sov_failure(
                            hospital,
                            period_key,
                            error_code,
                            _operation_run_id_from_task(self),
                            measurement_mode=measurement_mode,
                        )
                        _finish_sov_operation_run(
                            db,
                            self,
                            OperationRunState.FAILED,
                            error_code,
                            _sov_operation_error_message(error_code),
                        )
                        return
                    try:
                        results = _run_async(
                            run_single_query(
                                hospital.name,
                                spec["query_text"],
                                spec["platform"],
                                SOV_REPEAT_WEEKLY,
                                competitors=competitors,
                                region=(hospital.region or [""])[0],
                                hospital_id=hospital.id,
                            )
                        )
                    except Exception:
                        _run_async(cost_guard.release_reservation("sov", reserved_units))
                        reserved_units = 0
                        raise
                    reserved_units = 0
                    records = []
                    for result in results:
                        measurement_status, _reason = _measurement_status_for_result(result)
                        if measurement_status == "SUCCESS":
                            success_count += 1
                        else:
                            failure_count += 1
                        record = _build_sov_record_from_result(
                            hospital_id=hospital.id,
                            query_id=spec["query_id"],
                            measurement_run_id=run.id,
                            platform=spec["platform"],
                            result=result,
                            target_id=spec["target_id"],
                            variant_id=spec["variant_id"],
                        )
                        records.append(record)
                    db.add_all(records)
                    db.flush()
                    db.add_all(
                        [link_attempt(spec["manifest_cell"], record) for record in records]
                    )
                    run.query_count = success_count + failure_count
                    run.success_count = success_count
                    run.failure_count = failure_count
                    db.commit()
                    continue

                slots = slots_by_cell[spec["manifest_cell"].id]
                try:
                    for slot in slots:
                        _execute_paid_observation_slot(
                            db,
                            slot=slot,
                            hospital=hospital,
                            query_text=spec["query_text"],
                            competitors=competitors,
                            protocol=run_protocol,
                            target_id=spec["target_id"],
                            variant_id=spec["variant_id"],
                            monthly_cell=spec["manifest_cell"],
                        )
                except (SoftTimeLimitExceeded, DispatchAuthorizationError, WorkerLostError):
                    _finish_measurement_run(run, success_count, failure_count)
                    db.commit()
                    error_code = f"{failure_prefix}_MEASUREMENT_PARTIAL"
                    _record_weekly_sov_failure(
                        hospital,
                        period_key,
                        error_code,
                        _operation_run_id_from_task(self),
                        measurement_mode=measurement_mode,
                    )
                    _finish_sov_operation_run(
                        db,
                        self,
                        OperationRunState.PARTIAL,
                        error_code,
                        _sov_operation_error_message(error_code),
                    )
                    return

                confirmed = sum(slot.judgment_status == "CONFIRMED" for slot in slots)
                if confirmed:
                    spec["manifest_cell"].state = "SUCCESS"
                else:
                    spec["manifest_cell"].state = "FAILED"
                success_count += confirmed
                failure_count += len(slots) - confirmed
                run.query_count = success_count + failure_count
                run.success_count = success_count
                run.failure_count = failure_count
                db.commit()

                if (
                    spec_index + 1 < len(measurement_specs)
                    and _sov_chunk_deadline_reached(task_started_at)
                ):
                    _finish_measurement_run(run, success_count, failure_count)
                    db.commit()
                    error_code = f"{failure_prefix}_MEASUREMENT_PARTIAL"
                    _record_weekly_sov_failure(
                        hospital,
                        period_key,
                        error_code,
                        _operation_run_id_from_task(self),
                        measurement_mode=measurement_mode,
                    )
                    _finish_sov_operation_run(
                        db,
                        self,
                        OperationRunState.PARTIAL,
                        error_code,
                        _sov_operation_error_message(error_code),
                    )
                    return

            _finish_measurement_run(run, success_count, failure_count)
            db.commit()

            # 결과가 생긴 직후 노출 갭/보완 액션을 갱신한다. 대시보드 GET 요청이 우연히
            # 액션 생성을 일으키는 구조에 의존하지 않고 다음 콘텐츠 생성이 최신 결과를 읽는다.
            _refresh_exposure_actions_sync(hospital.id)
            if failure_count > 0 and not (
                monthly and _weekly_manifest_is_resolved(manifest)
            ):
                error_code = f"{failure_prefix}_MEASUREMENT_PARTIAL"
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    error_code,
                    _operation_run_id_from_task(self),
                    measurement_mode=measurement_mode,
                )
                _finish_sov_operation_run(
                    db,
                    self,
                    OperationRunState.PARTIAL,
                    error_code,
                    _sov_operation_error_message(error_code),
                )
                return
            if monthly:
                completed = _complete_monthly_measurement_and_dispatch_report(
                    db, self, hospital, manifest, period_year, period_month
                )
                if not completed:
                    return
            _run_async(
                _recover_sov_failure(
                    hospital_id=hospital.id,
                    period_key=period_key,
                    measurement_mode=measurement_mode,
                )
            )

    except DispatchAuthorizationError:
        with SyncSessionLocal() as db:
            _finish_sov_operation_run(
                db,
                self,
                OperationRunState.FAILED,
                "RUN_SOV_DISPATCH_AUTHORIZATION_FAILED",
                "인증된 작업 실행 시간이 지나 측정을 시작하지 못했습니다.",
            )
        return
    except (SoftTimeLimitExceeded, WorkerLostError):
        if reserved_units:
            _run_async(cost_guard.release_reservation("sov", reserved_units))
        with SyncSessionLocal() as db:
            _finish_sov_operation_run(
                db,
                self,
                OperationRunState.PARTIAL,
                "RUN_SOV_MEASUREMENT_PARTIAL",
                "측정 제한 시간 안에 완료하지 못한 항목이 남아 있습니다.",
            )
        return
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


def _sov_chunk_deadline_reached(started_at: float) -> bool:
    return monotonic() - started_at >= SOV_CHUNK_STOP_SECONDS


def _resolve_monthly_measurement_period(
    today_kst: date,
    measurement_year: int | None,
    measurement_month: int | None,
    *,
    observed_at: datetime | None = None,
) -> tuple[int, int] | None:
    if (measurement_year is None) != (measurement_month is None):
        return None
    if measurement_year is None or measurement_month is None:
        requested = (today_kst.year, today_kst.month)
    else:
        requested = (measurement_year, measurement_month)
    observed_at = observed_at or datetime.combine(
        today_kst, time.min, tzinfo=ZoneInfo("Asia/Seoul")
    )
    if today_kst.day >= settings.SOV_MONTHLY_WINDOW_START_DAY:
        return requested if requested == (today_kst.year, today_kst.month) else None
    if is_monthly_recovery_window(observed_at, *requested):
        return requested
    return None


def _complete_monthly_measurement_and_dispatch_report(
    db,
    task,
    hospital: Hospital,
    manifest: MonthlyMeasurementManifest,
    year: int,
    month: int,
) -> bool:
    """Close recovered coverage and enqueue one authenticated report rebuild."""

    observed_at = datetime.now(timezone.utc)
    period_key = f"{year:04d}-{month:02d}"
    coverage = summarize_manifest(
        manifest.cells,
        closed=True,
        configured_platforms=manifest.configured_platforms,
    )
    legacy_complete = (
        coverage.quality == "COMPLETE"
        and coverage.planned_count > 0
        and coverage.success_count == coverage.planned_count
        and coverage.failed_count == 0
        and coverage.excluded_count == 0
    )
    adequacy = _manifest_observation_adequacy(manifest, deadline_reached=True)
    finalized = (
        legacy_complete
        if adequacy is None
        else adequacy.planned_slots > 0
        and adequacy.status in {"COMPLETE", "LIMITED", "UNAVAILABLE"}
    )
    if not finalized:
        error_code = "MONTHLY_SOV_MEASUREMENT_INCOMPLETE"
        _record_weekly_sov_failure(
            hospital,
            period_key,
            error_code,
            _operation_run_id_from_task(task),
            measurement_mode="monthly",
        )
        _finish_sov_operation_run(
            db,
            task,
            OperationRunState.PARTIAL,
            error_code,
            _sov_operation_error_message(error_code),
        )
        return False
    if is_monthly_recovery_window(observed_at, year, month) and manifest.closed_at is None:
        close_manifest(manifest, now=observed_at)

    run_id = _operation_run_id_from_task(task)
    request = getattr(task, "request", None)
    worker_id = getattr(request, "id", None)
    claim_version = getattr(request, "operation_run_claim_version", None)
    if (
        run_id is not None
        and isinstance(worker_id, str)
        and worker_id.strip()
        and isinstance(claim_version, int)
        and not isinstance(claim_version, bool)
    ):
        current = db.get(OperationRun, run_id)
        summary = dict(getattr(current, "result_summary", None) or {})
        summary.update({
            "measurement_mode": "monthly",
            "measurement_month": period_key,
            "measurement_quality": (
                adequacy.status if adequacy is not None else "LEGACY_COMPLETE"
            ),
            "observation_adequacy": adequacy.to_payload() if adequacy is not None else None,
        })
        db.execute(
            update(OperationRun)
            .where(
                OperationRun.id == run_id,
                OperationRun.operation_type == "RUN_SOV",
                OperationRun.task_id == worker_id,
                OperationRun.state == OperationRunState.RUNNING,
                OperationRun.lease_owner == worker_id,
                OperationRun.version == claim_version,
            )
            .values(
                state=OperationRunState.SUCCEEDED,
                completed_at=observed_at,
                heartbeat_at=None,
                lease_owner=None,
                lease_expires_at=None,
                total_count=1,
                success_count=1,
                failure_count=0,
                skipped_count=0,
                result_summary=summary,
                safe_error_code=None,
                safe_error_message=None,
                version=OperationRun.version + 1,
            )
        )
    db.commit()
    if not is_monthly_recovery_window(observed_at, year, month):
        return True
    _dispatch_automatic_monthly_report_recovery(db, hospital, year, month)
    return True


def _monthly_report_quality_is_complete(report) -> bool:
    return (
        report is not None
        and report.quality == "COMPLETE"
        and report.planned_count > 0
        and report.success_count == report.planned_count
        and report.failed_count == 0
        and report.excluded_count == 0
    )


def _monthly_report_measurement_is_final(report) -> bool:
    if _monthly_report_quality_is_complete(report):
        return True
    raw_summary = getattr(report, "sov_summary", None) if report is not None else None
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    adequacy = summary.get("observation_adequacy")
    return isinstance(adequacy, dict) and adequacy.get("status") in {
        "LIMITED",
        "UNAVAILABLE",
    }


def _monthly_report_is_delivery_ready(db, report) -> bool:
    if report is None:
        return False
    manifest = (
        db.get(MonthlyMeasurementManifest, report.manifest_id)
        if report.manifest_id is not None
        else None
    )
    artifact = db.execute(
        select(MonthlyReportArtifact).where(
            MonthlyReportArtifact.report_id == report.id,
            MonthlyReportArtifact.audience == "DOCTOR",
        )
    ).scalar_one_or_none()
    return monthly_report_delivery_gate(report, manifest, artifact).ready


def _coverage_recovery_run_is_stale(run: OperationRun, observed_at: datetime) -> bool:
    """True when a non-terminal coverage-recovery run looks abandoned."""

    if run.state not in (
        OperationRunState.REQUESTED,
        OperationRunState.QUEUED,
        OperationRunState.RUNNING,
    ):
        return False
    if run.state == OperationRunState.RUNNING and run.lease_expires_at is not None:
        lease_expires_at = run.lease_expires_at
        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
        return lease_expires_at <= observed_at
    if run.state == OperationRunState.RUNNING:
        last_seen = run.heartbeat_at or run.started_at or run.queued_at or run.requested_at
    elif run.state == OperationRunState.QUEUED:
        last_seen = run.queued_at or run.requested_at
    else:
        last_seen = run.requested_at
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return last_seen <= observed_at - timedelta(hours=1)


def _dispatch_automatic_monthly_report_recovery(
    db, hospital: Hospital, year: int, month: int
) -> OperationRun | None:
    latest = _latest_monthly_report(db, hospital.id, year, month)
    report_final = _monthly_report_measurement_is_final(latest)
    if report_final and _has_valid_doctor_artifact(db, latest):
        return None
    idempotency_key = f"coverage-recovery:{hospital.id}:{year:04d}-{month:02d}"
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    hospital_id = str(hospital.id)
    task_args = (hospital_id, year, month, True, True)
    dispatch_payload = operation_run_payloads.build_request_payload(
        operation_run_payloads.DispatchPayload(
            "hospital", hospital_id, "reports", task_args
        )
    )
    observed_at = datetime.now(timezone.utc)

    def _enqueue(run: OperationRun) -> OperationRun:
        try:
            generate_monthly_report_for_hospital.apply_async(
                args=list(task_args),
                queue="reports",
                headers={
                    **build_dispatch_headers("generate-monthly-report", hospital_id),
                    "operation_run_id": str(run.id),
                },
                task_id=run.task_id,
            )
        except Exception:  # noqa: BLE001 - REQUESTED run is recovered by the workflow reconciler.
            logger.exception(
                "Automatic monthly report recovery dispatch failed; reconciler will retry",
                extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
            )
            return run
        _mark_weekly_sov_operation_queued(db, run.id, datetime.now(timezone.utc))
        return run

    def _rearm_existing(run: OperationRun) -> OperationRun:
        # 같은 coverage-recovery 키를 재사용한다. FAILED/PARTIAL 이후 remasure가
        # COMPLETE여도 리포트가 없으면 새 OperationRun을 만들지 않고 재무장한다.
        run.state = OperationRunState.REQUESTED
        run.task_id = str(uuid.uuid4())
        run.requested_at = observed_at
        run.queued_at = None
        run.started_at = None
        run.completed_at = None
        run.heartbeat_at = None
        run.lease_owner = None
        run.lease_expires_at = None
        run.total_count = 1
        run.success_count = 0
        run.failure_count = 0
        run.skipped_count = 0
        run.safe_error_code = None
        run.safe_error_message = None
        run.request_payload = dispatch_payload
        run.result_summary = {"period_year": year, "period_month": month}
        run.version += 1
        db.commit()
        return _enqueue(run)

    if existing is not None:
        if existing.state in (
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        ) and not _coverage_recovery_run_is_stale(existing, observed_at):
            return existing
        return _rearm_existing(existing)

    task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        task_id=task_id,
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=dispatch_payload,
        result_summary={"period_year": year, "period_month": month},
        version=1,
    )
    db.add(run)
    db.commit()
    return _enqueue(run)


def _sov_operation_error_message(error_code: str) -> str:
    suffix = error_code.rsplit("_SOV_", 1)[-1]
    messages = {
        "COST_GUARD_BLOCKED": "비용 한도를 초과해 AI 검색 노출 측정을 진행하지 못했습니다.",
        "NO_MEASUREMENT_MANIFEST": "활성 측정 질문이나 대상이 없어 측정을 시작하지 못했습니다.",
        "UNRESOLVED_MANIFEST_STATE": "완료·제외·재측정 대상으로 분류되지 않은 측정 항목이 남아 있습니다.",
        "MEASUREMENT_POLICY_DRIFT": "동결한 측정 기준과 현재 실행 기준이 달라 외부 호출을 시작하지 않았습니다.",
        "MEASUREMENT_PARTIAL": "일부 AI 검색 서비스 측정이 완료되지 않았습니다.",
    }
    return messages.get(suffix, "AI 검색 노출 측정이 완료되지 않았습니다.")


def _finish_sov_operation_run(
    db,
    task,
    state: OperationRunState,
    safe_error_code: str,
    safe_error_message: str,
) -> uuid.UUID | None:
    """태스크 본문이 소유한 RUN_SOV 실행을 CAS로 종결한다."""
    run_id = _operation_run_id_from_task(task)
    request = getattr(task, "request", None)
    worker_id = getattr(request, "id", None)
    claim_version = getattr(request, "operation_run_claim_version", None)
    if (
        run_id is None
        or not isinstance(worker_id, str)
        or not worker_id.strip()
        or not isinstance(claim_version, int)
        or isinstance(claim_version, bool)
    ):
        return None
    result = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.task_id == worker_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == worker_id,
            OperationRun.version == claim_version,
        )
        .values(
            state=state,
            completed_at=datetime.now(timezone.utc),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            total_count=1,
            success_count=0,
            failure_count=1,
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    ).scalar_one_or_none()
    db.commit()
    return result


def _operation_run_id_from_task(task) -> uuid.UUID | None:
    headers = getattr(getattr(task, "request", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    value = headers.get("operation_run_id")
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _sov_measurement_mode_from_operation_run(db, task) -> str:
    run_id = _operation_run_id_from_task(task)
    if run_id is None:
        return "weekly"
    run = db.get(OperationRun, run_id)
    summary = getattr(run, "result_summary", None)
    if isinstance(summary, dict) and summary.get("measurement_mode") == "monthly":
        return "monthly"
    return "weekly"


def _operation_run_claimed_or_legacy(task) -> bool:
    """Run only legacy tasks or tasks that OperationRun signals actually claimed."""

    if _operation_run_id_from_task(task) is None:
        return True
    claim_version = getattr(getattr(task, "request", None), "operation_run_claim_version", None)
    return isinstance(claim_version, int)


def _record_weekly_sov_failure(
    hospital: Hospital,
    week_key: str,
    error_code: str,
    operation_run_id: uuid.UUID | None,
    *,
    measurement_mode: str = "weekly",
) -> None:
    if measurement_mode == "monthly":
        coroutine = open_monthly_sov_failure(
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            period_key=week_key,
            error_code=error_code,
            operation_run_id=operation_run_id,
        )
    else:
        coroutine = open_weekly_sov_failure(
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            week_key=week_key,
            error_code=error_code,
            operation_run_id=operation_run_id,
        )
    _run_async(coroutine)


def _recover_sov_failure(
    *, hospital_id: uuid.UUID, period_key: str, measurement_mode: str
):
    if measurement_mode == "monthly":
        return recover_monthly_sov_failure(
            hospital_id=hospital_id,
            period_key=period_key,
        )
    return recover_weekly_sov_failure(hospital_id=hospital_id, week_key=period_key)


def _sov_spec_identity(spec: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(spec.get("query_id") or ""),
        _normalize_platform(str(spec.get("platform") or "")),
        str(spec.get("target_id") or ""),
    )


def _sov_spec_identities_match(
    left: tuple[str, str, str], right: tuple[str, str, str]
) -> bool:
    left_query, left_platform, left_target = left
    right_query, right_platform, right_target = right
    return (
        bool(left_query)
        and left_query == right_query
        and left_platform == right_platform
        and (not left_target or not right_target or left_target == right_target)
    )


def _manifest_slot_repeat_count(manifest) -> int | None:
    provenance = getattr(manifest, "platform_provenance", None)
    contract = provenance.get("observation_slots") if isinstance(provenance, dict) else None
    repeat_count = contract.get("repeat_count") if isinstance(contract, dict) else None
    if (
        isinstance(repeat_count, int)
        and not isinstance(repeat_count, bool)
        and repeat_count > 0
    ):
        return repeat_count
    return None


def _manifest_cell_slots_resolved(cell, repeat_count: int) -> bool:
    slots = list(getattr(cell, "observation_slots", ()) or ())
    return (
        len(slots) == repeat_count
        and {slot.repeat_no for slot in slots} == set(range(1, repeat_count + 1))
        and all(slot_is_terminal(slot) for slot in slots)
    )


def _manifest_observation_adequacy(manifest, *, deadline_reached: bool):
    repeat_count = _manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return None
    planned_cells = [
        cell
        for cell in (getattr(manifest, "cells", ()) or ())
        if cell.state != "EXCLUDED"
    ]
    if not planned_cells or any(
        len(list(getattr(cell, "observation_slots", ()) or ())) != repeat_count
        for cell in planned_cells
    ):
        return None
    return summarize_observation_slots(
        (
            slot
            for cell in planned_cells
            for slot in (getattr(cell, "observation_slots", ()) or ())
        ),
        deadline_reached=deadline_reached,
    )


def _pending_weekly_manifest_specs(manifest, selected_specs: list[dict]) -> list[dict]:
    """Reconnect selected specs to unresolved frozen cells without widening the cap."""

    selected = [_sov_spec_identity(spec) for spec in selected_specs]
    repeat_count = _manifest_slot_repeat_count(manifest)
    pending: list[dict] = []
    for cell in getattr(manifest, "cells", ()) or ():
        spec = {
            "query_id": cell.query_matrix_id,
            "query_text": cell.query_text,
            "platform": cell.platform,
            "target_id": cell.query_target_id,
            "variant_id": cell.query_variant_id,
            "manifest_cell": cell,
        }
        identity = _sov_spec_identity(spec)
        needs_work = (
            cell.state == "FAILED"
            if repeat_count is None
            else cell.state != "EXCLUDED"
            and not _manifest_cell_slots_resolved(cell, repeat_count)
        )
        if needs_work and any(
            _sov_spec_identities_match(identity, chosen) for chosen in selected
        ):
            pending.append(spec)
    return pending


def _selected_weekly_manifest_is_resolved(manifest, selected_specs: list[dict]) -> bool:
    cells = list(getattr(manifest, "cells", ()) or ())
    selected = [_sov_spec_identity(spec) for spec in selected_specs]
    repeat_count = _manifest_slot_repeat_count(manifest)
    if not selected:
        return False
    for chosen in selected:
        matching = [
            cell
            for cell in cells
            if _sov_spec_identities_match(
                _sov_spec_identity(
                    {
                        "query_id": cell.query_matrix_id,
                        "platform": cell.platform,
                        "target_id": cell.query_target_id,
                    }
                ),
                chosen,
            )
        ]
        if not matching:
            return False
        if repeat_count is None:
            if any(cell.state not in {"SUCCESS", "EXCLUDED"} for cell in matching):
                return False
        elif any(
            cell.state != "EXCLUDED"
            and not _manifest_cell_slots_resolved(cell, repeat_count)
            for cell in matching
        ):
            return False
    return True


def _weekly_manifest_is_resolved(manifest) -> bool:
    cells = list(getattr(manifest, "cells", ()) or ())
    if not cells:
        return False
    repeat_count = _manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        return all(getattr(cell, "state", None) in {"SUCCESS", "EXCLUDED"} for cell in cells)
    return all(
        cell.state == "EXCLUDED" or _manifest_cell_slots_resolved(cell, repeat_count)
        for cell in cells
    )


def _manifest_execution_policy_matches(manifest) -> bool:
    provenance = getattr(manifest, "platform_provenance", None)
    snapshot = provenance.get("measurement_protocol") if isinstance(provenance, dict) else None
    platforms = tuple(getattr(manifest, "configured_platforms", ()) or ())
    return bool(platforms) and sov_engine.same_execution_policy(
        snapshot,
        sov_engine.measurement_protocol(),
        platforms=platforms,
    )


def _start_measurement_run(
    db,
    hospital: Hospital,
    *,
    run_label: str,
    config: dict,
    measurement_protocol_kwargs: dict | None = None,
) -> MeasurementRun:
    now = datetime.now(timezone.utc)
    # 실제 호출 모드를 라벨에 정확히 반영. UI/리포트가 "ChatGPT 답변 노출률"이라고 잘못
    # 표기하던 컴플라이언스 이슈를 코드 수준에서 차단.
    chatgpt_method = (
        "OPENAI_RESPONSES_WEB_SEARCH"
        if settings.OPENAI_CHATGPT_USE_WEB_SEARCH
        else "OPENAI_CHAT_COMPLETIONS"
    )
    chatgpt_search_mode = "web" if settings.OPENAI_CHATGPT_USE_WEB_SEARCH else "model"
    run = MeasurementRun(
        hospital_id=hospital.id,
        run_label=run_label,
        measurement_method=chatgpt_method,
        status="RUNNING",
        query_count=0,
        success_count=0,
        failure_count=0,
        started_at=now,
        # model_name 단일 컬럼은 ChatGPT 측정 모델 기준 — Gemini 레코드까지 OpenAI 모델로
        # 기록되던 문제를 막기 위해 플랫폼별 모델은 config.model_names에 정확히 남긴다 (P2-17).
        model_name=settings.OPENAI_MODEL_QUERY,
        search_mode=chatgpt_search_mode,
        config={
            **config,
            "openai_use_web_search": settings.OPENAI_CHATGPT_USE_WEB_SEARCH,
            "gemini_grounded": bool(settings.GEMINI_API_KEY),
            "model_names": {
                "chatgpt": settings.OPENAI_MODEL_QUERY,
                **({"gemini": settings.GEMINI_MODEL} if settings.GEMINI_API_KEY else {}),
            },
            # 실행 시점 측정 정책 — 이 run의 숫자가 어떤 조건에서 나왔는지 남긴다.
            "measurement_protocol": sov_engine.measurement_protocol(
                **(measurement_protocol_kwargs or {})
            ),
        },
    )
    db.add(run)
    db.flush()
    return run


def _finish_measurement_run(
    run: MeasurementRun,
    success_count: int,
    failure_count: int,
    *,
    error_summary: dict[str, Any] | None = None,
) -> None:
    total = success_count + failure_count
    run.query_count = total
    run.success_count = success_count
    run.failure_count = failure_count
    run.completed_at = datetime.now(timezone.utc)
    if total == 0:
        run.status = "FAILED"
        run.error_summary = {"reason": "no_measurements"}
    elif failure_count == 0:
        run.status = "COMPLETED"
    elif success_count == 0:
        run.status = "FAILED"
        run.error_summary = error_summary or {"failed_count": failure_count}
    else:
        run.status = "PARTIAL"
        run.error_summary = error_summary or {"failed_count": failure_count}


def _measurement_status_for_result(result: dict) -> tuple[str, str | None]:
    explicit = str(result.get("measurement_status") or "").strip().upper()
    if explicit == "FAILED":
        return "FAILED", str(result.get("failure_reason") or "measurement_failed")
    if explicit == "SUCCESS":
        if (result.get("raw_response") or "").strip():
            return "SUCCESS", None
        return "FAILED", "empty_raw_response"
    if explicit:
        return "FAILED", "invalid_measurement_status"
    if (result.get("raw_response") or "").strip():
        return "SUCCESS", None
    return "FAILED", "empty_raw_response"


def _build_sov_record_from_result(
    *,
    hospital_id: uuid.UUID,
    query_id: uuid.UUID,
    measurement_run_id: uuid.UUID,
    platform: str,
    result: dict,
    target_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
) -> SovRecord:
    measurement_status, failure_reason = _measurement_status_for_result(result)
    return SovRecord(
        hospital_id=hospital_id,
        query_id=query_id,
        measurement_run_id=measurement_run_id,
        ai_query_target_id=target_id,
        ai_query_variant_id=variant_id,
        ai_platform=platform,
        # bool()로 감싸지 않는다 — AMBIGUOUS의 None이 False로 접히면 판정 보류가
        # 조용히 '미언급'이 되어 분모에 남는다.
        mention_verdict=result.get("verdict"),
        is_mentioned=result.get("is_mentioned"),
        mention_rank=result.get("mention_rank"),
        mention_sentiment=result.get("sentiment"),
        mention_context=result.get("mention_context"),
        raw_response=result.get("raw_response") or "",
        competitor_mentions=result.get("competitor_mentions"),
        measurement_method=result.get("measurement_method"),
        answer_model=result.get("answer_model"),
        search_calls=result.get("search_calls"),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        measurement_status=measurement_status,
        failure_reason=failure_reason,
        source_urls=result.get("source_urls") or [],
    )


def _result_from_sov_record(record: SovRecord) -> dict[str, Any]:
    return {
        "verdict": record.mention_verdict,
        "is_mentioned": record.is_mentioned,
        "mention_rank": record.mention_rank,
        "sentiment": record.mention_sentiment,
        "mention_context": record.mention_context,
        "raw_response": record.raw_response or "",
        "competitor_mentions": record.competitor_mentions,
        "measurement_method": record.measurement_method,
        "answer_model": record.answer_model,
        "search_calls": record.search_calls,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "measurement_status": record.measurement_status,
        "failure_reason": record.failure_reason,
        "source_urls": list(record.source_urls or []),
    }


def _execute_paid_observation_slot(
    db,
    *,
    slot: MeasurementObservationSlot,
    hospital: Hospital,
    hospital_name: str | None = None,
    region: str | None = None,
    query_text: str,
    competitors: list[str],
    protocol: Mapping[str, Any],
    target_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    monthly_cell=None,
) -> dict[str, Any]:
    """Resume exactly one paid answer/judgment slot and checkpoint each stage."""
    judgment_hospital_name = hospital_name or hospital.name
    judgment_region = region if region is not None else (hospital.region or [""])[0]
    if slot_needs_answer(slot):
        claim = claim_slot_stage(db, slot.id, stage="ANSWER")
        if claim is None:
            return {"measurement_status": "FAILED", "failure_reason": "slot_lease_active"}
        slot, lease_token = claim
        slot_id = slot.id
        answer_attempt = slot.answer_attempt_count
        answer_reservation = 1
        decision = _run_async(
            cost_guard.reserve(
                "sov",
                count=answer_reservation,
                reservation_id=f"measurement:{slot_id}:answer:{answer_attempt}",
            )
        )
        if not decision.allowed:
            db.rollback()
            return {
                "measurement_status": "FAILED",
                "failure_reason": "cost_guard_blocked",
            }
        db.commit()
        try:
            answer = _run_async(
                fetch_answer(
                    query_text,
                    slot.platform,
                    requested_model=(
                        protocol.get("openai_model_query")
                        if slot.platform == "chatgpt"
                        else protocol.get("gemini_model")
                    ),
                    hospital_id=hospital.id,
                    workflow=f"{slot.scope.lower()}_sov_answer",
                    run_id=str(slot.measurement_run_id),
                    item_id=str(slot.id),
                    attempt_id=str(answer_attempt),
                )
            )
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:  # provider may have accepted the request; keep reservation
            answer = {
                "measurement_status": "FAILED",
                "failure_reason": f"answer_exception:{type(exc).__name__}",
                "provider_calls": answer_reservation,
            }
        answer_calls = max(0, int(answer.get("provider_calls") or 0))
        _run_async(
            cost_guard.settle_reservation(
                decision.receipt,
                consumed_units=min(answer_calls, answer_reservation),
            )
        )
        checkpointed = checkpoint_answer(
            db, slot_id, answer, lease_token=lease_token
        )
        if checkpointed is None:
            db.rollback()
            return {
                "measurement_status": "FAILED",
                "failure_reason": "stale_slot_answer_discarded",
            }
        slot = checkpointed
        db.commit()
        if slot.answer_status != "RECEIVED":
            return dict(answer)

    # A slot whose answer claim budget was exhausted is a terminal unavailable
    # observation. It has no judgment artifact or fingerprint to reuse.
    if slot.answer_status != "RECEIVED":
        return {
            "measurement_status": "FAILED",
            "failure_reason": slot.answer_failure_reason or "answer_attempts_exhausted",
        }

    fingerprint = judgment_input_fingerprint(
        hospital_identity=str(hospital.id),
        hospital_name=judgment_hospital_name,
        response_text=slot.raw_response or "",
        region=judgment_region,
        competitors=competitors,
        policy=protocol,
    )
    if slot_is_terminal(slot):
        if slot.judgment_input_fingerprint != fingerprint:
            raise RuntimeError("measurement slot judgment input changed")
        record = db.get(SovRecord, slot.sov_record_id) if slot.sov_record_id else None
        if record is None:
            raise RuntimeError("terminal measurement slot has no result record")
        return _result_from_sov_record(record)
    if not slot_needs_judgment(slot):
        return {
            **(answer_artifact(slot) if slot.answer_status == "RECEIVED" else {}),
            "measurement_status": "FAILED",
            "failure_reason": slot.judgment_failure_reason or slot.answer_failure_reason,
        }

    judgment_reservation = sov_engine.estimate_judgment_provider_calls(
        judgment_hospital_name,
        slot.raw_response or "",
        competitors=competitors,
    )
    claim = claim_slot_stage(db, slot.id, stage="JUDGMENT")
    if claim is None:
        return {"measurement_status": "FAILED", "failure_reason": "slot_lease_active"}
    slot, lease_token = claim
    slot_id = slot.id
    judgment_attempt = slot.judgment_attempt_count
    decision = _run_async(
        cost_guard.reserve(
            "sov",
            count=judgment_reservation,
            reservation_id=f"measurement:{slot_id}:judgment:{judgment_attempt}",
        )
    )
    if not decision.allowed:
        db.rollback()
        return {
            **answer_artifact(slot),
            "measurement_status": "FAILED",
            "failure_reason": "cost_guard_blocked",
        }
    db.commit()
    try:
        judgment = _run_async(
            judge_answer(
                judgment_hospital_name,
                slot.raw_response or "",
                region=judgment_region,
                competitors=competitors,
                hospital_identity=str(hospital.id),
                hospital_id=hospital.id,
                workflow=f"{slot.scope.lower()}_sov_judgment",
                run_id=str(slot.measurement_run_id),
                item_id=str(slot.id),
                attempt_id=str(judgment_attempt),
                policy=protocol,
            )
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # retain answer; only the judgment stage failed
        judgment = {
            "measurement_status": "FAILED",
            "failure_reason": f"judgment_exception:{type(exc).__name__}",
            "provider_calls": judgment_reservation,
        }
    judgment_calls = max(0, int(judgment.get("provider_calls") or 0))
    _run_async(
        cost_guard.settle_reservation(
            decision.receipt,
            consumed_units=min(judgment_calls, judgment_reservation),
        )
    )
    result = {**answer_artifact(slot), **judgment}
    record = _build_sov_record_from_result(
        hospital_id=hospital.id,
        query_id=slot.query_id,
        measurement_run_id=slot.measurement_run_id,
        platform=slot.platform,
        result=result,
        target_id=target_id,
        variant_id=variant_id,
    )
    if slot.answered_at is not None:
        record.measured_at = slot.answered_at
    checkpointed = checkpoint_judgment(
        db,
        slot_id,
        fingerprint=fingerprint,
        result=judgment,
        sov_record=record,
        lease_token=lease_token,
    )
    if checkpointed is None:
        db.rollback()
        return {
            "measurement_status": "FAILED",
            "failure_reason": "stale_slot_judgment_discarded",
        }
    slot, judgment_status = checkpointed
    if monthly_cell is not None and judgment_status in {"CONFIRMED", "AMBIGUOUS"}:
        db.add(link_attempt(monthly_cell, record))
    db.commit()
    return result


def _priority_included(priority: str | None, is_even_week: bool, is_month_start: bool) -> bool:
    """priority 기반 주간 측정 게이팅 규칙.

    HIGH: 매주 포함 / LOW: 월초(첫째 주)만 / 그 외(NORMAL 등): 짝수 주차만.
    QueryMatrix.priority와 AIQueryTarget.priority에 동일 규칙을 적용해 스로틀링을 단일화한다.
    """
    normalized = str(priority or "NORMAL").upper()
    if normalized == "HIGH":
        return True
    if normalized == "LOW":
        return is_month_start
    return is_even_week


def _apply_high_priority_cap(specs: list[dict], cap: int) -> tuple[list[dict], int]:
    """HIGH 우선순위 spec을 상한까지만 유지하고 초과분은 잘라낸다 (결정론적: 앞에서부터 유지).

    Returns: (유지된 specs, 잘린 HIGH spec 개수).
    """
    if cap < 0:
        return specs, 0
    kept: list[dict] = []
    high_seen = 0
    dropped = 0
    for spec in specs:
        if str(spec.get("priority") or "NORMAL").upper() == "HIGH":
            if high_seen >= cap:
                dropped += 1
                continue
            high_seen += 1
        kept.append(spec)
    return kept, dropped


def _apply_total_spec_cap(specs: list[dict], cap: int) -> list[dict]:
    """Bound HIGH/NORMAL/LOW combined while preserving deterministic priority order."""

    if cap < 0:
        return specs
    return specs[:cap]


def _build_measurement_specs(
    *,
    db,
    hospital: Hospital,
    query_targets: list[AIQueryTarget],
    fallback_queries: list[QueryMatrix],
    is_even_week: bool = True,
    is_month_start: bool = True,
    high_priority_cap: int = SOV_HIGH_PRIORITY_CAP,
    total_spec_cap: int = SOV_TOTAL_SPEC_CAP,
    measurement_mode: str = "weekly",
) -> tuple[list[dict], int]:
    """주간 측정 spec 목록을 만든다.

    target/variant 유래 spec도 fallback 쿼리와 동일하게 target.priority 기준으로 게이팅한다
    (V0 후 target 자동 시드로 인해 스로틀링이 죽는 문제 방지). 마지막에 HIGH 상한과
    전체 상한을 적용해 NORMAL/LOW도 무제한 확장되지 않게 한다.
    Returns: (specs, 잘린 HIGH spec 개수).
    """
    specs: list[dict] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    priority_rank = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
    sorted_targets = sorted(
        query_targets,
        key=lambda target: (
            priority_rank.get(str(getattr(target, "priority", "NORMAL")).upper(), 9),
            str(getattr(target, "target_month", "") or ""),
            str(getattr(target, "name", "") or ""),
            str(getattr(target, "id", "")),
        ),
    )
    for target in sorted_targets:
        target_priority = str(getattr(target, "priority", "NORMAL") or "NORMAL").upper()
        if measurement_mode != "monthly" and not _priority_included(
            target_priority, is_even_week, is_month_start
        ):
            continue
        active_variants = sorted(
            [variant for variant in target.variants if variant.is_active],
            key=lambda variant: (
                _normalize_platform(variant.platform),
                str(variant.query_text),
                str(variant.id),
            ),
        )
        for variant in active_variants:
            platform = _normalize_platform(variant.platform)
            if platform == "gemini" and not settings.GEMINI_API_KEY:
                continue
            query = _ensure_variant_query_matrix(db, hospital, variant)
            query_intent = str(getattr(query, "query_intent", "LOCAL") or "LOCAL").upper()
            if query_intent == sov_engine.QUERY_INTENT_INFO:
                continue
            key = (query.id, platform)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "query_id": query.id,
                    "query_text": variant.query_text,
                    "platform": platform,
                    "target_id": target.id,
                    "variant_id": variant.id,
                    "priority": target_priority,
                    "query_intent": query_intent,
                }
            )

    if specs:
        if measurement_mode == "monthly":
            return specs, 0
        capped, trimmed_high = _apply_high_priority_cap(specs, high_priority_cap)
        return _apply_total_spec_cap(capped, total_spec_cap), trimmed_high

    platforms = ["chatgpt"]
    if settings.GEMINI_API_KEY:
        platforms.append("gemini")
    sorted_fallback_queries = sorted(
        fallback_queries,
        key=lambda query: (
            priority_rank.get(
                str(getattr(query, "priority", "NORMAL") or "NORMAL").upper(), 9
            ),
            str(getattr(query, "query_text", "") or ""),
            str(getattr(query, "id", "") or ""),
        ),
    )
    for query in sorted_fallback_queries:
        query_intent = str(getattr(query, "query_intent", "LOCAL") or "LOCAL").upper()
        if query_intent == sov_engine.QUERY_INTENT_INFO:
            continue
        for platform in platforms:
            specs.append(
                {
                    "query_id": query.id,
                    "query_text": query.query_text,
                    "platform": platform,
                    "target_id": None,
                    "variant_id": None,
                    "priority": str(getattr(query, "priority", "NORMAL") or "NORMAL").upper(),
                    "query_intent": query_intent,
                }
            )
    capped, trimmed_high = _apply_high_priority_cap(specs, high_priority_cap)
    return _apply_total_spec_cap(capped, total_spec_cap), trimmed_high


def _ensure_variant_query_matrix(db, hospital: Hospital, variant: AIQueryVariant) -> QueryMatrix:
    if variant.query_matrix_id:
        query = db.get(QueryMatrix, variant.query_matrix_id)
        if query and query.hospital_id == hospital.id:
            return query

    # variant 질문은 템플릿을 거치지 않고 들어오므로 유형을 텍스트에서 되짚는다.
    # 여기서 빠뜨리면 AE가 등록한 정보성 질문("무릎 통증 초기 증상")이 LOCAL로 들어가
    # 언급률 분모를 다시 희석한다 — 분모 분리가 조용히 무력화되는 지점이다.
    query = QueryMatrix(
        hospital_id=hospital.id,
        query_text=variant.query_text,
        query_intent=classify_query_intent(variant.query_text),
        priority="HIGH",
    )
    db.add(query)
    db.flush()
    variant.query_matrix_id = query.id
    return query


def _seed_query_targets_from_matrix_sync(hospital_id: uuid.UUID) -> None:
    """V0 완료 후 QueryMatrix → AIQueryTarget 시드 + 노출 보완 큐 생성.

    V0 리포트가 이미 커밋된 뒤에 실행되는 post-commit 사이드 이펙트다.
    실패해도 V0 결과를 건드리지 않고 로그만 남긴다.

    exposure_action_engine은 AsyncSession만 지원하므로 별도 async 루프로 실행한다.
    """
    try:
        from app.api.admin.query_targets import seed_query_targets_from_matrix
        from app.core.database import get_async_sessionmaker
        from app.services.exposure_action_engine import ensure_hospital_exposure_actions

        async def _run(h_id: uuid.UUID) -> None:
            async with get_async_sessionmaker()() as async_db:
                await seed_query_targets_from_matrix(async_db, h_id)
                await ensure_hospital_exposure_actions(async_db, h_id)

        _run_async(_run(hospital_id))
        logger.info(
            "V0 post-seed: query_targets seeded and exposure_actions populated for hospital=%s",
            hospital_id,
        )
    except Exception:
        logger.exception(
            "V0 post-seed failed (non-fatal, V0 report already committed): hospital=%s",
            hospital_id,
        )


def _refresh_exposure_actions_sync(hospital_id: uuid.UUID) -> None:
    try:
        from app.core.database import get_async_sessionmaker
        from app.services.exposure_action_engine import ensure_hospital_exposure_actions

        async def _run(h_id: uuid.UUID) -> None:
            async with get_async_sessionmaker()() as async_db:
                await ensure_hospital_exposure_actions(async_db, h_id)

        _run_async(_run(hospital_id))
    except Exception:
        # 측정 레코드는 이미 커밋됐다. 파생 작업 실패로 원 측정을 재실행해 비용·중복을
        # 만들지 않고 다음 주/운영 복구에서 따라잡게 한다.
        logger.exception(
            "exposure_actions refresh failed after measurement: hospital=%s",
            hospital_id,
        )


def _normalize_platform(platform: str) -> str:
    value = (platform or "CHATGPT").strip().lower()
    if value in {"gemini", "google"}:
        return "gemini"
    return "chatgpt"


# ══════════════════════════════════════════════════════════════════
# 다음 달 콘텐츠 슬롯 자동 생성 (매월 25일 00:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.monthly_slot_generation",
    soft_time_limit=1200,
    time_limit=1500,
)
def monthly_slot_generation(current_month: bool = False):
    """Reconcile the current contract daily and prepare next month after the 25th."""
    require_dispatch(current_task, "monthly-slot-generation")
    today = arrow.now("Asia/Seoul")
    if not current_month and today.day < 25:
        logger.info("Next-month slot reconciliation is not due: %s", today.date())
        return

    next_month = today.shift(months=0 if current_month else 1).floor("month")
    next_month_start = next_month.date()
    next_month_end = next_month.ceil("month").date()

    with SyncSessionLocal() as db:
        stmt = (
            select(ContentSchedule)
            .where(ContentSchedule.is_active)
            .options(joinedload(ContentSchedule.hospital))
        )
        result = db.execute(stmt)
        schedules = result.scalars().all()

        created_count = 0
        failures: list[tuple[uuid.UUID | None, str, str]] = []
        successes: list[uuid.UUID] = []
        period_key = f"{next_month_start.year}-{next_month_start.month:02d}"
        for schedule in schedules:
            schedule_hospital_id = getattr(schedule, "hospital_id", None)
            hospital_name = getattr(getattr(schedule, "hospital", None), "name", "(unknown)")
            # 병원(스케줄) 단위 격리 — 발행요일이 적은 스케줄이 2월(28일) 등에서
            # generate_monthly_slots ValueError를 내면 루프 전체가 죽어 이전 병원 슬롯이
            # 커밋되지 않고 이후 병원은 처리조차 안 되던 문제 방지. 슬롯 삽입 자체는 savepoint
            # (begin_nested)로 격리돼 한 병원 실패가 다른 병원 결과를 롤백하지 않는다.
            try:
                if create_next_month_slots_for_schedule(
                    db,
                    schedule,
                    next_month,
                    next_month_start,
                    next_month_end,
                ):
                    created_count += 1
                if schedule_hospital_id is not None:
                    successes.append(schedule_hospital_id)
            except Exception:
                logger.exception("monthly slot generation failed for %s; skipping", hospital_name)
                failures.append(
                    (
                        schedule_hospital_id,
                        hospital_name,
                        "MONTHLY_SLOT_GENERATION_FAILED",
                    )
                )
                continue

        db.commit()
        logger.info(
            f"monthly_slot_generation done: {created_count} hospitals processed, "
            f"{len(failures)} failed"
        )

        for hospital_id in successes:
            _run_async(
                recover_monthly_slot_failure(
                    hospital_id=hospital_id,
                    period_key=period_key,
                )
            )
        for hospital_id, hospital_name, error_code in failures:
            if hospital_id is None:
                continue
            _run_async(
                open_monthly_slot_failure(
                    hospital_id=hospital_id,
                    hospital_name=hospital_name,
                    period_key=period_key,
                    error_code=error_code,
                )
            )

    # One-off close: keep its session and failure boundary separate from September
    # reconciliation. The recurring monthly beat runs every six hours on Aug 26-31.
    if today.year == 2026 and today.month == 8 and today.day >= 26:
        try:
            backfill_nowon_august_2026_slots()
        except Exception:
            logger.exception("Nowon August backfill failed after monthly slot reconciliation")
        try:
            regenerate_nowon_orthopedic_faq()
        except Exception:
            logger.exception("Nowon orthopedic FAQ regeneration failed after August backfill")


def _log_blocked_convertible_tracking_sets(registration: Any) -> None:
    if not isinstance(registration, Mapping):
        return

    for blocked_item in registration.get("blocked") or []:
        if not isinstance(blocked_item, Mapping):
            continue
        name = str(blocked_item.get("name") or "unknown").replace("SoV", "visibility")  # copy-guard: internal-only
        reason = str(blocked_item.get("reason") or "unknown").replace("SoV", "visibility")  # copy-guard: internal-only
        extra = (
            {"hospital_id": blocked_item["hospital_id"]}
            if "hospital_id" in blocked_item
            else {}
        )
        logger.warning(
            "Conversion tracking set blocked: name=%s reason=%s",
            name,
            reason,
            extra=extra,
        )


@celery_app.task(name="app.workers.tasks.run_weekly_monitoring")
def run_weekly_monitoring():
    require_dispatch(current_task, "weekly-sov-monitoring")
    today_kst = arrow.now("Asia/Seoul").date()
    observed_at = datetime.now(timezone.utc)
    week_key = _weekly_measurement_key(today_kst)
    with SyncSessionLocal() as db:
        registration = register_convertible_tracking_sets(db, n=15)
        _log_blocked_convertible_tracking_sets(registration)
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        monthly_ids = {
            hospital.id
            for hospital in iter_monthly_sov_cohort(
                db, limit=settings.SOV_MONTHLY_COHORT_LIMIT
            )
        }
        # 주간 배치는 매주 월간 코호트를 건너뛴다. 해당 병원의 측정은
        # 월말 창에서 run_monthly_sov_measurement가 전담한다 (CLAUDE.md STEP 8).
        if monthly_ids:
            logger.info(
                "Weekly visibility measurement skipped for monthly cohort: %s hospitals on %s",
                len(monthly_ids),
                today_kst,
            )
        hospitals = [
            hospital for hospital in result.scalars().all() if hospital.id not in monthly_ids
        ]

        for h in hospitals:
            run = _ensure_weekly_sov_operation_run(db, h, week_key, observed_at)
            if run is None or run.task_id is None:
                continue
            hospital_id = str(h.id)
            try:
                run_sov_for_hospital.apply_async(
                    args=[hospital_id],
                    queue="sov",
                    headers={
                        **build_dispatch_headers("run-sov", hospital_id),
                        "operation_run_id": str(run.id),
                    },
                    task_id=run.task_id,
                )
            except Exception:
                logger.exception(
                    "Weekly visibility measurement dispatch failed; autonomous recovery will redispatch",
                    extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
                )
                continue
            _mark_weekly_sov_operation_queued(db, run.id, observed_at)

        # 측정 결과 기반 질문 우선순위 조정 (P1-4) — 같은 "sov" 큐 뒤에 적재되므로 단일
        # sov 워커(FIFO) 기준으로는 병원별 측정 태스크가 모두 끝난 뒤 실행된다.
        # 한계: sov 워커가 여러 개거나 측정 태스크가 재시도로 길어지면 일부 병원의 이번 주
        # 측정 결과가 반영되기 전에 실행될 수 있다 — 우선순위 조정은 최근 4주 누적 기준이라
        # 다음 주 실행에서 따라잡는다. countdown은 측정 큐 소화 시간의 보수적 버퍼.
        if hospitals:
            adjust_query_priorities.apply_async(
                queue="sov",
                countdown=1800,
                headers=build_dispatch_headers("adjust-query-priorities"),
            )


@celery_app.task(name="app.workers.tasks.run_monthly_sov_measurement")
def run_monthly_sov_measurement():
    """Run each converted hospital's fixed LOCAL set once in the month-end window."""

    require_dispatch(current_task, "monthly-sov-measurement")
    today_kst = arrow.now("Asia/Seoul").date()
    if today_kst.day < settings.SOV_MONTHLY_WINDOW_START_DAY:
        logger.info("Monthly measurement window is not open: %s", today_kst)
        return
    observed_at = datetime.now(timezone.utc)
    period_key = f"{today_kst.year:04d}-{today_kst.month:02d}"
    with SyncSessionLocal() as db:
        registration = register_convertible_tracking_sets(db, n=15)
        _log_blocked_convertible_tracking_sets(registration)
        hospitals = iter_monthly_sov_cohort(
            db, limit=settings.SOV_MONTHLY_COHORT_LIMIT
        )
        for hospital in hospitals:
            run = _ensure_monthly_sov_operation_run(db, hospital, period_key, observed_at)
            if run is None or run.task_id is None:
                continue
            hospital_id = str(hospital.id)
            task_args = [hospital_id, "monthly", today_kst.year, today_kst.month]
            try:
                run_sov_for_hospital.apply_async(
                    args=task_args,
                    queue="sov",
                    headers={
                        **build_dispatch_headers("run-sov", hospital_id),
                        "operation_run_id": str(run.id),
                    },
                    task_id=run.task_id,
                )
            except Exception:
                logger.exception(
                    "Monthly visibility measurement dispatch failed; recovery will redispatch",
                    extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
                )
                continue
            _mark_weekly_sov_operation_queued(db, run.id, observed_at)


@celery_app.task(name="app.workers.tasks.adjust_query_priorities")
def adjust_query_priorities():
    """Adjust query priorities based on recent AI mention results. Run after weekly measurement tasks complete."""
    require_dispatch(current_task, "adjust-query-priorities")
    with SyncSessionLocal() as db:
        four_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=4)
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        hospitals = result.scalars().all()

        for h in hospitals:
            q_stmt = select(QueryMatrix).where(
                QueryMatrix.hospital_id == h.id,
                QueryMatrix.is_active,
            )
            q_result = db.execute(q_stmt)
            queries = q_result.scalars().all()

            for q in queries:
                rec_stmt = (
                    select(SovRecord)
                    .where(
                        SovRecord.query_id == q.id,
                        SovRecord.measured_at >= four_weeks_ago,
                    )
                    .order_by(SovRecord.measured_at.desc())
                )
                rec_result = db.execute(rec_stmt)
                recent_records = rec_result.scalars().all()
                desired = _desired_query_priority(recent_records)
                if desired is None:
                    continue
                if q.priority != desired:
                    q.priority = desired
                    logger.info(
                        "Legacy query %s priority changed to %s",
                        q.id,
                        desired,
                    )

            # 콘텐츠 타깃 플래너와 노출 액션 엔진은 QueryMatrix가 아니라
            # AIQueryTarget.priority를 읽는다. 두 모델을 함께 갱신해야 측정 결과가
            # 다음 콘텐츠 생성의 실제 우선순위로 되먹여진다.
            target_result = db.execute(
                select(AIQueryTarget).where(
                    AIQueryTarget.hospital_id == h.id,
                    AIQueryTarget.status == "ACTIVE",
                )
            )
            for target in target_result.scalars().all():
                target_records = (
                    db.execute(
                        select(SovRecord)
                        .where(
                            SovRecord.ai_query_target_id == target.id,
                            SovRecord.measured_at >= four_weeks_ago,
                        )
                        .order_by(SovRecord.measured_at.desc())
                    )
                    .scalars()
                    .all()
                )
                desired = _desired_query_priority(target_records)
                if desired is not None and target.priority != desired:
                    target.priority = desired
                    target.updated_by = "SYSTEM_RECURSIVE_LEARNING"
                    logger.info(
                        "환자 질문 목표 %s 우선순위가 %s로 변경됨",
                        target.id,
                        desired,
                    )

        db.commit()


def _desired_query_priority(records: Sequence[SovRecord]) -> str | None:
    """Map recent successful measurements to the next-loop planning priority.

    확정 판정만 본다 — 판정 보류(is_mentioned=None)를 남기면 any()가 False로 접혀
    '전혀 언급되지 않음(HIGH)'으로 오판한다. 보류뿐인 질문은 판단 근거가 없는 것이므로
    None(우선순위 변경 없음)이 맞다.
    """
    successful_records = [record for record in records if sov_engine.record_is_confirmed(record)]
    if not successful_records:
        return None
    # 미언급 질문이 개선 작업의 우선 대상이다. 이미 언급되는 질문은 정상 감시로
    # 되돌리고, 전혀 언급되지 않은 질문만 HIGH로 올린다.
    return "NORMAL" if any(record.is_mentioned for record in successful_records) else "HIGH"


def _weekly_measurement_key(today: date) -> str:
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _ensure_weekly_sov_operation_run(
    db,
    hospital: Hospital,
    week_key: str,
    observed_at: datetime,
) -> OperationRun | None:
    idempotency_key = f"weekly-sov:{hospital.id}:{week_key}"
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing if existing.state == OperationRunState.REQUESTED else None
    hospital_id = str(hospital.id)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="RUN_SOV",
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=operation_run_payloads.build_request_payload(
            operation_run_payloads.DispatchPayload(
                "hospital",
                hospital_id,
                "sov",
                (hospital_id,),
            )
        ),
        result_summary={"measurement_week": week_key},
        requested_at=observed_at,
        version=1,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "RUN_SOV",
                OperationRun.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing if existing.state == OperationRunState.REQUESTED else None
    return run


def _ensure_monthly_sov_operation_run(
    db,
    hospital: Hospital,
    period_key: str,
    observed_at: datetime,
) -> OperationRun | None:
    idempotency_key = f"monthly-sov:{hospital.id}:{period_key}"
    try:
        year_text, month_text = period_key.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (AttributeError, TypeError, ValueError):
        return None
    hospital_id = str(hospital.id)
    task_args = (hospital_id, "monthly", year, month)
    dispatch_payload = operation_run_payloads.build_request_payload(
        operation_run_payloads.DispatchPayload("hospital", hospital_id, "sov", task_args)
    )
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.state == OperationRunState.REQUESTED:
            existing.request_payload = dispatch_payload
            existing.result_summary = {
                "measurement_month": period_key,
                "measurement_mode": "monthly",
            }
            db.commit()
            return existing

        def _rearm_existing() -> OperationRun:
            # 같은 병원×월 OperationRun을 다시 REQUESTED로 열어 월말 윈도우의 다음
            # 6시간 슬롯이 실패한 manifest cells만 재시도하게 한다. 새 월간 키를 만들지
            # 않으므로 중복 full run은 없고, 성공한 셀은 pending 필터에서 계속 빠진다.
            existing.state = OperationRunState.REQUESTED
            existing.task_id = str(uuid.uuid4())
            existing.queued_at = None
            existing.started_at = None
            existing.completed_at = None
            existing.lease_owner = None
            existing.lease_expires_at = None
            existing.success_count = 0
            existing.failure_count = 0
            existing.skipped_count = 0
            existing.safe_error_code = None
            existing.safe_error_message = None
            existing.request_payload = dispatch_payload
            existing.result_summary = {
                "measurement_month": period_key,
                "measurement_mode": "monthly",
            }
            existing.version += 1
            db.commit()
            return existing

        if existing.state == OperationRunState.PARTIAL and _monthly_sov_retry_window(
            period_key, observed_at
        ):
            return _rearm_existing()
        if existing.state == OperationRunState.FAILED:
            code = existing.safe_error_code or ""
            retry_window = _monthly_sov_retry_window(period_key, observed_at)
            if code.endswith("COST_GUARD_BLOCKED") and retry_window and (
                _monthly_sov_pending_budget_fits(db, hospital, period_key)
            ):
                return _rearm_existing()
            if retry_window and not code.endswith("COST_GUARD_BLOCKED"):
                failed_cell_count = _monthly_sov_failed_cell_count(
                    db, hospital.id, period_key
                )
                if failed_cell_count is None or failed_cell_count > 0:
                    return _rearm_existing()
            return None
        return None
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="RUN_SOV",
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=dispatch_payload,
        result_summary={
            "measurement_month": period_key,
            "measurement_mode": "monthly",
        },
        requested_at=observed_at,
        version=1,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "RUN_SOV",
                OperationRun.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing if existing.state == OperationRunState.REQUESTED else None
    return run


def _monthly_sov_retry_window(period_key: str, observed_at: datetime) -> bool:
    try:
        year_text, month_text = period_key.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (AttributeError, TypeError, ValueError):
        return False
    local = observed_at.astimezone(ZoneInfo("Asia/Seoul"))
    if (
        (year, month) == (local.year, local.month)
        and local.day >= settings.SOV_MONTHLY_WINDOW_START_DAY
    ):
        return True
    return is_monthly_recovery_window(observed_at, year, month)


def _monthly_sov_failed_cell_count(
    db, hospital_id: uuid.UUID, period_key: str
) -> int | None:
    try:
        year_text, month_text = period_key.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (AttributeError, TypeError, ValueError):
        return 0
    manifest = db.execute(
        select(MonthlyMeasurementManifest)
        .options(
            selectinload(MonthlyMeasurementManifest.cells).selectinload(
                MonthlyMeasurementCell.observation_slots
            )
        )
        .where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    ).scalar_one_or_none()
    if manifest is None:
        return None
    return sum(
        getattr(cell, "state", None) == "FAILED"
        for cell in (getattr(manifest, "cells", ()) or ())
    )


def _monthly_sov_pending_budget_fits(db, hospital: Hospital, period_key: str) -> bool:
    try:
        year_text, month_text = period_key.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (AttributeError, TypeError, ValueError):
        return False
    manifest = db.execute(
        select(MonthlyMeasurementManifest)
        .options(
            selectinload(MonthlyMeasurementManifest.cells).selectinload(
                MonthlyMeasurementCell.observation_slots
            )
        )
        .where(
            MonthlyMeasurementManifest.hospital_id == hospital.id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    ).scalar_one_or_none()
    if manifest is None:
        return False
    repeat_count = _manifest_slot_repeat_count(manifest)
    if repeat_count is None:
        pending_count = sum(
            cell.state == "FAILED" for cell in (manifest.cells or ())
        )
        if pending_count <= 0:
            return False
        needed = pending_count * SOV_REPEAT_WEEKLY
    else:
        needed = 0
        has_pending = False
        max_judgment_calls = 2 if (hospital.competitors or []) else 1
        for cell in manifest.cells or ():
            if cell.state == "EXCLUDED":
                continue
            slots = list(cell.observation_slots or ())
            missing = max(0, repeat_count - len(slots))
            if missing:
                has_pending = True
                needed += missing * (1 + max_judgment_calls)
            for slot in slots:
                if slot_is_terminal(slot):
                    continue
                has_pending = True
                if slot_needs_answer(slot):
                    needed += 1 + max_judgment_calls
                elif slot_needs_judgment(slot):
                    needed += sov_engine.estimate_judgment_provider_calls(
                        hospital.name,
                        slot.raw_response or "",
                        competitors=hospital.competitors or [],
                    )
        if not has_pending:
            return False
    daily_remaining, monthly_remaining = _run_async(cost_guard.remaining_units("sov"))
    return needed <= daily_remaining and needed <= monthly_remaining


def _mark_weekly_sov_operation_queued(db, run_id: uuid.UUID, observed_at: datetime) -> bool:
    """CAS REQUESTED->QUEUED after broker publish without overwriting worker signals."""

    queued = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run_id,
            OperationRun.state == OperationRunState.REQUESTED,
        )
        .values(
            state=OperationRunState.QUEUED,
            queued_at=observed_at,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    ).scalar_one_or_none()
    db.commit()
    return queued is not None


# ══════════════════════════════════════════════════════════════════
# 월간 리포트 (다음 달 1일 00:15 첫 마감, 2~7일 매일 자동 복구)
# ══════════════════════════════════════════════════════════════════
def _monthly_operation_run_id(task) -> uuid.UUID | None:
    headers = getattr(getattr(task, "request", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("operation_run_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _start_monthly_report_batch_run(db, task, period) -> uuid.UUID | None:
    task_id = getattr(getattr(task, "request", None), "id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.operation_type == "MONTHLY_REPORT_BATCH",
            OperationRun.idempotency_key == f"monthly-report-batch:{task_id}",
        )
    ).scalar_one_or_none()
    observed_at = datetime.now(timezone.utc)
    if existing is None:
        existing = OperationRun(
            id=uuid.uuid4(),
            hospital_id=None,
            operation_type="MONTHLY_REPORT_BATCH",
            state=OperationRunState.RUNNING,
            idempotency_key=f"monthly-report-batch:{task_id}",
            requested_by_id=None,
            task_id=task_id,
            attempt_count=1,
            total_count=0,
            success_count=0,
            failure_count=0,
            skipped_count=0,
            request_payload={
                "source_type": "MONTHLY_REPORT_BATCH",
                "source_id": f"{period.year:04d}-{period.month:02d}",
            },
            result_summary={
                "period_year": period.year,
                "period_month": period.month,
            },
            started_at=observed_at,
            heartbeat_at=observed_at,
            version=1,
        )
        db.add(existing)
    else:
        existing.state = OperationRunState.RUNNING
        existing.completed_at = None
        existing.heartbeat_at = observed_at
        existing.attempt_count += 1
        existing.safe_error_code = None
        existing.safe_error_message = None
        existing.version += 1
    headers = getattr(task.request, "headers", None)
    if not isinstance(headers, dict):
        headers = {}
        task.request.headers = headers
    # Runtime correlation only. The plain ``operation_run_id`` header is an
    # authorization claim for Admin-dispatched tasks; setting it on a Beat retry
    # would make AuthenticatedTask demand an incompatible stored dispatch policy.
    headers["reputation_dispatch_operation_run_id"] = str(existing.id)
    db.commit()
    return existing.id


def _finish_monthly_report_batch_run(
    db, run_id: uuid.UUID | None, result: Mapping[str, int | str], *, hard_failure: bool
) -> None:
    if run_id is None:
        return
    run = db.get(OperationRun, run_id)
    if run is None:
        return
    failures = int(result["failure_count"])
    successes = int(result["success_count"])
    run.state = (
        OperationRunState.FAILED
        if hard_failure and successes == 0
        else OperationRunState.PARTIAL
        if failures
        else OperationRunState.SUCCEEDED
    )
    run.completed_at = datetime.now(timezone.utc)
    run.heartbeat_at = None
    run.total_count = int(result["total_count"])
    run.success_count = successes
    run.failure_count = failures
    run.skipped_count = 0
    run.result_summary = dict(result)
    if hard_failure:
        run.safe_error_code = "MONTHLY_REPORT_BATCH_FAILED"
        run.safe_error_message = "월간 리포트 자동 마감 중 완료하지 못한 병원이 있습니다."
    run.version += 1
    db.commit()


def _dispatch_monthly_sov_catchup(
    db, hospital: Hospital, period_key: str, observed_at: datetime
) -> uuid.UUID | None:
    run = _ensure_monthly_sov_operation_run(db, hospital, period_key, observed_at)
    if run is None or run.task_id is None:
        return None
    year, month = (int(value) for value in period_key.split("-", 1))
    hospital_id = str(hospital.id)
    try:
        run_sov_for_hospital.apply_async(
            args=[hospital_id, "monthly", year, month],
            queue="sov",
            headers={
                **build_dispatch_headers("run-sov", hospital_id),
                "operation_run_id": str(run.id),
            },
            task_id=run.task_id,
        )
    except Exception:  # noqa: BLE001 - REQUESTED run is redispatched by reconciliation.
        logger.exception(
            "Monthly measurement catch-up dispatch failed; recovery will redispatch",
            extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
        )
        return run.id
    _mark_weekly_sov_operation_queued(db, run.id, observed_at)
    return run.id


def _mark_monthly_operation_run_running(
    db,
    run_id: uuid.UUID | None,
    year: int,
    month: int,
) -> None:
    if run_id is None:
        return
    run = db.get(OperationRun, run_id)
    if run is None or run.state not in (
        OperationRunState.REQUESTED,
        OperationRunState.QUEUED,
        OperationRunState.RUNNING,
    ):
        return
    observed_at = datetime.now(timezone.utc)
    run.state = OperationRunState.RUNNING
    run.started_at = run.started_at or observed_at
    run.heartbeat_at = observed_at
    run.attempt_count += 1
    run.result_summary = {
        "stage": MonthlyRunStage.RUNNING.value,
        "period_year": year,
        "period_month": month,
    }
    run.version += 1
    db.commit()


def _mark_monthly_report_measurement_incomplete(
    db,
    run_id: uuid.UUID | None,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
) -> None:
    if run_id is None:
        return
    run = db.get(OperationRun, run_id)
    if run is None:
        return
    run.state = OperationRunState.PARTIAL
    run.completed_at = datetime.now(timezone.utc)
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
    run.total_count = 1
    run.success_count = 0
    run.failure_count = 1
    run.skipped_count = 0
    run.safe_error_code = "MONTHLY_MEASUREMENT_INCOMPLETE"
    run.safe_error_message = "필수 측정이 완료되지 않아 월간 리포트를 만들지 않았습니다."
    run.result_summary = {
        "stage": MonthlyRunStage.BLOCKED.value,
        "period_year": year,
        "period_month": month,
        "hospital_id": str(hospital_id),
    }
    run.version += 1
    db.commit()


def _start_scheduled_monthly_operation_run(
    db, hospital: Hospital, now: arrow.Arrow
) -> tuple[uuid.UUID, bool]:
    idempotency_key = f"scheduled:{hospital.id}:{now.year}-{now.month:02d}"
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == "SCHEDULED_MONTHLY_REPORT",
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        observed_at = now.datetime.astimezone(timezone.utc)
        last_seen = existing.heartbeat_at or existing.started_at or existing.requested_at
        active = existing.state in (
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        )
        stale = active and last_seen <= observed_at - timedelta(hours=1)
        # PARTIAL is a durable operator-visible block (coverage/artifact), not a
        # transient build failure. Reopening it every six hours created a new
        # immutable report version without changing the underlying measurement.
        retryable = existing.state == OperationRunState.FAILED
        if stale or retryable:
            existing.state = OperationRunState.RUNNING
            existing.completed_at = None
            existing.started_at = observed_at
            existing.heartbeat_at = observed_at
            existing.total_count = 1
            existing.success_count = 0
            existing.failure_count = 0
            existing.skipped_count = 0
            existing.safe_error_code = None
            existing.safe_error_message = None
            existing.result_summary = {
                "stage": MonthlyRunStage.RUNNING.value,
                "period_year": now.year,
                "period_month": now.month,
            }
            existing.attempt_count += 1
            existing.version += 1
            db.commit()
            return existing.id, False
        return existing.id, True
    observed_at = datetime.now(timezone.utc)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="SCHEDULED_MONTHLY_REPORT",
        state=OperationRunState.RUNNING,
        idempotency_key=idempotency_key,
        requested_by_id=None,
        task_id=None,
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={
            "source_type": "MONTHLY_SCHEDULE",
            "source_id": f"{now.year}-{now.month:02d}",
        },
        result_summary={
            "stage": MonthlyRunStage.RUNNING.value,
            "period_year": now.year,
            "period_month": now.month,
        },
        requested_at=observed_at,
        started_at=observed_at,
        heartbeat_at=observed_at,
        version=1,
    )
    db.add(run)
    db.commit()
    return run.id, False


def _latest_monthly_report(db, hospital_id: uuid.UUID, year: int, month: int):
    return (
        db.execute(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital_id,
                MonthlyReport.period_year == year,
                MonthlyReport.period_month == month,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.version.desc())
        )
        .scalars()
        .first()
    )


def _latest_monthly_report_operation_run(
    db, hospital_id: uuid.UUID, year: int, month: int
) -> OperationRun | None:
    """Return the newest report-generation run that belongs to this period."""

    period_key = f"{year:04d}-{month:02d}"
    runs = (
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type.in_(
                    ("SCHEDULED_MONTHLY_REPORT", "GENERATE_MONTHLY_REPORT")
                ),
            )
            .order_by(OperationRun.created_at.desc())
        )
        .scalars()
        .all()
    )
    for run in runs:
        summary = run.result_summary if isinstance(run.result_summary, Mapping) else {}
        payload = run.request_payload if isinstance(run.request_payload, Mapping) else {}
        summary_period = (
            summary.get("period_year"),
            summary.get("period_month"),
        )
        if summary_period == (year, month):
            return run
        if payload.get("source_id") == period_key:
            return run
        if run.idempotency_key in {
            f"scheduled:{hospital_id}:{period_key}",
            f"coverage-recovery:{hospital_id}:{period_key}",
        }:
            return run
    return None


def _monthly_sov_measurement_succeeded(
    db, hospital_id: uuid.UUID, period_key: str
) -> bool:
    run = (
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.hospital_id == hospital_id,
                OperationRun.operation_type == "RUN_SOV",
                OperationRun.state == OperationRunState.SUCCEEDED,
                or_(
                    OperationRun.idempotency_key
                    == f"monthly-sov:{hospital_id}:{period_key}",
                    OperationRun.result_summary["measurement_month"].as_string()
                    == period_key,
                ),
            )
            .order_by(OperationRun.completed_at.desc(), OperationRun.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if run is None:
        return False
    try:
        year_text, month_text = period_key.split("-", 1)
        year, month = int(year_text), int(month_text)
    except (AttributeError, TypeError, ValueError):
        return False
    manifest = db.execute(
        select(MonthlyMeasurementManifest)
        .options(
            selectinload(MonthlyMeasurementManifest.cells).selectinload(
                MonthlyMeasurementCell.observation_slots
            )
        )
        .where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    ).scalar_one_or_none()
    if manifest is None:
        _mark_monthly_measurement_incomplete(
            db,
            run,
            planned=0,
            success=0,
            failed=0,
            manifest_closed=False,
        )
        return False

    summary = summarize_manifest(
        manifest.cells,
        closed=True,
        configured_platforms=manifest.configured_platforms,
    )
    legacy_complete = (
        summary.quality == "COMPLETE"
        and summary.planned_count > 0
        and summary.success_count == summary.planned_count
        and summary.failed_count == 0
        and summary.excluded_count == 0
    )
    adequacy = _manifest_observation_adequacy(manifest, deadline_reached=True)
    finalized = (
        legacy_complete
        if adequacy is None
        else adequacy.planned_slots > 0
        and adequacy.status in {"COMPLETE", "LIMITED", "UNAVAILABLE"}
    )
    if finalized:
        observed_at = datetime.now(timezone.utc)
        if manifest.closed_at is None:
            if observed_at < manifest.closes_at:
                return False
            close_manifest(manifest, now=observed_at)
            db.commit()
    else:
        _mark_monthly_measurement_incomplete(
            db,
            run,
            planned=summary.planned_count,
            success=summary.success_count,
            failed=summary.failed_count,
            manifest_closed=manifest.closed_at is not None,
        )
    return finalized


def _mark_monthly_measurement_incomplete(
    db,
    run: OperationRun,
    *,
    planned: int,
    success: int,
    failed: int,
    manifest_closed: bool,
) -> None:
    """Reconcile a task-level success that did not complete the frozen denominator."""
    run.state = OperationRunState.PARTIAL
    run.total_count = 1
    run.success_count = 0
    run.failure_count = 1
    run.safe_error_code = "MONTHLY_MEASUREMENT_INCOMPLETE"
    run.safe_error_message = (
        "필수 측정이 완료되지 않았습니다. 실패한 측정 항목만 복구한 뒤 리포트를 생성해 주세요."
    )
    result_summary = dict(run.result_summary or {})
    result_summary.update(
        {
            "measurement_quality": "INCOMPLETE",
            "planned_count": planned,
            "success_count": success,
            "failed_count": failed,
            "manifest_closed": manifest_closed,
        }
    )
    run.result_summary = result_summary
    run.version += 1
    db.commit()


def _hospital_requires_monthly_sov_success(db, hospital, period) -> bool:
    """월간 측정 전환 경로 병원은 해당 월 측정 SUCCEEDED 없이 리포트를 만들지 않는다.

    전환 윈도우(8/24–31)에 묶지 않는다. 9/1 직전 달 마감에서도 실패한 전환 병원은
    빈 월간 리포트를 만들지 않고, 월간 측정을 쓰지 않는 병원은 기존 마감 경로를 탄다.
    """
    if bool(getattr(hospital, "monthly_sov_cohort", False)):
        return True
    hospital_id = getattr(hospital, "id", None)
    if hospital_id is None:
        return False
    period_key = f"{period.year:04d}-{period.month:02d}"
    run_id = db.execute(
        select(OperationRun.id).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.idempotency_key == f"monthly-sov:{hospital_id}:{period_key}",
        )
    ).scalar_one_or_none()
    return run_id is not None


def _prior_monthly_manifest(db, hospital_id: uuid.UUID, now: arrow.Arrow):
    # August 2026 is the first converted month. Its report must not inherit weekly
    # mention cells from July as a month-over-month comparison.
    if now.year == 2026 and now.month == 8:
        return None
    prior_anchor = now.shift(months=-1)
    return db.execute(
        select(MonthlyMeasurementManifest).where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == prior_anchor.year,
            MonthlyMeasurementManifest.period_month == prior_anchor.month,
        )
    ).scalar_one_or_none()


def _finish_monthly_operation_run(
    db,
    run_id: uuid.UUID | None,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
    outcome: str,
) -> None:
    if run_id is None:
        return
    run = db.get(OperationRun, run_id)
    if run is None:
        return
    report = _latest_monthly_report(db, hospital_id, year, month)
    if outcome == "failed":
        stage = MonthlyRunStage.FAILED
        state = OperationRunState.FAILED
        counts = (0, 1, 0)
    elif outcome == "skipped_existing":
        stage = MonthlyRunStage.EXISTING
        state = OperationRunState.SUCCEEDED
        counts = (0, 0, 1)
    elif _monthly_report_is_delivery_ready(db, report):
        stage = MonthlyRunStage.ARTIFACT_VALIDATED
        state = OperationRunState.SUCCEEDED
        counts = (1, 0, 0)
    elif report is not None:
        stage = MonthlyRunStage.BLOCKED
        state = OperationRunState.PARTIAL
        counts = (0, 1, 0)
    else:
        stage = MonthlyRunStage.FAILED
        state = OperationRunState.FAILED
        counts = (0, 1, 0)
    raw_sov_summary = getattr(report, "sov_summary", None) if report is not None else None
    sov_summary = raw_sov_summary if isinstance(raw_sov_summary, dict) else {}
    raw_adequacy = sov_summary.get("observation_adequacy")
    observation_adequacy = raw_adequacy if isinstance(raw_adequacy, dict) else None
    measurement_quality = (
        observation_adequacy.get("status")
        if observation_adequacy is not None
        else "LEGACY_COMPLETE"
        if _monthly_report_quality_is_complete(report)
        else None
    )
    milestones = (
        [
            (
                "MEASUREMENT_LIMITED"
                if measurement_quality == "LIMITED"
                else MonthlyRunStage.COVERAGE_COMPLETE.value
            ),
            MonthlyRunStage.ARTIFACT_VALIDATED.value,
        ]
        if stage is MonthlyRunStage.ARTIFACT_VALIDATED
        else [stage.value]
    )
    run.state = state
    run.completed_at = datetime.now(timezone.utc)
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
    run.total_count = 1
    run.success_count, run.failure_count, run.skipped_count = counts
    run.result_summary = {
        "stage": stage.value,
        "milestones": milestones,
        "period_year": year,
        "period_month": month,
        "report_id": str(report.id) if report is not None else None,
        "report_version": report.version if report is not None else None,
        "measurement_quality": measurement_quality,
        "observation_adequacy": observation_adequacy,
        "supersedes_report_id": (
            str(report.supersedes_report_id)
            if report is not None and report.supersedes_report_id is not None
            else None
        ),
    }
    if stage is MonthlyRunStage.FAILED:
        run.safe_error_code = "MONTHLY_REPORT_FAILED"
        run.safe_error_message = "월간 리포트를 만들지 못했습니다. 다시 만들기를 시도해 주세요."
    elif stage is MonthlyRunStage.BLOCKED:
        artifact_blocked = (
            report is not None
            and _monthly_report_measurement_is_final(report)
            and not _has_valid_doctor_artifact(db, report)
        )
        run.safe_error_code = (
            "DOCTOR_ARTIFACT_BLOCKED" if artifact_blocked else "MONTHLY_REPORT_BLOCKED"
        )
        run.safe_error_message = (
            "원장 전달용 PDF 검증을 완료하지 못했습니다. 리포트 화면에서 다시 만들기를 눌러 주세요."
            if artifact_blocked
            else "필수 측정이나 운영 자료가 부족합니다. 운영 센터에서 차단 사유를 확인해 주세요."
        )
    run.version += 1
    db.commit()


def _has_valid_doctor_artifact(db, report: MonthlyReport) -> bool:
    artifact = db.execute(
        select(MonthlyReportArtifact).where(
            MonthlyReportArtifact.report_id == report.id,
            MonthlyReportArtifact.audience == "DOCTOR",
        )
    ).scalar_one_or_none()
    return monthly_doctor_artifact_is_valid(report, artifact)


def _fail_monthly_operation_run(
    db,
    run_id: uuid.UUID | None,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
) -> None:
    _finish_monthly_operation_run(db, run_id, hospital_id, year, month, "failed")


def _first_publication_at(item: ContentItem) -> datetime | None:
    return getattr(item, "first_published_at", None) or item.published_at


def _observed_contract_publications(items: Iterable[ContentItem], observed_at: datetime) -> list:
    """Only publications that had actually happened at report-build time fulfill a contract."""
    return [
        item
        for item in items
        if _first_publication_at(item) is not None
        and _first_publication_at(item) <= observed_at
    ]


def _contract_publication_timing_counts(
    items: Iterable[ContentItem],
    period_start: datetime,
    period_end: datetime,
) -> tuple[int, int]:
    """Return contract publications before the period and at/after its exclusive end."""
    early = 0
    late = 0
    for item in items:
        first_published_at = _first_publication_at(item)
        if first_published_at is None:
            continue
        if first_published_at < period_start:
            early += 1
        elif first_published_at >= period_end:
            late += 1
    return early, late


def _load_monthly_publication_facts(
    db,
    hospital_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
    observed_at: datetime,
) -> tuple[list[ContentItem], list[ContentItem], list[ContentItem]]:
    """Load immutable publication facts separately from currently visible rows."""
    first_publication_at = func.coalesce(
        ContentItem.first_published_at,
        ContentItem.published_at,
    )
    actual_publications = list(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at >= period_start,
                first_publication_at < period_end,
                first_publication_at <= observed_at,
            )
        ).scalars()
    )
    visible_publications = [
        item for item in actual_publications if item.status == ContentStatus.PUBLISHED
    ]
    contract_publications = _observed_contract_publications(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at <= observed_at,
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                >= period_start.date(),
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                < period_end.date(),
            )
        ).scalars(),
        observed_at,
    )
    return actual_publications, visible_publications, contract_publications


def _headline_uses_full_current_cohort(
    monthly_sov, current_cells: tuple[ManifestCellInput, ...]
) -> bool:
    current_local_keys = frozenset(
        (cell.query_key, cell.platform)
        for cell in current_cells
        if cell.query_intent == "LOCAL" and cell.state != "EXCLUDED"
    )
    return (
        monthly_sov.comparison.status != "COMPARABLE"
        or monthly_sov.comparison_cell_keys == current_local_keys
    )


def _build_monthly_report_for_hospital(
    db,
    h: Hospital,
    now: arrow.Arrow,
    *,
    rebuild: bool = False,
    observed_at: datetime | None = None,
    build_reason: ReportBuildReason = ReportBuildReason.MANUAL_REBUILD,
    correlation_key: str | None = None,
    operation_run_id: uuid.UUID | None = None,
) -> str:
    """`now`가 가리키는 달의 월간 리포트 1건을 만든다.

    반환값은 생성 완료 ``"created"``, 원장 PDF가 차단된 ``"blocked_artifact"``,
    이미 있어서 건너뛴 ``"skipped_existing"`` 중 하나다.

    월말 배치(run_monthly_reports)와 Admin의 병원별 수동 재생성이 이 함수를 공유한다 —
    두 경로가 서로 다른 코드였다면 배치 실패를 복구한 리포트가 배치본과 다른 내용이 될 수
    있다. 리포트 커밋까지 여기서 끝내고, 작업 상태 기록과 알림 이벤트는 호출자가 처리한다.
    """
    period = reporting_period(now.year, now.month)
    period_start = period.starts_at
    period_end = period.ends_at
    version_plan = lock_report_version_plan(
        db,
        hospital_id=h.id,
        period=period,
        reason_code=build_reason,
        correlation_key=correlation_key or f"manual:{h.id}:{now.year}-{now.month:02d}",
    )
    if not version_plan.create:
        return "skipped_existing"

    manifest = db.execute(
        select(MonthlyMeasurementManifest).where(
            MonthlyMeasurementManifest.hospital_id == h.id,
            MonthlyMeasurementManifest.period_year == now.year,
            MonthlyMeasurementManifest.period_month == now.month,
        )
    ).scalar_one_or_none()
    actual_now = observed_at or arrow.now("Asia/Seoul").datetime
    if manifest is not None and manifest.closed_at is None and actual_now >= manifest.closes_at:
        close_manifest(manifest, now=actual_now)

    # 월간 리포트 중복 생성 방지
    existing_check = db.execute(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == h.id,
            MonthlyReport.period_year == now.year,
            MonthlyReport.period_month == now.month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
    )
    existing_reports = existing_check.scalars().all()
    if existing_reports and not rebuild:
        logger.warning(
            f"Monthly report already exists for {h.name} {now.year}-{now.month:02d}, skipping."
        )
        return "skipped_existing"
    prev_start = now.shift(months=-1).floor("month").datetime
    prev_end = now.floor("month").datetime
    prior_manifest = _prior_monthly_manifest(db, h.id, now)
    current_loaded = load_monthly_sov_manifest(db, manifest) if manifest is not None else None
    prior_loaded = (
        load_monthly_sov_manifest(db, prior_manifest) if prior_manifest is not None else None
    )
    monthly_sov = build_monthly_sov(
        current_loaded.cells if current_loaded is not None else (),
        tuple(manifest.configured_platforms) if manifest is not None else (),
        prior_cells=prior_loaded.cells if prior_loaded is not None else None,
        prior_platforms=(
            tuple(prior_manifest.configured_platforms) if prior_manifest is not None else None
        ),
        # 동결 시점의 측정 정책 스냅샷. 두 달의 정책이 다르면(v1↔v2 경계 등)
        # 비교가 NON_COMPARABLE로 떨어진다 — 측정 기준 변경을 성과로 팔지 않는다.
        current_protocol=(
            (manifest.platform_provenance or {}).get("measurement_protocol")
            if manifest is not None
            else None
        ),
        prior_protocol=(
            (prior_manifest.platform_provenance or {}).get("measurement_protocol")
            if prior_manifest is not None
            else None
        ),
    )
    # 타깃별 언급 빈도는 성공 측정 **전부**로 센다. 대표 1건(evidence용)은 언급된
    # 시도를 먼저 고르므로 비율 계산에 쓰면 위로 편향된다.
    sov_records = list(current_loaded.scored_records) if current_loaded is not None else []
    evidence_records = (
        list(current_loaded.selected_records) if current_loaded is not None else []
    )
    # 헤드라인·전월·증감은 모두 같은 분모 위에 있다 — 비교가 성립하면 셋 다 매칭
    # 코호트 기준이고, 아니면 셋 다 이번 달 전 셀 기준(전월·증감은 None)이다.
    sov_pct = monthly_sov.sov_pct
    prev_sov = monthly_sov.comparison.prior_sov_pct
    change_pct = monthly_sov.comparison.change_pct
    report_platforms = list(manifest.configured_platforms) if manifest is not None else None

    # 최초 발행 시각은 닫힌 달의 실제 이행 근거다. 이후 자료 철회로 자동 복구 중인
    # 행도 실제 발행 건수와 계약 이행에서 사라지지 않지만, 원장에게 보여 주는 제목은
    # 현재 공개 상태인 행으로 제한한다.
    actual_published_contents, published_contents, contract_contents = (
        _load_monthly_publication_facts(
            db,
            h.id,
            period_start,
            period_end,
            actual_now,
        )
    )
    supplementary_count = sum(
        1 for item in actual_published_contents
        if item.carried_over_from is not None and item.carried_over_from < period_start.date()
    )
    contract_scheduled_content_stmt = select(ContentItem).where(
        ContentItem.hospital_id == h.id,
        func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
        >= period_start.date(),
        func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
        < period_end.date(),
    )
    contract_scheduled_contents = db.execute(
        contract_scheduled_content_stmt
    ).scalars().all()
    early_publication_count, late_recovery_count = _contract_publication_timing_counts(
        contract_contents,
        period_start,
        period_end,
    )
    # A September upgrade must not rewrite the August contractual denominator.
    period_plan = db.execute(
        select(ContentSchedule.plan)
        .where(
            ContentSchedule.hospital_id == h.id,
            ContentSchedule.active_from < period_end.date(),
        )
        .order_by(ContentSchedule.active_from.desc(), ContentSchedule.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    content_operations = build_monthly_content_operations_snapshot(
        plan=period_plan,
        scheduled_items=contract_scheduled_contents,
        published_items=actual_published_contents,
        cutoff_at=actual_now,
        supplementary_count=supplementary_count,
        contract_published_count=len(contract_contents),
        early_publication_count=early_publication_count,
        late_recovery_count=late_recovery_count,
    )

    # 전월 발행 콘텐츠(유형별 발행 누적을 전월과 나란히 비교하기 위함)
    previous_first_publication_at = func.coalesce(
        ContentItem.first_published_at,
        ContentItem.published_at,
    )
    prev_content_stmt = select(ContentItem).where(
        ContentItem.hospital_id == h.id,
        previous_first_publication_at >= prev_start,
        previous_first_publication_at < prev_end,
    )
    prev_content_result = db.execute(prev_content_stmt)
    prev_published_contents = prev_content_result.scalars().all()

    # 콘텐츠 발행-AI 언급 상관 집계(인과 주장 아님, 상관 표기용)
    attribution = build_content_attribution_summary(
        ContentAttributionInput(
            published_contents=published_contents,
            prev_published_contents=prev_published_contents,
            actual_publication_contents=actual_published_contents,
            current_cells=current_loaded.cells if current_loaded is not None else (),
            prior_cells=prior_loaded.cells if prior_loaded is not None else None,
            sov_pct=sov_pct,
            prev_sov_pct=prev_sov,
            change_pct=change_pct,
            comparison_reason=monthly_sov.comparison.reason,
            comparable_cell_keys=monthly_sov.comparison_cell_keys,
        )
    )

    # 인용 귀속 — AI 답변이 인용한 URL을 우리 글·허브 페이지에 매칭한다.
    # 매칭 대상은 이 달 발행분이 아니라 **발행된 전체 글**이다. AI는 지난달 글도
    # 인용하고, 그때 "우리 글이 읽혔다"는 사실은 이 달의 결과이기 때문이다.
    citation_content_rows = db.execute(
        select(
            ContentItem.id,
            ContentItem.title,
            ContentItem.content_type,
        ).where(
            ContentItem.hospital_id == h.id,
            ContentItem.status == ContentStatus.PUBLISHED,
        )
    ).all()
    # 인용 귀속도 헤드라인과 같은 원칙 — 셀의 모든 성공 반복(scored_records)을 본다.
    citation_records_by_id = {record.id: record for record in sov_records}
    citations = build_citation_attribution(
        CitationAttributionInput(
            hospital=h,
            cells=current_loaded.cells if current_loaded is not None else (),
            records_by_id=citation_records_by_id,
            content_items=citation_content_rows,
        )
    )

    # Persist the measurement-to-action layer that the Admin and PDF already know how to
    # render. Previously this analysis existed only as a helper and never entered the
    # production monthly-report path, so source evidence and next actions disappeared.
    query_targets = (
        db.execute(
            select(AIQueryTarget)
            .where(AIQueryTarget.hospital_id == h.id)
            .options(selectinload(AIQueryTarget.variants))
        )
        .scalars()
        .all()
    )
    exposure_gaps = (
        db.execute(
            select(ExposureGap).where(
                ExposureGap.hospital_id == h.id,
                ExposureGap.status.in_(("OPEN", "WATCHING")),
            )
        )
        .scalars()
        .all()
    )
    exposure_actions = (
        db.execute(
            select(ExposureAction)
            .where(
                ExposureAction.hospital_id == h.id,
                or_(
                    ExposureAction.status.in_(("OPEN", "IN_PROGRESS", "BLOCKED")),
                    and_(
                        ExposureAction.status == "COMPLETED",
                        ExposureAction.completed_at >= period_start,
                        ExposureAction.completed_at < period_end,
                    ),
                ),
            )
            .options(
                selectinload(ExposureAction.linked_content),
                selectinload(ExposureAction.query_target),
                selectinload(ExposureAction.gap),
            )
        )
        .scalars()
        .all()
    )
    next_month = arrow.get(period_start).shift(months=1).format("YYYY-MM")
    strategy = build_strategy_summary(
        hospital=h,
        query_targets=list(query_targets),
        sov_records=sov_records,
        exposure_gaps=list(exposure_gaps),
        exposure_actions=list(exposure_actions),
        period_start=period_start,
        period_end=period_end,
        next_month=next_month,
    )
    monthly_sov_payload = monthly_sov.to_payload()

    # "서비스 시작 시점(V0) 대비" 참고선. 질문 세트가 충분히 겹치지 않으면 None이라
    # 원장 페이지에 아무 말도 하지 않는다 — 다른 질문으로 잰 두 수치를 나란히
    # 놓는 순간 그 줄은 거짓말이 된다.
    headline_uses_full_current_cohort = _headline_uses_full_current_cohort(
        monthly_sov,
        current_loaded.cells if current_loaded is not None else (),
    )
    v0_baseline = _load_v0_baseline(
        db,
        h.id,
        current_sov_pct=sov_pct if headline_uses_full_current_cohort else None,
        tracking_query_texts=[
            cell.query_text
            for cell in (current_loaded.cells if current_loaded else ())
            if cell.query_intent == "LOCAL"
        ],
        current_platforms=tuple(report_platforms or ()),
        current_protocol=(
            (manifest.platform_provenance or {}).get("measurement_protocol")
            if manifest is not None
            else None
        ),
        current_cells=current_loaded.cells if current_loaded is not None else (),
    )
    # 원장 뷰를 AE PDF보다 **먼저** 만든다. 토킹 포인트는 이 뷰가 바인딩한 숫자에서
    # 나오고, 내부 PDF와 Admin이 그 같은 문장을 읽어야 한 자리에서 두 말이 안 된다.
    doctor_view = build_doctor_report_view(
        hospital=h,
        sov_pct=sov_pct,
        prev_sov_pct=prev_sov,
        published_count=len(actual_published_contents),
        plan_quota=monthly_quota_for_plan(period_plan),
        supplementary_count=supplementary_count,
        early_publication_count=early_publication_count,
        late_recovery_count=late_recovery_count,
        contract_published_count=len(contract_contents),
        attribution=attribution,
        citations=citations,
        published_contents=list(published_contents),
        v0_baseline=v0_baseline,
        records=evidence_records,
        platforms=report_platforms,
        sov_coverage=monthly_sov_payload,
        comparison_reason=monthly_sov_payload["comparison"]["reason"],
    )
    talking_points = list(doctor_view["talking_points"])

    pdf_path = generate_pdf_report(
        hospital=h,
        period_start=period_start,
        period_end=period_end,
        report_type="MONTHLY",
        sov_pct=sov_pct,
        published_count=len(actual_published_contents),
        repeat_count=SOV_REPEAT_WEEKLY,
        attribution=attribution,
        strategy=strategy,
        sov_coverage=monthly_sov_payload,
        content_operations=content_operations.payload,
        citations=citations,
        talking_points=talking_points,
        report_version=version_plan.version,
    )
    essence_summary = build_monthly_essence_summary(db, h, period_start, period_end)

    report = MonthlyReport(
        hospital_id=h.id,
        period_year=now.year,
        period_month=now.month,
        report_type="MONTHLY",
        version=version_plan.version,
        supersedes_report_id=version_plan.supersedes_report_id,
        pdf_path=pdf_path,
        doctor_pdf_path=None,
        sov_summary=monthly_sov_payload,
        content_summary={
            "published_count": len(actual_published_contents),
            "operations": content_operations.payload,
            "contract_timing": {
                "early_publication_count": early_publication_count,
                "late_recovery_count": late_recovery_count,
                "published_for_contract_count": len(contract_contents),
                "observed_at": actual_now.isoformat(),
            },
            "attribution": attribution,
            "strategy": strategy,
            "citations": citations,
            # AE 미팅 키트 — Admin 리포트 상세가 content_summary를 그대로 준다.
            "talking_points": talking_points,
        },
        essence_summary=essence_summary,
    )
    if manifest is None:
        report.quality = "BLOCKED"
        report.delivery_blockers = ["MANIFEST_MISSING", "DOCTOR_ARTIFACT_UNVALIDATED"]
    else:
        apply_manifest_to_report(report, manifest)
    if content_operations.delivery_blockers:
        report.delivery_blockers = [
            *list(report.delivery_blockers or []),
            *content_operations.delivery_blockers,
        ]
    db.add(report)
    db.flush()
    reported_next_action_ids = {
        item.get("id")
        for item in strategy.get("next_month_actions", [])
        if isinstance(item, dict) and item.get("id")
    }
    for action in exposure_actions:
        if action.linked_report_id is None and str(action.id) in reported_next_action_ids:
            action.linked_report_id = report.id

    artifact_error: DoctorPdfValidationError | None = None
    try:
        public_url = _public_site_url(h.aeo_domain, h.slug)
        doctor_artifact = generate_doctor_pdf_report(
            h, report.id, period_start, doctor_view, public_url
        )
        report.doctor_pdf_path = doctor_artifact.path
        report.delivery_blockers = [
            blocker
            for blocker in report.delivery_blockers
            if blocker != "DOCTOR_ARTIFACT_UNVALIDATED"
        ]
        db.add(
            MonthlyReportArtifact(
                report_id=report.id,
                audience="DOCTOR",
                path=doctor_artifact.path,
                sha256=doctor_artifact.sha256,
                byte_size=doctor_artifact.byte_size,
                validated=True,
                validated_at=datetime.now(timezone.utc),
                validated_by_id=None,
                validation_metadata=doctor_artifact.metadata.model_dump(mode="json"),
            )
        )
    except DoctorPdfValidationError as exc:
        artifact_error = exc
        report.doctor_pdf_path = None
        logger.warning(
            "Doctor report artifact blocked: hospital_id=%s report_id=%s code=%s",
            h.id,
            report.id,
            exc.code,
        )
    db.commit()

    incident_context = MonthlyArtifactIncidentContext(
        hospital_id=h.id,
        hospital_name=h.name,
        report_id=report.id,
        year=now.year,
        month=now.month,
        operation_run_id=operation_run_id,
    )
    if artifact_error is not None:
        _run_async(record_monthly_artifact_failure(incident_context, artifact_error))
    else:
        _run_async(recover_monthly_artifact_failures(incident_context))

    return "blocked_artifact" if artifact_error is not None else "created"


@celery_app.task(
    name="app.workers.tasks.run_monthly_reports",
    bind=True,
    # 일시 장애(DB/Slack/GCS)로 월 1회 리포트가 통째로 누락되지 않도록 자동 재시도 (P2-13).
    # 병원별 dedupe(existing_check)가 있어 재실행해도 중복 리포트는 생기지 않는다.
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    soft_time_limit=2400,
    time_limit=2700,
)
def run_monthly_reports(self):
    require_dispatch(self, "monthly-reports")
    now = arrow.now("Asia/Seoul")
    try:
        period = scheduled_report_period(now.datetime)
    except MonthlyPeriodError as exc:
        logger.info("Monthly close is not ready: %s", exc)
        return {"status": "period_not_closed"}
    anchor = arrow.get(period.ends_at).shift(microseconds=-1)

    with SyncSessionLocal() as db:
        batch_run_id = _start_monthly_report_batch_run(db, self, period)
        hospital_ids = eligible_hospital_ids(db, period)
        stmt = select(Hospital).where(Hospital.id.in_(hospital_ids))
        result = db.execute(stmt)
        eligible_hospitals = result.scalars().all()
        period_key = f"{period.year:04d}-{period.month:02d}"
        hospitals = []
        blocked: list[str] = []
        observed_at = now.datetime.astimezone(timezone.utc)
        for hospital in eligible_hospitals:
            if _hospital_requires_monthly_sov_success(
                db, hospital, period
            ) and not _monthly_sov_measurement_succeeded(db, hospital.id, period_key):
                blocked.append(hospital.name)
                measurement_run_id = (
                    _dispatch_monthly_sov_catchup(db, hospital, period_key, observed_at)
                    if is_monthly_recovery_window(
                        now.datetime, period.year, period.month
                    )
                    else None
                )
                _record_weekly_sov_failure(
                    hospital,
                    period_key,
                    "MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
                    measurement_run_id,
                    measurement_mode="monthly",
                )
                continue
            latest_run = _latest_monthly_report_operation_run(
                db, hospital.id, period.year, period.month
            )
            if latest_run is not None:
                if latest_run.state == OperationRunState.SUCCEEDED:
                    latest_report = _latest_monthly_report(
                        db, hospital.id, period.year, period.month
                    )
                    if _monthly_report_measurement_is_final(
                        latest_report
                    ) and not _has_valid_doctor_artifact(db, latest_report):
                        blocked.append(hospital.name)
                        if is_monthly_recovery_window(
                            now.datetime, period.year, period.month
                        ):
                            _dispatch_automatic_monthly_report_recovery(
                                db, hospital, period.year, period.month
                            )
                    else:
                        hospitals.append((hospital, "already_succeeded"))
                    continue
                if latest_run.state in (
                    OperationRunState.PARTIAL,
                    OperationRunState.REQUESTED,
                    OperationRunState.QUEUED,
                    OperationRunState.RUNNING,
                ):
                    if latest_run.state == OperationRunState.PARTIAL:
                        latest_report = _latest_monthly_report(
                            db, hospital.id, period.year, period.month
                        )
                        if _monthly_report_measurement_is_final(
                            latest_report
                        ) and not _has_valid_doctor_artifact(db, latest_report):
                            if is_monthly_recovery_window(
                                now.datetime, period.year, period.month
                            ):
                                _dispatch_automatic_monthly_report_recovery(
                                    db, hospital, period.year, period.month
                                )
                    blocked.append(hospital.name)
                    continue
            hospitals.append((hospital, "build"))
        failures: list[str] = []
        successes = 0

        for h, action in hospitals:
            if action == "already_succeeded":
                successes += 1
                continue
            run_id, replayed = _start_scheduled_monthly_operation_run(db, h, anchor)
            if replayed:
                existing_run = db.get(OperationRun, run_id)
                if existing_run is not None and existing_run.state == OperationRunState.SUCCEEDED:
                    successes += 1
                elif existing_run is not None and existing_run.state == OperationRunState.PARTIAL:
                    blocked.append(h.name)
                else:
                    failures.append(h.name)
                continue
            try:
                latest = _latest_monthly_report(db, h.id, anchor.year, anchor.month)
                rebuilding = latest is not None
                if latest is not None and latest.quality != "COMPLETE":
                    outcome = "coverage_incomplete"
                else:
                    outcome = _build_monthly_report_for_hospital(
                        db,
                        h,
                        anchor,
                        rebuild=rebuilding,
                        build_reason=(
                            ReportBuildReason.AUTOMATIC_RECOVERY
                            if rebuilding
                            else ReportBuildReason.SCHEDULED_CLOSE
                        ),
                        correlation_key=f"scheduled:{h.id}:{anchor.year}-{anchor.month:02d}",
                        operation_run_id=run_id,
                    )
                _finish_monthly_operation_run(db, run_id, h.id, anchor.year, anchor.month, outcome)
                finished_run = db.get(OperationRun, run_id)
                if finished_run is not None and finished_run.state == OperationRunState.PARTIAL:
                    blocked.append(h.name)
                elif finished_run is not None and finished_run.state == OperationRunState.FAILED:
                    failures.append(h.name)
                else:
                    successes += 1
            except Exception as e:
                logger.error(f"Monthly report failed for {h.name}: {e}")
                db.rollback()
                _fail_monthly_operation_run(db, run_id, h.id, anchor.year, anchor.month)
                failures.append(h.name)

        result = {
            "status": "PARTIAL"
            if blocked or (failures and successes)
            else "FAILED"
            if failures
            else "SUCCEEDED",
            "total_count": len(eligible_hospitals),
            "success_count": successes,
            "failure_count": len(failures) + len(blocked),
        }
        _finish_monthly_report_batch_run(
            db, batch_run_id, result, hard_failure=bool(failures)
        )
    if failures:
        raise MonthlyBatchIncompleteError(f"scheduled monthly reports incomplete: {len(failures)}")
    return result


@celery_app.task(name="app.workers.tasks.summarize_monthly_report_gaps")
def summarize_monthly_report_gaps():
    """Queue at most one durable prior-month gap summary per KST calendar day."""

    require_dispatch(current_task, "monthly-report-gap-summary")
    observed_at = datetime.now(timezone.utc)
    with SyncSessionLocal() as db:
        queued = enqueue_monthly_report_gap_summary_sync(db, now=observed_at)
        db.commit()
    return {"queued": queued}


@celery_app.task(
    name="app.workers.tasks.generate_monthly_report_for_hospital",
    bind=True,
    # max_retries만 두면 아무 일도 일어나지 않는다 — autoretry_for가 있어야 실제로 재시도한다.
    # PDF 렌더·GCS 업로드의 일시 장애가 곧바로 최종 실패가 되면 AE가 다시 눌러야 한다.
    # dedupe(_build_monthly_report_for_hospital)가 있어 재시도해도 중복 리포트는 생기지 않는다.
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
    soft_time_limit=600,
    time_limit=900,
)
def generate_monthly_report_for_hospital(
    self,
    hospital_id: str,
    year: int | None = None,
    month: int | None = None,
    rebuild: bool = False,
    automatic_recovery: bool = False,
):
    """병원 1곳의 월간 리포트를 수동으로 만든다 (Admin '월간 리포트 생성').

    월간 배치가 반복 실패해도 운영자가 해당 병원만 다시 만들 수 있는 복구 경로다.

    year/month를 주면 그 달을, 없으면 지난달을 만든다 — 배치가 실패했다는 사실은 보통
    달이 바뀐 뒤에 드러나므로 '지난달'이 기본값으로 맞다. 이미 리포트가 있으면 덮어쓰지
    않고 건너뛴다(배치와 같은 dedupe).
    """
    # 잘못된 요청은 재시도해도 결과가 같다 — autoretry_for에 걸리지 않도록 예외 대신
    # 상태를 반환한다. API가 먼저 같은 검사를 하므로 여기 걸리는 것은 직접 호출뿐이다.
    now = arrow.now("Asia/Seoul")
    if (year is None) != (month is None):
        logger.error(
            "Monthly report requested with a partial period: year=%s month=%s", year, month
        )
        return {"status": "invalid_period"}
    try:
        period = (
            require_closed_period(year, month, now=now.datetime)
            if year is not None and month is not None
            else scheduled_report_period(now.datetime)
        )
    except MonthlyPeriodError as exc:
        logger.error("Monthly report period is not closed: %s", exc)
        return {"status": "period_not_closed"}
    anchor = arrow.get(period.ends_at).shift(microseconds=-1)

    run_id = _monthly_operation_run_id(self)
    with SyncSessionLocal() as db:
        hospital = db.get(Hospital, uuid.UUID(str(hospital_id)))
        if hospital is None:
            logger.error(f"Monthly report requested for unknown hospital {hospital_id}")
            return {"status": "hospital_not_found"}
        if _hospital_requires_monthly_sov_success(db, hospital, period) and not (
            _monthly_sov_measurement_succeeded(
                db, hospital.id, f"{period.year:04d}-{period.month:02d}"
            )
        ):
            _mark_monthly_report_measurement_incomplete(
                db, run_id, hospital.id, period.year, period.month
            )
            return {
                "skipped": True,
                "status": "measurement_not_succeeded",
                "message": "필수 측정이 완료되지 않아 리포트를 만들지 않았습니다.",
                "year": period.year,
                "month": period.month,
            }
        _mark_monthly_operation_run_running(db, run_id, anchor.year, anchor.month)
        correlation_key = (
            f"operation-run:{run_id}"
            if run_id is not None
            else f"manual:{hospital.id}:{anchor.year}-{anchor.month:02d}"
        )
        try:
            build_kwargs = {
                "rebuild": rebuild,
                "build_reason": (
                    ReportBuildReason.AUTOMATIC_RECOVERY
                    if automatic_recovery
                    else ReportBuildReason.MANUAL_REBUILD
                ),
                "correlation_key": correlation_key,
            }
            if run_id is not None:
                build_kwargs["operation_run_id"] = run_id
            outcome = _build_monthly_report_for_hospital(db, hospital, anchor, **build_kwargs)
        except Exception as e:
            logger.error(f"Manual monthly report failed for {hospital.name}: {e}")
            db.rollback()
            # 재시도가 남아 있으면 알리지 않는다 — 일시 장애 한 번에 Slack이 세 번 울리면
            # AE가 알림을 신뢰하지 않게 된다. 마지막 시도에서만 사람을 부른다.
            if self.request.retries >= self.max_retries:
                _fail_monthly_operation_run(db, run_id, hospital.id, anchor.year, anchor.month)
            raise
        _finish_monthly_operation_run(db, run_id, hospital.id, anchor.year, anchor.month, outcome)
    return {"status": outcome, "year": anchor.year, "month": anchor.month}


def _check_custom_domain_https(
    client: httpx.Client,
    domain: str,
    *,
    expected_hospital_id: uuid.UUID,
    expected_slug: str,
) -> tuple[bool, str]:
    try:
        response = client.get(f"https://{domain}/.well-known/reputation-health")
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError:
        return False, "tls_or_network_error"
    if 300 <= response.status_code < 400:
        return False, "redirect_not_allowed"
    if response.status_code == 200:
        try:
            marker = response.json()
        except ValueError:
            return False, "invalid_tenant_marker"
        if not isinstance(marker, dict):
            return False, "invalid_tenant_marker"
        matches = (
            marker.get("hospital_id") == str(expected_hospital_id)
            and marker.get("slug") == expected_slug
            and marker.get("canonical_host") == domain
            and isinstance(marker.get("release"), str)
            and bool(marker["release"].strip())
        )
        return (True, "tenant_marker_ok") if matches else (False, "tenant_marker_mismatch")
    return False, f"http_{response.status_code}"


def _site_revalidation_context(
    run_id: uuid.UUID,
    expected_attempt_count: int,
) -> list[str] | None:
    with SyncSessionLocal() as db:
        run = db.get(OperationRun, run_id)
        if (
            run is None
            or run.state != OperationRunState.RUNNING.value
            or run.attempt_count != expected_attempt_count
        ):
            return None
        hospital = db.get(Hospital, run.hospital_id)
        if hospital is None:
            return None
        treatments = hospital.treatments if isinstance(hospital.treatments, list) else []
        if run.request_payload.get("scope") == "HOSPITAL":
            return hospital_site_paths(hospital.slug, treatments)
        raw_content_id = run.request_payload.get("content_id")
        try:
            content_id = uuid.UUID(str(raw_content_id))
        except (TypeError, ValueError):
            return None
        content = db.get(ContentItem, content_id)
        if content is None or content.hospital_id != hospital.id:
            return None
        # 복구 계획 조회(start_revalidation_failure)와 **같은** 조건을 쓴다. 현재 status로
        # 거르면 반려로 내려간 글의 재시도가 첫 실패 직후 조용히 끊긴다.
        if not content_is_revalidation_recoverable(
            content, direction=run_revalidation_direction(run.request_payload)
        ):
            return None
        # 무효화 경로는 방향과 무관하게 동일하다 — 내릴 때도 허브·목록·llms.txt를 함께 턴다.
        return content_site_paths(hospital.slug, content.id, treatments)


@celery_app.task(bind=True, name="app.workers.tasks.retry_site_revalidation")
def retry_site_revalidation(self, run_id: str, expected_attempt_count: int):
    """Retry only the public cache refresh; never repeat or undo publication."""

    require_dispatch(
        self,
        "retry-site-revalidation",
        run_id,
        args=[run_id, expected_attempt_count],
        kwargs={},
    )
    try:
        parsed_run_id = uuid.UUID(run_id)
    except (TypeError, ValueError):
        return {"status": "invalid_run"}
    context = _site_revalidation_context(parsed_run_id, expected_attempt_count)
    refreshed = False
    if context is not None:
        try:
            refreshed = bool(_run_async(trigger_site_revalidate(paths=context)))
        except Exception as exc:  # noqa: BLE001 — bounded retry records a safe code below.
            logger.warning("site revalidation retry failed: code=%s", exc.__class__.__name__)
    if refreshed:
        recorded = _run_async(record_revalidation_success(parsed_run_id, expected_attempt_count))
        return {"status": "recovered" if recorded else "stale_run"}

    plan = _run_async(record_retry_failure(parsed_run_id, expected_attempt_count))
    if plan is None:
        return {"status": "stale_run"}
    if plan.delay_seconds is not None:
        retry_site_revalidation.apply_async(
            args=[str(parsed_run_id), expected_attempt_count + 1],
            queue="control",
            priority=0,
            countdown=plan.delay_seconds,
            headers=build_dispatch_headers("retry-site-revalidation", str(parsed_run_id)),
        )
        return {"status": "retry_scheduled", "delay_seconds": plan.delay_seconds}
    return {"status": "operator_action_required"}


# ══════════════════════════════════════════════════════════════════
# 신규 런타임 도메인 HTTPS 상태 감시 — Terraform 정적 목록 밖까지 포함
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.monitor_live_custom_domains")
def monitor_live_custom_domains():
    """Persist every tenant marker check; Redis is never incident truth."""
    require_dispatch(current_task, "live-custom-domain-health")
    with SyncSessionLocal() as db:
        hospitals = (
            db.execute(
                select(Hospital).where(
                    Hospital.status == HospitalStatus.ACTIVE,
                    Hospital.site_live.is_(True),
                    Hospital.aeo_domain.is_not(None),
                )
            )
            .scalars()
            .all()
        )

    new_failures = 0
    recoveries = 0
    state_unavailable = 0
    refreshed = 0
    timeout = httpx.Timeout(10.0, connect=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for hospital in hospitals:
            domain = (hospital.aeo_domain or "").strip().lower()
            if not domain:
                continue
            healthy, reason = _check_custom_domain_https(
                client,
                domain,
                expected_hospital_id=hospital.id,
                expected_slug=hospital.slug,
            )
            # 배지·트래커가 읽는 도메인 상태를 이 관측으로 갱신한다. 인시던트 기록과
            # 달리 여기서 실패해도 감시 자체는 계속돼야 하므로 별도 세션으로 격리한다.
            refreshed += int(
                _persist_live_domain_check(
                    hospital.id,
                    LiveDomainCheck(
                        domain=domain,
                        healthy=healthy,
                        reason=reason,
                        checked_at=datetime.now(timezone.utc),
                        # 테넌트 마커 200은 DNS·TLS·라우팅이 모두 맞아야만 나온다.
                        proves_certificate=True,
                    ),
                )
            )
            try:
                outcome = _run_async(
                    record_domain_health_check(
                        hospital_id=hospital.id,
                        canonical_host=domain,
                        healthy=healthy,
                        safe_reason=reason,
                    )
                )
                new_failures += int(outcome.incident_opened)
                recoveries += int(outcome.incident_recovered)
            except Exception as exc:  # noqa: BLE001 — no fallback may invent incident truth.
                state_unavailable += 1
                logger.warning(
                    "domain health persistence unavailable: code=%s",
                    exc.__class__.__name__,
                )

            if not healthy:
                logger.warning("custom domain marker rejected: reason=%s", reason)
    return {
        "checked": len(hospitals),
        "new_failures": new_failures,
        "recoveries": recoveries,
        "state_unavailable": state_unavailable,
        "status_refreshed": refreshed,
    }


def _persist_live_domain_check(hospital_id: uuid.UUID, check: LiveDomainCheck) -> bool:
    try:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, hospital_id)
            if hospital is None or not apply_live_domain_check(hospital, check):
                return False
            db.commit()
            return True
    except Exception as exc:  # noqa: BLE001 — 상태 갱신 실패가 감시를 멈추면 안 된다.
        logger.warning("domain status refresh failed: code=%s", exc.__class__.__name__)
        return False


# ══════════════════════════════════════════════════════════════════
# Lead PII 보관기간 자동 파기 — 개인정보보호법 제21조
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.purge_expired_leads")
def purge_expired_leads():
    """retain_until 도달 lead의 PII를 익명화하고 purged_at을 기록한다.

    Soft-delete: 통계용 메타(clinic_type, source_path, consent_version)는 유지하되
    개인 식별 가능 필드(clinic_name, contact, question, consent_ip)는 즉시 폐기한다.
    이미 처리된 row는 skip.

    정상 파기는 DB 증적과 로그로 종료하고, 파기 실패가 남을 때만 Slack으로 올린다.
    """
    from app.models.hospital import Hospital
    from app.models.lead import SalesLead
    from app.services.lead_privacy import purge_lead_completely, scrub_onboarding_note

    require_dispatch(current_task, "purge-expired-leads")
    now = datetime.now(timezone.utc)
    purged = 0
    stuck = 0
    error_msg: str | None = None
    try:
        with SyncSessionLocal() as db:
            stmt = select(SalesLead).where(
                SalesLead.purged_at.is_(None),
                SalesLead.retain_until.is_not(None),
                SalesLead.retain_until <= now,
            )
            leads = db.execute(stmt).scalars().all()
            for lead in leads:
                # **리드별로 커밋한다.** 한 트랜잭션에 묶으면 한 건의 실패(GCS 장애,
                # 제약 위반)가 그날 파기 대상 **전부**를 롤백시키고, 같은 독성 행이
                # 다음 날 다시 선택되어 영구 반복된다 — 법정 파기 의무가 조용히 멈춘다.
                try:
                    if purge_lead_completely(db, lead, now)["anonymized"]:
                        purged += 1
                        # CDX-M2: 전환된 병원의 onboarding_note에 복사된 운영자 자유
                        # 텍스트도 함께 파기 (lead row만 익명화하면 라이프사이클 우회).
                        if lead.converted_hospital_id:
                            hospital = db.get(Hospital, lead.converted_hospital_id)
                            if hospital and hospital.onboarding_note:
                                hospital.onboarding_note = scrub_onboarding_note(
                                    hospital.onboarding_note, lead.id
                                )
                        db.commit()
                    else:
                        db.rollback()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    stuck += 1
                    logger.exception("lead purge failed for %s: %s", lead.id, exc)
        logger.info("purge_expired_leads: anonymized %s expired leads (%s stuck)", purged, stuck)
        if stuck:
            error_msg = f"{stuck}건이 파기에 실패했습니다 (로그 확인 필요)"
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("purge_expired_leads failed")

    if error_msg:
        try:
            _run_async(notifier.notify_lead_purge_result(purged=purged, error=error_msg))
        except Exception:
            logger.exception("purge_expired_leads slack notify failed (non-fatal)")

    return {"purged": purged, "stuck": stuck, "error": error_msg}


@celery_app.task(
    name="app.workers.tasks.backfill_indexnow",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
)
def backfill_indexnow(self, hospital_id: str | None = None, dry_run: bool = False):
    """이미 발행된 콘텐츠를 IndexNow에 소급 제출한다.

    발행 훅(nightly_content_publish)은 **앞으로 나가는 글**에만 걸린다. 훅을 붙이기 전에
    이미 발행된 글은 색인 신호를 한 번도 받은 적이 없다. 2026-07-29 측정에서 대장내시경
    주제 콘텐츠 7편이 질의와 제목이 거의 일치하는데도 85회 측정 중 인용 0회였던 것이
    이 경우일 수 있어, 한 번 밀어 넣고 재측정해 확인한다.

    발행 revision과 URL 묶음으로 멱등인 내구 의도를 저장한다. 실제 IndexNow 호출과
    bounded 재시도는 별도 저우선 drainer가 맡아 이 작업의 DB 트랜잭션을 막지 않는다.

    사용:
        backfill_indexnow.delay()                       # 전체
        backfill_indexnow.delay(hospital_id="...")      # 특정 병원
        backfill_indexnow.delay(dry_run=True)           # 의도 저장 없이 대상만 집계
    """
    if not indexnow.is_configured():
        logger.warning("backfill_indexnow: INDEXNOW_KEY 미설정 — 건너뜀")
        return {"skipped": "not_configured"}

    summary: list[dict] = []
    with SyncSessionLocal() as db:
        stmt = select(Hospital)
        if hospital_id:
            stmt = stmt.where(Hospital.id == hospital_id)
        hospitals = db.execute(stmt).scalars().all()

        for hospital in hospitals:
            # 공개되지 않은 병원의 URL을 색인에 넣으면 미완성 페이지가 노출된다.
            if not getattr(hospital, "site_live", False):
                continue

            content_rows = db.execute(
                select(ContentItem.id, ContentItem.content_revision)
                .where(ContentItem.hospital_id == hospital.id)
                .where(ContentItem.status == ContentStatus.PUBLISHED)
                .order_by(ContentItem.scheduled_date, ContentItem.id)
            ).all()
            content_ids = [row.id for row in content_rows]
            revision_digest = hashlib.sha256(
                "|".join(
                    f"{row.id}:{int(row.content_revision or 1)}" for row in content_rows
                ).encode()
            ).hexdigest()

            base, urls = indexnow.hospital_all_urls(
                slug=hospital.slug,
                aeo_domain=hospital.aeo_domain,
                treatments=hospital.treatments,
                content_ids=content_ids,
            )

            entry = {
                "hospital": hospital.name,
                "base_url": base,
                "contents": len(content_ids),
                "urls": len(urls),
            }
            if dry_run:
                entry["queued"] = False
            else:
                for start in range(0, len(urls), indexnow.MAX_URLS_PER_REQUEST):
                    indexnow.enqueue_urls_sync(
                        db,
                        base_url=base,
                        urls=urls[start : start + indexnow.MAX_URLS_PER_REQUEST],
                        revision=(
                            f"backfill:{revision_digest}:"
                            f"{start // indexnow.MAX_URLS_PER_REQUEST}"
                        ),
                        slug=hospital.slug,
                    )
                entry["queued"] = True
            summary.append(entry)
            logger.info(
                "backfill_indexnow: %s (%s) 콘텐츠 %d편 · URL %d개 · durable queue=%s",
                hospital.name,
                base,
                len(content_ids),
                len(urls),
                entry["queued"],
            )

        if not dry_run:
            db.commit()

    total_urls = sum(e["urls"] for e in summary)
    queued = sum(1 for e in summary if e["queued"])
    logger.info(
        "backfill_indexnow 완료: 병원 %d곳 · URL %d개 · durable queue %d곳 (dry_run=%s)",
        len(summary),
        total_urls,
        queued,
        dry_run,
    )
    return {"hospitals": summary, "total_urls": total_urls, "queued": queued, "dry_run": dry_run}
