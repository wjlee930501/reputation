"""load_incidents_queue must paginate at the cause-group level in SQL, not by
loading every matching incident's full 5-table row and slicing a Python list."""

import uuid
from datetime import UTC, datetime

from app.api.admin.operations_center_incident_queries import load_incidents_queue
from app.api.admin.operations_center_query_common import OperationsFilters
from app.models.operations import Incident


def _incident(*, safe_error_code: str, hospital_id=None, last_seen_at=None):
    now = last_seen_at or datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    return Incident(
        id=uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        operation_run_id=None,
        dedupe_key=f"test:{uuid.uuid4()}",
        incident_type="WEEKLY_SOV_MEASUREMENT_FAILED",
        state="OPEN",
        severity="HIGH",
        customer_impact="주간 AI 노출 측정이 지연됩니다.",
        source_type="WEEKLY_SOV",
        source_id="hospital:2026-W34",
        safe_error_code=safe_error_code,
        safe_error_message="측정이 지연되고 있습니다.",
        next_action="비용 한도를 확인하세요.",
        admin_path="/operations",
        first_seen_at=now,
        last_seen_at=now,
        occurrence_count=1,
        episode_seq=1,
        version=1,
    )


class _Result:
    """Mimics AsyncSession.execute(...) result: only `.all()` is used by the queue."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Returns pass-1 (group) rows, then pass-2 (page) rows, tracking each statement's
    SQL so the test can assert pass 2 only asks Postgres for the requested page's
    incident ids — not every incident that matched the filter."""

    def __init__(self, group_rows, page_rows_by_call):
        self.group_rows = group_rows
        self.page_rows_by_call = list(page_rows_by_call)
        self.compiled_statements: list[str] = []

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        self.compiled_statements.append(
            str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        )
        if len(self.compiled_statements) == 1:
            return _Result(self.group_rows)
        return _Result(self.page_rows_by_call.pop(0))


async def test_total_counts_cause_groups_not_raw_incidents():
    """Three incidents, two sharing a cause code, must report total=2 groups."""
    shared_a = _incident(safe_error_code="COST_BLOCKED")
    shared_b = _incident(safe_error_code="COST_GUARD_LIMIT_REACHED")
    distinct = _incident(safe_error_code="SITE_BUILD_FAILED")
    # ordering matches the query's ORDER BY (sla_due_at, last_seen_at desc, id) —
    # not exercised further here, first-seen order is enough for grouping.
    group_rows = [(shared_a, None), (distinct, None), (shared_b, None)]

    from app.models.hospital import Hospital

    page_rows = [
        (shared_a, Hospital(id=shared_a.hospital_id, name="A", slug="a"), None, None, None),
        (shared_b, Hospital(id=shared_b.hospital_id, name="B", slug="b"), None, None, None),
        (distinct, Hospital(id=distinct.hospital_id, name="C", slug="c"), None, None, None),
    ]
    db = _FakeDB(group_rows, [page_rows])

    total, items = await load_incidents_queue(
        db,
        OperationsFilters(),
        page=1,
        page_size=25,
        overview=False,
        now=datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert total == 2  # COST_LIMIT_EXHAUSTED (shared_a + shared_b) + SITE_BUILD_FAILED
    assert len(db.compiled_statements) == 2  # pass 1 (group) + pass 2 (page) — no more
    same_type_counts = sorted(item.same_type_count for item in items)
    assert same_type_counts == [1, 2]


async def test_pass_two_only_requests_the_current_pages_incident_ids():
    """With page_size=1, page 2 must load only the second group's incident — not all."""
    first = _incident(safe_error_code="SITE_BUILD_FAILED")
    second = _incident(safe_error_code="DOMAIN_CERT_FAILED")
    group_rows = [(first, None), (second, None)]

    from app.models.hospital import Hospital

    page_rows = [(second, Hospital(id=second.hospital_id, name="B", slug="b"), None, None, None)]
    db = _FakeDB(group_rows, [page_rows])

    total, items = await load_incidents_queue(
        db,
        OperationsFilters(),
        page=2,
        page_size=1,
        overview=False,
        now=datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert total == 2
    assert len(items) == 1
    page_statement_sql = db.compiled_statements[1]
    assert str(second.id) in page_statement_sql
    assert str(first.id) not in page_statement_sql


async def test_empty_page_short_circuits_without_a_second_query():
    """A page past the last group must not issue a page_statement at all."""
    only = _incident(safe_error_code="SITE_BUILD_FAILED")
    db = _FakeDB([(only, None)], [])

    total, items = await load_incidents_queue(
        db,
        OperationsFilters(),
        page=5,
        page_size=1,
        overview=False,
        now=datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert total == 1
    assert items == []
    assert len(db.compiled_statements) == 1  # only the group pass ran
