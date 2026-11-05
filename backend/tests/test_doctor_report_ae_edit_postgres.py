"""AE 수정 판 — 실제 매니페스트·관측 기록·PostgreSQL로 새 버전을 만든다(원장 PDF는 실제로 렌더·검증, 저장소만 생략).

끝에서 끝까지: v4 첫 버전 → AE 수정 판(문구·라벨·사례 교체) → 템플릿 갱신이 수정을 잇는다 → 일반 재생성이
숫자가 같으면 전부, 바뀌면 라벨만 잇는다. 거절 경로는 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import arrow
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_monthly_template_refresh_postgres import _item

from app.models.content import ContentSchedule, ContentStatus
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.doctor_report_ae_edit import base_view_of, stored_ae_overrides
from app.services.doctor_report_v4 import DoctorReportV4
from app.services.monthly_period import ReportBuildReason
from app.services.monthly_report_snapshot import report_render_inputs, restore_doctor_view
from app.services.monthly_template_refresh import TemplateRefreshRefused
from app.utils import monthly_template_refresh as cli
from app.workers import tasks
from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "TASK16_DATABASE_URL"
ANCHOR = arrow.get(2026, 7, 31, 23, 59, tzinfo="Asia/Seoul")
ORIGINAL_CUTOFF = datetime(2026, 8, 2, tzinfo=timezone.utc)
KEYWORD = "가상동 위내시경 잘하는 병원"
# 두 번째 키워드는 타깃에 진료 항목이 없어 폴백 분류기가 라벨을 잡는다 — AE가 바로잡을 자리.
KEYWORD_2 = "가상동 허리디스크 치료하는 병원"
LABEL_PATH = f"treatment_label[{KEYWORD_2}]"


@pytest.fixture
def pg_session():
    engine = create_engine(require_db_url(_URL_ENV), future=True)
    try:
        connection = engine.connect()
    except OperationalError as exc:
        engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _pdf_text(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


def _v4_first_version(session: Session, monkeypatch, texts: dict):
    """실제 매니페스트·셀·답변 기록으로 v4 첫 버전을 만든다(`_v3_snapshot_version`의 앞부분과 같다)."""
    from app.models.sov import AIQueryTarget, QueryMatrix, SovRecord
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.doctor_report_artifact import build_doctor_pdf_expectation
    from app.services.monthly_manifest import (
        ManifestCellSpec,
        freeze_monthly_manifest,
        link_attempt,
    )
    from app.services.report_artifact_validation import PublishedDoctorPdf

    url = "https://fictional.example.invalid/"
    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: url)

    def real_doctor(_hospital, report_id, period_start, view, public_url):
        rendered = render_validated_doctor_pdf(
            view=view, period_label=arrow.get(period_start).format("YYYY-MM"), public_url=public_url,
            expectation=build_doctor_pdf_expectation(view, public_url),
        )
        texts[report_id] = _pdf_text(rendered.pdf_bytes)
        return PublishedDoctorPdf(
            path=f"gs://qa-private/monthly/{report_id}.pdf", sha256=rendered.sha256,
            byte_size=rendered.byte_size, metadata=rendered.metadata,
        )

    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", real_doctor)
    monkeypatch.setattr(tasks, "generate_pdf_report", lambda **_k: "gs://qa-private/ae-edit.pdf")

    async def quiet(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tasks, "record_monthly_artifact_failure", quiet)
    monkeypatch.setattr(tasks, "recover_monthly_artifact_failures", quiet)

    hospital = Hospital(name="수정 판 가상 의원", slug=f"ae-edit-{uuid.uuid4().hex}", region=["가상동"])
    session.add(hospital)
    session.flush()
    schedule = ContentSchedule(hospital_id=hospital.id, plan="PLAN_12", publish_days=[0], active_from=date(2026, 7, 1), is_active=True)
    session.add(schedule)
    session.flush()
    items = [
        _item(hospital, schedule, 1, scheduled=date(2026, 7, 6), published_at=datetime(2026, 7, 6, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 2, scheduled=date(2026, 7, 13), published_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 3, scheduled=date(2026, 7, 27)),
    ]
    session.add_all(items)
    target = AIQueryTarget(hospital_id=hospital.id, name=KEYWORD, target_intent="추천 탐색", platforms=["chatgpt", "gemini"], treatment="위내시경")
    target2 = AIQueryTarget(hospital_id=hospital.id, name=KEYWORD_2, target_intent="증상 탐색", platforms=["chatgpt", "gemini"], treatment=None)
    query = QueryMatrix(hospital_id=hospital.id, query_text=KEYWORD, query_intent="LOCAL")
    query2 = QueryMatrix(hospital_id=hospital.id, query_text=KEYWORD_2, query_intent="LOCAL")
    session.add_all((target, target2, query, query2))
    session.flush()
    manifest = freeze_monthly_manifest(
        session, hospital.id, 2026, 7,
        [
            ManifestCellSpec(
                query_key=f"query:{q.id}", query_text=q.query_text, platform=platform,
                query_matrix_id=q.id, query_target_id=t.id, query_variant_id=None, query_intent="LOCAL",
            )
            for q, t in ((query, target), (query2, target2))
            for platform in ("chatgpt", "gemini")
        ],
        gemini_configured=True,
    )
    records = [
        SovRecord(
            hospital_id=hospital.id, query_id=q.id, ai_query_target_id=t.id, ai_platform=platform,
            is_mentioned=mentioned, measurement_status="SUCCESS",
            raw_response=(
                f"가상동에서는 수정 판 가상 의원의 위내시경 상담을 받아볼 수 있습니다. ({platform})"
                if mentioned else f"가상동에는 여러 의원이 있어 가까운 곳에서 상담해 보세요. ({q.query_text} · {platform})"
            ),
        )
        for q, t, platform, mentioned in (
            (query, target, "chatgpt", True), (query, target, "gemini", False),
            (query2, target2, "chatgpt", False), (query2, target2, "gemini", False),
        )
    ]
    session.add_all(records)
    session.flush()
    cells = {(cell.query_text, cell.platform): cell for cell in manifest.cells}
    texts_by_query_id = {query.id: KEYWORD, query2.id: KEYWORD_2}
    session.add_all(link_attempt(cells[(texts_by_query_id[r.query_id], r.ai_platform)], r) for r in records)
    session.commit()

    outcome = tasks._build_monthly_report_for_hospital(
        session, hospital, ANCHOR, observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.SCHEDULED_CLOSE, correlation_key=f"test:ae-first:{hospital.id}",
    )
    assert outcome == "created"
    first = session.execute(select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)).scalar_one()
    frozen = report_render_inputs(first.content_summary)
    assert frozen is not None and isinstance(restore_doctor_view(frozen.doctor_view), DoctorReportV4)
    return hospital, items, records, first


def _latest(session, hospital) -> MonthlyReport:
    return session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id).order_by(MonthlyReport.version.desc()).limit(1)
    ).scalar_one()


def _ae_edit(report, fields, **extra):
    return {"report_id": str(report.id), "fields": fields, "edited_by": "ae@example.invalid", "requested_at": "2026-11-05T01:00:00+00:00", **extra}


def test_ae_edit_makes_a_validated_new_version_and_later_versions_carry_it(pg_session, monkeypatch):
    texts: dict = {}
    hospital, items, records, first = _v4_first_version(pg_session, monkeypatch, texts)
    base = base_view_of(report_render_inputs(first.content_summary))
    assert base is not None and base.cases.missing is not None
    plan_row = (*base.plan.rows, *base.plan.continued)[0]
    assert plan_row.keyword == KEYWORD_2
    fallback_label = next(r for r in base.appendix.keyword_rows if r.keyword == KEYWORD_2).treatment
    assert fallback_label != "허리디스크 치료" and f"'가상동 {fallback_label}'" in plan_row.action
    # 후보 목록(API 미리보기와 같은 로더)에 그 달의 관측 기록이 전부 있다 — 기본 사례와 다른 키워드의 미노출 답변을 고른다.
    candidates = tasks.load_ae_case_candidates(pg_session, hospital, first)
    assert {c.record_id for c in candidates} == {str(r.id) for r in records}
    missing_candidate = next(c for c in candidates if not c.found and c.case.keyword != base.cases.missing.keyword)
    fields = {
        "summary.key_lines[0]": "위내시경 안내 콘텐츠가 ChatGPT 답변에 노출됐습니다.",
        LABEL_PATH: "허리디스크 치료",
        f"plan.action[{KEYWORD_2}]": f"'가상동 {fallback_label}' 키워드 대응 콘텐츠 1편 발행 예정(안내문 보강)",
        "cases.missing.record_id": missing_candidate.record_id,
    }

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:ae-edit:{hospital.id}", ae_edit=_ae_edit(first, fields),
    )
    assert outcome == "created"
    second = _latest(pg_session, hospital)
    assert second.version == 2 and second.supersedes_report_id == first.id
    assert second.sov_summary == first.sov_summary
    artifact = pg_session.execute(select(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id == second.id)).scalar_one()
    assert artifact.validated is True and artifact.validation_metadata["validation_version"] == "doctor-pdf-v4"
    inputs = report_render_inputs(second.content_summary)
    view = restore_doctor_view(inputs.doctor_view)
    assert isinstance(view, DoctorReportV4)
    assert view.summary.key_lines[0] == fields["summary.key_lines[0]"]
    assert next(r for r in view.appendix.keyword_rows if r.keyword == KEYWORD_2).treatment == "허리디스크 치료"
    new_plan_row = (*view.plan.rows, *view.plan.continued)[0]
    assert new_plan_row.treatment == "허리디스크 치료"
    # 명시한 실행 내용이 라벨에서 유도된 문장을 이긴다(문장 그대로 저장).
    assert new_plan_row.action == fields[f"plan.action[{KEYWORD_2}]"]
    assert {b.label for b in view.exposure.treatment_bars} == {"위내시경", "허리디스크 치료"}
    assert view.cases.missing is not None and view.cases.missing.keyword == missing_candidate.case.keyword
    assert inputs.doctor_metric_facts == view.facts()
    assert second.content_summary["talking_points"] == list(view.summary.key_lines)
    stored = stored_ae_overrides(inputs)
    assert stored["fields"] == fields and stored["edited_by"] == "ae@example.invalid"
    assert base_view_of(inputs) == base
    assert inputs.ae_overrides_dropped is None
    # 새 원장 PDF에 고친 문장과 라벨이 찍혔다.
    compact = "".join(texts[second.id].split())
    assert "".join(fields["summary.key_lines[0]"].split()) in compact and "허리디스크치료" in compact
    # 숫자 사실은 첫 버전과 같다(진료 항목 묶음은 라벨만 바뀐다).
    first_facts = {k: v for k, v in report_render_inputs(first.content_summary).doctor_metric_facts.items() if not k.startswith("treatment.")}
    assert {k: v for k, v in inputs.doctor_metric_facts.items() if not k.startswith("treatment.")} == first_facts
    # 사후 확인(운영 CLI)도 숫자 그대로를 본다.
    monkeypatch.setattr(cli, "_stored_doctor_text", lambda _db, report, _verdict: texts.get(report.id, "옛 PDF 본문"))
    after = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert after.verdict.status == "PASS", after.verdict.summary()

    # 이어지는 템플릿 갱신(수정 요청 없음)은 같은 수정을 다시 얹는다.
    assert tasks._build_monthly_template_refresh(pg_session, hospital, ANCHOR, correlation_key=f"test:ae-refresh:{hospital.id}") == "created"
    third = _latest(pg_session, hospital)
    third_inputs = report_render_inputs(third.content_summary)
    assert third.version == 3 and restore_doctor_view(third_inputs.doctor_view) == view
    assert stored_ae_overrides(third_inputs)["fields"] == fields
    assert stored_ae_overrides(third_inputs)["history"] == stored["history"]

    # 숫자가 같은 일반 재생성: 전부 잇는다.
    assert tasks._build_monthly_report_for_hospital(
        pg_session, hospital, ANCHOR, rebuild=True, observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.MANUAL_REBUILD, correlation_key=f"test:ae-rebuild-same:{hospital.id}",
    ) == "created"
    fourth = _latest(pg_session, hospital)
    fourth_inputs = report_render_inputs(fourth.content_summary)
    fourth_view = restore_doctor_view(fourth_inputs.doctor_view)
    assert fourth.version == 4 and fourth_view.summary.key_lines[0] == fields["summary.key_lines[0]"]
    assert stored_ae_overrides(fourth_inputs)["fields"] == fields and fourth_inputs.ae_overrides_dropped is None

    # 숫자가 바뀐 일반 재생성(발행 편수 변화): 라벨만 잇고 문장·사례 교체는 버린다.
    late = items[2]
    late.status = ContentStatus.PUBLISHED
    late.published_at = late.first_published_at = datetime(2026, 7, 27, 1, tzinfo=timezone.utc)
    pg_session.commit()
    assert tasks._build_monthly_report_for_hospital(
        pg_session, hospital, ANCHOR, rebuild=True, observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.MANUAL_REBUILD, correlation_key=f"test:ae-rebuild-changed:{hospital.id}",
    ) == "created"
    fifth = _latest(pg_session, hospital)
    fifth_inputs = report_render_inputs(fifth.content_summary)
    fifth_view = restore_doctor_view(fifth_inputs.doctor_view)
    assert fifth.version == 5 and fifth.content_summary["published_count"] == 3
    assert stored_ae_overrides(fifth_inputs)["fields"] == {LABEL_PATH: "허리디스크 치료"}
    assert next(r for r in fifth_view.appendix.keyword_rows if r.keyword == KEYWORD_2).treatment == "허리디스크 치료"
    assert fifth_view.summary.key_lines[0] != fields["summary.key_lines[0]"]
    dropped = fifth_inputs.ae_overrides_dropped
    assert dropped["reason"] == "NUMBERS_CHANGED" and dropped["from_report_id"] == str(fourth.id)
    assert set(dropped["fields"]) == {"summary.key_lines[0]", f"plan.action[{KEYWORD_2}]", "cases.missing.record_id"}


def test_ae_edit_with_a_forbidden_sentence_or_a_stale_report_writes_nothing(pg_session, monkeypatch):
    texts: dict = {}
    hospital, _items, _records, first = _v4_first_version(pg_session, monkeypatch, texts)
    for ae_edit, code in (
        (_ae_edit(first, {"summary.key_lines[0]": "최고의 위내시경 병원입니다."}), "AE_EDIT_INVALID"),
        (_ae_edit(first, {"summary.cards[1].value": "99%"}), "AE_EDIT_INVALID"),
        ({**_ae_edit(first, {"summary.key_lines[0]": "문장"}), "report_id": str(uuid.uuid4())}, "AE_EDIT_STALE_REPORT"),
    ):
        with pytest.raises(TemplateRefreshRefused) as caught:
            tasks._build_monthly_template_refresh(
                pg_session, hospital, ANCHOR, correlation_key=f"test:ae-refuse:{uuid.uuid4()}", ae_edit=ae_edit,
            )
        assert code in caught.value.verdict.summary()
        pg_session.rollback()
    versions = pg_session.execute(select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)).scalars().all()
    assert versions == [1]
    assert len(texts) == 1  # 새 원장 PDF를 그리지 않았다


def test_task_entry_reads_the_edit_from_the_run_and_marks_the_version_as_ae_edit(pg_session, monkeypatch):
    """워커 진입점(`generate_monthly_report_for_hospital`)이 OperationRun의 `ae_edit`를 읽어 같은 경로로 새 버전을 만든다."""
    from app.models.operations import OperationRun, OperationRunState

    texts: dict = {}
    hospital, _items, _records, first = _v4_first_version(pg_session, monkeypatch, texts)
    run = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED, task_id=str(uuid.uuid4()),
        request_payload={"_dispatch": {}, "ae_edit": _ae_edit(first, {"summary.key_lines[1]": "위내시경 키워드는 아직 노출되지 않았습니다."})},
        attempt_count=0, total_count=0, success_count=0, failure_count=0, skipped_count=0,
    )
    pg_session.add(run)
    pg_session.commit()
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _NoCloseSession(pg_session))
    monkeypatch.setattr(tasks, "_hospital_requires_monthly_sov_success", lambda *_a: False)
    monkeypatch.setattr(tasks, "_mark_monthly_operation_run_running", lambda *_a: None)
    finished: list = []
    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", lambda *args: finished.append(args))
    monkeypatch.setattr(tasks, "_monthly_operation_run_id", lambda _task: run.id)

    result = tasks.generate_monthly_report_for_hospital(str(hospital.id), 2026, 7, rebuild=True, template_only=True)

    assert result["status"] == "created" and finished and finished[0][-1] == "created"
    second = _latest(pg_session, hospital)
    assert second.version == 2
    assert restore_doctor_view(report_render_inputs(second.content_summary).doctor_view).summary.key_lines[1] == "위내시경 키워드는 아직 노출되지 않았습니다."


class _NoCloseSession:
    """`with SyncSessionLocal() as db` 자리에 테스트 세션을 넣는다 — 닫지 않는다."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc):
        return False
