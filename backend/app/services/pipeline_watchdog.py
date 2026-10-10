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
from typing import Any, Final, Literal
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
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.services.notification_labels import label_for_event, prefixed
from app.services.post_publish_review_policy import (
    AUTO_PUBLISH_BLOCKED_ACTION,
    auto_publish_due_predicate,
    publicly_operational_hospital_predicate,
)
from app.workers.canary_tasks import CANARY_MAX_AGE, EXPECTED_QUEUES, read_queue_canaries
from app.workers.generation_incident_control import generation_safe_cause

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
# 오늘 공개가 0건인 같은 사실을 두 원인으로 나눈다. 발행기가 한 건도 열어 보지 못한
# 것(`publish_missing`)과, 열어 보고 공개 직전 안전검사에서 되돌린 것
# (`publish_gate_residual`)은 사람이 할 일이 완전히 다르다.
CONDITION_PUBLISH_MISSING: Final = "publish_missing"
# 게이트 잔여는 판정·API에만 남기고 알리지 않는다. 발행기는 실행됐고 안전검사가 막은
# 것이라 파이프라인 생존 문제가 아니다 — 그 보류는 07:45·08:00 차단 요약, 원인별 인시던트,
# 18:00 일일 요약이 이미 소유한다(2026-10-10 같은 경보가 매시 다섯 번 나갔다).
CONDITION_PUBLISH_GATE_RESIDUAL: Final = "publish_gate_residual"

# 알림 정책의 수신자 구분. 순수 인프라 정지는 AE가 고칠 수 없으므로 개발 채널이 받고,
# "오늘 글이 한 건도 안 나갔다"는 고객이 보는 사실이라 운영 채널이 받는다.
AUDIENCE_DEVELOPER: Final = "developer"
AUDIENCE_OPERATOR: Final = "operator"
# "발행기가 오늘 글을 한 건도 열어 보지 못했다"는 고객이 보는 사실이라 운영 채널이 소유한다.
_OPERATOR_CONDITIONS: Final = frozenset({CONDITION_PUBLISH_MISSING})

# 발행기가 오늘 실제로 게이트를 돌렸다는 증거를 읽는 폭. 5개 병원 × 시간당 한 번이라
# 하루치가 이 안에 들어오고, 손상된 하루에도 쿼리가 폭주하지 않는다.
_BLOCK_AUDIT_SCAN_LIMIT: Final = 500
# Slack·API 한 건에 담는 차단 표본. 나머지는 개수로만 말한다.
PUBLISH_BLOCK_SAMPLE_LIMIT: Final = 20

_KEY_NAMESPACE: Final = "reputation:pipeline-watchdog:v1"
# 활성 에피소드와 인프라 관측을 기억한다. 하트비트(5분)마다 갱신되므로 조건이 이어지는
# 동안에는 만료되지 않는다. 하트비트가 하루 넘게 끊겼다면 그 기억은 버려도 된다.
_STATE_TTL_SECONDS: Final = 24 * 3600
# 동시에 도착한 두 호출의 같은 전이 전송을 막는 잠금. 하트비트 간격(5분)보다 짧아야
# 다음 하트비트의 재시도를 막지 않는다.
_SEND_LOCK_SECONDS: Final = 240
# 전달이 확인되지 않은 ALERT의 재시도 간격 — 매 하트비트가 아니라 15분에 한 번.
_RETRY_INTERVAL: Final = timedelta(minutes=15)
# 인프라 조건(Beat 정지·핵심 큐 정지)의 히스테리시스. 배포 중 Beat 재시작은 RedBeat 락을
# 1~3분 비우고, Worker 교체는 canary 한 주기를 놓칠 수 있다. 4분 이상 떨어진 연속 두
# 하트비트에서 같은 사실을 봐야 알리고, 같은 간격의 연속 두 정상 하트비트가 있어야 복구로 본다.
_CONFIRM_MIN_GAP: Final = timedelta(minutes=4)
# 대기 중인 관측이 이보다 오래되면(하트비트가 끊겼던 경우) 연속 관측으로 보지 않는다.
_OBSERVATION_MAX_AGE: Final = timedelta(minutes=30)
# Redis 장애 시 fail-open 전송을 허용하는 KST 분(0~4분) — 시간당 한 번.
_FAIL_OPEN_MINUTE_LIMIT: Final = 5
# KST 하루 단위로 성립하는 조건. 날짜가 바뀌면 조용히 사라지며 복구로 알리지 않는다.
_DAY_SCOPED_CONDITIONS: Final = frozenset({CONDITION_PUBLISH_MISSING})
# 예전에 알렸지만 더는 알리지 않는 조건. 이전 배포가 남긴 기억에서 조용히 지운다.
_RETIRED_CONDITIONS: Final = frozenset({CONDITION_PUBLISH_GATE_RESIDUAL})


