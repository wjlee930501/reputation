import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.models.hospital import Plan
from app.services.content_calendar import generate_monthly_slots


class _ScalarResult:
    def __init__(self, items=None):
        self._items = items or []

    def all(self):
        return self._items

    def first(self):
        return self._items[0] if self._items else None


class _ExecuteResult:
    def __init__(self, items=None):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class _FakeDB:
    def __init__(self, hospital, schedules=None):
        self.hospital = hospital
        self.schedules = schedules or []
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if object_id == self.hospital.id else None

    async def execute(self, statement):
        return _ExecuteResult(self.schedules)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def test_generate_monthly_slots_rejects_capacity_shortfall():
    with pytest.raises(ValueError, match="발행일 수\\(9\\).*요금제 편수\\(16\\)"):
        generate_monthly_slots("PLAN_16", [1, 4], arrow.get("2026-05-10").floor("month"))


def test_generate_monthly_slots_respects_active_from_date():
    with pytest.raises(ValueError, match="발행일 수\\(12\\).*요금제 편수\\(16\\)"):
        generate_monthly_slots(
            "PLAN_16",
            [0, 1, 2, 3],
            arrow.get("2026-05-11").floor("month"),
            start_date=date(2026, 5, 11),
        )


async def test_set_schedule_returns_validation_error_for_capacity_shortfall():
    hospital = SimpleNamespace(id=uuid.uuid4(), site_live=False, schedule_set=False)
    db = _FakeDB(hospital)
    body = content_api.ScheduleCreate(plan="PLAN_16", publish_days=[1, 4], active_from=date(2026, 5, 10))

    with pytest.raises(HTTPException) as exc:
        await content_api.set_schedule(hospital.id, body, db=db)

    assert exc.value.status_code == 400
    assert "발행일 수(6)" in exc.value.detail
    assert db.committed is False


def _freeze_arrow(monkeypatch, iso="2026-06-10T12:00:00+09:00"):
    frozen = arrow.get(iso)
    monkeypatch.setattr(
        content_api,
        "arrow",
        SimpleNamespace(get=arrow.get, Arrow=arrow.Arrow, now=lambda tz=None: frozen),
    )
    return frozen


async def test_set_schedule_syncs_hospital_plan_and_queues_imminent_slots(monkeypatch):
    """A3: hospital.plan 동기화 / P2-9: 오늘·내일 슬롯은 야간 배치를 못 타므로 즉시 큐잉."""
    hospital = SimpleNamespace(id=uuid.uuid4(), site_live=False, schedule_set=False, plan=None)
    db = _FakeDB(hospital)
    queued = []
    monkeypatch.setattr(
        content_api.regenerate_content_item,
        "apply_async",
        lambda *, args, queue: queued.append({"args": args, "queue": queue}),
    )
    _freeze_arrow(monkeypatch)  # today=2026-06-10, tomorrow=2026-06-11

    body = content_api.ScheduleCreate(
        plan="PLAN_8",
        publish_days=[0, 1, 2, 3, 4, 5, 6],
        active_from=date(2026, 6, 11),
    )
    response = await content_api.set_schedule(hospital.id, body, db=db)

    assert response["slots_created"] == 8
    assert response["first_publish_date"] == "2026-06-11"
    assert hospital.plan == Plan.PLAN_8
    assert hospital.schedule_set is True
    assert db.committed is True
    # 2026-06-11(내일) 슬롯 1개만 즉시 생성 큐에 적재
    assert len(queued) == 1
    assert queued[0]["queue"] == "content"


async def test_set_schedule_does_not_queue_future_only_slots(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), site_live=False, schedule_set=False, plan=None)
    db = _FakeDB(hospital)
    queued = []
    monkeypatch.setattr(
        content_api.regenerate_content_item,
        "apply_async",
        lambda *, args, queue: queued.append({"args": args, "queue": queue}),
    )
    _freeze_arrow(monkeypatch)  # tomorrow=2026-06-11

    body = content_api.ScheduleCreate(
        plan="PLAN_8",
        publish_days=[0, 1, 2, 3, 4, 5, 6],
        active_from=date(2026, 6, 15),
    )
    await content_api.set_schedule(hospital.id, body, db=db)

    assert queued == []


async def test_get_schedule_returns_active_schedule():
    """A2 — 스케줄 화면이 덮어쓰기 전 현재 상태를 표시할 수 있어야 한다."""
    hospital = SimpleNamespace(id=uuid.uuid4())
    schedule = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        plan="PLAN_12",
        publish_days=[0, 2, 4],
        active_from=date(2026, 7, 1),
        is_active=True,
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    db = _FakeDB(hospital, schedules=[schedule])

    response = await content_api.get_schedule(hospital.id, db=db)

    assert response == {
        "schedule_id": str(schedule.id),
        "hospital_id": str(hospital.id),
        "plan": "PLAN_12",
        "publish_days": [0, 2, 4],
        "active_from": "2026-07-01",
        "is_active": True,
        "created_at": schedule.created_at.isoformat(),
    }


async def test_get_schedule_returns_404_when_no_active_schedule():
    hospital = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDB(hospital, schedules=[])

    with pytest.raises(HTTPException) as exc:
        await content_api.get_schedule(hospital.id, db=db)

    assert exc.value.status_code == 404


def test_list_content_rejects_out_of_range_month():
    """P2-11 — month=13이 arrow까지 내려가 500이 나지 않고 422로 거절돼야 한다."""
    from fastapi.testclient import TestClient
    from slowapi import Limiter

    from app.core.database import get_db
    from app.core.rate_limit import get_request_ip
    from app.main import app

    class _EmptyDB:
        async def execute(self, _stmt):
            return _ExecuteResult([])

    async def override_get_db():
        yield _EmptyDB()

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            hospital_id = uuid.uuid4()
            headers = {"X-Admin-Key": "test-admin-key"}
            bad_month = client.get(
                f"/api/v1/admin/hospitals/{hospital_id}/content?year=2026&month=13",
                headers=headers,
            )
            bad_year = client.get(
                f"/api/v1/admin/hospitals/{hospital_id}/content?year=99999&month=5",
                headers=headers,
            )
            ok = client.get(
                f"/api/v1/admin/hospitals/{hospital_id}/content?year=2026&month=12",
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter

    assert bad_month.status_code == 422
    assert bad_year.status_code == 422
    assert ok.status_code == 200
    assert ok.json() == []
