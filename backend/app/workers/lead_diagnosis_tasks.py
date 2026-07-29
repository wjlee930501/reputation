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

from sqlalchemy import func, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisResult,
    LeadReportArtifact,
    ReportStatus,
)
from app.services import (
    lead_delivery,
    lead_diagnosis_engine,
    lead_query_cache,
    lead_report,
    notifier,
)

logger = logging.getLogger(__name__)

_tls = threading.local()

# 좌초한 RUNNING을 회수하기까지의 여유. 태스크 soft_time_limit보다 넉넉히 잡아,
# 정상 실행 중인 진단을 다른 워커가 뺏어가지 않게 한다.
DIAGNOSIS_SOFT_TIME_LIMIT = 900
DIAGNOSIS_LEASE_SECONDS = DIAGNOSIS_SOFT_TIME_LIMIT + 300

# 실행 재시도 상한. 넘으면 FAILED로 종결하고 AE에게 넘긴다(= DLQ).
MAX_EXECUTION_ATTEMPTS = 3

# 리포트 렌더 재시도 상한. 소진하면 BLOCKED — 자동으로 빈 리포트를 보내지 않는다.
MAX_REPORT_ATTEMPTS = 3
REPORT_LEASE_SECONDS = 600

# 한 번의 드레인에서 발행할 최대 건수. 하루 20건이므로 여유롭지만, 폴러가 예상치 못한
# 적체를 만나도 한 tick에 워커를 통째로 점유하지 않게 한다.
DRAIN_BATCH_SIZE = 20


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


