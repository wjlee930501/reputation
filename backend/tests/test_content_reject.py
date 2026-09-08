"""반려 → 야간 재생성 경로 (발행일 당일 반려 시 재스케줄 + 발행 메타 초기화)."""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import arrow
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.admin import content as content_api
from app.core.database import get_db
from app.main import app
from app.models.content import ContentStatus
from app.models.hospital import HospitalStatus
from app.services.audit_log import reset_request_actor, set_request_actor

_REASON = "의료광고 금지 표현을 확인해 내립니다."


def _reason() -> content_api.RejectBody:
    return content_api.RejectBody(reason=_REASON)


@pytest.fixture
def verified_actor():
    """확인된 요청 actor. 비공개는 되돌릴 수 없으므로 이 값 없이는 거절된다 (H-09)."""
    token = set_request_actor("owner@example.com")
    try:
        yield "owner@example.com"
    finally:
        reset_request_actor(token)


class _FakeDB:
    def __init__(self, item, hospital):
        self._item = item
        self._hospital = hospital
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        if object_id == self._item.id:
            return self._item
        if object_id == self._hospital.id:
            return self._hospital
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _item(scheduled_date, status=ContentStatus.PUBLISHED, carried_over_from=None):
    hospital_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        status=status,
        title="기존 제목",
        body="기존 본문",
        image_url="gs://bucket/img.png",
        published_at=datetime.now(timezone.utc),
        published_by="AE",
        first_published_at=None,
        first_published_by=None,
        generated_at=datetime.now(timezone.utc),
        scheduled_date=scheduled_date,
        carried_over_from=carried_over_from,
    )


def _hospital(hospital_id):
    # ACTIVE가 아니므로 revalidate 경로(외부 호출)는 타지 않는다.
    return SimpleNamespace(
        id=hospital_id,
        status=HospitalStatus.BUILDING,
        site_live=False,
        slug="test-clinic",
    )


async def test_reject_on_publish_day_reschedules_to_tomorrow(verified_actor):
    today = arrow.now("Asia/Seoul").date()
    tomorrow = arrow.now("Asia/Seoul").shift(days=1).date()
    item = _item(scheduled_date=today)
    db = _FakeDB(item, _hospital(item.hospital_id))

    result = await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert "Rejected" in result["detail"]
    assert item.status == ContentStatus.REJECTED
    assert item.body is None and item.title is None and item.image_url is None
    # 발행 메타 초기화 — 재생성 후 재발행 시 이전 발행 기록이 남지 않는다.
    assert item.published_at is None and item.published_by is None and item.generated_at is None
    assert item.first_published_at is not None
    assert item.first_published_by == "AE"
    # 야간 생성은 scheduled_date == 내일 만 집으므로 당일 반려는 내일로 재스케줄.
    assert item.scheduled_date == tomorrow
    assert db.committed


async def test_reject_future_item_keeps_schedule(verified_actor):
    future = arrow.now("Asia/Seoul").shift(days=3).date()
    item = _item(scheduled_date=future, status=ContentStatus.READY)
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert item.status == ContentStatus.REJECTED
    assert item.scheduled_date == future  # 발행 전날 밤 야간 배치가 그대로 집는다
    assert item.carried_over_from is None


# ── 월말 반려 carry-over (전월 이월) ─────────────────────────────────


def _freeze_seoul_now(monkeypatch, iso_datetime: str):
    """content_api가 보는 arrow.now(...)를 고정한다."""
    fixed = arrow.get(iso_datetime)
    monkeypatch.setattr(content_api.arrow, "now", lambda *_a, **_kw: fixed)


async def test_reject_across_month_boundary_sets_carried_over_from(monkeypatch, verified_actor):
    """월 마지막 날 반려 → 내일(다음 달 1일) 재스케줄 + 원래 예정일 기록."""
    _freeze_seoul_now(monkeypatch, "2026-06-30T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 6, 30))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert item.scheduled_date == date(2026, 7, 1)
    assert item.carried_over_from == date(2026, 6, 30)


async def test_reject_same_month_does_not_set_carried_over_from(monkeypatch, verified_actor):
    """같은 달 안에서의 당일 반려 재스케줄은 이월이 아니다."""
    _freeze_seoul_now(monkeypatch, "2026-06-10T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 6, 10))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert item.scheduled_date == date(2026, 6, 11)
    assert item.carried_over_from is None


async def test_re_reject_does_not_overwrite_existing_carried_over_from(monkeypatch, verified_actor):
    """재반려가 또 월 경계를 넘어도 최초 이월 기준일을 유지한다."""
    _freeze_seoul_now(monkeypatch, "2026-07-31T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 7, 31), carried_over_from=date(2026, 6, 30))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert item.scheduled_date == date(2026, 8, 1)
    assert item.carried_over_from == date(2026, 6, 30)  # 최초 값 유지


# ── 되돌릴 수 없는 조작의 감사 기록 (H-09) ───────────────────────────


async def test_reject_records_verified_actor_and_reason(verified_actor):
    """누가 왜 내렸는지가 남아야 나중에 판단을 되짚을 수 있다."""
    item = _item(scheduled_date=arrow.now("Asia/Seoul").date())
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    entries = [row for row in db.added if getattr(row, "action", None) == "reject_content"]
    assert len(entries) == 1
    assert entries[0].actor == verified_actor
    assert entries[0].detail["reason"] == _REASON
    assert entries[0].detail["rejected_by"] == verified_actor


async def _reject_over_http(item, hospital, payload, actor: str | None):
    async def override_get_db():
        yield _HttpFakeDB(item, hospital, actor)

    app.dependency_overrides[get_db] = override_get_db
    headers = {"X-Admin-Key": "test-admin-key"}
    if actor is not None:
        headers["X-Admin-Actor"] = actor
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                f"/api/v1/admin/hospitals/{item.hospital_id}/content/{item.id}/reject",
                headers=headers,
                json=payload,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


class _HttpFakeDB(_FakeDB):
    """라우트 경로용 — actor 확인 조회(`_resolve_admin_actor`)까지 답한다."""

    def __init__(self, item, hospital, actor: str | None):
        super().__init__(item, hospital)
        self._actor = actor

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._actor)


async def test_reject_route_rejects_an_unverified_actor():
    item = _item(scheduled_date=arrow.now("Asia/Seoul").date())
    response = await _reject_over_http(
        item, _hospital(item.hospital_id), {"reason": _REASON}, actor=None
    )

    assert response.status_code == 403
    assert item.status == ContentStatus.PUBLISHED  # 공개 글은 그대로 남는다


async def test_reject_route_requires_a_reason():
    item = _item(scheduled_date=arrow.now("Asia/Seoul").date())
    response = await _reject_over_http(
        item, _hospital(item.hospital_id), {"reason": "짧"}, actor="owner@example.com"
    )

    assert response.status_code == 422
    assert item.status == ContentStatus.PUBLISHED


async def test_reject_route_accepts_a_verified_actor_with_a_reason():
    item = _item(scheduled_date=arrow.now("Asia/Seoul").date())
    response = await _reject_over_http(
        item, _hospital(item.hospital_id), {"reason": _REASON}, actor="owner@example.com"
    )

    assert response.status_code == 200
    assert item.status == ContentStatus.REJECTED
