"""병원 현황 화면이 한 번에 받는 응답 (설계 §4.3 "현황", §4.5).

세 상태 카드 · 예외 카드 · 이번 달 요약을 한 호출로 준다. 화면이 8~10번 나눠 부르면
같은 순간의 병원 상태가 카드마다 다른 시각으로 섞인다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.operations import OperationsAction


class RemainingCondition(BaseModel):
    """아직 채워지지 않은 조건 하나. `href`는 사람 몫일 때만 준다 — 자동 진행 중인 일을
    운영자의 할 일로 만들지 않는다."""

    key: str
    label: str
    actor: Literal["human", "system"]
    href: str | None


class StateCard(BaseModel):
    kind: str
    label: str
    remaining: list[RemainingCondition]
    reason: str | None = None
    last_checked_at: datetime | None = None
    # 마지막 관측의 성패 — 화면이 "마지막 확인 …"에 응답 정상/실패를 붙이는 근거다.
    last_check_ok: bool | None = None


class ExceptionCard(BaseModel):
    """사람이 손대야 풀리는 건.

    `actions`는 운영 센터가 이 인시던트에 대해 실제로 등록한 mutation 서술자 그대로다 —
    카드가 자기만의 경로·조건을 만들면 버튼과 서버 인가가 갈린다. 지금 실행할 수 없는
    행동도 `enabled=False`로 함께 실어 왜 못 하는지 보여줄 수 있게 한다.
    `allowed_actions`는 그중 실행 가능한 코드만 담은 하위 호환 요약이다.
    """

    kind: Literal["incident", "escalated_draft"]
    id: str
    title: str
    evidence: str | None
    next_action: str
    allowed_actions: list[str]
    href: str
    hospital_id: uuid.UUID
    incident_id: uuid.UUID | None = None
    operation_run_id: uuid.UUID | None = None
    content_id: uuid.UUID | None = None
    # 인시던트의 낙관적 잠금 값. 상태를 바꾸는 행동은 이 값을 함께 보내야 한다.
    version: int | None = None
    # 이 카드에 접힌 같은 원인 인시던트 수. 행동은 대표 인시던트 하나에만 적용되므로
    # 2건 이상이면 화면이 그 사실을 말해야 한다.
    same_type_count: int = 1
    actions: list[OperationsAction] = []


class MonthSummary(BaseModel):
    """이번 달 발행 실적.

    `public_count`는 지금 실제로 공개 페이지에 나가는 글이며 `published_count`
    (DB PUBLISHED)와 같지 않다. `withheld_count`는 "발행했지만 지금 공개 페이지에서
    볼 수 없는" 글 수다 — 병원이 공개 서비스 중이 아니면(일시정지·준비 중) 공개 API가
    아무것도 내보내지 않으므로 발행 글 전부가 여기 들어간다.

    `mention_rate`는 마지막으로 실제 측정된 주의 값이고 `mention_rate_measured_at`은
    그 주의 시작일이다. 측정은 월 1회, 추이 버킷은 롤링 7일이라 "이번 주" 값은 거의 항상
    비어 있다 — 값과 함께 언제 잰 값인지 보여주지 않으면 지난달 수치가 오늘 수치가 된다.
    """

    year: int
    month: int
    published_count: int
    public_count: int
    withheld_count: int
    planned_total: int
    mention_rate: float | None
    mention_rate_measured_at: date | None
    next_report_date: date


class HospitalOverviewResponse(BaseModel):
    hospital_id: uuid.UUID
    public_service: StateCard
    content: StateCard
    domain: StateCard
    exceptions: list[ExceptionCard]
    month: MonthSummary
