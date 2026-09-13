"""외부 파이프라인 감시 — Celery/Beat 없이 자동 운영이 살아 있는지 확인한다.

Beat나 Worker가 죽으면 23:00 생성과 08:00 발행이 조용히 멈추고, 그 사실을 알려 줄
작업 자체도 같은 Beat 위에 있다. 그래서 이 모듈은 Celery 태스크를 쓰지 않는다.
Cloud Scheduler가 API(HTTP)를 직접 깨우고, 여기서 Redis와 DB만 읽어 판정한다.

읽는 근거는 네 가지다.
1. 큐 canary 신선도 (`read_queue_canaries`, Redis) — Worker가 각 큐의 작업을 실제로
   실행했는지.
2. Beat 생존 — RedBeat 분산 락과 canary 스케줄 엔트리의 `last_run_at`.
3. 당일 발행 — KST 오늘 발행 예정 슬롯과 08:00 이후 공개된 글 수.
4. 마지막 야간 생성 배치 OperationRun의 나이.

증명할 수 있는 것과 없는 것을 구분한다. Beat 락은 "누군가 dispatcher 자리를 잡고
있다"는 사실이고, canary `last_run_at`은 "그 dispatcher가 최근에 실제로 스케줄을
돌렸다"는 사실이다. 둘 다 있어야 살아 있다고 말한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx
import redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import _sync_connect_args
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.services.post_publish_review_policy import (
    auto_publish_due_predicate,
    publicly_operational_hospital_predicate,
)
from app.workers.canary_tasks import CANARY_MAX_AGE, EXPECTED_QUEUES, read_queue_canaries

logger = logging.getLogger(__name__)

KST: Final = ZoneInfo("Asia/Seoul")

# 이 두 큐가 멈추면 아침 발행(control)과 야간 생성(content)이 통째로 멈춘다.
# 나머지 큐의 지연은 보고서·리드 등 단일 기능에 국한되므로 보고만 하고 알리지 않는다.
CRITICAL_QUEUES: Final = ("control", "content")

# Beat 생존 판정에 쓰는 스케줄 엔트리 — canary는 5분마다 돌고 부작용이 없다.
BEAT_LIVENESS_SCHEDULES: Final = tuple(f"canary-{queue}" for queue in EXPECTED_QUEUES)
_DEFAULT_REDBEAT_PREFIX: Final = "redbeat:"

# 아침 자동 발행은 08:00에 시작한다. 08:30이면 정상 실행은 이미 끝나 있어야 한다.
PUBLISH_WINDOW_START: Final = time(8, 0)
PUBLISH_CHECK_AFTER: Final = time(8, 30)

# 야간 생성은 매일 23:00에 한 번 시작한다. 26시간이면 하루를 통째로 건너뛴 것이다.
GENERATION_BATCH_OPERATION_TYPE: Final = "NIGHTLY_CONTENT_GENERATION"
GENERATION_BATCH_MAX_AGE: Final = timedelta(hours=26)

CONDITION_QUEUE_STALE: Final = "queue_stale"
CONDITION_BEAT_DOWN: Final = "beat_down"
CONDITION_PUBLISH_MISSING: Final = "publish_missing"

# 알림 정책의 수신자 구분. 순수 인프라 정지는 AE가 고칠 수 없으므로 개발 채널이 받고,
# "오늘 글이 한 건도 안 나갔다"는 고객이 보는 사실이라 운영 채널이 받는다.
AUDIENCE_DEVELOPER: Final = "developer"
AUDIENCE_OPERATOR: Final = "operator"

_KEY_NAMESPACE: Final = "reputation:pipeline-watchdog:v1"
_DEDUPE_TTL_SECONDS: Final = 3600
# 알린 상태를 기억해 두고, 조건이 사라지면 복구 한 건을 보낸다. 하루가 지나도
# 복구 알림이 오지 않았다면 그 인시던트는 이미 사람이 다른 경로로 확인한 것이다.
_STATE_TTL_SECONDS: Final = 24 * 3600


@dataclass(frozen=True, slots=True)
class WatchdogReport:
    """한 번의 외부 점검 결과. 모든 시각은 tz-aware UTC다."""

    observed_at: datetime
    kst_date: str
    redis_available: bool
    database_available: bool
    queue_canaries_current: bool
    stale_queues: tuple[str, ...]
    stale_critical_queues: tuple[str, ...]
    beat_alive: bool
    beat_lock_held: bool
    beat_last_schedule_run_at: datetime | None
    beat_evidence: str
    publish_checked: bool
    publish_due_remaining: int | None
    publish_published_today: int | None
    publish_missing: bool
    publish_partial: bool
    last_generation_batch_at: datetime | None
    generation_batch_stale: bool

    @property
    def critical_conditions(self) -> tuple[str, ...]:
        """Slack 한 건을 만들 근거가 되는 조건 집합(중복 억제 키의 입력)."""
        conditions: list[str] = []
        if self.stale_critical_queues:
            conditions.extend(
                f"{CONDITION_QUEUE_STALE}:{queue}" for queue in self.stale_critical_queues
            )
        if not self.beat_alive:
            conditions.append(CONDITION_BEAT_DOWN)
        if self.publish_missing:
            conditions.append(CONDITION_PUBLISH_MISSING)
        return tuple(sorted(conditions))

    @property
    def healthy(self) -> bool:
        return not self.critical_conditions

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "kst_date": self.kst_date,
            "redis_available": self.redis_available,
            "database_available": self.database_available,
            "queue_canaries_current": self.queue_canaries_current,
            "stale_queues": list(self.stale_queues),
            "stale_critical_queues": list(self.stale_critical_queues),
            "beat_alive": self.beat_alive,
            "beat_lock_held": self.beat_lock_held,
            "beat_last_schedule_run_at": (
                self.beat_last_schedule_run_at.isoformat()
                if self.beat_last_schedule_run_at
                else None
            ),
            "beat_evidence": self.beat_evidence,
            "publish_checked": self.publish_checked,
            "publish_due_remaining": self.publish_due_remaining,
            "publish_published_today": self.publish_published_today,
            "publish_missing": self.publish_missing,
            "publish_partial": self.publish_partial,
            "last_generation_batch_at": (
                self.last_generation_batch_at.isoformat()
                if self.last_generation_batch_at
                else None
            ),
            "generation_batch_stale": self.generation_batch_stale,
            "critical_conditions": list(self.critical_conditions),
            "healthy": self.healthy,
        }


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """수신자별로 보낼지 말지와 그 이유. 전송 자체는 async 경계에서 한다."""

    send: bool
    kind: str | None
    audience: str
    text: str | None
    reason: str
    webhook_url: str | None


# ─── Redis 접근 ────────────────────────────────────────────────────


def utcnow() -> datetime:
    """호출자가 시각을 주입할 수 있도록 한 곳에서만 현재 시각을 읽는다."""
    return datetime.now(UTC)


_watchdog_engine: Engine | None = None


@contextmanager
def watchdog_session() -> Iterator[Session]:
    """감시 전용 sync 세션 — 풀을 상주시키지 않는다(NullPool).

    Cloud SQL 연결 예산(scripts/check_db_connection_budget.py)은 API의 async 풀과
    Worker의 sync 풀만 계산한다. 5분에 한 번 읽는 점검이 API 인스턴스마다 sync 풀을
    붙들고 있으면 그 예산 밖에서 커넥션이 늘어난다. 매 점검마다 열고 닫아 동시
    커넥션을 한 개로 묶는다.
    """
    global _watchdog_engine
    if _watchdog_engine is None:
        _watchdog_engine = create_engine(
            settings.SYNC_DATABASE_URL,
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args=_sync_connect_args(),
        )
    with Session(_watchdog_engine, expire_on_commit=False) as session:
        yield session


def _connect_redis() -> redis.Redis:
    return redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5
    )


def redbeat_key_prefix() -> str:
    value = celery_app.conf.get("redbeat_key_prefix")
    return value if isinstance(value, str) and value else _DEFAULT_REDBEAT_PREFIX


def beat_lock_key() -> str:
    """RedBeat 분산 락 키 — 기본 설정에서 ``redbeat::lock``."""
    return f"{redbeat_key_prefix()}:lock"


def beat_entry_key(schedule_name: str) -> str:
    return f"{redbeat_key_prefix()}{schedule_name}"


def _parse_redbeat_datetime(value: Any) -> datetime | None:
    """RedBeat의 meta JSON에 저장된 ``last_run_at``을 UTC datetime으로 읽는다.

    RedBeatJSONEncoder는 datetime을 ``{"__type__": "datetime", ..., "timezone": <초 오프셋|존 이름>}``
    으로 저장한다. 라이브러리 디코더를 그대로 부르지 않는 이유는, 감시자가 스케줄러
    내부 객체(crontab/rrule)까지 역직렬화할 필요가 없고 손상된 값에 예외를 내면 안 되기
    때문이다.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if not isinstance(value, dict) or value.get("__type__") != "datetime":
        return None
    zone = value.get("timezone", "UTC")
    if isinstance(zone, (int, float)):
        tzinfo: Any = timezone(timedelta(seconds=float(zone)))
    else:
        try:
            tzinfo = ZoneInfo(str(zone))
        except Exception:  # noqa: BLE001 — 손상된 존 이름은 UTC로 읽는다.
            tzinfo = UTC
    try:
        return datetime(
            year=int(value["year"]),
            month=int(value["month"]),
            day=int(value["day"]),
            hour=int(value.get("hour", 0)),
            minute=int(value.get("minute", 0)),
            second=int(value.get("second", 0)),
            microsecond=int(value.get("microsecond", 0)),
            tzinfo=tzinfo,
        ).astimezone(UTC)
    except (KeyError, TypeError, ValueError):
        return None


