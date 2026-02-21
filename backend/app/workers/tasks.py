"""
Celery 태스크 전체
- trigger_v0_report: 프로파일 완료 시 V0 분석 트리거
- build_aeo_site: AEO 홈페이지 빌드
- nightly_content_generation: 매일 밤 내일 콘텐츠 생성
- morning_content_notification: 매일 아침 오늘 콘텐츠 Slack
- run_sov_for_hospital: 단일 병원 SoV 측정
- run_weekly_monitoring: 전체 병원 주간 측정
- run_monthly_reports: 전체 병원 월간 리포트
"""
import asyncio
import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from itertools import product

import arrow
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType, PLAN_DISTRIBUTION
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MonthlyReport
from app.models.sov import QueryMatrix, SovRecord
from app.services import notifier, site_builder
from app.services.content_engine import generate_content
from app.services.image_engine import generate_image
from app.services.report_engine import generate_pdf_report
from app.services.sov_engine import generate_query_matrix, run_single_query, calculate_sov

logger = logging.getLogger(__name__)

ADMIN_BASE_URL = settings.ADMIN_BASE_URL
SOV_REPEAT = min(settings.SOV_REPEAT_COUNT, 20)  # 최대 20회로 제한 (비용 제어)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ══════════════════════════════════════════════════════════════════
# V0 리포트
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.trigger_v0_report", bind=True, max_retries=2)
def trigger_v0_report(self, hospital_id: str):
    """프로파일 완료 후 V0 분석 즉시 실행"""
    async def _run_inner():
        async with AsyncSessionLocal() as db:
            hospital = await db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital:
                return

            hospital.status = HospitalStatus.ANALYZING
            await db.commit()

            # 쿼리 매트릭스 생성
            queries = generate_query_matrix(hospital.region, hospital.specialties, hospital.keywords)
            for q_text in queries:
                db.add(QueryMatrix(hospital_id=hospital.id, query_text=q_text))
            await db.flush()

            # SoV 측정 (V0: 쿼리 수 최대 5개로 제한, 빠른 실행)
            all_records = []
            stmt = select(QueryMatrix).where(
                QueryMatrix.hospital_id == hospital.id
            ).limit(5)
            result = await db.execute(stmt)
            sample_queries = result.scalars().all()

            for q in sample_queries:
                for platform in ["chatgpt"]:
                    results = await run_single_query(
                        hospital.name, q.query_text, platform, repeat_count=5
                    )
                    for r in results:
                        record = SovRecord(
                            hospital_id=hospital.id,
                            query_id=q.id,
                            ai_platform=platform,
                            is_mentioned=r["is_mentioned"],
                            mention_rank=r.get("mention_rank"),
                            mention_sentiment=r.get("sentiment"),
                            mention_context=r.get("mention_context"),
                            raw_response=r["raw_response"],
                        )
                        db.add(record)
                        all_records.append(r)

            await db.commit()

            # SoV 계산
            sov_pct = calculate_sov(all_records)

            # PDF 리포트 생성
            now = arrow.now("Asia/Seoul")
            pdf_path = await generate_pdf_report(
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
                sov_summary={"sov_pct": sov_pct, "platform": "chatgpt"},
            )
            db.add(report)
            hospital.v0_report_done = True
            hospital.status = HospitalStatus.BUILDING
            await db.commit()

            # Slack 알림
            await notifier.notify_v0_report_ready(hospital.name, sov_pct, pdf_path)

            # AEO 사이트 빌드 태스크 큐잉
            build_aeo_site.apply_async(args=[hospital_id], queue="default")

    try:
        _run(_run_inner())
    except Exception as exc:
        logger.error(f"trigger_v0_report failed: {exc}")
        raise self.retry(exc=exc, countdown=120)


# ══════════════════════════════════════════════════════════════════
# AEO 사이트 빌드
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.build_aeo_site", bind=True)
def build_aeo_site(self, hospital_id: str):
    """AEO 홈페이지 정적 빌드"""
    async def _run_inner():
        async with AsyncSessionLocal() as db:
            hospital = await db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital:
                return

            domain = hospital.aeo_domain or f"{hospital.slug}.motionlabs.io"
            build_path = site_builder.build_site(hospital, domain)

            hospital.site_built = True
            hospital.aeo_site_path = build_path
            hospital.status = HospitalStatus.PENDING_DOMAIN
            await db.commit()

            preview_url = f"https://preview.motionlabs.io/{hospital.slug}/"
            await notifier.notify_site_built(hospital.name, preview_url)

    _run(_run_inner())