@dataclass(frozen=True, slots=True)
class PublishBlockFact:
    """오늘 자동 발행이 한 글을 게이트에서 되돌린 사실 한 건."""

    hospital_id: str | None
    hospital_name: str
    content_id: str
    code: str
    reason: str
    scheduled_date: str | None
    observed_at: datetime

    @property
    def safe_cause(self) -> str:
        """운영자 문구용 한국어 원인. 내부 코드는 Slack에 넣지 않는다."""
        return generation_safe_cause(self.code)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hospital_id": self.hospital_id,
            "hospital_name": self.hospital_name,
            "content_id": self.content_id,
            "code": self.code,
            "reason": self.reason,
            "scheduled_date": self.scheduled_date,
            "observed_at": self.observed_at.isoformat(),
        }


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
    publish_gate_residual: bool
    publish_blocked_today: tuple[PublishBlockFact, ...]
    publish_partial: bool
    last_generation_batch_at: datetime | None
    generation_batch_stale: bool

    @property
    def publish_block_reasons(self) -> tuple[tuple[str, int], ...]:
        """오늘 관측된 차단 코드와 글 수 — 많은 순, 같으면 코드 순."""
        counts: dict[str, int] = {}
        for fact in self.publish_blocked_today:
            counts[fact.code] = counts.get(fact.code, 0) + 1
        return tuple(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))

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
        # `publish_gate_residual`은 알림 근거가 아니다(위 CONDITION_PUBLISH_GATE_RESIDUAL 주석).
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
            "publish_gate_residual": self.publish_gate_residual,
            "publish_blocked_today": [fact.as_dict() for fact in self.publish_blocked_today],
            "publish_block_reasons": [
                {"code": code, "count": count} for code, count in self.publish_block_reasons
            ],
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
    # 이 결정이 가리키는 에피소드의 조건 집합 — 전달 확인을 같은 에피소드에만 기록한다.
    conditions: tuple[str, ...] = ()


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


def _publish_block_facts(db: Session, *, now: datetime) -> tuple[PublishBlockFact, ...]:
    """오늘 자동 발행이 게이트에서 되돌린 글을 글 단위로 최신 1건씩 읽는다.

    이 감사 기록은 발행기만 쓴다. 그러므로 08:00 이후에 한 건이라도 있으면 "발행기가
    실행되지 않았다"는 판정은 거짓이다 — 실행됐고, 공개 직전 검사가 막은 것이다.
    발행기는 정상 차단에 Slack을 보내지 않으므로(코드별 소유자가 주간 롤업이거나
    아예 없다) 이 조회가 운영자가 원인을 볼 수 있는 유일한 외부 경로다.
    """

    today = now.astimezone(KST).date()
    window_start = datetime.combine(today, PUBLISH_WINDOW_START, tzinfo=KST)
    rows = db.execute(
        select(
            AdminAuditLog.hospital_id,
            AdminAuditLog.target_id,
            AdminAuditLog.detail,
            AdminAuditLog.created_at,
            Hospital.name,
        )
        .join(Hospital, Hospital.id == AdminAuditLog.hospital_id, isouter=True)
        .where(
            AdminAuditLog.action == AUTO_PUBLISH_BLOCKED_ACTION,
            AdminAuditLog.created_at >= window_start,
            AdminAuditLog.created_at <= now,
        )
        .order_by(AdminAuditLog.created_at.desc())
        .limit(_BLOCK_AUDIT_SCAN_LIMIT)
    ).all()

    latest: dict[str, PublishBlockFact] = {}
    for hospital_id, target_id, detail, created_at, hospital_name in rows:
        content_id = str(target_id or "")
        # 같은 글은 시간마다 다시 막힌다. 가장 최근 판정 하나만 사실로 삼는다.
        if not content_id or content_id in latest:
            continue
        facts = detail if isinstance(detail, dict) else {}
        observed = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        latest[content_id] = PublishBlockFact(
            hospital_id=str(hospital_id) if hospital_id else None,
            hospital_name=str(hospital_name or "이름 미상 병원"),
            content_id=content_id,
            code=str(facts.get("code") or "UNKNOWN"),
            reason=str(facts.get("reason") or ""),
            scheduled_date=(
                str(facts["scheduled_date"]) if facts.get("scheduled_date") else None
            ),
            observed_at=observed.astimezone(UTC),
        )
    return tuple(list(latest.values())[:PUBLISH_BLOCK_SAMPLE_LIMIT])


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
    blocked: tuple[PublishBlockFact, ...] = ()
    last_batch: datetime | None = None
    try:
        due, published = _publish_facts(db, now=observed_at)
        blocked = _publish_block_facts(db, now=observed_at)
        last_batch = _last_generation_batch_at(db)
    except SQLAlchemyError:
        database_available = False
        logger.error("pipeline watchdog: database unavailable while reading publish facts")

    after_check_time = local_now.time() >= PUBLISH_CHECK_AFTER
    publish_checked = bool(database_available and after_check_time)
    # 오늘 아무것도 공개되지 않은 같은 사실을, 발행기가 이 글들을 열어 봤다는 증거가
    # 있는지로 나눈다. 증거가 있으면 "실행되지 않음"은 거짓 진단이다.
    publish_none_today = bool(publish_checked and (due or 0) > 0 and published == 0)
    publish_gate_residual = bool(publish_none_today and blocked)
    publish_missing = bool(publish_none_today and not blocked)
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
        publish_gate_residual=publish_gate_residual,
        publish_blocked_today=blocked,
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
        "한 건도 없습니다. 아침 자동 발행이 이 글들을 한 건도 열어 보지 못한 상태입니다.",
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
    label = label_for_event("PIPELINE_WATCHDOG_ALERT")
    if audience == AUDIENCE_OPERATOR:
        return prefixed(label, _operator_alert_text(report))
    return prefixed(label, _developer_alert_text(report))