def _read_beat_facts(client: redis.Redis, *, now: datetime) -> tuple[bool, datetime | None]:
    lock_held = bool(client.exists(beat_lock_key()))
    latest: datetime | None = None
    for name in BEAT_LIVENESS_SCHEDULES:
        raw = client.hget(beat_entry_key(name), "meta")
        if raw is None:
            continue
        try:
            meta = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        parsed = _parse_redbeat_datetime(meta.get("last_run_at"))
        # 미래로 적힌 값은 시계 드리프트나 손상이다 — 생존 근거로 쓰지 않는다.
        if parsed is None or parsed > now + timedelta(minutes=1):
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return lock_held, latest


def _beat_evidence(*, lock_held: bool, last_run: datetime | None, alive: bool) -> str:
    if alive:
        return (
            "예약 실행기가 분산 락을 잡고 있고 5분 주기 점검 작업이 15분 안에 실제로 "
            "실행됐다. 이 두 가지가 증명하는 것은 dispatcher가 살아서 스케줄을 내보내고 "
            "있다는 사실이며, 개별 작업의 성공 여부는 각 작업 기록으로 따로 확인한다."
        )
    if not lock_held:
        return (
            "예약 실행기의 분산 락이 없다. 실행기가 꺼졌거나 Redis에 접근하지 못하는 "
            "상태이며, 이 점검만으로 둘을 구분할 수는 없다."
        )
    if last_run is None:
        return (
            "분산 락은 잡혀 있지만 5분 주기 점검 작업의 최근 실행 기록이 없다. 락을 "
            "쥔 채 스케줄을 내보내지 못하는 상태일 수 있다."
        )
    return (
        "분산 락은 잡혀 있지만 5분 주기 점검 작업이 15분 넘게 실행되지 않았다. "
        "실행기가 멈췄거나 작업을 큐에 넣지 못하는 상태다."
    )


