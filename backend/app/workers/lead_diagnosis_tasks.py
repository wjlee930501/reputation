"""리드마그넷(1단) Celery 태스크.

**`tasks.py`와 분리한다.** 두 단은 데이터 모델도 큐도 예산도 공유하지 않으므로
(설계 §0) 태스크도 섞지 않는다. 이미 2,400줄인 파일에 더 얹을 이유도 없다.

## DB가 큐다 — outbox도 브로커 신뢰도 없다

접수는 DB에 커밋한 뒤 태스크를 발행하지만, **발행은 best-effort다.** 실패해도 삼킨다.
1분마다 도는 `drain_lead_diagnoses`가 `PENDING` 행을 다시 집기 때문이다.

이렇게 하면 "DB 커밋 성공 + 브로커 publish 실패 → 영구 QUEUED"라는 dual-write
문제가 아예 성립하지 않는다. 복구할 outbox가 없는 이유는 잃어버릴 수 있는 쓰기가
하나뿐이기 때문이다(설계 §5-3). 하루 20건 규모에서 폴링 지연 60초는 15분 예산 안에
충분히 들어간다.

실행 소유권은 **조건부 UPDATE claim**으로 잡는다. Celery는 `task_acks_late=True`에서
중복 실행을 막아주지 않는다 — 재배달이 정상 동작이다.
"""
import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

from celery import current_task
from sqlalchemy import String, and_, cast, exists, func, or_, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.lead import LEAD_SOURCE_AI_DIAGNOSIS, LEAD_SOURCE_INQUIRY, SalesLead
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
    LeadReportArtifact,
    ReportStatus,
)
from app.models.operations import Incident
from app.services import (
    inquiry_diagnosis,
    lead_delivery,
    lead_diagnosis_engine,
    lead_query_cache,
    lead_report,
    notifier,
)
from app.services.ops_incident_alerts import open_ops_incident, open_ops_incident_in
from app.workers.dispatch_auth import require_dispatch
from app.workers.lead_recovery_incidents import (
    mark_lead_recovery_failed,
    mark_lead_recovery_succeeded,
)
from app.workers.lead_report_writeback import finalize_lead_report_artifact

logger = logging.getLogger(__name__)

_tls = threading.local()

# 좌초한 RUNNING을 회수하기까지의 여유. 태스크 soft_time_limit보다 넉넉히 잡아,
# 정상 실행 중인 진단을 다른 워커가 뺏어가지 않게 한다.
DIAGNOSIS_SOFT_TIME_LIMIT = 900
DIAGNOSIS_LEASE_SECONDS = DIAGNOSIS_SOFT_TIME_LIMIT + 300

# 실행 재시도 상한. 넘으면 FAILED로 종결하고 AE에게 넘긴다(= DLQ). 첫 실패는 바로(일시 장애),
# 이후 1·2·4시간을 물러서 공급자 장애가 몇 시간 이어져도 사람 몫이 되지 않게 한다. 즉시 재시도만
# 두면 1분 폴러가 3회를 약 3분 안에 태워 10분짜리 장애도 사람에게 넘어갔다.
MAX_EXECUTION_ATTEMPTS = 5
EXECUTION_RETRY_BACKOFF = (
    timedelta(0),
    timedelta(hours=1),
    timedelta(hours=2),
    timedelta(hours=4),
)

# 리포트 렌더 재시도 상한. 소진하면 BLOCKED — 자동으로 빈 리포트를 보내지 않는다.
MAX_REPORT_ATTEMPTS = 3
REPORT_LEASE_SECONDS = 600

# 한 번의 드레인에서 발행할 최대 건수. 하루 20건이므로 여유롭지만, 폴러가 예상치 못한
# 적체를 만나도 한 tick에 워커를 통째로 점유하지 않게 한다.
DRAIN_BATCH_SIZE = 20

LEAD_NOTIFICATION_PENDING = "PENDING"
LEAD_NOTIFICATION_SENDING = "SENDING"
LEAD_NOTIFICATION_SENT = "SENT"
LEAD_NOTIFICATION_FAILED = "FAILED"

# 도입문의 접수 알림을 drain이 다시 보내는 기간. 넘으면 FAILED로 끝내고 운영 채널 사고로 올린다.
LEAD_INTAKE_NOTIFY_RETRY_WINDOW = timedelta(hours=1)
# 접수 API는 리드를 커밋한 뒤 진단을 만들고, 보내기 전에 NULL→SENDING을 커밋한다. 그래서 NULL은
# '아직 아무도 보내지 않음'이다 — 15분 넘은 NULL은 claim 전에 API가 죽은 행이라 PENDING처럼 다시
# 보낸다(두 번 갈 수 없다). 보낸 뒤 죽은 행은 SENDING으로 남아 다시 보내지 않고 만료된다.
LEAD_INTAKE_NULL_STATUS_GRACE = timedelta(minutes=15)
# SENDING은 창이 끝나도 이만큼 더 기다린 뒤에만 만료한다 — 59분에 집어 보내는 중인 행을
# 60분에 FAILED로 덮지 않는다(claim에서 기록까지는 몇 초다).
LEAD_INTAKE_SENDING_EXPIRY_MARGIN = timedelta(minutes=15)
# FAILED만 남고 사고가 없는 리드를 drain이 메우는 범위. NULL 만료도 이 범위만 본다 —
# 상태 칸이 생기기 전의 오래된 리드를 한꺼번에 사고로 올리지 않는다.
LEAD_SIDE_EFFECT_REPAIR_HORIZON = timedelta(days=7)

INTAKE_NOTICE_INCIDENT_TYPE = "LEAD_INTAKE_NOTIFICATION_FAILED"


def _is_inquiry():
    return func.upper(func.trim(SalesLead.source)) == LEAD_SOURCE_INQUIRY


def _stale_public_null_status(now: datetime):
    """접수 API가 상태를 쓰기 전에 죽은 공개 접수. Admin이 만든 리드(동의 없음)는 알림 대상이 아니다."""
    return and_(
        SalesLead.notification_status.is_(None),
        SalesLead.privacy.is_(True),
        SalesLead.created_at < now - LEAD_INTAKE_NULL_STATUS_GRACE,
    )