def build_recovery_text(report: WatchdogReport, audience: str) -> str:
    return prefixed(
        label_for_event("PIPELINE_WATCHDOG_RECOVERY"), _recovery_text(report, audience)
    )


def _recovery_text(report: WatchdogReport, audience: str) -> str:
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
            item for item in report.critical_conditions if item in _OPERATOR_CONDITIONS
        )
    return tuple(
        item for item in report.critical_conditions if item not in _OPERATOR_CONDITIONS
    )


def webhook_for(audience: str) -> str:
    """알림 정책의 수신 채널. 개발 채널이 없으면 운영 채널 하나로 운영한다.

    일반 알림(outbox)도 `SLACK_WEBHOOK_URL_DEV`가 비어 있으면 운영 채널로 `[개발 확인]`
    표시와 함께 간다. 개발 웹훅이 설정돼 있는데 실패하면 `deliver`가 운영 채널로 대신 보낸다.
    """
    if audience == AUDIENCE_DEVELOPER:
        developer = settings.SLACK_WEBHOOK_URL_DEV.strip()
        if developer:
            return developer
    return settings.SLACK_WEBHOOK_URL


# ─── 에피소드 ─────────────────────────────────────────────────────
#
# 한 수신자에게 "같은 원인 집합"은 한 번만 알린다. 2026-10-10 운영 채널에 같은 발행 경보가
# 08:30부터 매시 다섯 번 왔다 — 중복 억제 키에 KST 시간이 들어 있었기 때문이다. 이제
# 수신자별 활성 에피소드를 Redis에 두고, 원인 집합에 새 조건이 생길 때만 다시 알린다.
#
#   확정 조건 집합 C(인프라 조건은 아래 히스테리시스를 거친다)
#   C에 저장된 에피소드에 없는 조건이 있다            → ALERT, 새 에피소드(delivered=false)
#   같은 에피소드인데 아직 전달 확인이 없다            → 15분에 한 번 ALERT 재시도
#   C가 줄기만 했다                                    → 조용히 에피소드를 줄인다
#   C가 비었다(확정 대기 관측도 없음) + 전달된 에피소드 → RECOVERY 한 번, 에피소드 삭제
#   전달된 적 없는 에피소드가 사라졌다                 → 조용히 삭제
#   하루 단위 조건(당일 발행 0건)의 날짜가 지났다      → 그 조건만 조용히 버린다(복구 아님)


def _signature(conditions: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join(conditions).encode("utf-8")).hexdigest()[:16]