# ─── DB 사실 ───────────────────────────────────────────────────────


def _publish_facts(db: Session, *, now: datetime) -> tuple[int, int]:
    today = now.astimezone(KST).date()
    window_start = datetime.combine(today, PUBLISH_WINDOW_START, tzinfo=KST)
    due = db.scalar(
        select(func.count())
        .select_from(ContentItem)
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .where(
            publicly_operational_hospital_predicate(),
            auto_publish_due_predicate(today),
        )
    )
    published = db.scalar(
        select(func.count())
        .select_from(ContentItem)
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .where(
            publicly_operational_hospital_predicate(),
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.published_at >= window_start,
            ContentItem.published_at <= now,
        )
    )
    return int(due or 0), int(published or 0)


def _last_generation_batch_at(db: Session) -> datetime | None:
    started = db.scalar(
        select(func.max(OperationRun.started_at)).where(
            OperationRun.operation_type == GENERATION_BATCH_OPERATION_TYPE
        )
    )
    if started is None:
        return None
    return started if started.tzinfo else started.replace(tzinfo=UTC)


# ─── 판정 ─────────────────────────────────────────────────────────


def evaluate(db: Session, *, now: datetime) -> WatchdogReport:
    """Celery 없이 파이프라인 생존을 판정한다. Redis·DB 장애는 보고서에 남긴다."""
    if now.tzinfo is None:
        raise ValueError("watchdog evaluate requires a timezone-aware 'now'")
    observed_at = now.astimezone(UTC)
    local_now = observed_at.astimezone(KST)

    canaries = read_queue_canaries(now=observed_at)
    stale_queues = tuple(canaries.missing_or_stale_queues)
    stale_critical = tuple(queue for queue in CRITICAL_QUEUES if queue in stale_queues)

    redis_available = True
    lock_held = False
    last_run: datetime | None = None
    try:
        with _connect_redis() as client:
            lock_held, last_run = _read_beat_facts(client, now=observed_at)
    except RedisError:
        redis_available = False
        logger.error("pipeline watchdog: Redis unavailable while reading beat liveness")
    beat_alive = bool(
        redis_available
        and lock_held
        and last_run is not None
        and timedelta(0) <= observed_at - last_run <= CANARY_MAX_AGE
    )
    evidence = (
        "Redis에 접근하지 못해 예약 실행기 생존을 확인할 수 없다. 실행기가 살아 있어도 "
        "Redis가 없으면 스케줄과 큐가 동작하지 못한다."
        if not redis_available
        else _beat_evidence(lock_held=lock_held, last_run=last_run, alive=beat_alive)
    )

    database_available = True
    due: int | None = None
    published: int | None = None
    last_batch: datetime | None = None
    try:
        due, published = _publish_facts(db, now=observed_at)
        last_batch = _last_generation_batch_at(db)
    except SQLAlchemyError:
        database_available = False
        logger.error("pipeline watchdog: database unavailable while reading publish facts")

    after_check_time = local_now.time() >= PUBLISH_CHECK_AFTER
    publish_checked = bool(database_available and after_check_time)
    publish_missing = bool(publish_checked and (due or 0) > 0 and published == 0)
    # 일부만 나간 상태는 정보다. 남은 슬롯은 예산 안의 자동 복구가 소유하므로
    # 그 자체로는 사람의 일이 아니다.
    publish_partial = bool(publish_checked and (due or 0) > 0 and (published or 0) > 0)
    generation_batch_stale = bool(
        database_available
        and (last_batch is None or observed_at - last_batch > GENERATION_BATCH_MAX_AGE)
    )

    return WatchdogReport(
        observed_at=observed_at,
        kst_date=local_now.date().isoformat(),
        redis_available=redis_available,
        database_available=database_available,
        queue_canaries_current=canaries.current,
        stale_queues=stale_queues,
        stale_critical_queues=stale_critical,
        beat_alive=beat_alive,
        beat_lock_held=lock_held,
        beat_last_schedule_run_at=last_run,
        beat_evidence=evidence,
        publish_checked=publish_checked,
        publish_due_remaining=due,
        publish_published_today=published,
        publish_missing=publish_missing,
        publish_partial=publish_partial,
        last_generation_batch_at=last_batch,
        generation_batch_stale=generation_batch_stale,
    )