def _intake_notification_claimable(now: datetime):
    """무료 진단은 NULL/PENDING, 도입문의는 창 안의 PENDING(또는 15분 넘은 공개 접수의 NULL).

    도입문의의 갓 생긴 NULL은 접수 API가 곧 SENDING으로 잡을 행이다 — drain이 집으면 두 번 간다.
    SENDING(접수 API든 워커든 보내는 중이거나 보낸 뒤 죽음)은 어느 쪽도 다시 집지 않는다.
    """
    return or_(
        and_(
            SalesLead.source == LEAD_SOURCE_AI_DIAGNOSIS,
            or_(
                SalesLead.notification_status.is_(None),
                SalesLead.notification_status == LEAD_NOTIFICATION_PENDING,
            ),
        ),
        and_(
            _is_inquiry(),
            or_(
                SalesLead.notification_status == LEAD_NOTIFICATION_PENDING,
                _stale_public_null_status(now),
            ),
            SalesLead.purged_at.is_(None),
            SalesLead.created_at >= now - LEAD_INTAKE_NOTIFY_RETRY_WINDOW,
        ),
    )


def _intake_notice_expirable(now: datetime):
    """창 안에 보내지 못한 도입문의 접수 알림. 선택과 CAS UPDATE가 같은 술어를 쓴다."""
    window_closed = SalesLead.created_at < now - LEAD_INTAKE_NOTIFY_RETRY_WINDOW
    return and_(
        _is_inquiry(),
        SalesLead.purged_at.is_(None),
        or_(
            and_(SalesLead.notification_status == LEAD_NOTIFICATION_PENDING, window_closed),
            and_(
                _stale_public_null_status(now),
                window_closed,
                SalesLead.created_at >= now - LEAD_SIDE_EFFECT_REPAIR_HORIZON,
            ),
            and_(
                SalesLead.notification_status == LEAD_NOTIFICATION_SENDING,
                SalesLead.created_at
                < now - LEAD_INTAKE_NOTIFY_RETRY_WINDOW - LEAD_INTAKE_SENDING_EXPIRY_MARGIN,
            ),
        ),
    )


class LeadRecoveryRejected(RuntimeError):
    """Recovery lost its compare-and-swap gate or remained terminal."""


def execution_retry_not_before(attempts_done: int, now: datetime) -> datetime | None:
    """`attempts_done`번째 시도가 실패한 뒤 다음 시도가 가능한 시각. 소진이면 None."""
    if attempts_done >= MAX_EXECUTION_ATTEMPTS:
        return None
    index = min(max(attempts_done, 1), len(EXECUTION_RETRY_BACKOFF)) - 1
    return now + EXECUTION_RETRY_BACKOFF[index]


async def _requeue_with_backoff(
    session,
    diagnosis_id,
    *,
    attempts: int,
    error: str | None = None,
    now: datetime | None = None,
    from_failed: bool = False,
) -> None:
    """실패한 진단을 PENDING으로 되돌리되 물러설 시각을 남긴다.

    `cost_deferred_until`은 이름과 달리 '다음 실행 가능 시각'이다 — claim과 폴러가 이미 이 값을
    존중하고, 엔진은 비용 보류 사유가 있을 때만 지운다(물러서기는 사유를 남기지 않는다).
    """
    observed = now or datetime.now(timezone.utc)
    values: dict[str, object] = {
        "execution_status": ExecutionStatus.PENDING.value,
        "running_since": None,
        "cost_deferred_until": execution_retry_not_before(attempts, observed),
    }
    if error is not None:
        values["error"] = error
    predicates = [LeadDiagnosis.id == diagnosis_id]
    if from_failed:
        predicates.append(LeadDiagnosis.execution_status == ExecutionStatus.FAILED.value)
        values["finished_at"] = None
    await session.execute(update(LeadDiagnosis).where(*predicates).values(**values))
    await session.commit()


def _run_async(coro):
    """동기 Celery 태스크 안에서 코루틴을 돌린다 (tasks.py와 동일 규약).

    스레드당 루프를 재사용한다 — 매번 새 루프를 만들면 특정 루프에 묶인 비동기
    클라이언트(OpenAI/httpx)의 커넥션 풀이 깨진다.
    """
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop.run_until_complete(coro)


async def _notify_lead_intake(lead_id: str) -> dict:
    """접수 Slack을 한 번만 보내고 결과를 lead 행에 남긴다."""
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        return await _notify_lead_intake_in(session, lead_id)