def _state_key(audience: str) -> str:
    return f"{_KEY_NAMESPACE}:active:{audience}"


def _observation_key() -> str:
    return f"{_KEY_NAMESPACE}:observations"


def _send_lock_key(kind: str, audience: str, signature: str) -> str:
    return f"{_KEY_NAMESPACE}:send:{kind}:{audience}:{signature}"


def _is_infra_condition(condition: str) -> bool:
    return condition == CONDITION_BEAT_DOWN or condition.startswith(f"{CONDITION_QUEUE_STALE}:")


def _owned_by(condition: str, audience: str) -> bool:
    if audience == AUDIENCE_OPERATOR:
        return condition in _OPERATOR_CONDITIONS
    return condition not in _OPERATOR_CONDITIONS


def _decode_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Observations:
    """인프라 조건의 확정·확정 대기·해제 대기 관측. 시각은 tz-aware UTC다."""

    confirmed: frozenset[str]
    pending: dict[str, datetime]
    clearing: dict[str, datetime]

    def as_json(self) -> str:
        return json.dumps(
            {
                "confirmed": sorted(self.confirmed),
                "pending": {key: value.isoformat() for key, value in sorted(self.pending.items())},
                "clearing": {
                    key: value.isoformat() for key, value in sorted(self.clearing.items())
                },
            },
            ensure_ascii=False,
        )


def _load_observations(client: redis.Redis) -> _Observations:
    stored = _decode_json(client.get(_observation_key()))
    if not isinstance(stored, dict):
        return _Observations(frozenset(), {}, {})

    def times(name: str) -> dict[str, datetime]:
        raw = stored.get(name)
        if not isinstance(raw, dict):
            return {}
        parsed = {str(key): _parse_time(value) for key, value in raw.items()}
        return {key: value for key, value in parsed.items() if value is not None}

    confirmed = stored.get("confirmed")
    return _Observations(
        frozenset(str(item) for item in confirmed) if isinstance(confirmed, list) else frozenset(),
        times("pending"),
        times("clearing"),
    )


def _fresh_since(first: datetime | None, *, now: datetime) -> datetime | None:
    """하트비트가 오래 비어 있었다면 연속 관측이 아니다 — 처음부터 다시 센다."""
    if first is None or now - first > _OBSERVATION_MAX_AGE:
        return None
    return first


def _advance_observations(
    previous: _Observations, raw: frozenset[str], *, now: datetime
) -> _Observations:
    """인프라 조건 하나하나를 히스테리시스로 확정하거나 해제한다.

    조건은 4분 이상 떨어진 연속 두 하트비트에서 보여야 확정되고, 4분 이상 떨어진 연속
    두 정상 하트비트가 있어야 해제된다. 배포 중 Beat 재시작은 1~3분 동안 RedBeat 락을
    비우므로 한 번의 관측으로 알리면 배포마다 '멈춤 → 복구' 쌍이 나간다(2026-10-09
    14:30~15:45). 08:30 발행 확인 job은 같은 분의 하트비트와 몇 초 차이로 겹치므로 4분
    간격이 그 두 호출을 한 관측으로 묶는다.
    """

    confirmed = set(previous.confirmed)
    pending: dict[str, datetime] = {}
    clearing: dict[str, datetime] = {}
    for condition in sorted(raw | previous.confirmed):
        if condition in raw:
            if condition in confirmed:
                continue
            first = _fresh_since(previous.pending.get(condition), now=now)
            if first is not None and now - first >= _CONFIRM_MIN_GAP:
                confirmed.add(condition)
            else:
                pending[condition] = first or now
            continue
        first_healthy = _fresh_since(previous.clearing.get(condition), now=now)
        if first_healthy is not None and now - first_healthy >= _CONFIRM_MIN_GAP:
            confirmed.discard(condition)
        else:
            clearing[condition] = first_healthy or now
    return _Observations(frozenset(confirmed), pending, clearing)


@dataclass(frozen=True, slots=True)
class _Episode:
    conditions: tuple[str, ...]
    kst_date: str
    opened_at: str
    delivered: bool
    last_attempt_at: datetime | None

    def as_json(self) -> str:
        return json.dumps(
            {
                "conditions": list(self.conditions),
                "kst_date": self.kst_date,
                "opened_at": self.opened_at,
                "delivered": self.delivered,
                "last_attempt_at": (
                    self.last_attempt_at.isoformat() if self.last_attempt_at else None
                ),
            },
            ensure_ascii=False,
        )


