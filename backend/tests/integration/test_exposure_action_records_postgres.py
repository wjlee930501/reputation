"""Real-Postgres ownership contract for exposure-action evidence loading."""

import uuid
from datetime import datetime, timezone

from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)
from app.models.sov import AIQueryTarget, AIQueryVariant, MeasurementRun, QueryMatrix, SovRecord
from app.services.exposure_action_engine import _load_recent_sov_records


async def test_query_fallback_cannot_steal_record_with_explicit_different_target(
    pg_async_session,
):
    suffix = uuid.uuid4().hex
    hospital = Hospital(name="노출 근거 테스트", slug=f"exposure-{suffix}")
    query = QueryMatrix(
        hospital=hospital,
        query_text="동일 query id의 소유권은 어느 타깃인가요?",
        query_intent="LOCAL",
        is_active=True,
    )
    active_target = AIQueryTarget(
        hospital=hospital,
        name=f"active-{suffix}",
        target_intent="추천형",
        status="ACTIVE",
    )
    other_target = AIQueryTarget(
        hospital=hospital,
        name=f"other-{suffix}",
        target_intent="추천형",
        status="ACTIVE",
    )
    variant = AIQueryVariant(
        query_target=active_target,
        query_matrix=query,
        query_text=query.query_text,
        platform="CHATGPT",
        language="ko",
        is_active=True,
    )
    run = MeasurementRun(
        hospital=hospital,
        run_label=f"ownership-{suffix}",
        status="COMPLETE",
        config={
            "measurement_protocol": {"policy_version": "integration-v1"},
            "query_snapshot": [
                {
                    "query_id": None,
                    "query_text": query.query_text,
                    "query_intent": "LOCAL",
                }
            ],
        },
    )
    pg_async_session.add_all([hospital, query, active_target, other_target, variant, run])
    await pg_async_session.flush()
    run.config["query_snapshot"][0]["query_id"] = str(query.id)

    explicit_other = SovRecord(
        hospital_id=hospital.id,
        query_id=query.id,
        measurement_run_id=run.id,
        ai_query_target_id=other_target.id,
        ai_platform="chatgpt",
        measured_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
        mention_verdict="MATCHED",
        is_mentioned=True,
        raw_response="explicit other target",
        measurement_status="SUCCESS",
        answer_model="model-a",
        search_calls=1,
        source_urls=[],
    )
    legacy_without_target = SovRecord(
        hospital_id=hospital.id,
        query_id=query.id,
        measurement_run_id=run.id,
        ai_query_target_id=None,
        ai_platform="chatgpt",
        measured_at=datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc),
        mention_verdict="MATCHED",
        is_mentioned=True,
        raw_response="legacy query fallback",
        measurement_status="SUCCESS",
        answer_model="model-a",
        search_calls=1,
        source_urls=[],
    )
    pg_async_session.add_all([explicit_other, legacy_without_target])
    await pg_async_session.flush()
    manifest = MonthlyMeasurementManifest(
        hospital_id=hospital.id,
        period_year=2026,
        period_month=9,
        configured_platforms=["chatgpt"],
        platform_provenance={"measurement_protocol": {"policy_version": "integration-v1"}},
        closes_at=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
    )
    cell = MonthlyMeasurementCell(
        manifest=manifest,
        query_key=f"query:{query.id}",
        query_text=query.query_text,
        query_matrix_id=query.id,
        query_target_id=active_target.id,
        query_variant_id=variant.id,
        platform="chatgpt",
        state="SUCCESS",
    )
    attempt = MonthlyMeasurementAttempt(cell=cell, sov_record=legacy_without_target)
    pg_async_session.add_all([manifest, cell, attempt])
    await pg_async_session.flush()

    loaded = await _load_recent_sov_records(
        pg_async_session, hospital.id, [active_target]
    )

    assert [record.id for record in loaded] == [legacy_without_target.id]
    assert loaded[0]._exposure_question_snapshot == {
        "query_key": f"query:{query.id}",
        "query_text": query.query_text,
        "query_id": str(query.id),
        "target_id": str(active_target.id),
        "variant_id": str(variant.id),
        "platform": "chatgpt",
    }


async def test_query_fallback_requires_one_active_target_for_real_postgres(
    pg_async_session,
):
    suffix = uuid.uuid4().hex
    hospital = Hospital(name="공유 질의 소유권 테스트", slug=f"shared-query-{suffix}")
    shared_query = QueryMatrix(
        hospital=hospital,
        query_text="두 타깃이 공유하는 질문",
        query_intent="LOCAL",
        is_active=True,
    )
    first_only_query = QueryMatrix(
        hospital=hospital,
        query_text="첫 타깃만 사용하는 질문",
        query_intent="LOCAL",
        is_active=True,
    )
    first_target = AIQueryTarget(
        hospital=hospital,
        name=f"first-{suffix}",
        target_intent="추천형",
        status="ACTIVE",
    )
    second_target = AIQueryTarget(
        hospital=hospital,
        name=f"second-{suffix}",
        target_intent="추천형",
        status="ACTIVE",
    )
    variants = [
        AIQueryVariant(
            query_target=first_target,
            query_matrix=shared_query,
            query_text=shared_query.query_text,
            platform="CHATGPT",
            language="ko",
            is_active=True,
        ),
        AIQueryVariant(
            query_target=second_target,
            query_matrix=shared_query,
            query_text=shared_query.query_text,
            platform="CHATGPT",
            language="ko",
            is_active=True,
        ),
        AIQueryVariant(
            query_target=first_target,
            query_matrix=first_only_query,
            query_text=first_only_query.query_text,
            platform="GEMINI",
            language="ko",
            is_active=True,
        ),
    ]
    run = MeasurementRun(
        hospital=hospital,
        run_label=f"shared-query-ownership-{suffix}",
        status="COMPLETE",
        config={"measurement_protocol": {"policy_version": "integration-v1"}},
    )
    pg_async_session.add_all(
        [hospital, shared_query, first_only_query, first_target, second_target, *variants, run]
    )
    await pg_async_session.flush()

    def record(*, query, target, measured_at, response):
        return SovRecord(
            hospital_id=hospital.id,
            query_id=query.id,
            measurement_run_id=run.id,
            ai_query_target_id=target,
            ai_platform="chatgpt",
            measured_at=measured_at,
            mention_verdict="MATCHED",
            is_mentioned=True,
            raw_response=response,
            measurement_status="SUCCESS",
            answer_model="model-a",
            search_calls=1,
            source_urls=[],
        )

    explicit_first = record(
        query=shared_query,
        target=first_target.id,
        measured_at=datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc),
        response="explicit first",
    )
    explicit_second = record(
        query=shared_query,
        target=second_target.id,
        measured_at=datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc),
        response="explicit second",
    )
    ambiguous_legacy = record(
        query=shared_query,
        target=None,
        measured_at=datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc),
        response="ambiguous legacy",
    )
    unique_legacy = record(
        query=first_only_query,
        target=None,
        measured_at=datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc),
        response="unique legacy",
    )
    pg_async_session.add_all(
        [explicit_first, explicit_second, ambiguous_legacy, unique_legacy]
    )
    await pg_async_session.flush()

    loaded = await _load_recent_sov_records(
        pg_async_session,
        hospital.id,
        [first_target, second_target],
    )

    assert {row.id for row in loaded} == {
        explicit_first.id,
        explicit_second.id,
        unique_legacy.id,
    }
    assert ambiguous_legacy.id not in {row.id for row in loaded}
