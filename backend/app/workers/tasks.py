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
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import arrow
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, AIQueryVariant, MeasurementRun, QueryMatrix, SovRecord
from app.services import cost_guard, notifier
from app.services.audit_log import write_audit_log_sync
from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.content_engine import generate_content
from app.services.content_publication import (
    apply_publication_assessment,
    assess_content_publication,
)
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    build_monthly_essence_summary,
    screen_content_against_philosophy,
)
from app.services.essence_readiness import get_current_approved_philosophy_sync
from app.services.image_engine import generate_image
from app.services.report_engine import build_content_attribution_summary, generate_pdf_report
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_content_site_revalidate_safe,
)
from app.services.sov_engine import generate_query_matrix, run_single_query, calculate_sov
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.workers.monthly_slots import create_next_month_slots_for_schedule
from app.workers.nightly_generation_batch import (
    GENERATION_CATCHUP_DAYS,
    NIGHTLY_GENERATION_CAP,
    _load_nightly_generation_batch,
    _nightly_generation_stmt,  # noqa: F401 — test_tasks_nightly가 tasks 경유로 참조하는 re-export
)

logger = logging.getLogger(__name__)

AUTO_PUBLISH_ACTOR = "SYSTEM_AUTO_PUBLISH"

SOV_REPEAT_WEEKLY = min(settings.SOV_REPEAT_COUNT_WEEKLY, 20)  # 주간 측정용
V0_REPEAT_COUNT = 5  # V0 첫 측정 쿼리당 반복 횟수
V0_QUERY_SAMPLE_COUNT = 5  # V0 첫 측정에 쓰는 쿼리 개수
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
            # 실패한 실행은 _reset_v0_analyzing_status로 상태를 복원하므로 여기 걸리는 것은
            # 진행 중인 실행뿐이다.
            if hospital.status == HospitalStatus.ANALYZING:
                logger.info(
                    "V0 report already in progress for %s; skipping duplicate", hospital.name
                )
                return

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
                queries = generate_query_matrix(
                    hospital.region, hospital.specialties, hospital.keywords
                )
                for q_text in queries:
                    db.add(QueryMatrix(hospital_id=hospital.id, query_text=q_text))
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
            # 결정론적 순서로 상위 N개를 샘플링한다 — ORDER BY 없이 limit하면 DB가 임의 행을
            # 돌려줘 V0 결과가 재현 불가능해진다. 카테고리 컬럼이 없으므로 created_at(동률 시 id)
            # 기준으로 안정 정렬을 보장한다.
            stmt = (
                select(QueryMatrix)
                .where(QueryMatrix.hospital_id == hospital.id)
                .order_by(QueryMatrix.created_at, QueryMatrix.id)
                .limit(V0_QUERY_SAMPLE_COUNT)
            )
            result = db.execute(stmt)
            sample_queries = result.scalars().all()

            platforms = ["chatgpt"]
            if settings.GEMINI_API_KEY:
                platforms.append("gemini")
            competitors = hospital.competitors or []

            # 비용 가드: V0 측정 예산 확인(쿼리 × 플랫폼 수). V0는 사람이 기다리는 플로우이므로
            # 차단 시 ANALYZING을 이전 상태로 되돌리고, 명확한 실패 사유를 ops Slack으로 보낸다.
            v0_units = len(sample_queries) * len(platforms)
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
                        all_records.append(r)

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
                build_aeo_site.apply_async(args=[hospital_id], queue="default")
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
                                f"실패했습니다. Admin에서 허브 준비를 수동 재실행해 주세요."
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
    # 50개 슬롯 × (Claude+Imagen) 배치는 전역 900s를 초과하므로 상향. 멱등(body-null 필터)
    # 하므로 acks_late로 워커 크래시 시 안전하게 재배달.
    soft_time_limit=3000,
    time_limit=3300,
    acks_late=True,
)
def nightly_content_generation():
    """내일 발행 예정인 콘텐츠를 오늘 밤에 생성.

    catch-up window (P1-3/R1): 야간 배치가 누락(워커 다운 등)돼도 슬롯이 영구 고아가
    되지 않도록 '오늘-{GENERATION_CATCHUP_DAYS}일 ~ 내일' 범위의 미생성 슬롯을 함께
    집어 재시도한다.
    """
    now_kst = arrow.now("Asia/Seoul")
    window_start = now_kst.shift(days=-GENERATION_CATCHUP_DAYS).date()
    tomorrow = now_kst.shift(days=1).date()

    with SyncSessionLocal() as db:
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
            logger.info(f"No content to generate for {window_start}~{tomorrow}")
            return

        # 병원별 생성 성공/실패/차단 추적 → 배치 완료 후 요약 Slack
        hospital_stats: dict[str, dict] = {}

        for item in items:
            hospital = item.hospital
            hospital_key = str(hospital.id)

            if hospital_key not in hospital_stats:
                hospital_stats[hospital_key] = {
                    "name": hospital.name,
                    "generated": 0,
                    "failed": 0,
                    "skipped": 0,
                    "cost_blocked": 0,
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
                    continue

                # Claude Sonnet 콘텐츠 생성
                approved_brief = (
                    item.content_brief if item.brief_status == BRIEF_STATUS_APPROVED else None
                )
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
                item.title = content_data["title"]
                item.body = content_data["body"]
                item.meta_description = content_data.get("meta_description")
                item.references_list = content_data.get("references") or []
                item.faq_question = content_data.get("faq_question")
                item.faq_answer_summary = content_data.get("faq_answer_summary")
                item.generated_at = now
                item.body_updated_at = now
                item.status = ContentStatus.DRAFT
                item.content_philosophy_id = philosophy.id
                screening = screen_content_against_philosophy(item, philosophy)
                item.essence_status = screening.status
                item.essence_check_summary = screening.summary

                # 텍스트 콘텐츠 먼저 커밋 (이미지 실패가 텍스트를 롤백하지 않도록)
                db.commit()
                logger.info(f"Content generated: {hospital.name} — {item.title}")

                # 대표 이미지 생성 (gpt-image-2, 제목 주제 주입 — 실패해도 텍스트는 유지)
                try:
                    image_url, image_prompt = _run_async(
                        generate_image(item.content_type, hospital.slug, topic=item.title)
                    )
                    item.image_url = image_url
                    item.image_prompt = image_prompt
                    db.commit()
                except Exception as img_e:
                    logger.warning(f"Image generation failed for {item.id} (text saved): {img_e}")
                    db.rollback()
                    db.refresh(item)  # re-sync after rollback

                hospital_stats[hospital_key]["generated"] += 1

            except Exception as e:
                logger.error(f"Content generation failed for item {item.id} ({hospital.name}): {e}")
                db.rollback()
                db.expire_all()
                hospital_stats[hospital_key]["failed"] += 1
                _run_async(
                    notifier.notify_content_generation_failed(
                        hospital_name=hospital.name,
                        content_type=item.content_type.value if item.content_type else "UNKNOWN",
                        scheduled_date=str(item.scheduled_date),
                        error=str(e),
                    )
                )

        # 배치 완료 후 병원별 요약 Slack 발송
        for stat in hospital_stats.values():
            # 운영 기준 미승인 차단은 병원당 1회 전용 알림 — 한 달 내내 생성이 막혀도
            # Slack 신호가 0건이던 문제 해소 (P1-7).
            if stat["skipped"] > 0:
                _run_async(
                    notifier.notify_generation_blocked_no_philosophy(
                        hospital_name=stat["name"],
                        blocked_count=stat["skipped"],
                        scheduled_date=str(tomorrow),
                    )
                )
            if (
                stat["generated"] > 0
                or stat["failed"] > 0
                or stat["skipped"] > 0
                or stat["cost_blocked"] > 0
            ):
                _run_async(
                    notifier.notify_content_batch_summary(
                        hospital_name=stat["name"],
                        generated=stat["generated"],
                        failed=stat["failed"],
                        scheduled_date=str(tomorrow),
                        skipped=stat["skipped"],
                        cost_blocked=stat["cost_blocked"],
                    )
                )


@celery_app.task(name="app.workers.tasks.regenerate_content_item", bind=True, max_retries=1)
def regenerate_content_item(self, content_id: str):
    """Generate a single unpublished content item on operator request."""
    try:
        with SyncSessionLocal() as db:
            item = db.get(ContentItem, uuid.UUID(content_id))
            if not item:
                return
            if item.status == ContentStatus.PUBLISHED:
                logger.warning("Skipping regeneration for published content item %s", content_id)
                return

            hospital = db.get(Hospital, item.hospital_id)
            if not hospital:
                return

            item.title = None
            item.body = None
            item.meta_description = None
            item.image_url = None
            item.image_prompt = None
            item.generated_at = None
            item.published_at = None
            item.published_by = None
            item.post_publish_notified_at = None
            item.post_publish_reviewed_at = None
            item.post_publish_reviewed_by = None
            item.status = ContentStatus.DRAFT
            db.commit()

            _generate_single_content_item(db, item, hospital)
    except Exception as exc:
        logger.error("regenerate_content_item failed for %s: %s", content_id, exc)
        raise self.retry(exc=exc, countdown=120)


def _generate_single_content_item(db, item: ContentItem, hospital: Hospital) -> None:
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
        return

    # 비용 가드: Claude 호출 예산 확인. 차단 시 생성을 건너뛴다(item은 DRAFT/본문 없음 유지 —
    # 다음 야간 배치의 생성 누락 경보/재시도가 커버한다). 하드 상한 알림은 가드가 자체 발송한다.
    cost_decision = _run_async(cost_guard.check_and_increment("content"))
    if not cost_decision.allowed:
        logger.warning(
            "단일 콘텐츠 재생성이 비용 가드로 차단됨: %s — %s", hospital.name, cost_decision.reason
        )
        return

    approved_brief = item.content_brief if item.brief_status == BRIEF_STATUS_APPROVED else None
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
    item.title = content_data["title"]
    item.body = content_data["body"]
    item.meta_description = content_data.get("meta_description")
    item.references_list = content_data.get("references") or []
    item.faq_question = content_data.get("faq_question")
    item.faq_answer_summary = content_data.get("faq_answer_summary")
    item.generated_at = now
    item.body_updated_at = now
    item.status = ContentStatus.DRAFT
    item.content_philosophy_id = philosophy.id
    screening = screen_content_against_philosophy(item, philosophy)
    item.essence_status = screening.status
    item.essence_check_summary = screening.summary
    db.commit()

    if not item.image_url:
        try:
            image_url, image_prompt = _run_async(
                generate_image(item.content_type, hospital.slug, topic=item.title)
            )
            item.image_url = image_url
            item.image_prompt = image_prompt
            db.commit()
        except Exception as img_e:
            logger.warning("Image generation failed for %s (text saved): %s", item.id, img_e)
            db.rollback()
            db.refresh(item)


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
    today = arrow.now("Asia/Seoul").date()
    notification_failures = 0

    try:
        with SyncSessionLocal() as db:
            due_ids = list(db.execute(_auto_publish_due_stmt(today)).scalars().all())

        if due_ids:
            # Publishing without a working cache invalidation path can leave a successful DB
            # transaction invisible. Production therefore fails closed before any mutation.
            ensure_site_revalidate_configured()

        for content_id in due_ids:
            outcome = _auto_publish_one(content_id)
            if outcome is None:
                continue
            if outcome["kind"] == "blocked":
                block_key = (
                    f"auto_publish_blocked:{content_id}:"
                    f"{outcome['scheduled_date']}:{outcome['code']}"
                )
                if _already_done(block_key):
                    continue
                sent = _run_async(
                    notifier.notify_content_auto_publish_blocked(
                        hospital_name=outcome["hospital_name"],
                        title=outcome["title"],
                        scheduled_date=outcome["scheduled_date"],
                        reason=outcome["reason"],
                        admin_url=outcome["admin_url"],
                    )
                )
                if sent:
                    _mark_done(block_key, GENERATION_CATCHUP_DAYS * 86_400)
                else:
                    notification_failures += 1
                continue

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
            if not _deliver_post_publish_notification(content_id, outcome):
                notification_failures += 1

        # A worker may have committed publication and died before Slack. Recover those rows
        # without re-publishing or mutating their public timestamp.
        with SyncSessionLocal() as db:
            pending_ids = list(
                db.execute(_post_publish_notification_pending_stmt(today)).scalars().all()
            )
        for content_id in pending_ids:
            outcome = _load_published_notification_payload(content_id)
            if outcome and not _deliver_post_publish_notification(content_id, outcome):
                notification_failures += 1

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
            ContentItem.body.isnot(None),
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.site_live.is_(True),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
    )