# ─── 운영자 문구 ───────────────────────────────────────────────────


def _operations_link() -> str:
    return f"{settings.ADMIN_BASE_URL.rstrip('/')}/operations"


_QUEUE_LABELS: Final = {
    "control": "아침 자동 발행",
    "content": "콘텐츠 생성",
}


def _infra_problem_lines(report: WatchdogReport) -> list[str]:
    lines: list[str] = []
    if not report.beat_alive:
        lines.append(f"• 예약 실행기가 살아 있다는 근거가 없습니다. {report.beat_evidence}")
    for queue in report.stale_critical_queues:
        label = _QUEUE_LABELS.get(queue, "자동 작업")
        lines.append(f"• {label} 대기열이 15분 넘게 작업을 처리한 기록이 없습니다.")
    return lines


def _context_lines(report: WatchdogReport) -> list[str]:
    lines: list[str] = []
    if report.publish_partial and not report.publish_missing:
        lines.append(
            f"• 참고: 오늘 {report.publish_published_today}건이 공개됐고 "
            f"{report.publish_due_remaining}건이 남아 있습니다."
        )
    if report.generation_batch_stale:
        lines.append("• 참고: 어젯밤 콘텐츠 생성 배치가 시작된 기록이 26시간 넘게 없습니다.")
    if report.stale_queues and not report.stale_critical_queues:
        lines.append("• 참고: 일부 보조 대기열의 최근 처리 기록이 없습니다.")
    return lines


