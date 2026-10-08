"""월간 리포트 템플릿 갱신 — 저장된 숫자를 그대로 옮기고 PDF만 새로 그린다(PostgreSQL).

템플릿 배포 뒤 지난달 리포트를 새 버전으로 다시 찍는 동안 현재 행이 바뀌어 있어도
(늦은 발행, 공개 철회, 새 노출 행동, 사후검수 완료) 숫자는 처음 만든 그대로여야 한다.
"""

import re
import uuid
from datetime import date, datetime, timezone

import arrow
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.operations import OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.models.sov import ExposureAction
from app.services.monthly_period import ReportBuildReason
from app.services.monthly_report_snapshot import report_render_inputs, restore_doctor_view
from app.services.monthly_template_refresh import (
    TemplateRefreshRefused,
    doctor_view_facts,
    number_tokens,
    numeric_diff,
)
from app.workers import tasks
from tests.db_env import fail_unreachable, require_db_url
from tests.monthly_artifact_test_support import published

_URL_ENV = "TASK16_DATABASE_URL"
ANCHOR = arrow.get(2026, 7, 31, 23, 59, tzinfo="Asia/Seoul")
ORIGINAL_CUTOFF = datetime(2026, 8, 2, tzinfo=timezone.utc)


@pytest.fixture
def pg_session():
    engine = create_engine(require_db_url(_URL_ENV), future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    transaction = connection.begin()
    session = Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture
def renders(monkeypatch):
    """PDF 저장·사고 기록·외부 호출을 막고, 각 버전이 받은 입력을 모은다."""
    captured: dict[str, list] = {"ae": [], "doctor": []}

    def fake_ae(**kwargs):
        captured["ae"].append(kwargs)
        return f"gs://qa-private/ae-{len(captured['ae'])}.pdf"

    def fake_doctor(_hospital, report_id, _period_start, view, _public_url):
        captured["doctor"].append(view)
        return published(report_id)

    async def quiet(*_args, **_kwargs):
        return None

    def no_network(*_args, **_kwargs):
        raise AssertionError("템플릿 갱신은 외부 공급자를 호출하지 않는다")

    monkeypatch.setattr(tasks, "generate_pdf_report", fake_ae)
    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", fake_doctor)
    monkeypatch.setattr(tasks, "record_monthly_artifact_failure", quiet)
    monkeypatch.setattr(tasks, "recover_monthly_artifact_failures", quiet)
    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    return captured


def _item(hospital, schedule, number, *, scheduled, published_at=None, status=None):
    return ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.FAQ,
        sequence_no=number,
        total_count=12,
        title=f"가상 진료 안내 {number}",
        scheduled_date=scheduled,
        status=status or (ContentStatus.PUBLISHED if published_at else ContentStatus.DRAFT),
        published_at=published_at,
        published_by="AE" if published_at else None,
        first_published_at=published_at,
        first_published_by="AE" if published_at else None,
    )


