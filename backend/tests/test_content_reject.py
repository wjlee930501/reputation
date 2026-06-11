"""반려 → 야간 재생성 경로 (발행일 당일 반려 시 재스케줄 + 발행 메타 초기화)."""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import arrow

from app.api.admin import content as content_api
from app.models.content import ContentStatus
from app.models.hospital import HospitalStatus


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


async def test_reject_on_publish_day_reschedules_to_tomorrow():
    today = arrow.now("Asia/Seoul").date()
    tomorrow = arrow.now("Asia/Seoul").shift(days=1).date()
    item = _item(scheduled_date=today)
    db = _FakeDB(item, _hospital(item.hospital_id))

    result = await content_api.reject_content(item.hospital_id, item.id, db=db)

    assert "Rejected" in result["detail"]
    assert item.status == ContentStatus.REJECTED
    assert item.body is None and item.title is None and item.image_url is None
    # 발행 메타 초기화 — 재생성 후 재발행 시 이전 발행 기록이 남지 않는다.
    assert item.published_at is None and item.published_by is None and item.generated_at is None
    # 야간 생성은 scheduled_date == 내일 만 집으므로 당일 반려는 내일로 재스케줄.
    assert item.scheduled_date == tomorrow
    assert db.committed


async def test_reject_future_item_keeps_schedule():
    future = arrow.now("Asia/Seoul").shift(days=3).date()
    item = _item(scheduled_date=future, status=ContentStatus.READY)
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, db=db)

    assert item.status == ContentStatus.REJECTED
    assert item.scheduled_date == future  # 발행 전날 밤 야간 배치가 그대로 집는다
    assert item.carried_over_from is None


# ── 월말 반려 carry-over (전월 이월) ─────────────────────────────────


def _freeze_seoul_now(monkeypatch, iso_datetime: str):
    """content_api가 보는 arrow.now(...)를 고정한다."""
    fixed = arrow.get(iso_datetime)
    monkeypatch.setattr(content_api.arrow, "now", lambda *_a, **_kw: fixed)


async def test_reject_across_month_boundary_sets_carried_over_from(monkeypatch):
    """월 마지막 날 반려 → 내일(다음 달 1일) 재스케줄 + 원래 예정일 기록."""
    _freeze_seoul_now(monkeypatch, "2026-06-30T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 6, 30))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, db=db)

    assert item.scheduled_date == date(2026, 7, 1)
    assert item.carried_over_from == date(2026, 6, 30)


async def test_reject_same_month_does_not_set_carried_over_from(monkeypatch):
    """같은 달 안에서의 당일 반려 재스케줄은 이월이 아니다."""
    _freeze_seoul_now(monkeypatch, "2026-06-10T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 6, 10))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, db=db)

    assert item.scheduled_date == date(2026, 6, 11)
    assert item.carried_over_from is None


async def test_re_reject_does_not_overwrite_existing_carried_over_from(monkeypatch):
    """재반려가 또 월 경계를 넘어도 최초 이월 기준일을 유지한다."""
    _freeze_seoul_now(monkeypatch, "2026-07-31T10:00:00+09:00")
    item = _item(scheduled_date=date(2026, 7, 31), carried_over_from=date(2026, 6, 30))
    db = _FakeDB(item, _hospital(item.hospital_id))

    await content_api.reject_content(item.hospital_id, item.id, db=db)

    assert item.scheduled_date == date(2026, 8, 1)
    assert item.carried_over_from == date(2026, 6, 30)  # 최초 값 유지