# ══════════════════════════════════════════════════════════════════
# 콘텐츠 캘린더 생성
# ══════════════════════════════════════════════════════════════════
def _build_monthly_calendar(
    schedule: ContentSchedule,
    target_month: arrow.Arrow,
) -> tuple[list[tuple[date, ContentType, int]], int]:
    """
    해당 월의 발행 날짜·유형·순번 목록 생성.
    Returns: ([(scheduled_date, content_type, seq_no), ...], total)

    🔴 CRITICAL fix: return type annotation was `list[tuple[...]]` but function
    actually returns a `(list, int)` tuple — callers unpacking as `result, total`
    would silently unpack wrong values with the incorrect annotation.
    """
    distribution = PLAN_DISTRIBUTION.get(schedule.plan, {})
    publish_days = schedule.publish_days  # [1, 4] = 화·금

    # 발행 날짜 목록 (해당 월의 지정 요일)
    dates = []
    day = target_month.floor("month")
    end = target_month.ceil("month")
    while day <= end:
        if day.weekday() in publish_days:
            dates.append(day.date())
        day = day.shift(days=1)

    # 유형 시퀀스 생성 (순환)
    type_sequence = []
    for content_type, count in distribution.items():
        type_sequence.extend([content_type] * count)

    total = len(type_sequence)
    result = []
    for i, (pub_date, ctype) in enumerate(zip(dates, type_sequence)):
        result.append((pub_date, ctype, i + 1))

    # 🟡 WARNING: zip() silently truncates when dates < type_sequence
    if len(result) < total:
        logger.warning(
            f"Calendar slots ({len(result)}) < plan total ({total}) for schedule "
            f"{schedule.id}. Not enough publish days in {target_month.format('YYYY-MM')}."
        )

    return result, total


# ══════════════════════════════════════════════════════════════════
# 야간 콘텐츠 자동 생성 (매일 밤 23:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.nightly_content_generation")
def nightly_content_generation():
    """내일 발행 예정인 콘텐츠를 오늘 밤에 생성"""
    async def _run_inner():
        tomorrow = arrow.now("Asia/Seoul").shift(days=1).date()

        async with AsyncSessionLocal() as db:
            # 내일 발행 예정이고 아직 생성 안 된 콘텐츠 조회
            # TODO: 병원 수 증가 시 페이징(offset 기반) 처리 필요
            stmt = select(ContentItem).where(
                ContentItem.scheduled_date == tomorrow,
                ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
                ContentItem.body.is_(None),  # 아직 생성 안 됨
            ).options(selectinload(ContentItem.hospital)).limit(50)
            result = await db.execute(stmt)
            items = result.scalars().all()

            if not items:
                logger.info(f"No content to generate for {tomorrow}")
                return

            for item in items:
                hospital = item.hospital

                try:
                    # 기존 제목 목록 (중복 방지)
                    existing = await db.execute(
                        select(ContentItem.title).where(
                            ContentItem.hospital_id == hospital.id,
                            ContentItem.title.isnot(None),
                        )
                    )
                    existing_titles = [r[0] for r in existing.all()]

                    # Claude Sonnet 콘텐츠 생성
                    content_data = await generate_content(hospital, item.content_type, existing_titles)
                    item.title = content_data["title"]
                    item.body = content_data["body"]
                    item.meta_description = content_data.get("meta_description")
                    item.generated_at = datetime.now(timezone.utc)
                    item.status = ContentStatus.DRAFT

                    # Imagen 3 이미지 생성
                    image_url, image_prompt = await generate_image(item.content_type, hospital.slug)
                    item.image_url = image_url
                    item.image_prompt = image_prompt

                    await db.commit()
                    logger.info(f"Content generated: {hospital.name} — {item.title}")

                except Exception as e:
                    logger.error(f"Content generation failed for {item.id}: {e}")
                    await db.rollback()

    _run(_run_inner())


# ══════════════════════════════════════════════════════════════════
# 아침 Slack 알림 (매일 08:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.morning_content_notification")
def morning_content_notification():
    """오늘 발행 예정 콘텐츠 초안 완료 알림"""
    async def _run_inner():
        today = arrow.now("Asia/Seoul").date()

        async with AsyncSessionLocal() as db:
            stmt = select(ContentItem).where(
                ContentItem.scheduled_date == today,
                ContentItem.status == ContentStatus.DRAFT,
                ContentItem.body.isnot(None),
            ).options(selectinload(ContentItem.hospital))
            result = await db.execute(stmt)
            items = result.scalars().all()

            for item in items:
                admin_url = f"{settings.ADMIN_BASE_URL}/hospitals/{item.hospital_id}/content/{item.id}"
                await notifier.notify_content_draft_ready(
                    hospital_name=item.hospital.name,
                    sequence_no=item.sequence_no,
                    total_count=item.total_count,
                    content_type=item.content_type.value,
                    scheduled_date=str(item.scheduled_date),
                    admin_url=admin_url,
                )

    _run(_run_inner())


