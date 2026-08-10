"""Real-Postgres recovery proof for one failed Naver source URL."""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import ROLE_OPERATOR, AdminUser
from app.models.audit import AdminAuditLog
from app.models.essence import HospitalSourceAsset
from app.models.hospital import Hospital
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.services import naver_handoff
from app.services.asset_extractor import naver_blog_post_identity
from app.services.incidents import mark_retrying
from app.services.naver_handoff_incidents import (
    NaverIncidentContext,
    mark_naver_retrying,
    record_naver_failure,
)
from app.services.naver_handoff_runs import NaverRetryConflict
from app.workers import naver_sync

_DATABASE_URL = os.environ.get(
    "TASK18_DATABASE_URL",
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test",
)


@pytest.fixture
async def naver_db() -> AsyncSession:
    engine = create_async_engine(_DATABASE_URL)
    try:
        connection = await engine.connect()
    except OSError as exc:
        await engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def _hospital(db: AsyncSession) -> SimpleNamespace:
    hospital_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, blog_url) "
            "VALUES (:id, :name, :slug, :blog_url)"
        ),
        {
            "id": hospital_id,
            "name": "네이버 복구 검증 의원",
            "slug": f"naver-recovery-{hospital_id.hex}",
            "blog_url": "https://blog.naver.com/recovery_clinic",
        },
    )
    await db.commit()
    return SimpleNamespace(
        id=hospital_id,
        name="네이버 복구 검증 의원",
        blog_url="https://blog.naver.com/recovery_clinic",
    )


async def _account(db: AsyncSession, *, active: bool = True) -> AdminUser:
    account = AdminUser(
        email=f"naver-operator-{uuid.uuid4().hex}@example.test",
        name="네이버 운영 담당자",
        role=ROLE_OPERATOR,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=active,
    )
    db.add(account)
    await db.flush()
    await db.commit()
    return account


