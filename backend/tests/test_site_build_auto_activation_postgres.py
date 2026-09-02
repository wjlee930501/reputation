"""Real PostgreSQL proof that STEP 5 auto-activation decides under a row lock.

`build_aeo_site`는 acks_late·자율 복구 재디스패치로 두 번 이상 돈다. 판정을 잠금 없이 읽은
스냅샷으로 하면 두 가지가 깨진다:

1. 판정과 전환 사이에 커밋된 Admin `/pause`가 덮여 PAUSED 병원이 되살아난다
   (CLAUDE.md STEP 5: "PAUSED는 어떤 재실행에도 자동으로 되살리지 않는다").
2. 동시에 도는 두 build가 둘 다 게이트를 통과해 감사행·활성화 알림이 중복된다.

두 시나리오를 실제 Postgres 세션으로 재현한다 — 잠금 동작은 SQLite/가짜 세션으로는
증명할 수 없다.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval
from app.services.hospital_activation import ACTIVATE_AUDIT_ACTION
from app.workers import tasks

_SYNC_URL = os.getenv(
    "TASK19_SYNC_DATABASE_URL",
    "postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test",
)

# 워커 스레드가 잠긴 행에서 **실제로 막혀 있는 것**을 확인할 때까지 기다린다. 고정
# sleep으로 대신하면 두 방향으로 틀린다: 느린 러너에서는 워커가 아직 SELECT에 닿지도
# 않아 "잠금에 막혔다"가 아니라 "그냥 나중에 읽었다"가 되어 회귀를 놓치고, 빠른
# 러너에서는 이미 막혀 있는데도 남은 시간을 그냥 버린다.
_LOCK_CONTENTION_TIMEOUT_SECONDS = 5.0
_LOCK_CONTENTION_POLL_SECONDS = 0.02

# 잠긴 hospitals 행을 기다리는 **다른** 세션이 있는지 서버에 직접 묻는다.
# `wait_event_type = 'Lock'`이 곧 "잠금 대기 중"이라는 서버의 사실이다. 행 잠금 대기는
# relation이 아니라 상대 트랜잭션(transactionid)에 걸리므로 pg_locks.relation으로는
# 거를 수 없어 대기 중인 쿼리문으로 대상을 좁힌다.
#
# `FOR UPDATE`까지 매칭하지 않는 이유: pg_stat_activity.query는
# track_activity_query_size(기본 1kB)에서 잘리는데, hospitals는 컬럼이 60개가 넘어
# SELECT 문이 그 한도를 넘는다 — 문장 끝의 FOR UPDATE는 잘려 나가 절대 매칭되지 않는다.
_LOCK_WAITER_SQL = text(
    """
    SELECT count(*)
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
      AND wait_event_type = 'Lock'
      AND query ILIKE '%hospitals%'
    """
)


def _wait_until_worker_blocks_on_the_hospital_row(sessions) -> None:
    """워커가 hospitals 행 잠금에서 막힐 때까지 기다린다(최대 5초)."""
    deadline = time.monotonic() + _LOCK_CONTENTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with sessions() as db:
            if int(db.execute(_LOCK_WAITER_SQL).scalar_one()) > 0:
                return
        time.sleep(_LOCK_CONTENTION_POLL_SECONDS)
    raise AssertionError(
        f"{_LOCK_CONTENTION_TIMEOUT_SECONDS}초 안에 hospitals 행 잠금을 기다리는 세션이 "
        "나타나지 않았다. build_aeo_site가 with_for_update 없이 읽고 있거나(회귀), "
        "워커 스레드가 그 SELECT에 도달하기 전에 죽었다는 뜻이다."
    )


def _seed(sessions, hospital_id: uuid.UUID, slug: str, status: HospitalStatus) -> None:
    with sessions() as db:
        db.add(
            Hospital(
                id=hospital_id,
                name="자동활성화 잠금 테스트의원",
                slug=slug,
                status=status,
                plan=Plan.PLAN_12,
                profile_complete=True,
                v0_report_done=True,
                site_built=True,
                site_live=False,
                treatments=[],
            )
        )
        db.commit()


def _cleanup(sessions, hospital_id: uuid.UUID) -> None:
    # admin_audit_logs는 DB 트리거가 DELETE를 막는 append-only 테이블이라 남겨 둔다.
    with sessions() as db:
        db.execute(
            delete(HospitalServiceInterval).where(
                HospitalServiceInterval.hospital_id == hospital_id
            )
        )
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()


def _patch_worker(monkeypatch, sessions, intents: list) -> None:
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", sessions)
    monkeypatch.setattr(
        tasks,
        "enqueue_onboarding_notification_sync",
        lambda _db, intent: intents.append(intent),
    )

    async def _noop_revalidate(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tasks, "trigger_hospital_site_revalidate_safe", _noop_revalidate)


def _activation_audit_count(sessions, hospital_id: uuid.UUID) -> int:
    with sessions() as db:
        return int(
            db.execute(
                select(func.count())
                .select_from(AdminAuditLog)
                .where(
                    AdminAuditLog.hospital_id == hospital_id,
                    AdminAuditLog.action == ACTIVATE_AUDIT_ACTION,
                )
            ).scalar_one()
        )


def test_pause_committed_mid_build_is_not_overwritten(monkeypatch) -> None:
    """판정 직전에 커밋된 `/pause`가 자동 활성화에 덮이지 않는다."""

    engine = create_engine(_SYNC_URL)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    slug = f"lock-pause-{hospital_id.hex[:10]}"
    intents: list = []
    _patch_worker(monkeypatch, sessions, intents)

    try:
        _seed(sessions, hospital_id, slug, HospitalStatus.PENDING_DOMAIN)

        pauser = sessions()
        errors: list[BaseException] = []
        worker = None
        try:
            # Admin `/pause` 트랜잭션이 행을 잠근 채 아직 커밋하지 않은 상태를 재현한다.
            paused = pauser.execute(
                select(Hospital).where(Hospital.id == hospital_id).with_for_update()
            ).scalar_one()
            paused.status = HospitalStatus.PAUSED
            pauser.flush()

            def _build() -> None:
                try:
                    tasks.build_aeo_site.run(str(hospital_id))
                except BaseException as exc:  # pragma: no cover - 실패 원인을 드러내기 위함
                    errors.append(exc)

            worker = threading.Thread(target=_build)
            worker.start()
            # 워커가 잠긴 행을 읽으려다 막혀 있는 것을 확인한 뒤에 pause를 커밋한다.
            _wait_until_worker_blocks_on_the_hospital_row(sessions)
            pauser.commit()
        finally:
            # 대기 확인이 실패해도 잠금은 반드시 풀어야 한다. 열린 채로 두면 워커도
            # `_cleanup`의 DELETE도 이 행에서 영원히 막혀, 실패가 실패로 보이지 않고
            # 테스트가 멈춘 것처럼 보인다.
            pauser.close()
        if worker is not None:
            worker.join(timeout=30)

        assert errors == []
        assert worker.is_alive() is False

        with sessions() as db:
            final = db.get(Hospital, hospital_id)
            assert final is not None
            assert final.status is HospitalStatus.PAUSED
            assert final.site_live is False

        assert _activation_audit_count(sessions, hospital_id) == 0
        assert intents == []
    finally:
        _cleanup(sessions, hospital_id)
        engine.dispose()


def test_two_concurrent_builds_activate_exactly_once(monkeypatch) -> None:
    """동시에 도는 두 build가 감사행도 활성화 알림도 하나만 남긴다."""

    engine = create_engine(_SYNC_URL)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    hospital_id = uuid.uuid4()
    slug = f"lock-double-{hospital_id.hex[:10]}"
    intents: list = []
    _patch_worker(monkeypatch, sessions, intents)

    try:
        _seed(sessions, hospital_id, slug, HospitalStatus.PENDING_DOMAIN)

        start = threading.Barrier(2)
        errors: list[BaseException] = []

        def _build() -> None:
            try:
                start.wait(timeout=10)
                tasks.build_aeo_site.run(str(hospital_id))
            except BaseException as exc:  # pragma: no cover - 실패 원인을 드러내기 위함
                errors.append(exc)

        threads = [threading.Thread(target=_build) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert all(thread.is_alive() is False for thread in threads)

        with sessions() as db:
            final = db.get(Hospital, hospital_id)
            assert final is not None
            assert final.status is HospitalStatus.ACTIVE
            assert final.site_live is True

        assert _activation_audit_count(sessions, hospital_id) == 1
        assert len(intents) == 1
    finally:
        _cleanup(sessions, hospital_id)
        engine.dispose()
