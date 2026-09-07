"""V0 측정 체크포인트 — 재시도가 150건의 유료 측정을 다시 사지 않는다.

배경(2026-09-01 아키텍처 리뷰 §2-3, §7 6행):
`trigger_v0_report`는 15질의 × 2플랫폼 × 5반복 = 150 유료 호출을 낸 뒤 PDF·GCS·커밋을
진행한다. 측정 **이후** 단계가 실패하면 `self.retry`가 태스크를 맨 위부터 다시 돌렸고,
QueryMatrix만 멱등이었을 뿐 측정은 아니었다 — 최악의 경우 한 병원 진단에 450호출,
비용 가드 예약 3회. 여기 테스트는 그 비용이 다시 발생하지 않는다는 것을 **호출 횟수로**
고정한다. "재사용이 되긴 하는가"가 아니라 "공급자를 다시 부르지 않는가"가 대상이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import MeasurementObservationSlot, MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services.measurement_slots import protocol_fingerprint
from app.services.report_engine import GeneratedPdfArtifact
from app.workers import tasks, v0_checkpoint

# ──────────────────────────────────────────────────────────────────
# 최소 가짜 세션 — 태스크 본문이 실제로 쓰는 질의만 응답한다.
# ──────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarResult":
        return _ScalarResult(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._rows[0]

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _ScalarResult:
    """SQLAlchemy ScalarResult subset; deliberately excludes Result-only methods."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list[Any]:
        return list(self._rows)

    def one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """태스크가 던지는 SELECT를 대상 테이블로 구분해 in-memory 상태로 답한다."""

    def __init__(self, hospital: Hospital, queries: list[QueryMatrix]) -> None:
        self.hospital = hospital
        self.queries = queries
        self.measurement_runs: list[MeasurementRun] = []
        self.sov_records: list[SovRecord] = []
        self.measurement_slots: list[MeasurementObservationSlot] = []
        self.artifacts: list[MonthlyReportArtifact] = []
        self.reports: list[MonthlyReport] = []
        self.commits = 0
        self.status_on_refresh: HospitalStatus | None = None

    # -- context manager (SyncSessionPinnedConnection / SyncSessionLocal 대역) --
    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    # -- session API --
    def get(self, model: type, ident: uuid.UUID, **_kwargs: Any) -> Any:
        if model is Hospital and ident == self.hospital.id:
            return self.hospital
        if model is MeasurementRun:
            return next((row for row in self.measurement_runs if row.id == ident), None)
        if model is SovRecord:
            return next((row for row in self.sov_records if row.id == ident), None)
        return None

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if isinstance(obj, MeasurementRun):
            self.measurement_runs.append(obj)
        elif isinstance(obj, SovRecord):
            self.sov_records.append(obj)
        elif isinstance(obj, MonthlyReport):
            if obj.created_at is None:
                obj.created_at = datetime.now(UTC)
            self.reports.append(obj)
        elif isinstance(obj, MonthlyReportArtifact):
            self.artifacts.append(obj)

    def flush(self) -> None:
        return None

    def refresh(self, obj: Any, *, with_for_update: bool = False) -> None:
        assert obj is self.hospital
        assert with_for_update is True
        if self.status_on_refresh is not None:
            self.hospital.status = self.status_on_refresh

    def commit(self) -> None:
        self.commits += 1

    def execute(self, stmt: Any) -> _Result:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        is_count = "count(*)" in sql
        if "FROM query_matrix" in sql and "sov_records" not in sql:
            return _Result([len(self.queries)] if is_count else list(self.queries))
        if "FROM sov_records" in sql:
            if is_count:
                run_id = _bound_uuid(stmt)
                return _Result(
                    [sum(1 for r in self.sov_records if r.measurement_run_id == run_id)]
                )
            run_id = _bound_uuid(stmt)
            intents = {q.id: q.query_intent for q in self.queries}
            return _Result(
                [
                    (record, intents.get(record.query_id))
                    for record in self.sov_records
                    if record.measurement_run_id == run_id
                ]
            )
        if "FROM monthly_reports" in sql:
            return _Result([sum(1 for r in self.reports if r.report_type == "V0")])
        if "FROM measurement_runs" in sql:
            # 상태·완료시각 필터는 SQL이 담당한다. 여기서는 그 SQL이 실제로 그렇게
            # 컴파일되는지를 별도 테스트로 못 박고, 세션은 후보 목록만 돌려준다.
            compiled = stmt.compile(dialect=postgresql.dialect())
            if 1 in compiled.params.values():
                candidates = [
                    run
                    for run in self.measurement_runs
                    if run.status in v0_checkpoint.RESUMABLE_RUN_STATUSES
                ]
            else:
                candidates = [
                    run
                    for run in self.measurement_runs
                    if run.status in v0_checkpoint.REUSABLE_RUN_STATUSES
                    and run.completed_at is not None
                ]
            return _Result(
                sorted(candidates, key=lambda run: run.completed_at, reverse=True)
            )
        raise AssertionError(f"unexpected statement: {sql}")


