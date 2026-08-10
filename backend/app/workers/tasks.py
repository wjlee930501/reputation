# allow: SIZE_OK -- Celery task registry keeps legacy task import names; release-critical helpers are split by task family.
"""
Celery 태스크 전체
- trigger_v0_report: 프로파일 완료 시 V0 분석 트리거
- build_aeo_site: 콘텐츠 허브 공개 노출 상태 준비 (legacy task name)
- nightly_content_generation: 매일 밤 내일 콘텐츠 생성
- morning_content_auto_publish: 매일 아침 오늘 콘텐츠 자동 발행 + 후행 확인 Slack
- run_sov_for_hospital: 단일 병원 AI 답변 언급률 측정
- run_weekly_monitoring: 전체 병원 주간 측정
- adjust_query_priorities: AI 답변 언급 결과 기반 질문 우선순위 조정
- run_monthly_reports: 전체 병원 월간 리포트
"""

import asyncio
import hashlib
import logging
import threading
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import arrow
import httpx
from celery import current_task
from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload, selectinload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal, get_async_sessionmaker
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
    PhilosophyStatus,
    SourceStatus,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.operations import OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, AIQueryVariant, MeasurementRun, QueryMatrix, SovRecord
from app.services import cost_guard, indexnow, notifier
from app.services.audit_log import write_audit_log_sync
from app.services.content_engine import generate_content
from app.services.content_publication import (
    apply_publication_assessment,
    assess_content_publication,
)
from app.services.content_publish_notifications import enqueue_publish_notification_sync
from app.services.content_publish_reconciliation import reconcile_sent_publish_notifications
from app.services.content_publish_state import recover_publish_notification_sync
from app.services.content_target_planner import prepare_automatic_content_brief_sync
from app.services.doctor_report_artifact import generate_doctor_pdf_report
from app.services.domain_health_control import record_domain_health_check
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    build_monthly_essence_summary,
    compute_source_content_hash,
    metered_llm_calls,
    process_source_asset,
    screen_content_against_philosophy,
    validate_source_excerpt,
)
from app.services.essence_readiness import get_current_approved_philosophy_sync
from app.services.image_engine import generate_image
from app.services.monthly_events import MonthlyRunStage
from app.services.monthly_manifest import (
    ManifestError,
    apply_manifest_to_report,
    close_manifest,
    freeze_dispatch_manifest,
    link_attempt,
)
from app.services.monthly_period import (
    MonthlyPeriodError,
    ReportBuildReason,
    eligible_hospital_ids,
    lock_report_version_plan,
    prior_month_to_close,
    reporting_period,
    require_closed_period,
)
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest
from app.services.report_artifact_validation import (
    DoctorPdfValidationError,
    parse_doctor_artifact_metadata,
)
from app.services.report_attribution import (
    ContentAttributionInput,
    build_content_attribution_summary,
)
from app.services.report_engine import (
    build_doctor_report_view,
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
    record_retry_failure,
    record_revalidation_success,
)
from app.services.sov_engine import (
    MENTION_RATE_INTENTS,
    calculate_sov,
    classify_query_intent,
    generate_query_matrix_specs,
    run_single_query,
)
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.workers.content_publication_block_control import ensure_publication_block_run
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.generation_batch_run import GenerationBatchRecorder
from app.workers.generation_incident_control import (
    open_generation_incident,
    recover_generation_incidents,
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
from app.workers.monthly_slots import create_next_month_slots_for_schedule
from app.workers.nightly_generation_batch import (
    GENERATION_CATCHUP_DAYS,
    NIGHTLY_GENERATION_CAP,
    _load_nightly_generation_batch,
    _nightly_generation_stmt,  # noqa: F401 — test_tasks_nightly가 tasks 경유로 참조하는 re-export
    load_stuck_claims,
    release_unfinished_claims,
    write_back_generated_content,
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


class MonthlyBatchIncompleteError(RuntimeError):
    """Raised after durable per-hospital failures so Celery schedules a retry."""

SOV_REPEAT_WEEKLY = min(settings.SOV_REPEAT_COUNT_WEEKLY, 20)  # 주간 측정용
V0_REPEAT_COUNT = 5  # V0 첫 측정 쿼리당 반복 횟수
# V0 첫 측정에 쓰는 질문 개수.
# 5였을 때: 플랫폼당 25개 관측이라 1건 차이로 언급률이 4%p씩 튀었다(±8%p 수준).
# 원장에게 처음 보여주는 '진단서'의 오차로는 너무 크다. 타임아웃 수정과 luna 전환으로
# 측정이 빨라져(p50 24.7s) 15개로 늘려도 태스크 예산 안에 들어온다
# (15 × 5회 × 2플랫폼 = 150호출 ÷ 동시10 × 25s ≈ 375s < soft_time_limit 1800s).
V0_QUERY_SAMPLE_COUNT = 15


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
        )
        .order_by(QueryMatrix.created_at, QueryMatrix.query_text)
        .limit(V0_QUERY_SAMPLE_COUNT)
    )


# 주간 측정에서 HIGH 우선순위 쿼리 spec 상한 — target 자동 시드로 매트릭스가 폭증해도
# 매주 전량 측정되며 API 비용이 무한정 늘지 않도록 태스크 측에서 잘라낸다.
SOV_HIGH_PRIORITY_CAP = settings.SOV_HIGH_PRIORITY_CAP

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


_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis

        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


def _already_done(key: str) -> bool:
    """Idempotency READ — True if this daily run was already marked done (CELERY-4).

    Fail-open: returns False on a Redis error so a transient broker hiccup never
    silently drops a scheduled run (better to risk a duplicate than to lose it).
    """
    try:
        return _get_redis().get(key) is not None
    except Exception:
        logger.warning("Redis idempotency read unavailable for %s; proceeding", key)
        return False


def _mark_done(key: str, ttl_seconds: int = 82_800) -> None:
    """Mark a daily run done AFTER its side effects succeeded (claim-after-success).

    Claiming before the work would forfeit the entire day's notification on a
    mid-task crash, since the beat fires only once/day and the key would block any
    re-trigger for ~23h.
    """
    try:
        _get_redis().set(key, "1", ex=ttl_seconds)
    except Exception:
        logger.warning("Redis idempotency mark unavailable for %s", key)


def _generation_missed_alert_key(hospital_id: uuid.UUID | str, content_ids: list[uuid.UUID]) -> str:
    """같은 미생성 항목 집합은 날짜가 바뀌어도 같은 알림 상태로 취급한다."""
    fingerprint = hashlib.sha256(
        "|".join(sorted(str(content_id) for content_id in content_ids)).encode()
    ).hexdigest()[:16]
    return f"content_generation_missed:{hospital_id}:{fingerprint}"


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
            hospital = db.get(Hospital, uuid.UUID(hospital_id))
            if hospital and hospital.status == HospitalStatus.ANALYZING:
                hospital.status = HospitalStatus(prior_status)
                db.commit()
    except Exception:
        logger.exception("Failed to reset ANALYZING status for hospital %s", hospital_id)


# V0 클레임의 최대 생존 시간. 태스크 하드 리밋(time_limit=2100s)보다 넉넉히 잡아,
# 정상 실행이 진행 중인데 다른 실행이 클레임을 뺏어가는 일이 없게 한다.
V0_CLAIM_MAX_AGE_SECONDS = 2400


