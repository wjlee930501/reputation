"""비공개(보존) 전환·되돌리기 — 실제 Postgres와 실제 라우트 함수로 검증한다.

withhold는 공개 글을 본문·참고자료·이미지·발행 이력을 그대로 둔 채 공개 표면에서만
내린다. 공개 목록·상세·이미지·sitemap(목록 API를 limit=500으로 순회)은 모두
`status == PUBLISHED`로 고르므로 상태 전환만으로 빠져야 하고, restore는 같은 발행
시각으로 되돌리되 공개 페이지가 실제로 내보낼 수 없는 글이면 409로 거절해야 한다.
"""

import hashlib
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import content as content_api
from app.api.admin import operations as operations_api
from app.api.public import site as site_api
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.schemas.content import ContentBriefUpdate
from app.services import indexnow, site_revalidate
from app.services import site_revalidation_control as revalidation_control
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, compute_sources_snapshot_hash
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.services.reference_verification import (
    item_topic_fingerprint,
    override_reference_fetcher,
    reference_check_record,
)
from tests.reference_fetch_doubles import PageFetcher

pytestmark = pytest.mark.asyncio

list_published_contents = site_api.list_published_contents.__wrapped__
get_content_public = site_api.get_content_public.__wrapped__
get_public_content_image = site_api.get_public_content_image.__wrapped__

_ACTOR = "owner@example.com"
_PUBLISHED_AT = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
_FIRST_PUBLISHED_AT = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
_REFERENCES = [
    {
        "title": "질병관리청 국가건강정보포털",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo.do",
    }
]


@pytest.fixture
def verified_actor():
    token = set_request_actor(_ACTOR)
    try:
        yield _ACTOR
    finally:
        reset_request_actor(token)


@pytest.fixture
def revalidations(monkeypatch):
    calls: list[tuple] = []

    async def fake_revalidate(slug, content_id, **kwargs):
        calls.append((slug, content_id, kwargs))
        return True

    monkeypatch.setattr(content_api, "trigger_content_site_revalidate_safe", fake_revalidate)
    return calls


@pytest.fixture
def failing_site_revalidation(pg_async_session, monkeypatch):
    """실제 재시도 제어 경로 — 사이트 갱신은 매번 실패하고 재시도 디스패치만 기록한다."""
    dispatched: list[str] = []

    async def site_down(**_kwargs):
        raise RuntimeError("site revalidate unavailable")

    def control_sessions():
        # 테스트 트랜잭션과 같은 연결 — 라우트가 커밋한 행을 보고, 끝나면 함께 롤백된다.
        return AsyncSession(
            bind=pg_async_session.bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    def send_task(_name, *, args, **_kwargs):
        dispatched.append(args[0])

    monkeypatch.setattr(site_revalidate, "trigger_site_revalidate", site_down)
    monkeypatch.setattr(revalidation_control, "get_async_sessionmaker", lambda: control_sessions)
    monkeypatch.setattr(celery_app, "send_task", send_task)
    return dispatched


async def _seed(session, *, status=ContentStatus.PUBLISHED) -> tuple[Hospital, ContentItem]:
    """공개 게이트를 모두 통과하는 병원 1곳과 공개 글 1편."""
    suffix = uuid.uuid4().hex[:8]
    hospital = Hospital(
        id=uuid.uuid4(),
        name="보존전환병원",
        slug=f"withhold-{suffix}",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        treatments=[],
    )
    session.add(hospital)
    await session.flush()
    processed_at = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="홈페이지",
        raw_text="근거 자료 본문",
        content_hash=f"hash-{suffix}",
        status=SourceStatus.PROCESSED,
        processed_at=processed_at,
    )
    session.add(source)
    await session.flush()
    philosophy = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="근거 중심으로 충분히 설명합니다.",
        patient_promise="확인된 정보만 환자에게 안내합니다.",
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        approved_at=processed_at,
    )
    schedule = ContentSchedule(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[1, 3],
        active_from=date(2026, 9, 1),
    )
    session.add_all([philosophy, schedule])
    await session.flush()

    title = "위내시경 전 확인할 점"
    content_type = ContentType.FAQ
    image_hash = hashlib.sha256(f"{suffix}-image".encode()).hexdigest()
    image_url = f"gs://reputation-images/content/{image_hash}-fixture.png"
    published = status == ContentStatus.PUBLISHED
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=content_type,
        sequence_no=2,
        total_count=12,
        title=title,
        body="검사 전날 식사와 복용약을 의료진과 확인해 주세요.",
        references_list=list(_REFERENCES),
        faq_question="위내시경 전에 무엇을 확인해야 하나요?",
        faq_answer_summary="식사 시간과 복용 중인 약을 의료진과 확인하세요.",
        image_url=image_url,
        image_policy_verified_at=processed_at,
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash(content_type, title),
        image_policy_version=IMAGE_POLICY_VERSION,
        scheduled_date=date(2026, 9, 10),
        status=status,
        published_at=_PUBLISHED_AT if published else None,
        published_by=_ACTOR if published else None,
        first_published_at=_FIRST_PUBLISHED_AT if published else None,
        first_published_by="auto" if published else None,
        content_revision=3,
        essence_status=ESSENCE_STATUS_ALIGNED,
        content_philosophy_id=philosophy.id,
    )
    # 공개됐던 글이 받은 실제 문서 확인 기록(같은 URL·같은 글 주제·신선함). restore의 참고자료
    # 게이트는 이 기록이 없거나 낡았으면 다시 확인하고, 통과하지 못하면 거절한다.
    verified_at = datetime.now(timezone.utc)
    item.reference_checks = [
        reference_check_record(
            reference["url"],
            verdict="pass",
            reason="page_verified",
            checked_at=verified_at,
            curated=False,
            status=200,
            final_url=reference["url"],
            page_title="위내시경 | 국가건강정보포털 | 질병관리청",
            text_len=900,
            verified_at=verified_at,
            topic_fingerprint=item_topic_fingerprint(item),
        )
        for reference in _REFERENCES
    ]
    session.add(item)
    await session.flush()
    return hospital, item


