from types import SimpleNamespace

from app.services.measurement_slots import (
    slot_is_terminal,
    slot_needs_answer,
    slot_needs_judgment,
    summarize_observation_slots,
)


def _slot(*, answer="RECEIVED", judgment="CONFIRMED", answer_attempts=1, judgment_attempts=1):
    return SimpleNamespace(
        answer_status=answer,
        judgment_status=judgment,
        answer_attempt_count=answer_attempts,
        judgment_attempt_count=judgment_attempts,
    )


def test_complete_requires_every_frozen_repeat_to_be_confirmed():
    slots = [_slot() for _ in range(5)]

    summary = summarize_observation_slots(slots, deadline_reached=True)

    assert summary.status == "COMPLETE"
    assert summary.confirmed_slots == summary.planned_slots == 5


def test_partial_sample_stays_resumable_then_finishes_as_limited():
    slots = [_slot() for _ in range(4)] + [
        _slot(answer="FAILED", judgment="PENDING", answer_attempts=3, judgment_attempts=0)
    ]

    assert summarize_observation_slots(slots, deadline_reached=False).status == "IN_PROGRESS"
    final = summarize_observation_slots(slots, deadline_reached=True)
    assert final.status == "LIMITED"
    assert final.confirmed_slots == 4
    assert final.answer_failed_slots == 1


def test_all_ambiguous_is_truthful_unavailable_without_resampling():
    slots = [_slot(judgment="AMBIGUOUS") for _ in range(5)]

    final = summarize_observation_slots(slots, deadline_reached=True)

    assert final.status == "UNAVAILABLE"
    assert final.confirmed_slots == 0
    assert final.ambiguous_slots == 5
    assert all(slot_is_terminal(slot) for slot in slots)
    assert all(not slot_needs_answer(slot) for slot in slots)
    assert all(not slot_needs_judgment(slot) for slot in slots)