@pytest.mark.parametrize(
    ("case", "expected_status"),
    (("spoof", 422), ("inactive", 403), ("missing", 403)),
)
@pytest.mark.asyncio
async def test_retry_endpoint_rejects_spoofed_or_unverified_actor(
    naver_db: AsyncSession,
    case: str,
    expected_status: int,
) -> None:
    # Given: the request does not carry one verified active server-side identity
    account = await _account(naver_db, active=case != "inactive")

    async def override_get_db():
        yield naver_db

    headers = {"X-Admin-Key": "test-admin-key"}
    if case != "missing":
        headers["X-Admin-Actor"] = account.email
    payload = {"actor": "위조된 담당자"} if case == "spoof" else {}
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        # When: the caller asks to retry a URL with the invalid identity/body
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/admin/hospitals/{uuid.uuid4()}/essence/sources/crawl-blog/"
                f"runs/{uuid.uuid4()}/items/{'a' * 64}/retry",
                headers=headers,
                json=payload,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    # Then: no caller-supplied actor can enter the recovery audit trail
    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_same_naver_url_keeps_incident_state_isolated_per_hospital(
    naver_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared public post URL must never create shared tenant recovery state."""
    first_hospital = await _hospital(naver_db)
    second_hospital = await _hospital(naver_db)
    shared_url = "https://m.blog.naver.com/shared_clinic/777"

    async def fake_urls(_ref: str, _max_posts: int):
        return [shared_url], None

    async def failed_fetch(_url: str):
        return "", "HTTP 503 — URL 접근 실패.", None

    monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
    monkeypatch.setattr(naver_handoff, "fetch_url_text", failed_fetch)
    first_run = await naver_handoff.sync_hospital_naver_sources(
        naver_db, first_hospital
    )
    second_run = await naver_handoff.sync_hospital_naver_sources(
        naver_db, second_hospital
    )

    incidents = list(
        (
            await naver_db.scalars(
                select(Incident).where(
                    Incident.hospital_id.in_([first_hospital.id, second_hospital.id])
                )
            )
        ).all()
    )
    assert {incident.hospital_id for incident in incidents} == {
        first_hospital.id,
        second_hospital.id,
    }
    assert len({incident.dedupe_key for incident in incidents}) == 2

    second_item = second_run.items[0]
    claimed = await mark_naver_retrying(
        naver_db,
        NaverIncidentContext(
            second_hospital.id,
            second_hospital.name,
            second_run.run_id,
            second_item,
            "tenant-check@example.test",
        ),
    )
    assert claimed is not None and claimed.hospital_id == second_hospital.id
    first_incident = next(
        incident for incident in incidents if incident.hospital_id == first_hospital.id
    )
    await naver_db.refresh(first_incident)
    assert first_incident.state == "OPEN"
    assert first_run.items[0].url_hash == second_item.url_hash


@pytest.mark.asyncio
async def test_failed_url_retries_without_reprocessing_success(
    naver_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one post is valid and the other is temporarily unavailable
    hospital = await _hospital(naver_db)
    first_url = "https://blog.naver.com/recovery_clinic/101"
    failed_url = "https://m.blog.naver.com/recovery_clinic/202"
    empty_url = "https://blog.naver.com/recovery_clinic/303"

    async def fake_urls(_ref: str, max_posts: int):
        assert max_posts == 15
        return [first_url, failed_url, empty_url], None

    first_identity = naver_blog_post_identity(first_url)
    failed_identity = naver_blog_post_identity(failed_url)
    empty_identity = naver_blog_post_identity(empty_url)
    attempts = {first_identity: 0, failed_identity: 0, empty_identity: 0}

    async def first_fetch(url: str):
        attempts[url] += 1
        if url == failed_identity:
            return "", "HTTP 500 — URL 접근 실패.", None
        if url == empty_identity:
            return "", None, SimpleNamespace(looks_like_shell=True)
        return "원장 진료 철학과 병원 근거 " * 30, None, SimpleNamespace(looks_like_shell=False)

    monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
    monkeypatch.setattr(naver_handoff, "fetch_url_text", first_fetch)
    original = await naver_sync.sync_hospital_naver_sources(naver_db, hospital)
    actor = await _account(naver_db)
    other_hospital = await _hospital(naver_db)
    original_source_id = original.items[0].source_id
    assert [item.state.value for item in original.items] == [
        "INGESTED",
        "FAILED",
        "SKIPPED",
    ]

    async def override_get_db():
        yield naver_db

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            open_response = await client.get(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/crawl-blog/failures",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
            )
            cross_hospital_response = await client.post(
                f"/api/v1/admin/hospitals/{other_hospital.id}/essence/sources/crawl-blog/"
                f"runs/{original.run_id}/items/{original.items[1].url_hash}/retry",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
                json={},
            )
            crawl_actor_spoof_response = await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/crawl-blog",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
                json={
                    "url": "https://blog.naver.com/recovery_clinic",
                    "max_posts": 1,
                    "created_by": "위조된 작성자",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter
    assert open_response.status_code == 200
    assert cross_hospital_response.status_code == 404
    assert crawl_actor_spoof_response.status_code == 422
    assert open_response.json() == {
        "items": [
            {
                **original.items[1].payload(),
                "operation_run_id": str(original.run_id),
            }
        ]
    }

    # Given: another operator request has already claimed this URL retry
    active_incident = await naver_db.scalar(
        select(Incident).where(Incident.hospital_id == hospital.id)
    )
    assert active_incident is not None
    claimed = await mark_retrying(
        naver_db,
        active_incident.id,
        expected_version=active_incident.version,
        actor=actor.email,
        reason="동시 재시도 검증",
    )
    await naver_db.commit()
    assert isinstance(claimed, Incident)
    run_count_before_conflict = await naver_db.scalar(select(func.count(OperationRun.id)))

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            active_retry_response = await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/crawl-blog/"
                f"runs/{original.run_id}/items/{original.items[1].url_hash}/retry",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
                json={},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter
    assert active_retry_response.status_code == 409
    assert await naver_db.scalar(select(func.count(OperationRun.id))) == run_count_before_conflict
    await record_naver_failure(
        naver_db,
        NaverIncidentContext(
            hospital.id,
            hospital.name,
            original.run_id,
            original.items[1],
            actor.email,
        ),
    )
    await naver_db.commit()

    # When: a retry still has no usable body, it must not be called recovered
    async def empty_retry_fetch(url: str):
        attempts[url] += 1
        return "", None, SimpleNamespace(looks_like_shell=True)

    monkeypatch.setattr(naver_handoff, "fetch_url_text", empty_retry_fetch)
    empty_retry = await naver_handoff.retry_failed_naver_source(
        naver_db,
        naver_handoff.NaverRetryRequest(
            hospital=hospital,
            parent_run_id=original.run_id,
            url_hash=original.items[1].url_hash,
            actor=actor.email,
        ),
    )
    assert empty_retry.items[0].safe_error_code == "EMPTY_CONTENT"
    still_open = await naver_db.scalar(
        select(Incident).where(Incident.hospital_id == hospital.id)
    )
    assert still_open is not None and still_open.state == "OPEN"
    assert await naver_db.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.incident_id == still_open.id
        )
    ) == 0

    # When: only the failed URL is retried after Naver recovers
    async def recovered_fetch(url: str):
        attempts[url] += 1
        return "복구 후 확인된 병원 근거 " * 30, None, SimpleNamespace(looks_like_shell=False)

    monkeypatch.setattr(naver_handoff, "fetch_url_text", recovered_fetch)
    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/crawl-blog/"
                f"runs/{original.run_id}/items/{original.items[1].url_hash}/retry",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
                json={},
            )
            recovered_list_response = await client.get(
                f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/crawl-blog/failures",
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor": actor.email,
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter
    assert response.status_code == 200
    assert recovered_list_response.status_code == 200
    assert recovered_list_response.json() == {"items": []}
    payload = response.json()

    # Then: the successful source is untouched and failure truth becomes recovered
    assert attempts == {first_identity: 1, failed_identity: 3, empty_identity: 1}
    assert payload["items"][0]["state"] == "INGESTED"
    assert await naver_db.scalar(
        select(func.count(HospitalSourceAsset.id)).where(
            HospitalSourceAsset.hospital_id == hospital.id
        )
    ) == 2
    assert await naver_db.get(HospitalSourceAsset, original_source_id) is not None
    incident = await naver_db.scalar(
        select(Incident).where(Incident.hospital_id == hospital.id)
    )
    assert incident is not None and incident.state == "RECOVERED"
    assert "클릭" not in incident.customer_impact
    assert "개발팀" in incident.next_action
    outbox = await naver_db.scalar(
        select(NotificationOutbox).where(NotificationOutbox.incident_id == incident.id)
    )
    assert outbox is not None and outbox.state == "PENDING"
    child = await naver_db.get(OperationRun, uuid.UUID(payload["operation_run_id"]))
    assert child is not None and child.parent_run_id == original.run_id
    audit_actor = await naver_db.scalar(
        select(AdminAuditLog.actor).where(
            AdminAuditLog.action == "retry_naver_blog_item",
            AdminAuditLog.target_id == payload["operation_run_id"],
        )
    )
    assert audit_actor == actor.email


@pytest.mark.asyncio
async def test_concurrent_retry_creates_one_child_and_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two real transactions must serialize one stable URL retry claim."""
    engine = create_async_engine(_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id: uuid.UUID | None = None
    release_fetch = asyncio.Event()
    fetch_started = asyncio.Event()
    try:
        async with sessions() as setup_db:
            hospital = await _hospital(setup_db)
            hospital_id = hospital.id
            failed_url = "https://m.blog.naver.com/recovery_clinic/909"

            async def fake_urls(_ref: str, _max_posts: int):
                return [failed_url], None

            async def initial_failure(_url: str):
                return "", "HTTP 503 — URL 접근 실패.", None

            monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
            monkeypatch.setattr(naver_handoff, "fetch_url_text", initial_failure)
            original = await naver_handoff.sync_hospital_naver_sources(setup_db, hospital)
            failed = original.items[0]

        fetch_count = 0

        async def blocked_success(_url: str):
            nonlocal fetch_count
            fetch_count += 1
            fetch_started.set()
            await release_fetch.wait()
            return "동시 재시도에서 한 번만 저장되는 병원 근거 " * 30, None, SimpleNamespace(
                looks_like_shell=False
            )

        monkeypatch.setattr(naver_handoff, "fetch_url_text", blocked_success)

        async def retry_once():
            async with sessions() as retry_db:
                hospital_row = await retry_db.get(Hospital, hospital.id)
                assert hospital_row is not None
                return await naver_handoff.retry_failed_naver_source(
                    retry_db,
                    naver_handoff.NaverRetryRequest(
                        hospital=hospital_row,
                        parent_run_id=original.run_id,
                        url_hash=failed.url_hash,
                        actor="concurrency@example.test",
                    ),
                )

        first_retry = asyncio.create_task(retry_once())
        await asyncio.wait_for(fetch_started.wait(), timeout=3)
        with pytest.raises(NaverRetryConflict) as conflict:
            await retry_once()
        assert conflict.value.code == "NAVER_RETRY_ALREADY_CLAIMED"
        release_fetch.set()
        first_result = await asyncio.wait_for(first_retry, timeout=3)
        assert first_result.items[0].state.value == "INGESTED"

        async with sessions() as verify_db:
            assert fetch_count == 1
            assert await verify_db.scalar(
                select(func.count(OperationRun.id)).where(
                    OperationRun.parent_run_id == original.run_id
                )
            ) == 1
            assert await verify_db.scalar(
                select(func.count(HospitalSourceAsset.id)).where(
                    HospitalSourceAsset.hospital_id == hospital.id
                )
            ) == 1
    except OSError as exc:
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    finally:
        release_fetch.set()
        if hospital_id is not None:
            async with sessions() as cleanup_db:
                await cleanup_db.execute(
                    delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
                )
                await cleanup_db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
                await cleanup_db.execute(
                    delete(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id)
                )
                await cleanup_db.execute(
                    delete(OperationRun).where(OperationRun.hospital_id == hospital_id)
                )
                await cleanup_db.execute(delete(Hospital).where(Hospital.id == hospital_id))
                await cleanup_db.commit()
        await engine.dispose()