def _post_publish_notification_pending_stmt(today):
    return (
        select(ContentItem.id)
        .where(
            ContentItem.scheduled_date <= today,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.published_by == AUTO_PUBLISH_ACTOR,
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
            db.commit()
            return {
                "kind": "blocked",
                "code": assessment.code or "UNKNOWN",
                "reason": assessment.message or "자동 안전검사를 통과하지 못했습니다.",
                "hospital_name": hospital.name,
                "title": item.title,
                "scheduled_date": str(item.scheduled_date),
                "admin_url": admin_url,
            }

        published_at = datetime.now(timezone.utc)
        item.status = ContentStatus.PUBLISHED
        item.published_at = published_at
        item.published_by = AUTO_PUBLISH_ACTOR
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
        payload = _publication_notification_payload(item, hospital)
        db.commit()
        return payload


def _publication_notification_payload(item: ContentItem, hospital: Hospital) -> dict:
    public_base = _public_site_url(hospital.aeo_domain, hospital.slug).rstrip("/")
    return {
        "kind": "published",
        "hospital_name": hospital.name,
        "slug": hospital.slug,
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


def _load_published_notification_payload(content_id: uuid.UUID) -> dict | None:
    with SyncSessionLocal() as db:
        item = db.execute(
            select(ContentItem)
            .where(ContentItem.id == content_id)
            .options(joinedload(ContentItem.hospital))
        ).scalar_one_or_none()
        if (
            not item
            or item.status != ContentStatus.PUBLISHED
            or item.published_by != AUTO_PUBLISH_ACTOR
            or item.post_publish_notified_at is not None
        ):
            return None
        return _publication_notification_payload(item, item.hospital)


def _deliver_post_publish_notification(content_id: uuid.UUID, payload: dict) -> bool:
    sent = _run_async(
        notifier.notify_content_auto_published(
            hospital_name=payload["hospital_name"],
            title=payload["title"],
            sequence_no=payload["sequence_no"],
            total_count=payload["total_count"],
            content_type=payload["content_type"],
            scheduled_date=payload["scheduled_date"],
            public_url=payload["public_url"],
            admin_url=payload["admin_url"],
            carried_over=payload["carried_over"],
        )
    )
    if not sent:
        return False
    with SyncSessionLocal() as db:
        item = db.execute(
            select(ContentItem)
            .where(ContentItem.id == content_id)
            .with_for_update()
        ).scalar_one_or_none()
        if item and item.post_publish_notified_at is None:
            item.post_publish_notified_at = datetime.now(timezone.utc)
            write_audit_log_sync(
                db,
                action="post_publish_notification_sent",
                hospital_id=item.hospital_id,
                actor=AUTO_PUBLISH_ACTOR,
                target_type="content_item",
                target_id=item.id,
                detail={"channel": "slack"},
            )
            db.commit()
    return True


def _notify_missed_content_generation(today) -> None:
    with SyncSessionLocal() as db:
        missed_items = db.execute(_morning_missed_stmt(today)).scalars().all()
        missed_by_hospital: dict[str, dict] = {}
        for item in missed_items:
            entry = missed_by_hospital.setdefault(
                str(item.hospital_id), {"name": item.hospital.name, "dates": []}
            )
            entry["dates"].append(str(item.scheduled_date))

    for hospital_id, entry in missed_by_hospital.items():
        key = f"content_generation_missed:{today}:{hospital_id}"
        if _already_done(key):
            continue
        sent = _run_async(
            notifier.notify_content_generation_missed(
                hospital_name=entry["name"],
                missed_count=len(entry["dates"]),
                dates=entry["dates"],
            )
        )
        if sent:
            _mark_done(key)
        else:
            logger.warning("generation-missed alert delivery failed for %s", entry["name"])


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
            current_week = today_kst.isocalendar()[1]
            is_even_week = current_week % 2 == 0
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

            if not measurement_specs:
                logger.info(
                    f"No queries to run for hospital {hospital_id} this week (priority filter)"
                )
                return

            # 비용 가드: 측정 spec 개수만큼 예산을 run 단위로 일괄 확인. 차단 시 측정을 건너뛰고
            # ops 알림만 남긴다(예외로 재시도를 유발하지 않는다 — 재시도해도 상한에 다시 걸린다).
            sov_decision = _run_async(
                cost_guard.check_and_increment("sov", count=len(measurement_specs))
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
                    records.append(
                        _build_sov_record_from_result(
                            hospital_id=hospital.id,
                            query_id=spec["query_id"],
                            measurement_run_id=run.id,
                            platform=spec["platform"],
                            result=r,
                            target_id=spec["target_id"],
                            variant_id=spec["variant_id"],
                        )
                    )

            db.add_all(records)
            _finish_measurement_run(run, success_count, failure_count)
            db.commit()

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
        measurement_status=measurement_status,
        failure_reason=failure_reason,
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
    for target in query_targets:
        target_priority = str(getattr(target, "priority", "NORMAL") or "NORMAL").upper()
        if not _priority_included(target_priority, is_even_week, is_month_start):
            continue
        active_variants = [variant for variant in target.variants if variant.is_active]
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
                }
            )
    return _apply_high_priority_cap(specs, high_priority_cap)


def _ensure_variant_query_matrix(db, hospital: Hospital, variant: AIQueryVariant) -> QueryMatrix:
    if variant.query_matrix_id:
        query = db.get(QueryMatrix, variant.query_matrix_id)
        if query and query.hospital_id == hospital.id:
            return query

    query = QueryMatrix(
        hospital_id=hospital.id,
        query_text=variant.query_text,
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
        from app.services.exposure_action_engine import ensure_hospital_exposure_actions
        from app.core.database import get_async_sessionmaker

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
    """매월 25일: 다음 달 콘텐츠 슬롯을 미리 생성"""
    today = arrow.now("Asia/Seoul")
    if today.day != 25:
        logger.info(f"Not the 25th ({today.date()}), skipping monthly slot generation")
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
                        f"확인 후 Admin에서 재생성해 주세요."
                    ),
                )
            )


