"""자동 입력 적용·완료 승격(P4-6·P4-7)을 실제 PostgreSQL에서 검증한다.

- 빈 칸만 채우고 마케터 칸(OPERATOR)은 덮지 않는다 — 적용 뒤 PATCH가 이기고, PATCH 뒤 적용은 건너뛴다.
- 의료진이 없으면 대표 의료진을 만들고, 주소는 지오코딩하고, 홈페이지는 근거 자료로 등록한다.
- 필수 항목이 다 차면 완료로 승격하고 사이트 준비·V0를 보낸다. 인수 미수락이면 승격하지 않는다.
- 일시정지·공개 중 병원은 손대지 않는다(공개 중은 allow_live일 때만).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import hospitals as hospitals_api
from app.models.audit import AdminAuditLog
from app.models.essence import HospitalSourceAsset
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun
from app.models.physician import HospitalPhysician
from app.services import profile_autofill_apply as apply_mod
from app.services import profile_channel_sources, profile_completion
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.hospital_geocoding import GeocodeResult
from app.services.hospital_profile_autofill import AutofillResult, SourceStatus
from app.workers import profile_autofill_tasks
from app.workers import tasks as worker_tasks

_DRAFT = {
    "director_name": "김원장",
    "director_career": "외과 전문의",
    "address": "서울 성동구 왕십리로 1",
    "phone": "02-000-0000",
    "business_hours": {"mon": "09:00-18:00"},
    "website_url": "https://clinic-autofill.example.com",
    "naver_place_url": "https://m.place.naver.com/hospital/1595672233/home",
    "naver_place_id": "1595672233",
    "region": ["성동구"],
    "specialties": ["외과"],
    "keywords": ["치질", "최고 치질"],
    "treatments": [{"name": "치질 수술", "description": "부작용 없는 시술"}],
}


def _summary(draft=None):
    draft = dict(draft or _DRAFT)
    return {
        "draft": draft,
        "field_meta": {key: {"source": "naver", "verified": True} for key in draft},
        "naver_identity": "VERIFIED_SEARCH",
    }


def _hospital(**overrides) -> Hospital:
    values = dict(
        id=uuid.uuid4(),
        name="자동적용의원",
        slug=f"autofill-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ONBOARDING,
        profile_complete=False,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        treatments=[],
        profile_field_provenance={},
    )
    values.update(overrides)
    return Hospital(**values)


def _accepted(hospital_id):
    return HospitalHandoff(
        hospital_id=hospital_id,
        state=HandoffState.HANDOFF_ACCEPTED,
        acceptance_source=HandoffSource.LEGACY_BACKFILL,
        accepted_at=datetime.now(UTC),
    )


@pytest.fixture
def effects(monkeypatch):
    calls = {"site": [], "v0": [], "fetch": [], "revalidate": [], "geocode": []}

    async def fake_geocode(address):
        calls["geocode"].append(address)
        return GeocodeResult(latitude=37.56, longitude=127.03)

    async def fake_dispatch(_db, command, _task):
        calls["v0"].append(command.hospital_id)

    async def fake_revalidate(slug, treatments=None, *, hospital_name=None):
        calls["revalidate"].append(slug)
        return True

    monkeypatch.setattr(apply_mod, "geocode_address", fake_geocode)
    monkeypatch.setattr(profile_completion, "dispatch_operation", fake_dispatch)
    monkeypatch.setattr(
        worker_tasks.build_aeo_site, "apply_async", lambda **kw: calls["site"].append(kw["args"])
    )
    monkeypatch.setattr(
        profile_channel_sources.celery_app,
        "send_task",
        lambda *args, **kw: calls["fetch"].append(kw["args"]),
    )
    monkeypatch.setattr(
        "app.services.site_revalidate.trigger_hospital_site_revalidate_safe", fake_revalidate
    )
    return calls


def _session_factory(session: AsyncSession):
    """워커 코드가 여는 새 세션을 테스트 연결(롤백되는 트랜잭션) 위의 SAVEPOINT로 만든다."""

    def factory():
        return AsyncSession(
            bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

    return lambda: factory


async def _reload(db: AsyncSession, hospital_id) -> Hospital:
    db.expire_all()
    return await db.get(Hospital, hospital_id, populate_existing=True)


@pytest.mark.asyncio
async def test_apply_fills_empty_fields_promotes_and_dispatches(pg_async_session, effects):
    db = pg_async_session
    hospital = _hospital()
    db.add(hospital)
    await db.flush()
    db.add(_accepted(hospital.id))
    await db.commit()

    outcome = await apply_mod.apply_autofill_draft(db, hospital.id, _summary())

    stored = await _reload(db, hospital.id)
    assert outcome.status == "APPLIED"
    assert stored.address == "서울 성동구 왕십리로 1"
    assert stored.keywords == ["치질"]  # 금지 표현 원소는 빠졌다
    assert stored.treatments == [{"name": "치질 수술", "description": None}]
    assert (stored.latitude, stored.longitude) == (37.56, 127.03)
    assert stored.profile_complete is True
    ledger = stored.profile_field_provenance
    assert ledger["address"]["origin"] == "AUTOFILL" and ledger["address"]["source"] == "naver"
    physicians = (
        await db.execute(select(HospitalPhysician).where(HospitalPhysician.hospital_id == hospital.id))
    ).scalars().all()
    assert [(row.name, row.career, row.is_representative) for row in physicians] == [
        ("김원장", "외과 전문의", True)
    ]
    sources = (
        await db.execute(
            select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital.id)
        )
    ).scalars().all()
    assert [row.url for row in sources] == ["https://clinic-autofill.example.com"]
    assert effects["fetch"] == [[str(sources[0].id)]]
    assert effects["site"] == [[str(hospital.id)]]
    assert effects["v0"] == [hospital.id]
    audit = (
        await db.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.hospital_id == hospital.id,
                AdminAuditLog.action == "apply_profile_autofill",
            )
        )
    ).scalar_one()
    assert audit.actor == "system:profile-autofill"
    assert audit.detail["profile_complete_transition"] is True
    assert {item["field"] for item in audit.detail["dropped_fields"]} >= {
        "keywords",
        "treatments.description",
    }


@pytest.mark.asyncio
async def test_without_handoff_acceptance_values_apply_but_completion_waits(pg_async_session, effects):
    db = pg_async_session
    hospital = _hospital()
    db.add(hospital)
    await db.commit()

    outcome = await apply_mod.apply_autofill_draft(db, hospital.id, _summary())

    stored = await _reload(db, hospital.id)
    assert outcome.status == "APPLIED"
    assert outcome.completion["blocked"] == "HANDOFF_NOT_ACCEPTED"
    assert stored.address and stored.profile_complete is False
    assert effects["site"] == [] and effects["v0"] == []


@pytest.mark.asyncio
async def test_a_later_patch_wins_and_an_earlier_patch_is_never_overwritten(pg_async_session, effects):
    db = pg_async_session
    hospital = _hospital()
    db.add(hospital)
    await db.flush()
    db.add(_accepted(hospital.id))
    await db.commit()

    token = set_request_actor("ae@example.com")
    try:
        # PATCH 먼저 — 마케터가 진료시간을 넣었다.
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(business_hours={"mon": "08:30-17:30"}),
            BackgroundTasks(),
            db=db,
        )
        outcome = await apply_mod.apply_autofill_draft(db, hospital.id, _summary())
        stored = await _reload(db, hospital.id)
        assert stored.business_hours["mon"] == "08:30-17:30"
        assert "business_hours" in outcome.skipped_operator_fields
        assert stored.profile_field_provenance["business_hours"]["conflict"]["value"] == {
            "mon": "09:00-18:00"
        }
        # 적용 뒤 PATCH — 마케터가 자동 입력된 전화번호를 고친다. PATCH가 이기고 OPERATOR가 남는다.
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(phone="02-123-4567"),
            BackgroundTasks(),
            db=db,
        )
    finally:
        reset_request_actor(token)

    stored = await _reload(db, hospital.id)
    assert stored.phone == "02-123-4567"
    assert stored.profile_field_provenance["phone"]["origin"] == "OPERATOR"
    again = await apply_mod.apply_autofill_draft(db, hospital.id, _summary({"phone": "02-000-0000"}))
    stored = await _reload(db, hospital.id)
    assert stored.phone == "02-123-4567"
    assert again.skipped_operator_fields == ["phone"]


@pytest.mark.asyncio
async def test_paused_and_live_hospitals_are_left_alone(pg_async_session, effects):
    db = pg_async_session
    paused = _hospital(status=HospitalStatus.PAUSED)
    live = _hospital(
        status=HospitalStatus.ACTIVE,
        site_live=True,
        site_built=True,
        profile_complete=True,
        address="서울 성동구",
        phone="02-1",
        treatments=[{"name": "치질 수술"}],
    )
    db.add_all([paused, live])
    await db.commit()
    live_slug, live_id, paused_id = live.slug, live.id, paused.id

    paused_outcome = await apply_mod.apply_autofill_draft(db, paused_id, _summary())
    live_outcome = await apply_mod.apply_autofill_draft(db, live_id, _summary())

    assert paused_outcome.status == "SKIPPED_PAUSED"
    assert live_outcome.status == "SKIPPED_LIVE"
    assert "website_url" in live_outcome.would_apply_fields
    assert (await _reload(db, paused_id)).address is None
    stored_live = await _reload(db, live_id)
    assert stored_live.website_url is None and stored_live.status == HospitalStatus.ACTIVE

    allowed = await apply_mod.apply_autofill_draft(db, live_id, _summary(), allow_live=True)
    stored_live = await _reload(db, live_id)
    assert allowed.status == "APPLIED"
    assert stored_live.website_url == "https://clinic-autofill.example.com"
    assert stored_live.address == "서울 성동구"  # 값이 있던 칸은 그대로
    assert effects["revalidate"] == [live_slug]


@pytest.mark.asyncio
async def test_worker_collects_stores_and_applies_once(pg_async_session, effects, monkeypatch):
    db = pg_async_session
    hospital = _hospital(name="워커적용의원")
    db.add(hospital)
    await db.flush()
    db.add(_accepted(hospital.id))
    await db.commit()

    async def fake_autofill(name, **kwargs):
        return AutofillResult(
            draft=dict(_DRAFT),
            field_meta={key: {"source": "naver", "verified": True} for key in _DRAFT},
            sources=[SourceStatus("네이버 플레이스", ok=True)],
            naver_identity="VERIFIED_SEARCH",
            naver_place_id="1595672233",
        )

    async def allow(_category):
        from types import SimpleNamespace

        return SimpleNamespace(allowed=True, reason=None)

    monkeypatch.setattr(profile_autofill_tasks, "get_async_sessionmaker", _session_factory(db))
    monkeypatch.setattr(profile_autofill_tasks, "autofill_profile", fake_autofill)
    monkeypatch.setattr(profile_autofill_tasks.cost_guard, "check_and_increment", allow)

    first = await profile_autofill_tasks._autofill_once(hospital.id)
    second = await profile_autofill_tasks._autofill_once(hospital.id)

    assert first == {"status": "DRAFT_STORED", "apply_status": "APPLIED"}
    assert second == {"status": "ALREADY_DONE"}
    run = (
        await db.execute(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "PROFILE_AUTOFILL",
            )
        )
    ).scalar_one()
    await db.refresh(run)
    assert run.result_summary["apply_status"] == "APPLIED"
    assert "address" in run.result_summary["applied_fields"]
    assert run.result_summary["followup_needed"] is True  # 홈페이지 없이 모았고 주소가 새로 생겼다
    assert profile_autofill_tasks.next_autofill_key([run], now=datetime.now(UTC)) == "followup"
    stored = await _reload(db, hospital.id)
    assert stored.profile_complete is True


@pytest.mark.asyncio
async def test_promotion_sweep_opens_hospitals_the_d31_predicate_now_passes(
    pg_async_session, effects, monkeypatch
):
    db = pg_async_session
    complete_fields = dict(
        director_name="김원장",
        director_career="외과 전문의",
        address="서울 성동구",
        phone="02-1",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://a.example",
        naver_place_url="https://naver.me/x",
        latitude=37.5,
        longitude=127.0,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        treatments=[{"name": "치질 수술"}],
    )
    ready = _hospital(**complete_fields)  # 진료 철학·Google 없음 — D31 이전에는 막혔다
    not_accepted = _hospital(**complete_fields)
    paused = _hospital(status=HospitalStatus.PAUSED, **complete_fields)
    missing_career = _hospital(**{**complete_fields, "director_career": None})
    db.add_all([ready, not_accepted, paused, missing_career])
    await db.flush()
    db.add_all([_accepted(ready.id), _accepted(paused.id), _accepted(missing_career.id)])
    await db.commit()
    ready_id, not_accepted_id, paused_id, missing_id = (
        ready.id, not_accepted.id, paused.id, missing_career.id
    )
    monkeypatch.setattr(profile_autofill_tasks, "get_async_sessionmaker", _session_factory(db))

    promoted = await profile_autofill_tasks.promote_completed_profiles(limit=1000)

    assert str(ready_id) in promoted
    assert not {str(not_accepted_id), str(paused_id), str(missing_id)} & set(promoted)
    assert (await _reload(db, ready_id)).profile_complete is True
    assert (await _reload(db, paused_id)).status == HospitalStatus.PAUSED
    assert [str(ready_id)] == [args[0] for args in effects["site"] if args[0] == str(ready_id)]
    audit = (
        await db.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.hospital_id == ready_id,
                AdminAuditLog.action == "promote_profile_complete",
            )
        )
    ).scalar_one()
    assert audit.detail["profile_complete_transition"] is True
