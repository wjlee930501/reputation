"""주간 측정 기준표의 정책 버전 전환 — 게이트는 그대로 막고, 알림만 Report 1회로 낮춘다.

9월 기준표는 v2.1로 동결됐고 9/20 OpenRouter 전환 뒤 실행 기준은 v3.0이다. 매주 같은 이유로
막히는 이 차단은 재시도로 풀리지 않는다. 같은 병원·같은 월 기준표·같은 (동결, 현재) 버전 쌍은
처음 한 번만 Report로 알리고, 버전이 같은데 다른 실행 조건만 다르거나 버전을 읽을 수 없는
불일치는 지금처럼 Error로 남는다.

작업 본문(`run_sov_for_hospital`)부터 인시던트·outbox까지 실제 코드를 통과시키고, 인시던트와
outbox는 격리된 로컬 PostgreSQL(`INCIDENT_TEST_DATABASE_URL`)의 롤백 트랜잭션 안에 쓴다.
공급자 호출 경로와 외부 네트워크는 가드로 막고 호출 0건을 확인한다.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, NotificationOutbox, OperationRunState
from app.services import sov_engine
from app.services.notification_labels import ERROR_LABEL, REPORT_LABEL
from app.workers import tasks, weekly_sov_incident_control

DEFAULT_DATABASE_URL = "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
HOSPITAL_NAME = "장앤김테스트의원"
FROZEN_VERSION = "v2.1-neutral-auto-systemrole"
TRANSITION_TYPE = "SOV_MEASUREMENT_POLICY_TRANSITION"
DRIFT_CODE = "WEEKLY_SOV_MEASUREMENT_POLICY_DRIFT"
EXISTING_NEXT_ACTION = (
    "측정 질문 설정과 외부 측정 서비스 장애 여부를 확인한 뒤 이번 주 측정을 재시도하세요."
)
EXISTING_SAFE_MESSAGE = "월간 측정 기준과 현재 실행 기준이 달라 외부 AI 측정 호출을 시작하지 않았습니다."
WEEK_ONE = (2026, 9, 21)
WEEK_TWO = (2026, 9, 28)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


# ── isolation: local PostgreSQL in one rolled-back transaction ────────────────


@dataclass
class _Postgres:
    connection: AsyncConnection
    hospital_id: uuid.UUID

    def session(self) -> AsyncSession:
        return AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    def rows(self, model):
        async def load():
            async with self.session() as db:
                result = await db.scalars(
                    select(model)
                    .where(model.hospital_id == self.hospital_id)
                    .order_by(model.created_at, model.id)
                )
                return list(result.all())

        return tasks._run_async(load())


@pytest.fixture
def postgres(monkeypatch) -> Iterator[_Postgres]:
    url = os.getenv("INCIDENT_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    required = "INCIDENT_TEST_DATABASE_URL" in os.environ
    engine = create_async_engine(url, poolclass=NullPool)

    async def begin():
        connection = await engine.connect()
        transaction = await connection.begin()
        ready = await connection.scalar(
            text(
                "SELECT to_regclass('public.incidents') IS NOT NULL "
                "AND to_regclass('public.notification_outbox') IS NOT NULL"
            )
        )
        return connection, transaction, ready

    try:
        connection, transaction, ready = tasks._run_async(begin())
    except OSError as exc:
        tasks._run_async(engine.dispose())
        if required:
            pytest.fail(f"required incident PostgreSQL unavailable: {exc}", pytrace=False)
        pytest.skip("local incident PostgreSQL is unavailable")

    async def finish():
        await transaction.rollback()
        await connection.close()
        await engine.dispose()

    if not ready:
        tasks._run_async(finish())
        if required:
            pytest.fail("incident PostgreSQL must include the operations schema", pytrace=False)
        pytest.skip("local incident PostgreSQL lacks the operations schema")

    harness = _Postgres(connection=connection, hospital_id=uuid.uuid4())

    async def seed():
        async with harness.session() as db:
            db.add(
                Hospital(
                    id=harness.hospital_id,
                    name=HOSPITAL_NAME,
                    slug=f"sov-policy-{harness.hospital_id.hex[:12]}",
                )
            )
            await db.commit()

    tasks._run_async(seed())
    monkeypatch.setattr(weekly_sov_incident_control, "get_async_sessionmaker", lambda: harness.session)
    try:
        yield harness
    finally:
        tasks._run_async(finish())


# ── guards: no provider call and no non-local network ─────────────────────────


@dataclass
class _Guard:
    network: list[object] = field(default_factory=list)
    provider: list[str] = field(default_factory=list)


@pytest.fixture
def guard(monkeypatch) -> _Guard:
    observed = _Guard()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _external(address) -> bool:
        return isinstance(address, tuple) and address[0] not in _LOCAL_HOSTS

    def connect(sock, address):
        if _external(address):
            observed.network.append(address)
            raise ConnectionRefusedError("non-local network is blocked in this test")
        return real_connect(sock, address)

    def connect_ex(sock, address):
        if _external(address):
            observed.network.append(address)
            raise ConnectionRefusedError("non-local network is blocked in this test")
        return real_connect_ex(sock, address)

    def http_send(_client, request, *_args, **_kwargs):
        observed.network.append(str(request.url))
        raise RuntimeError("HTTP is blocked in this test")

    async def async_http_send(_client, request, *_args, **_kwargs):
        observed.network.append(str(request.url))
        raise RuntimeError("HTTP is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(httpx.Client, "send", http_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_http_send)

    def provider(name: str):
        def blocked(*_args, **_kwargs):
            observed.provider.append(name)
            raise AssertionError(f"{name} must not run behind the policy gate")

        return blocked

    async def blocked_cost_guard(*_args, **_kwargs):
        observed.provider.append("cost_guard.check_and_increment")
        raise AssertionError("cost guard must not reserve behind the policy gate")

    monkeypatch.setattr(tasks, "run_single_query", provider("run_single_query"))
    monkeypatch.setattr(tasks, "_start_measurement_run", provider("_start_measurement_run"))
    monkeypatch.setattr(tasks, "ensure_monthly_slots", provider("ensure_monthly_slots"))
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", blocked_cost_guard)
    return observed


# ── the weekly task body up to the policy gate ────────────────────────────────


class _TaskDB:
    def __init__(self, hospital):
        self.hospital = hospital

    def get(self, _model, _id):
        return self.hospital

    def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _manifest(snapshot: dict | None, *, month: int = 9):
    provenance: dict = {"chatgpt": "ALWAYS", "gemini": "CONFIGURED"}
    if snapshot is not None:
        provenance["measurement_protocol"] = snapshot
    return SimpleNamespace(
        cells=[SimpleNamespace(state="FAILED")],
        configured_platforms=["chatgpt", "gemini"],
        platform_provenance=provenance,
        period_year=2026,
        period_month=month,
    )


def _september_v21_snapshot() -> dict:
    """What the September manifest froze: v2.1, before `provider_gateway` existed."""

    snapshot = {**sov_engine.measurement_protocol(), "policy_version": FROZEN_VERSION}
    snapshot.pop("provider_gateway", None)
    return snapshot


def _run_week(monkeypatch, postgres: _Postgres, manifest, day: tuple[int, int, int]):
    hospital = SimpleNamespace(
        id=postgres.hospital_id,
        name=HOSPITAL_NAME,
        status=HospitalStatus.ACTIVE,
        competitors=[],
        region=["서울"],
    )
    retries: list[object] = []
    finished: list[tuple[OperationRunState, str]] = []

    def fake_retry(*, exc=None, countdown=None):
        retries.append(exc)
        raise RuntimeError("retry-called")

    task = SimpleNamespace(
        retry=fake_retry,
        request=SimpleNamespace(headers={}, id="worker-task", operation_run_claim_version=1),
    )
    spec = {
        "query_id": uuid.uuid4(),
        "query_text": "강남 대장항문외과 추천",
        "platform": "CHATGPT",
        "target_id": uuid.uuid4(),
        "variant_id": uuid.uuid4(),
        "manifest_cell": manifest.cells[0],
    }
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _TaskDB(hospital))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "_operation_run_claimed_or_legacy", lambda *_args: True)
    monkeypatch.setattr(tasks, "_sov_measurement_mode_from_operation_run", lambda *_args: "weekly")
    monkeypatch.setattr(tasks, "_build_measurement_specs", lambda **_kwargs: ([spec], 0))
    monkeypatch.setattr(tasks, "freeze_dispatch_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(tasks, "_pending_weekly_manifest_specs", lambda _manifest, specs: specs)
    monkeypatch.setattr(
        tasks,
        "_finish_sov_operation_run",
        lambda _db, _task, state, code, _message: finished.append((state, code)) or uuid.uuid4(),
    )
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(*day, 2, tzinfo="Asia/Seoul"),
    )
    body = getattr(tasks.run_sov_for_hospital.run, "__func__", tasks.run_sov_for_hospital.run)
    body(task, str(postgres.hospital_id))
    return retries, finished


def _texts(row: NotificationOutbox) -> str:
    blocks = row.payload.get("blocks") or []
    parts = [row.fallback_text, str(row.payload.get("text") or "")]
    for block in blocks:
        inner = block.get("text")
        if isinstance(inner, dict):
            parts.append(str(inner.get("text") or ""))
    return "\n".join(parts)


def _header(row: NotificationOutbox) -> str:
    return next(
        block["text"]["text"] for block in row.payload["blocks"] if block["type"] == "header"
    )


# ── tests ────────────────────────────────────────────────────────────────────


def test_intended_version_transition_first_week_sends_exactly_one_report(
    monkeypatch, postgres, guard
):
    _run_week(monkeypatch, postgres, _manifest(_september_v21_snapshot()), WEEK_ONE)

    rows = postgres.rows(NotificationOutbox)
    assert [row.notification_type for row in rows] == [TRANSITION_TYPE]
    (notice,) = rows
    assert notice.fallback_text.startswith(REPORT_LABEL)
    assert _header(notice).startswith(REPORT_LABEL)
    assert ERROR_LABEL not in _texts(notice)
    assert notice.channel == "SLACK"
    assert guard.network == []
    assert guard.provider == []


def test_same_hospital_month_and_version_pair_second_week_sends_nothing(
    monkeypatch, postgres, guard
):
    _run_week(monkeypatch, postgres, _manifest(_september_v21_snapshot()), WEEK_ONE)
    after_first_week = postgres.rows(NotificationOutbox)

    _run_week(monkeypatch, postgres, _manifest(_september_v21_snapshot()), WEEK_TWO)
    after_second_week = postgres.rows(NotificationOutbox)

    assert len(after_first_week) == 1
    assert [row.id for row in after_second_week] == [row.id for row in after_first_week]
    assert guard.network == []
    assert guard.provider == []


def test_report_body_drops_retry_advice_and_says_no_action_needed(
    monkeypatch, postgres, guard
):
    _run_week(monkeypatch, postgres, _manifest(_september_v21_snapshot()), WEEK_ONE)

    notices = [
        row for row in postgres.rows(NotificationOutbox) if row.notification_type == TRANSITION_TYPE
    ]
    assert len(notices) == 1
    body = _texts(notices[0])
    assert "재시도" not in body
    assert "조치가 필요 없습니다" in body
    assert "다음 달 기준표부터 자동 해소" in body
    assert FROZEN_VERSION in body
    assert sov_engine.MEASUREMENT_POLICY_VERSION in body

    (incident,) = postgres.rows(Incident)
    assert "재시도" not in incident.next_action
    assert "조치가 필요 없습니다" in incident.next_action
    assert guard.network == []


@pytest.mark.parametrize(
    "case",
    ["same_version_model_differs", "same_version_gateway_differs", "version_missing", "no_snapshot"],
)
def test_non_version_drift_stays_error_with_existing_copy(monkeypatch, postgres, guard, case):
    current = sov_engine.measurement_protocol()
    snapshot = {
        "same_version_model_differs": {**current, "openai_model_query": "openai/gpt-4o-legacy"},
        "same_version_gateway_differs": {**current, "provider_gateway": "direct"},
        "version_missing": {k: v for k, v in current.items() if k != "policy_version"},
        "no_snapshot": None,
    }[case]
    manifest = _manifest(snapshot)

    retries, finished = _run_week(monkeypatch, postgres, manifest, WEEK_ONE)

    assert retries == []
    assert finished == [(OperationRunState.FAILED, DRIFT_CODE)]
    rows = postgres.rows(NotificationOutbox)
    assert [row.notification_type for row in rows] == ["INCIDENT_OPEN"]
    assert rows[0].fallback_text.startswith(ERROR_LABEL)
    (incident,) = postgres.rows(Incident)
    assert incident.next_action == EXISTING_NEXT_ACTION
    assert incident.safe_error_message == EXISTING_SAFE_MESSAGE
    assert guard.network == []
    assert guard.provider == []

    from app.services.measurement_manifest_policy import manifest_policy_version_transition

    assert manifest_policy_version_transition(manifest) is None


def test_gate_still_blocks_and_records_each_week_while_notice_is_deduplicated(
    monkeypatch, postgres, guard
):
    outcomes = [
        _run_week(monkeypatch, postgres, _manifest(_september_v21_snapshot()), day)
        for day in (WEEK_ONE, WEEK_TWO)
    ]

    for retries, finished in outcomes:
        assert retries == []
        assert finished == [(OperationRunState.FAILED, DRIFT_CODE)]
    incidents = postgres.rows(Incident)
    assert sorted(incident.source_id for incident in incidents) == [
        f"{postgres.hospital_id}:2026-W39",
        f"{postgres.hospital_id}:2026-W40",
    ]
    for incident in incidents:
        assert incident.state == "OPEN"
        assert incident.severity == "HIGH"
        assert incident.incident_type == "WEEKLY_SOV_MEASUREMENT_FAILED"
        assert incident.safe_error_code == DRIFT_CODE
        assert incident.safe_error_message == EXISTING_SAFE_MESSAGE
    assert len(postgres.rows(NotificationOutbox)) == 1
    assert guard.provider == []
    assert guard.network == []
