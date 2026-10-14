"""W8 — 보고서는 언제나 나온다. 실제 PostgreSQL 사실로 확인한다.

1. V0 PDF에 월간 계약을 적용해 열린 'hash 불일치' 사고는 V0 계약으로 유효하면 닫힌다(장보고·뉴플랜 10월).
2. 유효한 원장 PDF의 사고는 측정 커버리지와 무관하게 닫힌다(장앤김·신기한 9월).
3. 8일 09:00 뒤 안전망: 전달할 보고서가 없으면 두 번 다시 만들고, 그래도 없을 때만 개발 사고 하나.
4. 월간 측정 사고는 최종 판정 시각이 지나고 그 달 보고서가 있으면 닫힌다.
5. 마감이 지난 미완료 옛 매니페스트는 측정 '최종'이다(보고서를 다시 만들 수 있다).
6. 10일이 지나도 전달 기록 없는 준비본을 센다(D39).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from monthly_artifact_test_support import isolate_reconciler, published
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.hospital import Hospital
from app.models.monthly_control import (
    HospitalServiceInterval,
    MonthlyDeliveryEvent,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
    MonthlyReportArtifact,
)
from app.models.operations import (
    Incident,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.models.report import MonthlyReport
from app.services import fleet_heartbeat, ops_incident_alerts
from app.services.monthly_sov import build_monthly_sov
from app.services.report_artifact_validation import DoctorPdfValidationError
from app.workers import (
    incident_backlog,
    monthly_artifact_incident_control,
    monthly_artifact_reconciliation,
    monthly_artifact_recovery_control,
    monthly_report_safety_net,
)
from app.workers import tasks as worker_tasks
from app.workers.monthly_artifact_incident_contracts import MonthlyArtifactIncidentContext
from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "TASK24_DATABASE_URL"
KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    url = require_db_url(_URL_ENV)
    engine = create_engine(url, future=True)
    try:
        engine.connect().close()
    except OperationalError as exc:
        engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    session = Session(engine, expire_on_commit=False)
    async_engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool
    )
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    for module in (
        monthly_artifact_incident_control,
        monthly_artifact_recovery_control,
        ops_incident_alerts,
    ):
        monkeypatch.setattr(module, "get_async_sessionmaker", lambda: async_sessions)

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(monthly_artifact_reconciliation, "SyncSessionLocal", SessionContext)
    hospital_ids: list[uuid.UUID] = []
    try:
        yield SimpleNamespace(db=session, hospital_ids=hospital_ids)
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id.in_(hospital_ids))
        )
        session.execute(delete(Incident).where(Incident.hospital_id.in_(hospital_ids)))
        report_ids = select(MonthlyReport.id).where(MonthlyReport.hospital_id.in_(hospital_ids))
        session.execute(
            delete(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id.in_(report_ids))
        )
        session.execute(delete(MonthlyReport).where(MonthlyReport.hospital_id.in_(hospital_ids)))
        session.execute(
            delete(MonthlyMeasurementManifest).where(
                MonthlyMeasurementManifest.hospital_id.in_(hospital_ids)
            )
        )
        session.execute(delete(OperationRun).where(OperationRun.hospital_id.in_(hospital_ids)))
        session.execute(
            delete(HospitalServiceInterval).where(
                HospitalServiceInterval.hospital_id.in_(hospital_ids)
            )
        )
        session.execute(delete(Hospital).where(Hospital.id.in_(hospital_ids)))
        session.commit()
        session.close()
        asyncio.run(async_engine.dispose())
        engine.dispose()


def _hospital(env, name: str, *, in_service: bool = True) -> Hospital:
    hospital = Hospital(id=uuid.uuid4(), name=name, slug=f"w8-{uuid.uuid4().hex}")
    env.hospital_ids.append(hospital.id)
    env.db.add(hospital)
    env.db.flush()
    if in_service:
        env.db.add(
            HospitalServiceInterval(
                hospital_id=hospital.id,
                started_at=datetime(2026, 8, 1, tzinfo=UTC),
                provenance="LEGACY_CUTOVER",
            )
        )
        env.db.flush()
    return hospital


def _record_pdf_failure(hospital: Hospital, report_id: uuid.UUID, year: int, month: int) -> None:
    asyncio.run(
        monthly_artifact_incident_control.record_monthly_artifact_failure(
            MonthlyArtifactIncidentContext(
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                report_id=report_id,
                year=year,
                month=month,
                operation_run_id=None,
            ),
            DoctorPdfValidationError(
                "DOCTOR_PDF_ARTIFACT_INVALID",
                "저장된 원장 전달용 PDF의 검증 정보가 파일과 일치하지 않습니다.",
            ),
        )
    )


def _pdf_incident(env, hospital_id: uuid.UUID) -> Incident | None:
    env.db.expire_all()
    return env.db.scalar(
        select(Incident).where(
            Incident.hospital_id == hospital_id,
            Incident.incident_type == "MONTHLY_DOCTOR_PDF_BLOCKED",
        )
    )


def _v0_report(env, hospital: Hospital, *, valid: bool) -> MonthlyReport:
    path = f"gs://qa-private/reports/{hospital.slug}_V0-진단.pdf"
    sha = "d" * 64
    report = MonthlyReport(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=10,
        report_type="V0",
        version=1,
        quality="COMPLETE",
        pdf_path=path,
        doctor_pdf_path=path,
        sov_summary={"sov_pct": 20.0},
    )
    env.db.add(report)
    env.db.flush()
    env.db.add(
        MonthlyReportArtifact(
            report_id=report.id,
            audience="DOCTOR",
            path=path,
            sha256=sha,
            byte_size=33899,
            validated=True,
            validated_at=datetime.now(UTC),
            validation_metadata={
                "validation_version": "v0-pdf-v1",
                "validation_source": "SYSTEM",
                "sha256": sha if valid else "e" * 64,
                "byte_size": 33899,
            },
        )
    )
    env.db.commit()
    return report


def test_v0_origin_pdf_incident_closes_under_the_v0_contract(env, monkeypatch) -> None:
    isolate_reconciler(monkeypatch, monthly_artifact_reconciliation)
    valid_hospital = _hospital(env, "장보고형 V0 의원")
    broken_hospital = _hospital(env, "V0 손상 의원")
    valid_v0 = _v0_report(env, valid_hospital, valid=True)
    broken_v0 = _v0_report(env, broken_hospital, valid=False)
    _record_pdf_failure(valid_hospital, valid_v0.id, 2026, 10)
    _record_pdf_failure(broken_hospital, broken_v0.id, 2026, 10)

    result = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()

    assert result["recovered_count"] >= 1
    assert _pdf_incident(env, valid_hospital.id).state == "RECOVERED"
    # 정말로 손상된 V0 artifact는 닫지 않는다 — 판정 기준을 느슨하게 한 것이 아니다.
    assert _pdf_incident(env, broken_hospital.id).state in {"OPEN", "RETRYING"}


def test_valid_monthly_pdf_closes_its_incident_even_if_coverage_is_not_final(
    env, monkeypatch
) -> None:
    repairs = isolate_reconciler(monkeypatch, monthly_artifact_reconciliation)
    hospital = _hospital(env, "장앤김형 9월 의원")
    report = MonthlyReport(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        period_year=2026,
        period_month=9,
        report_type="MONTHLY",
        version=1,
        quality="DEGRADED",
        planned_count=80,
        success_count=31,
        failed_count=49,
        sov_summary={"observation_adequacy": {"status": "LEGACY_UNKNOWN"}},
        delivery_blockers=["MANIFEST_CELL_FAILURES"],
    )
    artifact = published(report.id)
    report.doctor_pdf_path = artifact.path
    env.db.add(report)
    env.db.flush()
    env.db.add(
        MonthlyReportArtifact(
            report_id=report.id,
            audience="DOCTOR",
            path=artifact.path,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            validated=True,
            validated_at=datetime.now(UTC),
            validation_metadata=artifact.metadata.model_dump(mode="json"),
        )
    )
    env.db.commit()
    _record_pdf_failure(hospital, report.id, 2026, 9)

    result = monthly_artifact_reconciliation.reconcile_monthly_artifact_incidents.run()

    assert result["recovered_count"] >= 1
    assert _pdf_incident(env, hospital.id).state == "RECOVERED"
    assert repairs == []  # 유효한 PDF는 다시 만들지 않는다.


# ── 안전망 ───────────────────────────────────────────────────────────────


def _baseline_ready_report(env, hospital: Hospital, *, commit: bool = True) -> MonthlyReport:
    payload = build_monthly_sov((), ()).to_payload()
    payload["report_kind"] = "SETUP"
    report = MonthlyReport(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        period_year=2026,
        period_month=10,
        report_type="MONTHLY",
        version=1,
        quality="BLOCKED",
        planned_count=0,
        success_count=0,
        failed_count=0,
        pdf_path="gs://qa-private/ae.pdf",
        sov_summary=payload,
        content_summary={"published_count": 4, "operations": {}},
        essence_summary={},
        delivery_blockers=[],
    )
    artifact = published(report.id)
    report.doctor_pdf_path = artifact.path
    env.db.add(report)
    env.db.flush()
    env.db.add(
        MonthlyReportArtifact(
            report_id=report.id,
            audience="DOCTOR",
            path=artifact.path,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            validated=True,
            validated_at=datetime.now(UTC),
            validation_metadata=artifact.metadata.model_dump(mode="json"),
        )
    )
    if commit:
        env.db.commit()
    else:
        env.db.flush()
    return report


def _repair_runs(env, hospital_id: uuid.UUID) -> list[OperationRun]:
    env.db.expire_all()
    return monthly_report_safety_net.repair_runs(env.db, hospital_id, 2026, 10)


def _finish(env, run: OperationRun, at: datetime) -> None:
    env.db.execute(
        update(OperationRun)
        .where(OperationRun.id == run.id)
        .values(state=OperationRunState.FAILED, created_at=at, completed_at=at)
    )
    env.db.commit()


def test_safety_net_rebuilds_twice_then_opens_one_developer_incident(env, monkeypatch) -> None:
    published_tasks: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: published_tasks.append(kwargs),
    )
    missing = _hospital(env, "보고서 없는 의원")
    ready = _hospital(env, "기준 설정 준비 의원")
    _baseline_ready_report(env, ready)
    monkeypatch.setattr(
        monthly_report_safety_net,
        "eligible_hospital_ids",
        lambda *_args: [missing.id, ready.id],
    )
    judgment = datetime(2026, 11, 8, 9, 0, tzinfo=KST)

    before = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment - timedelta(minutes=1)
    )
    assert before == {"status": "before_final_judgment"}

    first = monthly_report_safety_net.run_monthly_report_safety_net(env.db, now=judgment)
    assert (first["ready"], first["dispatched"]) == (1, 1)
    [run] = _repair_runs(env, missing.id)
    assert run.idempotency_key.endswith(":2026-10:1")
    # 보고서가 아예 없으면 숫자를 옮길 판이 없으므로 같은 달 재생성이다.
    assert published_tasks[0]["args"] == [str(missing.id), 2026, 10, True, True, False, False]
    assert published_tasks[0]["queue"] == "reports"
    _finish(env, run, judgment)

    waiting = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=1)
    )
    assert waiting["waiting"] == 1 and waiting["dispatched"] == 0

    second = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=7)
    )
    assert second["dispatched"] == 1
    runs = _repair_runs(env, missing.id)
    assert [item.idempotency_key[-1] for item in runs] == ["1", "2"]
    _finish(env, runs[-1], judgment + timedelta(hours=7))

    # 두 번째 시도가 빨리 끝나도 첫 시도 뒤 18시간(원장 PDF 사고와 같은 창)이 지나야 사람을 부른다.
    early = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=14)
    )
    assert early["opened"] == 0
    exhausted = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=18)
    )
    again = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=19)
    )
    env.db.expire_all()
    incidents = list(
        env.db.scalars(
            select(Incident).where(
                Incident.hospital_id == missing.id,
                Incident.incident_type == "MONTHLY_REPORT_SAFETY_NET_FAILED",
            )
        )
    )
    assert exhausted["opened"] == 1 and again["opened"] == 0
    assert len(incidents) == 1 and incidents[0].state == "OPEN"
    assert len(_repair_runs(env, missing.id)) == 2  # 예산을 넘겨 사지 않는다.
    assert not env.db.scalar(
        select(Incident.id).where(
            Incident.hospital_id == ready.id,
            Incident.incident_type == "MONTHLY_REPORT_SAFETY_NET_FAILED",
        )
    )

    # 사람이 고치든 늦은 재생성이 성공하든, 전달 가능한 보고서가 생기면 안전망이 닫는다.
    _baseline_ready_report(env, missing)
    healed = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=judgment + timedelta(hours=20)
    )
    env.db.expire_all()
    assert healed["recovered"] == 1
    # 복구 확인은 사람의 일이 아니다 — 시스템이 확인까지 닫는다.
    assert env.db.get(Incident, incidents[0].id).state in {"RECOVERED", "ACKNOWLEDGED"}
    assert incident_backlog.RESOLVERS["MONTHLY_REPORT_SAFETY_NET_FAILED"](
        env.db, incidents[0], judgment
    ) == "monthly_report_deliverable"


def test_artifact_only_problem_is_repaired_by_template_refresh_first(env, monkeypatch) -> None:
    published_tasks: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: published_tasks.append(kwargs),
    )
    hospital = _hospital(env, "PDF만 손상된 의원")
    report = _baseline_ready_report(env, hospital)
    env.db.execute(
        update(MonthlyReportArtifact)
        .where(MonthlyReportArtifact.report_id == report.id)
        .values(sha256="f" * 64)
    )
    env.db.commit()
    monkeypatch.setattr(
        monthly_report_safety_net, "eligible_hospital_ids", lambda *_args: [hospital.id]
    )

    result = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=datetime(2026, 11, 8, 9, 5, tzinfo=KST)
    )

    assert result["dispatched"] == 1
    # 숫자는 그대로 두고 PDF만 다시 그린다(템플릿 갱신, 저장된 숫자를 옮기므로 측정 관문을 넘는다).
    assert published_tasks[0]["args"] == [str(hospital.id), 2026, 10, True, True, True, True]


def test_repair_dispatcher_refuses_a_month_outside_the_service_interval(env, monkeypatch) -> None:
    published_tasks: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: published_tasks.append(kwargs),
    )
    hospital = _hospital(env, "서비스 밖 의원", in_service=False)
    decision = monthly_report_safety_net.dispatch_report_repair(
        env.db, hospital, 2026, 10, prefer_template=True, now=datetime(2026, 11, 8, 9, tzinfo=KST)
    )
    assert decision.action == "out_of_service"
    assert published_tasks == [] and _repair_runs(env, hospital.id) == []


def test_pdf_incident_owns_escalation_so_the_net_does_not_open_a_duplicate(
    env, monkeypatch
) -> None:
    monkeypatch.setattr(
        worker_tasks.generate_monthly_report_for_hospital, "apply_async", lambda **_kwargs: None
    )
    hospital = _hospital(env, "PDF 사고 보유 의원")
    report = _baseline_ready_report(env, hospital)
    env.db.execute(
        update(MonthlyReportArtifact)
        .where(MonthlyReportArtifact.report_id == report.id)
        .values(sha256="f" * 64)
    )
    env.db.commit()
    _record_pdf_failure(hospital, report.id, 2026, 10)
    monkeypatch.setattr(
        monthly_report_safety_net, "eligible_hospital_ids", lambda *_args: [hospital.id]
    )
    start = datetime(2026, 11, 8, 9, 0, tzinfo=KST)
    for hours in (0, 7):
        monthly_report_safety_net.run_monthly_report_safety_net(
            env.db, now=start + timedelta(hours=hours)
        )
        for run in _repair_runs(env, hospital.id):
            if run.state != OperationRunState.FAILED:
                _finish(env, run, start + timedelta(hours=hours))

    late = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=start + timedelta(hours=30)
    )
    env.db.expire_all()
    assert late["opened"] == 0
    assert not env.db.scalar(
        select(Incident.id).where(
            Incident.hospital_id == hospital.id,
            Incident.incident_type == "MONTHLY_REPORT_SAFETY_NET_FAILED",
        )
    )


def test_actual_byte_corruption_is_detected_and_repaired(env, monkeypatch) -> None:
    """메타데이터는 맞아도 저장소 바이트가 다르면 전달 가능으로 보지 않는다(일시 장애는 손상 아님)."""
    from app.services.report_file_integrity import ReportFileUnavailable

    published_tasks: list[dict] = []
    monkeypatch.setattr(
        worker_tasks.generate_monthly_report_for_hospital,
        "apply_async",
        lambda **kwargs: published_tasks.append(kwargs),
    )
    corrupt = _hospital(env, "바이트 손상 의원")
    flaky = _hospital(env, "저장소 일시 장애 의원")
    corrupt_report = _baseline_ready_report(env, corrupt)
    _baseline_ready_report(env, flaky)
    monkeypatch.setattr(
        monthly_report_safety_net, "eligible_hospital_ids", lambda *_args: [corrupt.id, flaky.id]
    )

    def read(path, _sha, _size):
        if path.endswith(f"{corrupt_report.id}.pdf"):
            raise ReportFileUnavailable("Report bytes differ from validated artifact")
        raise ReportFileUnavailable("Report storage unavailable")

    monkeypatch.setattr(monthly_report_safety_net, "read_verified_report", read)
    result = monthly_report_safety_net.run_monthly_report_safety_net(
        env.db, now=datetime(2026, 11, 8, 9, 0, tzinfo=KST)
    )

    assert result["corrupted"] == 1 and result["ready"] == 1 and result["dispatched"] == 1
    # 숫자는 그대로 두고 PDF만 다시 그린다.
    assert published_tasks[0]["args"] == [str(corrupt.id), 2026, 10, True, True, True, True]


# ── 월간 측정 사고 근거 ───────────────────────────────────────────────────


def test_monthly_sov_incident_closes_after_final_judgment_when_a_report_exists(env) -> None:
    hospital = _hospital(env, "신기한형 9월 의원")
    incident = SimpleNamespace(
        hospital_id=hospital.id,
        incident_type="MONTHLY_SOV_MEASUREMENT_FAILED",
        source_id=f"{hospital.id}:2026-09",
        first_seen_at=datetime(2026, 10, 6, 9, 16, tzinfo=UTC),
    )
    after = datetime(2026, 10, 8, 9, 0, tzinfo=KST)
    resolver = incident_backlog.RESOLVERS["MONTHLY_SOV_MEASUREMENT_FAILED"]

    assert resolver(env.db, incident, after) is None  # 보고서가 없으면 아직이다.
    env.db.add(
        MonthlyReport(
            hospital_id=hospital.id,
            period_year=2026,
            period_month=9,
            report_type="MONTHLY",
            version=1,
            quality="DEGRADED",
        )
    )
    env.db.commit()
    assert resolver(env.db, incident, after - timedelta(seconds=1)) is None
    assert resolver(env.db, incident, after) == "finalized_as_degraded"


# ── 옛 매니페스트 마감 ────────────────────────────────────────────────────


def _legacy_manifest(env, hospital: Hospital, year: int, month: int) -> MonthlyMeasurementManifest:
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital.id,
        period_year=year,
        period_month=month,
        configured_platforms=["chatgpt", "gemini"],
        platform_provenance={"chatgpt": "ALWAYS", "gemini": "CONFIGURED"},
        closes_at=datetime(year, month, 1, tzinfo=UTC) + timedelta(days=31),
    )
    manifest.cells = [
        MonthlyMeasurementCell(
            query_key=f"q{index}", query_text="가상동 내과", platform=platform, state=state
        )
        for index, (platform, state) in enumerate(
            (("chatgpt", "SUCCESS"), ("gemini", "SUCCESS"), ("chatgpt", "FAILED"))
        )
    ]
    env.db.add(manifest)
    env.db.commit()
    return manifest


def test_incomplete_legacy_manifest_is_final_after_the_recovery_deadline(env) -> None:
    hospital = _hospital(env, "옛 매니페스트 의원")
    past = _legacy_manifest(env, hospital, 2026, 9)
    future = _legacy_manifest(env, hospital, 2030, 1)

    # 9월 복구 마감(10/7 23:59:59 KST)은 지났다 — LIMITED로 최종이고 매니페스트가 닫힌다.
    assert worker_tasks._monthly_sov_measurement_succeeded(env.db, hospital.id, "2026-09")
    env.db.expire_all()
    assert env.db.get(MonthlyMeasurementManifest, past.id).closed_at is not None
    # 마감 전인 달은 종전처럼 실패 셀이 남으면 최종이 아니다.
    assert not worker_tasks._monthly_sov_measurement_succeeded(env.db, hospital.id, "2030-01")
    assert env.db.get(MonthlyMeasurementManifest, future.id).closed_at is None


# ── D39 ─────────────────────────────────────────────────────────────────


def test_ready_reports_without_a_delivery_record_are_counted_after_the_tenth(
    env, monkeypatch
) -> None:
    # 전달 기록은 append-only라 지울 수 없다 — 이 시험의 행은 커밋하지 않고 끝에 되돌린다.
    hospital = Hospital(id=uuid.uuid4(), name="전달 기록 없는 의원", slug=f"w8-{uuid.uuid4().hex}")
    env.db.add(hospital)
    env.db.flush()
    report = _baseline_ready_report(env, hospital, commit=False)
    monkeypatch.setattr(fleet_heartbeat, "eligible_hospital_ids", lambda *_args: [hospital.id])
    eleventh = datetime(2026, 11, 11, 9, 0, tzinfo=KST)
    try:
        assert fleet_heartbeat.count_undelivered_ready_reports(env.db, now=eleventh) == 1
        env.db.add(MonthlyDeliveryEvent(report_id=report.id, event_type="DELIVERED"))
        env.db.flush()
        assert fleet_heartbeat.count_undelivered_ready_reports(env.db, now=eleventh) == 0
    finally:
        env.db.rollback()
