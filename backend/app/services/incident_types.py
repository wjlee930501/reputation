"""Typed contracts for the durable incident service."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TypeAlias
from zoneinfo import ZoneInfo

from app.models.operations import Incident, IncidentSeverity, IncidentState


class IncidentFingerprint(StrEnum):
    """Closed, non-volatile causes used in incident identity."""

    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    COST_BLOCKED = "COST_BLOCKED"
    MISSING_PREREQUISITE = "MISSING_PREREQUISITE"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    DELIVERY_OUTCOME_UNKNOWN = "DELIVERY_OUTCOME_UNKNOWN"
    RENDER_FAILED = "RENDER_FAILED"
    DOMAIN_UNHEALTHY = "DOMAIN_UNHEALTHY"
    CACHE_REVALIDATION_FAILED = "CACHE_REVALIDATION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN"


class IncidentAudience(StrEnum):
    """Who can actually act on an incident type.

    ``OPERATOR`` incidents describe customer-visible work an AE can finish in Admin.
    ``DEVELOPER`` incidents describe infrastructure an AE cannot repair (broker,
    background task crashes, notification transport, cache refresh, PDF rendering).
    Routing those to a separate webhook keeps the AE channel actionable.

    개발 웹훅이 없으면(단일 채널 운영) 개발 대상 알림도 운영 채널로 가며 `[개발 확인]`
    표시가 붙는다. 대상 구분은 표시·묶음에 계속 쓰인다(`notification_delivery`).
    """

    OPERATOR = "operator"
    DEVELOPER = "developer"


SLACK_CHANNEL = "SLACK"
SLACK_DEVELOPER_CHANNEL = "SLACK_DEV"

def incident_is_quiet(incident_type: str | None) -> bool:
    """이 종류의 사고는 Slack 알림을 만들지 않는다(행과 Admin 화면에는 남는다)."""

    return incident_policy(incident_type).quiet


def incident_audience(incident_type: str | None) -> IncidentAudience:
    """Return the audience registered for one incident type (default: operator)."""

    return incident_policy(incident_type).audience


def incident_type_of(incident: object) -> str:
    """Read an incident row's registry key defensively.

    Legacy projections and lightweight adapters can hand over objects without the
    column. An unknown type simply routes to the operator channel.
    """

    value = getattr(incident, "incident_type", "")
    return value if isinstance(value, str) else ""


def notification_channel_for_incident_type(incident_type: str | None) -> str:
    """Map one incident type onto the outbox channel that will carry it."""

    if incident_audience(incident_type) is IncidentAudience.DEVELOPER:
        return SLACK_DEVELOPER_CHANNEL
    return SLACK_CHANNEL


@dataclass(frozen=True, slots=True)
class IncidentOpenRequest:
    pipeline: str
    object_type: str
    object_id: str
    fingerprint: IncidentFingerprint
    incident_type: str
    severity: IncidentSeverity
    customer_impact: str
    source_type: str
    next_action: str
    admin_path: str
    hospital_id: uuid.UUID | None = None
    operation_run_id: uuid.UUID | None = None
    source_id: str | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None
    # 스윕 소유 종류에서 호출자가 아는 다음 자동 시도 시각(Essence). None이면 정책의 창을 쓴다.
    retry_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class IncidentNotFound:
    code: str
    incident_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class IncidentVersionConflict:
    code: str
    incident_id: uuid.UUID
    expected_version: int
    current_version: int
    current_state: str


@dataclass(frozen=True, slots=True)
class IncidentTransitionConflict:
    code: str
    incident_id: uuid.UUID
    current_version: int
    current_state: str
    required_state: str


IncidentMutationResult: TypeAlias = (
    Incident | IncidentNotFound | IncidentVersionConflict | IncidentTransitionConflict
)


@dataclass(frozen=True, slots=True)
class IncidentFilters:
    states: tuple[IncidentState, ...] = ()
    severities: tuple[IncidentSeverity, ...] = ()
    owner_id: uuid.UUID | None = None
    due_before: datetime | None = None
    overdue_only: bool = False


@dataclass(frozen=True, slots=True)
class IncidentLabels:
    state_label: str
    severity_label: str
    ownership_label: str
    sla_label: str
    next_action: str
    admin_path: str
    requires_operator_action: bool
    # RETRYING rows stay in the queue as a collapsed secondary state: automatic
    # recovery already owns them, so they are context, not work.
    automatic_recovery_in_progress: bool = False


# ── 사고 종류별 소유 정책 (2026-10 Phase 1) ──────────────────────────────────────
# 대상·조용함·스윕 소유·기한을 한 표에 둔다. 닫는 근거(resolver)는 동기 세션과 도메인 모델이
# 필요해 `workers/incident_backlog.RESOLVERS`에 같은 키로 둔다(이 모듈은 바닥 모듈이라 순환 금지).

DEFAULT_RETRYING_DEADLINE = timedelta(hours=24)
_KST = ZoneInfo("Asia/Seoul")

RetryWindow: TypeAlias = Callable[[IncidentOpenRequest, datetime], datetime | None]


@dataclass(frozen=True, slots=True)
class IncidentTypePolicy:
    """한 사고 종류를 누가 소유하는가.

    - audience: 고칠 수 있는 사람. 등록하지 않으면 운영 담당 — 새 종류를 조용히 숨기지 않는다.
    - quiet: Slack을 만들지 않고 운영자의 할 일·일일 요약 건수에도 넣지 않는다.
    - sweep_owned: 스윕이 재시도를 소유한다. 기한이 남아 있으면 RETRYING+기한으로 열리고,
      기한이 지나야 사람의 일이 된다. 기한을 정할 수 없으면 OPEN(안전한 쪽).
    - retry_window: 에피소드 시작 시각에서 기한을 계산한다. None이면 호출자가 retry_until을 준다.
    """

    audience: IncidentAudience = IncidentAudience.OPERATOR
    quiet: bool = False
    sweep_owned: bool = False
    retry_window: RetryWindow | None = None


def _period_month(source_id: str | None) -> tuple[int, int] | None:
    """`"{hospital_id}:YYYY-MM"` 또는 `"YYYY-MM"`에서 연월을 읽는다. 아니면 None."""

    raw = str(source_id or "")
    _scope, separator, tail = raw.rpartition(":")
    candidate = tail if separator else raw
    try:
        year_text, month_text = candidate.split("-", 1)
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    return (year, month) if 2000 <= year <= 2200 and 1 <= month <= 12 else None


def _one_day_window(_request: IncidentOpenRequest, episode_start: datetime) -> datetime:
    return episode_start + DEFAULT_RETRYING_DEADLINE


def _monthly_slot_window(
    request: IncidentOpenRequest, episode_start: datetime
) -> datetime | None:
    """다음 달분은 25~31일 6시간 스윕, 이번 달분은 매일 21:30 재조정이 다시 돈다.

    실패 원인이 대개 결정적(발행 요일 부족)이라 하루 안에 못 풀면 사람에게 넘긴다. 다음 달분은
    그 달이 시작되기 전에 넘겨 일정이 비기 전에 사람이 볼 수 있게 한다.
    """

    period = _period_month(request.source_id)
    if period is None:
        return None
    deadline = episode_start + DEFAULT_RETRYING_DEADLINE
    period_start = datetime(period[0], period[1], 1, tzinfo=_KST)
    return min(deadline, period_start) if period_start > episode_start else deadline


def _monthly_sov_window(
    request: IncidentOpenRequest, _episode_start: datetime
) -> datetime | None:
    """월간 측정은 다음 달 7일 23:59:59 KST까지 catch-up이 맡는다(`tasks._monthly_sov_recovery_deadline`)."""

    period = _period_month(request.source_id)
    if period is None:
        return None
    year, month = period
    next_start = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=_KST)
    return next_start + timedelta(days=7) - timedelta(seconds=1)


_DEVELOPER = IncidentTypePolicy(audience=IncidentAudience.DEVELOPER)
_DEFAULT_POLICY = IncidentTypePolicy()

INCIDENT_TYPE_POLICIES: dict[str, IncidentTypePolicy] = {
    # 순수 인프라 — 운영 담당이 Admin에서 고칠 수 없다. 등록하지 않은 종류는 운영 담당이다.
    "BACKGROUND_TASK_FAILED": _DEVELOPER,
    "OPERATION_TERMINAL_FAILED": _DEVELOPER,
    "BROKER_UNAVAILABLE": _DEVELOPER,
    "UNSAFE_STORED_DISPATCH": _DEVELOPER,
    # 자율 복구가 예산을 다 쓰거나 안전하게 다시 돌릴 수 없어 멈춘 실행(원인별 사고 하나).
    # 닫는 근거는 `incident_backlog.RESOLVERS`(같은 원인 코드의 후속 성공)에 있다.
    "OPERATION_RECOVERY_EXHAUSTED": _DEVELOPER,
    "NOTIFICATION_DELIVERY_FAILED": _DEVELOPER,
    # 행 단위 수신 불명. 채널 단위 전송 사고(NOTIFICATION_DELIVERY_FAILED)가 정본이고, 행은
    # `incident_backlog` 근거가 정리한다 — 상태 기록이며 Slack·할 일·요약 건수에서 빠진다.
    "NOTIFICATION_DELIVERY_UNKNOWN": IncidentTypePolicy(
        audience=IncidentAudience.DEVELOPER, quiet=True
    ),
    "CACHE_REVALIDATION_FAILED": _DEVELOPER,
    "MONTHLY_DOCTOR_PDF_BLOCKED": _DEVELOPER,
    # 같은 점검에서 여러 병원 주소가 공유 공개 서비스의 5xx를 받은 상태. AE가 병원 정보에서 고칠
    # 수 있는 일이 아니다(`domain_health_control.record_public_site_check`).
    "PUBLIC_SITE_UNAVAILABLE": _DEVELOPER,
    # 사후검수 지적은 계약상 운영자 큐에 올리지 않는 표본 확인이며, 하루 20편씩 지적·교정 복구마다
    # Slack을 보내 24시간에 60건이 나갔다(2026-10-08 대표 지시로 끔). 콘텐츠 탭에서 본다.
    "POST_PUBLISH_REVIEW_FLAGGED": IncidentTypePolicy(quiet=True),
    # AE가 Admin에서 고칠 수 없는 일 → 개발 담당(Phase 1-8). 일일 요약의 "개발 확인"으로만 센다.
    # 위조·불일치 actor 헤더는 BFF 서명 문제다(30일 무재발이면 근거로 닫힌다).
    "UNVERIFIED_ADMIN_ACTOR": _DEVELOPER,
    # 조치가 비용 경고 기준·측정 한도 환경값 변경(배포 일)이다.
    "MONTHLY_SOV_COHORT_OVER_LIMIT": _DEVELOPER,
    # 하루 자동 재실행 예산을 다 쓴 사이트 준비 = 코드·인프라 문제. 사람이 누른 재시도의
    # 실패(SITE_BUILD_FAILED)는 운영 담당 그대로다. 비용 한도(COST_GUARD_*)는 대표 결정이라
    # 운영 담당에 남긴다.
    "SITE_BUILD_RETRIES_EXHAUSTED": _DEVELOPER,
    # 상태로 충분한 관찰 — 행은 남고 Slack·할 일·요약 건수에서 빠진다(Phase 1-8).
    # 주간 상한 초과는 주가 끝나면 근거로 닫힌다. 주간 측정은 월간 근거의 보조이며, 사람의 일은
    # 월간 측정 실패·코호트 공백이 따로 연다(같은 사고에 묶인 정책 전환 안내도 함께 조용해진다).
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": IncidentTypePolicy(quiet=True),
    "WEEKLY_SOV_MEASUREMENT_FAILED": IncidentTypePolicy(quiet=True),
    # 스윕이 재시도를 소유한다 — 기한 안에서는 사람의 일이 아니다.
    "MONTHLY_SLOT_GENERATION_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_monthly_slot_window
    ),
    "MONTHLY_SOV_MEASUREMENT_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_monthly_sov_window
    ),
    # 사이트 준비는 1분 자율 복구(`autonomous_recovery.reconcile`)가, 가까운 글 생성은 야간·복구
    # 스윕이 다시 집는다. 큐에 못 넣은 사실만으로는 사람의 일이 아니다.
    "SITE_BUILD_DISPATCH_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_one_day_window
    ),
    "CONTENT_DISPATCH_FAILED": IncidentTypePolicy(sweep_owned=True, retry_window=_one_day_window),
    # 자동 재검수 예산이 남은 보류. 다음 시도 시각은 호출자가 안다(`retry_until`).
    "ESSENCE_AUTO_REVIEW_ESCALATED": IncidentTypePolicy(sweep_owned=True),
}


def incident_policy(incident_type: str | None) -> IncidentTypePolicy:
    """한 사고 종류의 소유 정책. 등록하지 않은 종류는 운영 담당·비조용·사람 소유다."""

    if not incident_type:
        return _DEFAULT_POLICY
    return INCIDENT_TYPE_POLICIES.get(incident_type.strip().upper(), _DEFAULT_POLICY)


# 기존 import·SQL·테스트가 쓰는 집합은 레지스트리에서 유도한다(정본은 위 표 하나).
_DEVELOPER_INCIDENT_TYPES: frozenset[str] = frozenset(
    key
    for key, policy in INCIDENT_TYPE_POLICIES.items()
    if policy.audience is IncidentAudience.DEVELOPER
)
QUIET_INCIDENT_TYPES: frozenset[str] = frozenset(
    key for key, policy in INCIDENT_TYPE_POLICIES.items() if policy.quiet
)
SWEEP_OWNED_INCIDENT_TYPES: frozenset[str] = frozenset(
    key for key, policy in INCIDENT_TYPE_POLICIES.items() if policy.sweep_owned
)


def retry_deadline(
    request: IncidentOpenRequest, *, episode_start: datetime, observed_at: datetime
) -> datetime | None:
    """스윕 소유 사고의 남은 자동 재시도 기한. 소유가 아니거나, 정할 수 없거나, 지났으면 None."""

    policy = incident_policy(request.incident_type)
    if not policy.sweep_owned:
        return None
    deadline = request.retry_until
    if deadline is None and policy.retry_window is not None:
        deadline = policy.retry_window(request, episode_start)
    if deadline is None or deadline <= observed_at:
        return None
    return deadline
