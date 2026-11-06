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
from app.services.doctor_report_v4 import DoctorReportV4
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
    # 2026-11-04 v4: 원장 뷰는 `DoctorReportV4` dataclass다(얼린 스냅샷에서 같은 형으로 복원된다).
    assert isinstance(first_view, DoctorReportV4) and isinstance(second_view, DoctorReportV4)
    assert first_view.hospital_name == second_view.hospital_name == "템플릿 갱신 가상 의원"
    assert "마감 뒤 바뀐 현재 글 제목" not in str(second_view)
    assert renders["ae"][1]["hospital"].name == "템플릿 갱신 가상 의원"
    assert second_view.summary.cards[0].value == first_view.summary.cards[0].value
    assert second_view.current_rate == first_view.current_rate
    assert second_view == first_view
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

    # UTC 7/31 20:00 = KST 8/1 05:00 — 7월 달의 1일 새벽 창 안.
    plan = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime(2026, 7, 31, 20, tzinfo=timezone.utc)
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
    """1일 새벽 자동 복구는 측정이 덜 끝난 병원만 다시 만든다 — 전부 확정된 병원은 막지 않는다."""

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

    # UTC 7/31 20:00 = KST 8/1 05:00 — 7월 달 새벽 창 안. UTC 8/1 00:00 = KST 8/1 09:00 — 창 뒤.
    in_window = datetime(2026, 7, 31, 20, tzinfo=timezone.utc)
    after_window = datetime(2026, 8, 1, tzinfo=timezone.utc)

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

    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.doctor_report_artifact import build_doctor_pdf_expectation

    url = "https://fictional.example.invalid/"
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-07", public_url=url,
        expectation=build_doctor_pdf_expectation(view, url),
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

    # v4 카드 1: 큰 숫자 '3편'(발행 콘텐츠). 숫자를 바꾼 본문은 사후 확인이 막는다.
    assert re.search(r"(?<![\d.])3\s*편", texts["new"])
    texts["new"] = re.sub(r"(?<![\d.])3\s*편", "4편", texts["new"])
    flagged = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert flagged.verdict.status == "DIFF"


# ── v4 배포 전(v3 스냅샷) 보고서의 템플릿 갱신 = v4 승격 ──────────────────────────