def _first_version(session: Session):
    hospital = Hospital(name="템플릿 갱신 가상 의원", slug=f"template-refresh-{uuid.uuid4().hex}")
    session.add(hospital)
    session.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[0],
        active_from=date(2026, 7, 1), is_active=True,
    )
    session.add(schedule)
    session.flush()
    items = [
        _item(hospital, schedule, 1, scheduled=date(2026, 7, 6),
              published_at=datetime(2026, 7, 6, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 2, scheduled=date(2026, 7, 13),
              published_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 3, scheduled=date(2026, 7, 20),
              published_at=datetime(2026, 7, 20, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 4, scheduled=date(2026, 7, 27)),
    ]
    session.add_all(items)
    session.add(
        MonthlyMeasurementManifest(
            hospital_id=hospital.id, period_year=2026, period_month=7,
            configured_platforms=["chatgpt"], platform_provenance={"query_intents": {}},
            closes_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
    )
    session.commit()
    outcome = tasks._build_monthly_report_for_hospital(
        session, hospital, ANCHOR,
        observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.SCHEDULED_CLOSE,
        correlation_key=f"test:first:{hospital.id}",
    )
    assert outcome == "created"
    first = session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)
    ).scalar_one()
    return hospital, schedule, items, first


def _change_live_rows(session, hospital, items):
    hospital.name = "현재 이름으로 바뀐 의원"
    hospital.director_philosophy = "마감 뒤 승인된 현재 운영 철학"
    items[0].title = "마감 뒤 바뀐 현재 글 제목"
    # 늦은 발행: 7월 몫이 원래 마감 뒤에 공개됐다.
    late = items[3]
    late.status = ContentStatus.PUBLISHED
    late.published_at = late.first_published_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
    # 공개 철회
    items[1].status = ContentStatus.REJECTED
    # 사후검수 완료
    items[0].post_publish_reviewed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    # 새 노출 행동
    action = ExposureAction(
        hospital_id=hospital.id, action_type="CONTENT", title="가상 보강 행동",
        description="가상 설명", status="OPEN",
    )
    session.add(action)
    session.commit()
    return action


def test_template_refresh_keeps_every_number_when_live_rows_changed(pg_session, renders):
    hospital, _schedule, items, first = _first_version(pg_session)
    action = _change_live_rows(pg_session, hospital, items)
    # 지금 시각으로 다시 계산하면 계약 이행 숫자가 실제로 달라진다(테스트가 의미 있다는 증거).
    live = tasks._load_monthly_publication_facts(
        pg_session, hospital.id,
        datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    assert len(live[2]) != first.content_summary["contract_timing"]["published_for_contract_count"]

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:refresh:{hospital.id}"
    )

    assert outcome == "created"
    second = pg_session.execute(
        select(MonthlyReport)
        .where(MonthlyReport.hospital_id == hospital.id, MonthlyReport.version == 2)
    ).scalar_one()
    assert second.supersedes_report_id == first.id
    assert second.sov_summary == first.sov_summary
    assert second.essence_summary == first.essence_summary
    old_content = dict(first.content_summary)
    new_content = dict(second.content_summary)
    assert number_tokens(old_content.pop("talking_points")) == number_tokens(
        new_content.pop("talking_points")
    )
    assert numeric_diff(old_content, new_content) == []
    for name in ("manifest_id", "cutoff_at", "quality", "planned_count", "success_count",
                 "failed_count", "excluded_count"):
        assert getattr(second, name) == getattr(first, name)
    pg_session.refresh(action)
    assert action.linked_report_id is None
    first_view, second_view = renders["doctor"]
    assert first_view["hospital_name"] == second_view["hospital_name"] == "템플릿 갱신 가상 의원"
    assert "마감 뒤 바뀐 현재 글 제목" not in str(second_view)
    assert renders["ae"][1]["hospital"].name == "템플릿 갱신 가상 의원"
    assert second_view["tiles"][0]["value"] == first_view["tiles"][0]["value"]
    assert second_view["narrative"].current == first_view["narrative"].current
    assert renders["ae"][1]["published_count"] == renders["ae"][0]["published_count"]
    assert renders["ae"][1]["content_operations"] == renders["ae"][0]["content_operations"]


def test_template_refresh_uses_snapshot_when_backdated_live_fact_appears(pg_session, renders):
    hospital, schedule, _items, first = _first_version(pg_session)
    # 원래 마감 이전으로 기록된 발행이 뒤늦게 생겼다 — 같은 마감으로도 숫자를 재현할 수 없다.
    pg_session.add(
        _item(hospital, schedule, 9, scheduled=date(2026, 7, 29),
              published_at=datetime(2026, 7, 29, tzinfo=timezone.utc))
    )
    pg_session.commit()

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:frozen:{hospital.id}"
    )

    assert outcome == "created"
    versions = pg_session.execute(
        select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)
        .order_by(MonthlyReport.version)
    ).scalars().all()
    assert versions == [first.version, 2]
    assert renders["ae"][1]["published_count"] == renders["ae"][0]["published_count"]


