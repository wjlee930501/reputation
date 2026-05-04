from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

if settings.SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    sentry_sdk.init(dsn=settings.SENTRY_DSN, integrations=[CeleryIntegration()], traces_sample_rate=0.1)

celery_app = Celery(
    "reputation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_routes={
        "app.workers.tasks.nightly_content_generation": {"queue": "content"},
        "app.workers.tasks.morning_content_notification": {"queue": "content"},
        "app.workers.tasks.run_sov_for_hospital": {"queue": "sov"},
        "app.workers.tasks.run_weekly_monitoring": {"queue": "sov"},
        "app.workers.tasks.run_monthly_reports": {"queue": "reports"},
        "app.workers.tasks.trigger_v0_report": {"queue": "reports"},
        "app.workers.tasks.build_aeo_site": {"queue": "default"},
        "app.workers.tasks.monthly_slot_generation": {"queue": "default"},
    },
    beat_schedule={
        # 매일 밤 23:00 — 내일 발행 예정 콘텐츠 자동 생성
        "nightly-content-generation": {
            "task": "app.workers.tasks.nightly_content_generation",
            "schedule": crontab(hour=23, minute=0),
        },
        # 매일 아침 08:00 — 오늘 발행 예정 콘텐츠 Slack 알림
        "morning-content-notification": {
            "task": "app.workers.tasks.morning_content_notification",
            "schedule": crontab(hour=8, minute=0),
        },
        # 매주 월요일 02:00 — 전체 병원 AI 답변 언급률 측정
        "weekly-sov-monitoring": {
            "task": "app.workers.tasks.run_weekly_monitoring",
            "schedule": crontab(hour=2, minute=0, day_of_week=1),
        },
        # 매월 28~31일 23:00 — 월간 SoV 리포트 (태스크 내부에서 마지막 날 체크)
        "monthly-reports": {
            "task": "app.workers.tasks.run_monthly_reports",
            "schedule": crontab(hour=23, minute=0, day_of_month="28-31"),
        },
        # 매월 25일 00:00 — 다음 달 콘텐츠 슬롯 자동 생성
        "monthly-slot-generation": {
            "task": "app.workers.tasks.monthly_slot_generation",
            "schedule": crontab(hour=0, minute=0, day_of_month=25),
        },
    },
)