def _developer_alert_text(report: WatchdogReport) -> str:
    body = [
        "자동 운영 실행기가 멈춰 있습니다.",
        "",
        "무슨 문제인지",
        *_infra_problem_lines(report),
        *_context_lines(report),
        "",
        "고객 영향",
        "밤사이 콘텐츠 생성과 아침 자동 발행이 실행되지 않습니다. 이 상태가 이어지면 "
        "오늘 예정된 글이 공개되지 않아 병원 공개 화면과 검색 노출이 어제 상태로 멈춥니다.",
        "",
        "지금 할 일",
        "작업 실행 서비스와 예약 실행기가 살아 있는지 확인하고, 필요하면 다시 시작한 뒤 "
        "10분 안에 이 점검이 정상으로 돌아오는지 확인해 주세요.",
        _operations_link(),
    ]
    return "\n".join(body)


def _operator_alert_text(report: WatchdogReport) -> str:
    body = [
        "오늘 예정된 글이 아직 한 건도 공개되지 않았습니다.",
        "",
        "무슨 문제인지",
        f"• 오늘 발행 예정 {report.publish_due_remaining}건 가운데 08:30까지 공개된 글이 "
        "한 건도 없습니다. 아침 자동 발행이 실행되지 않은 상태입니다.",
        *_context_lines(report),
        "",
        "고객 영향",
        "오늘 병원 공개 화면에 새 글이 올라가지 않습니다. 이 상태로 하루가 지나면 그날의 "
        "계약 분량이 밀립니다.",
        "",
        "지금 할 일",
        "운영센터에서 오늘 발행 큐를 확인해 주세요. 자동 복구가 진행 중인 항목은 그대로 두고, "
        "조치가 필요한 항목만 처리하면 됩니다. 개발팀에도 같은 시각의 실행기 점검 결과가 갑니다.",
        _operations_link(),
    ]
    return "\n".join(body)


def build_alert_text(report: WatchdogReport, audience: str) -> str:
    """무슨 문제인지 → 고객 영향 → 지금 할 일 순서의 수신자별 문구."""
    if audience == AUDIENCE_OPERATOR:
        return _operator_alert_text(report)
    return _developer_alert_text(report)