def _stored_conditions(values: list[Any]) -> tuple[str, ...]:
    # 더는 알리지 않는 조건(게이트 잔여)은 기억에서도 조용히 뺀다 — 남겨 두면 배포 직후
    # '조건이 사라졌다'로 읽혀 아무도 기다리지 않은 복구가 나간다.
    return tuple(sorted({str(item) for item in values} - _RETIRED_CONDITIONS))


def _load_episode(client: redis.Redis, audience: str) -> _Episode | None:
    stored = _decode_json(client.get(_state_key(audience)))
    if isinstance(stored, list):
        # 이전 형식(조건 목록만 저장) — 이미 알린 에피소드로 읽어 배포 직후 같은 조건을
        # 다시 알리지 않는다. 날짜가 없으므로 하루 단위 조건은 다음 판정에서 버려진다.
        conditions = _stored_conditions(stored)
        return _Episode(conditions, "", "", True, None) if conditions else None
    if not isinstance(stored, dict) or not isinstance(stored.get("conditions"), list):
        return None
    conditions = _stored_conditions(stored["conditions"])
    if not conditions:
        return None
    return _Episode(
        conditions=conditions,
        kst_date=str(stored.get("kst_date") or ""),
        opened_at=str(stored.get("opened_at") or ""),
        delivered=bool(stored.get("delivered")),
        last_attempt_at=_parse_time(stored.get("last_attempt_at")),
    )


def _save_episode(client: redis.Redis, audience: str, episode: _Episode) -> None:
    client.set(_state_key(audience), episode.as_json(), ex=_STATE_TTL_SECONDS)


def _claim_send(
    client: redis.Redis, kind: str, audience: str, conditions: tuple[str, ...]
) -> bool:
    """동시에 도착한 두 호출(하트비트와 08:30 발행 확인)이 같은 전이를 두 번 보내지 않게 한다."""
    return bool(
        client.set(
            _send_lock_key(kind, audience, _signature(conditions)),
            "1",
            nx=True,
            ex=_SEND_LOCK_SECONDS,
        )
    )


def _silent(audience: str, reason: str) -> AlertDecision:
    return AlertDecision(False, None, audience, None, reason, None)


def _alert(report: WatchdogReport, audience: str, reason: str, conditions: tuple[str, ...]) -> AlertDecision:
    return AlertDecision(
        True,
        "ALERT",
        audience,
        build_alert_text(report, audience),
        reason,
        webhook_for(audience),
        conditions,
    )


def _fail_open(report: WatchdogReport, audience: str, *, now: datetime) -> AlertDecision:
    """Redis 없이 보낼지 정한다 — 기억이 없으므로 KST 매시 첫 하트비트에만 보낸다.

    Redis가 죽으면 Beat 락도 읽지 못해 `beat_down`이 계속 성립한다. 기억 없이 매 하트비트
    (5분)마다 보내면 시간당 12건이 된다. Cloud Scheduler 하트비트는 매시 :00, :05, … 에
    돌므로 '분 < 5' 조건은 결정적으로 시간당 한 번만 참이다. 히스테리시스도 기억이
    필요하므로 이 경로는 관측된 조건을 그대로 쓴다.
    """
    conditions = conditions_for(report, audience)
    if not conditions:
        return _silent(audience, "redis_unavailable_no_state")
    if now.astimezone(KST).minute >= _FAIL_OPEN_MINUTE_LIMIT:
        return _silent(audience, "redis_unavailable_throttled")
    return _alert(report, audience, "redis_unavailable", conditions)


def _confirmed_for(
    report: WatchdogReport, audience: str, observations: _Observations
) -> tuple[tuple[str, ...], bool]:
    """수신자의 확정 조건 집합과, 아직 확정을 기다리는 인프라 관측이 있는지."""
    direct = {item for item in conditions_for(report, audience) if not _is_infra_condition(item)}
    infra = {item for item in observations.confirmed if _owned_by(item, audience)}
    waiting = any(_owned_by(item, audience) for item in observations.pending)
    return tuple(sorted(direct | infra)), waiting


def _drop_stale_day_conditions(
    client: redis.Redis, audience: str, stored: _Episode | None, kst_date: str
) -> _Episode | None:
    """하루 단위 조건은 그날의 사실이다. 날짜가 바뀌어 사라진 것은 복구가 아니다."""
    if stored is None or stored.kst_date == kst_date:
        return stored
    kept = tuple(item for item in stored.conditions if item not in _DAY_SCOPED_CONDITIONS)
    if not kept:
        client.delete(_state_key(audience))
        return None
    return _Episode(kept, stored.kst_date, stored.opened_at, stored.delivered, stored.last_attempt_at)


