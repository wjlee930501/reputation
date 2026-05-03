import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin.sov import get_sov_measurement_runs, get_sov_queries, get_sov_trend


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _FakeDB:
    def __init__(self, hospital=None, execute_results=None):
        self.hospital = hospital
        self.execute_results = list(execute_results or [])

    async def get(self, model, item_id):
        return self.hospital

    async def execute(self, stmt):
        return self.execute_results.pop(0)


def _record(query_id, *, status="SUCCESS", is_mentioned=False, measured_at=None):
    return SimpleNamespace(
        query_id=query_id,
        measurement_status=status,
        is_mentioned=is_mentioned,
        measured_at=measured_at or datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


async def test_sov_queries_exclude_failed_records_from_denominator():
    hospital_id = uuid.uuid4()
    query_id = uuid.uuid4()
    query = SimpleNamespace(id=query_id, query_text="강남 치질 병원 추천")
    records = [
        _record(query_id, status="SUCCESS", is_mentioned=True),
        _record(query_id, status=None, is_mentioned=False),
        _record(query_id, status="FAILED", is_mentioned=True),
    ]
    db = _FakeDB(
        hospital=SimpleNamespace(id=hospital_id),
        execute_results=[_ScalarResult([query]), _ScalarResult(records)],
    )

    response = await get_sov_queries(hospital_id, db)

    assert response[0]["mention_count"] == 1
    assert response[0]["total_count"] == 2
    assert response[0]["failure_count"] == 1
    assert response[0]["mention_rate"] == 50.0


async def test_sov_trend_excludes_failed_records_from_denominator():
    hospital_id = uuid.uuid4()
    query_id = uuid.uuid4()
    measured_at = arrow.now("Asia/Seoul").shift(days=-1).datetime
    records = [
        _record(query_id, status="SUCCESS", is_mentioned=True, measured_at=measured_at),
        _record(query_id, status="FAILED", is_mentioned=True, measured_at=measured_at),
    ]
    db = _FakeDB(
        hospital=SimpleNamespace(id=hospital_id),
        execute_results=[_ScalarResult(records)],
    )

    response = await get_sov_trend(hospital_id, db)

    assert response[-1]["mention_count"] == 1
    assert response[-1]["total_count"] == 1
    assert response[-1]["failure_count"] == 1
    assert response[-1]["sov_pct"] == 100.0


async def test_measurement_runs_endpoint_shape():
    hospital_id = uuid.uuid4()
    run_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    run = SimpleNamespace(
        id=run_id,
        hospital_id=hospital_id,
        run_label="baseline",
        measurement_method="OPENAI_RESPONSE",
        status="COMPLETED",
        query_count=10,
        success_count=8,
        failure_count=2,
        started_at=timestamp,
        completed_at=timestamp,
        model_name="gpt-4.1",
        search_mode="web",
        config={"sample": True},
        error_summary={"timeout": 2},
        created_at=timestamp,
        updated_at=timestamp,
    )
    db = _FakeDB(
        hospital=SimpleNamespace(id=hospital_id),
        execute_results=[_ScalarResult([run])],
    )

    response = await get_sov_measurement_runs(hospital_id, db)

    assert response == [
        {
            "id": str(run_id),
            "hospital_id": str(hospital_id),
            "run_label": "baseline",
            "measurement_method": "OPENAI_RESPONSE",
            "status": "COMPLETED",
            "query_count": 10,
            "success_count": 8,
            "failure_count": 2,
            "success_rate": 80.0,
            "failure_rate": 20.0,
            "started_at": timestamp.isoformat(),
            "completed_at": timestamp.isoformat(),
            "model_name": "gpt-4.1",
            "search_mode": "web",
            "config": {"sample": True},
            "error_summary": {"timeout": 2},
            "created_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
        }
    ]


async def test_measurement_runs_endpoint_404s_unknown_hospital():
    with pytest.raises(HTTPException) as exc_info:
        await get_sov_measurement_runs(uuid.uuid4(), _FakeDB(hospital=None))

    assert exc_info.value.status_code == 404
