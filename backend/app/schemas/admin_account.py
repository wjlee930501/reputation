"""Admin 콘솔 운영자 계정 관리 스키마.

password_hash는 어떤 응답에도 실리지 않는다 — 응답 모델에 필드를 두지 않는 것이
직렬화 실수를 구조적으로 막는 유일한 방법이다.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.admin_user import ADMIN_ROLES, ROLE_OPERATOR

# admin_passwords.hash_admin_password가 14자 미만을 거부한다. 여기서 같은 하한을
# 다시 두는 이유는 422로 필드 단위 메시지를 돌려주기 위함이고, 실제 강제는 해시 함수가 한다.
MIN_ADMIN_PASSWORD_LENGTH = 14
MAX_ADMIN_PASSWORD_LENGTH = 500


def _normalize_email(value: str) -> str:
    cleaned = value.strip().lower()
    if "@" not in cleaned:
        raise ValueError("올바른 이메일 주소가 필요합니다.")
    return cleaned


def _validate_role(value: str) -> str:
    cleaned = value.strip().upper()
    if cleaned not in ADMIN_ROLES:
        raise ValueError(f"역할은 {' 또는 '.join(sorted(ADMIN_ROLES))} 중 하나여야 합니다.")
    return cleaned


class AdminAccountCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=100)
    role: str = ROLE_OPERATOR
    password: str = Field(
        min_length=MIN_ADMIN_PASSWORD_LENGTH,
        max_length=MAX_ADMIN_PASSWORD_LENGTH,
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("이름을 입력해 주세요.")
        return cleaned

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        return _validate_role(value)


class AdminAccountUpdateRequest(BaseModel):
    """부분 수정 — None인 필드는 변경하지 않는다."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("이름을 입력해 주세요.")
        return cleaned

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_role(value)


class AdminAccountPasswordRequest(BaseModel):
    password: str = Field(
        min_length=MIN_ADMIN_PASSWORD_LENGTH,
        max_length=MAX_ADMIN_PASSWORD_LENGTH,
    )


class AdminAccountResponse(BaseModel):
    id: UUID
    email: str
    name: str
    role: str
    is_active: bool
    is_operations_test: bool = False
    last_login_at: datetime | None
    created_at: datetime | None

    model_config = {"from_attributes": True}