@celery_app.task(name="app.workers.tasks.run_weekly_monitoring")
def run_weekly_monitoring():
    with SyncSessionLocal() as db:
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        hospitals = result.scalars().all()

        for h in hospitals:
            run_sov_for_hospital.apply_async(args=[str(h.id)], queue="sov")

        # 측정은 이제 막 큐에 적재됐을 뿐이다 — '완료'가 아니라 '시작'을 알린다 (P2-14).
        _run_async(notifier.notify_monitoring_queued(len(hospitals)))

        # 측정 결과 기반 질문 우선순위 조정 (P1-4) — 같은 "sov" 큐 뒤에 적재되므로 단일
        # sov 워커(FIFO) 기준으로는 병원별 측정 태스크가 모두 끝난 뒤 실행된다.
        # 한계: sov 워커가 여러 개거나 측정 태스크가 재시도로 길어지면 일부 병원의 이번 주
        # 측정 결과가 반영되기 전에 실행될 수 있다 — 우선순위 조정은 최근 4주 누적 기준이라
        # 다음 주 실행에서 따라잡는다. countdown은 측정 큐 소화 시간의 보수적 버퍼.
        if hospitals:
            adjust_query_priorities.apply_async(queue="sov", countdown=1800)


@celery_app.task(name="app.workers.tasks.adjust_query_priorities")
def adjust_query_priorities():
    """Adjust query priorities based on recent AI mention results. Run after weekly measurement tasks complete."""
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

                has_any_mention = any(r.is_mentioned for r in recent_records)

                if has_any_mention and q.priority != "HIGH":
                    q.priority = "HIGH"
                    logger.info(f"Query {q.id} promoted to HIGH (mention found)")
                elif not has_any_mention and q.priority == "HIGH":
                    q.priority = "NORMAL"
                    logger.info(f"Query {q.id} demoted to NORMAL (no mention in 4 weeks)")

        db.commit()


