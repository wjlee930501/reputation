"""Recover committed workflow state whose first Celery dispatch was lost."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from celery import current_task
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    OperationRun,
    OperationRunState,
)
from app.services import operation_run_payloads
from app.services import published_image_recertification as recertification
from app.services.incident_assignment import auto_assign_owner_sync, owner_label_sync
from app.services.incident_safety import (
    REBUILD_SITE_SWEEP_KEY_PREFIX,
    build_incident_key,
)
from app.services.incident_types import IncidentFingerprint, incident_type_of
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.services.site_build_incidents import (
    load_site_build_incident,
    open_site_build_incident,
    reopen_site_build_incident,
    touch_site_build_incident,
)
from app.services.site_revalidation_control import retry_delay
from app.workers import generation_run_control
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch
from app.workers.dispatch_envelope import expected_purpose
from app.workers.incident_backlog import close_resolved_backlog_incidents, settle_incidents

_BATCH_SIZE: Final = 100
# REQUESTED는 '아직 브로커에 넣지 못했다'만 뜻한다 — 모든 배포 지점이 publish 직후 QUEUED로
# 바꾸므로 짧게 둔다. QUEUED는 '브로커에 있다'라서 적체를 유실로 오인하지 않도록 길게 둔다.
# 관측된 최악의 content 적체는 약 70분이었고, 2분 만에 다시 보내던 때는 적체마다 사본이 생겼다.
_REQUESTED_REDISPATCH_GRACE: Final = timedelta(minutes=2)
_QUEUED_REDISPATCH_GRACE: Final = timedelta(hours=3)
# 고객이 기다리는 결과(V0·월간 보고서)는 45분만 기다린다. 배포 중 유실은 cold shutdown
# (REMAP_SIGTERM=SIGQUIT)이 몇 초 안에 메시지를 반납해 사라졌고, 남은 대기는 앞 큐(content·sov)의
# 적체다 — reports 큐는 priority 순서상 뒤에 있다. 적체 중 다시 보내도 늦게 온 이전 사본은
# task id가 달라 STALE로 물러나므로 공급자 비용이 없다.
_FAST_QUEUED_REDISPATCH_GRACE: Final = timedelta(minutes=45)
_FAST_QUEUED_REDISPATCH_TYPES: Final = frozenset(
    {"TRIGGER_V0_REPORT", "GENERATE_MONTHLY_REPORT"}
)
# 유형별 재배달 예산. 재배달마다 QUEUED 유예가 두 배가 되므로(45m→90m→3h→6h) 정상 적체로는
# 소진되지 않는다. 다 쓰면 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다.
_REDISPATCH_BUDGET: Final = 3
_RECOVERY_STOP_INCIDENT_TYPE: Final = "OPERATION_RECOVERY_EXHAUSTED"
_REDISPATCH_EXHAUSTED: Final = "OPERATION_REDISPATCH_EXHAUSTED"
_LEASE_EXPIRED: Final = "OPERATION_LEASE_EXPIRED"
# 이만큼 아무 갱신이 없고 lease도 없는 RUNNING은 끝낼 주체가 사라진 잔재다(9/7 야간 배치 잔재).
# 시작 시각이 아니라 마지막 갱신으로 본다 — 월간 측정 구간 실행은 24~31일 내내 RUNNING이다.
_ABANDONED_RUNNING_AFTER: Final = timedelta(days=10)
# 유형별로 더 길게 기다리는 예외. 정상인 `MONTHLY_SOV_PERIOD`는 말일 18시(KST) 마지막 창 sweep 뒤
# 다음 달 8일 00:15 최종화(`_finalize_monthly_sov_period_run`)까지 약 7일 6시간 아무도 건드리지
# 않는다. 기본 10일과의 여유가 3일뿐이라 이 유형만 20일을 기다린다.
_ABANDONED_RUNNING_AFTER_BY_TYPE: Final[dict[str, timedelta]] = {
    "MONTHLY_SOV_PERIOD": timedelta(days=20),
}
_RUN_ABANDONED: Final = "OPERATION_RUN_ABANDONED"
_RECOVERY_STOP_MESSAGES: Final[dict[str, str]] = {
    # FAILED는 마지막 재배달의 유예(기본값 × 8) 뒤에 온다 — V0·월간 보고서는 6시간, 그 밖은 24시간.
    _REDISPATCH_EXHAUSTED: (
        f"자동 재배달을 {_REDISPATCH_BUDGET}번 했지만 작업이 시작되지 않거나 끝나지 않아 "
        "자동 복구를 멈췄습니다."
    ),
    _LEASE_EXPIRED: (
        "보고서 재생성 작업이 끝나지 않은 채 중단됐습니다. 다시 실행하면 새 버전이 중복으로 생길 수 "
        "있어 자동으로 다시 실행하지 않았습니다."
    ),
    _RUN_ABANDONED: "10일 넘게 진행 기록이 없는 작업을 종료 처리했습니다.",
}
_RECERTIFY_DISPATCH_LIMIT: Final = 20
_REBUILD_SITE_ATTEMPT_BUDGET: Final = 3
_REBUILD_SITE_BUDGET_WINDOW: Final = timedelta(hours=24)
_INTEGER_ARG: Final = object()
_UUID_ARG: Final = object()
_UUID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class _RedispatchPolicy:
    task_name: str
    queue: str
    target_type: str
    allowed_arg_suffixes: tuple[tuple[object, ...], ...] = ((),)


_OPERATION_REDISPATCH_POLICIES: Final[dict[str, _RedispatchPolicy]] = {
    "TRIGGER_V0_REPORT": _RedispatchPolicy(
        "app.workers.tasks.trigger_v0_report", "reports", "hospital"
    ),
    "RUN_SOV": _RedispatchPolicy(
        "app.workers.tasks.run_sov_for_hospital",
        "sov",
        "hospital",
        ((), ("monthly", _INTEGER_ARG, _INTEGER_ARG)),
    ),
    "REBUILD_SITE": _RedispatchPolicy(
        "app.workers.tasks.build_aeo_site", "default", "hospital"
    ),
    "GENERATE_MONTHLY_REPORT": _RedispatchPolicy(
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "reports",
        "hospital",
        (
            (_INTEGER_ARG, _INTEGER_ARG),
            (_INTEGER_ARG, _INTEGER_ARG, True),
            (_INTEGER_ARG, _INTEGER_ARG, True, True),
            # 문구·디자인만 다시 만들기(template_only). 숫자 판정은 워커가 다시 한다.
            (_INTEGER_ARG, _INTEGER_ARG, True, False, True),
            # 운영자 지정으로 측정 미완료 병원도 템플릿 갱신(allow_recovery_pending).
            (_INTEGER_ARG, _INTEGER_ARG, True, False, True, True),
        ),
    ),
    "REGENERATE_CONTENT": _RedispatchPolicy(
        "app.workers.tasks.regenerate_content_item", "content", "content_item"
    ),
    "REGENERATE_CONTENT_IMAGE": _RedispatchPolicy(
        "app.workers.tasks.generate_content_image", "content", "content_item"
    ),
    # 야간 배치가 팬아웃한 슬롯 하나의 생성. 유실된 배포만 되살린다 — claim token까지
    # 저장된 그대로 다시 실어 주고, 그 사이 lease가 바뀌었으면 태스크 자신이 물러난다.
    # 토큰 없이 되살아난 경우에는 태스크가 자기 lease를 새로 claim한다.
    generation_run_control.GENERATE_CONTENT_ITEM_OPERATION: _RedispatchPolicy(
        generation_run_control.GENERATE_CONTENT_ITEM_TASK,
        "content",
        "content_item",
        ((), (_UUID_ARG,), (_UUID_ARG, None), (_UUID_ARG, False)),
    ),
    recertification.RECERTIFY_OPERATION: _RedispatchPolicy(
        "app.workers.tasks.recertify_published_content_image", "content", "content_item"
    ),
}


# lease가 만료된 RUNNING을 새 task id로 다시 보내도 안전한 유형과 근거(2026-10-11).
# lease(1시간)는 가장 긴 hard limit(2700초)보다 길어, 만료된 RUNNING은 죽은 실행이다.
_LEASE_TAKEOVER_TYPES: Final = frozenset(
    {
        "TRIGGER_V0_REPORT",  # 측정 체크포인트 — 이미 산 공급자 단계를 다시 사지 않는다
        "REBUILD_SITE",  # 상태 준비는 멱등이고 하루 시도 예산이 있다
        "RUN_SOV",  # 고정 manifest의 남은 칸만 측정, 슬롯은 유료 호출 전에 저장된다
        "GENERATE_MONTHLY_REPORT",  # rebuild=False만 — 아래 `_lease_takeover_refused`
        "REGENERATE_CONTENT",  # 글 단위 생성 lease + 일일 예산
        "REGENERATE_CONTENT_IMAGE",  # 같은 생성 lease + 이미지 일일 예산
        # 저장된 claim token이 바뀌었으면 공급자 호출 없이 CANCELLED로 끝난다.
        generation_run_control.GENERATE_CONTENT_ITEM_OPERATION,
    }
)
# RECERTIFY_PUBLISHED_IMAGE는 넣지 않는다 — 재인증 sweep이 30분 뒤 좌초 실행을 새 실행으로
# 대체하므로(`IN_FLIGHT_TIMEOUT`) 인수하면 같은 (글, subject)에 주인이 둘이 된다.


class RecoveryCounts(TypedDict):
    site_builds: int
    site_revalidations: int
    operation_runs: int
    image_recertifications: int
    resolved_task_incidents: int


def _redispatch_count(run: OperationRun) -> int:
    return run.redispatch_count


def _queued_redispatch_grace(run: OperationRun) -> timedelta:
    base = (
        _FAST_QUEUED_REDISPATCH_GRACE
        if str(run.operation_type) in _FAST_QUEUED_REDISPATCH_TYPES
        else _QUEUED_REDISPATCH_GRACE
    )
    return base * (2 ** min(_redispatch_count(run), _REDISPATCH_BUDGET))


def _counts_against_redispatch_budget(run: OperationRun) -> bool:
    """이 재배달이 '브로커에 있던 사본을 잃었다'는 추정인가.

    publish 실패로 남은 REQUESTED는 브로커 장애 동안 2분마다 다시 시도하므로 세지 않는다.
    미룬 실행(`not_before_at`)이 정한 시각에 나가는 것도 계획된 배달이라 세지 않는다.
    """

    if run.state == OperationRunState.RUNNING:
        return True
    return run.state == OperationRunState.QUEUED and getattr(run, "not_before_at", None) is None


def _redispatch_budget_exhausted(run: OperationRun) -> bool:
    return _counts_against_redispatch_budget(run) and _redispatch_count(run) >= _REDISPATCH_BUDGET


def _lease_takeover_refused(run: OperationRun) -> bool:
    """월간 보고서 재생성(rebuild=True)은 다시 돌면 새 버전을 하나 더 만든다 — 인수하지 않는다."""

    return (
        str(run.operation_type) == "GENERATE_MONTHLY_REPORT"
        and run.state == OperationRunState.RUNNING
        and _stored_monthly_report_flags(run) != ()
    )


def _is_abandoned_running(run: OperationRun, observed_at: datetime) -> bool:
    if run.state != OperationRunState.RUNNING:
        return False
    if str(getattr(run, "operation_type", "")) == "SITE_REVALIDATION":
        # 재검증 실행은 같은 reconcile의 재검증 루프가 소유한다.
        return False
    lease = getattr(run, "lease_expires_at", None)
    if lease is not None and lease > observed_at:
        return False
    touched = getattr(run, "updated_at", None)
    window = _ABANDONED_RUNNING_AFTER_BY_TYPE.get(
        str(getattr(run, "operation_type", "")), _ABANDONED_RUNNING_AFTER
    )
    return touched is not None and touched <= observed_at - window


def _abandoned_running_clause(observed_at: datetime):
    return and_(
        OperationRun.state == OperationRunState.RUNNING,
        # 재검증 실행은 같은 reconcile의 재검증 루프가 소유한다.
        OperationRun.operation_type != "SITE_REVALIDATION",
        or_(
            OperationRun.lease_expires_at.is_(None),
            OperationRun.lease_expires_at <= observed_at,
        ),
        OperationRun.updated_at
        <= case(
            *(
                (OperationRun.operation_type == operation_type, observed_at - window)
                for operation_type, window in _ABANDONED_RUNNING_AFTER_BY_TYPE.items()
            ),
            else_=observed_at - _ABANDONED_RUNNING_AFTER,
        ),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _revalidation_is_due(run: OperationRun, observed_at: datetime) -> bool:
    delay = retry_delay(run.attempt_count)
    if delay is None:
        return False
    last_attempt = run.heartbeat_at or run.started_at or run.requested_at
    return last_attempt <= observed_at - timedelta(seconds=delay)


def _operation_redispatch_is_due(run: OperationRun, observed_at: datetime) -> bool:
    """Distinguish a publish that may be lost from legitimate broker queue time."""

    if run.state == OperationRunState.REQUESTED:
        last_transition = run.requested_at
        grace = _REQUESTED_REDISPATCH_GRACE
    elif run.state == OperationRunState.QUEUED:
        not_before = getattr(run, "not_before_at", None)
        if not_before is not None:
            # 워커가 미뤄 둔 실행이다(예: V0 비용 창). 그 시각 전에는 막힌 것이 아니다.
            return not_before <= observed_at
        last_transition = run.queued_at or run.requested_at
        grace = _queued_redispatch_grace(run)
    elif (
        run.operation_type in _LEASE_TAKEOVER_TYPES
        and run.state == OperationRunState.RUNNING
        and run.lease_expires_at is not None
    ):
        # 이전 워커의 lease가 만료돼 더는 이 실행을 소유할 수 없다는 것이 증명된 뒤에만 인수한다.
        # 재실행 안전성은 유형별로 `_LEASE_TAKEOVER_TYPES` 주석에 있다.
        return run.lease_expires_at <= observed_at
    else:
        return False
    return last_transition <= observed_at - grace


@celery_app.task(name="app.workers.autonomous_recovery.reconcile")
def reconcile() -> RecoveryCounts:
    """Re-dispatch idempotent work from committed database truth."""

    require_dispatch(current_task, "reconcile-autonomous-workflows")
    observed_at = _now()
    with SyncSessionLocal() as db:
        hospitals = list(
            db.execute(
                select(Hospital)
                .where(
                    Hospital.profile_complete.is_(True),
                    or_(
                        Hospital.site_built.is_(False),
                        # 허브는 준비됐는데 기본 주소 자동 활성화가 유실된 병원. STEP5 재촉
                        # Slack을 없앤 뒤에는 이 재실행이 유일한 복구 경로다 —
                        # build_aeo_site는 이미 ACTIVE·PAUSED·자기 도메인 병원을 건드리지
                        # 않으므로 재배달해도 안전하다.
                        and_(
                            Hospital.site_built.is_(True),
                            Hospital.site_live.is_(False),
                            Hospital.status == HospitalStatus.PENDING_DOMAIN,
                            or_(Hospital.aeo_domain.is_(None), Hospital.aeo_domain == ""),
                        ),
                    ),
                )
                .order_by(Hospital.created_at, Hospital.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
        )
        runs = [
            run
            for run in db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == "SITE_REVALIDATION",
                    OperationRun.state == OperationRunState.RUNNING,
                )
                .order_by(OperationRun.heartbeat_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
            if _revalidation_is_due(run, observed_at)
        ]
        operation_runs = [
            run
            for run in db.execute(
                select(OperationRun)
                .where(
                    or_(
                        and_(
                            OperationRun.operation_type.in_(
                                tuple(_OPERATION_REDISPATCH_POLICIES)
                            ),
                            or_(
                                and_(
                                    OperationRun.state == OperationRunState.REQUESTED,
                                    OperationRun.requested_at
                                    <= observed_at - _REQUESTED_REDISPATCH_GRACE,
                                ),
                                and_(
                                    OperationRun.state == OperationRunState.QUEUED,
                                    OperationRun.not_before_at.is_(None),
                                    # 유형별 기본 유예로 고른다(다른 유형의 45분~3시간 적체가 배치를
                                    # 채우지 않게). 재배달 횟수에 따른 두 배 판정은
                                    # `_operation_redispatch_is_due`가 한다.
                                    func.coalesce(OperationRun.queued_at, OperationRun.requested_at)
                                    <= case(
                                        (
                                            OperationRun.operation_type.in_(
                                                tuple(_FAST_QUEUED_REDISPATCH_TYPES)
                                            ),
                                            observed_at - _FAST_QUEUED_REDISPATCH_GRACE,
                                        ),
                                        else_=observed_at - _QUEUED_REDISPATCH_GRACE,
                                    ),
                                ),
                                # 미룬 실행은 적체가 아니다 — 정한 시각이 지났을 때만 보낸다.
                                and_(
                                    OperationRun.state == OperationRunState.QUEUED,
                                    OperationRun.not_before_at <= observed_at,
                                ),
                                and_(
                                    OperationRun.operation_type.in_(tuple(_LEASE_TAKEOVER_TYPES)),
                                    OperationRun.state == OperationRunState.RUNNING,
                                    OperationRun.lease_expires_at.isnot(None),
                                    OperationRun.lease_expires_at <= observed_at,
                                ),
                            ),
                        ),
                        # 끝낼 주체가 사라진 RUNNING 잔재(정책 밖 유형 포함).
                        _abandoned_running_clause(observed_at),
                    )
                )
                .order_by(OperationRun.requested_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
            if _operation_redispatch_is_due(run, observed_at)
            or _is_abandoned_running(run, observed_at)
        ]

        for run in runs:
            celery_app.send_task(
                "app.workers.tasks.retry_site_revalidation",
                args=[str(run.id), run.attempt_count],
                queue="control",
                priority=0,
                headers=build_dispatch_headers("retry-site-revalidation", str(run.id)),
            )
            run.heartbeat_at = observed_at
        operation_redispatches = 0
        handled_site_hospitals: set[uuid.UUID] = set()
        for run in operation_runs:
            if _is_abandoned_running(run, observed_at):
                if (
                    _stop_operation_run(db, run, observed_at, _RUN_ABANDONED)
                    and run.operation_type == "REBUILD_SITE"
                    and run.hospital_id is not None
                ):
                    handled_site_hospitals.add(run.hospital_id)
                continue
            if _site_rebuild_attempts_exhausted(run):
                if _fail_exhausted_site_rebuild(db, run, observed_at):
                    if run.hospital_id is not None:
                        handled_site_hospitals.add(run.hospital_id)
                continue
            if _lease_takeover_refused(run):
                _stop_operation_run(db, run, observed_at, _LEASE_EXPIRED)
                continue
            if _redispatch_budget_exhausted(run):
                if (
                    _stop_operation_run(db, run, observed_at, _REDISPATCH_EXHAUSTED)
                    and run.operation_type == "REBUILD_SITE"
                    and run.hospital_id is not None
                ):
                    handled_site_hospitals.add(run.hospital_id)
                continue
            redispatched = _redispatch_operation_run(db, run, observed_at)
            if redispatched:
                operation_redispatches += 1
                if run.operation_type == "REBUILD_SITE" and run.hospital_id is not None:
                    handled_site_hospitals.add(run.hospital_id)
        # 실행 기록을 먼저 커밋하는 경로이므로 같은 tick의 상태 변경 뒤에 둔다.
        site_builds = 0
        for hospital in hospitals:
            if hospital.id in handled_site_hospitals:
                continue
            rebuild = _ensure_rebuild_site_run(db, hospital, observed_at)
            if rebuild is not None and _redispatch_operation_run(db, rebuild, observed_at):
                site_builds += 1
        recertifications = _dispatch_published_image_recertifications(db, observed_at)
        resolved_task_incidents = _settle_incident_backlog(db)
        db.commit()
    return {
        "site_builds": site_builds,
        "site_revalidations": len(runs),
        "operation_runs": operation_redispatches,
        "image_recertifications": recertifications,
        "resolved_task_incidents": resolved_task_incidents,
    }


def _settle_incident_backlog(db) -> int:
    """근거로 닫을 사고와 자동화가 끝낸 사고를 정리한다(사고 쪽 정리의 한 이음매).

    새 정리 단계는 여기에 더한다 — 재조정 테스트는 이 한 함수만 막으면 된다.
    """

    return close_resolved_backlog_incidents(db) + settle_incidents(db)


def _cleared_certificate_candidates(db) -> list[ContentItem]:
    """공개 중인데 이미지 인증이 지워진 글. 제목 편집이 남기는 상태만 SQL로 값싸게 고른다.

    정책 버전 교체는 manifest로 운전하는 별도 작업이므로 여기서 다루지 않는다 — 그 조건을
    넣으면 롤아웃 때 전체 공개 글이 한꺼번에 이 sweep으로 쏟아진다. hash 불일치도 저장된
    바이트를 다시 읽어야 알 수 있어 대상이 아니다. 사람의 결정을 기다리고 **그 사고가
    아직 보이는** 판은 SQL에서 빼, 아직 자동으로 고칠 수 있는 글의 자리를 뺏지 않게 한다.
    """
    return list(
        db.execute(
            select(ContentItem)
            .join(Hospital, Hospital.id == ContentItem.hospital_id)
            .where(
                publicly_operational_hospital_predicate(),
                ContentItem.status == ContentStatus.PUBLISHED,
                ContentItem.image_url.isnot(None),
                ContentItem.image_url != "",
                ContentItem.image_policy_verified_at.is_(None),
                ~recertification.blocked_row_predicate(),
            )
            .order_by(ContentItem.published_at, ContentItem.id)
            .limit(_BATCH_SIZE)
        )
        .scalars()
        .all()
    )


def _recertify_runs_by_item(db, candidates: list[ContentItem]) -> dict[str, list[OperationRun]]:
    """후보 글들의 재인증 실행 이력을 색인된 컬럼으로 한 번에 읽는다.

    payload 안의 대상·판 비교는 Python에서 한다 — JSON 표현식으로 거르면 색인을 못 쓴다.
    """
    if not candidates:
        return {}
    hospital_ids = {item.hospital_id for item in candidates}
    wanted = {str(item.id) for item in candidates}
    grouped: dict[str, list[OperationRun]] = {}
    for run in (
        db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id.in_(hospital_ids),
                OperationRun.operation_type == recertification.RECERTIFY_OPERATION,
            )
        )
        .scalars()
        .all()
    ):
        source_id = recertification.payload_source_id(run)
        if source_id in wanted:
            grouped.setdefault(source_id, []).append(run)
    return grouped


def _visible_block_incidents(db, candidates: list[ContentItem]) -> set[str]:
    """차단이 아직 사람에게 남아 있는 (글, subject)의 사고 키.

    사고가 닫혔는데 실행 이력에는 보류 코드가 남은 subject는 아무도 보지 않는 보류다.
    그 한 건만 무료 실행으로 되살린다.
    """
    if not candidates:
        return set()
    keys = {
        recertification.incident_dedupe_key(
            item.id, recertification.subject_hash_of(item)
        )
        for item in candidates
    }
    return set(
        db.execute(
            select(Incident.dedupe_key).where(
                Incident.dedupe_key.in_(keys),
                Incident.state.in_(recertification.VISIBLE_INCIDENT_STATES),
            )
        )
        .scalars()
        .all()
    )


def _dispatch_published_image_recertifications(db, observed_at: datetime) -> int:
    """인증이 지워진 공개 글의 재인증을 예산 안에서 다시 실행한다 (H-01).

    태스크는 실행 하나당 공급자를 많아야 한 번 부르므로 재실행은 이 sweep만 만든다.
    쿨다운·진행 중 판정·예산은 모두 `published_image_recertification`의 한 규칙이라,
    PATCH·sweep·운영자 재시도가 겹쳐도 (글, subject)당 유료 호출이 늘지 않는다.
    """
    candidates = _cleared_certificate_candidates(db)
    runs_by_item = _recertify_runs_by_item(db, candidates)
    visible_blocks = _visible_block_incidents(db, candidates)
    dispatched = 0
    for item in candidates:
        if dispatched >= _RECERTIFY_DISPATCH_LIMIT:
            break
        subject = recertification.subject_hash_of(item)
        runs = runs_by_item.get(str(item.id), [])
        if not recertification.sweep_may_dispatch(
            runs,
            subject,
            now=observed_at,
            block_visible=recertification.incident_dedupe_key(item.id, subject)
            in visible_blocks,
        ):
            continue
        if _start_recertify_run(db, item, subject, runs, observed_at):
            dispatched += 1
    return dispatched


def _start_recertify_run(
    db,
    item: ContentItem,
    subject_hash: str,
    runs: list[OperationRun],
    observed_at: datetime,
) -> bool:
    """REQUESTED run을 먼저 커밋하고 publish한다.

    worker가 publish를 먼저 집어도 claim할 행이 있어야 한다. publish가 실패해도 남은
    REQUESTED run을 이 파일의 재배달 sweep이 잇는다. 두 reconcile이 겹쳐 같은 키를
    만들면 뒤에 온 쪽은 건너뛴다 — 같은 subject를 두 번 사지 않기 위해서다.
    """
    target_id = str(item.id)
    task_id = str(uuid.uuid4())
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=item.hospital_id,
        operation_type=recertification.RECERTIFY_OPERATION,
        state=OperationRunState.REQUESTED,
        idempotency_key=recertification.next_sweep_key(item.id, subject_hash, runs),
        requested_by_id=None,
        task_id=task_id,
        requested_at=observed_at,
        attempt_count=0,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=recertification.request_payload(
            item.id,
            subject_hash=subject_hash,
            title=item.title,
            revision=int(getattr(item, "content_revision", 1) or 1),
        ),
        version=1,
    )
    savepoint = db.begin_nested()
    try:
        db.add(run)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    db.commit()
    celery_app.send_task(
        "app.workers.tasks.recertify_published_content_image",
        args=[target_id],
        queue="content",
        headers={
            **build_dispatch_headers("recertify-published-image", target_id),
            "operation_run_id": str(run.id),
        },
        task_id=task_id,
    )
    generation_run_control.mark_operation_run_queued(db, run.id, observed_at)
    return True


def _ensure_rebuild_site_run(
    db, hospital: Hospital, observed_at: datetime
) -> OperationRun | None:
    """사이트 준비 재실행을 REBUILD_SITE OperationRun 예산 아래 둔다 (H-13).

    예전에는 이 sweep이 같은 병원에 `build_aeo_site`를 매분 새로 큐잉했다. claim도 시도
    기록도 없어 실패하는 병원 하나가 무한히 재실행됐다. 이제 진행 중인 run이 grace 안이면
    건너뛰고, 24시간 안에 실패가 예산만큼 쌓이면 병원 하나당 사고 한 건으로 사람에게
    넘긴다. 그 밖에는 REQUESTED run을 만들어 호출자가 서명된 재배달로 보낸다.
    """

    recent = list(
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.operation_type == "REBUILD_SITE",
                OperationRun.hospital_id == hospital.id,
                OperationRun.requested_at >= observed_at - _REBUILD_SITE_BUDGET_WINDOW,
            )
            .order_by(OperationRun.requested_at.desc())
        )
        .scalars()
        .all()
    )
    for run in recent:
        if run.state in (
            OperationRunState.REQUESTED,
            OperationRunState.QUEUED,
            OperationRunState.RUNNING,
        ):
            # 진행 중인 실행이 있으면 새 run을 만들지 않는다. 유실이 의심될 때만
            # 같은 run을 재배달한다 — 판단은 일반 재배달 규칙과 하나로 유지한다.
            return run if _operation_redispatch_is_due(run, observed_at) else None
    failed = _failures_since_last_success(recent)
    if _site_build_attempts_used(failed) >= _REBUILD_SITE_ATTEMPT_BUDGET:
        _open_rebuild_site_incident(
            db, hospital, max(failed, key=_run_observed_at), observed_at
        )
        return None

    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="REBUILD_SITE",
        state=OperationRunState.REQUESTED,
        idempotency_key=(
            f"{REBUILD_SITE_SWEEP_KEY_PREFIX}{hospital.id}"
            f":{observed_at.astimezone(UTC).date().isoformat()}:{_runs_started_today(recent, observed_at)}"
        ),
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        requested_at=observed_at,
        attempt_count=len(failed),
        redispatch_count=0,
        total_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=operation_run_payloads.build_request_payload(
            operation_run_payloads.DispatchPayload(
                "hospital", str(hospital.id), "default", (str(hospital.id),)
            )
        ),
        version=1,
    )
    # 실행 기록을 먼저 커밋한다. worker가 publish를 먼저 집어도 claim할 행이 있어야 하고,
    # 같은 키를 동시에 만든 두 번째 sweep은 여기서 조용히 물러난다.
    savepoint = db.begin_nested()
    try:
        db.add(run)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return None
    db.commit()
    return run


def _site_rebuild_attempts_exhausted(run: OperationRun) -> bool:
    return (
        run.operation_type == "REBUILD_SITE"
        and run.state == OperationRunState.RUNNING
        and run.attempt_count >= _REBUILD_SITE_ATTEMPT_BUDGET
    )


def _site_build_attempts_used(failed: list[OperationRun]) -> int:
    """Count legacy one-run failures and cumulative takeover attempts consistently."""

    return max(
        len(failed),
        max((run.attempt_count for run in failed), default=0),
    )


def _fail_exhausted_site_rebuild(
    db, run: OperationRun, observed_at: datetime
) -> bool:
    """Terminalize one expired site run only if its lease/version ownership is unchanged."""

    failed = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run.id,
            OperationRun.task_id == run.task_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.version == run.version,
            OperationRun.lease_expires_at <= observed_at,
            OperationRun.attempt_count >= _REBUILD_SITE_ATTEMPT_BUDGET,
        )
        .values(
            state=OperationRunState.FAILED,
            completed_at=observed_at,
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            safe_error_code="SITE_BUILD_RETRIES_EXHAUSTED",
            safe_error_message="사이트 준비 자동 재실행이 하루치 예산을 모두 사용했습니다.",
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if failed is None:
        return False
    run.state = OperationRunState.FAILED
    run.completed_at = observed_at
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = "SITE_BUILD_RETRIES_EXHAUSTED"
    run.safe_error_message = "사이트 준비 자동 재실행이 하루치 예산을 모두 사용했습니다."
    run.version += 1
    db.commit()
    hospital = db.get(Hospital, run.hospital_id)
    if hospital is not None:
        _open_rebuild_site_incident(db, hospital, run, observed_at)
    return True


def _runs_started_today(recent: list[OperationRun], observed_at: datetime) -> int:
    """오늘 만들어진 사이트 준비 실행의 수 — 새 실행의 idempotency key 꼬리표다 (H-13).

    꼬리표는 날짜 안에서 절대 되돌아가면 안 된다. 실패 수로 세면 성공 한 번이 계수를 0으로
    되돌려 같은 날 이미 쓴 키가 다시 나오고, 유일 제약이 savepoint 안에서 조용히 삼켜져
    그 병원은 자정까지 자동 재실행을 못 받는다. 24시간 창 전체를 세도 어제 같은 시각의
    실행이 창 밖으로 빠지는 만큼 계수가 줄어 같은 충돌이 하루 뒤에 다시 온다. 오늘 만든
    실행은 오늘 안에는 창에서 빠지지 않으므로, 이 수는 날짜 안에서 늘기만 한다.
    """

    # 키의 날짜 부분과 같은 UTC 날짜로 비교한다 — DB 세션 시간대에 기대지 않는다.
    today = observed_at.astimezone(UTC).date()
    return sum(
        1 for run in recent if run.requested_at.astimezone(UTC).date() == today
    )


def _run_observed_at(run: OperationRun) -> datetime:
    """이 실행의 종료가 관측된 시각. 종료 기록이 없으면 마지막 상태 변경 시각을 쓴다."""

    return run.completed_at or run.updated_at or run.requested_at


def _failures_since_last_success(recent: list[OperationRun]) -> list[OperationRun]:
    """예산을 쓴 실패만 남긴다 — 성공보다 앞선 실패는 이미 해결된 일이다.

    운영자 재시도 한 번이 성공하면 그 전의 실패는 더 이상 막을 이유가 아니다. 그대로 세면
    성공한 병원이 24시간 동안 자동 재실행을 못 받고, 성공 전의 실패로 사고가 다시 열린다.
    """

    latest_success = max(
        (
            _run_observed_at(run)
            for run in recent
            if run.state == OperationRunState.SUCCEEDED
        ),
        default=None,
    )
    return [
        run
        for run in recent
        if run.state == OperationRunState.FAILED
        and (latest_success is None or _run_observed_at(run) > latest_success)
    ]


# 개발 담당의 사고다(`incident_types`). 원인을 고친 뒤 할 수 있는 일은 운영센터의 재시도 버튼 하나다.
SITE_BUILD_EXHAUSTED_NEXT_ACTION = (
    "사이트 준비 오류를 확인해 원인을 고친 뒤, 운영센터에서 이 사고에 연결된 작업의 "
    "‘작업 다시 시도’를 눌러 주세요."
)


def _open_rebuild_site_incident(
    db, hospital: Hospital, newest_failed_run: OperationRun, observed_at: datetime
) -> None:
    """예산을 다 쓴 사이트 준비를 병원 하나당 사고 한 건으로 넘긴다 (H-13).

    최종 차단은 원인별로 한 건이지만, 그 한 건이 영원히 같은 값으로 굳어서도 안 된다.
    이미 열려 있으면 사고가 가리키는 실행과 다른 실행이 새로 실패했을 때만 관측을 갱신한다 —
    tick마다 세면 occurrence_count는 하루 1,400이 되어 재발 횟수라는 뜻을 잃는다. 반대로
    복구·확인으로 닫힌 건은 새 에피소드로 다시 연다. 같은 문제가 다시 예산을 다 썼는데
    조용하면 아무도 그 사실을 모른다.
    """

    incident = load_site_build_incident(db, hospital.id)
    if incident is None:
        created = open_site_build_incident(
            db,
            hospital_id=hospital.id,
            hospital_name=hospital.name,
            failed_run_id=newest_failed_run.id,
            safe_error_code="SITE_BUILD_RETRIES_EXHAUSTED",
            safe_error_message="사이트 준비 자동 재실행이 하루치 예산을 모두 사용했습니다.",
            next_action=SITE_BUILD_EXHAUSTED_NEXT_ACTION,
            observed_at=observed_at,
        )
        if created is not None:
            return
        # 같은 tick의 다른 replica가 먼저 만들었다 — 병원 잠금은 앞선 commit에서 이미
        # 풀려 두 sweep이 여기까지 온다. IntegrityError를 그대로 올리면 뒤따르는 병원과
        # 재인증이 통째로 밀리므로, 상대가 만든 행을 다시 읽어 아래 규칙으로 이어간다.
        incident = load_site_build_incident(db, hospital.id)
        if incident is None:
            return
    if incident.state in (
        IncidentState.RECOVERED.value,
        IncidentState.ACKNOWLEDGED.value,
    ):
        closed_at = incident.recovered_at or incident.acknowledged_at
        if closed_at is not None and _run_observed_at(newest_failed_run) <= closed_at:
            # 닫히기 전의 실패다. 그 뒤로 새로 실패한 적이 없는데 다시 열면, 이미 해결된
            # 일로 새 에피소드와 Slack이 나가고 자동 재실행까지 24시간 막힌다.
            return
        reopen_site_build_incident(
            db,
            incident,
            hospital_name=hospital.name,
            failed_run_id=newest_failed_run.id,
            observed_at=observed_at,
        )
        return
    if newest_failed_run.id == incident.operation_run_id:
        # 이미 센 실패다 — 사고가 그 실행을 가리키고 있다. 관측 시각으로 가르면, 운영자
        # 재시도의 실패를 `record_task_failure`가 먼저 실은 뒤 `completed_at`이 찍히는
        # 순서 때문에 다음 tick이 같은 실패를 한 번 더 세어 재발 횟수를 부풀린다.
        return
    touch_site_build_incident(
        db, incident, failed_run_id=newest_failed_run.id, observed_at=observed_at
    )


def _redispatch_operation_run(db, run: OperationRun, observed_at: datetime) -> bool:
    policy = _OPERATION_REDISPATCH_POLICIES.get(str(run.operation_type))
    dispatch = _validated_dispatch(db, run, policy)
    if dispatch is None:
        dispatch = _rebuild_dispatch(db, run, policy)
    if policy is None or dispatch is None:
        _fail_unsafe_operation_run(db, run, observed_at)
        return False
    # 다시 보낼 때마다 새 task id다. 그래야 `run.task_id == 워커 task id`가 '지금 유효한 사본'을
    # 한 가지 뜻으로 가리키고, 늦게 도착한 이전 사본은 실패가 아니라 건너뛰기(STALE)로 끝난다.
    # task id와 REQUESTED intent를 먼저 commit해야 즉시 실행된 worker의 별도 DB session도
    # 자기 task id를 보고 claim할 수 있다. version/task-id CAS는 같은 stale 사본을 든 다른
    # reconciler가 이미 전진한 실행을 다시 REQUESTED로 되돌리는 것을 막는다.
    previous_task_id = run.task_id
    previous_state = run.state
    previous_version = run.version
    # 상태를 REQUESTED로 바꾸기 전에 정한다 — 유실 추정 재배달만 예산을 쓴다.
    spent = 1 if _counts_against_redispatch_budget(run) else 0
    task_id = str(uuid.uuid4())
    prepared_version = previous_version + 1
    prepared = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run.id,
            OperationRun.task_id == previous_task_id,
            OperationRun.state == previous_state,
            OperationRun.version == previous_version,
        )
        .values(
            task_id=task_id,
            state=OperationRunState.REQUESTED,
            queued_at=None,
            not_before_at=None,
            completed_at=None,
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            safe_error_code=None,
            safe_error_message=None,
            redispatch_count=OperationRun.redispatch_count + spent,
            version=prepared_version,
        )
        .returning(OperationRun.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if prepared is None:
        return False
    run.task_id = task_id
    run.state = OperationRunState.REQUESTED
    run.queued_at = None
    run.not_before_at = None
    run.completed_at = None
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = None
    run.safe_error_message = None
    run.redispatch_count = _redispatch_count(run) + spent
    run.version = prepared_version
    db.commit()
    celery_app.send_task(
        policy.task_name,
        args=list(dispatch.task_args),
        queue=policy.queue,
        headers=_operation_run_dispatch_headers(policy, dispatch, run),
        task_id=task_id,
    )
    queued = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run.id,
            OperationRun.task_id == task_id,
            OperationRun.state == OperationRunState.REQUESTED,
            OperationRun.version == prepared_version,
        )
        .values(
            state=OperationRunState.QUEUED,
            queued_at=observed_at,
            version=prepared_version + 1,
        )
        .returning(OperationRun.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if queued is not None:
        run.state = OperationRunState.QUEUED
        run.queued_at = observed_at
        run.version = prepared_version + 1
    db.commit()
    return True


def _operation_run_dispatch_headers(
    policy: _RedispatchPolicy,
    dispatch: operation_run_payloads.DispatchPayload,
    run: OperationRun,
) -> dict[str, str]:
    """Rebuild the signed-dispatch seed headers for a lost OperationRun publish."""

    headers = build_dispatch_headers(expected_purpose(policy.task_name), dispatch.target_id)
    headers["operation_run_id"] = str(run.id)
    return headers


def _validated_dispatch(
    db,
    run: OperationRun,
    policy: _RedispatchPolicy | None,
) -> operation_run_payloads.DispatchPayload | None:
    if policy is None:
        return None
    try:
        dispatch = operation_run_payloads.parse_stored_dispatch(
            run.request_payload.get("_dispatch")
        )
    except operation_run_payloads.UnsafeDispatchPayload:
        return None
    if dispatch.queue != policy.queue or dispatch.target_type != policy.target_type:
        return None
    if not _args_match_policy(dispatch, policy):
        return None
    if run.hospital_id is None:
        return None
    match policy.target_type:
        case "hospital":
            return dispatch if dispatch.target_id == str(run.hospital_id) else None
        case "content_item":
            try:
                content_id = uuid.UUID(dispatch.target_id)
            except ValueError:
                return None
            item = db.get(ContentItem, content_id)
            if item is not None and item.hospital_id == run.hospital_id:
                return dispatch
            return None
    return None


def _rebuild_dispatch(
    db,
    run: OperationRun,
    policy: _RedispatchPolicy | None,
) -> operation_run_payloads.DispatchPayload | None:
    """Reconstruct an allowlisted dispatch from durable run facts, never stored IDs."""

    if policy is None or run.hospital_id is None:
        return None
    hospital_id = str(run.hospital_id)
    target_id = hospital_id
    if policy.target_type == "content_item":
        payload = _mapping(getattr(run, "request_payload", None))
        if payload.get("source_type") != "content_item":
            return None
        raw_target = payload.get("source_id")
        if not isinstance(raw_target, str):
            return None
        try:
            content_id = uuid.UUID(raw_target)
        except ValueError:
            return None
        item = db.get(ContentItem, content_id)
        if item is None or item.hospital_id != run.hospital_id:
            return None
        target_id = str(content_id)

    suffix: tuple[object, ...]
    if str(run.operation_type) == "GENERATE_MONTHLY_REPORT":
        period = _stored_period(run)
        if period is None:
            return None
        flags = _stored_monthly_report_flags(run)
        suffix = (*period, *flags)
    elif str(run.operation_type) == "RUN_SOV" and _stored_sov_mode(run) == "monthly":
        period = _stored_period(run)
        if period is None:
            return None
        suffix = ("monthly", *period)
    else:
        suffix = ()

    rebuilt = operation_run_payloads.DispatchPayload(
        policy.target_type,
        target_id,
        policy.queue,
        (target_id, *suffix),
    )
    return rebuilt if _args_match_policy(rebuilt, policy) else None


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _raw_stored_args(run: OperationRun) -> tuple[object, ...]:
    payload = _mapping(getattr(run, "request_payload", None))
    dispatch = _mapping(payload.get("_dispatch"))
    args = dispatch.get("task_args")
    return tuple(args) if isinstance(args, list) else ()


def _stored_period(run: OperationRun) -> tuple[int, int] | None:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    candidates: list[tuple[object, object]] = [
        (summary.get("period_year"), summary.get("period_month")),
        (payload.get("period_year"), payload.get("period_month")),
    ]
    raw_args = _raw_stored_args(run)
    if str(run.operation_type) == "RUN_SOV" and len(raw_args) >= 4:
        candidates.append((raw_args[2], raw_args[3]))
    elif len(raw_args) >= 3:
        candidates.append((raw_args[1], raw_args[2]))
    for value in (
        summary.get("measurement_month"),
        payload.get("measurement_month"),
        payload.get("source_id") if payload.get("source_type") == "MONTHLY_SCHEDULE" else None,
    ):
        if isinstance(value, str):
            try:
                year_text, month_text = value.split("-", 1)
                candidates.append((int(year_text), int(month_text)))
            except ValueError:
                pass
    for year, month in candidates:
        if (
            type(year) is int
            and 2000 <= year <= 2200
            and type(month) is int
            and 1 <= month <= 12
        ):
            return year, month
    return None


def _stored_monthly_report_flags(run: OperationRun) -> tuple[object, ...]:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    raw_args = _raw_stored_args(run)
    rebuild = any(
        value is True
        for value in (
            payload.get("rebuild"),
            summary.get("rebuild"),
            raw_args[3] if len(raw_args) >= 4 else None,
        )
    )
    automatic = any(
        value is True
        for value in (
            payload.get("automatic_recovery"),
            summary.get("automatic_recovery"),
            raw_args[4] if len(raw_args) >= 5 else None,
        )
    )
    if automatic:
        return True, True
    # 템플릿 갱신(숫자 그대로)을 일반 재생성으로 다시 보내면 지금 행으로 숫자를 다시 센다.
    # 저장된 인자 꼴 그대로(rebuild, False, template_only[, allow_recovery_pending]) 되살린다.
    if rebuild and len(raw_args) >= 6 and raw_args[4] is False and raw_args[5] is True:
        if len(raw_args) >= 7 and raw_args[6] is True:
            return True, False, True, True
        return True, False, True
    return (True,) if rebuild else ()


def _stored_sov_mode(run: OperationRun) -> str:
    payload = _mapping(getattr(run, "request_payload", None))
    summary = _mapping(getattr(run, "result_summary", None))
    raw_args = _raw_stored_args(run)
    if any(
        value == "monthly"
        for value in (
            summary.get("measurement_mode"),
            payload.get("measurement_mode"),
            raw_args[1] if len(raw_args) >= 2 else None,
        )
    ) or str(getattr(run, "idempotency_key", "") or "").startswith("monthly-sov:"):
        return "monthly"
    return "weekly"


def _args_match_policy(
    dispatch: operation_run_payloads.DispatchPayload,
    policy: _RedispatchPolicy,
) -> bool:
    args = dispatch.task_args
    if not args or args[0] != dispatch.target_id:
        return False
    return any(
        len(args) == len(suffix) + 1
        and all(
            _arg_matches_shape(value, expected)
            for value, expected in zip(args[1:], suffix, strict=True)
        )
        for suffix in policy.allowed_arg_suffixes
    )


def _arg_matches_shape(value: object, expected: object) -> bool:
    if expected is _INTEGER_ARG:
        return type(value) is int
    if expected is _UUID_ARG:
        return isinstance(value, str) and bool(_UUID_PATTERN.fullmatch(value))
    if expected is None:
        return value is None
    if expected is True:
        return value is True
    if expected is False:
        return value is False
    return value == expected


def _stop_operation_run(db, run: OperationRun, observed_at: datetime, code: str) -> bool:
    """자율 복구가 더 다루지 않는 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다.

    CAS는 읽은 task id·상태·판이 그대로일 때만 쓴다 — 같은 틱의 다른 reconciler나 늦게
    claim한 워커가 이미 전진시킨 실행을 덮지 않는다. 끝난 실행의 늦은 사본은 claim을 못 해
    STALE로 물러난다.
    """

    message = _RECOVERY_STOP_MESSAGES[code]
    stopped = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run.id,
            OperationRun.task_id.is_not_distinct_from(run.task_id),
            OperationRun.state == run.state,
            OperationRun.version == run.version,
        )
        .values(
            state=OperationRunState.FAILED,
            completed_at=observed_at,
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            not_before_at=None,
            safe_error_code=code,
            safe_error_message=message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if stopped is None:
        return False
    run.state = OperationRunState.FAILED
    run.completed_at = observed_at
    run.heartbeat_at = None
    run.lease_owner = None
    run.lease_expires_at = None
    run.not_before_at = None
    run.safe_error_code = code
    run.safe_error_message = message
    run.version += 1
    db.commit()
    hospital = (
        db.get(Hospital, run.hospital_id)
        if run.operation_type == "REBUILD_SITE" and run.hospital_id is not None
        else None
    )
    if hospital is not None:
        # 사이트 준비는 이미 병원 하나당 사고 한 건이 있다(H-13).
        _open_rebuild_site_incident(db, hospital, run, observed_at)
    else:
        _open_recovery_stop_incident(db, run, observed_at, code)
    return True


def _open_recovery_stop_incident(
    db, run: OperationRun, observed_at: datetime, code: str
) -> None:
    """원인 코드 하나에 사고 하나. 열려 있으면 횟수만 늘리고, 닫혔으면 새 에피소드로 다시 연다."""

    dedupe_key = build_incident_key(
        "autonomous_recovery", "operation_recovery_stop", code, IncidentFingerprint.UNKNOWN
    )
    closed = (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
    previous_state = db.scalar(select(Incident.state).where(Incident.dedupe_key == dedupe_key))
    message = _RECOVERY_STOP_MESSAGES[code]
    impact = "자동 복구가 멈춘 작업이 있어 진단·보고서·콘텐츠·사이트 준비 결과가 늦어질 수 있습니다."
    next_action = (
        "운영 관제의 실패한 작업에서 이 원인으로 멈춘 작업을 열어 ‘작업 다시 시도’를 누르세요. "
        "다시 멈추면 ‘개발팀 문의용 정보 복사’를 개발팀에 전달하세요."
    )
    incident = db.execute(
        pg_insert(Incident)
        .values(
            id=uuid.uuid4(),
            hospital_id=None,
            operation_run_id=run.id,
            dedupe_key=dedupe_key,
            incident_type=_RECOVERY_STOP_INCIDENT_TYPE,
            state=IncidentState.OPEN.value,
            severity=IncidentSeverity.HIGH.value,
            customer_impact=impact,
            source_type="OPERATION_RUN",
            source_id=code,
            safe_error_code=code,
            safe_error_message=message,
            next_action=next_action,
            admin_path="/operations",
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            created_at=observed_at,
            updated_at=observed_at,
        )
        .on_conflict_do_update(
            index_elements=[Incident.dedupe_key],
            set_={
                "state": IncidentState.OPEN.value,
                "operation_run_id": run.id,
                "last_seen_at": observed_at,
                "occurrence_count": Incident.occurrence_count + 1,
                "episode_seq": case(
                    (Incident.state.in_(closed), Incident.episode_seq + 1),
                    else_=Incident.episode_seq,
                ),
                "first_seen_at": case(
                    (Incident.state.in_(closed), observed_at),
                    else_=Incident.first_seen_at,
                ),
                "recovered_at": None,
                "acknowledged_at": None,
                "acknowledged_by_id": None,
                "version": Incident.version + 1,
                "updated_at": observed_at,
            },
        )
        .returning(Incident)
        .execution_options(populate_existing=True)
    ).scalar_one()
    if previous_state is not None and previous_state not in closed:
        return
    auto_assign_owner_sync(db, incident, observed_at=observed_at)
    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name="시스템 공통 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label=owner_label_sync(db, incident.owner_id),
        sla_label="확인 필요",
        hospital_id=None,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        problem=message,
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
    enqueue_notification_sync(
        db,
        build_open_incident_notification(projection, settings.ADMIN_BASE_URL),
        now=observed_at,
    )


def _fail_unsafe_operation_run(db, run: OperationRun, observed_at: datetime) -> None:
    run.state = OperationRunState.FAILED
    run.completed_at = observed_at
    run.safe_error_code = "UNSAFE_STORED_DISPATCH"
    run.safe_error_message = "저장된 작업 재실행 정보가 안전한 허용 목록과 맞지 않습니다."
    run.version += 1
    incident = Incident(
        id=uuid.uuid4(),
        hospital_id=run.hospital_id,
        operation_run_id=run.id,
        dedupe_key=build_incident_key(
            "autonomous_recovery",
            "operation_run",
            str(run.id),
            IncidentFingerprint.VALIDATION_FAILED,
        ),
        incident_type="UNSAFE_STORED_DISPATCH",
        state=IncidentState.OPEN.value,
        severity=IncidentSeverity.HIGH.value,
        customer_impact="저장된 운영 작업을 안전하게 재실행할 수 없어 자동 복구가 중단되었습니다.",
        source_type="OPERATION_RUN",
        source_id=str(run.id),
        safe_error_code=run.safe_error_code,
        safe_error_message=run.safe_error_message,
        next_action=(
            "운영 센터에서 작업 상세를 확인한 뒤 원 요청을 다시 실행해 주세요. "
            "같은 문제가 반복되면 개발팀에 작업 ID를 전달해 주세요."
        ),
        admin_path="/operations",
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
        version=1,
        episode_seq=1,
    )
    # 자동 복구가 멈춘 이 예외도 서비스·worker 경로와 같은 규칙으로 담당자를 정한다
    # (H-15). 여기만 배정을 건너뛰면 안전하지 않은 재실행은 언제나 주인이 없다.
    auto_assign_owner_sync(db, incident, observed_at=observed_at)
    db.add(incident)
    hospital = db.get(Hospital, run.hospital_id) if run.hospital_id is not None else None
    projection = IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name=hospital.name if hospital is not None else "병원 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        # 자동 배정된 담당자를 그대로 싣는다 — 주인이 정해진 예외를 Slack이 "미지정"으로
        # 알리면 아무도 자기 일로 보지 않는다.
        owner_label=owner_label_sync(db, incident.owner_id),
        sla_label="확인 필요",
        hospital_id=incident.hospital_id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        problem=incident.safe_error_message or "자동 복구가 중단되었습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )
    enqueue_notification_sync(
        db,
        build_open_incident_notification(projection, settings.ADMIN_BASE_URL),
        now=observed_at,
    )
