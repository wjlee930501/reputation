"""원장 사전 설문 — AE가 병원 정보에서 링크를 만들고 원장이 직접 작성한다.

병원마다 한 행이다. 링크는 불투명 토큰이고 원문은 저장하지 않는다(`token_hash`만). 토큰은
`(병원 id, token_version)`에서 결정적으로 다시 만들 수 있어 AE가 언제든 같은 링크를 복사할 수
있고, 새 링크를 만들면 `token_version`이 올라 옛 링크는 열리지 않는다
(`app/services/doctor_survey_token.py`).

답변은 작성 중에도 서버에 저장된다(`answers`). 근거 자료(설문 자료 한 행,
`app/services/doctor_survey_source.py`)에는 **제출한** 답만 들어간다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DoctorSurvey(Base):
    __tablename__ = "doctor_surveys"
    __table_args__ = (
        UniqueConstraint("hospital_id", name="uq_doctor_surveys_hospital_id"),
        UniqueConstraint("token_hash", name="uq_doctor_surveys_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospitals.id", ondelete="CASCADE"), nullable=False
    )
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    link_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    link_issued_by: Mapped[str] = mapped_column(String(255), nullable=False)
    # 새로 개원한 병원이면 '개원 계기' 문항을 보인다(링크를 만들 때 AE가 정한다).
    new_clinic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    schema_version: Mapped[str | None] = mapped_column(String(40))
    answers: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # 원장이 마지막으로 본 화면 — 다시 열면 그 화면부터 이어 쓴다.
    current_step: Mapped[str | None] = mapped_column(String(400))
    # 화면(한 번 연 페이지)마다 임의의 id와, 그 화면이 저장할 때마다 1씩 올리는 순번. 같은 화면에서 늦게 도착한
    # 옛 저장(느린 요청·화면 떠날 때 보낸 요청)이 새 답을 덮지 않게, 같은 화면의 더 작은 순번은 받지 않는다.
    # 다른 화면(다른 기기)의 저장은 순번을 비교하지 않는다 — 기기 시계가 달라도 저장이 버려지지 않는다.
    client_session: Mapped[str | None] = mapped_column(String(40))
    client_seq: Mapped[int | None] = mapped_column(BigInteger)
    respondent: Mapped[str | None] = mapped_column(String(20))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(40))
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # 마지막으로 제출한 답(근거 자료에 실린 답). 제출 뒤 고친 답이 있는지 비교하는 데 쓴다.
    submitted_answers: Mapped[dict | None] = mapped_column(JSONB)
    interview_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hospital_source_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
