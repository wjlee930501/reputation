import sqlalchemy as sa
from alembic import op

revision = "0027_add_domain_management_metadata"
down_revision = "0026_add_content_carried_over_from"
branch_labels = None
depends_on = None


DOMAIN_MANAGEMENT_MODE = "domainmanagementmode"
DOMAIN_DNS_STRATEGY = "domaindnsstrategy"


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _enum_type(name: str, values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    if not _has_table("hospitals"):
        return

    management_mode = _enum_type(
        DOMAIN_MANAGEMENT_MODE,
        ("HOSPITAL_MANAGED", "MOTIONLABS_MANAGED"),
    )
    dns_strategy = _enum_type(DOMAIN_DNS_STRATEGY, ("CNAME", "APEX_ADDRESS"))
    bind = op.get_bind()
    management_mode.create(bind, checkfirst=True)
    dns_strategy.create(bind, checkfirst=True)

    if not _has_column("hospitals", "domain_management_mode"):
        op.add_column(
            "hospitals",
            sa.Column(
                "domain_management_mode",
                management_mode,
                nullable=False,
                server_default="HOSPITAL_MANAGED",
            ),
        )
    if not _has_column("hospitals", "domain_dns_strategy"):
        op.add_column(
            "hospitals",
            sa.Column(
                "domain_dns_strategy",
                dns_strategy,
                nullable=False,
                server_default="CNAME",
            ),
        )
    if not _has_column("hospitals", "domain_registrar"):
        op.add_column("hospitals", sa.Column("domain_registrar", sa.String(length=200), nullable=True))
    if not _has_column("hospitals", "domain_dns_provider"):
        op.add_column("hospitals", sa.Column("domain_dns_provider", sa.String(length=200), nullable=True))
    if not _has_column("hospitals", "domain_purchase_note"):
        op.add_column("hospitals", sa.Column("domain_purchase_note", sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_table("hospitals"):
        return

    for column_name in (
        "domain_purchase_note",
        "domain_dns_provider",
        "domain_registrar",
        "domain_dns_strategy",
        "domain_management_mode",
    ):
        if _has_column("hospitals", column_name):
            op.drop_column("hospitals", column_name)

    _enum_type(DOMAIN_DNS_STRATEGY, ("CNAME", "APEX_ADDRESS")).drop(op.get_bind(), checkfirst=True)
    _enum_type(
        DOMAIN_MANAGEMENT_MODE,
        ("HOSPITAL_MANAGED", "MOTIONLABS_MANAGED"),
    ).drop(op.get_bind(), checkfirst=True)