def _decide_one(
    report: WatchdogReport,
    audience: str,
    *,
    now: datetime,
    client: redis.Redis,
    observations: _Observations,
) -> AlertDecision:
    current, waiting = _confirmed_for(report, audience, observations)
    stored = _drop_stale_day_conditions(
        client, audience, _load_episode(client, audience), report.kst_date
    )

    if current:
        if stored is None or not set(current) <= set(stored.conditions):
            if not _claim_send(client, "alert", audience, current):
                return _silent(audience, "deduped")
            _save_episode(
                client, audience, _Episode(current, report.kst_date, now.isoformat(), False, now)
            )
            return _alert(report, audience, "new_condition_set", current)
        retry_due = not stored.delivered and (
            stored.last_attempt_at is None or now - stored.last_attempt_at >= _RETRY_INTERVAL
        )
        if retry_due and _claim_send(client, "retry", audience, current):
            _save_episode(
                client, audience, _Episode(current, report.kst_date, stored.opened_at, False, now)
            )
            return _alert(report, audience, "retry_undelivered", current)
        # 같은 에피소드(또는 줄어든 집합) — 날짜와 TTL만 갱신하고 조용히 둔다.
        _save_episode(
            client,
            audience,
            _Episode(
                current, report.kst_date, stored.opened_at, stored.delivered, stored.last_attempt_at
            ),
        )
        return _silent(audience, "deduped" if stored.delivered else "delivery_pending")

    if stored is None:
        return _silent(audience, "healthy")
    if waiting:
        # 새 조건이 확정을 기다리는 동안에는 복구를 말하지 않는다 — 곧 다시 알릴 수 있다.
        _save_episode(client, audience, stored)
        return _silent(audience, "awaiting_confirmation")
    client.delete(_state_key(audience))
    if not stored.delivered:
        return _silent(audience, "recovered_before_delivery")
    if not _claim_send(client, "recovery", audience, stored.conditions):
        return _silent(audience, "deduped")
    return AlertDecision(
        True,
        "RECOVERY",
        audience,
        build_recovery_text(report, audience),
        "recovered",
        webhook_for(audience),
        stored.conditions,
    )


def decide_alerts(report: WatchdogReport, *, now: datetime) -> tuple[AlertDecision, ...]:
    """수신자별로 보낼지 정한다. Redis 장애 시 알림은 시간당 한 번 fail-open으로 보낸다.

    복구는 "앞서 알린 사실"이 있어야 성립하므로, 그 기억을 잃은 경우(Redis 장애)에는
    보내지 않는다. 아무도 알림을 받은 적 없는 복구 메시지는 소음일 뿐이다.
    """
    audiences = (AUDIENCE_DEVELOPER, AUDIENCE_OPERATOR)
    observed = now.astimezone(UTC)
    try:
        with _connect_redis() as client:
            raw_infra = frozenset(
                item for item in report.critical_conditions if _is_infra_condition(item)
            )
            observations = _advance_observations(
                _load_observations(client), raw_infra, now=observed
            )
            client.set(_observation_key(), observations.as_json(), ex=_STATE_TTL_SECONDS)
            return tuple(
                _decide_one(
                    report, audience, now=observed, client=client, observations=observations
                )
                for audience in audiences
            )
    except RedisError:
        return tuple(_fail_open(report, audience, now=observed) for audience in audiences)


def record_deliveries(
    decisions: tuple[AlertDecision, ...], delivered: tuple[bool, ...]
) -> None:
    """전달이 확인된(2xx) ALERT만 에피소드를 delivered로 바꾼다.

    실패한 ALERT는 delivered=false로 남아 다음 하트비트부터 15분에 한 번 다시 보낸다.
    Redis가 없으면 기억할 곳이 없으므로 넘어간다(fail-open 경로가 시간당 한 번 보낸다).
    """
    confirmed = [
        decision
        for decision, ok in zip(decisions, delivered, strict=True)
        if ok and decision.send and decision.kind == "ALERT" and decision.conditions
    ]
    if not confirmed:
        return
    try:
        with _connect_redis() as client:
            for decision in confirmed:
                stored = _load_episode(client, decision.audience)
                if stored is None or stored.delivered:
                    continue
                if stored.conditions != tuple(sorted(decision.conditions)):
                    # 그사이 다른 에피소드가 시작됐다 — 그 에피소드의 전달은 따로 확인한다.
                    continue
                _save_episode(
                    client,
                    decision.audience,
                    _Episode(
                        stored.conditions, stored.kst_date, stored.opened_at, True,
                        stored.last_attempt_at,
                    ),
                )
    except RedisError:
        logger.warning("pipeline watchdog: Redis unavailable while recording delivery")