# ══════════════════════════════════════════════════════════════════
# SoV 측정
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.run_sov_for_hospital", bind=True, max_retries=1)
def run_sov_for_hospital(self, hospital_id: str):
    async def _run_inner():
        async with AsyncSessionLocal() as db:
            hospital = await db.get(Hospital, uuid.UUID(hospital_id))
            if not hospital or hospital.status not in (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN):
                return

            stmt = select(QueryMatrix).where(
                QueryMatrix.hospital_id == hospital.id,
                QueryMatrix.is_active == True,
            ).limit(10)
            result = await db.execute(stmt)
            queries = result.scalars().all()

            platforms = ["chatgpt"]
            if settings.PERPLEXITY_API_KEY:
                platforms.append("perplexity")

            records = []
            for q in queries:
                for platform in platforms:
                    results = await run_single_query(hospital.name, q.query_text, platform, SOV_REPEAT)
                    for r in results:
                        records.append(SovRecord(
                            hospital_id=hospital.id,
                            query_id=q.id,
                            ai_platform=platform,
                            is_mentioned=r["is_mentioned"],
                            mention_rank=r.get("mention_rank"),
                            mention_sentiment=r.get("sentiment"),
                            mention_context=r.get("mention_context"),
                            raw_response=r["raw_response"],
                        ))

            db.add_all(records)
            await db.flush()
            await db.commit()

    try:
        _run(_run_inner())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.workers.tasks.run_weekly_monitoring")
def run_weekly_monitoring():
    async def _run_inner():
        async with AsyncSessionLocal() as db:
            stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
            result = await db.execute(stmt)
            hospitals = result.scalars().all()

            for h in hospitals:
                run_sov_for_hospital.apply_async(args=[str(h.id)], queue="sov")

            await notifier.notify_monitoring_done(len(hospitals), len(hospitals))

    _run(_run_inner())


# ══════════════════════════════════════════════════════════════════
# 월간 리포트 (매월 마지막 날 23:00)
# ══════════════════════════════════════════════════════════════════
@celery_app.task(name="app.workers.tasks.run_monthly_reports")
def run_monthly_reports():
    async def _run_inner():
        now = arrow.now("Asia/Seoul")
        # beat은 28~31일에 매일 실행 — 실제 마지막 날인지 확인
        if now.date() != now.ceil("month").date():
            logger.info(f"Not last day of month ({now.date()}), skipping monthly reports")
            return
        period_start = now.floor("month").datetime
        period_end = now.ceil("month").datetime

        async with AsyncSessionLocal() as db:
            stmt = select(Hospital).where(Hospital.status == HospitalStatus.ACTIVE)
            result = await db.execute(stmt)
            hospitals = result.scalars().all()

            for h in hospitals:
                try:
                    # 🔴 CRITICAL fix: 월간 리포트 중복 생성 방지
                    existing_check = await db.execute(
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

                    # 이번 달 SoV 집계
                    sov_stmt = select(SovRecord).where(
                        SovRecord.hospital_id == h.id,
                        SovRecord.measured_at >= period_start,
                        SovRecord.measured_at <= period_end,
                    )
                    sov_result = await db.execute(sov_stmt)
                    sov_records = sov_result.scalars().all()
                    sov_pct = calculate_sov([{"is_mentioned": r.is_mentioned} for r in sov_records])

                    # 전월 SoV
                    prev_start = now.shift(months=-1).floor("month").datetime
                    prev_end = now.floor("month").datetime
                    prev_stmt = select(SovRecord).where(
                        SovRecord.hospital_id == h.id,
                        SovRecord.measured_at >= prev_start,
                        SovRecord.measured_at < prev_end,
                    )
                    prev_result = await db.execute(prev_stmt)
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
                    content_result = await db.execute(content_stmt)
                    published_contents = content_result.scalars().all()

                    pdf_path = await generate_pdf_report(
                        db=db,
                        hospital=h,
                        period_start=period_start,
                        period_end=period_end,
                        report_type="MONTHLY",
                        sov_pct=sov_pct,
                        published_count=len(published_contents),
                    )

                    db.add(MonthlyReport(
                        hospital_id=h.id,
                        period_year=now.year,
                        period_month=now.month,
                        report_type="MONTHLY",
                        pdf_path=pdf_path,
                        sov_summary={"sov_pct": sov_pct, "prev_sov_pct": prev_sov, "change_pct": change_pct},
                        content_summary={"published_count": len(published_contents)},
                    ))
                    await db.commit()

                    await notifier.notify_monthly_report_ready(
                        h.name, now.year, now.month, sov_pct, change_pct, pdf_path
                    )

                except Exception as e:
                    logger.error(f"Monthly report failed for {h.name}: {e}")
                    await db.rollback()

    _run(_run_inner())