def build_recovery_text(report: WatchdogReport, audience: str) -> str:
    if audience == AUDIENCE_OPERATOR:
        published = (
            f"오늘 공개된 글은 {report.publish_published_today}건이고 "
            f"{report.publish_due_remaining}건이 남아 있습니다."
            if report.publish_checked
            else "오늘 발행 결과는 운영센터에서 확인할 수 있습니다."
        )
        lines = [
            "오늘 예정된 글이 다시 공개되고 있습니다.",
            "",
            "무슨 문제인지",
            "앞서 알린 아침 발행 정지가 지금 점검에서는 사라졌습니다.",
            "",
            "고객 영향",
            published,
            "",
            "지금 할 일",
            "추가 조치는 필요하지 않습니다. 남은 슬롯은 자동 복구가 이어서 처리합니다.",
            _operations_link(),
        ]
        return "\n".join(lines)
    lines = [
        "자동 운영 실행기가 정상으로 돌아왔습니다.",
        "",
        "무슨 문제인지",
        "앞서 알린 예약 실행기·대기열 문제가 지금 점검에서는 모두 사라졌습니다.",
        "",
        "고객 영향",
        "생성과 발행이 다시 자동으로 진행됩니다. 멈춰 있던 동안 밀린 글은 자동 복구가 "
        "이어서 처리합니다.",
        "",
        "지금 할 일",
        "추가 조치는 필요하지 않습니다. 오늘 발행 결과만 참고로 확인해 주세요.",
        _operations_link(),
    ]
    return "\n".join(lines)


# ─── 채널 라우팅 ───────────────────────────────────────────────────


def conditions_for(report: WatchdogReport, audience: str) -> tuple[str, ...]:
    """수신자별로 소유하는 조건만 남긴다 — 같은 사실을 두 채널이 중복 소유하지 않는다."""
    if audience == AUDIENCE_OPERATOR:
        return tuple(
            item for item in report.critical_conditions if item == CONDITION_PUBLISH_MISSING
        )
    return tuple(item for item in report.critical_conditions if item != CONDITION_PUBLISH_MISSING)


def webhook_for(audience: str) -> str:
    """알림 정책의 수신 채널. 감시만의 예외로 개발 채널이 없으면 운영 채널로 내린다.

    일반 인시던트는 `SLACK_WEBHOOK_URL_DEV`가 비어 있으면 outbox에 HOLD로 남기고 운영
    채널로 폴백하지 않는다. 이 부품은 그 HOLD를 처리할 Worker가 죽었을 때를 위해
    존재하므로, 같은 규칙을 적용하면 막으려던 침묵을 그대로 만든다.
    """
    if audience == AUDIENCE_DEVELOPER:
        developer = settings.SLACK_WEBHOOK_URL_DEV.strip()
        if developer:
            return developer
    return settings.SLACK_WEBHOOK_URL


# ─── 중복 억제 ─────────────────────────────────────────────────────


def _signature(conditions: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join(conditions).encode("utf-8")).hexdigest()[:16]


def _dedupe_key(kind: str, audience: str, signature: str, *, now: datetime) -> str:
    hour = now.astimezone(KST).strftime("%Y%m%dT%H")
    return f"{_KEY_NAMESPACE}:{kind}:{audience}:{signature}:{hour}"


def _state_key(audience: str) -> str:
    return f"{_KEY_NAMESPACE}:active:{audience}"


def _fail_open(report: WatchdogReport, audience: str) -> AlertDecision:
    conditions = conditions_for(report, audience)
    if conditions:
        return AlertDecision(
            True,
            "ALERT",
            audience,
            build_alert_text(report, audience),
            "redis_unavailable",
            webhook_for(audience),
        )
    return AlertDecision(False, None, audience, None, "redis_unavailable_no_state", None)


