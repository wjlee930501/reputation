"""Real-Postgres acceptance for the additive content revision migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests import content_revision_reconciliation_cases as reconciliation_cases
from tests.db_env import require_db_url

_URL_ENV = "MIGRATION_UPGRADE_DATABASE_URL"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_VISIBLE_ID = reconciliation_cases.VISIBLE_ID
_HIDDEN_ID = reconciliation_cases.HIDDEN_ID
_WITHHELD_ID = reconciliation_cases.WITHHELD_ID
_UNKNOWN_ID = reconciliation_cases.UNKNOWN_ID
_MALFORMED_ID = reconciliation_cases.MALFORMED_ID
_HASH_ONLY_ID = reconciliation_cases.HASH_ONLY_ID


def _database_url() -> str:
    return require_db_url(_URL_ENV)


def _sync_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


def _async_url(value: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if value.startswith(prefix):
            return "postgresql+asyncpg://" + value[len(prefix) :]
    return value


def _upgrade(revision: str, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _async_url(database_url)
    env["SYNC_DATABASE_URL"] = _sync_url(database_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_BACKEND_ROOT,
        env=env,
        check=True,
    )


def _insert_fixture(connection) -> None:
    hospital_id, schedule_id, philosophy_id = (
        uuid.UUID(f"84000000-0000-0000-0000-{value:012d}") for value in range(10, 13)
    )
    connection.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, plan) "
            "VALUES (:id, '판 이력 병원', 'revision-clinic', 'PLAN_12')"
        ),
        {"id": hospital_id},
    )
    connection.execute(
        text(
            "INSERT INTO content_schedules "
            "(id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hospital_id, 'PLAN_12', '[1]'::json, DATE '2026-10-01')"
        ),
        {"id": schedule_id, "hospital_id": hospital_id},
    )
    connection.execute(
        text(
            "INSERT INTO hospital_content_philosophies "
            "(id, hospital_id, version, status, source_snapshot_hash, approved_at) "
            "VALUES (:id, :hospital_id, 1, 'APPROVED', :hash, now())"
        ),
        {"id": philosophy_id, "hospital_id": hospital_id, "hash": "a" * 64},
    )
    rows = (
        (_VISIBLE_ID, "PUBLISHED", philosophy_id, "ALIGNED", "공개 원문", "공개 본문"),
        (_HIDDEN_ID, "DRAFT", philosophy_id, "ALIGNED", "초안", "숨김 본문"),
        (_WITHHELD_ID, "WITHHELD", philosophy_id, "ALIGNED", "철회", "철회 본문"),
        (_UNKNOWN_ID, "PUBLISHED", None, "ALIGNED", "근거 불명", "불명 본문"),
        (_MALFORMED_ID, "PUBLISHED", philosophy_id, "ALIGNED", "오도된 근거", "오도 본문"),
        (_HASH_ONLY_ID, "PUBLISHED", philosophy_id, "ALIGNED", "해시만 근거", "해시 본문"),
    )
    for sequence_no, row in enumerate(rows, start=1):
        content_id, status, content_philosophy_id, essence_status, title, body = row
        connection.execute(
            text(
                "INSERT INTO content_items "
                "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
                "title, body, meta_description, references_list, reference_checks, "
                "scheduled_date, status, content_philosophy_id, generation_philosophy_id, "
                "last_reviewed_philosophy_id, essence_status, essence_check_summary, "
                "content_brief, content_revision, generated_at, published_at, published_by, "
                "post_publish_reviewed_at, post_publish_reviewed_by) VALUES "
                "(:id, :hospital_id, :schedule_id, 'NOTICE', :sequence_no, 4, :title, :body, "
                "'설명', '[{\"title\": \"보건복지부\", \"url\": "
                "\"https://www.mohw.go.kr/\"}]'::jsonb, "
                "'[{\"verdict\": \"PASS\", \"url\": \"https://www.mohw.go.kr/\"}]'::jsonb, "
                "DATE '2026-10-09', :status, :philosophy_id, :philosophy_id, :philosophy_id, "
                ":essence_status, '{\"generation_provenance\": {\"evidence_note_ids\": "
                "[\"note-1\"]}}'::jsonb, "
                "'{\"schema_version\": \"content-brief-v2\", \"target_query\": \"진료 질문\", "
                "\"treatment_narrative\": {\"source\": \"approved_philosophy\", "
                "\"angle\": \"승인 근거를 환자 언어로 설명\", "
                "\"evidence_note_ids\": [\"note-1\"]}, "
                "\"source_snapshot\": {\"hash\": \"snapshot-1\", "
                "\"source_asset_ids\": [\"asset-1\"]}}'::jsonb, 7, now(), "
                "CASE WHEN :status = 'PUBLISHED' THEN now() ELSE NULL END, 'LEGACY_AE', "
                "now(), 'REVIEWER')"
            ),
            {
                "id": content_id,
                "hospital_id": hospital_id,
                "schedule_id": schedule_id,
                "sequence_no": sequence_no,
                "title": title,
                "body": body,
                "status": status,
                "philosophy_id": content_philosophy_id,
                "essence_status": essence_status,
            },
        )
    connection.execute(
        text(
            "UPDATE content_items SET content_brief=NULL, essence_check_summary='{}'::jsonb, "
            "reference_checks='{\"misleading\": \"PASS\"}'::jsonb WHERE id=:id"
        ),
        {"id": _MALFORMED_ID},
    )
    connection.execute(
        text(
            "UPDATE content_items SET content_brief="
            "'{\"schema_version\": \"content-brief-v2\", \"target_query\": \"질문\", "
            "\"source_snapshot\": {\"hash\": \"hash-only\", "
            "\"source_asset_ids\": []}}'::jsonb, essence_check_summary="
            "'{\"generation_provenance\": {}}'::jsonb, references_list='[]'::jsonb, "
            "reference_checks='[]'::jsonb WHERE id=:id"
        ),
        {"id": _HASH_ONLY_ID},
    )


@pytest.fixture(scope="module")
def migrated_engine():
    database_url = _database_url()
    parsed = make_url(_sync_url(database_url))
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "reputation_autonomy_migration" or (
        parsed.database is not None and parsed.database.endswith("_revisions_upgrade_test")
    )
    engine = create_engine(parsed)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    _upgrade("0083_add_operation_run_not_before", database_url)
    baseline = inspect(engine)
    assert not baseline.has_table("content_revisions")
    assert "active_revision_id" not in {
        column["name"] for column in baseline.get_columns("content_items")
    }
    with engine.begin() as connection:
        _insert_fixture(connection)
    _upgrade("head", database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_visible_approved_content_gets_one_exact_active_revision(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT ci.title AS old_title, ci.body AS old_body, ci.meta_description AS old_meta, "
                "ci.references_list AS old_references, cr.title, cr.body, cr.meta_description, "
                "cr.references_list, cr.reference_checks, cr.source_snapshot, "
                "cr.generation_provenance, cr.source_snapshot_hash, cr.approval_status, "
                "cr.legacy_content_revision, cr.source_fingerprint, cr.approval_hash "
                "FROM content_items ci JOIN content_revisions cr ON cr.id=ci.active_revision_id "
                "WHERE ci.id=:id"
            ),
            {"id": _VISIBLE_ID},
        ).one()
        assert (row.old_title, row.old_body, row.old_meta, row.old_references) == (
            row.title,
            row.body,
            row.meta_description,
            row.references_list,
        )
        assert row.reference_checks[0]["verdict"] == "PASS"
        assert row.source_snapshot["source_snapshot"]["source_asset_ids"] == ["asset-1"]
        assert row.source_snapshot["target_query"] == "진료 질문"
        assert row.source_snapshot["treatment_narrative"]["angle"] == (
            "승인 근거를 환자 언어로 설명"
        )
        assert row.generation_provenance["evidence_note_ids"] == ["note-1"]
        assert row.source_snapshot_hash == "a" * 64
        assert row.approval_status == "APPROVED"
        assert row.legacy_content_revision == 7
        assert len(row.source_fingerprint) == 64
        assert len(row.approval_hash) == 64
        revision_columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='content_revisions'"
                )
            ).scalars()
        )
        assert revision_columns.isdisjoint(
            {"image_url", "image_prompt", "image_content_hash", "image_subject_hash"}
        )
        revision_count = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"), {"id": _VISIBLE_ID}
        ).scalar_one()
        assert revision_count == 1


@pytest.mark.parametrize("content_id", [_HIDDEN_ID, _WITHHELD_ID])
def test_hidden_or_withheld_content_is_not_auto_approved(
    migrated_engine, content_id: uuid.UUID
) -> None:
    with migrated_engine.connect() as connection:
        active_id = connection.execute(
            text("SELECT active_revision_id FROM content_items WHERE id=:id"), {"id": content_id}
        ).scalar_one()
        revision_count = connection.execute(
            text("SELECT count(*) FROM content_revisions WHERE content_item_id=:id"), {"id": content_id}
        ).scalar_one()
        assert active_id is None
        assert revision_count == 0


test_post_drain_reconciliation_handles_five_legacy_commit_classes_repeatably = (
    reconciliation_cases.test_post_drain_reconciliation_handles_five_legacy_commit_classes_repeatably
)
test_approved_revision_rows_reject_in_place_mutation = (
    reconciliation_cases.test_approved_revision_rows_reject_in_place_mutation
)
test_approved_revision_rows_reject_direct_delete = (
    reconciliation_cases.test_approved_revision_rows_reject_direct_delete
)
test_content_item_with_approved_history_cannot_be_deleted = (
    reconciliation_cases.test_content_item_with_approved_history_cannot_be_deleted
)
test_active_pointer_cannot_reference_another_content_item = (
    reconciliation_cases.test_active_pointer_cannot_reference_another_content_item
)
test_unknown_legacy_provenance_is_not_fabricated_or_approved = (
    reconciliation_cases.test_unknown_legacy_provenance_is_not_fabricated_or_approved
)
test_historical_snapshot_preserves_the_pre_migration_public_subset = (
    reconciliation_cases.test_historical_snapshot_preserves_the_pre_migration_public_subset
)
test_previously_published_text_gets_a_truthful_historical_snapshot = (
    reconciliation_cases.test_previously_published_text_gets_a_truthful_historical_snapshot
)
test_reconciliation_preserves_frozen_history_until_explicit_unpublish = (
    reconciliation_cases.test_reconciliation_preserves_frozen_history_until_explicit_unpublish
)
