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


@pytest.fixture(autouse=True)
def isolated_rate_limit(monkeypatch):
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter
    monkeypatch.setattr(app.state.limiter, "_limiter", FixedWindowRateLimiter(MemoryStorage()))


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


def _item(
    scheduled_date,
    status=ContentStatus.PUBLISHED,
    carried_over_from=None,
    essence_check_summary=None,
):
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
        essence_check_summary=essence_check_summary,
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


# ── 반려가 지운 본문이 실제로 다시 쓰이는가 (2026-09-20 due5 서울W) ──────────────


async def test_reject_releases_the_suppression_that_described_the_deleted_body(
    monkeypatch, verified_actor
):
    """반려는 "오늘 밤 다시 씁니다"라고 약속한다 — 로더가 걸러 버리면 거짓말이다.

    본문이 없는 행의 claim 자격은 `_generation_retry_is_eligible`이 정한다. 그 술어는
    저장된 차단 사유·맥락이 그대로이고 재시도 기한이 오지 않았으면 claim 전에 행을
    걸러 낸다. 시도 지문에는 예정일이 들어가지 않으므로(H-08) 반려의 재스케줄도 그
    억제를 풀지 못한다. 종착으로 굳은 사유를 그대로 두면 반려로 비워진 본문이 영영
    다시 쓰이지 않는다 — 운영에서 본 "본문만 사라지고 재생성은 오지 않는" 상태다.
    """
    from app.workers import tasks

    philosophy = SimpleNamespace(id="p1", director_delta_ids=[])
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    unchanged_context = tasks._generation_attempt_context(
        SimpleNamespace(content_type=SimpleNamespace(value="FAQ"), query_target_id=None),
        philosophy,
    )
    terminal_attempt = {
        "context": unchanged_context,
        "reason": "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "retry_class": "OPERATOR_REQUIRED",
        "next_retry_at": None,
        "attempt_period": "2026-09-18",
        "attempt_count": 4,
        "provider_attempt_count": 4,
        "exhausted_days": 3,
        "first_observed_at": "2026-09-18T00:00:00+00:00",
    }
    item = _item(
        scheduled_date=arrow.now("Asia/Seoul").shift(days=3).date(),
        status=ContentStatus.READY,
        essence_check_summary={"generation_attempt": dict(terminal_attempt)},
    )
    item.content_type = SimpleNamespace(value="FAQ")
    item.query_target_id = None
    db = _FakeDB(item, _hospital(item.hospital_id))
    # 종전 반려가 남기던 모양: 본문만 비고 시도 기록은 그대로. 로더가 claim 전에 거른다.
    stale = SimpleNamespace(
        **{**vars(item), "body": None, "title": None, "image_url": None}
    )
    assert tasks._generation_retry_is_eligible(db)(stale) is False

    await content_api.reject_content(item.hospital_id, item.id, _reason(), db=db)

    assert item.body is None
    assert tasks._generation_retry_is_eligible(db)(item) is True
    # 억제만 풀고 예산 사다리는 남긴다 — 반려 한 번이 하루 예산과 소진 일수를 0에서
    # 다시 시작하게 하면 3일 소진도, 그 소진이 여는 주제 교체도 영영 오지 않는다.
    carried = item.essence_check_summary["generation_attempt"]
    assert carried["exhausted_days"] == 3
    assert carried["provider_attempt_count"] == 4
    assert carried["first_observed_at"] == "2026-09-18T00:00:00+00:00"
    assert "reason" not in carried and "next_retry_at" not in carried


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
        from app.core.config import settings
        from tests.test_admin_actor_verification import _assertion_payload, _sign_assertion
        headers["X-Admin-Actor-Assertion"] = _sign_assertion(
            settings.BFF_ACTOR_SECRET, _assertion_payload(email=actor)
        )
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