def _bound_uuid(stmt: Any) -> uuid.UUID | None:
    for value in stmt.compile(dialect=postgresql.dialect()).params.values():
        if isinstance(value, uuid.UUID):
            return value
    return None


# ──────────────────────────────────────────────────────────────────
# 태스크 하네스
# ──────────────────────────────────────────────────────────────────


@dataclass
class Harness:
    session: FakeSession
    hospital: Hospital
    provider_calls: list[str]
    cost_reservations: list[int]
    pdf_calls: list[int]
    pdf_failures: list[bool]


def _query(text: str) -> QueryMatrix:
    q = QueryMatrix(query_text=text, query_intent="LOCAL", is_active=True)
    q.id = uuid.uuid4()
    return q


def _measurement_result(mentioned: bool) -> dict[str, Any]:
    return {
        "is_mentioned": mentioned,
        "verdict": "MATCHED" if mentioned else "NOT_MATCHED",
        "mention_rank": 1 if mentioned else None,
        "sentiment": "neutral",
        "raw_response": "답변 본문",
        "measurement_status": "SUCCESS",
        "measurement_method": "OPENAI_RESPONSES_WEB_SEARCH",
        "source_urls": [],
    }


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    hospital = Hospital(name="체크포인트병원", slug="checkpoint-clinic")
    hospital.id = uuid.uuid4()
    hospital.status = HospitalStatus.ONBOARDING
    hospital.profile_complete = True
    hospital.v0_report_done = False
    hospital.region = ["노원구"]
    hospital.specialties = ["정형외과"]
    hospital.keywords = ["무릎"]
    hospital.competitors = []

    queries = [_query("노원구 정형외과 추천"), _query("노원구 무릎")]
    for query in queries:
        query.hospital_id = hospital.id
    session = FakeSession(hospital, queries)
    provider_calls: list[str] = []
    cost_reservations: list[int] = []
    pdf_calls: list[int] = []
    pdf_failures: list[bool] = [False]

    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(tasks, "SyncSessionPinnedConnection", lambda: session)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        tasks, "acquire_hospital_advisory_session_lock_sync", lambda *_a, **_k: None
    )
    monkeypatch.setattr(tasks, "acquire_hospital_advisory_lock_sync", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "release_v0_session_lock", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_seed_query_targets_from_matrix_sync", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "enqueue_onboarding_notification_sync", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks.build_aeo_site, "apply_async", lambda *_a, **_k: None, raising=False
    )

    async def _allow(_category: str, count: int = 1):
        cost_reservations.append(count)
        return type("Decision", (), {"allowed": True, "reason": None})()

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", _allow)

    def _ensure_slots(
        db,
        *,
        hospital_id,
        measurement_run_id,
        query_id,
        platform,
        repeat_count,
        protocol,
    ):
        rows = [
            slot
            for slot in db.measurement_slots
            if slot.measurement_run_id == measurement_run_id
            and slot.query_id == query_id
            and slot.platform == platform
        ]
        if rows:
            return rows
        for repeat_no in range(1, repeat_count + 1):
            slot = MeasurementObservationSlot(
                scope="V0",
                hospital_id=hospital_id,
                measurement_run_id=measurement_run_id,
                query_id=query_id,
                platform=platform,
                repeat_no=repeat_no,
                protocol_hash=protocol_fingerprint(protocol),
                answer_status="PENDING",
                answer_attempt_count=0,
                judgment_status="PENDING",
                judgment_attempt_count=0,
                version=1,
            )
            slot.id = uuid.uuid4()
            db.measurement_slots.append(slot)
            rows.append(slot)
        return rows

    def _execute_slot(db, *, slot, query_text, **_kwargs):
        if tasks.slot_is_terminal(slot):
            record = db.get(SovRecord, slot.sov_record_id)
            assert record is not None
            return tasks._result_from_sov_record(record)
        provider_calls.append(f"{slot.platform}:{query_text}:{slot.repeat_no}")
        # The production path owns one answer and one judgment reservation per
        # unresolved slot. This harness records that split while keeping its fake
        # provider deliberately small.
        cost_reservations.extend([1, 1])
        result = _measurement_result(slot.repeat_no % 2 == 1)
        record = tasks._build_sov_record_from_result(
            hospital_id=slot.hospital_id,
            query_id=slot.query_id,
            measurement_run_id=slot.measurement_run_id,
            platform=slot.platform,
            result=result,
        )
        db.add(record)
        slot.answer_status = "RECEIVED"
        slot.raw_response = result["raw_response"]
        slot.judgment_status = "CONFIRMED"
        slot.judgment_input_fingerprint = tasks.judgment_input_fingerprint(
            hospital_identity=str(slot.hospital_id),
            hospital_name=hospital.name,
            response_text=result["raw_response"],
            region=hospital.region[0],
            competitors=[],
            policy=tasks.sov_engine.measurement_protocol(),
        )
        slot.sov_record_id = record.id
        return result

    monkeypatch.setattr(tasks, "ensure_v0_slots", _ensure_slots)
    monkeypatch.setattr(tasks, "_execute_paid_observation_slot", _execute_slot)
    monkeypatch.setattr(
        tasks,
        "slots_for_run",
        lambda db, run_id: [
            slot for slot in db.measurement_slots if slot.measurement_run_id == run_id
        ],
    )

    def _fake_pdf(**kwargs):
        pdf_calls.append(kwargs.get("repeat_count"))
        if pdf_failures[0]:
            raise RuntimeError("WeasyPrint가 렌더에 실패했습니다")
        return GeneratedPdfArtifact(
            path="/tmp/reports/v0.pdf",
            sha256="a" * 64,
            byte_size=1024,
            validation_metadata={
                "validation_version": "v0-pdf-v1",
                "validation_source": "SYSTEM",
                "sha256": "a" * 64,
                "byte_size": 1024,
            },
        )

    monkeypatch.setattr(tasks, "generate_pdf_report", _fake_pdf)

    return Harness(
        session=session,
        hospital=hospital,
        provider_calls=provider_calls,
        cost_reservations=cost_reservations,
        pdf_calls=pdf_calls,
        pdf_failures=pdf_failures,
    )


