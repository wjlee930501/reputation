"""H-11/M-02: '측정이 최종인가'를 한 함수가 정한다."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.monthly_report_delivery import (
    coverage_is_final,
    monthly_report_delivery_blockers,
    monthly_report_delivery_gate,
    monthly_report_delivery_warnings,
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


def test_closed_unavailable_without_confirmed_slots_is_final():
    assert coverage_is_final(
        _report(
            "DEGRADED",
            {
                "status": "UNAVAILABLE",
                "planned_slots": 5,
                "received_answers": 2,
                "confirmed_slots": 0,
                "pending_slots": 4,
                "ambiguous_slots": 1,
                "answer_failed_slots": 1,
                "judgment_failed_slots": 1,
            },
            success=0,
            failed=10,
        )
    ) is True
    assert coverage_is_final(
        _report(
            "BLOCKED",
            {
                "status": "UNAVAILABLE",
                "planned_slots": 0,
                "received_answers": 0,
                "confirmed_slots": 0,
                "ambiguous_slots": 0,
                "answer_failed_slots": 0,
                "judgment_failed_slots": 0,
                "pending_slots": 0,
            },
            planned=0,
            success=0,
            failed=0,
        )
    ) is True
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
    assert _gate_code(
        "DEGRADED", {"status": "UNAVAILABLE", "confirmed_slots": 0}
    ).code == "doctor_artifact_missing"


def test_open_period_is_blocked_before_artifact_delivery():
    manifest_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    report = _gate_report("COMPLETE", manifest_id=manifest_id, hospital_id=hospital_id)
    manifest = _closed_manifest(manifest_id, hospital_id)
    manifest.closed_at = None

    assert monthly_report_delivery_gate(report, manifest, None).code == "manifest_open"


def test_manifest_identity_mismatch_is_blocked_before_artifact_delivery():
    manifest_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    report = _gate_report("COMPLETE", manifest_id=manifest_id, hospital_id=hospital_id)
    manifest = _closed_manifest(manifest_id, uuid.uuid4())

    assert monthly_report_delivery_gate(report, manifest, None).code == "manifest_mismatch"


def test_unavailable_null_headline_is_valid_but_fabricated_zero_is_blocked():
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={
            "sov_pct": None,
            "change_pct": None,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
            "observation_adequacy": {
                "status": "UNAVAILABLE",
                "planned_slots": 5,
                "received_answers": 2,
                "confirmed_slots": 0,
                "pending_slots": 4,
                "ambiguous_slots": 1,
                "answer_failed_slots": 1,
                "judgment_failed_slots": 1,
            },
        },
        content_summary={
            "published_count": 3,
            "operations": {"delivery_blockers": ["legacy shortfall"]},
        },
        essence_summary={},
    )

    assert monthly_report_delivery_blockers(report) == []
    warnings = monthly_report_delivery_warnings(report)
    assert any("산출하지 않았습니다" in warning for warning in warnings)
    assert any("legacy shortfall" in warning for warning in warnings)
    report.sov_summary["sov_pct"] = 0.0
    assert monthly_report_delivery_blockers(report) == [
        "확정된 답변이 없는 달에는 AI 언급률을 0으로 표시할 수 없습니다."
    ]


def test_one_platform_outage_keeps_limited_k_n_without_fabricating_headline():
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={
            "sov_pct": None,
            "change_pct": None,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
            "observation_adequacy": {
                "status": "LIMITED",
                "planned_slots": 10,
                "received_answers": 4,
                "confirmed_slots": 3,
                "ambiguous_slots": 0,
                "answer_failed_slots": 5,
                "judgment_failed_slots": 1,
                "pending_slots": 7,
                "platforms": [
                    {
                        "platform": "chatgpt",
                        "planned_slots": 5,
                        "received_answers": 4,
                        "confirmed_slots": 3,
                        "ambiguous_slots": 0,
                        "answer_failed_slots": 1,
                        "judgment_failed_slots": 1,
                        "pending_slots": 2,
                        "confirmed_mentioned_count": 2,
                        "confirmed_sample_count": 3,
                    },
                    {
                        "platform": "gemini",
                        "planned_slots": 5,
                        "received_answers": 0,
                        "confirmed_slots": 0,
                        "ambiguous_slots": 0,
                        "answer_failed_slots": 4,
                        "judgment_failed_slots": 0,
                        "pending_slots": 5,
                        "confirmed_mentioned_count": 0,
                        "confirmed_sample_count": 0,
                    },
                ],
            },
        },
        content_summary={"published_count": 0, "operations": {}},
        essence_summary={},
    )

    assert monthly_report_delivery_blockers(report) == []
    platforms = report.sov_summary["observation_adequacy"]["platforms"]
    assert platforms[0]["confirmed_sample_count"] == 3
    assert any("제한된 결과" in warning for warning in monthly_report_delivery_warnings(report))


def test_non_comparable_report_cannot_claim_a_delta():
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={
            "sov_pct": 0.0,
            "change_pct": 4.2,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": 4.2},
            "observation_adequacy": {"status": "LIMITED", "confirmed_slots": 2},
        },
        content_summary={"published_count": 1, "operations": {}},
        essence_summary={},
    )

    assert monthly_report_delivery_blockers(report) == [
        "비교할 수 없는 표본에는 전월 대비 증감을 표시할 수 없습니다."
    ]


def test_malformed_overall_or_platform_count_conservation_blocks_delivery():
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={
            "sov_pct": None,
            "change_pct": None,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
            "observation_adequacy": {
                "status": "UNAVAILABLE",
                "planned_slots": 5,
                "received_answers": 2,
                "confirmed_slots": 0,
                "ambiguous_slots": 1,
                "answer_failed_slots": 2,
                "judgment_failed_slots": 1,
                "pending_slots": 2,
            },
        },
        content_summary={"published_count": 0, "operations": {}},
        essence_summary={},
    )
    message = "월간 측정 가용성 건수의 합계가 계획 표본과 일치하지 않습니다."
    assert message in monthly_report_delivery_blockers(report)

    report.sov_summary["observation_adequacy"] = {
        "status": "UNAVAILABLE",
        "planned_slots": 5,
        "received_answers": 2,
        "confirmed_slots": 0,
        "ambiguous_slots": 1,
        "answer_failed_slots": 2,
        "judgment_failed_slots": 1,
        "pending_slots": 4,
        "platforms": [
            {
                "platform": "chatgpt",
                "planned_slots": 4,
                "received_answers": 2,
                "confirmed_slots": 0,
                "ambiguous_slots": 1,
                "answer_failed_slots": 1,
                "judgment_failed_slots": 1,
                "pending_slots": 1,
                "confirmed_mentioned_count": 0,
                "confirmed_sample_count": 0,
            }
        ],
    }
    assert message in monthly_report_delivery_blockers(report)


def test_unknown_or_misleading_availability_claim_is_blocked():
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        sov_summary={
            "sov_pct": None,
            "change_pct": None,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
            "observation_adequacy": {"status": "UNKNOWN", "confirmed_slots": 0},
        },
        content_summary={"published_count": 0, "operations": {}},
        essence_summary={},
    )

    assert monthly_report_delivery_blockers(report) == [
        "지원하지 않는 월간 측정 가용성 상태입니다."
    ]
    report.sov_summary["observation_adequacy"] = {
        "status": "UNAVAILABLE",
        "confirmed_slots": 1,
    }
    assert monthly_report_delivery_blockers(report) == [
        "측정 불가 상태의 확정 답변 수가 0이 아닙니다."
    ]


def test_delivery_blockers_ignore_source_stale_audit_flag():
    from app.services.monthly_report_delivery import monthly_report_delivery_blockers

    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/demo.pdf",
        doctor_pdf_path="gs://reputation-reports/demo_doctor.pdf",
        sov_summary={
            "sov_pct": 42.0,
            "observation_adequacy": {"status": "COMPLETE", "confirmed_slots": 10},
        },
        content_summary={
            "published_count": 8,
            "operations": {"delivery_blockers": []},
        },
        essence_summary={
            "approved_philosophy_exists": True,
            "source_count": 4,
            "processed_source_count": 4,
            "source_stale": True,
            "needs_review_content_count": 0,
            "missing_philosophy_content_count": 0,
            "medical_risk_findings": [],
        },
    )
    blockers = monthly_report_delivery_blockers(report)
    assert blockers == []
    assert not any("일치하지 않습니다" in b for b in blockers)