async def _status_of(coro) -> int:
    try:
        await coro
    except HTTPException as exc:
        return exc.status_code
    return 200


async def _http_error(coro) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await coro
    return caught.value


async def _public_ids(session, hospital) -> set[str]:
    # sitemap·llms 빌더가 쓰는 호출 모양(limit=500) 그대로다.
    items = await list_published_contents(
        None, hospital.slug, limit=500, offset=0, db=session
    )
    return {entry["id"] for entry in items}


async def _audit(session, item, action) -> list[AdminAuditLog]:
    rows = await session.execute(
        select(AdminAuditLog).where(
            AdminAuditLog.target_id == str(item.id), AdminAuditLog.action == action
        )
    )
    return list(rows.scalars().all())


async def _withhold(session, hospital, item, reason="원장 요청으로 잠시 내립니다."):
    return await content_api.withhold_content(
        hospital.id, item.id, content_api.WithholdBody(reason=reason), db=session
    )


async def _restore(session, hospital, item, reason="원장 확인 뒤 다시 공개합니다."):
    return await content_api.restore_content(
        hospital.id, item.id, content_api.RestoreBody(reason=reason), db=session
    )


# ── 1·4. withhold: 보존 필드, 판, 감사, 공개 표면 제외 ─────────────────────


async def test_withhold_preserves_the_edition_and_leaves_every_public_surface(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    image_before = (
        item.image_url,
        item.image_content_hash,
        item.image_subject_hash,
        item.image_policy_version,
        item.image_policy_verified_at,
    )
    # 전제: 공개 목록·상세·이미지에 보인다.
    assert str(item.id) in await _public_ids(session, hospital)
    assert await _status_of(get_content_public(None, hospital.slug, item.id, db=session)) == 200
    assert (
        await _status_of(get_public_content_image(None, hospital.slug, item.id, db=session))
        != 404
    )

    result = await _withhold(session, hospital, item)

    assert result == {
        "detail": "Withheld",
        "status": "WITHHELD",
        "published_at": _PUBLISHED_AT.isoformat(),
        "content_revision": 4,
    }
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.title == "위내시경 전 확인할 점"
    assert item.body == "검사 전날 식사와 복용약을 의료진과 확인해 주세요."
    assert item.references_list == _REFERENCES
    assert (
        item.image_url,
        item.image_content_hash,
        item.image_subject_hash,
        item.image_policy_version,
        item.image_policy_verified_at,
    ) == image_before
    assert item.published_at == _PUBLISHED_AT
    assert item.published_by == _ACTOR
    assert item.first_published_at == _FIRST_PUBLISHED_AT
    assert item.first_published_by == "auto"
    assert item.content_revision == 4

    [audit] = await _audit(session, item, "withhold_content")
    assert audit.actor == _ACTOR
    assert audit.hospital_id == hospital.id
    assert audit.detail["reason"] == "원장 요청으로 잠시 내립니다."
    assert audit.detail["published_at"] == _PUBLISHED_AT.isoformat()
    assert audit.detail["revision"] == 4
    assert audit.detail["withheld_by"] == _ACTOR
    # 캐시에 남은 판을 내린다.
    assert revalidations == [
        (hospital.slug, item.id, {
            "hospital_name": hospital.name,
            "treatments": hospital.treatments,
            "unpublished_from": _PUBLISHED_AT,
            "edition_revision": 4,
        })
    ]

    assert str(item.id) not in await _public_ids(session, hospital)
    assert await _status_of(get_content_public(None, hospital.slug, item.id, db=session)) == 404
    assert (
        await _status_of(get_public_content_image(None, hospital.slug, item.id, db=session))
        == 404
    )


async def test_withhold_requires_a_verified_actor(pg_async_session, revalidations):
    session = pg_async_session
    hospital, item = await _seed(session)

    error = await _http_error(_withhold(session, hospital, item))

    assert error.status_code == 403
    await session.refresh(item)
    assert item.status == ContentStatus.PUBLISHED
    assert await _audit(session, item, "withhold_content") == []


@pytest.mark.parametrize(
    "status", [ContentStatus.DRAFT, ContentStatus.READY, ContentStatus.REJECTED,
               ContentStatus.CANCELLED, ContentStatus.WITHHELD]
)
async def test_withhold_only_accepts_published_content(
    pg_async_session, verified_actor, revalidations, status
):
    session = pg_async_session
    hospital, item = await _seed(session, status=status)

    error = await _http_error(_withhold(session, hospital, item))

    assert error.status_code == 409
    await session.refresh(item)
    assert item.status == status
    assert item.content_revision == 3


# ── 2. restore: 같은 발행 시각으로 다시 보인다 / 막히면 409 ────────────────


async def test_restore_republishes_with_the_same_published_at(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)

    result = await _restore(session, hospital, item)

    assert result["detail"] == "Restored"
    assert result["published_at"] == _PUBLISHED_AT.isoformat()
    assert result["content_revision"] == 5
    await session.refresh(item)
    assert item.status == ContentStatus.PUBLISHED
    assert item.published_at == _PUBLISHED_AT
    assert item.published_by == _ACTOR
    assert item.first_published_at == _FIRST_PUBLISHED_AT
    assert str(item.id) in await _public_ids(session, hospital)
    assert await _status_of(get_content_public(None, hospital.slug, item.id, db=session)) == 200

    [audit] = await _audit(session, item, "restore_content")
    assert audit.actor == _ACTOR
    assert audit.detail["reason"] == "원장 확인 뒤 다시 공개합니다."
    assert audit.detail["published_at"] == _PUBLISHED_AT.isoformat()
    assert audit.detail["revision"] == 5
    assert revalidations[-1][2].get("unpublished_from") is None
    assert revalidations[-1][2]["edition_revision"] == 5


async def test_each_withhold_and_restore_opens_its_own_cache_refresh_retry(
    pg_async_session, verified_actor, failing_site_revalidation
):
    """withhold·restore는 published_at을 보존한다 — 재시도 키가 발행 시각만 보면
    withhold→restore→withhold의 두 번째 withhold가 이미 성공으로 닫힌 첫 run에 흡수돼
    재시도 없이 사라지고, 내린 글이 캐시에 남는다. restore 뒤 restore도 같다."""
    session = pg_async_session
    hospital, item = await _seed(session)

    # 공개 표면 intent(public-surface:*)도 같은 operation_type이다 — 재시도 run만 본다.
    retry_runs = (
        OperationRun.hospital_id == hospital.id,
        OperationRun.operation_type == "SITE_REVALIDATION",
        OperationRun.idempotency_key.startswith(f"site-revalidation:{item.id}:"),
    )
    for step in (_withhold, _restore, _withhold, _restore):
        await step(session, hospital, item)
        # 앞 전환의 재시도는 성공으로 닫혔다 — 다음 전환이 거기에 흡수되면 안 된다.
        await session.execute(
            update(OperationRun)
            .where(*retry_runs)
            .values(state=OperationRunState.SUCCEEDED.value)
        )
        await session.commit()

    runs = (await session.execute(select(OperationRun).where(*retry_runs))).scalars().all()
    edition = _PUBLISHED_AT.isoformat()
    assert {run.idempotency_key: run.request_payload["direction"] for run in runs} == {
        f"site-revalidation:{item.id}:unpublish:{edition}:rev4": "UNPUBLISH",
        f"site-revalidation:{item.id}:{edition}:rev5": "PUBLISH",
        f"site-revalidation:{item.id}:unpublish:{edition}:rev6": "UNPUBLISH",
        f"site-revalidation:{item.id}:{edition}:rev7": "PUBLISH",
    }
    assert sorted(failing_site_revalidation) == sorted(str(run.id) for run in runs)


async def test_restore_is_refused_when_references_were_emptied(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    # WITHHELD 글은 참고자료를 0개로도 고칠 수 있다(공개 글은 400).
    await content_api.update_content(
        hospital.id, item.id, content_api.ContentPatch(references=[]), db=session
    )
    await session.refresh(item)
    assert item.references_list == []

    error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    assert error.detail["code"] == "RESTORE_BLOCKED"
    assert "MISSING_REFERENCES" in error.detail["blockers"]
    assert "STATUS_NOT_PUBLISHED" not in error.detail["blockers"]
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert await _audit(session, item, "restore_content") == []


async def test_restore_is_refused_after_an_authority_change(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    item.essence_check_summary = {
        "authority_change": [{"source_ids": [str(uuid.uuid4())], "reason": "근거 철회"}]
    }
    await session.flush()

    error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    assert error.detail["blockers"] == ["CONTENT_AUTHORITY_CHANGED"]
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD


@pytest.mark.parametrize(
    "change",
    [
        {"status": HospitalStatus.PAUSED},
        {"site_live": False},
        {"schedule_set": False},
    ],
)
async def test_restore_is_refused_for_a_hospital_without_a_public_site(
    pg_async_session, verified_actor, revalidations, change
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    for key, value in change.items():
        setattr(hospital, key, value)
    await session.flush()

    error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    assert error.detail["code"] == "HOSPITAL_NOT_PUBLIC"
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD


@pytest.mark.parametrize(
    "status", [ContentStatus.PUBLISHED, ContentStatus.DRAFT, ContentStatus.REJECTED]
)
async def test_restore_only_accepts_withheld_content(
    pg_async_session, verified_actor, revalidations, status
):
    session = pg_async_session
    hospital, item = await _seed(session, status=status)

    error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    await session.refresh(item)
    assert item.status == status
    assert item.content_revision == 3


async def test_restore_requires_a_verified_actor(pg_async_session, revalidations):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.WITHHELD)
    item.published_at = _PUBLISHED_AT
    await session.flush()

    error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 403
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD


# ── 5. WITHHELD 글: reschedule·cancel·publish 409, 참고자료 PATCH 허용 ──────


async def test_withheld_content_cannot_be_rescheduled(pg_async_session, verified_actor):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.WITHHELD)

    error = await _http_error(
        content_api.reschedule_content(
            hospital.id,
            item.id,
            content_api.ContentRescheduleBody(scheduled_date=date(2099, 1, 1)),
            db=session,
        )
    )

    assert error.status_code == 409
    await session.refresh(item)
    assert item.scheduled_date == date(2026, 9, 10)


async def test_withheld_content_cannot_be_cancelled(pg_async_session, verified_actor):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.WITHHELD)

    error = await _http_error(content_api.cancel_content(hospital.id, item.id, db=session))

    assert error.status_code == 409
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD


async def test_withheld_content_cannot_be_published_and_points_at_restore(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)

    error = await _http_error(
        content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=session)
    )

    assert error.status_code == 409
    assert error.detail["code"] == "CONTENT_WITHHELD"
    assert "restore" in error.detail["message"]
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.published_at == _PUBLISHED_AT


