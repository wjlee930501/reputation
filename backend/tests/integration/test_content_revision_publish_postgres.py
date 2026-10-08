"""Real PostgreSQL/API proof for candidate-to-active revision publication."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.content import (
    ContentItem,
    ContentRevision,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_candidate_publication import (
    CandidateApprovalApplied,
    CandidateApprovalRejected,
    CandidateStageConflict,
    parse_pending_candidate,
    pending_candidate_content,
    publish_pending_candidate,
    reject_pending_candidate,
    stage_pending_candidate_cas,
)
from app.services.content_revision_storage import reconcile_content_revisions
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.services.reference_publication import refresh_publication_references
from app.services.reference_verification import (
    FetchResult,
    ReferenceVerifier,
    item_topic_fingerprint,
    override_reference_fetcher,
    reference_check_record,
)
from tests.db_env import require_db_url
from tests.reference_fetch_doubles import PageFetcher, document_body, page_html

pytestmark = pytest.mark.asyncio


@pytest.fixture
def reference_http_server():
    html = page_html(
        "치핵 | 국가건강정보포털 | 질병관리청",
        document_body("치핵"),
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/reference"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _async_database_url() -> str:
    url = require_db_url("INTEGRATION_DATABASE_URL")
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


async def _seed(db):
    now = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    token = uuid.uuid4().hex[:10]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"판 전환 병원 {token}",
        slug=f"revision-publish-{token}",
        address="서울시 강남구 테헤란로 1",
        phone="02-1234-5678",
        treatments=[{"name": "외과 진료"}],
        status=HospitalStatus.ACTIVE,
        site_built=True,
        site_live=True,
        schedule_set=True,
        region=["서울"],
        specialties=["외과"],
        keywords=[],
        competitors=[],
    )
    db.add(hospital)
    await db.flush()
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="공식 진료 안내",
        raw_text="병원 진료 범위와 예약 전 확인 사항입니다.",
        content_hash=f"source-{token}",
        status=SourceStatus.PROCESSED,
        processed_at=now,
    )
    db.add(source)
    await db.flush()
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="공식 자료에 근거해 설명합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        source_asset_ids=[str(source.id)],
        approved_at=now,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 9, 1),
    )
    db.add_all([philosophy, schedule])
    await db.flush()
    reference = {"title": "질병관리청", "url": "https://health.kdca.go.kr/example"}
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=1,
        total_count=12,
        title="승인된 이전 제목",
        body="승인된 이전 본문입니다.",
        meta_description="이전 설명",
        references_list=[reference],
        reference_checks=[],
        scheduled_date=date(2026, 10, 1),
        status=ContentStatus.PUBLISHED,
        published_at=now,
        published_by="first@example.com",
        first_published_at=now,
        first_published_by="first@example.com",
        generated_at=now,
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy.id,
        generation_philosophy_id=philosophy.id,
        last_reviewed_philosophy_id=philosophy.id,
        content_revision=4,
        content_brief={
            "schema_version": "content-brief-v2",
            "target_query": "외과 진료 예약 전 확인 사항",
            "treatment_narrative": {
                "source": "approved_philosophy",
                "angle": "공식 자료에 근거한 예약 안내",
            },
            "source_snapshot": {"hash": token, "source_asset_ids": [str(source.id)]},
        },
        essence_check_summary={"generation_provenance": {"source_asset_ids": [str(source.id)]}},
    )
    legacy_checked_at = datetime.now(UTC) - timedelta(days=365)
    item.reference_checks = [
        reference_check_record(
            reference["url"],
            verdict="pass",
            reason="page_verified",
            checked_at=legacy_checked_at,
            curated=False,
            status=200,
            final_url=reference["url"],
            page_title="질병관리청 외과 진료 안내",
            text_len=500,
            verified_at=legacy_checked_at,
            topic_fingerprint=item_topic_fingerprint(item),
        )
    ]
    db.add(item)
    await db.flush()
    written = await reconcile_content_revisions(db, content_item_id=item.id)
    assert written.created_count == 1
    await db.flush()
    loaded = (
        await db.execute(
            select(ContentItem)
            .options(selectinload(ContentItem.active_revision))
            .where(ContentItem.id == item.id)
        )
    ).scalar_one()
    return hospital, philosophy, loaded


async def _get_public(db, hospital: Hospital, item: ContentItem):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    previous = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/v1/public/hospitals/{hospital.slug}/contents/{item.id}")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous


async def _get_public_list(db, hospital: Hospital):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    previous = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/v1/public/hospitals/{hospital.slug}/contents")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous


async def _cancel_candidate(db, hospital: Hospital, item: ContentItem, candidate_sha: str):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                f"/api/v1/admin/hospitals/{hospital.id}/content/{item.id}/candidate/cancel",
                json={"candidate_sha256": candidate_sha},
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor-System": "publication-qa",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _patch_content(db, hospital: Hospital, item: ContentItem, payload: dict):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.patch(
                f"/api/v1/admin/hospitals/{hospital.id}/content/{item.id}",
                json=payload,
                headers={
                    "X-Admin-Key": "test-admin-key",
                    "X-Admin-Actor-System": "publication-qa",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def _pass(candidate) -> ContentAiReview:
    content = pending_candidate_content(candidate)
    return ContentAiReview(
        status=ContentAiReviewStatus.PASS,
        confidence=0.98,
        findings=(),
        summary="통과",
        model="test-reviewer",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
    )


def _candidate_checks(item: ContentItem, *, title: str, body: str) -> list[dict]:
    observed = datetime.now(UTC)
    view = SimpleNamespace(
        **{
            **vars(item),
            "active_revision_id": None,
            "title": title,
            "body": body,
            "faq_question": None,
        }
    )
    url = item.references_list[0]["url"]
    return [
        reference_check_record(
            url,
            verdict="pass",
            reason="page_verified",
            checked_at=observed,
            curated=False,
            status=200,
            final_url=url,
            page_title="질병관리청 진료 안내",
            text_len=500,
            verified_at=observed,
            topic_fingerprint=item_topic_fingerprint(view),
        )
    ]


async def test_candidate_pass_swaps_one_revision_and_public_cache_identity(pg_async_session):
    hospital, philosophy, item = await _seed(pg_async_session)
    first_published_at = item.first_published_at
    old_pointer = item.active_revision_id
    before = await _get_public(pg_async_session, hospital, item)
    assert before.status_code == 200
    old_hash = before.json()["revision_hash"]
    before_list = await _get_public_list(pg_async_session, hospital)
    assert before_list.status_code == 200
    assert before_list.json()[0]["title"] == "승인된 이전 제목"
    assert before_list.json()[0]["revision_hash"] == old_hash

    candidate = await stage_pending_candidate_cas(
        pg_async_session,
        item,
        expected_active_revision_id=old_pointer,
        expected_content_revision=4,
        title="검수 중인 새 제목",
        body="검수 중인 새 본문입니다.",
        meta_description="새 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="ae@example.com",
        created_at=datetime(2026, 10, 9, 9, 0, tzinfo=UTC),
    )
    during = await _get_public(pg_async_session, hospital, item)
    assert during.json()["body"] == "승인된 이전 본문입니다."
    assert during.json()["revision_hash"] == old_hash

    assert reject_pending_candidate(
        item,
        expected_candidate_sha256=candidate.candidate_sha256,
        review_payload={"status": "REVISE", "candidate_sha256": candidate.candidate_sha256},
        reviewed_at=datetime(2026, 10, 9, 9, 1, tzinfo=UTC),
    )
    rejected = await _get_public(pg_async_session, hospital, item)
    assert rejected.json()["body"] == "승인된 이전 본문입니다."
    stale_cancel = await _cancel_candidate(pg_async_session, hospital, item, "f" * 64)
    assert stale_cancel.status_code == 409
    assert parse_pending_candidate(item) is not None
    assert (
        await pg_async_session.scalar(
            select(func.count(OperationRun.id)).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "SITE_REVALIDATION",
            )
        )
        == 0
    )
    cancelled = await _cancel_candidate(
        pg_async_session, hospital, item, candidate.candidate_sha256
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["pending_revision"] is None
    after_cancel = await _get_public(pg_async_session, hospital, item)
    assert after_cancel.json()["body"] == "승인된 이전 본문입니다."
    assert after_cancel.json()["revision_hash"] == old_hash

    candidate = await stage_pending_candidate_cas(
        pg_async_session,
        item,
        expected_active_revision_id=old_pointer,
        expected_content_revision=4,
        title="승인된 새 제목",
        body="승인된 새 본문입니다.",
        meta_description="새 설명",
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=_candidate_checks(
            item, title="승인된 새 제목", body="승인된 새 본문입니다."
        ),
        created_by="ae@example.com",
        created_at=datetime(2026, 10, 9, 9, 2, tzinfo=UTC),
    )
    outcome = await publish_pending_candidate(
        pg_async_session,
        item,
        _pass(candidate),
        philosophy=philosophy,
        approved_by="system:ai-review",
        approved_at=datetime(2026, 10, 9, 9, 3, tzinfo=UTC),
    )
    assert isinstance(outcome, CandidateApprovalApplied)
    intent = enqueue_public_surface_intent(pg_async_session, hospital, content_ids=[item.id])
    await pg_async_session.flush()
    assert item.active_revision_id != old_pointer
    assert item.first_published_at == first_published_at
    assert intent is not None
    assert (
        await pg_async_session.scalar(
            select(func.count(ContentRevision.id)).where(ContentRevision.content_item_id == item.id)
        )
        == 2
    )
    assert (
        await pg_async_session.scalar(
            select(func.count(OperationRun.id)).where(
                OperationRun.id == intent.id,
                OperationRun.operation_type == "SITE_REVALIDATION",
            )
        )
        == 1
    )

    after = await _get_public(pg_async_session, hospital, item)
    assert after.status_code == 200
    assert after.json()["body"] == "승인된 새 본문입니다."
    assert after.json()["revision_hash"] != old_hash
    after_list = await _get_public_list(pg_async_session, hospital)
    assert after_list.status_code == 200
    assert after_list.json()[0]["title"] == "승인된 새 제목"
    assert after_list.json()[0]["revision_hash"] == after.json()["revision_hash"]

    stale = await stage_pending_candidate_cas(
        pg_async_session,
        item,
        expected_active_revision_id=old_pointer,
        expected_content_revision=4,
        title="늦은 제목",
        body="늦은 본문",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        references_list=item.references_list,
        reference_checks=item.reference_checks,
        created_by="stale@example.com",
        created_at=datetime(2026, 10, 9, 9, 4, tzinfo=UTC),
    )
    assert isinstance(stale, CandidateStageConflict)
    assert item.pending_revision is None

    repeated = await publish_pending_candidate(
        pg_async_session,
        item,
        _pass(candidate),
        philosophy=philosophy,
        approved_by="system:ai-review",
        approved_at=datetime(2026, 10, 9, 9, 5, tzinfo=UTC),
    )
    assert isinstance(repeated, CandidateApprovalRejected)
    assert (
        await pg_async_session.scalar(
            select(func.count(ContentRevision.id)).where(ContentRevision.content_item_id == item.id)
        )
        == 2
    )

    item.status = ContentStatus.WITHHELD
    await pg_async_session.flush()
    withdrawn = await _get_public(pg_async_session, hospital, item)
    assert withdrawn.status_code == 404


async def test_changed_candidate_binds_real_retrieval_evidence_before_public_swap(
    pg_async_session, reference_http_server
):
    """Real HTTP bytes become immutable evidence before candidate PASS changes public output."""

    hospital, philosophy, item = await _seed(pg_async_session)
    cited_url = (
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
        "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=99124"
    )
    title = "치핵의 원인과 치료"
    body = "## 치핵의 원인과 치료\n" + document_body("치핵")
    async def fixture_fetcher(url: str) -> FetchResult:
        async with AsyncClient() as client:
            response = await client.get(reference_http_server)
        return FetchResult(
            url=url,
            status=response.status_code,
            final_url=url,
            html=response.text,
        )

    with override_reference_fetcher(fixture_fetcher):
        patch_response = await _patch_content(
            pg_async_session,
            hospital,
            item,
            {
                "title": title,
                "body": body,
                "meta_description": "치핵 진료 안내",
                "references": [{"title": "치핵", "url": cited_url}],
            },
        )
    assert patch_response.status_code == 200, patch_response.text
    await pg_async_session.refresh(item, attribute_names=["pending_revision"])
    candidate = parse_pending_candidate(item)
    assert candidate is not None
    verification_checks = candidate.model_dump(mode="json")["reference_checks"]
    cited_check = next(check for check in verification_checks if check["url"] == cited_url)
    assert cited_check["verdict"] == "pass"
    assert cited_check["content_fingerprint"], cited_check
    outcome = await publish_pending_candidate(
        pg_async_session,
        item,
        _pass(candidate),
        philosophy=philosophy,
        approved_by="system:ai-review",
        approved_at=datetime(2026, 10, 9, 10, 1, tzinfo=UTC),
    )

    assert isinstance(outcome, CandidateApprovalApplied)
    revision = await pg_async_session.get(ContentRevision, item.active_revision_id)
    assert revision.reference_checks == verification_checks
    response = await _get_public(pg_async_session, hospital, item)
    assert response.status_code == 200
    assert response.json()["body"] == body


async def test_unchanged_approved_revision_keeps_public_api_during_aged_source_outage(
    pg_async_session,
):
    hospital, _philosophy, item = await _seed(pg_async_session)
    url = item.active_revision.references_list[0]["url"]
    fetcher = PageFetcher({url: (503, url, "")})

    refresh = await refresh_publication_references(
        item, ReferenceVerifier(fetcher, domain_spacing=0)
    )
    response = await _get_public(pg_async_session, hospital, item)

    assert refresh.already_current
    assert fetcher.calls == []
    assert response.status_code == 200
    assert response.json()["body"] == "승인된 이전 본문입니다."


async def test_explicit_retract_hides_the_existing_active_revision(
    pg_async_session, monkeypatch
):
    from app.services import reference_verification

    hospital, _philosophy, item = await _seed(pg_async_session)
    url = item.active_revision.references_list[0]["url"]
    monkeypatch.setattr(
        reference_verification,
        "reference_exclusion_reason",
        lambda candidate: "source_retracted" if candidate == url else None,
    )

    response = await _get_public(pg_async_session, hospital, item)

    assert response.status_code == 404


async def test_malformed_candidate_never_replaces_the_active_revision(pg_async_session):
    hospital, _philosophy, item = await _seed(pg_async_session)
    old_pointer = item.active_revision_id
    item.pending_revision = {"body": "형식이 깨진 후보"}
    await pg_async_session.flush()

    assert parse_pending_candidate(item) is None
    response = await _get_public(pg_async_session, hospital, item)
    assert response.status_code == 200
    assert response.json()["body"] == "승인된 이전 본문입니다."
    assert item.active_revision_id == old_pointer


async def test_two_sessions_stale_candidate_cas_writes_nothing() -> None:
    engine = create_async_engine(_async_database_url(), future=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hospital_id: uuid.UUID | None = None
    try:
        async with sessions() as setup:
            hospital, _philosophy, seeded = await _seed(setup)
            hospital_id = hospital.id
            item_id = seeded.id
            await setup.commit()

        async with sessions() as stale_session:
            stale_item = (
                await stale_session.execute(
                    select(ContentItem)
                    .options(selectinload(ContentItem.active_revision))
                    .where(ContentItem.id == item_id)
                )
            ).scalar_one()
            stale_pointer = stale_item.active_revision_id
            stale_revision = stale_item.content_revision

            async with sessions() as winner_session:
                winner_item = (
                    await winner_session.execute(
                        select(ContentItem)
                        .options(selectinload(ContentItem.active_revision))
                        .where(ContentItem.id == item_id)
                    )
                ).scalar_one()
                philosophy = await winner_session.get(
                    HospitalContentPhilosophy, winner_item.content_philosophy_id
                )
                winner = await stage_pending_candidate_cas(
                    winner_session,
                    winner_item,
                    expected_active_revision_id=winner_item.active_revision_id,
                    expected_content_revision=winner_item.content_revision,
                    title="먼저 승인된 제목",
                    body="먼저 승인된 본문입니다.",
                    meta_description="먼저 승인된 설명",
                    faq_question=None,
                    faq_answer_summary=None,
                    references_list=winner_item.references_list,
                    reference_checks=_candidate_checks(
                        winner_item,
                        title="먼저 승인된 제목",
                        body="먼저 승인된 본문입니다.",
                    ),
                    created_by="winner@example.com",
                    created_at=datetime(2026, 10, 9, 10, 0, tzinfo=UTC),
                )
                assert not isinstance(winner, CandidateStageConflict)
                published = await publish_pending_candidate(
                    winner_session,
                    winner_item,
                    _pass(winner),
                    philosophy=philosophy,
                    approved_by="system:ai-review",
                    approved_at=datetime(2026, 10, 9, 10, 1, tzinfo=UTC),
                )
                assert isinstance(published, CandidateApprovalApplied)
                enqueue_public_surface_intent(
                    winner_session, hospital, content_ids=[winner_item.id]
                )
                await winner_session.commit()

            async with sessions() as before_stale:
                winner_hash = await before_stale.scalar(
                    select(ContentRevision.approval_hash).where(
                        ContentRevision.id
                        == select(ContentItem.active_revision_id)
                        .where(ContentItem.id == item_id)
                        .scalar_subquery()
                    )
                )
                intent_count = await before_stale.scalar(
                    select(func.count(OperationRun.id)).where(
                        OperationRun.hospital_id == hospital_id,
                        OperationRun.operation_type == "SITE_REVALIDATION",
                    )
                )

            stale = await stage_pending_candidate_cas(
                stale_session,
                stale_item,
                expected_active_revision_id=stale_pointer,
                expected_content_revision=stale_revision,
                title="늦게 도착한 제목",
                body="늦게 도착한 본문입니다.",
                meta_description="늦은 설명",
                faq_question=None,
                faq_answer_summary=None,
                references_list=stale_item.references_list,
                reference_checks=stale_item.reference_checks,
                created_by="stale@example.com",
                created_at=datetime(2026, 10, 9, 10, 2, tzinfo=UTC),
            )
            assert isinstance(stale, CandidateStageConflict)
            await stale_session.commit()

        async with sessions() as verify:
            current = await verify.get(ContentItem, item_id)
            assert current is not None
            assert current.pending_revision is None
            assert current.body == "먼저 승인된 본문입니다."
            assert current.active_revision_id != stale_pointer
            assert (
                await verify.scalar(
                    select(ContentRevision.approval_hash).where(
                        ContentRevision.id == current.active_revision_id
                    )
                )
                == winner_hash
            )
            assert (
                await verify.scalar(
                    select(func.count(OperationRun.id)).where(
                        OperationRun.hospital_id == hospital_id,
                        OperationRun.operation_type == "SITE_REVALIDATION",
                    )
                )
                == intent_count
            )
    finally:
        if hospital_id is not None:
            async with sessions() as cleanup:
                await cleanup.execute(text("SET LOCAL session_replication_role = replica"))
                await cleanup.execute(
                    delete(ContentRevision).where(ContentRevision.content_item_id == item_id)
                )
                await cleanup.execute(text("SET LOCAL session_replication_role = origin"))
                await cleanup.execute(delete(Hospital).where(Hospital.id == hospital_id))
                await cleanup.commit()
        await engine.dispose()