def _v0_claim_is_alive(db, hospital_id: uuid.UUID) -> bool:
    """ANALYZING 클레임이 아직 살아 있는 실행의 것인가.

    측정 실행(MeasurementRun)을 클레임의 하트비트로 쓴다. RUNNING 상태의 최근 실행이
    있으면 진행 중, 없거나 하드 리밋을 넘겼으면 죽은 클레임으로 본다.

    상태 컬럼만으로는 판단할 수 없다 — 하드 종료된 실행은 상태를 되돌리지 못하고
    죽으므로, ANALYZING은 "진행 중"과 "죽은 채 방치됨"을 구분하지 못한다.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=V0_CLAIM_MAX_AGE_SECONDS)
    running = db.execute(
        select(MeasurementRun.id)
        .where(
            MeasurementRun.hospital_id == hospital_id,
            MeasurementRun.status == "RUNNING",
            MeasurementRun.started_at.isnot(None),
            MeasurementRun.started_at >= cutoff,
        )
        .limit(1)
    )
    return running.scalar() is not None


def _ensure_v0_has_successful_measurements(success_count: int, failure_count: int) -> None:
    if success_count <= 0:
        raise RuntimeError(
            f"V0 리포트를 만들 수 있는 성공 측정 결과가 없습니다 (실패 {failure_count}건)"
        )


def _raise_if_monthly_report_failures(failures: list[tuple[str, Exception]]) -> None:
    if not failures:
        return
    names = ", ".join(name for name, _exc in failures[:5])
    suffix = "" if len(failures) <= 5 else f" 외 {len(failures) - 5}건"
    raise RuntimeError(f"월간 리포트 실패: {names}{suffix}")


def _mark_source_processing_error(source_id: uuid.UUID, error: Exception) -> None:
    """Persist a terminal source-processing error without masking the original failure."""
    try:
        with SyncSessionLocal() as db:
            source = db.get(HospitalSourceAsset, source_id)
            if source and source.status != SourceStatus.EXCLUDED:
                source.status = SourceStatus.ERROR
                source.process_error = str(error)[:2000]
                db.commit()
    except Exception:
        logger.exception("Failed to persist source-processing error for %s", source_id)


# ══════════════════════════════════════════════════════════════════
# 온보딩 근거 자료 비동기 처리
# ══════════════════════════════════════════════════════════════════
async def _metered_process_source_asset(source):
    """동기 근거 추출을 실제 공급자 호출 계수와 함께 실행한다."""
    async with metered_llm_calls():
        return await asyncio.to_thread(process_source_asset, source)


@celery_app.task(
    name="app.workers.tasks.process_source_asset_task",
    bind=True,
    max_retries=2,
    soft_time_limit=300,
    time_limit=330,
)
def process_source_asset_task(self, source_id: str) -> dict[str, object]:
    """Extract evidence notes for one pending onboarding source.

    Admin의 일괄 처리 엔드포인트가 이 태스크 이름을 그대로 큐잉한다. 구현을 워커
    모듈에 두어야 Celery가 기동 시 등록하고, 중복 전달에도 안전하게 복귀한다.
    """
    source_uuid = uuid.UUID(source_id)
    hospital_slug: str | None = None
    hospital_name: str | None = None
    should_revalidate = False

    try:
        with SyncSessionLocal() as db:
            source = db.get(HospitalSourceAsset, source_uuid)
            if not source:
                logger.warning("Source asset not found; skipping: %s", source_uuid)
                return {"source_id": source_id, "status": "NOT_FOUND"}
            if source.status == SourceStatus.EXCLUDED:
                return {"source_id": source_id, "status": SourceStatus.EXCLUDED.value}
            if source.status == SourceStatus.PROCESSED:
                return {"source_id": source_id, "status": SourceStatus.PROCESSED.value}
            if not source.raw_text or not source.raw_text.strip():
                raise ValueError("자료 본문이 없는 URL 전용 자료는 처리할 수 없습니다.")

            # 일괄 근거 추출도 Admin 화면 경로와 같은 유료 호출이다 — 같은 예산에 계수한다.
            payloads = _run_async(_metered_process_source_asset(source))
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
            source.status = SourceStatus.PROCESSED
            source.process_error = None
            source.processed_at = datetime.now(timezone.utc)
            source.content_hash = compute_source_content_hash(
                source.title,
                source.url,
                source.raw_text,
                source.operator_note,
            )

            hospital = db.get(Hospital, source.hospital_id)
            if hospital:
                hospital_slug = hospital.slug
                hospital_name = hospital.name
                should_revalidate = hospital.status == HospitalStatus.ACTIVE and bool(
                    hospital.site_live
                )
            db.commit()

        if should_revalidate and hospital_slug:
            _run_async(
                trigger_hospital_site_revalidate_safe(
                    hospital_slug,
                    hospital_name=hospital_name,
                )
            )
        return {
            "source_id": source_id,
            "status": SourceStatus.PROCESSED.value,
            "evidence_note_count": len(notes),
        }
    except ValueError as exc:
        _mark_source_processing_error(source_uuid, exc)
        logger.warning("Source processing rejected for %s: %s", source_uuid, exc)
        return {"source_id": source_id, "status": SourceStatus.ERROR.value, "error": str(exc)}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))
        _mark_source_processing_error(source_uuid, exc)
        logger.exception("Source processing failed after retries: %s", source_uuid)
        raise


# ══════════════════════════════════════════════════════════════════
# V0 리포트
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.trigger_v0_report",
    bind=True,
    max_retries=2,
    soft_time_limit=1800,
    time_limit=2100,
)
def trigger_v0_report(self, hospital_id: str):
    """프로파일 완료 후 V0 분석 즉시 실행"""
    require_dispatch(self, "trigger-v0-report", hospital_id)
    prior_status: str | None = None  # ANALYZING 전환 전 상태 — 실패 시 복원용 (P2-15)
    try:
        with SyncSessionLocal() as db:
            hospital_uuid = uuid.UUID(hospital_id)
            # check-and-set 직렬화 (P2-15): 프로파일 저장 트리거와 수동 재트리거가 동시에
            # 들어와도 v0_report_done/ANALYZING 검사를 둘 다 통과해 매트릭스·측정 비용이
            # 중복 발생하지 않게 병원 단위 advisory lock으로 묶는다.
            acquire_hospital_advisory_lock_sync(db, hospital_uuid)
            hospital = db.get(Hospital, hospital_uuid)
            if not hospital:
                return

            # Idempotency: 이미 V0가 완료된 병원은 재트리거/재배달 시 중복 리포트를 만들지 않는다.
            if hospital.v0_report_done:
                logger.info("V0 report already done for %s; skipping re-trigger", hospital.name)
                return

            # in-progress 가드: 다른 실행이 이미 ANALYZING으로 클레임했다면 중복 측정 금지.
            #
            # 단, ANALYZING만 보고 판단하면 안 된다. 실패 경로는 _reset_v0_analyzing_status로
            # 상태를 복원하지만 **하드 종료(SIGKILL·OOM·Cloud Run scale-in)에서는 except가
            # 실행되지 않는다**. 그러면 재배달된 실행이 ANALYZING을 보고 조용히 return하고,
            # v0_report_done은 영원히 False로 남아 STEP4까지 함께 멈춘 채 Slack 신호도 없다.
            # 그래서 클레임의 생존 여부를 측정 실행 기록으로 확인해 만료된 클레임은 탈취한다.
            if hospital.status == HospitalStatus.ANALYZING:
                if _v0_claim_is_alive(db, hospital.id):
                    logger.info(
                        "V0 report already in progress for %s; skipping duplicate", hospital.name
                    )
                    return
                logger.warning(
                    "Reclaiming a stale V0 ANALYZING claim for %s — the previous run died "
                    "without releasing it",
                    hospital.name,
                )

            prior_status = (
                hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
            )
            hospital.status = HospitalStatus.ANALYZING
            db.commit()

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

            # AI 답변 언급률 측정 (V0: 쿼리 수 제한, 빠른 실행)
            run = _start_measurement_run(
                db,
                hospital,
                run_label="V0 first measurement",
                config={"source": "trigger_v0_report", "repeat_count": V0_REPEAT_COUNT},
            )
            all_records = []
            success_count = 0
            failure_count = 0
            result = db.execute(v0_sample_query_stmt(hospital.id))
            sample_queries = result.scalars().all()

            platforms = ["chatgpt"]
            if settings.GEMINI_API_KEY:
                platforms.append("gemini")
            competitors = hospital.competitors or []

            # 비용 가드: V0 측정 예산 확인(쿼리 × 플랫폼 × **반복 횟수**).
            # 반복 횟수를 빼면 실제 호출의 1/V0_REPEAT_COUNT만 예약하게 되어 가드가
            # 5배 적게 센다. run_single_query가 repeat_count만큼 실제 호출을 낸다.
            # V0는 사람이 기다리는 플로우이므로 차단 시 ANALYZING을 이전 상태로 되돌리고,
            # 명확한 실패 사유를 ops Slack으로 보낸다.
            v0_units = sov_budget_units(
                query_count=len(sample_queries),
                platform_count=len(platforms),
                repeat_count=V0_REPEAT_COUNT,
            )
            v0_decision = _run_async(cost_guard.check_and_increment("sov", count=v0_units))
            if not v0_decision.allowed:
                logger.warning(
                    "V0 측정이 비용 가드로 차단됨: %s — %s", hospital.name, v0_decision.reason
                )
                if prior_status:
                    hospital.status = HospitalStatus(prior_status)
                    db.commit()
                _run_async(
                    notifier.notify_ops_alert(
                        title="V0 리포트 비용 가드 차단",
                        message=(
                            f"병원: *{hospital.name}*\n"
                            f"사유: {v0_decision.reason}\n"
                            f"V0 진단 측정({v0_units} 호출)이 차단돼 리포트가 생성되지 않았습니다. "
                            f"상한/킬스위치를 조정한 뒤 Admin에서 V0를 재트리거해 주세요."
                        ),
                    )
                )
                return

            for q in sample_queries:
                for platform in platforms:
                    results = _run_async(
                        run_single_query(
                            hospital.name,
                            q.query_text,
                            platform,
                            repeat_count=V0_REPEAT_COUNT,
                            competitors=competitors,
                        )
                    )
                    for r in results:
                        measurement_status, _failure_reason = _measurement_status_for_result(r)
                        if measurement_status == "SUCCESS":
                            success_count += 1
                        else:
                            failure_count += 1
                        record = _build_sov_record_from_result(
                            hospital_id=hospital.id,
                            query_id=q.id,
                            measurement_run_id=run.id,
                            platform=platform,
                            result=r,
                        )
                        db.add(record)
                        # 언급률 분모가 유형을 알아야 INFO(이길 수 없는 질문)를 뺄 수 있다.
                        all_records.append({**r, "query_intent": q.query_intent})

            _finish_measurement_run(run, success_count, failure_count)
            db.commit()
            _ensure_v0_has_successful_measurements(success_count, failure_count)

            # AI 답변 언급률 계산 (성공 측정 0건이면 None — 위 _ensure로 이미 방어됨)
            sov_pct = calculate_sov(all_records)

            # PDF 리포트 생성
            now = arrow.now("Asia/Seoul")
            pdf_path = generate_pdf_report(
                hospital=hospital,
                period_start=now.shift(days=-7).datetime,
                period_end=now.datetime,
                report_type="V0",
                sov_pct=sov_pct,
                repeat_count=V0_REPEAT_COUNT,
            )

            # DB 저장
            report = MonthlyReport(
                hospital_id=hospital.id,
                period_year=now.year,
                period_month=now.month,
                report_type="V0",
                pdf_path=pdf_path,
                sov_summary={"sov_pct": sov_pct, "platforms": platforms},
            )
            db.add(report)
            hospital.v0_report_done = True
            hospital.status = HospitalStatus.BUILDING
            db.commit()

            # Slack 알림 (실제 측정 플랫폼 라벨 전달 — Gemini 미측정 시 라벨에서 제외)
            _run_async(
                notifier.notify_v0_report_ready(
                    hospital.name, sov_pct, pdf_path, platforms=platforms
                )
            )

            # V0 QueryMatrix → AIQueryTarget 자동 시드 (노출 보완 탭 즉시 활성화)
            # V0 리포트·Slack이 이미 커밋·발송 완료된 뒤 실행하므로, 시드 실패는
            # V0 결과를 롤백하지 않고 로그만 남긴다 (post-commit side effect 격리).
            _seed_query_targets_from_matrix_sync(hospital.id)

            # 콘텐츠 허브 공개 노출 상태 준비 태스크 큐잉 — V0가 이미 커밋된 뒤의 post-commit
            # 사이드이펙트다. 큐잉 실패가 outer except로 흘러가면 self.retry가 v0_report_done
            # 멱등 가드에 막혀 STEP4가 영구 유실되므로, 여기서 격리하고 실패는 ops 알림만 낸다.
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
                        notifier.notify_ops_alert(
                            title="콘텐츠 허브 준비 태스크 큐잉 실패",
                            message=(
                                f"병원: *{hospital.name}* (`{hospital_id}`)\n"
                                f"V0 리포트는 정상 생성됐으나 콘텐츠 허브 준비(build_aeo_site) 큐잉에 "
                            f"실패했습니다. 자동 복구 작업이 다시 큐잉합니다."
                            ),
                        )
                    )
                except Exception:
                    logger.exception("build_aeo_site enqueue-failure ops alert delivery failed")

    except Exception as exc:
        logger.error(f"trigger_v0_report failed: {exc}")
        # 이 실행이 ANALYZING을 클레임했다면 복원 — 그래야 재시도/수동 재트리거가
        # in-progress 가드를 통과한다 (P2-15).
        _reset_v0_analyzing_status(hospital_id, prior_status)
        if self.request.retries >= self.max_retries:
            # 재시도 소진 — 병원이 ANALYZING에 갇히지 않게 복원했음을 운영자에게 알린다.
            try:
                _run_async(
                    notifier.notify_ops_alert(
                        title="V0 리포트 생성 최종 실패",
                        message=(
                            f"병원 ID: `{hospital_id}`\n"
                            f"재시도 {self.max_retries}회 모두 실패했습니다. "
                            f"병원 상태는 이전 상태({prior_status or '유지'})로 복원했습니다.\n"
                            f"오류: `{str(exc)[:200]}`\n"
                            f"원인 확인 후 Admin에서 V0 리포트를 수동 재실행해 주세요."
                        ),
                    )
                )
            except Exception:
                logger.exception("V0 final-failure ops alert delivery failed (non-fatal)")
            raise exc
        raise self.retry(exc=exc, countdown=120)


# ══════════════════════════════════════════════════════════════════
# 콘텐츠 허브 공개 노출 상태 준비
# ══════════════════════════════════════════════════════════════════
def _public_site_url(aeo_domain: str | None, slug: str | None) -> str:
    """실제 접근 가능한 공개 허브 URL을 만든다.

    site.py의 호스트 라우팅 규칙과 일치시킨다:
      1. 병원 자기 도메인(aeo_domain)이 있으면 https://{aeo_domain}/
      2. 없으면 기본 서브도메인 https://{slug}.{platform host}/  (SITE_BASE_URL 호스트 파생)
    존재하지 않던 하드코딩 preview.motionlabs.io를 대체한다.
    """
    if aeo_domain:
        return f"https://{aeo_domain}/"
    host = (urlparse(settings.SITE_BASE_URL).hostname or "").lower()
    if host and slug:
        return f"https://{slug}.{host}/"
    return settings.SITE_BASE_URL


def _site_build_prerequisites_met(hospital: Hospital) -> bool:
    return bool(hospital.profile_complete and hospital.v0_report_done)


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
    """콘텐츠 허브 노출 상태 전환 + Slack 알림 (legacy task name; 실제 공개 화면은 Next.js /site 담당)"""
    require_dispatch(self, "build-aeo-site", hospital_id)
    with SyncSessionLocal() as db:
        hospital = db.get(Hospital, uuid.UUID(hospital_id))
        if not hospital:
            return
        if not _site_build_prerequisites_met(hospital):
            logger.warning(
                "Skipping site build before profile/V0 gates: hospital_id=%s profile_complete=%s v0_report_done=%s",
                hospital.id,
                hospital.profile_complete,
                hospital.v0_report_done,
            )
            return
        if hospital.site_built:
            return

        hospital.site_built = True
        # ACTIVE/PAUSED 병원을 강등하지 않는다 — admin의 "허브 재준비"나 도메인 재저장이
        # 라이브 공개 허브를 PENDING_DOMAIN으로 떨어뜨려 공개 표면 전체가 404 되는 것 방지.
        # (공개 엔드포인트는 status==ACTIVE && site_live 필수.) 도메인이 실제로 바뀐 경우의
        # 강등은 connect_domain이 검증 무효화와 함께 명시적으로 수행한다.
        if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PAUSED):
            hospital.status = HospitalStatus.PENDING_DOMAIN
        db.commit()

        # PENDING_DOMAIN is intentionally not public, so a public preview URL is a
        # guaranteed 404. Send the AE to the control plane activation step instead.
        admin_url = (
            f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{hospital.id}/profile#domain-setup"
        )
        notified = _run_async(notifier.notify_site_built(hospital.name, admin_url))
        if not notified:
            raise RuntimeError("site build Slack notification was not delivered")


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
    """내일 발행 예정인 콘텐츠를 오늘 밤에 생성.

    catch-up window (P1-3/R1): 야간 배치가 누락(워커 다운 등)돼도 슬롯이 영구 고아가
    되지 않도록 '오늘-{GENERATION_CATCHUP_DAYS}일 ~ 내일' 범위의 미생성 슬롯을 함께
    집어 재시도한다.
    """
    require_dispatch(self, "nightly-content-generation")
    now_kst = arrow.now("Asia/Seoul")
    window_start = now_kst.shift(days=-GENERATION_CATCHUP_DAYS).date()
    tomorrow = now_kst.shift(days=1).date()

    with SyncSessionLocal() as db:
        task_id = str(getattr(self.request, "id", None) or uuid.uuid4())
        recorder = GenerationBatchRecorder(db, task_id, window_start, tomorrow)
        items, truncated_count = _load_nightly_generation_batch(db, window_start, tomorrow)

        if truncated_count:
            # 상한 절단은 조용히 슬롯을 버리는 것과 같다 — 반드시 로그 + Slack (P1-3).
            logger.warning(
                "nightly_content_generation cap reached: %d items deferred beyond cap %d",
                truncated_count,
                NIGHTLY_GENERATION_CAP,
            )
            _run_async(
                notifier.notify_ops_alert(
                    title="야간 콘텐츠 생성 상한 초과",
                    message=(
                        f"생성 대기 슬롯이 배치 상한({NIGHTLY_GENERATION_CAP}건)을 초과해 "
                        f"{truncated_count}건이 이번 실행에서 처리되지 못했습니다.\n"
                        f"대상 기간: {window_start} ~ {tomorrow}\n"
                        f"미처리분은 다음 야간 배치에서 재시도됩니다. 누적이 계속되면 "
                        f"워커 증설 또는 수동 재생성이 필요합니다."
                    ),
                )
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
                _run_async(
                    notifier.notify_ops_alert(
                        title="야간 콘텐츠 생성이 빈손으로 종료",
                        message=(
                            f"{len(stuck_items)}건이 직전 실행의 claim에 잠겨 이번 배치가 아무것도 "
                            f"생성하지 못했습니다. 직전 워커가 비정상 종료했을 수 있습니다."
                        ),
                    )
                )
            else:
                logger.info(f"No content to generate for {window_start}~{tomorrow}")
            recorder.finish()
            return

        claimed_item_ids = [item.id for item in items]

        # 병원별 생성 성공/실패/차단 추적 → 배치 완료 후 요약 Slack
        hospital_stats: dict[str, dict] = {}

        for item in items:
            item_id = item.id
            hospital = item.hospital
            hospital_key = str(hospital.id)
            hospital_id = hospital.id
            hospital_name = hospital.name
            claim_time = item.generation_claimed_at
            item_state = GenerationItemState.SUCCEEDED

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
                    )
                )

            if hospital_key not in hospital_stats:
                hospital_stats[hospital_key] = {
                    "name": hospital.name,
                    "generated": 0,
                    "failed": 0,
                    # "skipped"는 **운영 기준 미승인 차단 전용**이다 — 그 값이 그대로
                    # notify_generation_blocked_no_philosophy의 blocked_count로 나간다.
                    # 다른 사유의 건너뜀을 여기에 더하면 잘못된 사유로 알림이 발송된다.
                    "skipped": 0,
                    "cost_blocked": 0,
                    # 생성 도중 운영자가 상태를 바꿔(취소 등) 결과를 버린 건수.
                    "discarded": 0,
                    # 본문은 저장됐지만 대표 이미지가 없는 건수. 생성 성공으로만 집계하면
                    # 이미지 없는 글이 나간 것을 로그 말고는 알 길이 없다.
                    "image_missing": 0,
                }

            try:
                # 기존 제목 목록 (중복 방지)
                existing = db.execute(
                    select(ContentItem.title).where(
                        ContentItem.hospital_id == hospital.id,
                        ContentItem.title.isnot(None),
                    )
                )
                existing_titles = [r[0] for r in existing.all()]

                philosophy = get_current_approved_philosophy_sync(db, hospital.id)
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
                    db.commit()
                    logger.warning(
                        f"Skipping content generation without approved clinic writing standard: {hospital.name}"
                    )
                    hospital_stats[hospital_key]["skipped"] += 1
                    code = "MISSING_APPROVED_ESSENCE"
                    message = "승인된 콘텐츠 운영 기준을 먼저 승인해 주세요."
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
                    hospital_stats[hospital_key]["cost_blocked"] += 1
                    code = "COST_BLOCKED"
                    message = "비용 가드가 생성을 보류했습니다. 운영 센터에서 한도를 확인해 주세요."
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
                content_data = _run_async(
                    generate_content(
                        hospital,
                        item.content_type,
                        existing_titles,
                        philosophy,
                        approved_brief,
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
                screening_probe = ContentItem(
                    title=content_data["title"],
                    body=content_data["body"],
                    meta_description=content_data.get("meta_description"),
                    faq_question=content_data.get("faq_question"),
                    faq_answer_summary=content_data.get("faq_answer_summary"),
                )
                screening = screen_content_against_philosophy(screening_probe, philosophy)

                written = write_back_generated_content(
                    db,
                    item_id=item.id,
                    values={
                        "title": content_data["title"],
                        "body": content_data["body"],
                        "meta_description": content_data.get("meta_description"),
                        "references_list": content_data.get("references") or [],
                        "faq_question": content_data.get("faq_question"),
                        "faq_answer_summary": content_data.get("faq_answer_summary"),
                        "generated_at": now,
                        "body_updated_at": now,
                        "status": ContentStatus.DRAFT,
                        "content_philosophy_id": philosophy.id,
                        "essence_status": screening.status,
                        "essence_check_summary": screening.summary,
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
                    hospital_stats[hospital_key]["discarded"] += 1
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

                # 대표 이미지 생성 (gpt-image-2, 제목 주제 주입 — 실패해도 텍스트는 유지)
                try:
                    image_url, image_prompt = _run_async(
                        generate_image(item.content_type, hospital.slug, topic=item.title)
                    )
                    if not image_url:
                        # generate_image는 실패·비용차단을 ("", "") 센티널로 알린다.
                        # 그대로 대입하면 기존 이미지를 지우게 되므로 값이 있을 때만 쓴다.
                        hospital_stats[hospital_key]["image_missing"] += 1
                        logger.warning(
                            "Image generation returned no URL for %s (text saved)", item.id
                        )
                        item_state = GenerationItemState.PARTIAL
                    else:
                        image_written = write_back_generated_content(
                            db,
                            item_id=item.id,
                            values={"image_url": image_url, "image_prompt": image_prompt},
                        )
                        if image_written == 0:
                            # 이미지 생성 중 상태가 바뀌어 쓰지 못했다. 성공으로만 보고하면
                            # 이미지 없는 글이 생긴 걸 아무도 모른다 — 요약에 드러낸다.
                            db.rollback()
                            db.refresh(item)
                            hospital_stats[hospital_key]["image_missing"] += 1
                            logger.warning(
                                "Image write-back skipped for %s — status changed during "
                                "image generation",
                                item.id,
                            )
                            item_state = GenerationItemState.DISCARDED
                        else:
                            db.commit()
                            db.refresh(item)
                except Exception as img_e:
                    logger.warning(
                        "Image generation failed for %s (text saved): %s",
                        item.id,
                        type(img_e).__name__,
                    )
                    hospital_stats[hospital_key]["image_missing"] += 1
                    db.rollback()
                    db.refresh(item)  # re-sync after rollback
                    item_state = GenerationItemState.PARTIAL

                readiness_failure = None
                if item_state != GenerationItemState.DISCARDED:
                    readiness_failure = _persist_publication_readiness(db, item, philosophy)
                    if (
                        readiness_failure is not None
                        and item_state == GenerationItemState.SUCCEEDED
                    ):
                        item_state = GenerationItemState.FAILED

                hospital_stats[hospital_key]["generated"] += 1
                if item_state == GenerationItemState.FAILED:
                    code, message = readiness_failure or (
                        "GENERATION_FAILED",
                        "자동 발행 준비 검사를 통과하지 못했습니다.",
                    )
                    hospital_stats[hospital_key]["failed"] += 1
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
                hospital_stats[hospital_key]["failed"] += 1
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
                    )
                )
            finally:
                released = release_unfinished_claims(
                    db,
                    [item_id],
                    expected_claimed_at=claim_time,
                )
                if released:
                    db.commit()

        # 다른 워커가 보유한 live lease도 이 실행의 미처리 결과다. 일부만 생성한 경우
        # PARTIAL, 전부 잠긴 경우 FAILED로 남겨 빈 성공으로 오인되지 않게 한다.
        stuck_items = load_stuck_claims(db, window_start, tomorrow)
        if stuck_items:
            _record_locked_generation_items(recorder, stuck_items)
        recorder.finish()

        # 배치 완료 후 전체 병원을 한 번에 요약한다. 병원별 메시지는 고객 수에 비례해
        # Slack 소음을 만들고, 같은 '운영 기준 미승인'을 차단 알림과 배치 알림으로
        # 중복 전달했다. 준비만 덜 된 병원은 기존 3일 dedupe도 유지한다.
        digest_entries: list[dict[str, object]] = []
        preparation_keys: list[str] = []
        for hospital_id, stat in hospital_stats.items():
            if (
                stat["generated"] > 0
                or stat["failed"] > 0
                or stat["skipped"] > 0
                or stat["cost_blocked"] > 0
                or stat["discarded"] > 0
                or stat["image_missing"] > 0
            ):
                preparation_key = f"content_generation_preparation:{hospital_id}"
                preparation_only = (
                    stat["skipped"] > 0
                    and stat["generated"] == 0
                    and stat["failed"] == 0
                    and stat["cost_blocked"] == 0
                    and stat["discarded"] == 0
                    and stat["image_missing"] == 0
                )
                if preparation_only and _already_done(preparation_key):
                    continue
                digest_entries.append(
                    {
                        "hospital_name": stat["name"],
                        "generated": stat["generated"],
                        "failed": stat["failed"],
                        "skipped": stat["skipped"],
                        "cost_blocked": stat["cost_blocked"],
                        "discarded": stat["discarded"],
                        "image_missing": stat["image_missing"],
                    }
                )
                if preparation_only:
                    preparation_keys.append(preparation_key)

        if digest_entries:
            sent = _run_async(
                notifier.notify_content_generation_digest(
                    scheduled_date=str(tomorrow), entries=digest_entries
                )
            )
            if sent:
                for preparation_key in preparation_keys:
                    _mark_done(preparation_key, 3 * 86_400)

        logger.info("Nightly generation finalized %d item claims", len(claimed_item_ids))


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
            image_url, image_prompt = _run_async(
                generate_image(
                    item.content_type,
                    hospital.slug,
                    topic=item.title or "병원 의료 정보",
                )
            )
            if not image_url:
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
            written = write_back_generated_content(
                db,
                item_id=item.id,
                values={"image_url": image_url, "image_prompt": image_prompt},
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
            philosophy = get_current_approved_philosophy_sync(db, hospital.id)
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
                        ),
                    )
                )
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
            logger.error("generate_content_image failed for %s: %s", content_id, type(exc).__name__)
            raise


def _generate_single_content_item(
    db, item: ContentItem, hospital: Hospital
) -> tuple[GenerationItemState, str | None, str | None]:
    existing = db.execute(
        select(ContentItem.title).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.id != item.id,
            ContentItem.title.isnot(None),
        )
    )
    existing_titles = [row[0] for row in existing.all()]

    philosophy = get_current_approved_philosophy_sync(db, hospital.id)
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
        db.commit()
        return (
            GenerationItemState.SKIPPED,
            "MISSING_APPROVED_ESSENCE",
            "승인된 콘텐츠 운영 기준을 먼저 승인해 주세요.",
        )

    # 비용 가드: Claude 호출 예산 확인. 차단 시 생성을 건너뛴다(item은 DRAFT/본문 없음 유지 —
    # 다음 야간 배치의 생성 누락 경보/재시도가 커버한다). 하드 상한 알림은 가드가 자체 발송한다.
    cost_decision = _run_async(cost_guard.check_and_increment("content"))
    if not cost_decision.allowed:
        logger.warning(
            "단일 콘텐츠 재생성이 비용 가드로 차단됨: %s — %s", hospital.name, cost_decision.reason
        )
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
    content_data = _run_async(
        generate_content(
            hospital,
            item.content_type,
            existing_titles,
            philosophy,
            approved_brief,
        )
    )
    now = datetime.now(timezone.utc)

    # 배치 경로와 같은 상태 가드를 쓴다. 재생성이 도는 동안 AE가 이 슬롯을 종료(CANCELLED)할
    # 수 있고, 가드 없이 쓰면 종료된 슬롯에 미검수 본문이 들어간다(실제 DB에서 재현됨).
    screening_probe = ContentItem(
        title=content_data["title"],
        body=content_data["body"],
        meta_description=content_data.get("meta_description"),
        faq_question=content_data.get("faq_question"),
        faq_answer_summary=content_data.get("faq_answer_summary"),
    )
    screening = screen_content_against_philosophy(screening_probe, philosophy)

    written = write_back_generated_content(
        db,
        item_id=item.id,
        values={
            "title": content_data["title"],
            "body": content_data["body"],
            "meta_description": content_data.get("meta_description"),
            "references_list": content_data.get("references") or [],
            "faq_question": content_data.get("faq_question"),
            "faq_answer_summary": content_data.get("faq_answer_summary"),
            "generated_at": now,
            "body_updated_at": now,
            "status": ContentStatus.DRAFT,
            "content_philosophy_id": philosophy.id,
            "essence_status": screening.status,
            "essence_check_summary": screening.summary,
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

    image_failed = False
    if not item.image_url:
        try:
            image_url, image_prompt = _run_async(
                generate_image(item.content_type, hospital.slug, topic=item.title)
            )
            if not image_url:
                # 실패 센티널("")을 그대로 쓰면 기존 이미지를 지운다.
                logger.warning("Image generation returned no URL for %s (text saved)", item.id)
                image_failed = True
            elif write_back_generated_content(
                db,
                item_id=item.id,
                values={"image_url": image_url, "image_prompt": image_prompt},
            ):
                db.commit()
                db.refresh(item)
            else:
                db.rollback()
                logger.warning(
                    "Image write-back skipped for %s — status changed during image generation",
                    item.id,
                )
                return GenerationItemState.DISCARDED, None, None
        except Exception as img_e:
            logger.warning(
                "Image generation failed for %s (text saved): %s",
                item.id,
                type(img_e).__name__,
            )
            db.rollback()
            db.refresh(item)
            image_failed = True
    if image_failed:
        _persist_publication_readiness(db, item, philosophy)
        return (
            GenerationItemState.PARTIAL,
            "IMAGE_GENERATION_FAILED",
            "본문은 저장됐지만 대표 이미지 생성이 완료되지 않았습니다.",
        )
    readiness_failure = _persist_publication_readiness(db, item, philosophy)
    if readiness_failure is not None:
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


# ══════════════════════════════════════════════════════════════════
# 아침 자동 발행 + 후행 확인 Slack (매일 08:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(
    name="app.workers.tasks.morning_content_auto_publish",
    bind=True,
    max_retries=3,
)
def morning_content_auto_publish(self):
    """Publish due content after machine checks, then request a human follow-up check."""
    require_dispatch(self, "morning-content-auto-publish")
    today = arrow.now("Asia/Seoul").date()
    notification_failures = 0
    blocked_entries: list[dict[str, object]] = []
    blocked_keys: list[str] = []

    try:
        with SyncSessionLocal() as db:
            due_ids = list(db.execute(_auto_publish_due_stmt(today)).scalars().all())

        for content_id in due_ids:
            outcome = _auto_publish_one(content_id)
            if outcome is None:
                continue
            if outcome["kind"] == "blocked":
                _run_async(
                    open_generation_incident(
                        item_id=content_id,
                        hospital_id=outcome["hospital_id"],
                        hospital_name=outcome["hospital_name"],
                        run_id=outcome["run_id"],
                        code=outcome["code"],
                        message=outcome["message"],
                    )
                )
                block_key = _auto_publish_block_alert_key(
                    content_id,
                    outcome["scheduled_date"],
                    outcome["code"],
                    outcome["reason"],
                )
                if _already_done(block_key):
                    continue
                blocked_entries.append(
                    {
                        "hospital_name": outcome["hospital_name"],
                        "title": outcome["title"],
                        "scheduled_date": outcome["scheduled_date"],
                        "reason": outcome["reason"],
                    }
                )
                blocked_keys.append(block_key)
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

            # 색인 신호 — sitemap이 크롤러를 기다리는 동안 발행 사실을 즉시 밀어 넣는다.
            # 실측(2026-07-29): AI가 병원 허브를 인용한 답변의 93%가 병원을 언급했고,
            # 인용하지 않은 답변은 4%였다. 읽히지 않으면 언급되지 않으므로 색인 진입이
            # 콘텐츠 발행만큼 중요하다. 실패해도 발행은 계속한다.
            _run_async(
                indexnow.submit_content_published_safe(
                    slug=outcome["slug"],
                    content_id=content_id,
                    aeo_domain=outcome.get("aeo_domain"),
                    treatments=outcome["treatments"],
                )
            )

        if blocked_entries:
            sent = _run_async(
                notifier.notify_auto_publish_block_digest(
                    entries=blocked_entries,
                    admin_url=f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals",
                )
            )
            if sent:
                for block_key in blocked_keys:
                    _mark_done(block_key, GENERATION_CATCHUP_DAYS * 86_400)
            else:
                notification_failures += 1

        # A worker may have committed publication and died before Slack. Recover those rows
        # without re-publishing or mutating their public timestamp.
        with SyncSessionLocal() as db:
            pending_ids = list(
                db.execute(_post_publish_notification_pending_stmt(today)).scalars().all()
            )
        for content_id in pending_ids:
            _recover_post_publish_notification(content_id)
        _run_async(reconcile_sent_publish_notifications(get_async_sessionmaker()))

        _notify_missed_content_generation(today)
    except Exception as exc:
        logger.exception("morning_content_auto_publish failed")
        raise self.retry(exc=exc, countdown=300)

    if notification_failures:
        raise self.retry(
            exc=RuntimeError(f"자동 발행 Slack 알림 {notification_failures}건 전송 실패"),
            countdown=300,
        )


def _auto_publish_due_stmt(today):
    window_start = today - timedelta(days=GENERATION_CATCHUP_DAYS)
    return (
        select(ContentItem.id)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date <= today,
            ContentItem.scheduled_date >= window_start,
            ContentItem.status == ContentStatus.DRAFT,
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.site_live.is_(True),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
    )


def _post_publish_notification_pending_stmt(today):
    return (
        select(ContentItem.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.post_publish_notified_at.is_(None),
        )
        .order_by(ContentItem.published_at)
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
        if not item or item.status != ContentStatus.DRAFT:
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
            findings = assessment.essence_summary.get("findings") or []
            operator_reason = (
                str(findings[0])
                if findings
                else (assessment.message or "자동 안전검사를 통과하지 못했습니다.")
            )
            write_audit_log_sync(
                db,
                action="auto_publish_blocked",
                hospital_id=hospital.id,
                actor=AUTO_PUBLISH_ACTOR,
                target_type="content_item",
                target_id=item.id,
                detail={
                    "code": assessment.code,
                    "reason": assessment.message,
                    "scheduled_date": str(item.scheduled_date),
                },
            )
            blocked_run = ensure_publication_block_run(
                db,
                item=item,
                hospital=hospital,
                code=assessment.code or "GENERATION_FAILED",
                message=assessment.message or "자동 발행 준비 검사를 통과하지 못했습니다.",
            )
            db.commit()
            return {
                "kind": "blocked",
                "code": assessment.code or "UNKNOWN",
                "message": assessment.message or "자동 발행 준비 검사를 통과하지 못했습니다.",
                "reason": operator_reason,
                "hospital_id": hospital.id,
                "hospital_name": hospital.name,
                "title": item.title,
                "scheduled_date": str(item.scheduled_date),
                "admin_url": admin_url,
                "run_id": blocked_run.id,
            }

        # Publishing without a working cache invalidation path can leave a successful DB
        # transaction invisible. Check only after blocker projection so a missing body/image
        # still reaches Operations Center even when the revalidation dependency is unavailable.
        ensure_site_revalidate_configured()
        published_at = datetime.now(timezone.utc)
        item.status = ContentStatus.PUBLISHED
        item.published_at = published_at
        item.published_by = AUTO_PUBLISH_ACTOR
        item.post_publish_notified_at = None
        item.post_publish_reviewed_at = None
        item.post_publish_reviewed_by = None
        enqueue_publish_notification_sync(db, item, hospital)
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
    }


def _recover_post_publish_notification(content_id: uuid.UUID) -> None:
    with SyncSessionLocal() as db:
        item = db.execute(
            select(ContentItem)
            .where(ContentItem.id == content_id)
            .options(joinedload(ContentItem.hospital))
        ).scalar_one_or_none()
        if (
            not item
            or item.status != ContentStatus.PUBLISHED
            or item.post_publish_notified_at is not None
        ):
            return
        recover_publish_notification_sync(db, item, item.hospital)
        db.commit()


def _notify_missed_content_generation(today) -> None:
    with SyncSessionLocal() as db:
        missed_items = db.execute(_morning_missed_stmt(today)).scalars().all()
        missed_by_hospital: dict[str, dict] = {}
        for item in missed_items:
            entry = missed_by_hospital.setdefault(
                str(item.hospital_id), {"name": item.hospital.name, "dates": [], "ids": []}
            )
            entry["dates"].append(str(item.scheduled_date))
            entry["ids"].append(item.id)

    digest_entries: list[dict[str, object]] = []
    digest_keys: list[str] = []
    for hospital_id, entry in missed_by_hospital.items():
        key = _generation_missed_alert_key(hospital_id, entry["ids"])
        if _already_done(key):
            continue
        digest_entries.append(
            {
                "hospital_name": entry["name"],
                "missed_count": len(entry["dates"]),
                "dates": entry["dates"],
            }
        )
        digest_keys.append(key)

    if digest_entries:
        sent = _run_async(
            notifier.notify_content_missed_digest(
                entries=digest_entries,
                admin_url=f"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals",
            )
        )
        if sent:
            for key in digest_keys:
                _mark_done(key, GENERATION_CATCHUP_DAYS * 86_400)
        else:
            logger.warning("generation-missed digest delivery failed")


def _morning_missed_stmt(today):
    """생성 누락 경보 조회 statement (R1) — 테스트에서 윈도우/필터 경계를 검증한다."""
    window_start = today - timedelta(days=GENERATION_CATCHUP_DAYS)
    approved_philosophy_hospitals = select(HospitalContentPhilosophy.hospital_id).where(
        HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED
    )
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date <= today,
            ContentItem.scheduled_date >= window_start,
            ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
            ContentItem.body.is_(None),
            Hospital.status == HospitalStatus.ACTIVE,
            ContentItem.hospital_id.in_(approved_philosophy_hospitals),
        )
        .options(joinedload(ContentItem.hospital))
        .order_by(ContentItem.scheduled_date)
    )


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
def run_sov_for_hospital(self, hospital_id: str):
    require_dispatch(self, "run-sov", hospital_id)
    try:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital or hospital.status not in (
                HospitalStatus.ACTIVE,
                HospitalStatus.PENDING_DOMAIN,
            ):
                return

            # priority 기반 쿼리 필터링 — beat은 월요일 02:00 KST(=일요일 UTC)에 발화하므로
            # UTC date.today()를 쓰면 ISO 주차 짝/홀이 뒤집히고 월초 판정도 어긋난다 (P1-5).
            today_kst = arrow.now("Asia/Seoul").date()
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
                .options(selectinload(AIQueryTarget.variants))
                .where(
                    AIQueryTarget.hospital_id == hospital.id,
                    AIQueryTarget.status == "ACTIVE",
                )
            )
            query_targets = target_result.scalars().all()

            # priority 필터 적용 (HIGH 항상 / NORMAL 짝수주 / LOW 월초) — 동일 규칙을
            # target/variant 유래 spec에도 적용하기 위해 _priority_included 헬퍼로 단일화한다.
            queries = [
                q
                for q in all_queries
                if _priority_included(q.priority, is_even_week, is_month_start)
            ]

            measurement_specs, trimmed_high = _build_measurement_specs(
                db=db,
                hospital=hospital,
                query_targets=query_targets,
                fallback_queries=queries,
                is_even_week=is_even_week,
                is_month_start=is_month_start,
                high_priority_cap=SOV_HIGH_PRIORITY_CAP,
            )

            if trimmed_high:
                # HIGH 상한 절단은 조용히 쿼리를 버리는 것과 같다 — 로그 + ops 알림 (P?-7).
                logger.warning(
                    "HIGH priority query cap reached for %s: %d specs trimmed (cap %d)",
                    hospital.name,
                    trimmed_high,
                    SOV_HIGH_PRIORITY_CAP,
                )
                _run_async(
                    notifier.notify_ops_alert(
                        title="주간 측정 HIGH 우선순위 쿼리 상한 초과",
                        message=(
                            f"병원: *{hospital.name}*\n"
                            f"HIGH 우선순위 측정 spec이 상한({SOV_HIGH_PRIORITY_CAP}건)을 초과해 "
                            f"{trimmed_high}건이 이번 주 측정에서 제외됐습니다.\n"
                            f"쿼리 타깃/변형이 과도하게 늘었는지 Admin에서 확인해 주세요."
                        ),
                    )
                )

            frozen_specs, _ = _build_measurement_specs(
                db=db,
                hospital=hospital,
                query_targets=query_targets,
                fallback_queries=all_queries,
                is_even_week=True,
                is_month_start=True,
                high_priority_cap=-1,
            )
            try:
                manifest = freeze_dispatch_manifest(
                    db,
                    hospital.id,
                    today_kst.year,
                    today_kst.month,
                    frozen_specs,
                    gemini_configured=bool(settings.GEMINI_API_KEY),
                )
            except ManifestError:
                logger.info("No queries available to freeze for hospital %s", hospital_id)
                return
            db.commit()
            measurement_specs = [
                {
                    "query_id": cell.query_matrix_id,
                    "query_text": cell.query_text,
                    "platform": cell.platform,
                    "target_id": cell.query_target_id,
                    "variant_id": cell.query_variant_id,
                    "manifest_cell": cell,
                }
                for cell in manifest.cells
                if cell.state == "FAILED"
            ]
            if not measurement_specs:
                logger.info("Monthly manifest has no pending cells for hospital %s", hospital_id)
                return

            # 비용 가드: spec 개수 × **반복 횟수**만큼 예산을 run 단위로 일괄 확인.
            # 각 spec은 run_single_query에서 SOV_REPEAT_WEEKLY번 실제 호출을 낸다.
            # 반복 횟수를 빼면 가드가 실제 호출의 1/SOV_REPEAT_WEEKLY만 예약한다.
            # 차단 시 측정을 건너뛰고 ops 알림만 남긴다(예외로 재시도를 유발하지 않는다 —
            # 재시도해도 상한에 다시 걸린다).
            # spec은 이미 (질의 × 플랫폼) 조합이므로 platform_count=1로 넘긴다.
            sov_decision = _run_async(
                cost_guard.check_and_increment(
                    "sov",
                    count=sov_budget_units(
                        query_count=len(measurement_specs),
                        platform_count=1,
                        repeat_count=SOV_REPEAT_WEEKLY,
                    ),
                )
            )
            if not sov_decision.allowed:
                logger.warning(
                    "주간 AI 언급률 측정이 비용 가드로 차단됨: %s — %s",
                    hospital.name,
                    sov_decision.reason,
                )
                _run_async(
                    notifier.notify_ops_alert(
                        title="주간 AI 언급률 측정 비용 가드 차단",
                        message=(
                            f"병원: *{hospital.name}*\n"
                            f"사유: {sov_decision.reason}\n"
                            f"이번 주 측정({len(measurement_specs)} spec)이 건너뛰어졌습니다. "
                            f"상한/킬스위치를 Admin에서 확인해 주세요."
                        ),
                    )
                )
                return

            competitors = hospital.competitors or []
            run = _start_measurement_run(
                db,
                hospital,
                run_label=f"weekly_sov_{today_kst.isoformat()}",
                config={
                    "source": "run_sov_for_hospital",
                    "repeat_count": SOV_REPEAT_WEEKLY,
                    "spec_count": len(measurement_specs),
                },
            )
            records = []
            attempt_pairs = []
            success_count = 0
            failure_count = 0
            for spec in measurement_specs:
                results = _run_async(
                    run_single_query(
                        hospital.name,
                        spec["query_text"],
                        spec["platform"],
                        SOV_REPEAT_WEEKLY,
                        competitors=competitors,
                    )
                )
                for r in results:
                    measurement_status, _failure_reason = _measurement_status_for_result(r)
                    if measurement_status == "SUCCESS":
                        success_count += 1
                    else:
                        failure_count += 1
                    record = _build_sov_record_from_result(
                        hospital_id=hospital.id,
                        query_id=spec["query_id"],
                        measurement_run_id=run.id,
                        platform=spec["platform"],
                        result=r,
                        target_id=spec["target_id"],
                        variant_id=spec["variant_id"],
                    )
                    records.append(record)
                    attempt_pairs.append((spec["manifest_cell"], record))

            db.add_all(records)
            db.flush()
            db.add_all([link_attempt(cell, record) for cell, record in attempt_pairs])
            _finish_measurement_run(run, success_count, failure_count)
            db.commit()

            # 결과가 생긴 직후 노출 갭/보완 액션을 갱신한다. 대시보드 GET 요청이 우연히
            # 액션 생성을 일으키는 구조에 의존하지 않고 다음 콘텐츠 생성이 최신 결과를 읽는다.
            _refresh_exposure_actions_sync(hospital.id)

    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


def _start_measurement_run(
    db, hospital: Hospital, *, run_label: str, config: dict
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
        },
    )
    db.add(run)
    db.flush()
    return run


def _finish_measurement_run(run: MeasurementRun, success_count: int, failure_count: int) -> None:
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
        run.error_summary = {"failed_count": failure_count}
    else:
        run.status = "PARTIAL"
        run.error_summary = {"failed_count": failure_count}


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
        is_mentioned=bool(result.get("is_mentioned")),
        mention_rank=result.get("mention_rank"),
        mention_sentiment=result.get("sentiment"),
        mention_context=result.get("mention_context"),
        raw_response=result.get("raw_response") or "",
        competitor_mentions=result.get("competitor_mentions"),
        measurement_method=result.get("measurement_method"),
        measurement_status=measurement_status,
        failure_reason=failure_reason,
        source_urls=result.get("source_urls") or [],
    )


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


def _build_measurement_specs(
    *,
    db,
    hospital: Hospital,
    query_targets: list[AIQueryTarget],
    fallback_queries: list[QueryMatrix],
    is_even_week: bool = True,
    is_month_start: bool = True,
    high_priority_cap: int = SOV_HIGH_PRIORITY_CAP,
) -> tuple[list[dict], int]:
    """주간 측정 spec 목록을 만든다.

    target/variant 유래 spec도 fallback 쿼리와 동일하게 target.priority 기준으로 게이팅한다
    (V0 후 target 자동 시드로 인해 스로틀링이 죽는 문제 방지). 마지막에 HIGH 상한을 적용한다.
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
        if not _priority_included(target_priority, is_even_week, is_month_start):
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
                    "query_intent": str(getattr(query, "query_intent", "LOCAL") or "LOCAL"),
                }
            )

    if specs:
        return _apply_high_priority_cap(specs, high_priority_cap)

    platforms = ["chatgpt"]
    if settings.GEMINI_API_KEY:
        platforms.append("gemini")
    for query in fallback_queries:
        for platform in platforms:
            specs.append(
                {
                    "query_id": query.id,
                    "query_text": query.query_text,
                    "platform": platform,
                    "target_id": None,
                    "variant_id": None,
                    "priority": str(getattr(query, "priority", "NORMAL") or "NORMAL").upper(),
                    "query_intent": str(getattr(query, "query_intent", "LOCAL") or "LOCAL"),
                }
            )
    return _apply_high_priority_cap(specs, high_priority_cap)


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
def monthly_slot_generation():
    """매월 25일 이후 반복 실행해 다음 달의 누락 슬롯을 자동 보충한다."""
    require_dispatch(current_task, "monthly-slot-generation")
    today = arrow.now("Asia/Seoul")
    if today.day < 25:
        logger.info("Next-month slot reconciliation is not due: %s", today.date())
        return

    next_month = today.shift(months=1).floor("month")
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
        failures: list[str] = []
        for schedule in schedules:
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
            except Exception:
                hospital_name = getattr(getattr(schedule, "hospital", None), "name", "(unknown)")
                logger.exception("monthly slot generation failed for %s; skipping", hospital_name)
                failures.append(hospital_name)
                continue

        db.commit()
        logger.info(
            f"monthly_slot_generation done: {created_count} hospitals processed, "
            f"{len(failures)} failed"
        )

        if failures:
            names = ", ".join(failures[:10]) + (" 외" if len(failures) > 10 else "")
            _run_async(
                notifier.notify_ops_alert(
                    title="다음 달 콘텐츠 슬롯 생성 실패",
                    message=(
                        f"{len(failures)}개 병원의 다음 달 슬롯 생성에 실패했습니다: {names}\n"
                        f"나머지 병원은 정상 생성됐습니다. 실패 병원의 스케줄(발행요일/요금제)을 "
                        f"확인해 주세요. 시스템은 다음 6시간 주기에 자동으로 다시 시도합니다."
                    ),
                )
            )