# ══════════════════════════════════════════════════════════════════
# 월간 리포트 (매월 마지막 날 21:00)
# ══════════════════════════════════════════════════════════════════
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
    now = arrow.now("Asia/Seoul")
    # beat은 28~31일에 매일 실행 — 실제 마지막 날인지 확인
    if now.date() != now.ceil("month").date():
        logger.info(f"Not last day of month ({now.date()}), skipping monthly reports")
        return
    period_start = now.floor("month").datetime
    period_end = now.ceil("month").datetime

    with SyncSessionLocal() as db:
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        hospitals = result.scalars().all()
        failures: list[tuple[str, Exception]] = []

        for h in hospitals:
            try:
                # 월간 리포트 중복 생성 방지
                existing_check = db.execute(
                    select(MonthlyReport).where(
                        MonthlyReport.hospital_id == h.id,
                        MonthlyReport.period_year == now.year,
                        MonthlyReport.period_month == now.month,
                        MonthlyReport.report_type == "MONTHLY",
                    )
                )
                if existing_check.scalar_one_or_none():
                    logger.warning(
                        f"Monthly report already exists for {h.name} "
                        f"{now.year}-{now.month:02d}, skipping."
                    )
                    continue

                # 이번 달 AI 답변 언급률 집계
                sov_stmt = select(SovRecord).where(
                    SovRecord.hospital_id == h.id,
                    SovRecord.measured_at >= period_start,
                    SovRecord.measured_at <= period_end,
                )
                sov_result = db.execute(sov_stmt)
                sov_records = sov_result.scalars().all()
                # None → '측정 데이터 없음' (허위 0%가 원장 보고에 들어가는 것 방지)
                sov_pct = calculate_sov(
                    [
                        {"is_mentioned": r.is_mentioned, "measurement_status": r.measurement_status}
                        for r in sov_records
                    ]
                )
                # 실제 측정된 플랫폼만 라벨에 반영 (없으면 None → 설정 기준 유추).
                measured_platforms = sorted({r.ai_platform for r in sov_records if r.ai_platform})
                report_platforms = measured_platforms or None

                # 전월 AI 답변 언급률
                prev_start = now.shift(months=-1).floor("month").datetime
                prev_end = now.floor("month").datetime
                prev_stmt = select(SovRecord).where(
                    SovRecord.hospital_id == h.id,
                    SovRecord.measured_at >= prev_start,
                    SovRecord.measured_at < prev_end,
                )
                prev_result = db.execute(prev_stmt)
                prev_records = prev_result.scalars().all()
                prev_sov = (
                    calculate_sov(
                        [
                            {
                                "is_mentioned": r.is_mentioned,
                                "measurement_status": r.measurement_status,
                            }
                            for r in prev_records
                        ]
                    )
                    if prev_records
                    else None
                )
                # 전월대비는 두 달 모두 실측치가 있을 때만 계산 (None-safe).
                change_pct = (
                    round(sov_pct - prev_sov, 1)
                    if sov_pct is not None and prev_sov is not None
                    else None
                )

                # 이번 달 발행 콘텐츠 집계
                content_stmt = select(ContentItem).where(
                    ContentItem.hospital_id == h.id,
                    ContentItem.status == ContentStatus.PUBLISHED,
                    ContentItem.published_at >= period_start,
                    ContentItem.published_at <= period_end,
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
                    published_contents=published_contents,
                    prev_published_contents=prev_published_contents,
                    this_records=sov_records,
                    prev_records=prev_records,
                    sov_pct=sov_pct,
                    prev_sov_pct=prev_sov,
                    change_pct=change_pct,
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
                )
                essence_summary = build_monthly_essence_summary(db, h, period_start, period_end)

                db.add(
                    MonthlyReport(
                        hospital_id=h.id,
                        period_year=now.year,
                        period_month=now.month,
                        report_type="MONTHLY",
                        pdf_path=pdf_path,
                        sov_summary={
                            "sov_pct": sov_pct,
                            "prev_sov_pct": prev_sov,
                            "change_pct": change_pct,
                        },
                        content_summary={
                            "published_count": len(published_contents),
                            "attribution": attribution,
                        },
                        essence_summary=essence_summary,
                    )
                )
                db.commit()

                _run_async(
                    notifier.notify_monthly_report_ready(
                        h.name,
                        now.year,
                        now.month,
                        sov_pct,
                        change_pct,
                        pdf_path,
                        platforms=report_platforms,
                        new_mention_count=attribution["new_mention_count"],
                    )
                )

            except Exception as e:
                logger.error(f"Monthly report failed for {h.name}: {e}")
                db.rollback()
                failures.append((h.name, e))

        _raise_if_monthly_report_failures(failures)


