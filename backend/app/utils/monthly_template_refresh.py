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
from collections.abc import Sequence
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
from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.monthly_period import MonthlyPeriodError, require_closed_period
from app.services.monthly_report_delivery import monthly_doctor_artifact_is_valid
from app.services.monthly_template_refresh import (
    RefreshVerdict,
    compare_doctor_pdf_facts,
    number_tokens,
    numeric_diff,
)
from app.services.report_file_integrity import ReportFileUnavailable, read_verified_report

SYSTEM_JOB = "monthly-template-refresh"
_CAVEAT = "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다."
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


def precheck_hospital(db, hospital: Hospital, anchor: arrow.Arrow) -> HospitalResult:
    """새 버전을 메모리에서만 만들어 숫자를 비교한다. 호출자가 세션을 롤백한다."""
    from app.workers.tasks import _public_site_url, build_monthly_template_refresh_plan

    plan = build_monthly_template_refresh_plan(
        db, hospital, anchor, observed_now=datetime.now(UTC)
    )
    verdict = plan.verdict
    latest = plan.superseded
    if latest is not None and plan.doctor_view is not None and verdict.status != "BLOCKED":
        old_text = _stored_doctor_text(db, latest, verdict)
        public_url = _public_site_url(hospital.aeo_domain, hospital.slug)
        view = plan.doctor_view
        try:
            rendered = render_validated_doctor_pdf(
                view=view,
                period_label=f"{anchor.year:04d}-{anchor.month:02d}",
                public_url=public_url,
                expectation=DoctorPdfExpectation(
                    hospital_name=str(view["hospital_name"]),
                    coverage_text=str(view["coverage_text"]),
                    caveat_text=_CAVEAT,
                    public_url=public_url,
                    appendix_expected=bool(view.get("appendix_rows")),
                ),
            )
        except DoctorPdfValidationError as exc:
            verdict.add("DIFF", "NEW_DOCTOR_PDF_INVALID", exc.code)
        else:
            if old_text is not None:
                for problem in compare_doctor_pdf_facts(old_text, _pdf_text(rendered.pdf_bytes)):
                    verdict.add("DIFF", "DOCTOR_PDF_NUMBER", problem)
    return HospitalResult(
        hospital.id, hospital.name, latest.version if latest is not None else None, verdict
    )


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
    for prefix, old, new in (
        ("sov_summary", previous.sov_summary, latest.sov_summary),
        ("content_summary", old_content, new_content),
        ("essence_summary", previous.essence_summary, latest.essence_summary),
    ):
        for line in numeric_diff(old, new, prefix=prefix):
            verdict.add("DIFF", "STORED_NUMBER", line)
    if number_tokens(old_points) != number_tokens(new_points):
        verdict.add("DIFF", "TALKING_POINT_NUMBERS")
    old_text = _stored_doctor_text(db, previous, verdict)
    new_text = _stored_doctor_text(db, latest, verdict)
    if old_text is not None and new_text is not None:
        for problem in compare_doctor_pdf_facts(old_text, new_text):
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


def run_precheck(year: int, month: int, hospital_ids: Sequence[str]) -> list[HospitalResult]:
    anchor = _anchor(year, month)
    results: list[HospitalResult] = []
    with SyncSessionLocal() as db:
        for hospital in _target_hospitals(db, year, month, hospital_ids):
            try:
                results.append(precheck_hospital(db, hospital, anchor))
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
    client: httpx.Client, result: HospitalResult, year: int, month: int, reason: str
) -> str:
    response = client.post(
        f"/api/v1/admin/hospitals/{result.hospital_id}/operations/generate-monthly-report",
        params={"year": year, "month": month, "rebuild": "true", "template_only": "true"},
        json={"reason": reason},
        headers={
            # 같은 원본 버전에 대한 재요청은 새 버전을 또 만들지 않고 기존 작업을 돌려준다.
            "Idempotency-Key": (
                f"template-refresh:{result.hospital_id}:{year:04d}-{month:02d}:v{result.version}"
            ),
        },
    )
    response.raise_for_status()
    return str(response.json()["operation_run_id"])


def _wait_for_run(client: httpx.Client, hospital_id: uuid.UUID, run_id: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/admin/hospitals/{hospital_id}/operations/monthly-report-runs")
        response.raise_for_status()
        for run in response.json():
            if str(run["run_id"]) == run_id and run["state"] in _TERMINAL_RUN_STATES:
                return str(run["state"])
        time.sleep(10)
    return "TIMEOUT"


def run_execute(
    year: int,
    month: int,
    hospital_ids: Sequence[str],
    *,
    reason: str,
    api_base: str,
    confirm: bool,
    timeout: float,
) -> int:
    results = run_precheck(year, month, hospital_ids)
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
            again = run_precheck(year, month, [str(result.hospital_id)])
            if not again or not again[0].verdict.passed:
                _print_table("요청 직전 재확인 실패 — 중단", again)
                return 1
            run_id = _request_refresh(client, again[0], year, month, reason)
            state = _wait_for_run(client, result.hospital_id, run_id, timeout)
            print(f"{result.name}: 작업 {run_id} → {state}")
            if state != "SUCCEEDED":
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
        if name == "execute":
            command.add_argument("--reason", required=True)
            command.add_argument("--api-base", required=True)
            command.add_argument("--confirm", action="store_true")
            command.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "precheck":
            results = run_precheck(args.year, args.month, args.hospital_id)
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
        )
    except MonthlyPeriodError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
