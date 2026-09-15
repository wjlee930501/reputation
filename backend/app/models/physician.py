"""병원 의료진 행 — 공동 대표원장이 있는 병원을 하나의 이름 문자열로 뭉개지 않는다.

`hospitals.director_*`는 원장이 한 명이라는 전제에서 만들어졌다. 공동원장 2명은
`director_name="김성열 · 전상훈"`처럼 한 칸에 들어갈 수밖에 없었고, 사진·약력·자격은
한 명분만 남았다. 의료진을 행으로 두고, 병원 단위 컬럼은 대표 행에서 파생해 동기화한다
(`app/services/hospital_physicians.py`). 기존 공개 표면·llms.txt가 읽는 `director_name`은
그대로 유지된다.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.hospital import Hospital


class HospitalPhysician(Base):
    __tablename__ = "hospital_physicians"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospitals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(50))  # 직함: 대표원장/원장/과장…
    specialties: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    career: Mapped[str | None] = mapped_column(Text)
    # `hospitals.director_credentials`와 같은 형태:
    # {"medical_school": ..., "board_certifications": [...], "society_memberships": [...],
    #  "license_number": ...}  — license_number는 공개 노출 X.
    credentials: Mapped[dict | None] = mapped_column(JSON)
    # 원장 사진은 이미 검수된 근거 자료다. 별도 업로드 경로를 만들지 않고 그 행을 가리킨다.
    # 자료가 지워져도 의료진 행은 남아야 하므로 SET NULL.
    photo_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_source_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    is_representative: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hospital: Mapped["Hospital"] = relationship(back_populates="physicians")

    def __repr__(self) -> str:
        return f"<HospitalPhysician {self.name} [{self.title}]>"