async def _claim_for_execution(session, diagnosis_id) -> bool:
    """PENDING → RUNNING 조건부 전이. 이긴 워커만 True.

    `rowcount == 0`은 다른 워커가 이미 가져갔거나 시도가 소진됐다는 뜻이다 —
    에러가 아니라 정상 경로이므로 조용히 종료한다.
    """
    result = await session.execute(
        update(LeadDiagnosis)
        .where(
            LeadDiagnosis.id == diagnosis_id,
            LeadDiagnosis.execution_status == ExecutionStatus.PENDING.value,
            LeadDiagnosis.execution_attempts < MAX_EXECUTION_ATTEMPTS,
        )
        .values(
            execution_status=ExecutionStatus.RUNNING.value,
            running_since=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            execution_attempts=LeadDiagnosis.execution_attempts + 1,
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def _run_lead_diagnosis(diagnosis_id: str) -> dict:
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        import uuid as _uuid

        pk = _uuid.UUID(str(diagnosis_id))
        if not await _claim_for_execution(session, pk):
            logger.info("lead diagnosis %s already claimed or exhausted", diagnosis_id)
            return {"skipped": "not_claimed"}

        diagnosis = (
            await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
        ).scalar_one_or_none()
        if diagnosis is None:  # pragma: no cover - claim이 성공했으면 존재한다
            return {"skipped": "missing"}

        try:
            return await lead_diagnosis_engine.run_diagnosis_measurements(session, diagnosis)
        except Exception as exc:
            # 실행 중 죽으면 RUNNING으로 남는다. 되돌려 놓아야 폴러가 다시 집는다 —
            # 리스 만료를 기다리면 15분 SLA 안에 재시도할 기회가 사라진다.
            await session.rollback()
            await session.execute(
                update(LeadDiagnosis)
                .where(LeadDiagnosis.id == pk)
                .values(
                    execution_status=ExecutionStatus.PENDING.value,
                    running_since=None,
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                )
            )
            await session.commit()
            raise


@celery_app.task(
    name="app.workers.lead_diagnosis_tasks.run_lead_diagnosis",
    bind=True,
    max_retries=0,  # 재시도는 폴러가 한다 — Celery 재시도와 겹치면 attempts가 두 배로 샌다.
    soft_time_limit=DIAGNOSIS_SOFT_TIME_LIMIT,
    time_limit=DIAGNOSIS_SOFT_TIME_LIMIT + 120,
)
def run_lead_diagnosis(self, diagnosis_id: str):
    return _run_async(_run_lead_diagnosis(diagnosis_id))


async def _claim_for_report(session, diagnosis_id) -> bool:
    """PENDING → BUILDING. 측정이 쓸 만한 상태일 때만 통과한다.

    `REPORTABLE_EXECUTION_STATUSES`가 게이트의 전부인 것은 `ExecutionStatus`의 정의
    덕분이다 — 어느 한 플랫폼이라도 성공 0이면 `PARTIAL`이 아니라 `FAILED`이므로,
    분모가 0인 플랫폼 칸을 가진 리포트가 만들어질 수 없다.
    """
    result = await session.execute(
        update(LeadDiagnosis)
        .where(
            LeadDiagnosis.id == diagnosis_id,
            LeadDiagnosis.report_status == ReportStatus.PENDING.value,
            LeadDiagnosis.execution_status.in_(sorted(REPORTABLE_EXECUTION_STATUSES)),
            LeadDiagnosis.report_attempts < MAX_REPORT_ATTEMPTS,
        )
        .values(
            report_status=ReportStatus.BUILDING.value,
            report_attempts=LeadDiagnosis.report_attempts + 1,
        )
    )
    await session.commit()
    return bool(result.rowcount)


async def _build_lead_report(diagnosis_id: str) -> dict:
    import uuid as _uuid

    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        pk = _uuid.UUID(str(diagnosis_id))
        if not await _claim_for_report(session, pk):
            logger.info("lead report %s not claimable", diagnosis_id)
            return {"skipped": "not_claimed"}

        diagnosis = (
            await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
        ).scalar_one_or_none()
        if diagnosis is None:  # pragma: no cover
            return {"skipped": "missing"}

        results = list(
            (
                await session.execute(
                    select(LeadDiagnosisResult).where(
                        LeadDiagnosisResult.diagnosis_id == pk
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

            session.add(
                LeadReportArtifact(
                    diagnosis_id=pk,
                    version=version,
                    storage_uri=storage_uri,
                    content_hash=lead_report.content_hash(pdf_bytes),
                    byte_size=len(pdf_bytes),
                    template_version=lead_report.TEMPLATE_VERSION,
                )
            )
            diagnosis.report_status = ReportStatus.READY.value
            await session.commit()
            return {"version": version, "bytes": len(pdf_bytes)}

        except Exception as exc:
            await session.rollback()
            # 시도가 남았으면 PENDING으로 되돌려 폴러가 다시 집게 한다.
            # 소진했으면 BLOCKED — 사람이 봐야 한다(자동으로 빈 리포트를 보내지 않는다).
            row = (
                await session.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == pk))
            ).scalar_one()
            exhausted = row.report_attempts >= MAX_REPORT_ATTEMPTS
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
        await notifier.notify_ops_alert(
            title="무료 진단 리포트 생성 실패 — 재시도 소진",
            message=(
                f"진단 `{diagnosis.id}` ({diagnosis.subject_hospital_name})의 리포트 생성이 "
                f"{MAX_REPORT_ATTEMPTS}회 모두 실패했습니다.\n"
                f"사유: {diagnosis.error or '알 수 없음'}\n"
                "신청자에게 메일이 나가지 않았습니다. Admin에서 확인해 주세요."
            ),
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


async def _reports_to_build(session) -> list[str]:
    rows = (
        await session.execute(
            select(LeadDiagnosis.id)
            .where(
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
            .where(
                LeadDiagnosis.report_status == ReportStatus.READY.value,
                LeadDiagnosis.delivery_status == DeliveryStatus.PENDING.value,
            )
            .order_by(LeadDiagnosis.created_at.asc())
            .limit(DRAIN_BATCH_SIZE)
        )
    ).scalars().all()
    return [str(row) for row in rows]


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
    rows = (
        await session.execute(
            select(LeadDiagnosis.id)
            .where(
                LeadDiagnosis.execution_status == ExecutionStatus.PENDING.value,
                LeadDiagnosis.execution_attempts < MAX_EXECUTION_ATTEMPTS,
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

        purged = await lead_query_cache.purge_expired(session)
        await session.commit()

        pending = await _pending_to_dispatch(session)
        reports = await _reports_to_build(session)
        deliveries = await _deliveries_to_send(session)
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

    for diagnosis in exhausted:
        try:
            await notifier.notify_ops_alert(
                title="무료 진단 측정 실패 — 재시도 소진",
                message=(
                    f"진단 `{diagnosis.id}` ({diagnosis.subject_hospital_name})의 측정이 "
                    f"{MAX_EXECUTION_ATTEMPTS}회 모두 실패했습니다.\n"
                    f"사유: {diagnosis.error or '알 수 없음'}\n"
                    "Admin에서 확인하고 필요하면 재실행해 주세요."
                ),
            )
        except Exception:  # noqa: BLE001 — 알림 실패가 드레인을 멈추지 않는다.
            logger.warning("lead diagnosis failure alert delivery failed")

    return {
        "dispatched": len(pending),
        "reports": len(reports),
        "deliveries": len(deliveries),
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
    if not settings.LEADGEN_DAILY_SLOTS:
        return {"skipped": "disabled"}
    return _run_async(_drain())