async def test_withheld_content_references_can_be_edited(
    pg_async_session, verified_actor, revalidations, monkeypatch
):
    session = pg_async_session
    hospital, item = await _seed(session)
    reviewed_at = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
    item.post_publish_reviewed_at = reviewed_at
    item.post_publish_reviewed_by = _ACTOR
    await session.commit()
    await _withhold(session, hospital, item)
    await session.refresh(item)
    revision_before = item.content_revision
    image_before = (item.image_content_hash, item.image_subject_hash, item.image_policy_version)
    revalidations.clear()
    indexnow_calls: list[dict] = []

    async def record_indexnow(_db, **kwargs):
        indexnow_calls.append(kwargs)

    monkeypatch.setattr(indexnow, "enqueue_content_published", record_indexnow)
    replacement = {
        "title": "국가건강정보포털 위내시경",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfoView.do",
    }
    # PATCH는 사람이 고른 주소도 실제로 열어 이 글의 주제인지 확인한다(네트워크 없이 가짜
    # fetcher가 그 주소의 위내시경 문서를 돌려준다).
    fetcher = PageFetcher()
    fetcher.add_document(
        replacement["url"], "위내시경 | 국가건강정보포털 | 질병관리청", topic="위내시경"
    )

    with override_reference_fetcher(fetcher):
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(references=[replacement]),
            db=session,
        )
    await session.refresh(item)
    assert fetcher.calls == [replacement["url"]]
    assert item.status == ContentStatus.WITHHELD
    assert [ref["url"] for ref in item.references_list] == [replacement["url"]]
    # 공개 글의 참고자료 편집과 같은 기록 — 옛 확인 기록은 새 판을 보증하지 않는다.
    assert item.post_publish_reviewed_at is None
    assert item.post_publish_reviewed_by is None
    assert item.body_updated_at is not None
    assert item.human_edited_at is not None
    assert item.content_revision == revision_before + 1
    assert (
        item.image_content_hash, item.image_subject_hash, item.image_policy_version
    ) == image_before
    assert item.published_at == _PUBLISHED_AT
    checks = {check["url"]: check for check in item.reference_checks}
    assert checks[replacement["url"]]["verdict"] == "pass"
    assert checks[replacement["url"]]["topic_fingerprint"] == item_topic_fingerprint(item)
    # 공개 중이 아니므로 공개 표면 갱신·색인 제출은 없다(restore가 한다).
    assert revalidations == []
    assert indexnow_calls == []

    # 공개 글과 달리 0개로도 고칠 수 있다 — restore가 MISSING_REFERENCES로 막는다.
    await content_api.update_content(
        hospital.id, item.id, content_api.ContentPatch(references=[]), db=session
    )
    await session.refresh(item)
    assert item.references_list == []
    assert item.status == ContentStatus.WITHHELD