async def _notify_lead_intake_in(session, lead_id: str) -> dict:
    import uuid as _uuid

    pk = _uuid.UUID(str(lead_id))
    claimed = (
        await session.execute(
            update(SalesLead)
            .where(SalesLead.id == pk, _intake_notification_claimable(datetime.now(timezone.utc)))
            .values(notification_status=LEAD_NOTIFICATION_SENDING, notification_error=None)
            .returning(SalesLead.id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await session.rollback()
        return {"skipped": "already_claimed_or_finished"}
    await session.commit()

    lead = (
        await session.execute(select(SalesLead).where(SalesLead.id == pk))
    ).scalar_one_or_none()
    if lead is None:  # pragma: no cover - claim 직후 삭제된 극단적 경쟁
        return {"skipped": "lead_deleted"}

    inquiry = (lead.source or "").strip().upper() == LEAD_SOURCE_INQUIRY
    if inquiry:
        # 접수 API와 같은 문구·같은 고지 범위(병원명·진료과·지역·마스킹 연락처·유입 경로·접수 시각).
        sent = await notifier.notify_lead_created(
            clinic_name=lead.clinic_name,
            contact=lead.contact,
            admin_url=f"{settings.ADMIN_BASE_URL.rstrip('/')}/leads",
            diagnosis_note=await inquiry_diagnosis.inquiry_diagnosis_note(session, lead),
            specialty=lead.specialty,
            region_keyword=lead.region_keyword,
            source_path=lead.source_path,
            created_at=lead.created_at,
        )
    else:
        sent = await notifier.notify_lead_diagnosis_received(
            clinic_name=lead.clinic_name,
            clinic_type=lead.clinic_type,
            region=lead.region_keyword or "",
            keywords=list(lead.core_keywords or []),
            contact=lead.contact,
            email=lead.email or "",
            slot_no=(
                await session.execute(
                    select(LeadDiagnosis.slot_no).where(LeadDiagnosis.lead_id == lead.id)
                )
            ).scalar_one(),
            admin_url=f"{settings.ADMIN_BASE_URL.rstrip('/')}/leads",
        )
    unknown = sent is notifier.SendOutcome.UNKNOWN
    sent = bool(sent)
    if sent:
        outcome = LEAD_NOTIFICATION_SENT
    elif inquiry and unknown:
        # 갔는지 모른다(응답 유실·5xx) — SENDING 그대로 두어 다시 보내지 않는다. 창+여유 뒤 만료가
        # '확인하지 못함' 사고로 끝낸다.
        outcome = LEAD_NOTIFICATION_SENDING
    else:
        # 도입문의는 창 안에서 다시 보낸다(만료는 drain이 FAILED+사고로 끝낸다). 무료 진단은 종전대로 FAILED.
        outcome = LEAD_NOTIFICATION_PENDING if inquiry else LEAD_NOTIFICATION_FAILED
    # CAS — 보내는 사이 만료 스윕이 이 행을 FAILED로 끝내고 사고를 열었다면 그 결정을 덮지 않는다.
    recorded = (
        await session.execute(
            update(SalesLead)
            .where(SalesLead.id == pk, SalesLead.notification_status == LEAD_NOTIFICATION_SENDING)
            .values(
                notification_status=outcome,
                notification_error=(
                    None
                    if sent
                    else notifier.DELIVERY_UNKNOWN_ERROR
                    if unknown
                    else "Slack/webhook delivery failed or is not configured."
                ),
            )
            .returning(SalesLead.id)
        )
    ).scalar_one_or_none()
    await session.commit()
    if recorded is None:
        logger.warning("lead intake notification %s finished after its row was settled", pk)
    return {"sent": sent, "lead_id": str(pk), "recorded": recorded is not None}


@celery_app.task(name="app.workers.lead_diagnosis_tasks.notify_lead_intake")
def notify_lead_intake(lead_id: str):
    """신청 응답을 Slack 네트워크 지연과 분리한다."""
    return _run_async(_notify_lead_intake(lead_id))


async def _claim_for_execution(
    session, diagnosis_id, recovery_expected_attempts: int | None = None
) -> bool:
    """PENDING → RUNNING 조건부 전이. 이긴 워커만 True.

    `rowcount == 0`은 다른 워커가 이미 가져갔거나 시도가 소진됐다는 뜻이다 —
    에러가 아니라 정상 경로이므로 조용히 종료한다.
    """
    recovery = recovery_expected_attempts is not None
    now = datetime.now(timezone.utc)
    # 갈음된 진단은 어느 경로로도 공급자를 부르지 않는다. AE가 잘못된 입력을 고쳐
    # 새 진단을 만든 뒤에도 옛 행이 PENDING이면 폴러가 그대로 돈을 쓴다.
    predicates = [LeadDiagnosis.id == diagnosis_id, LeadDiagnosis.superseded_at.is_(None)]
    if recovery:
        predicates.extend(
            (
                LeadDiagnosis.execution_status == ExecutionStatus.FAILED.value,
                LeadDiagnosis.execution_attempts == recovery_expected_attempts,
                LeadDiagnosis.report_status.notin_(
                    (ReportStatus.READY.value, ReportStatus.PURGED.value)
                ),
                LeadDiagnosis.delivery_status.notin_(
                    (DeliveryStatus.SENDING.value, DeliveryStatus.SENT.value)
                ),
            )
        )
    else:
        predicates.extend(
            (
                LeadDiagnosis.execution_status == ExecutionStatus.PENDING.value,
                LeadDiagnosis.execution_attempts < MAX_EXECUTION_ATTEMPTS,
                or_(
                    LeadDiagnosis.cost_deferred_until.is_(None),
                    LeadDiagnosis.cost_deferred_until <= now,
                ),
            )
        )
    result = await session.execute(
        update(LeadDiagnosis)
        .where(*predicates)
        .values(
            execution_status=ExecutionStatus.RUNNING.value,
            running_since=now,
            started_at=now,
            execution_attempts=LeadDiagnosis.execution_attempts + 1,
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def _run_lead_diagnosis(
    diagnosis_id: str, recovery_expected_attempts: int | None = None
) -> dict:
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        import uuid as _uuid

        pk = _uuid.UUID(str(diagnosis_id))
        if not await _claim_for_execution(session, pk, recovery_expected_attempts):
            logger.info("lead diagnosis %s already claimed or exhausted", diagnosis_id)
            if recovery_expected_attempts is not None:
                raise LeadRecoveryRejected("measurement recovery state changed")
            return {"skipped": "not_claimed"}

        diagnosis = (
            await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
        ).scalar_one_or_none()
        if diagnosis is None:  # pragma: no cover - claim이 성공했으면 존재한다
            return {"skipped": "missing"}
        # 롤백 뒤 비동기 지연 로드를 피하려고 이번 시도 번호를 먼저 잡아 둔다.
        attempts = int(diagnosis.execution_attempts or 1)

        try:
            result = await lead_diagnosis_engine.run_diagnosis_measurements(session, diagnosis)
        except Exception as exc:
            # 실행 중 죽으면 RUNNING으로 남는다. 되돌려 놓아야 폴러가 다시 집는다 —
            # 리스 만료를 기다리지 않고, 시도 번호에 맞춰 물러선 시각에 다시 잡는다.
            await session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:2000]
            if recovery_expected_attempts is not None:
                await session.execute(
                    update(LeadDiagnosis)
                    .where(LeadDiagnosis.id == pk)
                    .values(
                        execution_status=ExecutionStatus.FAILED.value,
                        running_since=None,
                        error=error,
                    )
                )
                await session.commit()
            else:
                await _requeue_with_backoff(session, pk, attempts=attempts, error=error)
            raise
        if (
            recovery_expected_attempts is not None
            and result.get("status") == ExecutionStatus.FAILED.value
        ):
            raise LeadRecoveryRejected("measurement recovery did not produce a usable result")

        # 확정 판정이 하한에 미달하면 엔진은 FAILED로 종결한다. 그런데 FAILED는 **수동**
        # 복구 대상이라(Admin에서 AE가 클릭), 일시적인 공급자 장애까지 사람 손을 기다리게
        # 된다. 시도가 남아 있으면 PENDING으로 되돌려 폴러가 자동으로 다시 집게 한다 —
        # 소진되면 그때 FAILED로 남고, 그 목록이 DLQ가 된다.
        #
        # 정책 드리프트는 배포 조건이 바뀌기 전에는 재큐하지 않는다. 비용 차단은 엔진이
        # 이미 PENDING+due로 바꾸고, 실제 공급자 호출이 없었으므로 claim 횟수도 복원한다.
        if (
            recovery_expected_attempts is None
            and result.get("status") == ExecutionStatus.FAILED.value
            and not result.get("blocked")
            and attempts < MAX_EXECUTION_ATTEMPTS
        ):
            await _requeue_with_backoff(session, pk, attempts=attempts, from_failed=True)
            logger.info(
                "lead diagnosis %s requeued for retry (attempt %s/%s): %s",
                diagnosis_id,
                diagnosis.execution_attempts,
                MAX_EXECUTION_ATTEMPTS,
                diagnosis.error,
            )
            return {**result, "requeued": True}

        # 재큐 없이 FAILED로 종결되는 진단은 여기가 마지막이다. `_exhausted_to_failed`의
        # DLQ 알림은 PENDING에서 소진된 것만 잡으므로, 이 경로를 알리지 않으면 신청자
        # 리포트가 **무통보로** 죽는다. 예산 차단은 엔진이 전용 알림을 이미 보냈다.
        if (
            recovery_expected_attempts is None
            and result.get("status") == ExecutionStatus.FAILED.value
            and result.get("blocked") != "cost_guard"
        ):
            try:
                if await lead_diagnosis_engine.is_superseded(diagnosis_id):
                    return result
                await open_ops_incident(
                    pipeline="lead_diagnosis",
                    object_type="diagnosis",
                    object_id=str(diagnosis_id),
                    incident_type="LEAD_DIAGNOSIS_FAILED",
                    safe_error_code="LEAD_DIAGNOSIS_FAILED",
                    problem="무료 진단 측정이 재시도 없이 실패로 종결되었습니다.",
                    customer_impact="신청자에게 진단 리포트를 전달할 수 없습니다.",
                    next_action="운영센터에서 원인을 확인하고 해소한 뒤 진단을 재실행하세요.",
                    source_type="LEAD_DIAGNOSIS",
                    hospital_name=diagnosis.subject_hospital_name,
                    actor="lead-diagnosis-worker",
                )
            except Exception:  # noqa: BLE001 — 알림 실패가 상태 확정을 되돌리지 않는다.
                logger.warning("lead diagnosis terminal-failure alert delivery failed")
        return result


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.run_lead_diagnosis",
    bind=True,
    max_retries=0,  # 재시도는 폴러가 한다 — Celery 재시도와 겹치면 attempts가 두 배로 샌다.
    soft_time_limit=DIAGNOSIS_SOFT_TIME_LIMIT,
    time_limit=DIAGNOSIS_SOFT_TIME_LIMIT + 120,
)
def run_lead_diagnosis(self, diagnosis_id: str):
    return _run_async(_run_lead_diagnosis(diagnosis_id))


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
    bind=True,
    max_retries=0,
    soft_time_limit=DIAGNOSIS_SOFT_TIME_LIMIT,
    time_limit=DIAGNOSIS_SOFT_TIME_LIMIT + 120,
)
def recover_lead_diagnosis_measurement(self, diagnosis_id: str, expected_attempts: int):
    return _run_async(
        _recover_lead_measurement(diagnosis_id, expected_attempts, _operation_run_id(self))
    )


async def _recover_lead_measurement(
    diagnosis_id: str, expected_attempts: int, operation_run_id
) -> dict:
    import uuid as _uuid

    diagnosis_uuid = _uuid.UUID(diagnosis_id)
    try:
        result = await _run_lead_diagnosis(diagnosis_id, expected_attempts)
    except Exception as exc:  # noqa: BLE001 — Celery 작업 경계에서 incident를 되연다.
        await mark_lead_recovery_failed(diagnosis_uuid, "MEASUREMENT", operation_run_id, str(exc))
        raise
    await mark_lead_recovery_succeeded(diagnosis_uuid, "MEASUREMENT")
    return result


async def _claim_for_report(
    session, diagnosis_id, recovery_expected_attempts: int | None = None
) -> bool:
    """PENDING → BUILDING. 측정이 쓸 만한 상태일 때만 통과한다.

    `REPORTABLE_EXECUTION_STATUSES`가 게이트의 전부인 것은 `ExecutionStatus`의 정의
    덕분이다 — 어느 한 플랫폼이라도 성공 0이면 `PARTIAL`이 아니라 `FAILED`이므로,
    분모가 0인 플랫폼 칸을 가진 리포트가 만들어질 수 없다.
    """
    recovery = recovery_expected_attempts is not None
    predicates = [
        LeadDiagnosis.id == diagnosis_id,
        # 갈음된 진단의 보고서는 더 만들지 않는다 (claim_for_execution과 같은 이유).
        LeadDiagnosis.superseded_at.is_(None),
        LeadDiagnosis.execution_status.in_(sorted(REPORTABLE_EXECUTION_STATUSES)),
    ]
    if recovery:
        # 복구는 생성 실패(BLOCKED)와 이미 만들어진 리포트(READY)를 모두 다시 만든다.
        # HTTP 경계(lead_recovery._ensure_recoverable)와 같은 집합이어야 한다 — 한쪽만
        # 넓히면 요청은 받아 놓고 워커가 조용히 물러나 사람이 영문을 모른 채 기다린다.
        predicates.extend(
            (
                LeadDiagnosis.report_status.in_(
                    (ReportStatus.BLOCKED.value, ReportStatus.READY.value)
                ),
                LeadDiagnosis.report_attempts == recovery_expected_attempts,
                # 고객에게 나간 리포트는 제자리 재생성 대상이 아니다
                # (lead_recovery._ensure_recoverable와 같은 집합).
                LeadDiagnosis.delivery_status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.INTERNAL.value)
                ),
            )
        )
    else:
        predicates.extend(
            (
                LeadDiagnosis.report_status == ReportStatus.PENDING.value,
                LeadDiagnosis.report_attempts < MAX_REPORT_ATTEMPTS,
            )
        )
    result = await session.execute(
        update(LeadDiagnosis)
        .where(*predicates)
        .values(
            report_status=ReportStatus.BUILDING.value,
            report_attempts=LeadDiagnosis.report_attempts + 1,
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def _build_lead_report(
    diagnosis_id: str, recovery_expected_attempts: int | None = None
) -> dict:
    import uuid as _uuid

    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        pk = _uuid.UUID(str(diagnosis_id))
        if not await _claim_for_report(session, pk, recovery_expected_attempts):
            logger.info("lead report %s not claimable", diagnosis_id)
            if recovery_expected_attempts is not None:
                raise LeadRecoveryRejected("report recovery state changed")
            return {"skipped": "not_claimed"}

        diagnosis = (
            await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
        ).scalar_one_or_none()
        if diagnosis is None:  # pragma: no cover
            return {"skipped": "missing"}

        # **최신 시도의 결과만 읽는다.** 측정은 재시도마다 18행을 새로 INSERT하고
        # 이전 시도 행을 지우지 않는다(감사·장애분석용으로 보존). diagnosis_id만으로
        # 긁으면 2회 시도한 진단이 "9번 중 N번"을 "18번 중 2N번"으로 인쇄하게 된다.
        results = list(
            (
                await session.execute(
                    select(LeadDiagnosisResult).where(
                        LeadDiagnosisResult.diagnosis_id == pk,
                        LeadDiagnosisResult.attempt_no == diagnosis.execution_attempts,
                    )
                )
            ).scalars().all()
        )

        try:
            payload = lead_report.build_lead_report_payload(
                diagnosis, results, generated_at=datetime.now(timezone.utc)
            )
            pdf_bytes = lead_report.render_lead_report_pdf(payload)

            latest = await session.scalar(
                select(func.max(LeadReportArtifact.version)).where(
                    LeadReportArtifact.diagnosis_id == pk
                )
            )
            version = int(latest or 0) + 1

            # 업로드가 먼저다 — 행을 먼저 쓰면 READY인데 파일이 없는 상태가 생긴다.
            storage_uri = lead_report.store_report_pdf(pk, version, pdf_bytes)

            artifact = LeadReportArtifact(
                diagnosis_id=pk,
                version=version,
                storage_uri=storage_uri,
                content_hash=lead_report.content_hash(pdf_bytes),
                byte_size=len(pdf_bytes),
                template_version=lead_report.TEMPLATE_VERSION,
            )
            finalized = await finalize_lead_report_artifact(
                session, pk, diagnosis.report_attempts, artifact
            )
            if not finalized:
                return {"skipped": "report_state_changed"}
            return {"version": version, "bytes": len(pdf_bytes)}

        except LeadRecoveryRejected:
            raise
        except Exception as exc:
            await session.rollback()
            # 시도가 남았으면 PENDING으로 되돌려 폴러가 다시 집게 한다.
            # 소진했으면 BLOCKED — 사람이 봐야 한다(자동으로 빈 리포트를 보내지 않는다).
            row = (
                await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
            ).scalar_one()
            if row.report_status == ReportStatus.PURGED.value:
                raise
            # 이미 낼 수 있는 리포트가 있으면 READY로 되돌린다. READY 상태에서 시작한
            # 재생성이 실패했을 때 PENDING/BLOCKED로 내리면, 멀쩡한 콜용 보고서를 가진
            # 진단이 "만들지 못했다"로 표시되고 폴러가 같은 실패를 되풀이한다.
            servable = await session.scalar(
                select(LeadReportArtifact.id)
                .where(
                    LeadReportArtifact.diagnosis_id == pk,
                    LeadReportArtifact.purged_at.is_(None),
                )
                .limit(1)
            )
            exhausted = servable is None and row.report_attempts >= MAX_REPORT_ATTEMPTS
            if servable is not None:
                row.report_status = ReportStatus.READY.value
            else:
                row.report_status = (
                    ReportStatus.BLOCKED.value if exhausted else ReportStatus.PENDING.value
                )
            row.error = f"리포트 생성 실패: {type(exc).__name__}: {exc}"[:2000]
            await session.commit()
            if exhausted:
                await _notify_report_blocked(row)
            raise


async def _notify_report_blocked(diagnosis: LeadDiagnosis) -> None:
    try:
        # 생성 중에 갈음된 진단은 아무도 다시 만들지 않는다 — 닫힐 수 없는 인시던트를 열지 않는다.
        if await lead_diagnosis_engine.is_superseded(diagnosis.id):
            return
        await open_ops_incident(
            pipeline="lead_report",
            object_type="diagnosis",
            object_id=str(diagnosis.id),
            incident_type="LEAD_REPORT_BLOCKED",
            safe_error_code="LEAD_REPORT_RETRIES_EXHAUSTED",
            problem="무료 진단 리포트 생성 재시도가 모두 실패했습니다.",
            customer_impact="신청자에게 리포트 메일을 보낼 수 없습니다.",
            next_action="운영센터에서 리포트 생성 상태를 확인하고 원인 해소 후 다시 실행하세요.",
            source_type="LEAD_REPORT",
            hospital_name=diagnosis.subject_hospital_name,
            actor="lead-report-worker",
        )
    except Exception:  # noqa: BLE001
        logger.warning("lead report blocked alert delivery failed")


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.build_lead_report",
    bind=True,
    max_retries=0,  # 재시도는 폴러가 한다 (실행 태스크와 같은 규약).
    soft_time_limit=300,
    time_limit=420,
)
def build_lead_report(self, diagnosis_id: str):
    return _run_async(_build_lead_report(diagnosis_id))


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
    bind=True,
    max_retries=0,
    soft_time_limit=300,
    time_limit=420,
)
def recover_lead_diagnosis_report(self, diagnosis_id: str, expected_attempts: int):
    return _run_async(
        _recover_lead_report(diagnosis_id, expected_attempts, _operation_run_id(self))
    )


