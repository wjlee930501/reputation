"""release blocker integrity constraints

Revision ID: 0028_release_blocker_integrity_constraints
Revises: 0027_add_domain_management_metadata
Create Date: 2026-06-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0028_release_blocker_integrity_constraints"
down_revision = "0027_add_domain_management_metadata"
branch_labels = None
depends_on = None

UQ_DOMAIN = "uq_hospitals_aeo_domain_lower"
UQ_SLOT = "uq_content_items_schedule_slot"
IX_LEAD_CONVERTED = "ix_sales_leads_converted_hospital_id"
FK_LEAD_CONVERTED = "fk_sales_leads_converted_hospital_id_hospitals"


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def _has_fk(table_name: str, fk_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))


def _duplicate_schedule_slots(bind) -> list[tuple[str, str, int]]:
    rows = bind.execute(
        sa.text(
            "SELECT CAST(schedule_id AS TEXT), CAST(scheduled_date AS TEXT), sequence_no "
            "FROM content_items "
            "WHERE schedule_id IS NOT NULL "
            "AND scheduled_date IS NOT NULL "
            "AND sequence_no IS NOT NULL "
            "GROUP BY schedule_id, scheduled_date, sequence_no "
            "HAVING count(*) > 1"
        )
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table("hospitals") and not _has_index("hospitals", UQ_DOMAIN):
        duplicates = bind.execute(
            sa.text(
                "SELECT lower(aeo_domain) AS domain, count(*) "
                "FROM hospitals "
                "WHERE aeo_domain IS NOT NULL "
                "GROUP BY lower(aeo_domain) "
                "HAVING count(*) > 1"
            )
        ).fetchall()
        if duplicates:
            domains = ", ".join(row[0] for row in duplicates[:10])
            raise RuntimeError(
                "Cannot create unique hospital aeo_domain index; duplicate domain(s): "
                f"{domains}"
            )
        op.create_index(
            UQ_DOMAIN,
            "hospitals",
            [sa.text("lower(aeo_domain)")],
            unique=True,
            postgresql_where=sa.text("aeo_domain IS NOT NULL"),
        )

    if _has_table("content_items") and not _has_index("content_items", UQ_SLOT):
        duplicates = _duplicate_schedule_slots(bind)
        if duplicates:
            slots = ", ".join(f"{schedule_id}:{scheduled_date}:{sequence_no}" for schedule_id, scheduled_date, sequence_no in duplicates[:10])
            raise RuntimeError(
                "Cannot create unique content schedule slot index; duplicate slot(s): "
                f"{slots}"
            )
        op.create_index(
            UQ_SLOT,
            "content_items",
            ["schedule_id", "scheduled_date", "sequence_no"],
            unique=True,
        )

    if _has_table("sales_leads"):
        bind.execute(
            sa.text(
                "UPDATE sales_leads "
                "SET converted_hospital_id = NULL "
                "WHERE converted_hospital_id IS NOT NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM hospitals WHERE hospitals.id = sales_leads.converted_hospital_id"
                ")"
            )
        )
        if not _has_index("sales_leads", IX_LEAD_CONVERTED):
            op.create_index(IX_LEAD_CONVERTED, "sales_leads", ["converted_hospital_id"])
        if not _has_fk("sales_leads", FK_LEAD_CONVERTED):
            op.create_foreign_key(
                FK_LEAD_CONVERTED,
                "sales_leads",
                "hospitals",
                ["converted_hospital_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if _has_table("sales_leads"):
        if _has_fk("sales_leads", FK_LEAD_CONVERTED):
            op.drop_constraint(FK_LEAD_CONVERTED, "sales_leads", type_="foreignkey")
        if _has_index("sales_leads", IX_LEAD_CONVERTED):
            op.drop_index(IX_LEAD_CONVERTED, table_name="sales_leads")

    if _has_table("content_items") and _has_index("content_items", UQ_SLOT):
        op.drop_index(UQ_SLOT, table_name="content_items")

    if _has_table("hospitals") and _has_index("hospitals", UQ_DOMAIN):
        op.drop_index(UQ_DOMAIN, table_name="hospitals")