def _v3_snapshot_version(session: Session, monkeypatch, texts: dict):
    """실제 매니페스트·셀·답변 기록으로 보고서를 만든 뒤, 스냅샷을 v4 배포 전 모양(v3 뷰)으로 되돌린다."""
    from app.models.sov import AIQueryTarget, QueryMatrix, SovRecord
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.doctor_report_artifact import build_doctor_pdf_expectation
    from app.services.doctor_report_v4 import DoctorReportV4
    from app.services.monthly_manifest import (
        ManifestCellSpec,
        freeze_monthly_manifest,
        link_attempt,
    )
    from app.services.monthly_report_snapshot import freeze_doctor_view
    from app.services.report_artifact_validation import PublishedDoctorPdf
    from app.services.report_engine import build_doctor_report_view

    url = "https://fictional.example.invalid/"
    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: url)

    def real_doctor(_hospital, report_id, period_start, view, public_url):
        # 실제로 렌더·검증해 판 이름(doctor-pdf-v4)이 진짜가 되게 하고, 저장소 업로드만 생략한다.
        rendered = render_validated_doctor_pdf(
            view=view, period_label=arrow.get(period_start).format("YYYY-MM"), public_url=public_url,
            expectation=build_doctor_pdf_expectation(view, public_url),
        )
        texts[report_id] = _pdf_bytes_text(rendered.pdf_bytes)
        return PublishedDoctorPdf(
            path=f"gs://qa-private/monthly/{report_id}.pdf", sha256=rendered.sha256,
            byte_size=rendered.byte_size, metadata=rendered.metadata,
        )

    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", real_doctor)
    monkeypatch.setattr(tasks, "generate_pdf_report", lambda **_k: "gs://qa-private/ae-upgrade.pdf")

    async def quiet(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tasks, "record_monthly_artifact_failure", quiet)
    monkeypatch.setattr(tasks, "recover_monthly_artifact_failures", quiet)

    hospital = Hospital(name="승격 가상 의원", slug=f"upgrade-{uuid.uuid4().hex}", region=["가상동"])
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
    ]
    session.add_all(items)
    target = AIQueryTarget(
        hospital_id=hospital.id, name="가상동 위내시경 잘하는 병원", target_intent="추천 탐색",
        platforms=["chatgpt", "gemini"], treatment="위내시경",
    )
    query = QueryMatrix(hospital_id=hospital.id, query_text="가상동 위내시경 잘하는 병원", query_intent="LOCAL")
    session.add_all((target, query))
    session.flush()
    manifest = freeze_monthly_manifest(
        session, hospital.id, 2026, 7,
        [
            ManifestCellSpec(
                query_key=f"query:{query.id}", query_text=query.query_text, platform=platform,
                query_matrix_id=query.id, query_target_id=target.id, query_variant_id=None,
                query_intent="LOCAL",
            )
            for platform in ("chatgpt", "gemini")
        ],
        gemini_configured=True,
    )
    records = [
        SovRecord(
            hospital_id=hospital.id, query_id=query.id, ai_query_target_id=target.id, ai_platform=platform,
            is_mentioned=mentioned, measurement_status="SUCCESS",
            raw_response=(
                "가상동에서는 승격 가상 의원의 위내시경 상담을 받아볼 수 있습니다."
                if mentioned else "가상동에는 여러 내과가 있어 가까운 곳에서 상담해 보세요."
            ),
        )
        for platform, mentioned in (("chatgpt", True), ("gemini", False))
    ]
    session.add_all(records)
    session.flush()
    cells = {cell.platform: cell for cell in manifest.cells}
    session.add_all(link_attempt(cells[r.ai_platform], r) for r in records)
    # 매니페스트는 불변이다 — 마감은 보고서 생성이 저장된 마감 시각(ORIGINAL_CUTOFF)으로 닫는다.
    session.commit()

    outcome = tasks._build_monthly_report_for_hospital(
        session, hospital, ANCHOR, observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.SCHEDULED_CLOSE, correlation_key=f"test:v3:{hospital.id}",
    )
    assert outcome == "created"
    first = session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)
    ).scalar_one()
    frozen = report_render_inputs(first.content_summary)
    assert frozen is not None and isinstance(restore_doctor_view(frozen.doctor_view), DoctorReportV4)

    # v4 배포 전 코드가 저장하던 모양: v3 뷰를 같은 저장 요약으로 만들어 스냅샷에 얼린다.
    content = dict(first.content_summary)
    sov = first.sov_summary
    operations = content.get("operations") or {}
    timing = content.get("contract_timing") or {}
    v3_view = build_doctor_report_view(
        report_kind="MONTHLY", protocol_label="기록 없음", hospital=hospital,
        sov_pct=sov["sov_pct"], prev_sov_pct=None, published_count=content["published_count"],
        plan_quota=operations.get("plan_quota"), contract_published_count=timing.get("published_for_contract_count"),
        attribution=content.get("attribution"), citations=content.get("citations"),
        published_contents=list(items), v0_baseline=None, records=records, platforms=["chatgpt", "gemini"],
        sov_coverage=sov, comparison_reason=sov["comparison"]["reason"], cumulative_published_count=2,
    )
    snapshot = dict(content["report_snapshot"])
    inputs = dict(snapshot["render_inputs"])
    inputs["doctor_view"] = freeze_doctor_view(v3_view)
    inputs["doctor_metric_facts"] = doctor_view_facts(v3_view)
    inputs["ae_report"] = {**inputs["ae_report"], "talking_points": list(v3_view["talking_points"])}
    snapshot["render_inputs"] = inputs
    content["report_snapshot"] = snapshot
    content["talking_points"] = list(v3_view["talking_points"])
    first.content_summary = content
    session.commit()
    session.refresh(first)
    persisted = report_render_inputs(first.content_summary)
    assert persisted is not None and not isinstance(restore_doctor_view(persisted.doctor_view), DoctorReportV4)
    assert "template" not in persisted.doctor_metric_facts
    return hospital, first


