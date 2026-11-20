"""병원 공개 페이지 섹션 설정(레이아웃 편집기) — 초안·공개 판·이력(설계 2.1).

병원마다 초안(DRAFT) 하나와 공개 판(PUBLISHED) 하나를 둔다. 공개 반영하면 옛 공개 판은 SUPERSEDED가
되고 지우지 않는다(이력, append-only). 복원은 옛 판의 레이아웃·설정을 **새 초안**으로 복사한다.

- `config`: 기본값과 다른 칸만(편집·비교용, `site_config_schema` 저장 모양).
- `resolved`: 저장 시점의 기본값과 합친 완성본 스냅샷 — 공개 응답·이력 미리보기가 쓴다. 나중에
  `site_defaults`가 바뀌어도 옛 공개 판이 그대로 재현된다. 승인 FAQ 질문·답이나 병원 사실은 담지
  않는다(공개할 때 지금 값을 읽는다).
- `hospitals.site_layout`은 그대로 남는다. 공개 반영이 두 곳을 같은 트랜잭션에서 쓴다. 설정 행이
  없는 병원은 `site_layout`만으로 지금처럼 그린다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

SITE_CONFIG_DRAFT = "DRAFT"
SITE_CONFIG_PUBLISHED = "PUBLISHED"
SITE_CONFIG_SUPERSEDED = "SUPERSEDED"

ORIGIN_DEFAULT = "DEFAULT"
ORIGIN_EDIT = "EDIT"
ORIGIN_RESTORE = "RESTORE"
ORIGIN_RESET = "RESET"


class HospitalSiteConfig(Base):
    __tablename__ = "hospital_site_configs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED')", name="ck_hospital_site_configs_state"
        ),
        CheckConstraint("layout IN ('A', 'B', 'C')", name="ck_hospital_site_configs_layout"),
        CheckConstraint(
            "origin IN ('DEFAULT', 'EDIT', 'RESTORE', 'RESET')", name="ck_hospital_site_configs_origin"
        ),
        # 초안은 판 번호가 없고, 공개된 적 있는 판은 판 번호와 공개 시각이 있다.
        CheckConstraint(
            "(state = 'DRAFT' AND version IS NULL AND published_at IS NULL) OR "
            "(state <> 'DRAFT' AND version IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_hospital_site_configs_version_state",
        ),
        Index("ix_hospital_site_configs_hospital_created", "hospital_id", "created_at"),
        Index(
            "uq_hospital_site_configs_one_draft",
            "hospital_id",
            unique=True,
            postgresql_where=text("state = 'DRAFT'"),
        ),
        Index(
            "uq_hospital_site_configs_one_published",
            "hospital_id",
            unique=True,
            postgresql_where=text("state = 'PUBLISHED'"),
        ),
        Index(
            "uq_hospital_site_configs_version",
            "hospital_id",
            "version",
            unique=True,
            postgresql_where=text("version IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    layout: Mapped[str] = mapped_column(String(1), nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    resolved: Mapped[dict] = mapped_column(JSONB, nullable=False)
    defaults_version: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # 초안 낙관적 잠금 — 저장마다 +1. 바꾸는 요청은 {draft_id, revision} 전제조건을 보낸다.
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital_site_configs.id", ondelete="SET NULL")
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_by: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 직전 공개 판과 다른 설정 구역·섹션 키(이력 화면용, 서버 계산).
    change_summary: Mapped[list | None] = mapped_column(JSONB)
