"""0055 백필은 실제 Postgres에서 두 번 돌아도 같은 결과여야 한다.

`jsonb ||`는 왼쪽이 객체가 아니면 병합이 아니라 배열 연결이다. `[1,2] || {...}`는
`[1,2,{...}]`이 되고, 그러면 `source_metadata->>'asset_kind'`가 여전히 NULL이라
WHERE 조건이 계속 참이다 — 마이그레이션을 다시 돌릴 때마다 객체가 하나씩 더 붙는다.
프로덕션은 이미 0054에 스탬프돼 있어 0055가 다음 배포에서 실행되므로, 이 UPDATE의
재실행 안전성은 실제 SQL로 확인해야 한다.

여기서는 객체·배열·스칼라·JSON null을 실제 테이블에 넣고 마이그레이션이 실행하는
UPDATE를 두 번 돌려 값이 자라지 않는지 본다. SQL NULL은 `source_metadata`가 NOT
NULL이라 행으로 만들 수 없어 식 수준에서 따로 검증한다.
"""
import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import bindparam, text

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0055_backfill_photo_asset_kind.py"
)

BACKFILL_KEYS = {"asset_kind", "approved_usage", "asset_kind_source", "needs_operator_review"}

# (라벨, source_type, source_metadata JSON 리터럴)
SEED_ROWS = (
    ("object_missing_kind", "PHOTO_CLINIC_EXTERIOR", '{"original_filename": "front.png"}'),
    ("object_already_classified", "PHOTO_DOCTOR", '{"asset_kind": "VERIFIED_REAL_PERSON"}'),
    ("array", "PHOTO_CLINIC_INTERIOR", "[1, 2]"),
    ("scalar_string", "PHOTO_TREATMENT_ROOM", '"legacy"'),
    ("json_null", "PHOTO_DOCTOR", "null"),
    ("not_a_photo", "HOMEPAGE", "{}"),
)

BACKFILLED_LABELS = ("object_missing_kind", "array", "scalar_string", "json_null")
NON_OBJECT_LABELS = ("array", "scalar_string", "json_null")


def _backfill_statement():
    """마이그레이션이 실제로 실행하는 UPDATE 문을 그대로 꺼낸다."""
    spec = importlib.util.spec_from_file_location("photo_asset_kind_backfill", MIGRATION_PATH)
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured: list[object] = []
    original = module.op.execute
    module.op.execute = captured.append
    try:
        module.upgrade()
    finally:
        module.op.execute = original
    assert len(captured) == 1
    return captured[0]


@pytest.fixture
def seeded(pg_conn) -> dict[str, uuid.UUID]:
    """분류 없는 사진의 온갖 metadata 형태를 실제 테이블에 넣는다."""
    hospital_id = uuid.uuid4()
    pg_conn.execute(
        text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
        {
            "id": hospital_id,
            "name": "백필형태검증병원",
            "slug": f"backfill-shapes-{uuid.uuid4().hex[:8]}",
        },
    )

    ids: dict[str, uuid.UUID] = {}
    for label, source_type, metadata in SEED_ROWS:
        asset_id = uuid.uuid4()
        ids[label] = asset_id
        pg_conn.execute(
            text(
                "INSERT INTO hospital_source_assets"
                " (id, hospital_id, source_type, title, source_metadata)"
                " VALUES (:id, :hospital_id, CAST(:source_type AS hospital_source_type),"
                " :title, CAST(:metadata AS jsonb))"
            ),
            {
                "id": asset_id,
                "hospital_id": hospital_id,
                "source_type": source_type,
                "title": label,
                "metadata": metadata,
            },
        )
    return ids


def _state(pg_conn, ids: dict[str, uuid.UUID]) -> dict[str, tuple[object, str]]:
    rows = pg_conn.execute(
        text(
            "SELECT title, source_metadata, jsonb_typeof(source_metadata)"
            " FROM hospital_source_assets WHERE id IN :ids"
        ).bindparams(bindparam("ids", value=tuple(ids.values()), expanding=True))
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_second_run_changes_nothing_for_any_metadata_shape(pg_conn, seeded):
    statement = _backfill_statement()

    pg_conn.execute(statement)
    after_first = _state(pg_conn, seeded)
    pg_conn.execute(statement)
    after_second = _state(pg_conn, seeded)

    assert after_first == after_second


def test_every_backfilled_row_becomes_an_object_with_a_top_level_asset_kind(pg_conn, seeded):
    statement = _backfill_statement()

    pg_conn.execute(statement)
    state = _state(pg_conn, seeded)

    for label in BACKFILLED_LABELS:
        metadata, typeof = state[label]
        assert typeof == "object", label
        assert metadata["asset_kind_source"] == "LEGACY_BACKFILL", label
        assert metadata["needs_operator_review"] is True, label
        assert metadata["asset_kind"] in {"EDITORIAL_GRAPHIC", "VERIFIED_FACILITY"}, label


def test_non_object_metadata_never_grows_into_a_nested_array(pg_conn, seeded):
    statement = _backfill_statement()

    pg_conn.execute(statement)
    pg_conn.execute(statement)
    state = _state(pg_conn, seeded)

    for label in NON_OBJECT_LABELS:
        metadata, typeof = state[label]
        # 배열 연결이 일어났다면 typeof가 'array'이고 실행마다 항목이 하나씩 늘어난다.
        assert typeof == "object", label
        assert set(metadata) == BACKFILL_KEYS, label


def test_object_metadata_keeps_the_keys_it_already_had(pg_conn, seeded):
    statement = _backfill_statement()

    pg_conn.execute(statement)
    metadata, _ = _state(pg_conn, seeded)["object_missing_kind"]

    assert metadata["original_filename"] == "front.png"
    assert set(metadata) == BACKFILL_KEYS | {"original_filename"}


def test_already_classified_and_non_photo_rows_are_left_alone(pg_conn, seeded):
    statement = _backfill_statement()
    before = _state(pg_conn, seeded)

    pg_conn.execute(statement)
    after = _state(pg_conn, seeded)

    assert after["object_already_classified"] == before["object_already_classified"]
    assert after["not_a_photo"] == before["not_a_photo"]


def test_sql_null_metadata_is_coerced_instead_of_concatenated(pg_conn):
    """SQL NULL은 NOT NULL 컬럼에 넣을 수 없으므로 식 수준에서 검증한다.

    WHERE의 `IS DISTINCT FROM 'object'`가 NULL도 대상으로 잡고, SET의 CASE가 그걸
    객체로 만들어야 한다. CASE 없이 `NULL || {...}`이면 결과가 NULL이라 백필이
    조용히 아무것도 쓰지 못한다.
    """
    matched, coerced, without_coercion = pg_conn.execute(
        text(
            "SELECT jsonb_typeof(NULL::jsonb) IS DISTINCT FROM 'object',"
            " jsonb_typeof(CASE WHEN jsonb_typeof(NULL::jsonb) = 'object'"
            "     THEN NULL::jsonb ELSE '{}'::jsonb END"
            "   || jsonb_build_object('asset_kind', 'VERIFIED_FACILITY')),"
            " NULL::jsonb || jsonb_build_object('asset_kind', 'VERIFIED_FACILITY')"
        )
    ).one()

    assert matched is True
    assert coerced == "object"
    assert without_coercion is None