def _pdf_bytes_text(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


def test_template_refresh_upgrades_a_v3_snapshot_report_to_v4(pg_session, monkeypatch):
    from app.utils import monthly_template_refresh as cli

    texts: dict = {}
    hospital, first = _v3_snapshot_version(pg_session, monkeypatch, texts)

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:upgrade:{hospital.id}"
    )

    assert outcome == "created"
    second = pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id, MonthlyReport.version == 2)
    ).scalar_one()
    assert second.supersedes_report_id == first.id
    assert second.sov_summary == first.sov_summary
    artifact = pg_session.execute(
        select(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id == second.id)
    ).scalar_one()
    assert artifact.validated is True
    assert artifact.validation_metadata["validation_version"] == "doctor-pdf-v4"
    upgraded = report_render_inputs(second.content_summary)
    assert upgraded is not None
    assert upgraded.doctor_view["template_version"] == "v4"
    assert upgraded.doctor_metric_facts["template"] == "v4"
    assert upgraded.doctor_metric_facts["card.published"] == "2편"
    assert second.content_summary["talking_points"] == list(upgraded.ae_report.talking_points)
    assert "승격 가상 의원" in "".join(texts[second.id].split()).replace("승격가상의원", "승격 가상 의원")

    monkeypatch.setattr(
        cli, "_stored_doctor_text",
        lambda _db, report, _verdict: texts.get(report.id, "옛 PDF 본문"),
    )
    after = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert after.verdict.status == "PASS", after.verdict.summary()


def test_template_refresh_refuses_a_v3_upgrade_whose_facts_disagree_without_writing(pg_session, monkeypatch):
    """승격 뷰의 사실이 저장 요약에서 다시 만든 사실과 어긋나면 V4_UPGRADE_FACTS로 거절하고 아무것도 쓰지 않는다.

    저장된 `published_count`를 손대면 승격 뷰와 저장 사실이 **같은 값**을 읽어 어긋나지 않는다(둘 다 저장
    요약이 근거다). 그래서 거절 경로는 저장 사실 쪽을 바꿔 확인한다.
    """
    from app.services import doctor_report_v4

    texts: dict = {}
    hospital, _first = _v3_snapshot_version(pg_session, monkeypatch, texts)
    real = doctor_report_v4.stored_facts_v4
    monkeypatch.setattr(
        doctor_report_v4, "stored_facts_v4",
        lambda sov, content: {**real(sov, content), "card.published": "9편"},
    )

    with pytest.raises(TemplateRefreshRefused) as caught:
        tasks._build_monthly_template_refresh(
            pg_session, hospital, ANCHOR, correlation_key=f"test:upgrade-tamper:{hospital.id}"
        )
    assert "V4_UPGRADE_FACTS" in caught.value.verdict.summary()
    assert "card.published: 저장 9편 ≠ 뷰 2편" in caught.value.verdict.summary()
    pg_session.rollback()
    versions = pg_session.execute(
        select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)
    ).scalars().all()
    assert versions == [1]
    assert len(texts) == 1  # 새 원장 PDF를 그리지 않았다