def _decide_one(
    report: WatchdogReport, audience: str, *, now: datetime, client: redis.Redis
) -> AlertDecision:
    conditions = conditions_for(report, audience)
    webhook = webhook_for(audience)
    try:
        if conditions:
            claimed = bool(
                client.set(
                    _dedupe_key("alert", audience, _signature(conditions), now=now),
                    "1",
                    nx=True,
                    ex=_DEDUPE_TTL_SECONDS,
                )
            )
            client.set(
                _state_key(audience),
                json.dumps(list(conditions), ensure_ascii=False),
                ex=_STATE_TTL_SECONDS,
            )
            if not claimed:
                return AlertDecision(False, None, audience, None, "deduped", None)
            return AlertDecision(
                True,
                "ALERT",
                audience,
                build_alert_text(report, audience),
                "new_condition_set",
                webhook,
            )
        previous = client.get(_state_key(audience))
        if previous is None:
            return AlertDecision(False, None, audience, None, "healthy", None)
        claimed = bool(
            client.set(
                _dedupe_key("recovery", audience, _previous_signature(previous), now=now),
                "1",
                nx=True,
                ex=_DEDUPE_TTL_SECONDS,
            )
        )
        client.delete(_state_key(audience))
        if not claimed:
            return AlertDecision(False, None, audience, None, "deduped", None)
        return AlertDecision(
            True,
            "RECOVERY",
            audience,
            build_recovery_text(report, audience),
            "recovered",
            webhook,
        )
    except RedisError:
        return _fail_open(report, audience)


def decide_alerts(report: WatchdogReport, *, now: datetime) -> tuple[AlertDecision, ...]:
    """수신자별로 보낼지 정한다. Redis 장애 시 알림은 fail-open으로 보낸다.

    복구는 "앞서 알린 사실"이 있어야 성립하므로, 그 기억을 잃은 경우(Redis 장애)에는
    보내지 않는다. 아무도 알림을 받은 적 없는 복구 메시지는 소음일 뿐이다.
    """
    audiences = (AUDIENCE_DEVELOPER, AUDIENCE_OPERATOR)
    try:
        with _connect_redis() as client:
            return tuple(
                _decide_one(report, audience, now=now, client=client) for audience in audiences
            )
    except RedisError:
        return tuple(_fail_open(report, audience) for audience in audiences)


def _previous_signature(raw: Any) -> str:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _signature(())
    try:
        stored = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _signature(())
    if not isinstance(stored, list):
        return _signature(())
    return _signature(tuple(sorted(str(item) for item in stored)))


# ─── 전송 ─────────────────────────────────────────────────────────


async def deliver(decision: AlertDecision) -> bool:
    """webhook으로 직접 보낸다 — outbox drain은 Worker가 살아 있어야 돈다.

    전송 실패는 로그로만 남긴다. 다음 하트비트(5분)가 같은 사실을 다시 판정하며,
    중복 억제 키는 KST 시간 단위라 그때 다시 보낼 수 있다.
    """
    if not (decision.send and decision.text and decision.webhook_url):
        return False
    # SSRF 가드는 기존 알림 경로와 같은 허용 목록을 쓴다.
    from app.services.notifier import _is_allowed_webhook

    if not _is_allowed_webhook(decision.webhook_url):
        logger.error(
            "pipeline watchdog: webhook rejected by allowlist audience=%s", decision.audience
        )
        return False
    payload = {"text": decision.text}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(decision.webhook_url, json=payload)
                response.raise_for_status()
                return True
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if (status == 429 or status >= 500) and attempt == 0:
                continue
            logger.error("pipeline watchdog: Slack delivery failed status=%s", status)
            return False
        except httpx.HTTPError as exc:
            if attempt == 0:
                continue
            logger.error("pipeline watchdog: Slack delivery failed: %s", exc.__class__.__name__)
            return False
    return False


async def deliver_all(decisions: tuple[AlertDecision, ...]) -> tuple[bool, ...]:
    return tuple([await deliver(decision) for decision in decisions])