def _run_task(hospital_id: uuid.UUID, *, operation_run_id: uuid.UUID | None) -> Any:
    task = tasks.trigger_v0_report
    headers = {"operation_run_id": str(operation_run_id)} if operation_run_id else {}
    task.push_request(headers=headers, retries=0, called_directly=True)
    try:
        return task.run(str(hospital_id))
    finally:
        task.pop_request()


def test_chunk_continuation_reuses_same_run_and_completed_slots(
    harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_run_id = uuid.uuid4()
    monkeypatch.setattr(
        tasks,
        "_v0_chunk_deadline_reached",
        lambda _started_at: len(harness.provider_calls) >= 3,
    )

    with pytest.raises(tasks.V0MeasurementResumable):
        monkeypatch.setattr(
            tasks.trigger_v0_report,
            "retry",
            lambda **kwargs: (_ for _ in ()).throw(kwargs["exc"]),
        )
        _run_task(harness.hospital.id, operation_run_id=operation_run_id)

    run = harness.session.measurement_runs[0]
    assert len(harness.provider_calls) == 3
    assert run.query_count == 10
    assert run.success_count == 3
    assert run.failure_count == 0
    assert run.error_summary["pending_slots"] == 7

    monkeypatch.setattr(tasks, "_v0_chunk_deadline_reached", lambda _started_at: False)
    _run_task(harness.hospital.id, operation_run_id=operation_run_id)

    assert len(harness.session.measurement_runs) == 1
    assert len(harness.provider_calls) == 10
    assert len(harness.cost_reservations) == 20
    assert harness.hospital.v0_report_done is True


def test_v0_resume_uses_frozen_platform_and_judgment_context() -> None:
    hospital_id = uuid.uuid4()
    hospital = Hospital(name="변경 후 병원", slug="changed")
    hospital.id = hospital_id
    hospital.region = ["변경 지역"]
    hospital.competitors = ["변경 경쟁사"]
    run = MeasurementRun(
        hospital_id=hospital_id,
        run_label=tasks.V0_MEASUREMENT_RUN_LABEL,
        status="RUNNING",
        config={
            "model_names": {
                "chatgpt": "frozen-openai",
                "gemini": "frozen-gemini",
            },
            "judgment_context": {
                "hospital_identity": str(hospital_id),
                "hospital_name": "동결 병원",
                "region": "동결 지역",
                "competitors": ["동결 경쟁사"],
            },
        },
    )

    assert tasks._v0_platforms_from_run(run) == ["chatgpt", "gemini"]
    assert tasks._v0_resume_judgment_context(run, hospital, [], {}) == {
        "hospital_identity": str(hospital_id),
        "hospital_name": "동결 병원",
        "region": "동결 지역",
        "competitors": ["동결 경쟁사"],
    }


def test_v0_resume_uses_frozen_query_text_after_tracking_row_changes(harness) -> None:
    query = harness.session.queries[0]
    snapshot = tasks._v0_query_snapshot(harness.session.queries)
    original_text = query.query_text
    original_intent = query.query_intent
    query.query_text = "나중에 변경된 질문"
    query.query_intent = "BRAND"

    resumed = tasks._v0_queries_from_snapshot(
        harness.session,
        snapshot,
        hospital_id=harness.hospital.id,
    )

    assert resumed is not None
    assert (resumed[0].id, resumed[0].query_text, resumed[0].query_intent) == (
        query.id,
        original_text,
        original_intent,
    )


def test_v0_policy_drift_stops_before_an_incomplete_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = tasks.sov_engine.measurement_protocol()
    changed = {**frozen, "judge_model": "different-judge"}
    monkeypatch.setattr(tasks.sov_engine, "measurement_protocol", lambda: changed)

    with pytest.raises(tasks.V0MeasurementPolicyDrift):
        tasks._require_v0_execution_policy(frozen, ["chatgpt"])


def test_v0_workflow_failure_budget_survives_dispatch_kwargs_loss(harness) -> None:
    run = MeasurementRun(
        hospital_id=harness.hospital.id,
        run_label=tasks.V0_MEASUREMENT_RUN_LABEL,
        status="RUNNING",
        config={"workflow_failure_count": 2},
    )
    harness.session.add(run)

    assert tasks._v0_workflow_failure_count(run) == 2
    tasks._persist_v0_workflow_failure_count(run.id, 3)
    assert tasks._v0_workflow_failure_count(run) == 3

    run.config = {"workflow_failure_count": "corrupt"}
    assert (
        tasks._v0_workflow_failure_count(run)
        == tasks.V0_CONTINUATION_MAX_RETRIES
    )


# ──────────────────────────────────────────────────────────────────
# (a) PDF 실패 후 재시도는 다시 측정하지 않는다
# ──────────────────────────────────────────────────────────────────


def test_retry_after_pdf_failure_reuses_the_measurement_and_finishes_the_report(harness):
    operation_run_id = uuid.uuid4()

    harness.pdf_failures[0] = True
    with pytest.raises(RuntimeError):
        _run_task(harness.hospital.id, operation_run_id=operation_run_id)

    first_attempt_calls = len(harness.provider_calls)
    assert first_attempt_calls == 10, "첫 시도는 질의 × 플랫폼 × 반복 슬롯만큼 답변을 산다"
    assert harness.hospital.v0_report_done is False
    assert harness.session.reports == []

    harness.pdf_failures[0] = False
    _run_task(harness.hospital.id, operation_run_id=operation_run_id)

    assert len(harness.provider_calls) == first_attempt_calls, (
        "재시도가 공급자를 다시 불렀다 — 체크포인트가 동작하지 않는다"
    )
    assert len(harness.cost_reservations) == 20, "완료 슬롯의 비용 예약이 재시도에서 반복됐다"
    assert len(harness.session.measurement_runs) == 1, "재시도가 새 측정 실행을 만들었다"
    assert harness.hospital.v0_report_done is True
    assert len(harness.session.reports) == 1
    report = harness.session.reports[0]
    assert report.report_type == "V0"
    # 재사용된 측정으로 계산해도 숫자는 같아야 한다(10건 중 6건 언급 = 60%).
    assert report.sov_summary["sov_pct"] == 60.0
    assert report.sov_summary["platforms"] == ["chatgpt"]
    assert report.sov_summary["baseline_basis"]["measurement_run_id"] == str(
        harness.session.measurement_runs[0].id
    )
    assert report.sov_summary["baseline_basis"]["measurement_protocol"]
    assert report.sov_summary["baseline_basis"]["query_snapshot"] == [
        {
            "query_id": str(query.id),
            "query_text": query.query_text,
            "query_intent": query.query_intent,
        }
        for query in harness.session.queries
    ]
    assert harness.pdf_calls[-1] == tasks.V0_REPEAT_COUNT


# ──────────────────────────────────────────────────────────────────
# (b) 새 트리거는 여전히 측정한다
# ──────────────────────────────────────────────────────────────────


def test_a_fresh_trigger_still_measures(harness):
    _run_task(harness.hospital.id, operation_run_id=uuid.uuid4())

    assert len(harness.provider_calls) == 10
    assert harness.cost_reservations == [1, 1] * 10
    assert len(harness.session.measurement_runs) == 1
    assert harness.session.measurement_runs[0].status == "COMPLETED"
    assert harness.hospital.v0_report_done is True


@pytest.mark.parametrize("late_status", [HospitalStatus.ACTIVE, HospitalStatus.PAUSED])
def test_late_v0_completion_preserves_concurrent_live_status(harness, late_status):
    """사이트 워커가 먼저 전진시킨 최신 상태를 pinned V0 snapshot이 덮지 않는다."""
    harness.session.status_on_refresh = late_status

    _run_task(harness.hospital.id, operation_run_id=uuid.uuid4())

    assert harness.hospital.v0_report_done is True
    assert harness.hospital.status is late_status


def test_a_different_v0_request_does_not_inherit_the_previous_measurement(harness):
    """AE가 상태를 되돌려 다시 요청하면 새 lineage다 — 옛 숫자를 물려주지 않는다."""
    harness.pdf_failures[0] = True
    with pytest.raises(RuntimeError):
        _run_task(harness.hospital.id, operation_run_id=uuid.uuid4())

    harness.pdf_failures[0] = False
    harness.hospital.v0_report_done = False
    _run_task(harness.hospital.id, operation_run_id=uuid.uuid4())

    assert len(harness.provider_calls) == 20, "다른 요청인데 옛 측정을 재사용했다"
    assert len(harness.session.measurement_runs) == 2


# ──────────────────────────────────────────────────────────────────
# (c) stale / 이미 소비된 측정은 재사용하지 않는다
# ──────────────────────────────────────────────────────────────────


class _LookupSession:
    def __init__(self, runs, record_counts, report_count) -> None:
        self.runs = runs
        self.record_counts = record_counts
        self.report_count = report_count
        self.statements: list[str] = []

    def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.statements.append(sql)
        if "FROM measurement_runs" in sql:
            return _Result(list(self.runs))
        if "FROM sov_records" in sql:
            run_id = _bound_uuid(stmt) or _uuid_in_sql(sql, self.record_counts)
            return _Result([self.record_counts.get(run_id, 0)])
        if "FROM monthly_reports" in sql:
            return _Result([self.report_count])
        raise AssertionError(sql)


def _uuid_in_sql(sql: str, keys) -> uuid.UUID | None:
    for key in keys:
        if str(key) in sql:
            return key
    return None


def _completed_run(*, operation_run_id: uuid.UUID | None, hospital_id: uuid.UUID) -> MeasurementRun:
    run = MeasurementRun(
        hospital_id=hospital_id,
        run_label="V0 first measurement",
        status="COMPLETED",
        success_count=10,
        failure_count=0,
        config=v0_checkpoint.v0_measurement_run_config(
            repeat_count=5, operation_run_id=operation_run_id
        ),
    )
    run.id = uuid.uuid4()
    run.started_at = datetime.now(UTC) - timedelta(minutes=5)
    run.completed_at = datetime.now(UTC) - timedelta(minutes=1)
    return run


def test_a_run_from_another_operation_is_not_reused():
    hospital_id = uuid.uuid4()
    run = _completed_run(operation_run_id=uuid.uuid4(), hospital_id=hospital_id)
    db = _LookupSession([run], {run.id: 10}, 0)

    assert (
        v0_checkpoint.find_reusable_v0_measurement_run(
            db, hospital_id, operation_run_id=uuid.uuid4()
        )
        is None
    )


def test_a_run_already_consumed_by_a_v0_report_is_not_reused():
    hospital_id = uuid.uuid4()
    op = uuid.uuid4()
    run = _completed_run(operation_run_id=op, hospital_id=hospital_id)
    db = _LookupSession([run], {run.id: 10}, 1)

    assert (
        v0_checkpoint.find_reusable_v0_measurement_run(db, hospital_id, operation_run_id=op)
        is None
    )


def test_a_run_without_sov_records_is_not_reused():
    """레코드가 없으면 언급률을 만들 재료가 없다 — 재사용하면 허위 숫자가 된다."""
    hospital_id = uuid.uuid4()
    op = uuid.uuid4()
    run = _completed_run(operation_run_id=op, hospital_id=hospital_id)
    db = _LookupSession([run], {run.id: 0}, 0)

    assert (
        v0_checkpoint.find_reusable_v0_measurement_run(db, hospital_id, operation_run_id=op)
        is None
    )


def test_a_non_v0_measurement_run_is_not_reused():
    """주간·월간 측정은 V0 표본이 아니다 — 진단 리포트의 근거로 쓸 수 없다."""
    hospital_id = uuid.uuid4()
    run = _completed_run(operation_run_id=None, hospital_id=hospital_id)
    run.config = {"source": "run_sov_for_hospital", "repeat_count": 5}
    db = _LookupSession([run], {run.id: 10}, 0)

    assert (
        v0_checkpoint.find_reusable_v0_measurement_run(db, hospital_id, operation_run_id=None)
        is None
    )


def test_a_legacy_dispatch_without_lineage_falls_back_to_the_recent_unconsumed_run():
    hospital_id = uuid.uuid4()
    run = _completed_run(operation_run_id=None, hospital_id=hospital_id)
    db = _LookupSession([run], {run.id: 10}, 0)

    found = v0_checkpoint.find_reusable_v0_measurement_run(
        db, hospital_id, operation_run_id=None
    )
    assert found is run


def test_the_candidate_query_bounds_status_and_age_in_sql():
    """상태·시간 창은 SQL이 건다 — 애플리케이션에서 재현하지 않으므로 여기서 고정한다."""
    hospital_id = uuid.uuid4()
    db = _LookupSession([], {}, 0)
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    v0_checkpoint.find_reusable_v0_measurement_run(
        db, hospital_id, operation_run_id=None, now=now
    )

    candidate_sql = db.statements[0]
    assert "measurement_runs.status IN ('COMPLETED', 'PARTIAL')" in candidate_sql
    assert "measurement_runs.completed_at IS NOT NULL" in candidate_sql
    cutoff = now - timedelta(seconds=v0_checkpoint.V0_CHECKPOINT_MAX_AGE_SECONDS)
    assert cutoff.strftime("%Y-%m-%d %H:%M:%S") in candidate_sql
    assert "ORDER BY measurement_runs.completed_at DESC" in candidate_sql
    # V0 판정도 SQL에 있어야 한다. LIMIT 뒤 파이썬에서 거르면, 6시간 창에 주간·월간
    # 측정이 상한(20건)만큼 쌓인 병원은 재사용 가능한 V0 측정을 후보에서 놓친다.
    assert "measurement_runs.config ->> 'source'" in candidate_sql
    assert f"'{v0_checkpoint.V0_MEASUREMENT_SOURCE}'" in candidate_sql


def test_the_consumption_query_only_counts_v0_reports_created_after_the_run():
    hospital_id = uuid.uuid4()
    op = uuid.uuid4()
    run = _completed_run(operation_run_id=op, hospital_id=hospital_id)
    db = _LookupSession([run], {run.id: 10}, 0)

    v0_checkpoint.find_reusable_v0_measurement_run(db, hospital_id, operation_run_id=op)

    report_sql = next(sql for sql in db.statements if "FROM monthly_reports" in sql)
    assert "monthly_reports.report_type = 'V0'" in report_sql
    assert "monthly_reports.created_at >=" in report_sql