async def _recover_lead_report(diagnosis_id: str, expected_attempts: int, operation_run_id) -> dict:
    import uuid as _uuid

    diagnosis_uuid = _uuid.UUID(diagnosis_id)
    try:
        result = await _build_lead_report(diagnosis_id, expected_attempts)
    except Exception as exc:  # noqa: BLE001 — Celery 작업 경계에서 incident를 되연다.
        await mark_lead_recovery_failed(diagnosis_uuid, "REPORT", operation_run_id, str(exc))
        raise
    if result.get("skipped") == "report_state_changed":
        rejection = LeadRecoveryRejected("report recovery state changed before write-back")
        await mark_lead_recovery_failed(
            diagnosis_uuid, "REPORT", operation_run_id, str(rejection)
        )
        raise rejection
    await mark_lead_recovery_succeeded(diagnosis_uuid, "REPORT")
    return result


def _operation_run_id(task):
    import uuid as _uuid

    headers = getattr(task.request, "headers", None) or {}
    raw = headers.get("operation_run_id")
    if not isinstance(raw, str):
        return None
    try:
        return _uuid.UUID(raw)
    except ValueError:
        return None


async def _reports_to_build(session) -> list[str]:
    rows = (
        await session.execute(
            select(LeadDiagnosis.id)
            .where(
                LeadDiagnosis.superseded_at.is_(None),
                LeadDiagnosis.report_status == ReportStatus.PENDING.value,
                LeadDiagnosis.execution_status.in_(sorted(REPORTABLE_EXECUTION_STATUSES)),
                LeadDiagnosis.report_attempts < MAX_REPORT_ATTEMPTS,
            )
            .order_by(LeadDiagnosis.created_at.asc())
            .limit(DRAIN_BATCH_SIZE)
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _reclaim_stalled_reports(session) -> int:
    """BUILDING으로 좌초한 리포트를 되돌린다. 워커가 죽으면 상태가 남는다."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=REPORT_LEASE_SECONDS)
    result = await session.execute(
        update(LeadDiagnosis)
        .where(
            LeadDiagnosis.report_status == ReportStatus.BUILDING.value,
            LeadDiagnosis.updated_at < cutoff,
        )
        .values(report_status=ReportStatus.PENDING.value)
    )
    return int(result.rowcount or 0)


async def _send_lead_report_email(diagnosis_id: str) -> dict:
    import uuid as _uuid

    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        pk = _uuid.UUID(str(diagnosis_id))
        diagnosis = (
            await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
        ).scalar_one_or_none()
        if diagnosis is None:  # pragma: no cover
            return {"skipped": "missing"}
        return await lead_delivery.deliver_report(session, diagnosis)


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.send_lead_report_email",
    bind=True,
    max_retries=0,  # 재시도는 스윕이 한다 — 같은 키를 유지해야 중복이 안 난다.
    soft_time_limit=120,
    time_limit=180,
)
def send_lead_report_email(self, diagnosis_id: str):
    return _run_async(_send_lead_report_email(diagnosis_id))


async def _deliveries_to_send(session) -> list[str]:
    """리포트는 준비됐는데 아직 발송을 시작하지 않은 진단."""
    rows = (
        await session.execute(
            select(LeadDiagnosis.id)
            .join(SalesLead, SalesLead.id == LeadDiagnosis.lead_id)
            .where(
                LeadDiagnosis.superseded_at.is_(None),
                LeadDiagnosis.report_status == ReportStatus.READY.value,
                LeadDiagnosis.delivery_status == DeliveryStatus.PENDING.value,
                func.upper(func.trim(SalesLead.source)) != LEAD_SOURCE_INQUIRY,
                func.trim(SalesLead.clinic_type) != "도입문의",
            )
            .order_by(LeadDiagnosis.created_at.asc())
            .limit(DRAIN_BATCH_SIZE)
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _lead_notifications_to_dispatch(session) -> list[str]:
    """API가 Redis dispatch·Slack에 실패해도 1분 폴러가 접수 알림을 회수한다."""
    rows = (
        await session.execute(
            select(SalesLead.id)
            .where(_intake_notification_claimable(datetime.now(timezone.utc)))
            .order_by(SalesLead.created_at.asc())
            .limit(DRAIN_BATCH_SIZE)
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _open_intake_notice_incident(session, lead_id, *, unconfirmed: bool = False) -> None:
    """도입문의 접수 알림 실패 사고. 호출자의 트랜잭션에 쓰고 커밋하지 않는다.

    `unconfirmed`는 SENDING으로 만료된 리드다 — 보냈지만 응답을 잃었거나 보내는 도중 죽어 Slack
    수신 여부를 모른다. 못 보낸 것으로 단정하지 않고 다시 보내지 않았다고 말한다.
    """
    if unconfirmed:
        problem = "도입문의 접수 알림이 Slack에 도착했는지 확인하지 못했습니다."
        customer_impact = (
            "담당자가 새 도입문의를 Slack으로 받지 못했을 수 있습니다. "
            "중복 알림을 피하려고 다시 보내지 않았습니다."
        )
    else:
        problem = "도입문의 접수 알림을 1시간 동안 보내지 못했습니다."
        customer_impact = "새 도입문의를 담당자가 Slack으로 받지 못했습니다."
    await open_ops_incident_in(
        session,
        pipeline="lead_intake_notification",
        object_type="lead",
        object_id=str(lead_id),
        incident_type=INTAKE_NOTICE_INCIDENT_TYPE,
        safe_error_code=INTAKE_NOTICE_INCIDENT_TYPE,
        problem=problem,
        customer_impact=customer_impact,
        next_action=(
            "상담 요청 화면에서 새 문의를 확인해 담당자를 정하세요. "
            "처리를 시작하면 자동으로 닫힙니다."
        ),
        source_type="SALES_LEAD",
        # 병원명·연락처를 사고 제목·Slack에 새로 싣지 않는다(처리방침 고지 범위 유지).
        hospital_name="도입문의",
        admin_path="/leads",
        actor="lead-intake-worker",
    )


async def _expired_inquiry_candidates(session, now: datetime) -> list:
    """만료할 리드를 호출자의 트랜잭션에서 `FOR UPDATE SKIP LOCKED`로 잠근다(tick당 상한).

    다른 트랜잭션(접수 API·발송 워커·다른 drain)이 잡은 행은 기다리지 않고 다음 tick으로 넘긴다 —
    행 잠금 하나가 30초 statement timeout씩 tick을 붙잡아 진단 dispatch를 늦추지 않는다.
    """
    return list(
        (
            await session.execute(
                select(SalesLead.id, SalesLead.notification_status)
                .where(_intake_notice_expirable(now))
                .order_by(SalesLead.created_at.asc())
                .limit(DRAIN_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )


async def _expire_inquiry_notice(
    session, lead_id, now: datetime, *, unconfirmed: bool = False
) -> bool:
    """한 리드의 FAILED와 그 사고·열림 알림을 savepoint 하나로 쓴다. 이미 끝난 행이면 False.

    UPDATE는 선택과 같은 술어의 CAS다 — 선택 뒤 발송 쪽이 SENT를 커밋했다면 덮지 않는다.
    사고 쓰기가 실패하면 savepoint가 상태도 되돌려 다음 tick이 다시 만료한다(상태만 남는 틈이
    없다). 커밋은 호출자가 한다.
    """
    try:
        async with session.begin_nested():
            expired = (
                await session.execute(
                    update(SalesLead)
                    .where(SalesLead.id == lead_id, _intake_notice_expirable(now))
                    .values(notification_status=LEAD_NOTIFICATION_FAILED)
                    .returning(SalesLead.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one_or_none()
            if expired is None:
                return False
            await _open_intake_notice_incident(session, lead_id, unconfirmed=unconfirmed)
        return True
    except Exception:  # noqa: BLE001 — 한 리드의 실패가 drain을 멈추지 않는다. 다음 tick이 다시 본다.
        logger.warning("lead intake notice expiry failed for %s", lead_id, exc_info=True)
        return False


async def _expire_inquiry_notifications(session, *, now: datetime | None = None) -> list:
    """창 안에 보내지 못한 도입문의 접수 알림을 FAILED와 운영 사고로 끝낸다. 끝낸 리드 id 목록.

    PENDING, 접수 API가 SENDING을 잡기 전에 죽은 공개 접수의 NULL, 보낸 뒤(또는 보내는 도중)
    죽어 갔는지 모르는 SENDING(창 + 여유 뒤 — 다시 보내지 않는다)을 본다. 파기된 리드는 알릴
    대상이 아니다. 후보 잠금부터 커밋까지 한 트랜잭션이다.
    """
    observed = now or datetime.now(timezone.utc)
    expired = []
    try:
        for lead_id, status in await _expired_inquiry_candidates(session, observed):
            # 잠근 행이라 상태가 바뀌지 않는다. SENDING 만료는 수신 불명이다.
            unconfirmed = status == LEAD_NOTIFICATION_SENDING
            if await _expire_inquiry_notice(session, lead_id, observed, unconfirmed=unconfirmed):
                expired.append(lead_id)
        await session.commit()
    except Exception:  # noqa: BLE001 — 다음 tick이 다시 본다.
        await session.rollback()
        logger.warning("lead intake notice expiry tick failed", exc_info=True)
        return []
    return expired


def _lacks_incident(kind: str):
    return ~exists().where(
        Incident.source_id == cast(SalesLead.id, String),
        Incident.incident_type == kind,
    )


async def _repair_missing_lead_incidents(session, *, now: datetime | None = None) -> int:
    """FAILED로 끝난 접수 알림·안내 문자인데 사고가 없는 최근 리드에 사고 하나를 연다.

    정상 경로는 상태와 사고를 한 커밋으로 쓰지만, 옛 코드가 남긴 행이나 사고 쓰기가 실패한 접수
    응답은 상태만 남는다. FAILED 행은 다시 읽히지 않으므로 여기서 메운다. 사람이 이미 맡은
    리드(상태 ≠ NEW)는 근거가 곧바로 닫을 일이라 열지 않는다. tick당 상한이 있다.
    """
    from app.services import inquiry_sms

    observed = now or datetime.now(timezone.utc)
    recent_untaken = (
        _is_inquiry(),
        SalesLead.purged_at.is_(None),
        func.coalesce(SalesLead.status, "NEW") == "NEW",
        SalesLead.created_at >= observed - LEAD_SIDE_EFFECT_REPAIR_HORIZON,
    )
    targets = (
        (
            and_(
                SalesLead.notification_status == LEAD_NOTIFICATION_FAILED,
                _lacks_incident(INTAKE_NOTICE_INCIDENT_TYPE),
            ),
            _open_intake_notice_incident,
        ),
        (
            and_(
                SalesLead.ack_sms_status == inquiry_sms.SMS_FAILED,
                _lacks_incident(inquiry_sms.SMS_FAILURE_INCIDENT_TYPE),
            ),
            lambda db, lead_id: inquiry_sms.open_failure_incident(
                db, lead_id, actor="lead-intake-worker"
            ),
        ),
    )
    repaired = 0
    for predicate, opener in targets:
        try:
            # 만료와 같은 꼴: 다른 트랜잭션이 잡은 리드는 기다리지 않고 다음 tick으로 넘긴다.
            lead_ids = (
                await session.execute(
                    select(SalesLead.id)
                    .where(*recent_untaken, predicate)
                    .order_by(SalesLead.created_at.asc())
                    .limit(DRAIN_BATCH_SIZE)
                    .with_for_update(of=SalesLead, skip_locked=True)
                )
            ).scalars().all()
            for lead_id in lead_ids:
                try:
                    async with session.begin_nested():
                        await opener(session, lead_id)
                    repaired += 1
                except Exception:  # noqa: BLE001 — 다음 tick이 다시 메운다.
                    logger.warning("lead side-effect incident repair failed for %s", lead_id)
            await session.commit()
        except Exception:  # noqa: BLE001 — 다음 tick이 다시 메운다.
            await session.rollback()
            logger.warning("lead side-effect incident repair tick failed", exc_info=True)
    return repaired


async def _reclaim_stalled(session) -> int:
    """워커가 죽어 RUNNING으로 좌초한 행을 PENDING으로 되돌린다.

    `task_reject_on_worker_lost`가 재배달을 해주지만, 하드 타임리밋으로 죽은
    프로세스는 상태를 되돌리지 못한다. `execution_attempts`는 claim 시점에 이미
    올라갔으므로 무한 재수확이 불가능하다.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=DIAGNOSIS_LEASE_SECONDS)
    result = await session.execute(
        update(LeadDiagnosis)
        .where(
            LeadDiagnosis.execution_status == ExecutionStatus.RUNNING.value,
            LeadDiagnosis.running_since.isnot(None),
            LeadDiagnosis.running_since < cutoff,
        )
        .values(execution_status=ExecutionStatus.PENDING.value, running_since=None)
    )
    return int(result.rowcount or 0)


async def _exhausted_to_failed(session) -> list[LeadDiagnosis]:
    """시도가 소진된 PENDING을 FAILED로 종결한다. 이 목록이 곧 DLQ다."""
    rows = (
        await session.execute(
            select(LeadDiagnosis).where(
                # 갈음된 행은 FAILED로 종결하지 않는다 — 사람이 이미 대체본을 만들었는데
                # DLQ와 인시던트가 열리면 없는 일을 운영자 큐에 올리는 셈이다.
                LeadDiagnosis.superseded_at.is_(None),
                LeadDiagnosis.execution_status == ExecutionStatus.PENDING.value,
                LeadDiagnosis.execution_attempts >= MAX_EXECUTION_ATTEMPTS,
            )
        )
    ).scalars().all()
    for diagnosis in rows:
        diagnosis.execution_status = ExecutionStatus.FAILED.value
        diagnosis.finished_at = datetime.now(timezone.utc)
        if not diagnosis.error:
            diagnosis.error = f"측정 재시도 {MAX_EXECUTION_ATTEMPTS}회 소진"
    return list(rows)


async def _pending_to_dispatch(session) -> list[str]:
    """오래된 것부터 집는다.

    LIFO로 두면 적체가 생겼을 때 가장 오래 기다린 신청자가 영원히 굶는다 —
    P95는 통과하면서 개별 사용자는 리포트를 못 받는 상태가 만들어진다.
    """
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(LeadDiagnosis.id)
            .where(
                LeadDiagnosis.superseded_at.is_(None),
                LeadDiagnosis.execution_status == ExecutionStatus.PENDING.value,
                LeadDiagnosis.execution_attempts < MAX_EXECUTION_ATTEMPTS,
                or_(
                    LeadDiagnosis.cost_deferred_until.is_(None),
                    LeadDiagnosis.cost_deferred_until <= now,
                ),
            )
            .order_by(LeadDiagnosis.created_at.asc())
            .limit(DRAIN_BATCH_SIZE)
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _drain() -> dict:
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        reclaimed = await _reclaim_stalled(session)
        reclaimed += await _reclaim_stalled_reports(session)
        exhausted = await _exhausted_to_failed(session)
        await session.commit()

    # 리드 한 건씩 커밋·롤백하므로 위의 진단 객체(exhausted)와 세션을 나눈다 — 롤백이 그 객체를 만료시킨다.
    async with sessionmaker_() as notice_session:
        expired_notices = await _expire_inquiry_notifications(notice_session)
        repaired_incidents = await _repair_missing_lead_incidents(notice_session)

    async with sessionmaker_() as session:
        purged = await lead_query_cache.purge_expired(session)
        await session.commit()

        pending = await _pending_to_dispatch(session)
        reports = await _reports_to_build(session)
        deliveries = await _deliveries_to_send(session)
        lead_notifications = await _lead_notifications_to_dispatch(session)
        stuck = await lead_delivery.sweep_stuck_deliveries(session)
        deliveries += [d for d in stuck["retriable"] if d not in deliveries]

    for diagnosis_id in pending:
        try:
            run_lead_diagnosis.delay(diagnosis_id)
        except Exception:  # noqa: BLE001 — 발행 실패는 다음 tick이 회수한다.
            logger.warning("lead diagnosis dispatch failed for %s", diagnosis_id)

    for diagnosis_id in reports:
        try:
            build_lead_report.delay(diagnosis_id)
        except Exception:  # noqa: BLE001
            logger.warning("lead report dispatch failed for %s", diagnosis_id)

    for diagnosis_id in deliveries:
        try:
            send_lead_report_email.delay(diagnosis_id)
        except Exception:  # noqa: BLE001
            logger.warning("lead delivery dispatch failed for %s", diagnosis_id)

    for lead_id in lead_notifications:
        try:
            notify_lead_intake.delay(lead_id)
        except Exception:  # noqa: BLE001 — 다음 tick이 PENDING을 다시 회수한다.
            logger.warning("lead intake notification dispatch failed for %s", lead_id)

    for delivery_id in stuck["abandoned"]:
        try:
            await open_ops_incident(
                pipeline="lead_delivery",
                object_type="delivery",
                object_id=str(delivery_id),
                incident_type="LEAD_DELIVERY_ABANDONED",
                safe_error_code="LEAD_DELIVERY_ABANDONED",
                problem="무료 진단 리포트 메일 발송이 재시도 소진 또는 멱등성 기한 초과로 중단되었습니다.",
                customer_impact="신청자가 진단 리포트 메일을 받지 못했습니다.",
                next_action="중복 발송 여부를 확인한 뒤 운영센터에서 수동 처리하세요.",
                source_type="LEAD_DELIVERY",
                actor="lead-delivery-worker",
            )
        except Exception:  # noqa: BLE001 — 알림 실패가 드레인을 멈추지 않는다.
            logger.warning("lead delivery abandon alert delivery failed")

    for diagnosis in exhausted:
        try:
            await open_ops_incident(
                pipeline="lead_diagnosis",
                object_type="diagnosis",
                object_id=str(diagnosis.id),
                incident_type="LEAD_DIAGNOSIS_RETRIES_EXHAUSTED",
                safe_error_code="LEAD_DIAGNOSIS_RETRIES_EXHAUSTED",
                problem="무료 진단 측정 재시도가 모두 실패했습니다.",
                customer_impact="신청자에게 진단 리포트를 전달할 수 없습니다.",
                next_action="운영센터에서 실패 상태를 확인하고 원인 해소 후 재실행하세요.",
                source_type="LEAD_DIAGNOSIS",
                hospital_name=diagnosis.subject_hospital_name,
                actor="lead-diagnosis-worker",
            )
        except Exception:  # noqa: BLE001 — 알림 실패가 드레인을 멈추지 않는다.
            logger.warning("lead diagnosis failure alert delivery failed")

    return {
        "dispatched": len(pending),
        "reports": len(reports),
        "deliveries": len(deliveries),
        "lead_notifications": len(lead_notifications),
        "expired_intake_notices": len(expired_notices),
        "repaired_incidents": repaired_incidents,
        "abandoned_deliveries": len(stuck["abandoned"]),
        "reclaimed": reclaimed,
        "failed": len(exhausted),
        "cache_purged": purged,
    }


@celery_app.task(name="app.workers.lead_diagnosis_tasks.drain_lead_diagnoses")
def drain_lead_diagnoses():
    """1분마다 — 폴러 · 좌초 회수 · 재시도 소진 종결 · 캐시 만료 삭제.

    태스크를 넷으로 쪼갤 물량이 아니다(하루 20건). 하나의 tick이 전부 처리한다.
    """
    require_dispatch(current_task, "drain-lead-diagnoses")
    if not settings.LEADGEN_DAILY_SLOTS:
        return {"skipped": "disabled"}
    return _run_async(_drain())
