"""Canonical repeat and ownership boundaries of customer-facing measurements."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_monthly_sov_repository import _cell_with_attempts

from app.services.monthly_report_delivery import coverage_is_final
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import MonthlySovDataError, load_monthly_sov_manifest


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