def test_snapshot_refresh_does_not_require_or_mutate_old_pdf(pg_session, renders):
    hospital, _schedule, _items, first = _first_version(pg_session)
    artifact = pg_session.execute(
        select(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id == first.id)
    ).scalar_one()
    original_artifact = (
        artifact.path,
        artifact.sha256,
        dict(artifact.validation_metadata),
    )
    first.doctor_pdf_path = "gs://qa-private/deleted-old.pdf"
    first.sent_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
    pg_session.commit()

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:invalid-old:{hospital.id}"
    )

    assert outcome == "created"
    pg_session.refresh(first)
    pg_session.refresh(artifact)
    assert first.doctor_pdf_path == "gs://qa-private/deleted-old.pdf"
    assert first.sent_at == datetime(2026, 8, 2, tzinfo=timezone.utc)
    assert artifact.validated is True
    assert (artifact.path, artifact.sha256, artifact.validation_metadata) == original_artifact


def test_persisted_snapshot_pdf_facts_survive_live_title_and_essence_changes(pg_session, renders):
    hospital, _schedule, items, first = _first_version(pg_session)
    frozen = report_render_inputs(first.content_summary)
    assert frozen is not None
    before_view = restore_doctor_view(frozen.doctor_view)
    before_text = _rendered_text(before_view)

    _change_live_rows(pg_session, hospital, items)
    pg_session.refresh(first)
    persisted = report_render_inputs(first.content_summary)
    assert persisted is not None
    after_view = restore_doctor_view(persisted.doctor_view)
    after_text = _rendered_text(after_view)

    assert doctor_view_facts(after_view) == doctor_view_facts(before_view)
    assert after_text == before_text
    assert "현재 이름으로 바뀐 의원" not in after_text
    assert "마감 뒤 바뀐 현재 글 제목" not in after_text
    assert "마감 뒤 승인된 현재 운영 철학" not in after_text


def test_legacy_incomplete_precheck_blocks_without_mutation(pg_session, renders):
    hospital = Hospital(name="열린 매니페스트 가상 의원", slug=f"open-{uuid.uuid4().hex}")
    pg_session.add(hospital)
    pg_session.flush()
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital.id, period_year=2026, period_month=7,
        configured_platforms=["chatgpt"], platform_provenance={},
        closes_at=datetime(2026, 8, 1, tzinfo=timezone.utc), closed_at=None,
    )
    pg_session.add(manifest)
    pg_session.flush()
    pg_session.add(
        MonthlyReport(
            hospital_id=hospital.id, period_year=2026, period_month=7, report_type="MONTHLY",
            version=1, manifest_id=manifest.id, sov_summary={}, content_summary={},
        )
    )
    pg_session.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="RUN_SOV",
            state=OperationRunState.RUNNING, attempt_count=1, total_count=0,
            success_count=0, failure_count=0, skipped_count=0,
            request_payload={"source_type": "hospital", "source_id": str(hospital.id)},
        )
    )
    pg_session.commit()

    plan = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime(2026, 8, 3, tzinfo=timezone.utc)
    )

    codes = {finding.code for finding in plan.verdict.findings if finding.kind == "BLOCKER"}
    assert plan.verdict.status == "BLOCKED"
    assert {
        "OPERATION_IN_FLIGHT", "RECOVERY_PENDING", "LEGACY_RENDER_INPUTS_INCOMPLETE",
    } <= codes
    assert plan.doctor_view is None
    original_content = dict(plan.superseded.content_summary)
    with pytest.raises(TemplateRefreshRefused) as refused:
        tasks._build_monthly_template_refresh(
            pg_session, hospital, ANCHOR, correlation_key=f"test:legacy-incomplete:{hospital.id}"
        )
    assert "LEGACY_RENDER_INPUTS_INCOMPLETE" in str(refused.value)
    pg_session.rollback()
    reports = pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)
    ).scalars().all()
    assert len(reports) == 1
    assert reports[0].content_summary == original_content


