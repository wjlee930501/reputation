import asyncio
import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_prerun

from app.core.config import settings
from app.core.observability import configure_logging, sentry_before_send, set_request_id

# Worker logs share the API's structured format + request_id filter (OBS-1/OBS-2).
configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)

if settings.SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[CeleryIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        before_send=sentry_before_send,
    )


@task_prerun.connect
def _bind_request_id(task=None, **_kwargs):
    """Re-bind the originating request_id (if propagated as a task header) for log correlation."""
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    set_request_id(headers.get("request_id") if isinstance(headers, dict) else None)


@task_failure.connect
def _alert_on_task_failure(sender=None, task_id=None, exception=None, **_kwargs):
    """Slack-alert when a task fails after retries are exhausted (CELERY-3)."""
    task_name = getattr(sender, "name", str(sender))
    try:
        from app.services import notifier

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                notifier.notify_task_failure(
                    task_name=task_name, task_id=str(task_id or "?"), error=str(exception)
                )
            )
        finally:
            loop.close()
    except Exception:
        logging.getLogger("app.celery").error("task_failure alert delivery failed for %s", task_name)

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
    # 외부 API(Claude/Imagen/OpenAI/Gemini)가 멈춰도 워커 슬롯이 영구 점유되지 않도록
    # 전역 wall-clock 한계(CELERY-2). soft → SoftTimeLimitExceeded로 부분 커밋 후 정리,
    # hard → 자식 프로세스 강제 종료. 긴 배치(nightly)는 태스크 데코레이터에서 상향.
    task_soft_time_limit=600,
    task_time_limit=900,
    # 워커가 루트 로거를 가로채지 않도록 — configure_logging 설정을 유지.
    worker_hijack_root_logger=False,
    task_routes={
        "app.workers.tasks.nightly_content_generation": {"queue": "content"},
        "app.workers.tasks.regenerate_content_item": {"queue": "content"},
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
        # 매일 04:00 — 보관기간 만료 리드 자동 파기 (개인정보보호법 제21조)
        "purge-expired-leads": {
            "task": "app.workers.tasks.purge_expired_leads",
            "schedule": crontab(hour=4, minute=0),
        },
    },
)
