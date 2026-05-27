from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.services.admin_passwords import verify_admin_password

router = APIRouter(prefix="/admin/auth", tags=["Admin — Auth"])


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=500)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned:
            raise ValueError("valid email is required")
        return cleaned


class AdminAccountResponse(BaseModel):
    id: UUID
    email: str
    name: str
    role: str


@router.post("/login", response_model=AdminAccountResponse)
async def login_admin(body: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not verify_admin_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    user.last_login_at = datetime.now(UTC)
    await db.commit()
    return AdminAccountResponse(id=user.id, email=user.email, name=user.name, role=user.role)
