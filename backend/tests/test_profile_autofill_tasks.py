"""계약 등록 직후 자동 입력 초안 작업."""

import uuid
from types import SimpleNamespace

from app.services.hospital_profile_autofill import AutofillResult, SourceStatus
from app.workers import profile_autofill_tasks as module
from app.workers.dispatch_envelope import expected_purpose, expected_target


class _FakeSession:
    def __init__(self, hospital, existing=None):
        self.hospital = hospital
        self.existing = existing
        self.added: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _model, _id):
        return self.hospital

    async def scalar(self, _stmt):
        return self.existing

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None


def _patch(monkeypatch, session, *, allowed=True, result=None):
    calls: list = []

    async def fake_autofill(name, *, website_url=None, blog_url=None, hospital_id=None):
        calls.append((name, website_url, blog_url, hospital_id))
        return result or AutofillResult(
            draft={"address": "서울시 강남구"},
            field_meta={"address": {"source": "naver", "confidence": 0.9}},
            sources=[SourceStatus("네이버 플레이스", ok=True)],
        )

    async def fake_guard(category):
        assert category == "content"
        return SimpleNamespace(allowed=allowed, reason=None if allowed else "limit")

    monkeypatch.setattr(module, "get_async_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(module, "autofill_profile", fake_autofill)
    monkeypatch.setattr(module.cost_guard, "check_and_increment", fake_guard)
    return calls


async def test_stores_one_draft_without_touching_the_hospital(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="자동입력의원", website_url=None,
                               blog_url=None, address=None)
    session = _FakeSession(hospital)
    calls = _patch(monkeypatch, session)

    outcome = await module._autofill_once(hospital.id)

    assert outcome["status"] == "DRAFT_STORED"
    assert calls == [("자동입력의원", None, None, hospital.id)]
    run = next(r for r in session.added if getattr(r, "operation_type", None) == "PROFILE_AUTOFILL")
    assert run.idempotency_key == "registration" and run.state == "SUCCEEDED"
    assert run.result_summary["draft"] == {"address": "서울시 강남구"}
    audit = next(r for r in session.added if getattr(r, "action", None) == "autofill_profile")
    assert audit.actor == "system:profile-autofill"
    assert hospital.address is None  # 병원 행에는 쓰지 않는다
    assert session.committed is True


async def test_existing_draft_is_not_bought_again(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url=None, blog_url=None)
    session = _FakeSession(hospital, existing=SimpleNamespace(id=uuid.uuid4()))
    calls = _patch(monkeypatch, session)

    assert (await module._autofill_once(hospital.id))["status"] == "ALREADY_DONE"
    assert calls == []


async def test_cost_guard_blocks_before_the_provider(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url=None, blog_url=None)
    session = _FakeSession(hospital)
    calls = _patch(monkeypatch, session, allowed=False)

    assert (await module._autofill_once(hospital.id))["status"] == "COST_BLOCKED"
    assert calls == [] and session.added == []


def test_dispatch_is_purpose_and_hospital_bound() -> None:
    name = "app.workers.profile_autofill_tasks.autofill_registered_hospital_profile"
    hospital_id = str(uuid.uuid4())
    assert expected_purpose(name) == "autofill-registered-profile"
    assert expected_target(name, [hospital_id]) == hospital_id


class _DraftDB:
    def __init__(self, hospital, run=None, lead=None):
        self.hospital, self.run, self.lead = hospital, run, lead

    async def get(self, model, _id):
        from app.models.lead import SalesLead

        return self.lead if model is SalesLead else self.hospital

    async def scalar(self, _stmt):
        return self.run


async def test_stored_draft_is_served_with_the_lead_phone_only_as_a_suggestion() -> None:
    from datetime import UTC, datetime

    from app.api.admin import hospitals as hospitals_api

    hospital = SimpleNamespace(id=uuid.uuid4(), source_lead_id=uuid.uuid4())
    run = SimpleNamespace(
        result_summary={"draft": {"address": "서울시 강남구"}, "sources": []},
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    lead = SimpleNamespace(clinic_phone=" 02-123-4567 ", purged_at=None)

    body = await hospitals_api.get_profile_autofill_draft(
        hospital.id, db=_DraftDB(hospital, run, lead)
    )

    assert body["has_draft"] is True
    assert body["draft"] == {"address": "서울시 강남구"}
    assert body["lead_suggestions"] == {"phone": "02-123-4567"}
    assert "phone" not in body["draft"]


async def test_lead_suggestion_without_a_stored_draft_says_so() -> None:
    from app.api.admin import hospitals as hospitals_api

    hospital = SimpleNamespace(id=uuid.uuid4(), source_lead_id=uuid.uuid4())
    lead = SimpleNamespace(clinic_phone="02-1", purged_at=None)

    body = await hospitals_api.get_profile_autofill_draft(
        hospital.id, db=_DraftDB(hospital, None, lead)
    )

    assert body["has_draft"] is False
    assert body["draft"] == {} and body["lead_suggestions"] == {"phone": "02-1"}


def test_draft_endpoint_reads_the_worker_operation_type() -> None:
    import inspect

    from app.api.admin import hospitals as hospitals_api

    assert hospitals_api.PROFILE_AUTOFILL_OPERATION == module.PROFILE_AUTOFILL_OPERATION
    assert '"PROFILE_AUTOFILL"' not in inspect.getsource(hospitals_api.get_profile_autofill_draft)


async def test_purged_lead_and_no_draft_answer_not_found() -> None:
    import pytest
    from fastapi import HTTPException

    from app.api.admin import hospitals as hospitals_api

    hospital = SimpleNamespace(id=uuid.uuid4(), source_lead_id=uuid.uuid4())
    purged = SimpleNamespace(clinic_phone="02-1", purged_at=object())

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.get_profile_autofill_draft(
            hospital.id, db=_DraftDB(hospital, None, purged)
        )
    assert exc.value.status_code == 404
