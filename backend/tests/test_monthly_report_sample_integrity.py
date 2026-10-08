"""Canonical repeat and ownership boundaries of customer-facing measurements."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_monthly_sov_repository import _cell_with_attempts

from app.services.monthly_report_delivery import coverage_is_final, monthly_report_delivery_blockers
from app.services.monthly_sov import build_monthly_sov, build_monthly_sov_horizon_summary
from app.services.monthly_sov_repository import MonthlySovDataError, load_monthly_sov_manifest
from app.workers.tasks import _freeze_platform_availability


def fixture(repeats=5):
    cell = _cell_with_attempts()
    cell.id = uuid4()
    hospital_id = uuid4()
    manifest = SimpleNamespace(
        id=uuid4(),
        hospital_id=hospital_id,
        platform_provenance={
            "query_intents": {cell.query_key: "LOCAL"},
            "observation_slots": {"repeat_count": repeats},
        },
    )
    for attempt in cell.attempts:
        record = attempt.sov_record
        record.hospital_id, record.query_id, record.ai_platform = (
            hospital_id,
            cell.query_matrix_id,
            cell.platform,
        )
    record = cell.attempts[1].sov_record
    cell.observation_slots = [
        SimpleNamespace(
            repeat_no=1,
            answer_status="RECEIVED",
            judgment_status="CONFIRMED",
            sov_record_id=record.id,
            hospital_id=hospital_id,
            query_id=cell.query_matrix_id,
            platform=cell.platform,
            monthly_cell_id=cell.id,
            scope="MONTHLY",
        )
    ]
    return cell, manifest


def load(cell, manifest, extra=()):
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: [(cell, "LOCAL"), *extra])
    )
    return load_monthly_sov_manifest(session, manifest)


def test_uncreated_repeats_remain_in_the_frozen_denominator():
    cell, manifest = fixture()
    loaded = load(cell, manifest)
    summary = build_monthly_sov(loaded.cells, ("chatgpt",))
    assert summary.observation_adequacy["planned_slots"] == 5
    assert summary.observation_adequacy["confirmed_slots"] == 1
    assert summary.observation_adequacy["pending_slots"] == 4
    assert summary.observation_adequacy["status"] == "LIMITED"
    assert summary.sov_pct == 0.0  # One confirmed negative, not one plus a stale positive.


def test_zero_started_repeats_never_downgrade_to_legacy_successes():
    cell, manifest = fixture()
    cell.observation_slots = []
    loaded = load(cell, manifest)
    summary = build_monthly_sov(loaded.cells, ("chatgpt",))
    assert not loaded.scored_records and summary.sov_pct is None
    assert summary.observation_adequacy["planned_slots"] == 5
    assert summary.observation_adequacy["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("repeat", [True, "1", 0, -1, 6])
def test_repeat_identity_must_be_inside_the_frozen_plan(repeat):
    cell, manifest = fixture()
    cell.observation_slots[0].repeat_no = repeat
    with pytest.raises(MonthlySovDataError):
        load(cell, manifest)


@pytest.mark.parametrize(
    "field", ["hospital_id", "query_id", "platform", "monthly_cell_id", "scope"]
)
def test_slot_ownership_never_crosses_hospital_question_or_scope(field):
    cell, manifest = fixture()
    setattr(
        cell.observation_slots[0], field, "wrong" if field in {"platform", "scope"} else uuid4()
    )
    with pytest.raises(MonthlySovDataError):
        load(cell, manifest)


def test_duplicate_repeat_is_not_an_extra_observation():
    cell, manifest = fixture()
    cell.observation_slots.append(cell.observation_slots[0])
    with pytest.raises(MonthlySovDataError):
        load(cell, manifest)


def test_legacy_duplicate_links_are_counted_once_without_rewriting_orm():
    cell, manifest = fixture()
    manifest.platform_provenance.pop("observation_slots")
    cell.observation_slots = []
    cell.attempts.append(cell.attempts[1])
    assert len(load(cell, manifest).scored_records) == 2
    assert len(cell.attempts) == 4


def test_same_record_cannot_count_in_two_cells():
    cell, manifest = fixture()
    with pytest.raises(MonthlySovDataError):
        load(cell, manifest, extra=((cell, "LOCAL"),))


@pytest.mark.parametrize("value", ["3", True, -1, None, [], {}])
def test_malformed_limited_counts_return_not_ready_instead_of_crashing(value):
    report = SimpleNamespace(
        quality="DEGRADED",
        planned_count=2,
        success_count=1,
        failed_count=1,
        sov_summary={"observation_adequacy": {"status": "LIMITED", "confirmed_slots": value}},
    )
    assert coverage_is_final(report) is False


def test_confirmed_observations_cannot_exceed_the_plan():
    report = SimpleNamespace(
        quality="DEGRADED",
        planned_count=2,
        success_count=1,
        failed_count=1,
        sov_summary={
            "observation_adequacy": {"status": "LIMITED", "planned_slots": 5, "confirmed_slots": 6}
        },
    )
    assert coverage_is_final(report) is False


def test_horizon_summary_keeps_each_platform_failure_class_out_of_the_denominator():
    rows = (
        SimpleNamespace(platform="chatgpt", answer_status="RECEIVED", judgment_status="CONFIRMED"),
        SimpleNamespace(platform="chatgpt", answer_status="RECEIVED", judgment_status="AMBIGUOUS"),
        SimpleNamespace(platform="gemini", answer_status="FAILED", judgment_status="PENDING"),
        SimpleNamespace(platform="gemini", answer_status="RECEIVED", judgment_status="FAILED"),
        SimpleNamespace(platform="gemini", answer_status="PENDING", judgment_status="PENDING"),
    )

    summary = build_monthly_sov_horizon_summary(rows, ("chatgpt", "gemini"))

    assert summary["status"] == "LIMITED"
    assert summary["confirmed_slots"] == 1
    assert summary["ambiguous_slots"] == 1
    assert summary["answer_failed_slots"] == 1
    assert summary["judgment_failed_slots"] == 1
    assert summary["pending_slots"] == 3
    assert summary["platforms"] == [
        {
            "platform": "chatgpt",
            "planned_slots": 2,
            "received_answers": 2,
            "confirmed_slots": 1,
            "ambiguous_slots": 1,
            "answer_failed_slots": 0,
            "judgment_failed_slots": 0,
            "pending_slots": 0,
        },
        {
            "platform": "gemini",
            "planned_slots": 3,
            "received_answers": 1,
            "confirmed_slots": 0,
            "ambiguous_slots": 0,
            "answer_failed_slots": 1,
            "judgment_failed_slots": 1,
            "pending_slots": 3,
        },
    ]
    report = SimpleNamespace(
        pdf_path="gs://reputation-reports/builder-roundtrip.pdf",
        sov_summary={
            "sov_pct": None,
            "change_pct": None,
            "comparison": {"status": "NON_COMPARABLE", "change_pct": None},
            "observation_adequacy": summary,
        },
        content_summary={"published_count": 0, "operations": {}},
        essence_summary={},
    )
    assert monthly_report_delivery_blockers(report) == []


def test_horizon_summary_with_zero_questions_is_truthfully_unavailable():
    summary = build_monthly_sov_horizon_summary((), ("chatgpt", "gemini"))

    assert summary["status"] == "UNAVAILABLE"
    assert summary["planned_slots"] == 0
    assert summary["confirmed_slots"] == 0

    report = SimpleNamespace(
        quality="DEGRADED",
        planned_count=0,
        success_count=0,
        failed_count=0,
        sov_summary={"observation_adequacy": summary},
    )
    assert coverage_is_final(report) is True


def test_render_payload_freezes_each_platform_k_n_and_outage_counts():
    def cell(platform, *, planned, received, confirmed, ambiguous, answer_failed,
             judgment_failed, pending):
        return SimpleNamespace(
            platform=platform,
            planned_repeat_count=planned,
            received_answer_count=received,
            confirmed_slot_count=confirmed,
            ambiguous_slot_count=ambiguous,
            answer_failed_slot_count=answer_failed,
            judgment_failed_slot_count=judgment_failed,
            pending_slot_count=pending,
        )

    payload = {
        "observation_adequacy": {"status": "LIMITED"},
        "platforms": [
            {"platform": "chatgpt", "mentioned_attempts": 2, "attempts_used": 3},
            {"platform": "gemini", "mentioned_attempts": 0, "attempts_used": 0},
        ]
    }
    frozen = _freeze_platform_availability(
        payload,
        (
            cell("chatgpt", planned=5, received=4, confirmed=3, ambiguous=1,
                 answer_failed=1, judgment_failed=0, pending=1),
            cell("gemini", planned=5, received=1, confirmed=0, ambiguous=0,
                 answer_failed=2, judgment_failed=1, pending=5),
        ),
    )

    assert frozen["platforms"][0] == {
        "platform": "chatgpt",
        "mentioned_attempts": 2,
        "attempts_used": 3,
        "confirmed_mentioned_count": 2,
        "confirmed_sample_count": 3,
        "availability": {
            "planned_slots": 5,
            "received_answers": 4,
            "confirmed_slots": 3,
            "ambiguous_slots": 1,
            "answer_failed_slots": 1,
            "judgment_failed_slots": 0,
            "pending_slots": 1,
            "pending_semantics": "INCLUDES_FAILURES",
        },
    }
    assert frozen["platforms"][1]["confirmed_sample_count"] == 0
    assert frozen["platforms"][1]["availability"] == {
        "planned_slots": 5,
        "received_answers": 1,
        "confirmed_slots": 0,
        "ambiguous_slots": 0,
        "answer_failed_slots": 2,
        "judgment_failed_slots": 1,
        "pending_slots": 5,
        "pending_semantics": "INCLUDES_FAILURES",
    }
    assert frozen["observation_adequacy"]["platforms"][1] == {
        "platform": "gemini",
        "confirmed_mentioned_count": 0,
        "confirmed_sample_count": 0,
        "planned_slots": 5,
        "received_answers": 1,
        "confirmed_slots": 0,
        "ambiguous_slots": 0,
        "answer_failed_slots": 2,
        "judgment_failed_slots": 1,
        "pending_slots": 5,
        "pending_semantics": "INCLUDES_FAILURES",
    }
    assert frozen["observation_adequacy"]["pending_semantics"] == "INCLUDES_FAILURES"
    assert payload["platforms"][0].get("availability") is None
