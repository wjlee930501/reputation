"""월간 리포트 템플릿 갱신(숫자 그대로, 문구·디자인만 새 버전) 운영 명령.

    python -m app.utils.monthly_template_refresh precheck  --year 2026 --month 9 [--hospital-id ID ...]
    python -m app.utils.monthly_template_refresh execute   --year 2026 --month 9 --reason "..." \\
        --api-base https://api.example [--hospital-id ID ...] [--confirm]
    python -m app.utils.monthly_template_refresh postcheck --year 2026 --month 9 [--hospital-id ID ...]

- precheck: 읽기 전용. 병원마다 새 버전이 무엇을 만들지 계산하고(세션 롤백, 저장소 쓰기·
  공급자 호출 없음), 저장된 최신 버전과 숫자를 비교해 PASS/DIFF/BLOCKED를 표로 찍는다.
  새 원장 PDF는 메모리에서만 렌더해 옛 원장 PDF(저장소의 검증된 바이트)와 숫자 사실을 비교한다.
- execute: PASS인 병원만 한 곳씩, Admin API와 같은 경로(OperationRun + 사유 감사)로
  요청한다(`X-Admin-Actor-System`). 작업이 끝나면 postcheck를 돌리고, 하나라도 어긋나면 멈춘다.
  `--confirm`이 없으면 대상만 출력한다.
- postcheck: 최신 버전과 그것이 대체한 버전의 숫자·원장 PDF 숫자 사실을 비교한다.

종료 코드: 모두 PASS면 0, 아니면 1.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO

import arrow
import httpx
from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.doctor_pdf_contracts import DoctorPdfValidationError
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.doctor_report_ae_edit import stored_ae_overrides
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import DoctorReportV4, apply_report_copy_policy
from app.services.doctor_report_v4 import _pct as _pct_v4
from app.services.monthly_period import MonthlyPeriodError, require_closed_period
from app.services.monthly_report_delivery import monthly_doctor_artifact_is_valid
from app.services.monthly_report_snapshot import report_render_inputs, restore_doctor_view
from app.services.monthly_template_refresh import (
    RefreshVerdict,
    compare_doctor_facts,
    doctor_view_facts,
    facts_are_v4,
    number_tokens,
    numeric_diff,
    pdf_fact_problems,
    stored_doctor_facts,
    stored_doctor_view_is_v4,
    stored_only_facts,
)
from app.services.report_file_integrity import ReportFileUnavailable, read_verified_report

SYSTEM_JOB = "monthly-template-refresh"
_TERMINAL_RUN_STATES = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}


@dataclass(frozen=True, slots=True)
class HospitalResult:
    hospital_id: uuid.UUID
    name: str
    version: int | None
    verdict: RefreshVerdict


def _anchor(year: int, month: int) -> arrow.Arrow:
    period = require_closed_period(year, month, now=datetime.now(UTC))
    return arrow.get(period.ends_at).shift(microseconds=-1)


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


def _secondary_facts(facts: dict[str, str], sov_summary) -> dict[str, str]:
    """PDF 이차 확인에 쓰는 사실. v3는 저장 요약에만 있는 범위·횟수를 더하고, v4는 뷰 사실만 본다
    (v4 템플릿은 신뢰구간·확정 횟수를 찍지 않는다 — 부록 B는 뷰 사실의 계획/확인/실패 수다)."""
    if facts_are_v4(facts):
        return dict(facts)
    return {**facts, **stored_only_facts(sov_summary or {})}


def _doctor_artifact(db, report: MonthlyReport) -> MonthlyReportArtifact | None:
    return db.execute(
        select(MonthlyReportArtifact).where(
            MonthlyReportArtifact.report_id == report.id,
            MonthlyReportArtifact.audience == "DOCTOR",
        )
    ).scalar_one_or_none()


def _stored_doctor_text(db, report: MonthlyReport, verdict: RefreshVerdict) -> str | None:
    artifact = _doctor_artifact(db, report)
    if not monthly_doctor_artifact_is_valid(report, artifact):
        verdict.add("BLOCKER", "NO_VALID_DOCTOR_ARTIFACT", f"v{report.version}")
        return None
    assert artifact is not None
    try:
        return _pdf_text(read_verified_report(artifact.path, artifact.sha256, artifact.byte_size))
    except ReportFileUnavailable as exc:
        verdict.add("BLOCKER", "DOCTOR_PDF_UNREADABLE", f"v{report.version}: {exc}")
        return None


def _target_hospitals(db, year: int, month: int, hospital_ids: Sequence[str]) -> list[Hospital]:
    query = (
        select(Hospital)
        .where(
            Hospital.id.in_(
                select(MonthlyReport.hospital_id).where(
                    MonthlyReport.period_year == year,
                    MonthlyReport.period_month == month,
                    MonthlyReport.report_type == "MONTHLY",
                )
            )
        )
        .order_by(Hospital.name)
    )
    if hospital_ids:
        query = query.where(Hospital.id.in_([uuid.UUID(value) for value in hospital_ids]))
    return list(db.execute(query).scalars())


def precheck_hospital(
    db, hospital: Hospital, anchor: arrow.Arrow, *, allow_recovery_pending: bool = False
) -> HospitalResult:
    """새 버전을 메모리에서만 만들어 숫자를 비교한다. 호출자가 세션을 롤백한다."""
    from app.workers.tasks import _public_site_url, build_monthly_template_refresh_plan

    plan = build_monthly_template_refresh_plan(
        db, hospital, anchor, observed_now=datetime.now(UTC),
        allow_recovery_pending=allow_recovery_pending,
    )
    verdict = plan.verdict
    latest = plan.superseded
    if latest is not None and plan.doctor_view is not None and verdict.status != "BLOCKED":
        # 옛 PDF는 대체할 버전의 검증된 산출물인지만 본다 — 본문 숫자는 비교하지 않는다(문구 변경으로
        # 막히던 정규식 대조를 없앴다). 숫자는 저장 요약과 원장 뷰의 사실을 키 단위로 대조한다.
        _stored_doctor_text(db, latest, verdict)
        public_url = _public_site_url(hospital.aeo_domain, hospital.slug)
        view = plan.doctor_view
        try:
            rendered = render_validated_doctor_pdf(
                view=view,
                period_label=f"{anchor.year:04d}-{anchor.month:02d}",
                public_url=public_url,
                expectation=build_doctor_pdf_expectation(view, public_url),
            )
        except DoctorPdfValidationError as exc:
            verdict.add("DIFF", "NEW_DOCTOR_PDF_INVALID", exc.code)
        else:
            facts = doctor_view_facts(view)
            # 뷰와 저장 요약의 대조(계획 단계)는 이미 끝났다. 여기서는 지난달 보고서에서 읽어
            # 새 뷰가 '지난달(참고)'로 옮겨 적는 값만 그 근거와 맞춰 본다. AE가 숫자를 고친 판(2026-11-05)은 그 뷰의
            # 사실이 정본이라 지난달 근거와 맞추지 않고 WARN만 남긴다.
            if _has_ae_overrides(latest.content_summary or {}):
                verdict.add("WARN", "AE_EDIT")
            else:
                for problem in _reference_fact_problems(db, latest, facts):
                    verdict.add("DIFF", "DOCTOR_VIEW_NUMBER", problem)
            for problem in pdf_fact_problems(
                _pdf_text(rendered.pdf_bytes), _secondary_facts(facts, latest.sov_summary)
            ):
                verdict.add("DIFF", "DOCTOR_PDF_NUMBER", problem)
    return HospitalResult(
        hospital.id, hospital.name, latest.version if latest is not None else None, verdict
    )


def _reference_fact_problems(db, report: MonthlyReport, facts: dict[str, str]) -> list[str]:
    """새 뷰의 '지난달(참고)' 값과 지난달 보고서의 근거를 맞춘다.

    v4 뷰는 직접 비교한 달에 참고치를 쓰지 않고 `rate.previous`를 쓴다(비교·참고를 함께 적지
    않는다). 그래서 v4는 얼린 뷰 사실에 `rate.reference_previous`가 있을 때만 근거와 대조하고,
    없으면 건너뛴다 — 지난달 보고서가 있다는 이유만으로 참고치를 요구하지 않는다. v3는 종전 그대로다.
    """
    reference_key = {"rate.reference_previous"}
    v4 = facts_are_v4(facts)
    if v4 and not (reference_key & set(facts)):
        return []
    expected = _prior_reference_fact(db, report, v4=v4)
    if v4 and not stored_doctor_view_is_v4(report.content_summary or {}):
        # v3→v4 승격: 승격 뷰는 지난달 보고서를 다시 읽지 않고 그 달에 얼린 v3 참고치를 쓴다 — 같은 값으로 대조한다.
        frozen = report_render_inputs(report.content_summary or {})
        frozen_reference = (frozen.doctor_metric_facts or {}).get("rate.reference_previous") if frozen else None
        if isinstance(frozen_reference, str) and frozen_reference.endswith("%"):
            expected = {"rate.reference_previous": _pct_v4(float(frozen_reference[:-1]))}
    return compare_doctor_facts(
        expected,
        {key: value for key, value in facts.items() if key in reference_key},
        require_backing=frozenset(reference_key),
    )


def _has_ae_overrides(content_summary: Mapping) -> bool:
    """이 버전이 AE 수정 판을 품었는가(`render_inputs.ae_overrides.fields`가 비어 있지 않다)."""
    render_inputs = report_render_inputs(content_summary)
    return render_inputs is not None and stored_ae_overrides(render_inputs) is not None


def _copy_policy_changes(content_summary: Mapping) -> bool:
    """이 버전의 얼린 v4 원장 뷰가 지금 공통 문구 규칙(2026-11-06)으로 바뀌는가(옛 문구를 품었는가)."""
    if not stored_doctor_view_is_v4(content_summary):
        return False
    render_inputs = report_render_inputs(content_summary)
    if render_inputs is None:
        return False
    view = restore_doctor_view(render_inputs.doctor_view)
    return isinstance(view, DoctorReportV4) and apply_report_copy_policy(view) is not view


def _without_doctor_view(content_summary: dict) -> dict:
    """스냅샷의 원장 뷰·뷰 사실·AE 핵심 3줄·AE 수정 값을 뺀 content_summary(v3→v4 승격·AE 수정 판 비교용). 나머지는 그대로다."""
    content = dict(content_summary)
    snapshot = content.get("report_snapshot")
    if not isinstance(snapshot, dict):
        return content
    snapshot = dict(snapshot)
    inputs = dict(snapshot.get("render_inputs") or {})
    inputs.pop("doctor_view", None)
    inputs.pop("doctor_metric_facts", None)
    inputs.pop("ae_overrides", None)
    inputs.pop("ae_overrides_dropped", None)
    ae_report = inputs.get("ae_report")
    if isinstance(ae_report, dict):
        inputs["ae_report"] = {key: value for key, value in ae_report.items() if key != "talking_points"}
    snapshot["render_inputs"] = inputs
    content["report_snapshot"] = snapshot
    return content


def _frozen_view_facts(report: MonthlyReport) -> dict[str, str]:
    """대체한 버전이 스냅샷에 얼린 원장 뷰 사실(`render_inputs.doctor_metric_facts`).

    v4의 진료 항목·그룹·경쟁·추이 사실은 저장 요약만으로 다시 만들 수 없어(현재 행의 값) 저장
    요약 사실에 더해 이 얼린 사실까지 새 PDF에 찍혔는지 본다. 스냅샷이 없으면 빈 dict다.
    """
    render_inputs = report_render_inputs(report.content_summary or {})
    if render_inputs is None:
        return {}
    return dict(render_inputs.doctor_metric_facts)


def _prior_reference_fact(db, report: MonthlyReport, *, v4: bool = False) -> dict[str, str]:
    """지난달 보고서에 저장된 언급 비율 — 새 PDF가 '지난달(참고)' 칸에 옮겨 적는 사실.

    측정 방식이 바뀌어 비교하지 않는 달에 새 템플릿이 처음 보여 주는 숫자다. 새 뷰가 아니라
    지난달 보고서 행에서 따로 읽어, 그 값과 정확히 같을 때만 숫자가 바뀐 것으로 보지 않는다.
    v4 뷰는 비율을 정수 %로 적으므로(`doctor_report_v4._pct`) 같은 꼴로 만든다.
    """
    from app.workers.tasks import reported_monthly_sov_pct

    prior_year, prior_month = (
        (report.period_year - 1, 12)
        if report.period_month == 1
        else (report.period_year, report.period_month - 1)
    )
    value = reported_monthly_sov_pct(db, report.hospital_id, prior_year, prior_month)
    if value is None:
        return {}
    return {"rate.reference_previous": _pct_v4(value) if v4 else f"{value:.1f}%"}


def postcheck_hospital(db, hospital: Hospital, year: int, month: int) -> HospitalResult:
    """최신 버전과 그것이 대체한 버전의 숫자가 같은지 확인한다."""
    verdict = RefreshVerdict()
    latest = db.execute(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital.id,
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    previous = (
        db.get(MonthlyReport, latest.supersedes_report_id)
        if latest is not None and latest.supersedes_report_id is not None
        else None
    )
    if latest is None or previous is None:
        verdict.add("BLOCKER", "NO_SUPERSEDED_VERSION")
        return HospitalResult(hospital.id, hospital.name, None, verdict)
    for name in (
        "manifest_id", "cutoff_at", "quality", "planned_count", "success_count",
        "failed_count", "excluded_count",
    ):
        if getattr(previous, name) != getattr(latest, name):
            verdict.add("DIFF", "ROW_FIELD", f"{name}: {getattr(previous, name)} → {getattr(latest, name)}")
    old_content = dict(previous.content_summary or {})
    new_content = dict(latest.content_summary or {})
    old_points = old_content.pop("talking_points", [])
    new_points = new_content.pop("talking_points", [])
    # v4 배포 전(v3 스냅샷) 보고서의 갱신은 원장 뷰·뷰 사실·핵심 3줄을 v4로 바꿔 저장한다 — 그 세 칸은
    # 형식이 달라 숫자 칸으로 비교할 수 없고, 승격 뷰의 숫자는 생성 단계가 저장 요약과 대조했다.
    upgraded = stored_doctor_view_is_v4(new_content) and not stored_doctor_view_is_v4(old_content)
    # AE 수정 판: 원장 뷰의 문장·라벨 묶음(진료 항목 막대)과 수정 기록은 숫자 칸으로 비교할 수 없다 — 숫자 보존은
    # 생성 단계(AE_EDIT_NUMBERS·AE_EDIT_FACTS)가 pristine 뷰·저장 요약과 대조했고, 아래 PDF 사실 확인이 다시 본다.
    ae_edited = _has_ae_overrides(new_content) or _has_ae_overrides(old_content)
    if ae_edited:
        verdict.add("WARN", "AE_EDIT")
    if upgraded:
        verdict.add("WARN", "V3_TO_V4_UPGRADE")
    # 공통 문구 규칙(2026-11-06)이 옛 판의 부록 B 횟수 줄·인용 0건 줄을 걷어 냈다 — 원장 뷰 칸은 숫자 칸으로 비교하지 않고,
    # 숫자 보존은 아래 PDF 사실 확인(저장 요약 사실 + 새 뷰 사실)이 본다.
    copy_refreshed = _copy_policy_changes(old_content)
    if copy_refreshed:
        verdict.add("WARN", "REPORT_COPY_POLICY")
    if upgraded or ae_edited or copy_refreshed:
        old_content = _without_doctor_view(old_content)
        new_content = _without_doctor_view(new_content)
    for prefix, old, new in (
        ("sov_summary", previous.sov_summary, latest.sov_summary),
        ("content_summary", old_content, new_content),
        ("essence_summary", previous.essence_summary, latest.essence_summary),
    ):
        for line in numeric_diff(old, new, prefix=prefix):
            verdict.add("DIFF", "STORED_NUMBER", line)
    # AE가 고친 핵심 3줄은 원문에 있던 숫자만 남길 수 있어(검증 규칙) 숫자가 줄 수 있다 — 늘지는 못한다.
    if not upgraded and not ae_edited and number_tokens(old_points) != number_tokens(new_points):
        verdict.add("DIFF", "TALKING_POINT_NUMBERS")
    # 옛 버전은 검증된 산출물이 있는지만 본다. 새 PDF에는 저장된 숫자 사실이 모두 찍혀 있어야 한다
    # (옛 PDF 본문과 비교하지 않는다 — 문구만 바뀐 달이 막히던 정규식 대조를 없앴다).
    _stored_doctor_text(db, previous, verdict)
    new_text = _stored_doctor_text(db, latest, verdict)
    if new_text is not None:
        stored = stored_doctor_facts(latest.sov_summary or {}, latest.content_summary or {})
        facts = _secondary_facts(stored, latest.sov_summary)
        if _has_ae_overrides(latest.content_summary or {}):
            # AE가 숫자를 고친 판(2026-11-05): 저장 요약의 숫자는 더는 PDF에 찍히지 않을 수 있다 — 고친 뷰 자신의 사실
            # (`doctor_metric_facts`)이 새 PDF에 찍혔는지만 본다.
            facts = _frozen_view_facts(latest)
        elif facts_are_v4(stored):
            # 저장 요약 사실 + 얼린 뷰 사실(진료 항목·그룹·경쟁·추이). 같은 키는 저장 요약과 같아야
            # 하고(계획 단계가 이미 대조), 얼린 사실만의 키도 새 PDF에 찍혀 있어야 한다.
            facts = {**facts, **_frozen_view_facts(latest)}
        for problem in pdf_fact_problems(new_text, facts):
            verdict.add("DIFF", "DOCTOR_PDF_NUMBER", problem)
    return HospitalResult(hospital.id, hospital.name, latest.version, verdict)


def _print_table(title: str, results: Sequence[HospitalResult]) -> None:
    print(f"\n== {title} ==")
    print(f"{'STATUS':<8} {'VER':>4}  {'HOSPITAL_ID':<36}  NAME")
    for result in results:
        print(
            f"{result.verdict.status:<8} {result.version if result.version is not None else '-':>4}  "
            f"{result.hospital_id!s:<36}  {result.name}"
        )
        for finding in result.verdict.findings:
            print(f"           {finding.kind:<7} {finding.code} {finding.detail}")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.verdict.status] = counts.get(result.verdict.status, 0) + 1
    print("합계: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))


def run_precheck(
    year: int, month: int, hospital_ids: Sequence[str], *, allow_recovery_pending: bool = False
) -> list[HospitalResult]:
    anchor = _anchor(year, month)
    results: list[HospitalResult] = []
    with SyncSessionLocal() as db:
        for hospital in _target_hospitals(db, year, month, hospital_ids):
            try:
                results.append(
                    precheck_hospital(
                        db, hospital, anchor, allow_recovery_pending=allow_recovery_pending
                    )
                )
            finally:
                db.rollback()
    return results


def run_postcheck(year: int, month: int, hospital_ids: Sequence[str]) -> list[HospitalResult]:
    results: list[HospitalResult] = []
    with SyncSessionLocal() as db:
        for hospital in _target_hospitals(db, year, month, hospital_ids):
            try:
                results.append(postcheck_hospital(db, hospital, year, month))
            finally:
                db.rollback()
    return results


def _request_refresh(
    client: httpx.Client,
    result: HospitalResult,
    year: int,
    month: int,
    reason: str,
    *,
    allow_recovery_pending: bool = False,
) -> str:
    params = {"year": year, "month": month, "rebuild": "true", "template_only": "true"}
    key = f"template-refresh:{result.hospital_id}:{year:04d}-{month:02d}:v{result.version}"
    if allow_recovery_pending:
        params["allow_recovery_pending"] = "true"
        key += ":allow-recovery-pending"
    response = client.post(
        f"/api/v1/admin/hospitals/{result.hospital_id}/operations/generate-monthly-report",
        params=params,
        json={"reason": reason},
        # 같은 원본 버전에 대한 재요청은 새 버전을 또 만들지 않고 기존 작업을 돌려준다.
        headers={"Idempotency-Key": key},
    )
    response.raise_for_status()
    return str(response.json()["operation_run_id"])


def _wait_for_run(
    client: httpx.Client, hospital_id: uuid.UUID, run_id: str, timeout: float
) -> tuple[str, str | None, int | None]:
    """작업이 끝날 때까지 기다려 (상태, 단계, 보고서 버전)을 돌려준다. 시간 초과면 TIMEOUT."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/admin/hospitals/{hospital_id}/operations/monthly-report-runs")
        response.raise_for_status()
        for run in response.json():
            if str(run["run_id"]) == run_id and run["state"] in _TERMINAL_RUN_STATES:
                return str(run["state"]), run.get("stage"), run.get("report_version")
        time.sleep(10)
    return "TIMEOUT", None, None


