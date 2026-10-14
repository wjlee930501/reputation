"""W8 '보고서는 언제나 나온다' — 순수 계약(가상 기록만 쓴다).

- 마감이 지난 옛(slot 계약 없는) 매니페스트는 셀 단위로 LIMITED/UNAVAILABLE로 닫힌다(신기한·장앤김 9월).
- V0 PDF는 V0 계약으로 판정한다 — 월간 원장 계약을 V0에 적용한 것이 'hash 불일치' 오탐의 원인이었다.
- 매니페스트 없는 달은 '기준 설정' 보고서로 전달된다(D38).
- 10일이 지나도 전달 기록이 없는 준비본은 일일 요약 한 줄이다(D39).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import monthly_report_delivery as delivery
from app.services.fleet_heartbeat import (
    FleetFacts,
    build_fleet_heartbeat,
    count_undelivered_ready_reports,
)
from app.services.measurement_manifest_policy import (
    legacy_cells_final_adequacy,
    legacy_manifest_is_final,
    manifest_observation_adequacy,
)
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import _legacy_final_attempt, _legacy_final_counts
from app.services.monthly_sov_types import (
    LEGACY_FINAL_LINEAGE,
    CellAttempt,
    ManifestCellInput,
)
from app.services.pipeline_watchdog import WatchdogReport
from app.services.report_artifact_validation import (
    validate_persisted_doctor_artifact,
    validate_persisted_report_artifact,
)


def _legacy_manifest(*states: str) -> SimpleNamespace:
    return SimpleNamespace(
        platform_provenance={"chatgpt": "ALWAYS", "measurement_protocol": {}},
        cells=[SimpleNamespace(state=state, observation_slots=[]) for state in states],
    )


# ── 옛 매니페스트의 마감 판정 ──────────────────────────────────────────────


def test_incomplete_legacy_manifest_closes_as_limited_after_the_deadline() -> None:
    manifest = _legacy_manifest("SUCCESS", "SUCCESS", "FAILED", "EXCLUDED")

    # 마감 전에는 종전처럼 판정하지 않는다(1~7일 catch-up이 실패 셀을 다시 산다).
    assert manifest_observation_adequacy(manifest, deadline_reached=False) is None
    adequacy = manifest_observation_adequacy(manifest, deadline_reached=True)

    assert adequacy is not None
    assert adequacy.status == "LIMITED"
    assert adequacy.lineage == LEGACY_FINAL_LINEAGE
    assert (adequacy.planned_slots, adequacy.confirmed_slots, adequacy.pending_slots) == (3, 2, 1)
    assert legacy_manifest_is_final(manifest, deadline_reached=True)
    assert not legacy_manifest_is_final(manifest, deadline_reached=False)


def test_legacy_manifest_without_any_answer_closes_as_unavailable() -> None:
    adequacy = legacy_cells_final_adequacy(_legacy_manifest("FAILED", "FAILED"))
    assert adequacy is not None and adequacy.status == "UNAVAILABLE"
    assert adequacy.confirmed_slots == 0


def test_complete_legacy_and_slotted_manifests_keep_their_old_judgment() -> None:
    complete = _legacy_manifest("SUCCESS", "SUCCESS")
    assert legacy_cells_final_adequacy(complete) is None
    assert not legacy_manifest_is_final(complete, deadline_reached=True)
    slotted = SimpleNamespace(
        platform_provenance={"observation_slots": {"repeat_count": 5, "version": 1}},
        cells=[SimpleNamespace(state="FAILED", observation_slots=[])],
    )
    assert not legacy_manifest_is_final(slotted, deadline_reached=True)


def _cell(
    platform: str, state: str, successes: int, *, mentioned: int = 0, first_mentioned: bool = True
) -> ManifestCellInput:
    """옛 셀 — 성공 시도 여러 건이 남아 있을 수 있다. 로더와 같은 규칙으로 표본 한 건을 고른다."""
    base = datetime(2026, 9, 20, tzinfo=UTC)
    attempts = tuple(
        CellAttempt(
            record_id=uuid.uuid4(),
            measured_at=base + timedelta(minutes=index),
            succeeded=True,
            is_mentioned=(index < mentioned) if first_mentioned else (index >= successes - mentioned),
        )
        for index in range(successes)
    )
    cell = ManifestCellInput(
        query_key=f"{platform}:{uuid.uuid4().hex[:6]}",
        query_text="가상동 내과 추천",
        platform=platform,
        query_intent="LOCAL",
        state=state,
        query_matrix_id=None,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=attempts,
    )
    chosen = _legacy_final_attempt(state, attempts)
    return replace(cell, attempts=chosen, **_legacy_final_counts(chosen))


def _freeze(payload: dict, cells) -> dict:
    from app.workers.tasks import _freeze_platform_availability

    return _freeze_platform_availability(payload, cells)


@pytest.mark.parametrize(
    "cells,status",
    [
        (
            (
                _cell("chatgpt", "SUCCESS", 1, mentioned=1),
                _cell("chatgpt", "FAILED", 0),
                _cell("gemini", "SUCCESS", 2),
            ),
            "LIMITED",
        ),
        ((_cell("chatgpt", "FAILED", 0), _cell("gemini", "FAILED", 0)), "UNAVAILABLE"),
    ],
)
def test_legacy_final_counts_pass_the_delivery_contract(cells, status) -> None:
    summary = build_monthly_sov(cells, ("chatgpt", "gemini"))
    payload = _freeze(summary.to_payload(), cells)
    adequacy = payload["observation_adequacy"]

    assert adequacy["status"] == status
    assert adequacy["lineage"] == LEGACY_FINAL_LINEAGE
    # 합계 검증(전달 게이트·커버리지 판정이 쓰는 것)을 그대로 통과해야 전달 가능하다.
    assert delivery._availability_counts_are_consistent(adequacy)
    assert delivery._availability_platforms_are_consistent(adequacy)
    report = SimpleNamespace(
        quality="DEGRADED" if status == "LIMITED" else "BLOCKED",
        planned_count=len(cells),
        success_count=sum(cell.state == "SUCCESS" for cell in cells),
        failed_count=sum(cell.state == "FAILED" for cell in cells),
        sov_summary=payload,
    )
    assert delivery.coverage_is_final(report)
    if status == "UNAVAILABLE":
        # null은 0이 아니다 — 확인된 답이 없는 달에 언급률을 0으로 쓰지 않는다.
        assert payload["sov_pct"] is None


def test_legacy_sample_is_one_unbiased_answer_per_cell() -> None:
    """반복이 많은 셀이 비율을 끌어올리지 않는다(Codex P1 재현: 5답 언급 셀 + 1답 미언급 셀 + 실패 셀)."""
    cells = (
        _cell("chatgpt", "SUCCESS", 5, mentioned=5),
        _cell("chatgpt", "SUCCESS", 1, mentioned=0),
        _cell("chatgpt", "FAILED", 0),
    )
    summary = build_monthly_sov(cells, ("chatgpt",))
    payload = _freeze(summary.to_payload(), cells)
    adequacy = payload["observation_adequacy"]

    assert (adequacy["planned_slots"], adequacy["confirmed_slots"]) == (3, 2)
    assert delivery._availability_platforms_are_consistent(adequacy)
    assert summary.sov_pct == 50.0
    chatgpt = next(row for row in payload["platforms"] if row["platform"] == "chatgpt")
    assert (chatgpt["confirmed_sample_count"], chatgpt["confirmed_mentioned_count"]) == (2, 1)
    assert manifest_observation_adequacy(
        _legacy_manifest("SUCCESS", "SUCCESS", "FAILED"), deadline_reached=True
    ).confirmed_slots == 2


def test_legacy_sample_choice_does_not_prefer_a_mentioned_answer() -> None:
    # 가장 이른 답이 미언급이면 뒤의 언급 답이 있어도 미언급이다 — 대표 사례 규칙(언급 우선)과 다르다.
    [answer] = _cell("chatgpt", "SUCCESS", 3, mentioned=2, first_mentioned=False).attempts
    assert answer.is_mentioned is False


# ── V0 PDF는 V0 계약으로 ─────────────────────────────────────────────────


def _v0(sha: str = "a" * 64, size: int = 24796):
    report_id = uuid.uuid4()
    path = "gs://bucket/reports/clinic/clinic_V0-진단.pdf"
    report = SimpleNamespace(
        id=report_id, report_type="V0", doctor_pdf_path=path, pdf_path=path
    )
    artifact = SimpleNamespace(
        report_id=report_id,
        audience="DOCTOR",
        path=path,
        validated=True,
        sha256=sha,
        byte_size=size,
        validation_metadata={
            "validation_version": "v0-pdf-v1",
            "validation_source": "SYSTEM",
            "sha256": sha,
            "byte_size": size,
        },
    )
    return report, artifact


def test_v0_pdf_is_valid_under_its_own_contract_not_the_monthly_one() -> None:
    report, artifact = _v0()

    # 2026-10-08 이전 대조 작업이 쓴 판정: V0 메타데이터를 월간 원장 계약으로 읽어 늘 '불일치'였다.
    assert validate_persisted_doctor_artifact(report, artifact).state == "INVALID"
    assert validate_persisted_report_artifact(report, artifact).state == "VALID"


def test_a_tampered_v0_identity_is_still_invalid() -> None:
    report, artifact = _v0()
    artifact.validation_metadata = {**artifact.validation_metadata, "sha256": "b" * 64}
    assert validate_persisted_report_artifact(report, artifact).state == "INVALID"
    assert validate_persisted_report_artifact(report, None).state == "MISSING"


# ── D38 '기준 설정' ───────────────────────────────────────────────────────


def _baseline_report(**overrides):
    payload = build_monthly_sov((), ()).to_payload()
    payload["report_kind"] = delivery.BASELINE_REPORT_KIND
    values = dict(
        report_type="MONTHLY",
        manifest_id=None,
        sov_summary=payload,
        pdf_path="gs://bucket/ae.pdf",
        content_summary={"published_count": 3, "operations": {}},
        essence_summary={},
        delivery_blockers=[],
        quality="BLOCKED",
        planned_count=0,
        success_count=0,
        failed_count=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_baseline_month_is_deliverable_without_a_manifest(monkeypatch) -> None:
    monkeypatch.setattr(delivery, "monthly_doctor_artifact_is_valid", lambda *_args: True)
    report = _baseline_report()

    assert delivery.report_is_baseline_setup(report)
    assert delivery.monthly_report_delivery_gate(report, None, object()).ready
    # 원장 PDF가 없으면 기준 설정 보고서도 전달하지 않는다.
    assert delivery.monthly_report_delivery_gate(report, None, None).code == (
        "doctor_artifact_missing"
    )


def test_baseline_marker_is_server_owned_and_monthly_only(monkeypatch) -> None:
    monkeypatch.setattr(delivery, "monthly_doctor_artifact_is_valid", lambda *_args: True)
    unmarked = _baseline_report(sov_summary=build_monthly_sov((), ()).to_payload())
    assert not delivery.report_is_baseline_setup(unmarked)
    assert not delivery.monthly_report_delivery_gate(unmarked, None, object()).ready
    with_manifest = _baseline_report(manifest_id=uuid.uuid4())
    assert not delivery.report_is_baseline_setup(with_manifest)


def test_baseline_warning_never_calls_the_month_unmeasured_or_zero() -> None:
    warnings = delivery.monthly_report_delivery_warnings(_baseline_report())
    text = " ".join(warnings)
    assert "기준 설정" in text
    assert "측정 없음" not in text
    assert "확정 가능한 측정 표본을 확보하지 못해" not in text


def test_baseline_doctor_view_says_baseline_not_unmeasured() -> None:
    from test_report_plain_language import _body_text, _html, _view

    view = _view("baseline")
    narrative = view["narrative"]
    text = _body_text(_html(view))

    assert narrative.title == "기준 설정 보고서"
    # 이번 달은 측정이 없다 — 다음 달이 첫 측정이고 그것이 기준점이다(이번 달을 기준으로 비교하지 않는다).
    assert "다음 달 첫 측정으로 기준점을 만들겠습니다" in narrative.conclusion
    assert "이번 달을 기준으로" not in text
    assert narrative.current is None and narrative.current_label == "기준 설정"
    assert "기준 설정" in text
    for banned in ("측정 없음", "측정 못 함", "확인 못 함", "0.0%"):
        assert banned not in text, banned


# ── D39 전달 기록 없는 준비본 ─────────────────────────────────────────────

_NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _watchdog() -> WatchdogReport:
    return WatchdogReport(
        observed_at=_NOW,
        kst_date="2026-11-11",
        redis_available=True,
        database_available=True,
        queue_canaries_current=True,
        stale_queues=(),
        stale_critical_queues=(),
        beat_alive=True,
        beat_lock_held=True,
        beat_last_schedule_run_at=_NOW,
        beat_evidence="fresh",
        publish_checked=True,
        publish_due_remaining=0,
        publish_published_today=3,
        publish_missing=False,
        publish_gate_residual=False,
        publish_blocked_today=(),
        publish_partial=False,
        last_generation_batch_at=_NOW,
        generation_batch_stale=False,
    )


def test_daily_summary_has_one_line_for_ready_but_undelivered_reports() -> None:
    facts = FleetFacts(9, 0, 0, 0, 9, 9, undelivered_ready_reports=2)
    intent = build_fleet_heartbeat(
        _watchdog(), facts, now=_NOW, admin_base_url="http://localhost:3000"
    )
    text = intent.message.fallback_text
    assert "지난달 원장 보고서 2건이 준비됐지만 10일이 지나도록 전달 기록이 없습니다" in text
    assert len(text) <= 1000
    quiet = build_fleet_heartbeat(
        _watchdog(), FleetFacts(9, 0, 0, 0, 9, 9), now=_NOW, admin_base_url="http://localhost:3000"
    )
    assert "전달 기록이 없습니다" not in quiet.message.fallback_text


def test_undelivered_reports_are_not_counted_through_the_tenth() -> None:
    # KST 11/10 18:00 — 아직 셀 때가 아니므로 DB를 읽지도 않는다.
    assert count_undelivered_ready_reports(object(), now=datetime(2026, 11, 10, 9, tzinfo=UTC)) == 0


def test_setup_report_survives_the_template_refresh_snapshot() -> None:
    """템플릿 갱신은 저장된 뷰를 되살려 다시 그린다 — '기준 설정'이 월간 문구로 바뀌지 않는다."""
    from test_report_plain_language import _view

    from app.services.monthly_report_snapshot import freeze_doctor_view, restore_doctor_view
    from app.services.monthly_template_refresh import doctor_view_facts

    view = _view("baseline")
    restored = restore_doctor_view(freeze_doctor_view(view))

    assert restored["report_kind"] == "SETUP"
    assert restored["narrative"].title == "기준 설정 보고서"
    assert doctor_view_facts(restored) == doctor_view_facts(view)


def test_setup_report_never_fills_the_question_table_from_last_month() -> None:
    from test_report_plain_language import _attribution
    from test_report_redesign import monthly_view

    view = monthly_view(
        sov_pct=None,
        prev_sov_pct=None,
        report_kind="SETUP",
        attribution=_attribution(prior=True),
        citations={"measured_cell_count": 0, "cited_items": []},
    )

    assert view["appendix_rows"] == []
    assert (view.get("highlights") or {}).get("measured_questions") is None