def test_a_tampered_stored_published_count_is_caught_by_the_frozen_v3_facts(pg_session, monkeypatch):
    """저장 요약만 보는 대조는 동어반복이지만, 원장이 받았던 v3 판의 숫자(doctor_metric_facts)와는 어긋나 거절한다."""
    texts: dict = {}
    hospital, first = _v3_snapshot_version(pg_session, monkeypatch, texts)
    tampered = dict(first.content_summary)
    tampered["published_count"] = int(tampered["published_count"]) + 1
    first.content_summary = tampered
    pg_session.commit()

    with pytest.raises(TemplateRefreshRefused) as caught:
        tasks._build_monthly_template_refresh(
            pg_session, hospital, ANCHOR, correlation_key=f"test:upgrade-tamper2:{hospital.id}"
        )

    assert "V4_UPGRADE_FACTS" in caught.value.verdict.summary()
    assert "highlight.published_this_month" in caught.value.verdict.summary()
    pg_session.rollback()
    versions = pg_session.execute(
        select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)
    ).scalars().all()
    assert versions == [1]


def test_template_refresh_refuses_without_a_doctor_artifact_and_writes_no_version(pg_session, monkeypatch):
    """원장 PDF를 붙이지 못한 템플릿 갱신은 PDF 없는 새 버전을 남기지 않는다(안전망의 일반 재생성 수리를 막는다)."""
    from app.services.doctor_pdf_contracts import DoctorPdfValidationError

    texts: dict = {}
    hospital, _first = _v3_snapshot_version(pg_session, monkeypatch, texts)

    def broken_doctor(*_a, **_k):
        raise DoctorPdfValidationError("DOCTOR_PDF_STORAGE_FAILED", "검증된 원장 전달용 PDF를 안전하게 저장하지 못했습니다.")

    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", broken_doctor)
    recorded: list = []
    monkeypatch.setattr(tasks, "record_monthly_artifact_failure", lambda *a, **k: recorded.append(a))

    with pytest.raises(TemplateRefreshRefused) as caught:
        tasks._build_monthly_template_refresh(
            pg_session, hospital, ANCHOR, correlation_key=f"test:refuse-artifact:{hospital.id}"
        )
    assert "NEW_DOCTOR_PDF_INVALID" in caught.value.verdict.summary()
    assert "DOCTOR_PDF_STORAGE_FAILED" in caught.value.verdict.summary()
    pg_session.rollback()
    versions = pg_session.execute(
        select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)
    ).scalars().all()
    assert versions == [1]
    assert recorded == []  # 커밋되지 않은 보고서에 사고를 열지 않는다


# ── 2026-11-06 공통 문구 정리 — 옛 문구를 얼린 v4 판(10월분)의 템플릿 갱신 ───────────────────────


def test_template_refresh_cleans_old_common_copy_and_postcheck_passes(pg_session, renders, monkeypatch):
    """옛 v4 판(부록 B·D, '직접 비교하지 않았습니다', 측정 실패 빗금 …)을 템플릿 갱신하면 새 버전은 지금 공통 문구로 찍히고
    숫자는 그대로라 사전·사후 확인이 통과한다(마케팅팀 요청 9개 예시)."""
    from test_doctor_report_v4 import MARKETING_REMOVED_COPY, with_legacy_copy

    from app.services.doctor_report_v4 import apply_report_copy_policy
    from app.services.monthly_report_snapshot import freeze_doctor_view
    from app.services.monthly_template_refresh import doctor_view_facts
    from app.utils import monthly_template_refresh as cli

    hospital, _schedule, _items, first = _first_version(pg_session)
    current = renders["doctor"][-1]
    assert isinstance(current, DoctorReportV4)
    legacy = with_legacy_copy(current)
    assert legacy != current
    content = dict(first.content_summary)
    snapshot = dict(content["report_snapshot"])
    inputs = dict(snapshot["render_inputs"])
    inputs["doctor_view"] = freeze_doctor_view(legacy)
    inputs["doctor_metric_facts"] = doctor_view_facts(legacy)
    snapshot["render_inputs"] = inputs
    content["report_snapshot"] = snapshot
    first.content_summary = content
    pg_session.commit()

    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: "https://fictional.example.invalid/")
    texts = {"old": _rendered_text(legacy)}
    assert "약속하지" in "".join(texts["old"].split())
    monkeypatch.setattr(
        cli, "_stored_doctor_text",
        lambda _db, report, _verdict: texts["old"] if report.version == 1 else texts.get("new"),
    )
    before = cli.precheck_hospital(pg_session, hospital, ANCHOR)
    assert before.verdict.status == "PASS", before.verdict.summary()
    pg_session.rollback()

    rendered_before = len(renders["doctor"])
    tasks._build_monthly_template_refresh(pg_session, hospital, ANCHOR, correlation_key=f"test:copy:{hospital.id}")
    assert len(renders["doctor"]) == rendered_before + 1
    rebuilt = renders["doctor"][-1]
    assert rebuilt == apply_report_copy_policy(legacy)
    texts["new"] = _rendered_text(rebuilt)
    squashed = "".join(texts["new"].split())
    for phrase in MARKETING_REMOVED_COPY:
        assert "".join(phrase.split()) not in squashed, phrase
    after = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert after.verdict.status == "PASS", after.verdict.summary()
    latest = pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id).order_by(MonthlyReport.version.desc()).limit(1)
    ).scalar_one()
    assert latest.version == 2
    stored = restore_doctor_view(report_render_inputs(latest.content_summary).doctor_view)
    assert stored == rebuilt and stored.appendix.caveat_lines == () and stored.appendix.method_lines == ()


