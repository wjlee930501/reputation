"""Admin 경로에서 유료 LLM 호출이 cost_guard 예약(check_and_increment)을 거치는지 검증.

배경: essence.py의 자료 처리/철학 합성, hospital_profile_autofill.py의 구조화 추출은
`metered_llm_calls`/`record_provider_call`로 "실제 호출 수"만 관측했을 뿐, 사전에
`cost_guard.check_and_increment`로 예산을 예약해 킬스위치·상한을 존중하지 않았다.
이 테스트는 그 예약 호출이 실제로 일어나고, 차단 시 HTTP 429로 응답하는지 확인한다.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import essence
from app.api.admin import hospitals as hospitals_api
from app.models.essence import SourceStatus
from app.services.cost_guard import CostGuardDecision


class _FakeDB:
    """cost_guard 차단 경로에서는 DB 쿼리가 전혀 일어나지 않아야 한다."""

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("cost_guard가 막았는데 DB 쿼리를 시도했다")

    async def get(self, *_args, **_kwargs):
        raise AssertionError("cost_guard가 막았는데 DB 조회를 시도했다")

    async def commit(self):
        raise AssertionError("cost_guard가 막았는데 커밋을 시도했다")


def _blocked_decision(reason: str = "비용 가드 킬스위치가 활성화되어 모든 자동 호출이 차단됐습니다."):
    async def _decision(*_args, **_kwargs):
        return CostGuardDecision(False, reason)

    return _decision


def _allowed_decision():
    async def _decision(*_args, **_kwargs):
        return CostGuardDecision(True, None)

    return _decision


# ── essence.py: POST /sources/{id}/process ──────────────────────────────


@pytest.fixture
def _stub_source_lookup(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), slug="h", status=None, site_live=False)
    source = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        status=SourceStatus.PENDING,
        raw_text="자료 본문 텍스트",
        title="제목",
        url=None,
        operator_note=None,
        content_hash="old-hash",
        source_type="TEXT",
        source_metadata=None,
        process_error=None,
        processed_at=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
        file_url=None,
        mime_type=None,
        file_size_bytes=None,
        is_public=False,
    )

    async def _hospital(_db, _hospital_id):
        return hospital

    async def _source(_db, _hospital_id, _source_id):
        return source

    async def _lock(_db, _hospital_id):
        return None

    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "_get_source_or_404", _source)
    monkeypatch.setattr(essence, "acquire_hospital_advisory_lock", _lock)
    return hospital, source


async def test_process_source_api_only_creates_durable_work(monkeypatch, _stub_source_lookup):
    """The API must not reserve or call a provider before the durable worker claim."""
    hospital, source = _stub_source_lookup
    queued: list[uuid.UUID] = []

    async def _should_not_reserve(*_args, **_kwargs):
        raise AssertionError("API layer must not duplicate the worker cost reservation")

    async def _start(_db, *, hospital_id, source_ids, **_kwargs):
        assert hospital_id == hospital.id
        queued.extend(source_ids)
        return SimpleNamespace(id=uuid.uuid4())

    async def _notes(_db, _source_id):
        return []

    class _DB(_FakeDB):
        async def refresh(self, _obj):
            return None

    monkeypatch.setattr(essence.cost_guard, "check_and_increment", _should_not_reserve)
    monkeypatch.setattr(essence, "_start_source_processing_best_effort", _start)
    monkeypatch.setattr(essence, "_get_notes_for_source", _notes)

    response = await essence.process_source(hospital.id, source.id, db=_DB())

    assert response["id"] == str(source.id)
    assert queued == [source.id]


async def test_process_source_does_not_extract_inside_request(monkeypatch, _stub_source_lookup):
    """The endpoint returns after enqueueing; extraction belongs to the worker."""
    hospital, source = _stub_source_lookup
    calls = {"queued": 0}

    async def _start(*_args, **_kwargs):
        calls["queued"] += 1
        return SimpleNamespace(id=uuid.uuid4())

    async def _notes(_db, _source_id):
        return []

    monkeypatch.setattr(essence, "_start_source_processing_best_effort", _start)
    monkeypatch.setattr(essence, "_get_notes_for_source", _notes)

    class _DB(_FakeDB):
        async def refresh(self, _obj):
            return None

    await essence.process_source(hospital.id, source.id, db=_DB())

    assert calls["queued"] == 1


async def test_process_source_skips_reextraction_when_already_processed_unchanged(
    monkeypatch, _stub_source_lookup
):
    """이미 같은 내용으로 PROCESSED된 자료는 재처리(및 예약)를 건너뛴다."""
    hospital, source = _stub_source_lookup
    from app.services.essence_engine import compute_source_content_hash

    source.status = SourceStatus.PROCESSED
    source.content_hash = compute_source_content_hash(
        source.title, source.url, source.raw_text, source.operator_note
    )
    from app.services.source_processing_runs import processing_input_hash

    source.source_metadata = {
        "extraction_input_hash": processing_input_hash(source, source.content_hash)
    }

    async def _should_not_reserve(*_args, **_kwargs):
        raise AssertionError("이미 처리된 자료인데 cost_guard 예약을 시도했다")

    monkeypatch.setattr(essence.cost_guard, "check_and_increment", _should_not_reserve)

    async def _should_not_queue(*_args, **_kwargs):
        raise AssertionError("이미 처리된 자료인데 재추출을 큐잉했다")

    monkeypatch.setattr(essence, "_start_source_processing_best_effort", _should_not_queue)

    async def _notes(_db, _source_id):
        return []

    monkeypatch.setattr(essence, "_get_notes_for_source", _notes)

    response = await essence.process_source(hospital.id, source.id, db=_FakeDB())

    assert response["id"] == str(source.id)


# ── essence.py: POST /philosophy/draft ──────────────────────────────────


@pytest.fixture
def _stub_philosophy_inputs(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="테스트병원", slug="test")
    sources = [SimpleNamespace(id=uuid.uuid4())]
    notes = [SimpleNamespace(id=uuid.uuid4())]

    async def _hospital(_db, _hospital_id):
        return hospital

    async def _sources(_db, _hospital_id, _ids):
        return sources

    async def _notes(_db, _source_ids):
        return notes

    async def _lock(_db, _hospital_id):
        return None

    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "_select_processed_sources", _sources)
    monkeypatch.setattr(essence, "_get_notes_for_sources", _notes)
    monkeypatch.setattr(essence, "acquire_hospital_advisory_lock", _lock)
    return hospital


async def test_create_philosophy_draft_blocked_by_cost_guard_returns_429(
    monkeypatch, _stub_philosophy_inputs
):
    hospital = _stub_philosophy_inputs
    monkeypatch.setattr(essence.cost_guard, "check_and_increment", _blocked_decision())

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("cost_guard가 막았는데 철학 합성을 시도했다")

    monkeypatch.setattr(essence, "synthesize_philosophy", _should_not_run)

    from app.schemas.essence import PhilosophyDraftCreate

    with pytest.raises(HTTPException) as exc_info:
        await essence.create_philosophy_draft(
            hospital.id, PhilosophyDraftCreate(created_by="AE"), db=_FakeDB()
        )

    assert exc_info.value.status_code == 429


# ── hospitals.py: POST /{hospital_id}/profile/autofill ──────────────────


async def test_autofill_hospital_profile_blocked_by_cost_guard_returns_429(monkeypatch):
    class _HospitalDB:
        def __init__(self, hospital):
            self.hospital = hospital

        async def get(self, _model, object_id):
            return self.hospital if self.hospital.id == object_id else None

    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="장편한외과의원", website_url="http://hp", blog_url=None
    )
    monkeypatch.setattr(hospitals_api.cost_guard, "check_and_increment", _blocked_decision())

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("cost_guard가 막았는데 autofill_profile을 시도했다")

    monkeypatch.setattr(hospitals_api, "autofill_profile", _should_not_run)

    body = hospitals_api.ProfileAutofillRequest(name=None, website_url=None, blog_url=None)

    with pytest.raises(HTTPException) as exc_info:
        await hospitals_api.autofill_hospital_profile(
            hospital.id, body, db=_HospitalDB(hospital)
        )

    assert exc_info.value.status_code == 429