@celery_app.task(name="app.workers.tasks.run_weekly_monitoring")
def run_weekly_monitoring():
    require_dispatch(current_task, "weekly-sov-monitoring")
    with SyncSessionLocal() as db:
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        hospitals = result.scalars().all()

        for h in hospitals:
            hospital_id = str(h.id)
            run_sov_for_hospital.apply_async(
                args=[hospital_id],
                queue="sov",
                headers=build_dispatch_headers("run-sov", hospital_id),
            )

        # 측정은 이제 막 큐에 적재됐을 뿐이다 — '완료'가 아니라 '시작'을 알린다 (P2-14).
        _run_async(notifier.notify_monitoring_queued(len(hospitals)))

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

                if not recent_records:
                    continue

                successful_records = [
                    record
                    for record in recent_records
                    if str(record.measurement_status or "SUCCESS").upper() == "SUCCESS"
                ]
                if not successful_records:
                    continue
                has_any_mention = any(r.is_mentioned for r in successful_records)

                # 미언급 질문이 개선 작업의 우선 대상이다. 기존 로직은 반대로 언급된
                # 질문을 HIGH로 올려 노출이 없는 질문을 측정·콘텐츠 큐에서 밀어냈다.
                desired = "NORMAL" if has_any_mention else "HIGH"
                if q.priority != desired:
                    q.priority = desired
                    logger.info(
                        "Query %s priority changed to %s (mentioned=%s)",
                        q.id,
                        desired,
                        has_any_mention,
                    )

        db.commit()


