"""Upgrade a populated 0064 database through the additive cost/content/measurement tail."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

_URL = os.getenv("MIGRATION_UPGRADE_DATABASE_URL")
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BASELINE = "0064_manifest_recovery_guard"

pytestmark = pytest.mark.skipif(
    not _URL, reason="MIGRATION_UPGRADE_DATABASE_URL is not configured"
)


def _sync_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


def _async_url(value: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+asyncpg://" + value[len(prefix) :]
    return value


def _upgrade(revision: str, database_url: str) -> None:
    upgrade_env = os.environ.copy()
    upgrade_env["DATABASE_URL"] = _async_url(database_url)
    upgrade_env["SYNC_DATABASE_URL"] = _sync_url(database_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_BACKEND_ROOT,
        env=upgrade_env,
        check=True,
    )


def test_populated_0064_upgrades_without_inventing_provenance_or_measurements(
) -> None:
    assert _URL is not None
    parsed = make_url(_sync_url(_URL))
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "reputation_autonomy_migration"

    engine = create_engine(parsed)
    hospital_id = uuid.UUID("a6500000-0000-0000-0000-000000000001")
    schedule_id = uuid.UUID("a6500000-0000-0000-0000-000000000002")
    content_id = uuid.UUID("a6500000-0000-0000-0000-000000000003")
    erased_content_id = uuid.UUID("a6500000-0000-0000-0000-000000000012")
    query_id = uuid.UUID("a6500000-0000-0000-0000-000000000004")
    run_id = uuid.UUID("a6500000-0000-0000-0000-000000000005")
    sov_id = uuid.UUID("a6500000-0000-0000-0000-000000000006")
    usage_id = uuid.UUID("a6500000-0000-0000-0000-000000000007")
    exact_lead_id = uuid.UUID("a6500000-0000-0000-0000-000000000008")
    other_lead_id = uuid.UUID("a6500000-0000-0000-0000-000000000009")
    exact_diagnosis_id = uuid.UUID("a6500000-0000-0000-0000-000000000010")
    other_diagnosis_id = uuid.UUID("a6500000-0000-0000-0000-000000000011")

    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        _upgrade(_BASELINE, _URL)

        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO hospitals (id, name, slug) VALUES (:id, '기존 병원', 'existing')"),
                {"id": hospital_id},
            )
            connection.execute(
                text(
                    "INSERT INTO content_schedules "
                    "(id, hospital_id, plan, publish_days, active_from) "
                    "VALUES (:id, :hospital_id, 'PLAN_12', '[1]'::json, DATE '2026-08-01')"
                ),
                {"id": schedule_id, "hospital_id": hospital_id},
            )
            connection.execute(
                text(
                    "INSERT INTO content_items "
                    "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
                    "title, image_url, image_policy_verified_at, scheduled_date, status, "
                    "published_at, published_by) VALUES "
                    "(:id, :hospital_id, :schedule_id, 'FAQ', 1, 12, '기존 글', "
                    "'https://cdn.example/image.png', TIMESTAMPTZ '2026-08-31 14:59:00+00', "
                    "DATE '2026-08-31', 'PUBLISHED', "
                    "TIMESTAMPTZ '2026-08-31 15:00:00+00', 'LEGACY_AE')"
                ),
                {"id": content_id, "hospital_id": hospital_id, "schedule_id": schedule_id},
            )
            connection.execute(
                text(
                    "INSERT INTO content_items "
                    "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
                    "title, scheduled_date, status, published_at, published_by) VALUES "
                    "(:id, :hospital_id, :schedule_id, 'FAQ', 2, 12, NULL, "
                    "DATE '2026-08-30', 'REJECTED', NULL, NULL)"
                ),
                {
                    "id": erased_content_id,
                    "hospital_id": hospital_id,
                    "schedule_id": schedule_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO query_matrix (id, hospital_id, query_text, query_intent) "
                    "VALUES (:id, :hospital_id, '기존 질의', 'LOCAL')"
                ),
                {"id": query_id, "hospital_id": hospital_id},
            )
            connection.execute(
                text(
                    "INSERT INTO measurement_runs (id, hospital_id, run_label) "
                    "VALUES (:id, :hospital_id, '기존 실행')"
                ),
                {"id": run_id, "hospital_id": hospital_id},
            )
            connection.execute(
                text(
                    "INSERT INTO sov_records "
                    "(id, hospital_id, query_id, measurement_run_id, ai_platform, is_mentioned, "
                    "raw_response) VALUES (:id, :hospital_id, :query_id, :run_id, "
                    "'chatgpt', true, '기존 답변')"
                ),
                {
                    "id": sov_id,
                    "hospital_id": hospital_id,
                    "query_id": query_id,
                    "run_id": run_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO hospital_usage_events "
                    "(id, hospital_id, kind, input_tokens, output_tokens) "
                    "VALUES (:id, :hospital_id, 'sov', 17, 5)"
                ),
                {"id": usage_id, "hospital_id": hospital_id},
            )
            for lead_id, suffix in ((exact_lead_id, "exact"), (other_lead_id, "other")):
                connection.execute(
                    text(
                        "INSERT INTO sales_leads "
                        "(id, clinic_name, clinic_type, contact, privacy, source) "
                        "VALUES (:id, :name, '내과', :contact, true, 'AI_DIAGNOSIS')"
                    ),
                    {
                        "id": lead_id,
                        "name": f"기존 리드 {suffix}",
                        "contact": f"legacy-{suffix}@example.invalid",
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO lead_diagnoses "
                    "(id, lead_id, applicant_email_hash, subject_phone_hash, "
                    "subject_hospital_name, subject_region, slot_date, slot_no, queries, "
                    "requested_models, repeat_count, execution_status, execution_attempts, error) "
                    "VALUES (:id, :lead_id, 'email-exact', 'phone-exact', '정확 차단', '서울', "
                    "DATE '2026-08-31', 1, '[]'::jsonb, '{}'::jsonb, 1, 'FAILED', 2, "
                    "'호출 예산 초과로 측정을 중단했습니다: 일일 상한')"
                ),
                {"id": exact_diagnosis_id, "lead_id": exact_lead_id},
            )
            connection.execute(
                text(
                    "INSERT INTO lead_diagnoses "
                    "(id, lead_id, applicant_email_hash, subject_phone_hash, "
                    "subject_hospital_name, subject_region, slot_date, slot_no, queries, "
                    "requested_models, repeat_count, execution_status, execution_attempts, error) "
                    "VALUES (:id, :lead_id, 'email-other', 'phone-other', '다른 실패', '서울', "
                    "DATE '2026-08-31', 2, '[]'::jsonb, '{}'::jsonb, 1, 'FAILED', 2, "
                    "'공급자 재시도 소진')"
                ),
                {"id": other_diagnosis_id, "lead_id": other_lead_id},
            )

        _upgrade("head", _URL)

        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0069_content_first_publication"
            )
            content = connection.execute(
                text(
                    "SELECT image_url, image_policy_verified_at, generation_philosophy_id, "
                    "last_reviewed_philosophy_id, content_revision, image_content_hash, "
                    "image_subject_hash, image_policy_version, first_published_at, "
                    "first_published_by FROM content_items WHERE id=:id"
                ),
                {"id": content_id},
            ).one()
            assert content.image_url == "https://cdn.example/image.png"
            assert content.image_policy_verified_at is not None
            assert content.generation_philosophy_id is None
            assert content.last_reviewed_philosophy_id is None
            assert content.content_revision == 1
            assert content.image_content_hash is None
            assert content.image_subject_hash is None
            assert content.image_policy_version is None
            assert content.first_published_at.astimezone(timezone.utc) == datetime(
                2026, 8, 31, 15, 0, tzinfo=timezone.utc
            )
            assert content.first_published_by == "LEGACY_AE"
            erased_content = connection.execute(
                text(
                    "SELECT first_published_at, first_published_by "
                    "FROM content_items WHERE id=:id"
                ),
                {"id": erased_content_id},
            ).one()
            assert erased_content.first_published_at is None
            assert erased_content.first_published_by is None
            assert connection.execute(
                text("SELECT count(*) FROM measurement_observation_slots")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM sov_records WHERE id=:id"), {"id": sov_id}
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT input_tokens, output_tokens FROM hospital_usage_events WHERE id=:id"
                ),
                {"id": usage_id},
            ).one() == (17, 5)
            exact = connection.execute(
                text(
                    "SELECT execution_status, execution_attempts, cost_defer_reason, "
                    "cost_deferred_until FROM lead_diagnoses WHERE id=:id"
                ),
                {"id": exact_diagnosis_id},
            ).one()
            assert exact.execution_status == "PENDING"
            assert exact.execution_attempts == 1
            assert exact.cost_defer_reason == "legacy_cost_guard"
            assert exact.cost_deferred_until is not None
            other = connection.execute(
                text(
                    "SELECT execution_status, execution_attempts, cost_defer_reason "
                    "FROM lead_diagnoses WHERE id=:id"
                ),
                {"id": other_diagnosis_id},
            ).one()
            assert other == ("FAILED", 2, None)

        provider_event_id = uuid.UUID("a6500000-0000-0000-0000-000000000020")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO provider_usage_events "
                    "(id, provider, workflow, cost_category, hospital_id, idempotency_key) "
                    "VALUES (:id, 'openai', 'SOV_ANSWER', 'sov', :hospital_id, 'upgrade-proof')"
                ),
                {"id": provider_event_id, "hospital_id": hospital_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO provider_usage_events "
                        "(id, provider, workflow, cost_category, hospital_id, idempotency_key) "
                        "VALUES (:id, 'openai', 'SOV_ANSWER', 'sov', :hospital_id, "
                        "'upgrade-proof')"
                    ),
                    {"id": uuid.uuid4(), "hospital_id": hospital_id},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO provider_usage_events "
                        "(id, provider, workflow, cost_category, hospital_id) "
                        "VALUES (:id, 'openai', 'SOV_ANSWER', 'sov', :hospital_id)"
                    ),
                    {"id": uuid.uuid4(), "hospital_id": uuid.uuid4()},
                )

        slot_id = uuid.UUID("a6500000-0000-0000-0000-000000000021")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO measurement_observation_slots "
                    "(id, scope, hospital_id, measurement_run_id, query_id, platform, "
                    "repeat_no, protocol_hash) VALUES "
                    "(:id, 'V0', :hospital_id, :run_id, :query_id, 'chatgpt', 1, :hash)"
                ),
                {
                    "id": slot_id,
                    "hospital_id": hospital_id,
                    "run_id": run_id,
                    "query_id": query_id,
                    "hash": "a" * 64,
                },
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO measurement_observation_slots "
                        "(id, scope, hospital_id, measurement_run_id, query_id, platform, "
                        "repeat_no, protocol_hash) VALUES "
                        "(:id, 'V0', :hospital_id, :run_id, :query_id, 'chatgpt', 1, :hash)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "hospital_id": hospital_id,
                        "run_id": run_id,
                        "query_id": query_id,
                        "hash": "b" * 64,
                    },
                )
    finally:
        engine.dispose()
