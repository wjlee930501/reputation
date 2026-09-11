import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from app.models.director_delta import DirectorDelta
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.services.content_engine import _build_philosophy_context
from app.services.content_target_planner import prepare_automatic_content_brief_sync
from app.services.director_delta import (
    ActiveDirectorDeltaLimit,
    DirectorDeltaInput,
    active_deltas_query,
    create_director_delta,
    effective_philosophy_sync,
    merge_director_deltas,
    retire_director_delta,
)
from app.services.essence_engine import screen_content_against_philosophy


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (Hospital, HospitalContentPhilosophy, DirectorDelta):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(db):
    hospital = Hospital(name="Delta test", slug=str(uuid.uuid4()))
    db.add(hospital)
    db.flush()
    base = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status="APPROVED",
        is_base=True,
        source_snapshot_hash="old-hash",
        avoid_messages=["base avoidance"],
        must_use_messages=["base message"],
        medical_ad_risk_rules=["base medical rule"],
    )
    db.add(base)
    db.flush()
    return hospital, base


def test_union_soft_preferences_retirement_and_no_base_rewrite(db):
    hospital, base = seed(db)
    first = create_director_delta(
        db,
        hospital.id,
        DirectorDeltaInput(
            source="DIRECTOR",
            avoid_messages=["base avoidance", "delta forbidden"],
            prefer_topics=["prevention", "delta forbidden"],
            prefer_messages=["soft message"],
        ),
    )
    retired = create_director_delta(
        db, hospital.id, DirectorDeltaInput(source="ADMIN", avoid_messages=["retired only"])
    )
    retire_director_delta(db, hospital.id, retired.id)
    effective = effective_philosophy_sync(db, base)
    assert effective.avoid_messages == ["base avoidance", "delta forbidden"]
    assert effective.prefer_topics == ["prevention"]
    assert effective.prefer_messages == ["soft message"]
    assert effective.must_use_messages == ["base message"]
    assert effective.medical_ad_risk_rules == ["base medical rule"]
    prompt = _build_philosophy_context(effective)
    assert "soft message" in prompt and "soft guidance only" in prompt
    item = SimpleNamespace(
        title="delta forbidden", body="", content_type="FAQ", hospital_id=hospital.id
    )
    screening = screen_content_against_philosophy(item, effective)
    assert any("delta forbidden" in finding for finding in screening.summary["findings"])
    item.title = "ordinary content"
    assert screen_content_against_philosophy(item, effective).summary["blocking"] is False
    effective.medical_ad_risk_rules.append("local mutation")
    db.flush()
    db.expire(base)
    assert base.avoid_messages == ["base avoidance"]
    assert base.medical_ad_risk_rules == ["base medical rule"]
    assert base.source_snapshot_hash == "old-hash" and base.is_base
    assert db.scalar(select(func.count()).select_from(HospitalContentPhilosophy)) == 1
    first.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert "delta forbidden" in effective_philosophy_sync(db, base).avoid_messages


def test_cap_rejects_and_manual_retirement_frees_slot(db):
    hospital, base = seed(db)
    rows = [
        create_director_delta(
            db, hospital.id, DirectorDeltaInput(source="ADMIN", avoid_messages=[str(i)])
        )
        for i in range(50)
    ]
    with pytest.raises(ActiveDirectorDeltaLimit, match="50 ACTIVE"):
        create_director_delta(db, hospital.id, DirectorDeltaInput(source="DIRECTOR"))
    assert len(db.scalars(active_deltas_query(hospital.id)).all()) == 50
    assert len(effective_philosophy_sync(db, base).avoid_messages) == 51
    retire_director_delta(db, hospital.id, rows[0].id)
    create_director_delta(db, hospital.id, DirectorDeltaInput(source="DIRECTOR"))
    assert len(db.scalars(active_deltas_query(hospital.id)).all()) == 50
    other, _ = seed(db)
    create_director_delta(db, other.id, DirectorDeltaInput(source="ADMIN"))
    with pytest.raises(NoResultFound):
        retire_director_delta(db, other.id, rows[1].id)
    assert rows[1].status == "ACTIVE"


@pytest.mark.parametrize(
    "field", ["medical_ad_risk_rules", "must_use_messages", "is_base", "status", "soft_weights"]
)
def test_write_allowlist_rejects_policy_and_base_overrides(field):
    with pytest.raises(ValidationError):
        DirectorDeltaInput.model_validate({"source": "ADMIN", field: []})


def test_ordering_and_cross_hospital_isolation(db):
    hospital, base = seed(db)
    later = create_director_delta(
        db, hospital.id, DirectorDeltaInput(source="ADMIN", prefer_topics=["later"])
    )
    earlier = create_director_delta(
        db, hospital.id, DirectorDeltaInput(source="DIRECTOR", prefer_topics=["earlier"])
    )
    earlier.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    db.flush()
    assert effective_philosophy_sync(db, base).prefer_topics == ["earlier", "later"]
    later.hospital_id = uuid.uuid4()
    assert merge_director_deltas(base, [later]) is base


