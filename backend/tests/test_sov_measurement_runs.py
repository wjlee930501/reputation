import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin.sov import get_sov_measurement_runs, get_sov_queries, get_sov_trend
from app.workers.tasks import _build_sov_record_from_result, _measurement_status_for_result


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


def _record(query_id, *, status="SUCCESS", is_mentioned=False, measured_at=None, ai_platform="chatgpt"):
    return SimpleNamespace(
        query_id=query_id,
        measurement_status=status,
        is_mentioned=is_mentioned,
        ai_platform=ai_platform,
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


async def test_sov_queries_split_success_and_failure_by_platform():
    hospital_id = uuid.uuid4()
    query_id = uuid.uuid4()
    query = SimpleNamespace(id=query_id, query_text="강남 치질 병원 추천")
    records = [
        _record(query_id, status="SUCCESS", is_mentioned=True, ai_platform="chatgpt"),
        _record(query_id, status="SUCCESS", is_mentioned=False, ai_platform="chatgpt"),
        _record(query_id, status="SUCCESS", is_mentioned=True, ai_platform="gemini"),
        _record(query_id, status="FAILED", is_mentioned=True, ai_platform="gemini"),
    ]
    db = _FakeDB(
        hospital=SimpleNamespace(id=hospital_id),
        execute_results=[_ScalarResult([query]), _ScalarResult(records)],
    )

    response = await get_sov_queries(hospital_id, db)

    assert response[0]["mention_count"] == 2
    assert response[0]["total_count"] == 3
    assert response[0]["failure_count"] == 1
    assert response[0]["mention_rate"] == 66.7
    assert response[0]["platform_breakdown"] == {
        "CHATGPT": {
            "platform_label": "ChatGPT",
            "mention_count": 1,
            "total_count": 2,
            "failure_count": 0,
            "mention_rate": 50.0,
        },
        "GEMINI": {
            "platform_label": "Gemini",
            "mention_count": 1,
            "total_count": 1,
            "failure_count": 1,
            "mention_rate": 100.0,
        },
    }


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
            "display": {
                "measurement_method_label": "AI 답변 측정",
                "status_label": "완료",
            },
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


async def test_measurement_runs_labels_current_openai_modes():
    hospital_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    runs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            run_label="model",
            measurement_method="OPENAI_CHAT_COMPLETIONS",
            status="COMPLETED",
            query_count=1,
            success_count=1,
            failure_count=0,
            started_at=timestamp,
            completed_at=timestamp,
            model_name="gpt-4o",
            search_mode="model",
            config={},
            error_summary=None,
            created_at=timestamp,
            updated_at=timestamp,
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            run_label="web",
            measurement_method="OPENAI_RESPONSES_WEB_SEARCH",
            status="COMPLETED",
            query_count=1,
            success_count=1,
            failure_count=0,
            started_at=timestamp,
            completed_at=timestamp,
            model_name="gpt-4o",
            search_mode="web",
            config={},
            error_summary=None,
            created_at=timestamp,
            updated_at=timestamp,
        ),
    ]
    db = _FakeDB(
        hospital=SimpleNamespace(id=hospital_id),
        execute_results=[_ScalarResult(runs)],
    )

    response = await get_sov_measurement_runs(hospital_id, db)

    assert response[0]["display"]["measurement_method_label"] == "OpenAI 모델 응답 측정"
    assert response[1]["display"]["measurement_method_label"] == "ChatGPT Search 유사 측정"


def test_measurement_status_treats_empty_raw_response_as_failed():
    assert _measurement_status_for_result({"raw_response": "답변", "is_mentioned": False}) == ("SUCCESS", None)
    assert _measurement_status_for_result({"raw_response": "", "is_mentioned": False}) == (
        "FAILED",
        "empty_raw_response",
    )


def test_sov_record_builder_persists_platform_and_failure_reason():
    hospital_id = uuid.uuid4()
    query_id = uuid.uuid4()
    run_id = uuid.uuid4()
    target_id = uuid.uuid4()
    variant_id = uuid.uuid4()

    record = _build_sov_record_from_result(
        hospital_id=hospital_id,
        query_id=query_id,
        measurement_run_id=run_id,
        platform="gemini",
        target_id=target_id,
        variant_id=variant_id,
        result={"is_mentioned": True, "raw_response": "", "competitor_mentions": [{"name": "경쟁의원"}]},
    )

    assert record.hospital_id == hospital_id
    assert record.query_id == query_id
    assert record.measurement_run_id == run_id
    assert record.ai_query_target_id == target_id
    assert record.ai_query_variant_id == variant_id
    assert record.ai_platform == "gemini"
    assert record.is_mentioned is True
    assert record.raw_response == ""
    assert record.competitor_mentions == [{"name": "경쟁의원"}]
    assert record.measurement_status == "FAILED"
    assert record.failure_reason == "empty_raw_response"
