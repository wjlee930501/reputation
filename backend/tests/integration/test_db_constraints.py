"""Real-Postgres constraint/trigger behavior (TEST-1/TEST-3).

Covers the schema guarantees the mock unit suite cannot: unique-constraint races
(→ IntegrityError → the app's 409 handler), the audit-log append-only trigger, the
NOT NULL JSON list columns, and the covering FK indexes.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


def _insert_hospital(conn, *, slug, hid=None):
    hid = hid or uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status) "
            "VALUES (:id, :name, :slug, 'ONBOARDING')"
        ),
        {"id": hid, "name": "통합테스트병원", "slug": slug},
    )
    return hid


def test_duplicate_slug_raises_integrity_error(pg_conn):
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    _insert_hospital(pg_conn, slug=slug)
    with pytest.raises(IntegrityError):
        with pg_conn.begin_nested():
            _insert_hospital(pg_conn, slug=slug)


def test_hospital_json_columns_reject_null(pg_conn):
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    with pytest.raises(IntegrityError):
        with pg_conn.begin_nested():
            pg_conn.execute(
                text(
                    "INSERT INTO hospitals (id, name, slug, status, region) "
                    "VALUES (:id, 'x', :slug, 'ONBOARDING', NULL)"
                ),
                {"id": uuid.uuid4(), "slug": slug},
            )


def test_audit_log_update_and_delete_are_blocked(pg_conn):
    hid = _insert_hospital(pg_conn, slug=f"itest-{uuid.uuid4().hex[:8]}")
    aid = uuid.uuid4()
    pg_conn.execute(
        text(
            "INSERT INTO admin_audit_logs (id, hospital_id, actor, action) "
            "VALUES (:id, :hid, 'AE', 'publish')"
        ),
        {"id": aid, "hid": hid},
    )

    with pytest.raises(DBAPIError):  # trigger RAISE EXCEPTION
        with pg_conn.begin_nested():
            pg_conn.execute(text("UPDATE admin_audit_logs SET actor='hacker' WHERE id=:id"), {"id": aid})

    with pytest.raises(DBAPIError):
        with pg_conn.begin_nested():
            pg_conn.execute(text("DELETE FROM admin_audit_logs WHERE id=:id"), {"id": aid})


def test_hospital_delete_cascades_audit_hospital_id_to_null(pg_conn):
    # The append-only trigger must still permit the FK ON DELETE SET NULL cascade.
    hid = _insert_hospital(pg_conn, slug=f"itest-{uuid.uuid4().hex[:8]}")
    aid = uuid.uuid4()
    pg_conn.execute(
        text(
            "INSERT INTO admin_audit_logs (id, hospital_id, actor, action) "
            "VALUES (:id, :hid, 'AE', 'publish')"
        ),
        {"id": aid, "hid": hid},
    )
    pg_conn.execute(text("DELETE FROM hospitals WHERE id=:id"), {"id": hid})
    row = pg_conn.execute(
        text("SELECT hospital_id, actor FROM admin_audit_logs WHERE id=:id"), {"id": aid}
    ).first()
    assert row is not None and row[0] is None and row[1] == "AE"


def test_fk_covering_indexes_exist(pg_conn):
    expected = {
        "ix_content_schedules_hospital_id",
        "ix_content_items_schedule_id",
        "ix_query_matrix_hospital_id",
        "ix_exposure_actions_linked_report_id",
    }
    present = {
        r[0]
        for r in pg_conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names)"),
            {"names": list(expected)},
        ).all()
    }
    assert expected <= present
