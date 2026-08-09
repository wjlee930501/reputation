import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, event, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import AttributeEventToken, Mapped, mapped_column
from sqlalchemy.orm.attributes import NEVER_SET, NO_VALUE

from app.core.database import Base
from app.models.hospital import Plan


class HandoffState(str, enum.Enum):
    CONTRACT_PENDING = "CONTRACT_PENDING"
    CONTRACTED = "CONTRACTED"
    HANDOFF_ACCEPTED = "HANDOFF_ACCEPTED"


class HandoffSource(str, enum.Enum):
    DIRECT_CREATE = "DIRECT_CREATE"
    LEAD_CONVERSION = "LEAD_CONVERSION"
    LEGACY_BACKFILL = "LEGACY_BACKFILL"


class HospitalHandoff(Base):
    """Mutable persisted handoff with database-backed optimistic versioning."""

    __tablename__ = "hospital_handoffs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CONTRACT_PENDING', 'CONTRACTED', 'HANDOFF_ACCEPTED')",
            name="ck_hospital_handoffs_state",
        ),
        CheckConstraint(
            "acceptance_source IN ('DIRECT_CREATE', 'LEAD_CONVERSION', 'LEGACY_BACKFILL')",
            name="ck_hospital_handoffs_acceptance_source",
        ),
        CheckConstraint(
            "acceptance_source <> 'LEGACY_BACKFILL' OR state = 'HANDOFF_ACCEPTED'",
            name="ck_hospital_handoffs_legacy_is_accepted",
        ),
        CheckConstraint(
            "(state = 'CONTRACT_PENDING' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NULL AND contract_effective_at IS NULL "
            "AND plan IS NULL AND sla_due_at IS NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NULL) "
            "OR (state = 'CONTRACTED' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NOT NULL AND contract_effective_at IS NOT NULL "
            "AND plan IS NOT NULL AND sla_due_at IS NOT NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NULL) "
            "OR (state = 'HANDOFF_ACCEPTED' AND acceptance_source = 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NULL AND ae_owner_id IS NULL "
            "AND contract_reference IS NULL AND contract_effective_at IS NULL "
            "AND plan IS NULL AND sla_due_at IS NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NOT NULL) "
            "OR (state = 'HANDOFF_ACCEPTED' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NOT NULL AND contract_effective_at IS NOT NULL "
            "AND plan IS NOT NULL AND sla_due_at IS NOT NULL AND accepted_by_id IS NOT NULL "
            "AND accepted_at IS NOT NULL)",
            name="ck_hospital_handoffs_state_facts",
        ),
        CheckConstraint(
            "plan IS NULL OR plan IN ('PLAN_12', 'PLAN_16', 'PLAN_20')",
            name="ck_hospital_handoffs_plan",
        ),
        CheckConstraint("version >= 1", name="ck_hospital_handoffs_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    state: Mapped[HandoffState] = mapped_column(
        Enum(HandoffState, native_enum=False, create_constraint=False, length=32),
        nullable=False,
        default=HandoffState.CONTRACT_PENDING,
        server_default=HandoffState.CONTRACT_PENDING.value,
    )
    sales_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True
    )
    ae_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True
    )
    contract_reference: Mapped[str | None] = mapped_column(String(200))
    contract_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[Plan | None] = mapped_column(
        Enum(Plan, native_enum=False, create_constraint=False, length=16)
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acceptance_source: Mapped[HandoffSource] = mapped_column(
        Enum(HandoffSource, native_enum=False, create_constraint=False, length=32),
        nullable=False,
        default=HandoffSource.DIRECT_CREATE,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __mapper_args__ = {"version_id_col": version}

    @classmethod
    def pending(
        cls,
        hospital_id: uuid.UUID,
        *,
        sales_owner_id: uuid.UUID,
        ae_owner_id: uuid.UUID,
        source: HandoffSource = HandoffSource.DIRECT_CREATE,
    ) -> "HospitalHandoff":
        return cls(
            hospital_id=hospital_id,
            state=HandoffState.CONTRACT_PENDING,
            acceptance_source=source,
            sales_owner_id=sales_owner_id,
            ae_owner_id=ae_owner_id,
        )


def _accepted_fact_cannot_change(
    _target: HospitalHandoff,
    value: uuid.UUID | datetime | None,
    old_value: uuid.UUID | datetime | None,
    _initiator: AttributeEventToken,
) -> uuid.UUID | datetime | None:
    if old_value not in (None, NEVER_SET, NO_VALUE) and value != old_value:
        raise ValueError("accepted_by_id and accepted_at are immutable once set")
    return value


event.listen(HospitalHandoff.accepted_by_id, "set", _accepted_fact_cannot_change, retval=True)
event.listen(HospitalHandoff.accepted_at, "set", _accepted_fact_cannot_change, retval=True)
