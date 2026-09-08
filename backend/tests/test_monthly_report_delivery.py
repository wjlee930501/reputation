"""H-11/M-02: '측정이 최종인가'를 한 함수가 정한다."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.monthly_report_delivery import (
    coverage_is_final,
    monthly_report_delivery_gate,
)


def _report(quality, adequacy=None, planned=10, success=10, failed=0):
    return SimpleNamespace(
        quality=quality,
        planned_count=planned,
        success_count=success,
        failed_count=failed,
        sov_summary={"observation_adequacy": adequacy} if adequacy else {},
    )


def test_complete_with_full_counts_is_final():
    assert coverage_is_final(_report("COMPLETE")) is True


def test_limited_with_confirmed_slots_is_final_like_the_delivery_gate():
    assert (
        coverage_is_final(_report("DEGRADED", {"status": "LIMITED", "confirmed_slots": 3})) is True
    )


def test_degraded_without_limited_adequacy_is_not_final():
    assert (
        coverage_is_final(_report("DEGRADED", {"status": "UNAVAILABLE", "confirmed_slots": 0}))
        is False
    )
    assert coverage_is_final(_report("COMPLETE", planned=10, success=9)) is False


def _gate_report(quality, adequacy=None, *, manifest_id, hospital_id):
    report = _report(quality, adequacy)
    report.id = uuid.uuid4()
    report.manifest_id = manifest_id
    report.hospital_id = hospital_id
    report.period_year = 2026
    report.period_month = 8
    return report


def _closed_manifest(manifest_id, hospital_id):
    return SimpleNamespace(
        id=manifest_id,
        hospital_id=hospital_id,
        period_year=2026,
        period_month=8,
        closed_at=SimpleNamespace(),
    )


def _gate_code(quality, adequacy=None):
    manifest_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    report = _gate_report(quality, adequacy, manifest_id=manifest_id, hospital_id=hospital_id)
    # 아티팩트는 일부러 없다. 표본 판정을 통과했는지만 코드로 구분한다.
    return monthly_report_delivery_gate(report, _closed_manifest(manifest_id, hospital_id), None)


def test_delivery_gate_coverage_decision_is_unchanged_for_complete_limited_unavailable():
    # COMPLETE·LIMITED는 표본 판정을 통과하고 그 다음 관문(원장용 PDF)에서 멈춘다.
    assert _gate_code("COMPLETE").code == "doctor_artifact_missing"
    assert _gate_code("DEGRADED", {"status": "LIMITED", "confirmed_slots": 3}).code == (
        "doctor_artifact_missing"
    )
    # UNAVAILABLE은 여전히 표본 판정에서 멈춘다.
    assert _gate_code("DEGRADED", {"status": "UNAVAILABLE", "confirmed_slots": 0}).code == (
        "coverage_incomplete"
    )
