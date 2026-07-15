import asyncio
import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_prerun

from app.core.config import settings
from app.core.observability import configure_logging, sentry_before_send, set_request_id


# Redis에 저장된 정적 스케줄과 배포 이미지의 선언을 맞출 때 사용하는 명시적 버전.
# beat_schedule을 추가/삭제/시간 변경할 때 반드시 올린다. 배포 스크립트의
# reconcile-redbeat Job이 이 버전을 기록하고, --check 모드가 드리프트를 차단한다.
REDBEAT_SCHEDULE_VERSION = "2026-07-16.2"

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
        logging.getLogger("app.celery").error(
            "task_failure alert delivery failed for %s", task_name
        )


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
    # 배포/scale-in 시 태스크 유실 방지: ack를 실행 완료 후로 미루고(acks_late),
    # 프리페치를 1로 줄여 미실행 태스크가 종료되는 워커에 잡혀 있지 않게 하며,
    # 워커 프로세스가 죽으면 태스크를 큐로 되돌린다(reject_on_worker_lost).
    # 재실행될 수 있으므로 태스크 멱등성 가드가 전제다 (이미 적용됨).
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Beat 신뢰성 (Cloud Run 롤아웃 중 구/신 beat가 잠시 공존):
    # RedBeat은 Redis 분산 락으로 단일 dispatcher를 보장하고, 스케줄 상태를
    # Redis에 보존해 재시작 후에도 last-run 정보가 유지된다(중복/누락 방지).
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.REDIS_URL,
    # RedBeat 기본 max loop interval은 300초다. 기존 락 TTL도 정확히 300초여서
    # 다음 tick에서 이미 만료된 락을 extend하며 LockNotOwnedError가 발생했다.
    # 30초마다 갱신해 Redis/Cloud Run 지연이 있어도 TTL 대비 10배 여유를 둔다.
    beat_max_loop_interval=30,
    # 락 TTL: beat가 죽으면 이 시간 후 새 beat가 인계. 롤아웃 중 이중 dispatch를
    # 막을 만큼 길고, 장애 시 스케줄 공백이 과하지 않을 만큼 짧게.
    redbeat_lock_timeout=300,
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
        # 라우팅 누락 시 기본 "celery" 큐로 떨어지는데, 워커는 -Q default,content,sov,reports만
        # 소비하므로 영원히 실행되지 않는다 — beat 태스크는 반드시 여기 등록할 것
        # (tests/test_celery_routing.py가 회귀를 막는다).
        "app.workers.tasks.purge_expired_leads": {"queue": "default"},
        "app.workers.tasks.adjust_query_priorities": {"queue": "sov"},
        "app.workers.tasks.monitor_live_custom_domains": {"queue": "default"},
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
        # 매월 28~31일 21:00 — 월간 SoV 리포트 (태스크 내부에서 마지막 날 체크).
        # 야간 콘텐츠 생성(23:00)과 시간대를 분리해 reports/content 워커 슬롯 경합을 피한다.
        "monthly-reports": {
            "task": "app.workers.tasks.run_monthly_reports",
            "schedule": crontab(hour=21, minute=0, day_of_month="28-31"),
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
        # 15분마다 — 런타임으로 추가된 모든 병원 자기 도메인의 실제 TLS/Host 응답 확인.
        "live-custom-domain-health": {
            "task": "app.workers.tasks.monitor_live_custom_domains",
            "schedule": crontab(minute="*/15"),
        },
    },
)