def _run_counts_as_success(
    state: str,
    stage: str | None,
    report_version: int | None,
    *,
    previous_version: int | None,
    allow_recovery_pending: bool,
) -> bool:
    """작업 종료 상태가 '갱신 성공'인가.

    측정이 덜 끝난 병원(`--allow-recovery-pending`)은 새 버전이 만들어져도 월간 작업이 늘
    PARTIAL·BLOCKED(MONTHLY_REPORT_BLOCKED)로 끝난다 — 전달 가능 조건(측정 완료)이 아닐 뿐 갱신은
    성공이다. 새 버전이 실제로 생겼을 때만 인정하고, 원장 PDF 검증 실패 같은 진짜 문제는 이어지는
    사후 확인(검증된 PDF·숫자 대조)이 막는다. FAILED·시간 초과는 어떤 경우에도 성공이 아니다.
    """
    if state == "SUCCEEDED":
        return True
    return (
        allow_recovery_pending
        and state == "PARTIAL"
        and stage == "BLOCKED"
        and previous_version is not None
        and isinstance(report_version, int)
        and report_version > previous_version
    )


def run_execute(
    year: int,
    month: int,
    hospital_ids: Sequence[str],
    *,
    reason: str,
    api_base: str,
    confirm: bool,
    timeout: float,
    allow_recovery_pending: bool = False,
) -> int:
    results = run_precheck(
        year, month, hospital_ids, allow_recovery_pending=allow_recovery_pending
    )
    _print_table("사전 확인", results)
    targets = [result for result in results if result.verdict.passed]
    skipped = [result for result in results if not result.verdict.passed]
    if not confirm:
        print(f"\n--confirm 없이 실행해 요청하지 않았습니다. 대상 {len(targets)}곳, 제외 {len(skipped)}곳.")
        return 0 if not skipped else 1
    key = settings.ADMIN_SECRET_KEY.strip()
    if not key:
        print("ADMIN_SECRET_KEY가 없어 요청하지 않았습니다.", file=sys.stderr)
        return 1
    headers = {"X-Admin-Key": key, "X-Admin-Actor-System": SYSTEM_JOB}
    with httpx.Client(base_url=api_base.rstrip("/"), headers=headers, timeout=30) as client:
        for result in targets:
            # 요청 직전에 한 번 더 확인한다 — 사전 확인 뒤 상태가 바뀌었을 수 있다.
            again = run_precheck(
                year, month, [str(result.hospital_id)],
                allow_recovery_pending=allow_recovery_pending,
            )
            if not again or not again[0].verdict.passed:
                _print_table("요청 직전 재확인 실패 — 중단", again)
                return 1
            run_id = _request_refresh(
                client, again[0], year, month, reason,
                allow_recovery_pending=allow_recovery_pending,
            )
            state, stage, report_version = _wait_for_run(
                client, result.hospital_id, run_id, timeout
            )
            print(f"{result.name}: 작업 {run_id} → {state}" + (f"/{stage}" if stage else ""))
            if not _run_counts_as_success(
                state, stage, report_version,
                previous_version=again[0].version,
                allow_recovery_pending=allow_recovery_pending,
            ):
                print("작업이 성공으로 끝나지 않아 중단합니다.", file=sys.stderr)
                return 1
            post = run_postcheck(year, month, [str(result.hospital_id)])
            _print_table("사후 확인", post)
            if not post or not post[0].verdict.passed:
                print("사후 확인에서 차이가 발견돼 중단합니다.", file=sys.stderr)
                return 1
    return 0 if not skipped else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("precheck", "execute", "postcheck"):
        command = sub.add_parser(name)
        command.add_argument("--year", type=int, required=True)
        command.add_argument("--month", type=int, required=True)
        command.add_argument("--hospital-id", action="append", default=[])
        if name in ("precheck", "execute"):
            # 복구 기간에 측정이 덜 끝난 병원도 지금 저장된 숫자로 갱신한다(운영자 명시).
            command.add_argument("--allow-recovery-pending", action="store_true")
        if name == "execute":
            command.add_argument("--reason", required=True)
            command.add_argument("--api-base", required=True)
            command.add_argument("--confirm", action="store_true")
            command.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "precheck":
            results = run_precheck(
                args.year, args.month, args.hospital_id,
                allow_recovery_pending=args.allow_recovery_pending,
            )
            _print_table("사전 확인", results)
            return 0 if results and all(result.verdict.passed for result in results) else 1
        if args.command == "postcheck":
            results = run_postcheck(args.year, args.month, args.hospital_id)
            _print_table("사후 확인", results)
            return 0 if results and all(result.verdict.passed for result in results) else 1
        if len(args.reason.strip()) < 3:
            print("--reason은 3자 이상이어야 합니다.", file=sys.stderr)
            return 1
        return run_execute(
            args.year,
            args.month,
            args.hospital_id,
            reason=args.reason,
            api_base=args.api_base,
            confirm=args.confirm,
            timeout=args.timeout,
            allow_recovery_pending=args.allow_recovery_pending,
        )
    except MonthlyPeriodError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