@pytest.mark.asyncio
async def test_drifted_generation_and_publish_loaders(db, monkeypatch):
    from app.services import essence_readiness
    from app.workers import tasks

    hospital, base = seed(db)
    create_director_delta(
        db, hospital.id, DirectorDeltaInput(source="ADMIN", avoid_messages=["delta forbidden"])
    )
    readiness = essence_readiness.resolve_essence_readiness(base, [])
    assert readiness.current is base and readiness.is_stale
    monkeypatch.setattr(
        essence_readiness, "get_essence_readiness", AsyncMock(return_value=readiness)
    )
    monkeypatch.setattr(essence_readiness, "get_essence_readiness_sync", lambda *_: readiness)
    monkeypatch.setattr(tasks, "get_essence_readiness_sync", lambda *_: readiness)
    async_db = SimpleNamespace(execute=AsyncMock(side_effect=lambda q: db.execute(q)))
    for effective in (
        await essence_readiness.get_current_approved_philosophy(async_db, hospital.id),
        essence_readiness.get_current_approved_philosophy_sync(db, hospital.id),
        tasks._generation_philosophy_sync(db, hospital.id),
    ):
        assert "delta forbidden" in effective.avoid_messages
        assert effective.id == base.id


def test_delta_activation_and_retirement_invalidate_failed_attempt_context(db):
    from app.workers.tasks import _generation_attempt_context

    hospital, base = seed(db)
    item = SimpleNamespace()
    before = _generation_attempt_context(item, base)
    delta = create_director_delta(db, hospital.id, DirectorDeltaInput(source="ADMIN"))
    during = _generation_attempt_context(item, effective_philosophy_sync(db, base))
    assert before != during
    retire_director_delta(db, hospital.id, delta.id)
    assert _generation_attempt_context(item, effective_philosophy_sync(db, base)) == before


def _stub_content_item():
    return SimpleNamespace(
        id=uuid.uuid4(),
        content_type="FAQ",
        title="Delta brief",
        scheduled_date=None,
        content_brief=None,
        brief_status=None,
    )


def test_retired_delta_invalidates_approved_stub_brief(db):
    hospital, base = seed(db)
    delta = create_director_delta(
        db,
        hospital.id,
        DirectorDeltaInput(source="DIRECTOR", avoid_messages=["retired avoidance"]),
    )
    item = _stub_content_item()
    original = prepare_automatic_content_brief_sync(
        db,
        item=item,
        hospital=hospital,
        philosophy=effective_philosophy_sync(db, base),
    )
    assert "retired avoidance" in original["avoid_messages"]
    assert original["philosophy_reference"]["director_delta_ids"] == [str(delta.id)]

    retire_director_delta(db, hospital.id, delta.id)
    rebuilt = prepare_automatic_content_brief_sync(
        db,
        item=item,
        hospital=hospital,
        philosophy=effective_philosophy_sync(db, base),
    )

    assert rebuilt is item.content_brief
    assert rebuilt is not original
    assert "retired avoidance" not in rebuilt["avoid_messages"]
    assert rebuilt["philosophy_reference"]["director_delta_ids"] == []


def test_new_active_delta_invalidates_approved_stub_brief(db):
    hospital, base = seed(db)
    item = _stub_content_item()
    original = prepare_automatic_content_brief_sync(
        db,
        item=item,
        hospital=hospital,
        philosophy=effective_philosophy_sync(db, base),
    )
    assert original["philosophy_reference"]["director_delta_ids"] == []

    delta = create_director_delta(
        db,
        hospital.id,
        DirectorDeltaInput(source="ADMIN", avoid_messages=["new avoidance"]),
    )
    rebuilt = prepare_automatic_content_brief_sync(
        db,
        item=item,
        hospital=hospital,
        philosophy=effective_philosophy_sync(db, base),
    )

    assert rebuilt is item.content_brief
    assert rebuilt is not original
    assert "new avoidance" in rebuilt["avoid_messages"]
    assert rebuilt["philosophy_reference"]["director_delta_ids"] == [str(delta.id)]


def test_retired_and_unrecognized_delta_policy_fields_cannot_weaken_safety(db):
    from app.services.essence_engine import effective_safety_policy

    hospital, base = seed(db)
    delta = create_director_delta(db, hospital.id, DirectorDeltaInput(source="DIRECTOR"))
    delta.medical_ad_risk_rules = []  # Even an untrusted in-memory extra is ignored.
    delta.must_use_messages = ["override"]
    effective = merge_director_deltas(base, [delta])
    assert effective_safety_policy(effective) == effective_safety_policy(base)
    assert effective.must_use_messages == base.must_use_messages
    retire_director_delta(db, hospital.id, delta.id)
    assert merge_director_deltas(base, [delta]) is base
    assert merge_director_deltas(None, [delta]) is None
