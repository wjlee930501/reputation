import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import before_task_publish, task_failure, task_postrun, task_prerun

from app.core.config import settings
from app.core.observability import configure_logging, sentry_before_send, set_request_id
from app.workers.dispatch_auth import (
    AuthenticatedTask,
    build_dispatch_headers,
    stamp_published_message,
)

# Redis에 저장된 정적 스케줄과 배포 이미지의 선언을 맞출 때 사용하는 명시적 버전.
# beat_schedule을 추가/삭제/시간 변경할 때 반드시 올린다. 배포 스크립트의
# reconcile-redbeat Job이 이 버전을 기록하고, --check 모드가 드리프트를 차단한다.
REDBEAT_SCHEDULE_VERSION = "2026-08-21.1"

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
    """Open one recoverable incident only for a durable operation run."""
    task_name = getattr(sender, "name", str(sender))
    try:
        from app.workers.task_incident_control import record_task_failure

        record_task_failure(sender, str(task_id) if task_id is not None else None)
    except Exception:  # noqa: BLE001 - Celery signal boundary must not replace task outcome.
        logging.getLogger("app.celery").error(
            "task failure incident projection unavailable task_name=%s", task_name
        )


@task_postrun.connect
def _recover_after_task_success(sender=None, task_id=None, state=None, **_kwargs):
    """Close only the incident correlated to this exact successful run."""
    if state != "SUCCESS":
        return
    task_name = getattr(sender, "name", str(sender))
    try:
        from app.workers.task_incident_control import record_task_success

        record_task_success(sender, str(task_id) if task_id is not None else None)
    except Exception:  # noqa: BLE001 - Celery signal boundary must not replace task outcome.
        logging.getLogger("app.celery").error(
            "task recovery projection unavailable task_name=%s", task_name
        )


celery_app = Celery(
    "reputation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    task_cls=AuthenticatedTask,
    include=[
        "app.workers.tasks",
        "app.workers.naver_sync",
        "app.workers.lead_diagnosis_tasks",
        "app.workers.notification_tasks",
        "app.workers.milestone_event_tasks",
        "app.workers.monthly_artifact_reconciliation",
        "app.workers.autonomous_recovery",
        "app.workers.content_backlog_recovery",
        "app.workers.domain_certificate_tasks",
        "app.workers.operation_run_signals",
        "app.workers.canary_tasks",
    ],
)

