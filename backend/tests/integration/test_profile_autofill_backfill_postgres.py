"""자동 입력 백필 CLI(P4-7) — 실제 PostgreSQL에서 dry-run은 아무것도 쓰지 않고, --apply는 원장 백필·
저장 초안 적용·수집 디스패치를 하며, 공개 중 병원은 --include-live 없이 적용하지 않는다."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdminAuditLog
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services import profile_autofill_apply as apply_mod
from app.services import profile_completion
from app.utils import profile_autofill_backfill as cli
from app.workers import tasks as worker_tasks


def _hospital(**overrides) -> Hospital:
    values = dict(
        id=uuid.uuid4(),
        name=f"백필-{uuid.uuid4().hex[:6]}",
        slug=f"bf-{uuid.uuid4().hex[:10]}",
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


def _legacy_run(hospital_id, draft) -> OperationRun:
    """E4 이전 초안 — naver_identity·verified·apply_status가 없다."""

    now = datetime.now(UTC)
    return OperationRun(
        hospital_id=hospital_id,
        operation_type="PROFILE_AUTOFILL",
        idempotency_key="registration",
        state=OperationRunState.SUCCEEDED,
        total_count=1,
        success_count=1,
        attempt_count=1,
        request_payload={"trigger": "contract_registration"},
        result_summary={
            "draft": draft,
            "field_meta": {
                "phone": {"source": "naver", "confidence": 0.9},
                "website_url": {"source": "homepage", "confidence": 0.9},
            },
            "sources": [{"name": "네이버 플레이스", "ok": True}],
        },
        requested_at=now,
        started_at=now,
        completed_at=now,
    )


@pytest.fixture
def quiet_effects(monkeypatch):
    async def fake_dispatch(_db, _command, _task):
        return None

    async def fake_revalidate(*_args, **_kwargs):
        return True

    monkeypatch.setattr(profile_completion, "dispatch_operation", fake_dispatch)
    monkeypatch.setattr(worker_tasks.build_aeo_site, "apply_async", lambda **_kw: None)
    monkeypatch.setattr(
        "app.services.site_revalidate.trigger_hospital_site_revalidate_safe", fake_revalidate
    )
    monkeypatch.setattr(
        "app.services.profile_channel_sources.celery_app.send_task", lambda *a, **k: None
    )

    async def no_geocode(_address):
        raise apply_mod.GeocodingError("테스트에서는 좌표를 찾지 않는다")

    monkeypatch.setattr(apply_mod, "geocode_address", no_geocode)


def _sessions(db: AsyncSession):
    return lambda: AsyncSession(
        bind=db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


async def _seed(db):
    legacy = _hospital()
    live = _hospital(
        status=HospitalStatus.ACTIVE,
        site_live=True,
        site_built=True,
        profile_complete=True,
        address="서울",
        phone="02-1",
        treatments=[{"name": "진료"}],
    )
    fresh = _hospital()
    paused = _hospital(status=HospitalStatus.PAUSED)
    db.add_all([legacy, live, fresh, paused])
    await db.flush()
    db.add(
        HospitalHandoff(
            hospital_id=legacy.id,
            state=HandoffState.HANDOFF_ACCEPTED,
            acceptance_source=HandoffSource.LEGACY_BACKFILL,
            accepted_at=datetime.now(UTC),
        )
    )
    draft = {"phone": "02-555-0000", "website_url": "https://legacy-clinic.example.com"}
    db.add_all([_legacy_run(legacy.id, draft), _legacy_run(live.id, draft)])
    db.add(
        AdminAuditLog(
            hospital_id=legacy.id,
            actor="ae@example.com",
            action="update_profile",
            detail={"changed_fields": ["keywords"]},
        )
    )
    await db.commit()
    return {"legacy": legacy.id, "live": live.id, "fresh": fresh.id, "paused": paused.id}


async def _run(db, ids, **kwargs):
    out = io.StringIO()
    dispatched = []

    def dispatch(hospital_id, key, *, allow_live=False):
        dispatched.append((hospital_id, key, allow_live))
        return True

    await cli.run_backfill(
        hospital_ids=list(ids.values()),
        out=out,
        sessions=_sessions(db),
        dispatch=dispatch,
        **kwargs,
    )
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    rows = {row["hospital_id"]: row for row in lines if "hospital_id" in row}
    return rows, lines[-1]["summary"], dispatched


@pytest.mark.asyncio
async def test_dry_run_reports_the_plan_and_writes_nothing(pg_async_session, quiet_effects):
    db = pg_async_session
    ids = await _seed(db)

    rows, summary, dispatched = await _run(db, ids, apply=False, include_live=False)

    assert summary["mode"] == "dry-run"
    assert dispatched == []
    legacy = rows[str(ids["legacy"])]
    assert legacy["action"] == "WOULD_APPLY_STORED_DRAFT"
    # E4 이전 초안의 네이버 값(전화)은 확인되지 않았다 — 홈페이지 값만 적용 예정.
    assert legacy["would_apply_fields"] == ["website_url"]
    assert legacy["skipped_unverified_fields"] == ["phone"]
    assert legacy["provenance_backfill"]["operator"] == ["keywords"]
    assert rows[str(ids["fresh"])]["action"] == "DISPATCH_COLLECTION"
    assert rows[str(ids["paused"])]["action"] == "SKIPPED_PAUSED"
    db.expire_all()
    stored = await db.get(Hospital, ids["legacy"], populate_existing=True)
    assert stored.website_url is None and stored.profile_field_provenance == {}


@pytest.mark.asyncio
async def test_apply_backfills_applies_stored_drafts_and_dispatches_collections(
    pg_async_session, quiet_effects
):
    db = pg_async_session
    ids = await _seed(db)

    rows, _summary, dispatched = await _run(db, ids, apply=True, include_live=False)

    assert rows[str(ids["legacy"])]["action"] == "APPLY_STORED_DRAFT"
    assert rows[str(ids["legacy"])]["apply_status"] == "APPLIED"
    assert rows[str(ids["live"])]["apply_status"] == "SKIPPED_LIVE"
    assert dispatched == [(ids["fresh"], "backfill", False)]
    db.expire_all()
    legacy = await db.get(Hospital, ids["legacy"], populate_existing=True)
    assert legacy.website_url == "https://legacy-clinic.example.com"
    assert legacy.phone is None
    assert legacy.profile_field_provenance["keywords"]["origin"] == "OPERATOR"
    assert legacy.profile_field_provenance["website_url"]["origin"] == "AUTOFILL"
    live = await db.get(Hospital, ids["live"], populate_existing=True)
    assert live.website_url is None

    # 두 번째 실행은 이미 적용한 초안을 다시 적용하지 않는다. 공개 병원은 --include-live로만.
    rows, _summary, dispatched = await _run(db, ids, apply=True, include_live=True)
    assert rows[str(ids["legacy"])]["action"] == "ALREADY_APPLIED"
    assert rows[str(ids["live"])]["apply_status"] == "APPLIED"
    assert dispatched == [(ids["fresh"], "backfill", True)]
    db.expire_all()
    live = await db.get(Hospital, ids["live"], populate_existing=True)
    assert live.website_url == "https://legacy-clinic.example.com"
    assert live.address == "서울"


def test_cli_args_accept_ids_and_repeated_hospitals():
    first, second = uuid.uuid4(), uuid.uuid4()
    args = cli.parse_args([f"--ids={first},{second}", f"--hospital={first}", "--apply"])
    assert args.ids == [first, second] and args.apply is True and args.include_live is False
    assert cli.parse_args([]).ids is None
    with pytest.raises(SystemExit):
        cli.parse_args(["--ids=not-a-uuid"])
