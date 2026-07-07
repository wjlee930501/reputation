"""Admin operations 제어 평면 스키마 — 비용 가드 조회/킬스위치."""
from pydantic import BaseModel


class CostGuardCategoryUsage(BaseModel):
    category: str
    label: str
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int


class CostGuardStatusResponse(BaseModel):
    enabled: bool
    kill_switch_active: bool
    categories: list[CostGuardCategoryUsage]


class CostGuardKillSwitchRequest(BaseModel):
    enabled: bool


class CostGuardKillSwitchResponse(BaseModel):
    kill_switch_active: bool