def test_template_refresh_drops_ae_edits_that_only_touched_removed_lines(pg_session, renders, monkeypatch):
    """옛 판에서 AE가 '약속하지 않습니다' 한 줄만 지웠다면(Codex 검토) 새 버전은 수정 기록 없이 지금 문구로 찍히고, 옛 pristine
    뷰를 싣지 않는다 — 지운 자리('')도 남지 않는다."""
    from test_doctor_report_v4 import with_legacy_copy

    from app.services.doctor_report_ae_edit import build_ae_overrides
    from app.services.doctor_report_v4 import (
        AE_DELETE,
        apply_ae_overrides,
        apply_report_copy_policy,
    )
    from app.services.monthly_report_snapshot import freeze_doctor_view
    from app.services.monthly_template_refresh import doctor_view_facts

    hospital, _schedule, _items, first = _first_version(pg_session)
    legacy = with_legacy_copy(renders["doctor"][-1])
    fields = {"appendix.caveat_lines[1]": AE_DELETE}
    edited = apply_ae_overrides(legacy, fields)
    assert edited.appendix.caveat_lines[1] == ""
    content = dict(first.content_summary)
    snapshot = dict(content["report_snapshot"])
    inputs = dict(snapshot["render_inputs"])
    inputs["doctor_view"] = freeze_doctor_view(edited)
    inputs["doctor_metric_facts"] = doctor_view_facts(edited)
    inputs["ae_overrides"] = build_ae_overrides(
        fields=fields, base_view_payload=freeze_doctor_view(legacy), edited_by="ae@example.invalid",
        edited_at="2026-11-06T00:00:00+00:00", previous=None, changed_paths=list(fields), report_version=2,
        number_changes=[], fact_changes={},
    )
    snapshot["render_inputs"] = inputs
    content["report_snapshot"] = snapshot
    first.content_summary = content
    pg_session.commit()
    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: "https://fictional.example.invalid/")

    tasks._build_monthly_template_refresh(pg_session, hospital, ANCHOR, correlation_key=f"test:copy-ae:{hospital.id}")
    rebuilt = renders["doctor"][-1]
    assert rebuilt == apply_report_copy_policy(legacy)
    assert rebuilt.appendix.caveat_lines == ()
    latest = pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id).order_by(MonthlyReport.version.desc()).limit(1)
    ).scalar_one()
    new_inputs = report_render_inputs(latest.content_summary)
    assert new_inputs.ae_overrides is None
    assert new_inputs.ae_overrides_dropped["reason"] == "REPORT_COPY_POLICY"
    assert new_inputs.ae_overrides_dropped["fields"] == {"appendix.caveat_lines[1]": AE_DELETE}
    assert restore_doctor_view(new_inputs.doctor_view) == rebuilt