# ══════════════════════════════════════════════════════════════════
# 신규 런타임 도메인 HTTPS 상태 감시 — Terraform 정적 목록 밖까지 포함
# ══════════════════════════════════════════════════════════════════
def _domain_health_incident_key(domain: str) -> str:
    digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:16]
    return f"reputation:domain-health:incident:{digest}"


def _check_custom_domain_https(
    client: httpx.Client,
    domain: str,
) -> tuple[bool, str]:
    try:
        response = client.get(f"https://{domain}/")
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError:
        return False, "tls_or_network_error"
    if 200 <= response.status_code < 400:
        return True, f"http_{response.status_code}"
    return False, f"http_{response.status_code}"


@celery_app.task(name="app.workers.tasks.monitor_live_custom_domains")
def monitor_live_custom_domains():
    """모든 LIVE 자기 도메인의 실제 TLS/Host routing 응답을 주기적으로 확인한다."""
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

    new_failures: list[tuple[str, str]] = []
    recoveries: list[str] = []
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        for hospital in hospitals:
            domain = (hospital.aeo_domain or "").strip().lower()
            if not domain:
                continue
            healthy, reason = _check_custom_domain_https(client, domain)
            incident_key = _domain_health_incident_key(domain)
            try:
                incident_open = bool(_get_redis().get(incident_key))
                if healthy:
                    if incident_open:
                        _get_redis().delete(incident_key)
                        recoveries.append(domain)
                elif not incident_open:
                    _get_redis().set(incident_key, reason, ex=21_600)
                    new_failures.append((domain, reason))
            except Exception:
                logger.warning("Domain health incident state unavailable: domain=%s", domain)
                if not healthy:
                    new_failures.append((domain, reason))

            if not healthy:
                logger.error(
                    "Custom domain health check failed: domain=%s reason=%s", domain, reason
                )

    if new_failures:
        lines = "\n".join(f"• {domain}: {reason}" for domain, reason in new_failures[:20])
        _run_async(
            notifier.notify_ops_alert(
                title=f"병원 커스텀 도메인 장애 {len(new_failures)}건",
                message=f"실제 HTTPS/Host routing 확인 실패:\n{lines}",
            )
        )
    if recoveries:
        _run_async(
            notifier.notify_ops_alert(
                title=f"병원 커스텀 도메인 복구 {len(recoveries)}건",
                message="HTTPS 응답 복구: " + ", ".join(recoveries[:20]),
            )
        )
    return {
        "checked": len(hospitals),
        "new_failures": len(new_failures),
        "recoveries": len(recoveries),
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
    from app.services.lead_privacy import anonymize_lead, scrub_onboarding_note

    now = datetime.now(timezone.utc)
    purged = 0
    error_msg: str | None = None
    try:
        with SyncSessionLocal() as db:
            stmt = select(SalesLead).where(
                SalesLead.purged_at.is_(None),
                SalesLead.retain_until.is_not(None),
                SalesLead.retain_until <= now,
            )
            for lead in db.execute(stmt).scalars().all():
                if anonymize_lead(lead, now):
                    purged += 1
                    # CDX-M2: 전환된 병원의 onboarding_note에 복사된 운영자 자유 텍스트도
                    # 함께 파기 (lead row만 익명화하면 파기 라이프사이클을 우회).
                    if lead.converted_hospital_id:
                        hospital = db.get(Hospital, lead.converted_hospital_id)
                        if hospital and hospital.onboarding_note:
                            hospital.onboarding_note = scrub_onboarding_note(
                                hospital.onboarding_note, lead.id
                            )
            if purged:
                db.commit()
        logger.info(f"purge_expired_leads: anonymized {purged} expired leads")
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("purge_expired_leads failed")

    try:
        _run_async(notifier.notify_lead_purge_result(purged=purged, error=error_msg))
    except Exception:
        logger.exception("purge_expired_leads slack notify failed (non-fatal)")

    return {"purged": purged, "error": error_msg}
