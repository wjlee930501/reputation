"""경쟁 병원 반영(경쟁사 재판정 → 경쟁 현황 부분 재생성) 운영 명령 — `monthly_template_refresh`와 같은 모양.

    python -m app.utils.monthly_competitor_refresh precheck  --year 2026 --month 10 [--hospital-id ID ...]
    python -m app.utils.monthly_competitor_refresh execute   --year 2026 --month 10 --api-base https://api.example \\
        [--reason "..."] [--hospital-id ID ...] [--confirm] [--timeout 1800] [--retry-suffix retry2]
    python -m app.utils.monthly_competitor_refresh postcheck --year 2026 --month 10 [--hospital-id ID ...]

- precheck: 읽기 전용(세션 롤백, 쓰기·공급자 호출 없음). 병원마다 등록 경쟁사·그 달 보고서가 쓴 답변 수·아직
  판정이 없는 답변 수를 세고, 경쟁 현황만 다시 계산한 새 버전이 **경쟁 사실을 뺀** 모든 숫자를 그대로 지키는지
  (`COMPETITOR_REFRESH` 판정)와 새 원장 PDF가 그 숫자를 찍는지 본다. 등록 경쟁사가 없는 병원은 BLOCKED다.
- execute: PASS인 병원만 한 곳씩 Admin API(`POST …/reports/competitors/refresh`)로 요청하고(`X-Admin-Actor-System`),
  재판정 실행과 그 자식 보고서 실행이 끝날 때까지 기다린 뒤 postcheck를 돌린다. 하나라도 어긋나면 멈춘다.
  `--confirm`이 없으면 대상만 출력한다.
- postcheck: 최신 버전과 그것이 대체한 버전을 비교한다 — 경쟁 사실·전략 요약의 경쟁 집계만 달라야 하고, 나머지
  숫자는 같아야 하며, 새 원장 PDF가 검증돼 있고 저장 숫자 사실을 찍어야 한다.

마감(1일 07:00 KST)이 지난 달도 대상이다 — 답변을 새로 사지 않고 저장된 답변 원문에 대한 경쟁사 판정만
다시 하므로 새 유료 측정 단계가 아니다(`services/competitor_rejudge.py`).

종료 코드: 모두 PASS면 0, 아니면 1.
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.services.competitor_rejudge import build_rejudge_plan, registered_competitor_names
from app.services.doctor_pdf_contracts import DoctorPdfValidationError
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import (
    compare_facts_excluding_competitors,
    competitor_fact_changes,
    is_competitor_fact,
)
from app.services.monthly_period import MonthlyPeriodError
from app.services.monthly_report_snapshot import report_render_inputs
from app.services.monthly_template_refresh import (
    RefreshVerdict,
    doctor_view_facts,
    facts_are_v4,
    numeric_diff,
    pdf_fact_problems,
    stored_doctor_view_is_v4,
)
from app.utils.monthly_template_refresh import (
    _anchor,
    _pdf_text,
    _print_table,
    _stored_doctor_text,
    _target_hospitals,
    _without_doctor_view,
)

SYSTEM_JOB = "monthly-competitor-refresh"
DEFAULT_REASON = "경쟁 병원 등록 반영(측정 숫자 변경 없음)"
_TERMINAL_RUN_STATES = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}


@dataclass(frozen=True, slots=True)
class HospitalResult:
    hospital_id: uuid.UUID
    name: str
    version: int | None
    verdict: RefreshVerdict
    registered: int = 0
    records_total: int = 0
    pending_records: int = 0


def _latest_report(db, hospital_id: uuid.UUID, year: int, month: int) -> MonthlyReport | None:
    from sqlalchemy import select

    return db.execute(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital_id,
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def _without_competitor_outcomes(content_summary: dict) -> dict:
    """전략 요약의 측정 키워드별 `competitor_outcomes`를 뺀 content_summary — 경쟁 병원 반영이 바꾸는 유일한 숫자 칸."""
    content = copy.deepcopy(content_summary)
    for holder in (
        content.get("strategy"),
        ((content.get("report_snapshot") or {}).get("render_inputs") or {}).get("ae_report", {}).get("strategy")
        if isinstance(content.get("report_snapshot"), dict)
        else None,
    ):
        if not isinstance(holder, dict):
            continue
        for row in holder.get("query_targets") or []:
            if isinstance(row, dict):
                row.pop("competitor_outcomes", None)
    return content


def precheck_hospital(db, hospital: Hospital, anchor) -> HospitalResult:
    """새 버전을 메모리에서만 만들어 경쟁 사실을 뺀 숫자가 그대로인지 본다. 호출자가 세션을 롤백한다."""
    from app.workers.tasks import _public_site_url, build_monthly_template_refresh_plan

    rejudge = build_rejudge_plan(db, hospital, anchor.year, anchor.month)
    verdict = RefreshVerdict()
    if not rejudge.registered_names:
        verdict.add("BLOCKER", "NO_REGISTERED_COMPETITORS", "병원 정보 탭에 경쟁 병원을 먼저 등록")
    plan = build_monthly_template_refresh_plan(
        db, hospital, anchor, observed_now=datetime.now(UTC), competitor_refresh=True
    )
    for finding in plan.verdict.findings:
        verdict.add(finding.kind, finding.code, finding.detail)
    latest = plan.superseded
    if latest is not None and plan.doctor_view is not None and verdict.status != "BLOCKED":
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
            for problem in pdf_fact_problems(_pdf_text(rendered.pdf_bytes), doctor_view_facts(view)):
                verdict.add("DIFF", "DOCTOR_PDF_NUMBER", problem)
    return HospitalResult(
        hospital.id,
        hospital.name,
        latest.version if latest is not None else None,
        verdict,
        registered=len(rejudge.registered_names),
        records_total=rejudge.records_total,
        pending_records=rejudge.pending_records,
    )


def postcheck_hospital(db, hospital: Hospital, year: int, month: int) -> HospitalResult:
    """최신 버전과 대체한 버전 — 경쟁 사실·경쟁 집계만 다르고 나머지 숫자는 같은지, 새 원장 PDF가 유효한지."""
    verdict = RefreshVerdict()
    latest = _latest_report(db, hospital.id, year, month)
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
    old_content = _without_competitor_outcomes(_without_doctor_view(dict(previous.content_summary or {})))
    new_content = _without_competitor_outcomes(_without_doctor_view(dict(latest.content_summary or {})))
    old_content.pop("talking_points", None)
    new_content.pop("talking_points", None)
    for prefix, old, new in (
        ("sov_summary", previous.sov_summary, latest.sov_summary),
        ("content_summary", old_content, new_content),
        ("essence_summary", previous.essence_summary, latest.essence_summary),
    ):
        for line in numeric_diff(old, new, prefix=prefix):
            verdict.add("DIFF", "STORED_NUMBER", line)
    old_inputs = report_render_inputs(previous.content_summary or {})
    new_inputs = report_render_inputs(latest.content_summary or {})
    if new_inputs is None or not facts_are_v4(new_inputs.doctor_metric_facts):
        verdict.add("BLOCKER", "NEW_VERSION_NOT_V4")
        return HospitalResult(hospital.id, hospital.name, latest.version, verdict)
    new_facts = dict(new_inputs.doctor_metric_facts)
    if old_inputs is not None and stored_doctor_view_is_v4(previous.content_summary or {}):
        old_facts = dict(old_inputs.doctor_metric_facts)
        for problem in compare_facts_excluding_competitors(old_facts, new_facts):
            verdict.add("DIFF", "DOCTOR_VIEW_NUMBER", problem)
        for change in competitor_fact_changes(old_facts, new_facts):
            verdict.add("WARN", "COMPETITOR_FACTS_RECOMPUTED", change)
    else:
        verdict.add("WARN", "V3_TO_V4_UPGRADE")
    registered = registered_competitor_names(hospital)
    shown = {key[len("competitor."):] for key in new_facts if is_competitor_fact(key) and not key.endswith(".rate")}
    for name in registered:
        if name not in shown:
            verdict.add("WARN", "COMPETITOR_NOT_SHOWN", f"{name}: 확정 판정이 없어 경쟁 현황에 행이 없음")
    _stored_doctor_text(db, previous, verdict)
    new_text = _stored_doctor_text(db, latest, verdict)
    if new_text is not None:
        for problem in pdf_fact_problems(new_text, new_facts):
            verdict.add("DIFF", "DOCTOR_PDF_NUMBER", problem)
    return HospitalResult(
        hospital.id, hospital.name, latest.version, verdict,
        registered=len(registered), records_total=0, pending_records=0,
    )


def _print_results(title: str, results: Sequence[HospitalResult]) -> None:
    _print_table(title, results)
    for result in results:
        print(
            f"           등록 경쟁사 {result.registered}곳 · 답변 {result.records_total}건 · "
            f"판정 없는 답변 {result.pending_records}건  ({result.name})"
        )


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
    client: httpx.Client, result: HospitalResult, year: int, month: int, reason: str, *, retry_suffix: str = ""
) -> str:
    """요청 키는 대체할 버전에 묶인다 — 같은 원본 버전으로 다시 실행해도 체인이 두 번 생기지 않는다. 끝난 실행(비용 보류·
    실패)을 같은 버전에서 다시 시도하려면 `--retry-suffix`로 키를 바꾼다(새 사유 감사·새 체인)."""
    key = f"competitor-refresh:{result.hospital_id}:{year:04d}-{month:02d}:v{result.version}"
    if retry_suffix:
        key += f":{retry_suffix}"
    response = client.post(
        f"/api/v1/admin/hospitals/{result.hospital_id}/reports/competitors/refresh",
        params={"year": year, "month": month},
        json={"reason": reason},
        headers={"Idempotency-Key": key},
    )
    response.raise_for_status()
    return str(response.json()["operation_run_id"])


def _wait_for_chain(
    client: httpx.Client, hospital_id: uuid.UUID, run_id: str, year: int, month: int, timeout: float
) -> tuple[str, dict | None]:
    """재판정 실행과 자식 보고서 실행이 모두 끝날 때까지 기다려 (재판정 상태, 보고서 실행)을 돌려준다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/admin/hospitals/{hospital_id}/reports/competitors/refresh",
            params={"year": year, "month": month},
        )
        response.raise_for_status()
        latest = response.json().get("latest_run") or {}
        if str(latest.get("run_id")) == run_id and latest.get("state") in _TERMINAL_RUN_STATES:
            report_run = latest.get("report_run")
            if latest.get("state") != "SUCCEEDED":
                return str(latest["state"]), report_run
            if report_run and report_run.get("state") in _TERMINAL_RUN_STATES:
                return "SUCCEEDED", report_run
        time.sleep(10)
    return "TIMEOUT", None