@pytest.mark.parametrize(
    "patch",
    [
        {"title": "제목을 바꾼 위내시경 안내"},
        {"body": "본문을 고친 판입니다."},
        {"meta_description": "설명을 고친 판입니다."},
        {"title": "제목을 바꾼 위내시경 안내", "references": []},
    ],
    ids=["title", "body", "meta", "title-with-references"],
)
async def test_withheld_content_refuses_edits_other_than_references(
    pg_async_session, verified_actor, revalidations, patch
):
    """제목 편집은 이미지 인증을 풀고, WITHHELD 글은 재인증 경로가 모두 비켜 가므로
    restore가 영구히 막힌다. 참고자료 외 필드가 하나라도 오면 아무것도 바꾸지 않고 409."""
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    await session.refresh(item)
    columns = (
        "title", "body", "meta_description", "references_list", "content_revision",
        "body_updated_at", "human_edited_at", "image_content_hash", "image_subject_hash",
        "image_policy_version", "image_policy_verified_at",
    )
    before = {column: getattr(item, column) for column in columns}

    error = await _http_error(
        content_api.update_content(
            hospital.id, item.id, content_api.ContentPatch(**patch), db=session
        )
    )

    assert error.status_code == 409
    assert "참고자료만" in error.detail
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert {column: getattr(item, column) for column in columns} == before


