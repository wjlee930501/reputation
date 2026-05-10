import uuid
from datetime import date
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.services.content_calendar import generate_monthly_slots


class _ScalarResult:
    def all(self):
        return []


class _ExecuteResult:
    def scalars(self):
        return _ScalarResult()


class _FakeDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if object_id == self.hospital.id else None

    async def execute(self, statement):
        return _ExecuteResult()

    def add(self, item):
        pass

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