# ─── 전송 ─────────────────────────────────────────────────────────


async def deliver(decision: AlertDecision) -> bool:
    """webhook으로 직접 보낸다 — outbox drain은 Worker가 살아 있어야 돈다.

    전송 실패는 로그로 남기고 False를 돌려준다. 호출자가 `record_deliveries`로 결과를
    기록하며, 전달이 확인되지 않은 ALERT 에피소드는 다음 하트비트부터 15분에 한 번 다시 보낸다.

    개발 담당 경보를 개발 웹훅이 확정적으로 거절하면(2xx가 아닌 응답) 운영 웹훅으로 한 번 더
    보낸다. 시간 초과처럼 받았는지 모르면 중복을 피해 대체 전송하지 않는다 —
    2026-09-19부터 개발 웹훅이 302를 돌려줘 경보가 ERROR 로그로만 85번 사라졌다.
    """
    if not (decision.send and decision.text and decision.webhook_url):
        return False
    from app.services.notification_messages import (  # noqa: PLC0415
        CHANNEL_FALLBACK_MARKER,
        DEVELOPER_ROUTED_MARKER,
        with_routing_marker,
    )

    operator = settings.SLACK_WEBHOOK_URL.strip()
    developer = decision.audience == AUDIENCE_DEVELOPER
    can_fall_back = developer and bool(operator) and decision.webhook_url != operator
    text = decision.text
    if developer and decision.webhook_url == operator:
        # 개발 채널 없이 운영 채널 하나로 운영한다 — 개발 담당 경보임을 표시한다.
        text = with_routing_marker(text, DEVELOPER_ROUTED_MARKER)
    outcome = await _post_once_with_retry(
        decision.webhook_url, text, decision.audience, quiet=can_fall_back
    )
    if outcome == "sent":
        return True
    if not can_fall_back or outcome == "unknown":
        # 시간 초과·연결 끊김은 개발 채널이 받았을 수도 있다 — 운영 채널에 중복으로 보내지 않는다.
        return False
    logger.warning("pipeline watchdog: developer webhook rejected; falling back to operator channel")
    return (
        await _post_once_with_retry(
            operator, with_routing_marker(decision.text, CHANNEL_FALLBACK_MARKER), decision.audience
        )
        == "sent"
    )


async def _post_once_with_retry(
    url: str, text: str, audience: str, *, quiet: bool = False
) -> Literal["sent", "rejected", "unknown"]:
    """한 웹훅에 보낸다. 429·5xx·네트워크 오류만 한 번 더.

    "rejected"는 Slack이 받지 않았다는 확정(2xx가 아닌 응답·허용 밖 주소)이고, "unknown"은
    시간 초과·연결 오류처럼 받았는지 모르는 경우다. `quiet`면 실패를 경고로만 남긴다
    (뒤이어 대체 전송을 하거나 받았을 수 있으므로 사람이 볼 오류가 아니다).
    """

    log = logger.warning if quiet else logger.error
    # SSRF 가드는 기존 알림 경로와 같은 허용 목록을 쓴다.
    from app.services.notifier import _is_allowed_webhook  # noqa: PLC0415

    if not _is_allowed_webhook(url):
        log("pipeline watchdog: webhook rejected by allowlist audience=%s", audience)
        return "rejected"
    payload = {"text": text}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return "sent"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if (status == 429 or status >= 500) and attempt == 0:
                continue
            log("pipeline watchdog: Slack delivery failed status=%s", status)
            return "rejected"
        except httpx.HTTPError as exc:
            if attempt == 0:
                continue
            log("pipeline watchdog: Slack delivery failed: %s", exc.__class__.__name__)
            return "unknown"
    return "unknown"


async def deliver_all(decisions: tuple[AlertDecision, ...]) -> tuple[bool, ...]:
    return tuple([await deliver(decision) for decision in decisions])
