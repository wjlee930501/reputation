import uuid
from datetime import datetime, timezone

import pytest

from app.workers import tasks
from app.workers.dispatch_envelope import expected_purpose, expected_target


def test_essence_review_dispatch_is_purpose_and_hospital_bound() -> None:
    hospital_id = str(uuid.uuid4())

    assert (
        expected_purpose("app.workers.tasks.auto_review_essence_snapshot")
        == "auto-review-essence-snapshot"
    )
    assert (
        expected_target("app.workers.tasks.auto_review_essence_snapshot", [hospital_id])
        == hospital_id
    )
    assert (
        expected_purpose("app.workers.tasks.reconcile_essence_snapshots")
        == "reconcile-essence-snapshots"
    )


def test_reconcile_offset_rotates_across_every_page() -> None:
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(0, timezone.utc)) == 0
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(900, timezone.utc)) == 200
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(1800, timezone.utc)) == 400
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(2700, timezone.utc)) == 0


def test_essence_reviewer_cost_guard_blocks_before_provider(monkeypatch) -> None:
    async def blocked(*_args, **_kwargs):
        return type("Decision", (), {"allowed": False, "reason": "limit"})()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("review provider must not run")

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", blocked)
    monkeypatch.setattr(tasks, "review_essence_candidate", unexpected)

    with pytest.raises(tasks._EssenceReviewCostBlocked, match="limit"):
        tasks._cost_guarded_essence_review(object(), object(), {}, [])
