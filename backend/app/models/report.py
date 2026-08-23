import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.hospital import Hospital


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


# `report_type` 값. 계약 문서(원장 보고 자료)로서 성격이 다르다 — V0는 계약 직후 한 번
# 만드는 초기 진단이고 MONTHLY는 매월 마감 후 만드는 정기 리포트다. 둘을 섞어 세면
# 운영 몇 달째 병원이 월간 리포트 덕에 초기 진단을 건너뛴 채로 완료 표시된다.
V0_REPORT_TYPE = "V0"
MONTHLY_REPORT_TYPE = "MONTHLY"


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"
    __table_args__ = (
        UniqueConstraint(
            "hospital_id",
            "period_year",
            "period_month",
            "report_type",
            "version",
            name="uq_monthly_reports_period_version",
        ),
        UniqueConstraint("supersedes_report_id", name="uq_monthly_reports_supersedes"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="CASCADE"))

    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), default="MONTHLY")  # V0 | MONTHLY
    manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monthly_measurement_manifests.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    supersedes_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("monthly_reports.id", ondelete="RESTRICT")
    )
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(
        String(30), default="LEGACY_UNVERIFIED", server_default="LEGACY_UNVERIFIED", nullable=False
    )
    planned_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    success_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    failed_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    excluded_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    customer_ready: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    delivery_blockers: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )

    pdf_path: Mapped[str | None] = mapped_column(String(500))
    # 원장에게 전달하는 1페이지 판본. 같은 데이터를 다른 편집으로 렌더한 별도 파일이라
    # AE용(pdf_path)과 함께 보관한다 — 하나를 두 독자에게 맞추면 양쪽 다 어정쩡해진다.
    doctor_pdf_path: Mapped[str | None] = mapped_column(String(500))
    sov_summary: Mapped[dict | None] = mapped_column(JSON)
    content_summary: Mapped[dict | None] = mapped_column(JSON)
    essence_summary: Mapped[dict | None] = mapped_column(_jsonb_type())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    hospital: Mapped["Hospital"] = relationship(back_populates="monthly_reports")
