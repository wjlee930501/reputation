"""폴러 — DB를 큐로 쓰는 경로 (설계 §5-2 · §5-3 · T-10).

여기서 검증하는 것은 "브로커를 믿지 않아도 진단이 실행된다"이다.
접수의 celery publish가 실패해도, 워커가 죽어도, 60초 안에 회수되어야 한다.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.lead import SalesLead
from app.models.lead_diagnosis import ExecutionStatus, LeadDiagnosis
from app.workers import lead_diagnosis_tasks as leadgen_tasks

_slot_sequence = itertools.count(100)


async def _seed(
    session,
    *,
    execution_status=ExecutionStatus.PENDING.value,
    execution_attempts=0,
    running_since=None,
    created_at=None,
):
    lead = SalesLead(
        clinic_name="폴러테스트의원",
        clinic_type="내과",
        contact="010-0000-0000",
        privacy=True,
        source="AI_DIAGNOSIS",
    )
    session.add(lead)
    await session.flush()

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=uuid.uuid4().hex,
        subject_phone_hash=uuid.uuid4().hex,
        subject_hospital_name="폴러테스트의원",
        subject_region="수서역",
        slot_date=date(2026, 8, 20),
        slot_no=next(_slot_sequence),
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=settings.LEADGEN_REPEAT_COUNT,
        execution_status=execution_status,
        execution_attempts=execution_attempts,
        running_since=running_since,
    )
    session.add(diagnosis)
    await session.flush()
    if created_at is not None:
        diagnosis.created_at = created_at
        await session.flush()
    return diagnosis


@pytest.mark.asyncio
class TestClaim:
    async def test_only_one_worker_wins_the_claim(self, pg_async_session):
        """Celery는 acks_late에서 중복 실행을 막아주지 않는다 — DB가 소유권을 정한다."""
        diagnosis = await _seed(pg_async_session)

        first = await leagen_claim(pg_async_session, diagnosis.id)
        second = await leagen_claim(pg_async_session, diagnosis.id)

        assert first is True
        assert second is False

    async def test_claim_increments_attempts_and_stamps_running(self, pg_async_session):
        diagnosis = await _seed(pg_async_session)
        await leagen_claim(pg_async_session, diagnosis.id)

        await pg_async_session.refresh(diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.RUNNING.value
        assert diagnosis.execution_attempts == 1
        assert diagnosis.running_since is not None

    async def test_exhausted_attempts_cannot_be_claimed(self, pg_async_session):
        """무한 재시도는 비용이다 — 3회에서 멈추고 사람에게 넘긴다."""
        diagnosis = await _seed(
            pg_async_session, execution_attempts=leadgen_tasks.MAX_EXECUTION_ATTEMPTS
        )
        assert await leagen_claim(pg_async_session, diagnosis.id) is False

    async def test_running_diagnosis_cannot_be_claimed(self, pg_async_session):
        diagnosis = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.RUNNING.value,
            running_since=datetime.now(timezone.utc),
        )
        assert await leagen_claim(pg_async_session, diagnosis.id) is False


async def leagen_claim(session, diagnosis_id):
    return await leadgen_tasks._claim_for_execution(session, diagnosis_id)


@pytest.mark.asyncio
class TestReclaimStalled:
    async def test_a_worker_death_does_not_strand_a_diagnosis(self, pg_async_session):
        """하드 타임리밋으로 죽은 프로세스는 상태를 되돌리지 못한다 — 리스로 회수한다."""
        stale = datetime.now(timezone.utc) - timedelta(
            seconds=leadgen_tasks.DIAGNOSIS_LEASE_SECONDS + 60
        )
        diagnosis = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.RUNNING.value,
            execution_attempts=1,
            running_since=stale,
        )

        reclaimed = await leadgen_tasks._reclaim_stalled(pg_async_session)
        await pg_async_session.flush()
        await pg_async_session.refresh(diagnosis)

        assert reclaimed >= 1
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value
        assert diagnosis.running_since is None
        # 시도 횟수는 claim 시점에 이미 올라갔으므로 무한 재수확이 불가능하다.
        assert diagnosis.execution_attempts == 1

    async def test_a_healthy_running_diagnosis_is_not_stolen(self, pg_async_session):
        diagnosis = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.RUNNING.value,
            running_since=datetime.now(timezone.utc),
        )
        await leadgen_tasks._reclaim_stalled(pg_async_session)
        await pg_async_session.flush()
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.RUNNING.value


@pytest.mark.asyncio
class TestDeadLetter:
    async def test_exhausted_pending_becomes_failed(self, pg_async_session):
        """별도 DLQ 큐를 만들지 않는다 — FAILED 행 목록이 죽은 편지함이다."""
        diagnosis = await _seed(
            pg_async_session, execution_attempts=leadgen_tasks.MAX_EXECUTION_ATTEMPTS
        )
        rows = await leadgen_tasks._exhausted_to_failed(pg_async_session)
        await pg_async_session.flush()
        await pg_async_session.refresh(diagnosis)

        assert diagnosis.id in {row.id for row in rows}
        assert diagnosis.execution_status == ExecutionStatus.FAILED.value
        assert diagnosis.finished_at is not None
        assert diagnosis.error

    async def test_a_retriable_diagnosis_is_left_alone(self, pg_async_session):
        diagnosis = await _seed(pg_async_session, execution_attempts=1)
        await leadgen_tasks._exhausted_to_failed(pg_async_session)
        await pg_async_session.flush()
        await pg_async_session.refresh(diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value


@pytest.mark.asyncio
class TestDispatchOrder:
    async def test_oldest_pending_is_dispatched_first(self, pg_async_session):
        """LIFO면 적체 시 가장 오래 기다린 신청자가 영원히 굶는다 (설계 T-10).

        P95는 통과하면서 개별 사용자는 리포트를 못 받는 상태가 만들어진다.
        """
        now = datetime.now(timezone.utc)
        newest = await _seed(pg_async_session, created_at=now)
        oldest = await _seed(pg_async_session, created_at=now - timedelta(hours=2))
        middle = await _seed(pg_async_session, created_at=now - timedelta(hours=1))

        dispatched = await leadgen_tasks._pending_to_dispatch(pg_async_session)
        ours = [d for d in dispatched if d in {str(oldest.id), str(middle.id), str(newest.id)}]
        assert ours == [str(oldest.id), str(middle.id), str(newest.id)]

    async def test_exhausted_rows_are_not_dispatched(self, pg_async_session):
        exhausted = await _seed(
            pg_async_session, execution_attempts=leadgen_tasks.MAX_EXECUTION_ATTEMPTS
        )
        dispatched = await leadgen_tasks._pending_to_dispatch(pg_async_session)
        assert str(exhausted.id) not in dispatched

    async def test_dispatch_is_capped_per_tick(self, pg_async_session, monkeypatch):
        """한 tick이 워커를 통째로 점유하지 않게 한다."""
        monkeypatch.setattr(leadgen_tasks, "DRAIN_BATCH_SIZE", 2)
        for _ in range(4):
            await _seed(pg_async_session)
        dispatched = await leadgen_tasks._pending_to_dispatch(pg_async_session)
        assert len(dispatched) == 2


@pytest.mark.asyncio
class TestExecutionRecovery:
    async def test_a_crash_returns_the_row_to_pending(self, pg_async_session, monkeypatch):
        """RUNNING으로 남겨두고 리스 만료를 기다리면 15분 SLA 안에 재시도할 기회가 사라진다."""
        diagnosis = await _seed(pg_async_session)
        # 실행 경로가 rollback을 하므로 ORM 객체가 만료된다 — id를 미리 잡아둔다.
        diagnosis_id = diagnosis.id

        async def boom(session, diag):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(
            leadgen_tasks.lead_diagnosis_engine, "run_diagnosis_measurements", boom
        )

        class _Ctx:
            async def __aenter__(self):
                return pg_async_session

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(leadgen_tasks, "get_async_sessionmaker", lambda: _Ctx)

        with pytest.raises(RuntimeError):
            await leadgen_tasks._run_lead_diagnosis(str(diagnosis_id))

        row = (
            await pg_async_session.execute(
                select(LeadDiagnosis).where(LeadDiagnosis.id == diagnosis_id)
            )
        ).scalar_one()
        assert row.execution_status == ExecutionStatus.PENDING.value
        assert row.running_since is None
        assert "provider exploded" in (row.error or "")
        # 시도는 소모됐다 — 크래시가 무한 루프가 되지 않는다.
        assert row.execution_attempts == 1