def _chain_succeeded(state: str, report_run: dict | None, *, previous_version: int | None) -> bool:
    if state != "SUCCEEDED" or not report_run:
        return False
    version = report_run.get("report_version")
    return (
        report_run.get("state") == "SUCCEEDED"
        and isinstance(version, int)
        and (previous_version is None or version > previous_version)
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
    retry_suffix: str = "",
) -> int:
    results = run_precheck(year, month, hospital_ids)
    _print_results("사전 확인", results)
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
            again = run_precheck(year, month, [str(result.hospital_id)])
            if not again or not again[0].verdict.passed:
                _print_results("요청 직전 재확인 실패 — 중단", again)
                return 1
            run_id = _request_refresh(client, again[0], year, month, reason, retry_suffix=retry_suffix)
            state, report_run = _wait_for_chain(client, result.hospital_id, run_id, year, month, timeout)
            print(
                f"{result.name}: 재판정 {run_id} → {state}"
                + (f" / 보고서 {report_run.get('state')} v{report_run.get('report_version')}" if report_run else "")
            )
            if not _chain_succeeded(state, report_run, previous_version=again[0].version):
                print("체인이 성공으로 끝나지 않아 중단합니다.", file=sys.stderr)
                return 1
            post = run_postcheck(year, month, [str(result.hospital_id)])
            _print_results("사후 확인", post)
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
            command.add_argument("--reason", default=DEFAULT_REASON)
            command.add_argument("--api-base", required=True)
            command.add_argument("--confirm", action="store_true")
            command.add_argument("--timeout", type=float, default=1800.0)
            # 같은 버전에서 끝난 실행(비용 보류·실패) 뒤 다시 시도할 때 요청 키를 바꾼다(예: retry2).
            command.add_argument("--retry-suffix", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "precheck":
            results = run_precheck(args.year, args.month, args.hospital_id)
            _print_results("사전 확인", results)
            return 0 if results and all(result.verdict.passed for result in results) else 1
        if args.command == "postcheck":
            results = run_postcheck(args.year, args.month, args.hospital_id)
            _print_results("사후 확인", results)
            return 0 if results and all(result.verdict.passed for result in results) else 1
        if len(args.reason.strip()) < 3:
            print("--reason은 3자 이상이어야 합니다.", file=sys.stderr)
            return 1
        return run_execute(
            args.year, args.month, args.hospital_id,
            reason=args.reason, api_base=args.api_base, confirm=args.confirm, timeout=args.timeout,
            retry_suffix=args.retry_suffix.strip(),
        )
    except MonthlyPeriodError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
