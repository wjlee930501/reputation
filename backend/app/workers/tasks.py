"""
Celery 태스크 전체
- trigger_v0_report: 프로파일 완료 시 V0 분석 트리거
- build_aeo_site: AI 노출 웹블로그 상태 준비
- nightly_content_generation: 매일 밤 내일 콘텐츠 생성
- morning_content_notification: 매일 아침 오늘 콘텐츠 Slack
- run_sov_for_hospital: 단일 병원 AI 답변 언급률 측정
- run_weekly_monitoring: 전체 병원 주간 측정
- adjust_query_priorities: AI 답변 언급 결과 기반 질문 우선순위 조정
- run_monthly_reports: 전체 병원 월간 리포트
"""
import asyncio
import logging
import threading
import uuid
from datetime import date, datetime, timezone, timedelta

import arrow
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, AIQueryVariant, MeasurementRun, QueryMatrix, SovRecord
from app.services import notifier
from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.content_engine import generate_content
from app.services.essence_engine import (
    ESSENCE_STATUS_MISSING_APPROVED,
    build_monthly_essence_summary,
    screen_content_against_philosophy,
)
from app.services.image_engine import generate_image
from app.services.report_engine import generate_pdf_report
from app.services.sov_engine import generate_query_matrix, run_single_query, calculate_sov

logger = logging.getLogger(__name__)

ADMIN_BASE_URL = settings.ADMIN_BASE_URL
SOV_REPEAT_WEEKLY = min(settings.SOV_REPEAT_COUNT_WEEKLY, 20)      # 주간 측정용

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


