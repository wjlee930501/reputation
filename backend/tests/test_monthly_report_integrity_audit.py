"""Reproduced report defects: frozen evidence, canonical repeats, honest copy."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_doctor_report_view import _attribution, _question_row, _record, _view
from test_monthly_sov_comparability import _cell
from test_monthly_sov_repository import _cell_with_attempts, _load

from app.services.monthly_report_delivery import coverage_is_final
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import MonthlySovDataError
from app.services.report_engine import _pick_evidence, _query_text_of


def test_evidence_uses_the_frozen_question_without_mutating_current_orm():
    cell = _cell_with_attempts()
    for attempt in cell.attempts:
        attempt.sov_record.query = SimpleNamespace(query_text="수정된 현재 질문")
    result = _load(cell, live_intent="LOCAL", snapshots={cell.query_key: "LOCAL"})
    assert _query_text_of(result.selected_records[0]) == "강남 환자 질문"
    assert cell.attempts[-1].sov_record.query.query_text == "수정된 현재 질문"


def test_only_the_current_canonical_repeat_is_scored_not_earlier_retry_links():
    cell = _cell_with_attempts()
    current = cell.attempts[1].sov_record  # confirmed non-mention; stale retry mentioned
    cell.observation_slots = [
        SimpleNamespace(
            repeat_no=1,
            answer_status="RECEIVED",
            judgment_status="CONFIRMED",
            sov_record_id=current.id,
        )
    ]
    result = _load(cell, live_intent="LOCAL", snapshots={cell.query_key: "LOCAL"})
    assert result.cells[0].attempts_used == 1
    assert result.cells[0].mentioned_attempts == 0
    assert [record.id for record in result.scored_records] == [current.id]


def test_confirmed_slot_without_its_exact_link_is_a_data_error():
    cell = _cell_with_attempts()
    cell.observation_slots = [
        SimpleNamespace(
            repeat_no=1,
            answer_status="RECEIVED",
            judgment_status="CONFIRMED",
            sov_record_id=uuid4(),
        )
    ]
    with pytest.raises(MonthlySovDataError):
        _load(cell, live_intent="LOCAL", snapshots={cell.query_key: "LOCAL"})


def test_preexcluded_cells_are_not_part_of_the_required_observation_denominator():
    good = replace(
        _cell("a", "chatgpt", mentioned=True),
        slot_lineage="SLOTTED",
        planned_repeat_count=1,
        confirmed_slot_count=1,
        received_answer_count=1,
    )
    excluded = replace(
        _cell("b", "chatgpt", state="EXCLUDED"),
        slot_lineage="SLOTTED",
        planned_repeat_count=5,
        pending_slot_count=5,
    )
    summary = build_monthly_sov((good, excluded), ("chatgpt",))
    assert summary.observation_adequacy["status"] == "COMPLETE"
    assert summary.observation_adequacy["planned_slots"] == 1
    assert summary.excluded_count == 1


def test_complete_label_cannot_override_persisted_partial_observations():
    report = SimpleNamespace(
        quality="COMPLETE",
        planned_count=2,
        success_count=2,
        failed_count=0,
        sov_summary={
            "observation_adequacy": {
                "status": "LIMITED",
                "planned_slots": 10,
                "confirmed_slots": 1,
                "pending_slots": 9,
            }
        },
    )
    assert not coverage_is_final(report)


def test_wilson_interval_is_not_falsely_drawn_as_symmetric_about_the_estimate():
    view = _view(
        sov_pct=0.0,
        prev_sov_pct=None,
        records=[],
        attribution=_attribution(),
        sov_coverage={
            "planned_count": 2,
            "success_count": 2,
            "attempts_used": 10,
            "ci95_low": 0.0,
            "ci95_high": 27.75,
            "margin_of_hundred": 14,
            "measurement_basis": {
                "question_count": 1,
                "platform_count": 2,
                "cell_count": 2,
                "repeat_count": 5,
                "attempts_used": 10,
            },
        },
    )
    note = " ".join(view["footnotes"])
    assert "±" not in note
    assert "0.0~27.8" in note and "독립" in note
    assert "환산" in view["summary"]


@pytest.mark.parametrize("verdict", ["SIGNIFICANT_UP", "SIGNIFICANT_DOWN", "WITHIN_NOISE"])
def test_independent_trial_heuristic_never_becomes_a_customer_significance_claim(verdict):
    view = _view(significance=verdict)
    assert "의미 있는" not in view["headline"]["delta_sentence"]
    assert "정상 변동" not in view["headline"]["delta_sentence"]


def test_appendix_does_not_silently_drop_sixteenth_question():
    questions = [f"정확한 지역 질문 {i:02d}" for i in range(31)]
    view = _view(
        records=[],
        attribution=_attribution(
            question_rows=[_question_row(str(i), text) for i, text in enumerate(questions)]
        ),
    )
    assert [row["query_text"] for row in view["appendix_rows"]] == questions


def test_appendix_preserves_the_full_question_and_zero_observation_denominator():
    text = "진료 전에 확인할 매우 구체적인 조건 " * 10 + "마지막 구분 조건"
    view = _view(
        records=[],
        attribution=_attribution(question_rows=[_question_row("long", text, current=(10, 0))]),
    )
    assert view["appendix_rows"][0]["query_text"] == text
    assert view["appendix_rows"][0]["current_label"] == "10번 중 0번"


def test_evidence_dates_are_presented_in_korean_local_time():
    record = _record(mentioned=True)
    record.measured_at = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
    evidence = _pick_evidence([record], "검증 의원")
    assert evidence["found"]["measured_at"].date().isoformat() == "2026-09-01"


def test_partial_observations_are_visible_in_the_customer_coverage_sentence():
    view = _view(
        records=[],
        sov_coverage={
            "planned_count": 30,
            "success_count": 30,
            "attempts_used": 60,
            "measurement_basis": {},
            "observation_adequacy": {
                "status": "LIMITED",
                "planned_slots": 150,
                "confirmed_slots": 60,
                "ambiguous_slots": 50,
                "answer_failed_slots": 20,
                "judgment_failed_slots": 20,
                "pending_slots": 0,
            },
        },
    )
    assert "150" in view["coverage_text"] and "60" in view["coverage_text"]
    assert "부분" in view["coverage_text"]


def test_customer_summary_does_not_use_parenthesized_korean_particles():
    assert "(를)" not in _view()["summary"]


@pytest.mark.parametrize("source,expected", [
    ("~~효과를 보장합니다~~", "[취소선 표시: 효과를 보장합니다]"),
    ("⚠️ 확인이 필요합니다", "[주의 표시] 확인이 필요합니다"),
    ("❌ 단정하지 않습니다", "[X 표시] 단정하지 않습니다"),
])
def test_evidence_format_cleanup_preserves_meaningful_marks(source, expected):
    from app.services.report_engine import _excerpt_around
    assert _excerpt_around(source, "") == expected
