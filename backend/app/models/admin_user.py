import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 운영자 역할. 인가 자체는 공유 X-Admin-Key로 이뤄지므로(core/security.py) 이 값은
# "백엔드 API 접근 권한"이 아니라 "Admin 콘솔에서 계정을 관리할 수 있는가"를 가른다.
# 기존 행은 전부 OWNER(컬럼 기본값)라 도입 시점에 권한이 축소되는 계정은 없다.
ROLE_OWNER = "OWNER"
ROLE_OPERATOR = "OPERATOR"
ADMIN_ROLES: frozenset[str] = frozenset({ROLE_OWNER, ROLE_OPERATOR})


class AdminUser(Base):
    """Privileged operator account for the Admin console."""

    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="OWNER")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 이 시각 이전에 발급된 세션은 전부 무효. 발급된 토큰을 서버가 열거할 수 없어
    # 개별 폐기가 불가능하므로, 계정 단위 기준선으로 한 번에 끊는다.
    # 비밀번호 재설정(탈취 대응)과 재활성화(정지 전 세션 부활 차단)에서 갱신한다.
    sessions_invalid_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