async def test_withheld_content_brief_patch_is_refused_so_philosophy_id_cannot_dodge_restore(
    pg_async_session, verified_actor, revalidations
):
    """가이드 승인(brief_status=APPROVED)은 content_philosophy_id를 현재 승인 기준으로
    바꾼다. WITHHELD 글에 이를 허용하면 옛 기준에 묶인 판이 restore 직전에
    PHILOSOPHY_MISMATCH를 지우고 그대로 다시 공개된다. brief PATCH는 무엇도 바꾸지 않고 409."""
    session = pg_async_session
    hospital, item = await _seed(session)
    # 옛 기준 — 지금은 보관된 판이다. 현재 승인 기준은 _seed의 v1이다.
    stale = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=2,
        status=PhilosophyStatus.ARCHIVED,
        positioning_statement="예전 기준입니다.",
        patient_promise="예전 약속입니다.",
    )
    session.add(stale)
    await session.flush()
    item.content_philosophy_id = stale.id
    # 가드가 없으면 승인이 실제로 통과하도록 쓸 수 있는 가이드를 둔다.
    item.content_brief = {"target_query": "위내시경 전 확인할 점"}
    await session.flush()
    await _withhold(session, hospital, item)
    await session.refresh(item)
    revision_before = item.content_revision

    error = await _http_error(
        content_api.update_content_brief(
            hospital.id,
            item.id,
            ContentBriefUpdate(brief_status="APPROVED", brief_approved_by=_ACTOR),
            db=session,
        )
    )

    assert error.status_code == 409
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.content_philosophy_id == stale.id
    assert item.content_revision == revision_before
    assert item.brief_approved_at is None

    # 우회가 일어나지 않았다 — restore는 여전히 기준 불일치로 막힌다.
    restore_error = await _http_error(_restore(session, hospital, item))
    assert restore_error.status_code == 409
    assert restore_error.detail["code"] == "RESTORE_BLOCKED"
    assert "PHILOSOPHY_MISMATCH" in restore_error.detail["blockers"]
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.content_philosophy_id == stale.id


