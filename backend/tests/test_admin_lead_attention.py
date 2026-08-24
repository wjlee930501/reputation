from datetime import datetime, timezone

from app.api.admin.leads import (
    get_sales_lead_summary,
    lead_needs_attention_clause,
    lead_overdue_uncontacted_clause,
)


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_attention_definition_is_new_or_overdue_and_uncontacted():
    checked_at = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    sql = _sql(lead_needs_attention_clause(checked_at))

    assert "sales_leads.status = 'NEW'" in sql
    assert "sales_leads.created_at <= '2026-08-23 12:00:00+00:00'" in sql
    assert "sales_leads.converted_hospital_id IS NULL" in sql
    assert "sales_leads.converted_at IS NULL" in sql
    assert "sales_leads.status NOT IN ('CONTACTED', 'CONVERTED', 'DISMISSED')" in sql


def test_overdue_counter_uses_the_same_uncontacted_subset():
    checked_at = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    sql = _sql(lead_overdue_uncontacted_clause(checked_at))

    assert "sales_leads.created_at <= '2026-08-23 12:00:00+00:00'" in sql
    assert "sales_leads.status NOT IN ('CONTACTED', 'CONVERTED', 'DISMISSED')" in sql


async def test_summary_excludes_qa_markers_without_deleting_the_rows():
    class SummaryDB:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return self

        def one(self):
            return (8, 8, 8, 2)

    db = SummaryDB()
    summary = await get_sales_lead_summary(db)
    sql = _sql(db.statement)

    assert summary == {"total": 8, "needs_attention": 8, "overdue": 8, "operations_test": 2}
    assert "ops-qa" in sql
    assert "ops-qa-v1" in sql
    assert "count(sales_leads.id) FILTER" in sql