before_task_publish.connect(stamp_published_message, weak=False)

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
        "app.workers.tasks.overnight_content_generation_recovery": {"queue": "content"},
        "app.workers.tasks.prepublish_content_generation_recovery": {"queue": "content"},
        "app.workers.tasks.regenerate_content_item": {"queue": "content"},
        "app.workers.tasks.auto_review_essence_snapshot": {"queue": "content"},
        "app.workers.tasks.reconcile_essence_snapshots": {"queue": "default"},
        "app.workers.tasks.morning_content_auto_publish": {"queue": "content"},
        "app.workers.tasks.run_sov_for_hospital": {"queue": "sov"},
        "app.workers.tasks.run_weekly_monitoring": {"queue": "sov"},
        "app.workers.tasks.run_monthly_reports": {"queue": "reports"},
        "app.workers.tasks.generate_monthly_report_for_hospital": {"queue": "reports"},
        "app.workers.tasks.trigger_v0_report": {"queue": "reports"},
        "app.workers.tasks.build_aeo_site": {"queue": "default"},
        "app.workers.tasks.retry_site_revalidation": {"queue": "default"},
        "app.workers.tasks.monthly_slot_generation": {"queue": "default"},
        # 라우팅 누락 시 기본 "celery" 큐로 떨어지는데 배포 워커는 명시한 큐만
        # 소비하므로 영원히 실행되지 않는다 — beat 태스크는 반드시 여기 등록할 것
        # (tests/test_celery_routing.py가 회귀를 막는다).
        "app.workers.tasks.purge_expired_leads": {"queue": "default"},
        "app.workers.tasks.adjust_query_priorities": {"queue": "sov"},
        "app.workers.tasks.monitor_live_custom_domains": {"queue": "default"},
        "app.workers.naver_sync.weekly_naver_source_sync": {"queue": "default"},
        # 리드마그넷(1단) — 전용 큐. 유료 측정(sov)과 워커 슬롯을 나눈다.
        # 동시성 풀도 따로다(sov_engine.POOL_LEADGEN) — 큐만 나누면 무료 진단이
        # 몰릴 때 유료 고객 측정이 같은 세마포어에서 굶는다.
        "app.workers.lead_diagnosis_tasks.run_lead_diagnosis": {"queue": "leadgen"},
        "app.workers.lead_diagnosis_tasks.build_lead_report": {"queue": "leadgen"},
        "app.workers.lead_diagnosis_tasks.send_lead_report_email": {"queue": "leadgen"},
        "app.workers.lead_diagnosis_tasks.notify_lead_intake": {"queue": "default"},
        "app.workers.lead_diagnosis_tasks.drain_lead_diagnoses": {"queue": "default"},
        "app.workers.notification_tasks.dispatch_notification_outbox": {"queue": "default"},
        "app.workers.milestone_event_tasks.project_milestone_events": {"queue": "default"},
        "app.workers.monthly_artifact_reconciliation.reconcile": {"queue": "reports"},
        "app.workers.autonomous_recovery.reconcile": {"queue": "default"},
        "app.workers.content_backlog_recovery.reconcile": {"queue": "default"},
        "app.workers.domain_certificate_tasks.provision_domain_certificate": {
            "queue": "certificates"
        },
        "app.workers.canary_tasks.canary_default": {"queue": "default"},
        "app.workers.canary_tasks.canary_content": {"queue": "content"},
        "app.workers.canary_tasks.canary_sov": {"queue": "sov"},
        "app.workers.canary_tasks.canary_reports": {"queue": "reports"},
        "app.workers.canary_tasks.canary_leadgen": {"queue": "leadgen"},
        "app.workers.canary_tasks.canary_certificates": {"queue": "certificates"},
    },
    beat_schedule={
        # 매일 밤 23:00 — 내일 발행 예정 콘텐츠 자동 생성
        "nightly-content-generation": {
            "task": "app.workers.tasks.nightly_content_generation",
            "schedule": crontab(hour=23, minute=0),
            "options": {"headers": build_dispatch_headers("nightly-content-generation")},
        },
        # 23시 생성 이후 운영 기준 승인·비용 차단 해제·일시 공급자 장애가 해결된 항목을
        # 아침 발행 전에 다시 회수한다. 횟수를 네 번으로 제한해 무한 비용 재시도를 막는다.
        "overnight-content-generation-recovery": {
            "task": "app.workers.tasks.overnight_content_generation_recovery",
            "schedule": crontab(hour="1,4,7", minute=0),
            "options": {
                "headers": build_dispatch_headers("overnight-content-generation-recovery")
            },
        },
        "prepublish-content-generation-recovery": {
            "task": "app.workers.tasks.prepublish_content_generation_recovery",
            "schedule": crontab(hour=7, minute=45),
            "options": {
                "headers": build_dispatch_headers("prepublish-content-generation-recovery")
            },
        },
        # 22:30 — 오늘까지 생성되지 못한 슬롯을 병원별 하루 한 편의 빈 미래 날짜로
        # 옮긴다. 23:00의 내일 슬롯 전용 생성 전에 실행해 오래된 슬롯이 좌초되지 않게 한다.
        "stranded-content-recovery": {
            "task": "app.workers.content_backlog_recovery.reconcile",
            "schedule": crontab(hour=22, minute=30),
            "options": {"headers": build_dispatch_headers("reconcile-stranded-content")},
        },
        # 15분마다 — 자료 처리 직후 broker publish가 유실되어도 승인된 병원의
        # 변경 snapshot을 AI 합성·독립 검수 경로로 다시 회수한다.
        "reconcile-essence-snapshots": {
            "task": "app.workers.tasks.reconcile_essence_snapshots",
            "schedule": crontab(minute="*/15"),
            "options": {"headers": build_dispatch_headers("reconcile-essence-snapshots")},
        },
        # 매일 아침 08:00 — 자동 안전검사 후 발행 + 자동 복구 소진 예외 요약
        "morning-content-auto-publish": {
            "task": "app.workers.tasks.morning_content_auto_publish",
            "schedule": crontab(hour=8, minute=0),
            "options": {"headers": build_dispatch_headers("morning-content-auto-publish")},
        },
        # 매주 월요일 02:00 — 전체 병원 AI 답변 언급률 측정
        "weekly-sov-monitoring": {
            "task": "app.workers.tasks.run_weekly_monitoring",
            "schedule": crontab(hour=2, minute=0, day_of_week=1),
            "options": {"headers": build_dispatch_headers("weekly-sov-monitoring")},
        },
        # 매월 1일 00:15 — 직전 달 자료가 모두 들어온 뒤 월간 SoV 리포트를 마감한다.
        "monthly-reports": {
            "task": "app.workers.tasks.run_monthly_reports",
            "schedule": crontab(hour="*/6", minute=15, day_of_month="1-7"),
            "options": {"headers": build_dispatch_headers("monthly-reports")},
        },
        # 매월 25일 00:00 — 다음 달 콘텐츠 슬롯 자동 생성
        "monthly-slot-generation": {
            "task": "app.workers.tasks.monthly_slot_generation",
            "schedule": crontab(hour="*/6", minute=0, day_of_month="25-31"),
            "options": {"headers": build_dispatch_headers("monthly-slot-generation")},
        },
        # 매일 04:00 — 보관기간 만료 리드 자동 파기 (개인정보보호법 제21조)
        "purge-expired-leads": {
            "task": "app.workers.tasks.purge_expired_leads",
            "schedule": crontab(hour=4, minute=0),
            "options": {"headers": build_dispatch_headers("purge-expired-leads")},
        },
        # 매주 화요일 03:00 — 병원 네이버 블로그 신규 글을 검토 대기 자산으로 인입.
        # 주간 측정(월 02:00)과 겹치지 않게 하루 뒤로 두어 워커 슬롯 경합을 피한다.
        "weekly-naver-source-sync": {
            "task": "app.workers.naver_sync.weekly_naver_source_sync",
            "schedule": crontab(hour=3, minute=0, day_of_week=2),
            "options": {"headers": build_dispatch_headers("weekly-naver-source-sync")},
        },
        # 1분마다 — 무료 진단 폴러. DB가 큐이므로(outbox 없음) 이 tick이 유일한
        # 신뢰 경로다: 접수의 celery publish가 실패해도 60초 안에 회수된다.
        # 좌초 RUNNING 회수·재시도 소진 종결·질의 캐시 만료 삭제도 함께 처리한다.
        "drain-lead-diagnoses": {
            "task": "app.workers.lead_diagnosis_tasks.drain_lead_diagnoses",
            "schedule": crontab(minute="*"),
            "options": {"headers": build_dispatch_headers("drain-lead-diagnoses")},
        },
        # 1분마다 — 커밋된 Slack 의도를 임대해 전송하고 재시도/HOLD 상태를 회수한다.
        "dispatch-notification-outbox": {
            "task": "app.workers.notification_tasks.dispatch_notification_outbox",
            "schedule": crontab(minute="*"),
        },
        # 1분마다 — 리포트 커밋 직후 워커가 종료돼도 누락된 운영 이슈와 알림을 복구한다.
        "reconcile-monthly-artifact-incidents": {
            "task": "app.workers.monthly_artifact_reconciliation.reconcile",
            "schedule": crontab(minute="*"),
        },
        # 1분마다 — 커밋은 됐지만 최초 broker publish가 유실된 허브 준비/캐시 복구를 회수.
        "reconcile-autonomous-workflows": {
            "task": "app.workers.autonomous_recovery.reconcile",
            "schedule": crontab(minute="*"),
            "options": {"headers": build_dispatch_headers("reconcile-autonomous-workflows")},
        },
        # 15분마다 — 완료된 구간의 현재 DB truth를 한 건의 운영 마일스톤 요약으로 투영.
        "project-milestone-events": {
            "task": "app.workers.milestone_event_tasks.project_milestone_events",
            "schedule": crontab(minute="*/15"),
        },
        # 15분마다 — 런타임으로 추가된 모든 병원 자기 도메인의 실제 TLS/Host 응답 확인.
        "live-custom-domain-health": {
            "task": "app.workers.tasks.monitor_live_custom_domains",
            "schedule": crontab(minute="*/15"),
            "options": {"headers": build_dispatch_headers("live-custom-domain-health")},
        },
        "canary-default": {
            "task": "app.workers.canary_tasks.canary_default",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-default")},
        },
        "canary-content": {
            "task": "app.workers.canary_tasks.canary_content",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-content")},
        },
        "canary-sov": {
            "task": "app.workers.canary_tasks.canary_sov",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-sov")},
        },
        "canary-reports": {
            "task": "app.workers.canary_tasks.canary_reports",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-reports")},
        },
        "canary-leadgen": {
            "task": "app.workers.canary_tasks.canary_leadgen",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-leadgen")},
        },
        "canary-certificates": {
            "task": "app.workers.canary_tasks.canary_certificates",
            "schedule": crontab(minute="*/5"),
            "options": {"headers": build_dispatch_headers("canary-certificates")},
        },
    },
)