async def test_withheld_content_can_still_be_rejected(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)

    await content_api.reject_content(
        hospital.id, item.id, content_api.RejectBody(reason="근거가 바뀌어 새로 씁니다."), db=session
    )

    await session.refresh(item)
    assert item.status == ContentStatus.REJECTED
    assert item.first_published_at == _FIRST_PUBLISHED_AT


# ── 1. 재생성·이미지 재생성 API 409 ────────────────────────────────────


async def test_withheld_content_cannot_be_regenerated_through_the_admin_api(
    pg_async_session, monkeypatch
):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.WITHHELD)

    async def forbidden_enqueue(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError("WITHHELD 글에 재생성 작업을 넣으면 안 된다")

    monkeypatch.setattr(operations_api, "_enqueue_with_truthful_audit", forbidden_enqueue)

    text_error = await _http_error(
        operations_api.regenerate_content_operation(hospital.id, item.id, db=session)
    )
    image_error = await _http_error(
        operations_api.regenerate_content_image_operation(hospital.id, item.id, db=session)
    )

    assert text_error.status_code == 409
    assert image_error.status_code == 409


# ── 표시: Admin이 '자동 발행 대기'로 보이지 않는다 ─────────────────────────


async def test_withheld_content_is_displayed_as_withheld_not_pending_publication(
    pg_async_session,
):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.WITHHELD)
    item.published_at = _PUBLISHED_AT
    await session.flush()

    serialized = await content_api._serialize_single(session, hospital.id, item)

    assert serialized["status"] == "WITHHELD"
    assert serialized["display"]["status_label"] == "비공개(보존)"
    assert serialized["display"]["review"]["label"] == "비공개(보존)"
    assert serialized["display"]["review"]["publishable"] is False
    assert serialized["compliance"]["publishable"] is False
    assert serialized["row_state"]["kind"] == "withheld"
    assert serialized["row_state"]["reason"] == "비공개(보존)"
    assert serialized["published_at"] == _PUBLISHED_AT.isoformat()


