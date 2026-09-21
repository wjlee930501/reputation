"""Offline regression coverage for the published image recovery worker."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.workers import published_image_refresh as refresh
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import next_recovery_sweep, retry_is_due
from app.workers.nightly_generation_batch import write_back_published_image


def test_daytime_recovery_is_the_next_retry():
    assert next_recovery_sweep(datetime(2026, 9, 21, 22, 5, tzinfo=UTC)) == datetime(
        2026, 9, 22, 3, tzinfo=UTC
    )


def test_fourth_image_failure_preserves_and_exhausts_third_day():
    now = datetime.now(UTC)
    item = SimpleNamespace(
        essence_check_summary={
            GENERATION_ATTEMPT_KEY: {
                "reason": "IMAGE_GENERATION_FAILED",
                "provider_attempt_count": 3,
                "attempt_period": refresh.environment_attempt_period(now),
                "exhausted_days": 2,
                "context": "unchanged",
            }
        }
    )
    attempt = refresh.remember_image_attempt(MagicMock(), item, "IMAGE_GENERATION_FAILED")
    assert attempt["exhausted_days"] == 3
    assert attempt["retry_class"] == "OPERATOR_REQUIRED"
    assert not retry_is_due(attempt, now + timedelta(days=1))


def test_refresh_refills_after_fifty_ineligible_candidates():
    blocked = [
        SimpleNamespace(
            id=uuid.uuid4(),
            published_at=None,
            sequence_no=i,
            essence_check_summary={GENERATION_ATTEMPT_KEY: {"retry_class": "OPERATOR_REQUIRED"}},
        )
        for i in range(50)
    ]
    eligible = SimpleNamespace(
        id=uuid.uuid4(), published_at=None, sequence_no=51, essence_check_summary={}
    )
    db = MagicMock()
    db.execute.return_value.unique.return_value.scalars.return_value.all.side_effect = [
        blocked,
        [eligible],
    ]
    assert list(refresh._image_candidates(db)) == [*blocked, eligible]


def test_published_writeback_requires_execution_token():
    db = MagicMock()
    token = uuid.uuid4()
    write_back_published_image(
        db,
        item_id=uuid.uuid4(),
        expected_title="title",
        expected_revision=3,
        expected_claim_token=token,
        values={"image_url": "fixture"},
    )
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "content_items.generation_claim_token =" in sql
    assert "content_items.generation_claimed_at >" in sql
    assert token in statement.compile().params.values()


def test_claim_loser_cannot_purchase_and_claim_query_is_locked():
    db = MagicMock()
    item = SimpleNamespace(essence_check_summary={})
    db.execute.return_value.unique.return_value.scalar_one_or_none.side_effect = [item, None]
    token = refresh._claim_image_refresh(db, uuid.uuid4())
    assert token == item.generation_claim_token
    assert refresh._claim_image_refresh(db, uuid.uuid4()) is None
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF content_items SKIP LOCKED" in sql
    assert "generation_claimed_at <=" in sql
    assert db.commit.call_count == 1


def test_image_commit_contains_durable_intent_even_if_worker_dies(monkeypatch):
    import pytest

    from app.models.content import ContentType
    from app.models.hospital import Hospital
    from app.services import image_direction, image_engine
    from app.workers import tasks

    hospital = Hospital(id=uuid.uuid4(), slug="fixture", name="fixture")
    item = SimpleNamespace(
        id=uuid.uuid4(),
        title="fixture",
        content_revision=3,
        content_type=ContentType.DISEASE,
        hospital=hospital,
        essence_check_summary={},
    )
    db = MagicMock()
    db.__enter__.return_value = db
    monkeypatch.setattr(refresh, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(refresh, "require_dispatch", lambda *_: None)
    monkeypatch.setattr(refresh, "_image_candidates", lambda _: iter([item]))
    monkeypatch.setattr(refresh, "_claim_image_refresh", lambda *_: uuid.uuid4())
    monkeypatch.setattr(refresh, "write_back_published_image", lambda *_, **__: 1)
    monkeypatch.setattr(
        image_engine,
        "generate_image",
        lambda *_, **__: ("https://fixture/" + "a" * 64 + ".png", "prompt"),
    )
    monkeypatch.setattr(image_direction, "hospital_image_direction", lambda _: None)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    committed = []

    def crash_after_commit():
        committed.extend(call.args[0] for call in db.add.call_args_list)
        raise RuntimeError("simulated worker exit after commit")

    db.commit.side_effect = crash_after_commit
    with pytest.raises(RuntimeError, match="simulated worker exit"):
        refresh.refresh_reused_content_images.run()
    assert len(committed) == 1
    assert committed[0].operation_type == "SITE_REVALIDATION"
    assert committed[0].request_payload["content_ids"] == [str(item.id)]


def test_cost_deferral_preserves_image_budget_without_spending():
    item = SimpleNamespace(
        essence_check_summary={
            GENERATION_ATTEMPT_KEY: {
                "provider_attempt_count": 3,
                "exhausted_days": 2,
                "attempt_period": refresh.environment_attempt_period(),
                "context": "same",
            }
        }
    )
    attempt = refresh.remember_image_attempt(MagicMock(), item, "COST_BLOCKED")
    assert attempt["provider_attempt_count"] == 3
    assert attempt["exhausted_days"] == 2
    assert attempt["context"] == "same"


def test_retry_hours_match_registered_generation_sweeps():
    from app.core.celery_app import celery_app
    from app.workers.generation_retry_policy import RECOVERY_SWEEP_HOURS

    hours = set()
    for key in (
        "nightly-content-generation",
        "overnight-content-generation-recovery",
        "daytime-content-generation-recovery",
    ):
        hours.update(celery_app.conf.beat_schedule[key]["schedule"].hour)
    assert set(RECOVERY_SWEEP_HOURS) == hours