# ══════════════════════════════════════════════════════════════════
# 월간 리포트 (다음 달 1일 00:15 마감, 7일까지 6시간 간격 자동 복구)
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
        observed_at = datetime.now(timezone.utc)
        last_seen = existing.heartbeat_at or existing.started_at or existing.requested_at
        active = existing.state in (
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        )
        stale = active and last_seen <= observed_at - timedelta(hours=1)
        retryable = existing.state in (
            OperationRunState.FAILED,
            OperationRunState.PARTIAL,
        )
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
    elif report is not None and report.quality == "COMPLETE" and _has_valid_doctor_artifact(
        db, report
    ):
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
    milestones = (
        [
            MonthlyRunStage.COVERAGE_COMPLETE.value,
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
        artifact_blocked = report is not None and report.quality == "COMPLETE"
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
    if artifact is None or not artifact.validated or artifact.path != report.doctor_pdf_path:
        return False
    metadata = parse_doctor_artifact_metadata(artifact.validation_metadata)
    return bool(
        metadata is not None
        and metadata.sha256 == artifact.sha256
        and metadata.byte_size == artifact.byte_size
    )


def _fail_monthly_operation_run(
    db,
    run_id: uuid.UUID | None,
    hospital_id: uuid.UUID,
    year: int,
    month: int,
) -> None:
    _finish_monthly_operation_run(db, run_id, hospital_id, year, month, "failed")


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
    prior_anchor = now.shift(months=-1)
    prior_manifest = db.execute(
        select(MonthlyMeasurementManifest).where(
            MonthlyMeasurementManifest.hospital_id == h.id,
            MonthlyMeasurementManifest.period_year == prior_anchor.year,
            MonthlyMeasurementManifest.period_month == prior_anchor.month,
        )
    ).scalar_one_or_none()
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
    )
    sov_records = list(current_loaded.selected_records) if current_loaded is not None else []
    sov_pct = monthly_sov.sov_pct
    prev_sov = monthly_sov.comparison.prior_sov_pct
    change_pct = monthly_sov.comparison.change_pct
    report_platforms = list(manifest.configured_platforms) if manifest is not None else None

    # 이번 달 발행 콘텐츠 집계
    content_stmt = select(ContentItem).where(
        ContentItem.hospital_id == h.id,
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.published_at >= period_start,
        ContentItem.published_at < period_end,
    )
    content_result = db.execute(content_stmt)
    published_contents = content_result.scalars().all()

    # 전월 발행 콘텐츠(유형별 발행 누적을 전월과 나란히 비교하기 위함)
    prev_content_stmt = select(ContentItem).where(
        ContentItem.hospital_id == h.id,
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.published_at >= prev_start,
        ContentItem.published_at < prev_end,
    )
    prev_content_result = db.execute(prev_content_stmt)
    prev_published_contents = prev_content_result.scalars().all()

    # 콘텐츠 발행-AI 언급 상관 집계(인과 주장 아님, 상관 표기용)
    attribution = build_content_attribution_summary(
        ContentAttributionInput(
            published_contents=published_contents,
            prev_published_contents=prev_published_contents,
            current_cells=current_loaded.cells if current_loaded is not None else (),
            prior_cells=prior_loaded.cells if prior_loaded is not None else None,
            sov_pct=sov_pct,
            prev_sov_pct=prev_sov,
            change_pct=change_pct,
        )
    )

    pdf_path = generate_pdf_report(
        hospital=h,
        period_start=period_start,
        period_end=period_end,
        report_type="MONTHLY",
        sov_pct=sov_pct,
        published_count=len(published_contents),
        repeat_count=SOV_REPEAT_WEEKLY,
        attribution=attribution,
        sov_coverage=monthly_sov.to_payload(),
    )
    essence_summary = build_monthly_essence_summary(db, h, period_start, period_end)

    version_plan = lock_report_version_plan(
        db,
        hospital_id=h.id,
        period=period,
        reason_code=build_reason,
        correlation_key=correlation_key or f"manual:{h.id}:{now.year}-{now.month:02d}",
    )
    if not version_plan.create:
        return "skipped_existing"

    report = MonthlyReport(
        hospital_id=h.id,
        period_year=now.year,
        period_month=now.month,
        report_type="MONTHLY",
        version=version_plan.version,
        supersedes_report_id=version_plan.supersedes_report_id,
        pdf_path=pdf_path,
        doctor_pdf_path=None,
        sov_summary=monthly_sov.to_payload(),
        content_summary={
            "published_count": len(published_contents),
            "attribution": attribution,
        },
        essence_summary=essence_summary,
    )
    if manifest is None:
        report.quality = "BLOCKED"
        report.delivery_blockers = ["MANIFEST_MISSING", "DOCTOR_ARTIFACT_UNVALIDATED"]
    else:
        apply_manifest_to_report(report, manifest)
    db.add(report)
    db.flush()

    artifact_error: DoctorPdfValidationError | None = None
    try:
        doctor_view = build_doctor_report_view(
            hospital=h,
            sov_pct=sov_pct,
            prev_sov_pct=prev_sov,
            published_count=len(published_contents),
            plan_quota=monthly_quota_for_plan(h.plan),
            attribution=attribution,
            records=sov_records,
            platforms=report_platforms,
            sov_coverage=monthly_sov.to_payload(),
        )
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
        period = prior_month_to_close(now.datetime)
    except MonthlyPeriodError as exc:
        logger.info("Monthly close is not ready: %s", exc)
        return {"status": "period_not_closed"}
    anchor = arrow.get(period.ends_at).shift(microseconds=-1)

    with SyncSessionLocal() as db:
        hospital_ids = eligible_hospital_ids(db, period)
        stmt = select(Hospital).where(Hospital.id.in_(hospital_ids))
        result = db.execute(stmt)
        hospitals = result.scalars().all()
        failures: list[str] = []
        successes = 0

        for h in hospitals:
            run_id, replayed = _start_scheduled_monthly_operation_run(db, h, anchor)
            if replayed:
                existing_run = db.get(OperationRun, run_id)
                if existing_run is not None and existing_run.state == OperationRunState.SUCCEEDED:
                    successes += 1
                else:
                    failures.append(h.name)
                continue
            try:
                latest = _latest_monthly_report(db, h.id, anchor.year, anchor.month)
                rebuilding = latest is not None
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
                _finish_monthly_operation_run(
                    db, run_id, h.id, anchor.year, anchor.month, outcome
                )
                finished_run = db.get(OperationRun, run_id)
                if finished_run is not None and finished_run.state in (
                    OperationRunState.PARTIAL,
                    OperationRunState.FAILED,
                ):
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
            if failures and successes
            else "FAILED"
            if failures
            else "SUCCEEDED",
            "total_count": len(hospitals),
            "success_count": successes,
            "failure_count": len(failures),
        }
    if failures:
        raise MonthlyBatchIncompleteError(
            f"scheduled monthly reports incomplete: {len(failures)}"
        )
    return result


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
            else prior_month_to_close(now.datetime)
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
        _mark_monthly_operation_run_running(db, run_id, anchor.year, anchor.month)
        correlation_key = (
            f"operation-run:{run_id}"
            if run_id is not None
            else f"manual:{hospital.id}:{anchor.year}-{anchor.month:02d}"
        )
        try:
            build_kwargs = {
                "rebuild": rebuild,
                "build_reason": ReportBuildReason.MANUAL_REBUILD,
                "correlation_key": correlation_key,
            }
            if run_id is not None:
                build_kwargs["operation_run_id"] = run_id
            outcome = _build_monthly_report_for_hospital(
                db, hospital, anchor, **build_kwargs
            )
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
        if (
            content is None
            or content.hospital_id != hospital.id
            or content.status != ContentStatus.PUBLISHED
        ):
            return None
        return content_site_paths(hospital.slug, content.id, treatments)