# ── 6. 자동 발행 보류 중에도 수동 발행은 된다 ───────────────────────────────


@pytest.mark.parametrize("hold_value", ["*", "{hospital}"])
async def test_manual_publish_is_allowed_while_auto_publish_is_held(
    pg_async_session, verified_actor, revalidations, monkeypatch, hold_value
):
    session = pg_async_session
    hospital, item = await _seed(session, status=ContentStatus.DRAFT)
    monkeypatch.setattr(
        settings, "AUTO_PUBLISH_HOLD_HOSPITALS", hold_value.format(hospital=hospital.id)
    )

    result = await content_api.publish_content(
        hospital.id, item.id, content_api.PublishBody(), db=session
    )

    assert result["detail"] == "Published"
    await session.refresh(item)
    assert item.status == ContentStatus.PUBLISHED
    assert item.published_by == _ACTOR


# ── 참고자료 게이트: restore는 검증만 하고, 통과하지 못하면 거절한다 ──────────────


async def _stale_reference_checks(session, item) -> None:
    """공개 당시 기록이 없는 레거시 공개 글(0082 이전) 모양."""
    item.reference_checks = None
    await session.commit()


async def test_restore_is_refused_when_a_legacy_reference_fails_a_real_get(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    await _stale_reference_checks(session, item)
    await session.refresh(item)
    before = {
        "references_list": item.references_list,
        "content_revision": item.content_revision,
        "status": item.status,
    }
    url = _REFERENCES[0]["url"]
    fetcher = PageFetcher({url: (404, url, "")})

    with override_reference_fetcher(fetcher):
        error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    assert error.detail["code"] == "REFERENCES_NOT_VERIFIED"
    assert url in error.detail["message"]
    assert "PATCH" in error.detail["message"]
    assert fetcher.calls == [url]
    await session.refresh(item)
    # 공개됐던 글의 참고자료는 빼지도·채우지도·바꾸지도 않는다. 검증 기록만 남는다.
    assert {
        "references_list": item.references_list,
        "content_revision": item.content_revision,
        "status": item.status,
    } == before
    assert item.status == ContentStatus.WITHHELD
    assert item.reference_checks[0]["reason"] == "dead_link"
    assert await _audit(session, item, "restore_content") == []
    assert str(item.id) not in await _public_ids(session, hospital)


async def test_restore_is_refused_while_the_institution_site_is_down(
    pg_async_session, verified_actor, revalidations
):
    import httpx

    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    await _stale_reference_checks(session, item)
    url = _REFERENCES[0]["url"]

    with override_reference_fetcher(PageFetcher({url: httpx.ConnectError("down")})):
        error = await _http_error(_restore(session, hospital, item))

    assert error.status_code == 409
    assert "기관 사이트에 접속하지 못함" in error.detail["message"]
    await session.refresh(item)
    assert item.status == ContentStatus.WITHHELD
    assert item.references_list == _REFERENCES


async def test_restore_reverifies_a_legacy_reference_and_republishes_when_it_passes(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    await _stale_reference_checks(session, item)
    url = _REFERENCES[0]["url"]
    fetcher = PageFetcher()
    fetcher.add_document(url, "위내시경 | 국가건강정보포털 | 질병관리청", topic="위내시경")

    with override_reference_fetcher(fetcher):
        result = await _restore(session, hospital, item)

    assert result["detail"] == "Restored"
    assert fetcher.calls == [url]
    await session.refresh(item)
    assert item.status == ContentStatus.PUBLISHED
    assert item.references_list == _REFERENCES
    assert item.reference_checks[0]["verdict"] == "pass"
    assert item.reference_checks[0]["topic_fingerprint"] == item_topic_fingerprint(item)


async def test_restore_uses_a_fresh_same_topic_pass_without_a_get(
    pg_async_session, verified_actor, revalidations
):
    session = pg_async_session
    hospital, item = await _seed(session)
    await _withhold(session, hospital, item)
    fetcher = PageFetcher()

    with override_reference_fetcher(fetcher):
        result = await _restore(session, hospital, item)

    assert result["detail"] == "Restored"
    assert fetcher.calls == []