def test_recovery_window_only_blocks_hospitals_whose_measurement_is_incomplete(pg_session):
    """1~7일 자동 복구는 측정이 덜 끝난 병원만 다시 만든다 — 전부 확정된 병원은 막지 않는다."""

    def report_with(quality: str, success: int) -> tuple[Hospital, MonthlyReport]:
        hospital = Hospital(name="복구 기간 가상 의원", slug=f"window-{uuid.uuid4().hex}")
        pg_session.add(hospital)
        pg_session.flush()
        manifest = MonthlyMeasurementManifest(
            hospital_id=hospital.id, period_year=2026, period_month=7,
            configured_platforms=["chatgpt"], platform_provenance={},
            closes_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        pg_session.add(manifest)
        pg_session.flush()
        report = MonthlyReport(
            hospital_id=hospital.id, period_year=2026, period_month=7, report_type="MONTHLY",
            version=1, manifest_id=manifest.id, sov_summary={}, content_summary={},
            quality=quality, planned_count=6, success_count=success, failed_count=6 - success,
            excluded_count=0,
        )
        pg_session.add(report)
        pg_session.commit()
        return hospital, report

    in_window = datetime(2026, 8, 3, tzinfo=timezone.utc)
    after_window = datetime(2026, 8, 9, tzinfo=timezone.utc)

    def blockers(hospital, observed_now):
        plan = tasks.build_monthly_template_refresh_plan(
            pg_session, hospital, ANCHOR, observed_now=observed_now
        )
        return {f.code for f in plan.verdict.findings if f.kind == "BLOCKER"}

    complete, _ = report_with("COMPLETE", 6)
    partial, _ = report_with("DEGRADED", 4)

    assert "RECOVERY_PENDING" not in blockers(complete, in_window)
    assert "RECOVERY_PENDING" in blockers(partial, in_window)
    assert "RECOVERY_PENDING" not in blockers(partial, after_window)

    # 운영자가 지금 숫자로 보내기로 명시하면 막지 않고 WARN으로만 남긴다.
    allowed = tasks.build_monthly_template_refresh_plan(
        pg_session, partial, ANCHOR, observed_now=in_window, allow_recovery_pending=True
    )
    codes = {(f.kind, f.code) for f in allowed.verdict.findings}
    assert ("BLOCKER", "RECOVERY_PENDING") not in codes
    assert ("WARN", "RECOVERY_PENDING_ALLOWED") in codes


def _rendered_text(view) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf

    url = "https://fictional.example.invalid/"
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-07", public_url=url,
        expectation=DoctorPdfExpectation(
            view["hospital_name"], view["coverage_text"],
            "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.", url,
            appendix_expected=bool(view["appendix_rows"]),
        ),
    )
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages)


def test_cli_precheck_and_postcheck_pass_for_a_number_preserving_refresh(
    pg_session, renders, monkeypatch
):
    from app.utils import monthly_template_refresh as cli

    hospital, _schedule, items, _first = _first_version(pg_session)
    _change_live_rows(pg_session, hospital, items)
    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: "https://fictional.example.invalid/")
    texts = {"old": _rendered_text(renders["doctor"][0])}
    monkeypatch.setattr(
        cli, "_stored_doctor_text",
        lambda _db, report, _verdict: texts["old"] if report.version == 1 else texts.get("new"),
    )

    before = cli.precheck_hospital(pg_session, hospital, ANCHOR)
    assert before.verdict.status == "PASS", before.verdict.summary()
    pg_session.rollback()

    tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:cli:{hospital.id}"
    )
    texts["new"] = _rendered_text(renders["doctor"][1])
    after = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert after.verdict.status == "PASS", after.verdict.summary()

    assert re.search(r"12\s*편\s*중\s*3\s*편", texts["new"])
    texts["new"] = re.sub(r"12\s*편\s*중\s*3\s*편", "12편 중 4편", texts["new"])
    flagged = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert flagged.verdict.status == "DIFF"