# ══════════════════════════════════════════════════════════════════
# V0 리포트
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.trigger_v0_report", bind=True, max_retries=2)
def trigger_v0_report(self, hospital_id: str):
    """프로파일 완료 후 V0 분석 즉시 실행"""
    try:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital:
                return

            hospital.status = HospitalStatus.ANALYZING
            db.commit()

            # 쿼리 매트릭스 생성
            queries = generate_query_matrix(hospital.region, hospital.specialties, hospital.keywords)
            for q_text in queries:
                db.add(QueryMatrix(hospital_id=hospital.id, query_text=q_text))
            db.flush()

            # AI 답변 언급률 측정 (V0: 쿼리 수 최대 5개로 제한, 빠른 실행)
            run = _start_measurement_run(
                db,
                hospital,
                run_label="V0 baseline",
                config={"source": "trigger_v0_report", "repeat_count": 5},
            )
            all_records = []
            success_count = 0
            failure_count = 0
            stmt = select(QueryMatrix).where(
                QueryMatrix.hospital_id == hospital.id
            ).limit(5)
            result = db.execute(stmt)
            sample_queries = result.scalars().all()

            platforms = ["chatgpt"]
            if settings.GEMINI_API_KEY:
                platforms.append("gemini")
            competitors = hospital.competitors or []
            for q in sample_queries:
                for platform in platforms:
                    results = _run_async(
                        run_single_query(hospital.name, q.query_text, platform, repeat_count=5, competitors=competitors)
                    )
                    for r in results:
                        measurement_status, failure_reason = _measurement_status_for_result(r)
                        if measurement_status == "SUCCESS":
                            success_count += 1
                        else:
                            failure_count += 1
                        record = SovRecord(
                            hospital_id=hospital.id,
                            query_id=q.id,
                            measurement_run_id=run.id,
                            ai_platform=platform,
                            is_mentioned=r["is_mentioned"],
                            mention_rank=r.get("mention_rank"),
                            mention_sentiment=r.get("sentiment"),
                            mention_context=r.get("mention_context"),
                            raw_response=r["raw_response"],
                            competitor_mentions=r.get("competitor_mentions"),
                            measurement_status=measurement_status,
                            failure_reason=failure_reason,
                        )
                        db.add(record)
                        all_records.append(r)

            _finish_measurement_run(run, success_count, failure_count)
            db.commit()

            # AI 답변 언급률 계산
            sov_pct = calculate_sov(all_records)

            # PDF 리포트 생성
            now = arrow.now("Asia/Seoul")
            pdf_path = generate_pdf_report(
                db=db,
                hospital=hospital,
                period_start=now.shift(days=-7).datetime,
                period_end=now.datetime,
                report_type="V0",
                sov_pct=sov_pct,
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

            # Slack 알림
            _run_async(notifier.notify_v0_report_ready(hospital.name, sov_pct, pdf_path))

            # AI 노출 웹블로그 준비 태스크 큐잉
            build_aeo_site.apply_async(args=[hospital_id], queue="default")

    except Exception as exc:
        logger.error(f"trigger_v0_report failed: {exc}")
        raise self.retry(exc=exc, countdown=120)


# ══════════════════════════════════════════════════════════════════
# AI 노출 웹블로그 준비
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.build_aeo_site", bind=True)
def build_aeo_site(self, hospital_id: str):
    """AI 노출 웹블로그 상태 전환 + Slack 알림 (실제 공개 화면은 Next.js /site 담당)"""
    with SyncSessionLocal() as db:
        hospital = db.get(Hospital, uuid.UUID(hospital_id))
        if not hospital:
            return

        hospital.site_built = True
        hospital.status = HospitalStatus.PENDING_DOMAIN
        db.commit()

        preview_url = f"https://preview.motionlabs.io/{hospital.slug}/"
        _run_async(notifier.notify_site_built(hospital.name, preview_url))


# ══════════════════════════════════════════════════════════════════
# 야간 콘텐츠 자동 생성 (매일 밤 23:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.nightly_content_generation")
def nightly_content_generation():
    """내일 발행 예정인 콘텐츠를 오늘 밤에 생성"""
    tomorrow = arrow.now("Asia/Seoul").shift(days=1).date()

    with SyncSessionLocal() as db:
        # 내일 발행 예정이고 아직 생성 안 된 콘텐츠 조회
        stmt = select(ContentItem).where(
            ContentItem.scheduled_date == tomorrow,
            ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
            ContentItem.body.is_(None),
        ).options(joinedload(ContentItem.hospital)).limit(50)
        result = db.execute(stmt)
        items = result.scalars().all()

        if not items:
            logger.info(f"No content to generate for {tomorrow}")
            return

        for item in items:
            hospital = item.hospital

            try:
                # 기존 제목 목록 (중복 방지)
                existing = db.execute(
                    select(ContentItem.title).where(
                        ContentItem.hospital_id == hospital.id,
                        ContentItem.title.isnot(None),
                    )
                )
                existing_titles = [r[0] for r in existing.all()]

                philosophy = db.execute(
                    select(HospitalContentPhilosophy).where(
                        HospitalContentPhilosophy.hospital_id == hospital.id,
                        HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
                    )
                ).scalar_one_or_none()
                if not philosophy:
                    item.content_philosophy_id = None
                    item.essence_status = ESSENCE_STATUS_MISSING_APPROVED
                    item.essence_check_summary = {
                        "blocking": True,
                        "findings": ["승인된 콘텐츠 철학이 없어 자동 생성/발행 품질을 통과할 수 없습니다."],
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    db.commit()
                    logger.warning(f"Skipping content generation without approved philosophy: {hospital.name}")
                    continue

                # Claude Sonnet 콘텐츠 생성
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
                item.title = content_data["title"]
                item.body = content_data["body"]
                item.meta_description = content_data.get("meta_description")
                item.generated_at = datetime.now(timezone.utc)
                item.status = ContentStatus.DRAFT
                item.content_philosophy_id = philosophy.id
                screening = screen_content_against_philosophy(item, philosophy)
                item.essence_status = screening.status
                item.essence_check_summary = screening.summary

                # 텍스트 콘텐츠 먼저 커밋 (이미지 실패가 텍스트를 롤백하지 않도록)
                db.commit()
                logger.info(f"Content generated: {hospital.name} — {item.title}")

                # Imagen 3 이미지 생성 (실패해도 텍스트는 유지)
                try:
                    image_url, image_prompt = _run_async(generate_image(item.content_type, hospital.slug))
                    item.image_url = image_url
                    item.image_prompt = image_prompt
                    db.commit()
                except Exception as img_e:
                    logger.warning(f"Image generation failed for {item.id} (text saved): {img_e}")
                    db.rollback()
                    db.refresh(item)  # re-sync after rollback

            except Exception as e:
                logger.error(f"Content generation failed for {item.id}: {e}")
                db.rollback()
                db.expire_all()  # expire stale ORM state after rollback


# ══════════════════════════════════════════════════════════════════
# 아침 Slack 알림 (매일 08:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.morning_content_notification")
def morning_content_notification():
    """오늘 발행 예정 콘텐츠 초안 완료 알림"""
    today = arrow.now("Asia/Seoul").date()

    with SyncSessionLocal() as db:
        stmt = select(ContentItem).where(
            ContentItem.scheduled_date == today,
            ContentItem.status == ContentStatus.DRAFT,
            ContentItem.body.isnot(None),
        ).options(joinedload(ContentItem.hospital))
        result = db.execute(stmt)
        items = result.scalars().all()

        for item in items:
            admin_url = f"{settings.ADMIN_BASE_URL}/hospitals/{item.hospital_id}/content/{item.id}"
            _run_async(notifier.notify_content_draft_ready(
                hospital_name=item.hospital.name,
                sequence_no=item.sequence_no,
                total_count=item.total_count,
                content_type=item.content_type.value,
                scheduled_date=str(item.scheduled_date),
                admin_url=admin_url,
            ))


# ══════════════════════════════════════════════════════════════════
# AI 답변 언급률 측정
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.run_sov_for_hospital", bind=True, max_retries=1)
def run_sov_for_hospital(self, hospital_id: str):
    try:
        with SyncSessionLocal() as db:
            hospital = db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital or hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
                return

            # priority 기반 쿼리 필터링
            current_week = date.today().isocalendar()[1]
            is_even_week = (current_week % 2 == 0)
            current_month_day = date.today().day
            is_month_start = (current_month_day <= 7)  # 월초 첫째 주

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

            # priority 필터 적용:
            # HIGH: 항상 포함
            # NORMAL: 짝수 주차에만 포함 (홀수 주차 스킵)
            # LOW: 월초(1~7일)에만 포함
            queries = [
                q for q in all_queries
                if q.priority == "HIGH"
                or (q.priority == "NORMAL" and is_even_week)
                or (q.priority == "LOW" and is_month_start)
            ]

            measurement_specs = _build_measurement_specs(
                db=db,
                hospital=hospital,
                query_targets=query_targets,
                fallback_queries=queries,
            )

            if not measurement_specs:
                logger.info(f"No queries to run for hospital {hospital_id} this week (priority filter)")
                return

            competitors = hospital.competitors or []
            run = _start_measurement_run(
                db,
                hospital,
                run_label=f"weekly_sov_{date.today().isoformat()}",
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
                    measurement_status, failure_reason = _measurement_status_for_result(r)
                    if measurement_status == "SUCCESS":
                        success_count += 1
                    else:
                        failure_count += 1
                    records.append(SovRecord(
                        hospital_id=hospital.id,
                        query_id=spec["query_id"],
                        measurement_run_id=run.id,
                        ai_query_target_id=spec["target_id"],
                        ai_query_variant_id=spec["variant_id"],
                        ai_platform=spec["platform"],
                        is_mentioned=r["is_mentioned"],
                        mention_rank=r.get("mention_rank"),
                        mention_sentiment=r.get("sentiment"),
                        mention_context=r.get("mention_context"),
                        raw_response=r["raw_response"],
                        competitor_mentions=r.get("competitor_mentions"),
                        measurement_status=measurement_status,
                        failure_reason=failure_reason,
                    ))

            db.add_all(records)
            _finish_measurement_run(run, success_count, failure_count)
            db.commit()

    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


def _start_measurement_run(db, hospital: Hospital, *, run_label: str, config: dict) -> MeasurementRun:
    now = datetime.now(timezone.utc)
    run = MeasurementRun(
        hospital_id=hospital.id,
        run_label=run_label,
        measurement_method="OPENAI_RESPONSE",
        status="RUNNING",
        query_count=0,
        success_count=0,
        failure_count=0,
        started_at=now,
        model_name=settings.OPENAI_MODEL_QUERY,
        search_mode="web" if settings.GEMINI_API_KEY else "model",
        config=config,
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


def _build_measurement_specs(
    *,
    db,
    hospital: Hospital,
    query_targets: list[AIQueryTarget],
    fallback_queries: list[QueryMatrix],
) -> list[dict]:
    specs: list[dict] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    for target in query_targets:
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
            specs.append({
                "query_id": query.id,
                "query_text": variant.query_text,
                "platform": platform,
                "target_id": target.id,
                "variant_id": variant.id,
            })

    if specs:
        return specs

    platforms = ["chatgpt"]
    if settings.GEMINI_API_KEY:
        platforms.append("gemini")
    for query in fallback_queries:
        for platform in platforms:
            specs.append({
                "query_id": query.id,
                "query_text": query.query_text,
                "platform": platform,
                "target_id": None,
                "variant_id": None,
            })
    return specs


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


def _normalize_platform(platform: str) -> str:
    value = (platform or "CHATGPT").strip().lower()
    if value in {"gemini", "google"}:
        return "gemini"
    return "chatgpt"


# ══════════════════════════════════════════════════════════════════
# 다음 달 콘텐츠 슬롯 자동 생성 (매월 25일 00:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.monthly_slot_generation")
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
        for schedule in schedules:
            hospital = schedule.hospital
            if hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
                continue

            # 이미 다음 달 슬롯이 있으면 스킵
            existing = db.execute(
                select(ContentItem.id).where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.scheduled_date >= next_month_start,
                    ContentItem.scheduled_date <= next_month_end,
                ).limit(1)
            )
            if existing.scalar():
                continue

            # 슬롯 생성 (content_calendar 모듈 사용)
            from app.services.content_calendar import generate_monthly_slots
            slots = generate_monthly_slots(schedule.plan, schedule.publish_days, next_month)
            for slot_date, ctype, seq_no, total in slots:
                db.add(ContentItem(
                    hospital_id=hospital.id,
                    schedule_id=schedule.id,
                    content_type=ctype,
                    sequence_no=seq_no,
                    total_count=total,
                    scheduled_date=slot_date,
                    status=ContentStatus.DRAFT,
                ))
            created_count += 1
            logger.info(
                f"Next month slots created: {hospital.name} "
                f"{next_month.format('YYYY-MM')} ({len(slots)} slots)"
            )

        db.commit()
        logger.info(f"monthly_slot_generation done: {created_count} hospitals processed")


@celery_app.task(name="app.workers.tasks.run_weekly_monitoring")
def run_weekly_monitoring():
    with SyncSessionLocal() as db:
        stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
        result = db.execute(stmt)
        hospitals = result.scalars().all()

        for h in hospitals:
            run_sov_for_hospital.apply_async(args=[str(h.id)], queue="sov")

        _run_async(notifier.notify_monitoring_done(len(hospitals), len(hospitals)))


@celery_app.task(name="app.workers.tasks.adjust_query_priorities")
def adjust_query_priorities():
    """Adjust query priorities based on recent SoV results. Run AFTER weekly SoV tasks complete."""
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
                rec_stmt = select(SovRecord).where(
                    SovRecord.query_id == q.id,
                    SovRecord.measured_at >= four_weeks_ago,
                ).order_by(SovRecord.measured_at.desc())
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
# 월간 리포트 (매월 마지막 날 23:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.run_monthly_reports")
def run_monthly_reports():
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
                sov_pct = calculate_sov([{"is_mentioned": r.is_mentioned} for r in sov_records])

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
                prev_sov = calculate_sov([{"is_mentioned": r.is_mentioned} for r in prev_records]) if prev_records else None
                change_pct = round(sov_pct - prev_sov, 1) if prev_sov is not None else None

                # 이번 달 발행 콘텐츠 집계
                content_stmt = select(ContentItem).where(
                    ContentItem.hospital_id == h.id,
                    ContentItem.status == ContentStatus.PUBLISHED,
                    ContentItem.published_at >= period_start,
                    ContentItem.published_at <= period_end,
                )
                content_result = db.execute(content_stmt)
                published_contents = content_result.scalars().all()

                pdf_path = generate_pdf_report(
                    db=db,
                    hospital=h,
                    period_start=period_start,
                    period_end=period_end,
                    report_type="MONTHLY",
                    sov_pct=sov_pct,
                    published_count=len(published_contents),
                )
                essence_summary = build_monthly_essence_summary(db, h, period_start, period_end)

                db.add(MonthlyReport(
                    hospital_id=h.id,
                    period_year=now.year,
                    period_month=now.month,
                    report_type="MONTHLY",
                    pdf_path=pdf_path,
                    sov_summary={"sov_pct": sov_pct, "prev_sov_pct": prev_sov, "change_pct": change_pct},
                    content_summary={"published_count": len(published_contents)},
                    essence_summary=essence_summary,
                ))
                db.commit()

                _run_async(notifier.notify_monthly_report_ready(
                    h.name, now.year, now.month, sov_pct, change_pct, pdf_path
                ))

            except Exception as e:
                logger.error(f"Monthly report failed for {h.name}: {e}")
                db.rollback()