@celery_app.task(name="app.workers.tasks.retry_site_revalidation")
def retry_site_revalidation(run_id: str, expected_attempt_count: int):
    """Retry only the public cache refresh; never repeat or undo publication."""

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
        recorded = _run_async(
            record_revalidation_success(parsed_run_id, expected_attempt_count)
        )
        return {"status": "recovered" if recorded else "stale_run"}

    plan = _run_async(record_retry_failure(parsed_run_id, expected_attempt_count))
    if plan is None:
        return {"status": "stale_run"}
    if plan.delay_seconds is not None:
        retry_site_revalidation.apply_async(
            args=[str(parsed_run_id), expected_attempt_count + 1],
            queue="default",
            countdown=plan.delay_seconds,
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
    }


# ══════════════════════════════════════════════════════════════════
# Lead PII 보관기간 자동 파기 — 개인정보보호법 제21조
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.purge_expired_leads")
def purge_expired_leads():
    """retain_until 도달 lead의 PII를 익명화하고 purged_at을 기록한다.

    Soft-delete: 통계용 메타(clinic_type, source_path, consent_version)는 유지하되
    개인 식별 가능 필드(clinic_name, contact, question, consent_ip)는 즉시 폐기한다.
    이미 처리된 row는 skip.

    매일 결과는 Slack에 notify — 0건이라도 송출하여 cron이 살아 있음을 운영자가 매일 확인.
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

    멱등이다 — IndexNow는 같은 URL을 다시 받아도 문제 없고, 우리 쪽 상태도 바꾸지 않는다.
    실패해도 예외를 올리지 않는다(색인 신호는 부가 기능이지 데이터 정합성이 아니다).

    사용:
        backfill_indexnow.delay()                       # 전체
        backfill_indexnow.delay(hospital_id="...")      # 특정 병원
        backfill_indexnow.delay(dry_run=True)           # 제출 없이 대상만 집계
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

            content_ids = (
                db.execute(
                    select(ContentItem.id)
                    .where(ContentItem.hospital_id == hospital.id)
                    .where(ContentItem.status == ContentStatus.PUBLISHED)
                    .order_by(ContentItem.scheduled_date)
                )
                .scalars()
                .all()
            )

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
                entry["submitted"] = False
            else:
                entry["submitted"] = _run_async(indexnow.submit_urls(base_url=base, urls=urls))
            summary.append(entry)
            logger.info(
                "backfill_indexnow: %s (%s) 콘텐츠 %d편 · URL %d개 · 제출=%s",
                hospital.name,
                base,
                len(content_ids),
                len(urls),
                entry["submitted"],
            )

    total_urls = sum(e["urls"] for e in summary)
    ok = sum(1 for e in summary if e["submitted"])
    logger.info(
        "backfill_indexnow 완료: 병원 %d곳 · URL %d개 · 성공 %d곳 (dry_run=%s)",
        len(summary),
        total_urls,
        ok,
        dry_run,
    )
    return {"hospitals": summary, "total_urls": total_urls, "succeeded": ok, "dry_run": dry_run}
