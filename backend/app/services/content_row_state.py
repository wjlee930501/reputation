"""월 표의 행 상태 — 글 하나가 지금 어떤 상태인지 정하는 단일 함수.

공개 여부는 공개 사이트와 같은 판정(`content_visibility.assess_public_visibility`)을
그대로 쓴다. 이 파일이 더하는 것은 "그래서 사람이 볼 화면에 뭐라고 쓰는가"와
"차단이면 운영 센터의 어디로 가는가"뿐이다. 화면이 자기만의 규칙으로 상태를 다시
계산하면 admin은 "공개 중"인데 공개 페이지에는 없는 글이 생긴다(H-01).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from app.models.content import ContentStatus
from app.services.content_visibility import PublicVisibility

RowStateKind = Literal["public", "withheld", "scheduled", "generating", "blocked", "closed"]

ROW_STATE_LABELS: Final[dict[str, str]] = {
    "public": "공개 중",
    "withheld": "공개 보류",
    "scheduled": "예정",
    "generating": "초안 생성 중",
    "blocked": "차단",
    "closed": "종료",
}


@dataclass(frozen=True, slots=True)
class RowState:
    """행 하나의 판정 — 상태값, 사람이 읽을 사유, 운영 센터로 가는 링크."""

    kind: RowStateKind
    reason: str | None
    link: dict[str, Any] | None


def content_row_state(
    item: Any,
    visibility: PublicVisibility,
    *,
    compliance_blockers: Sequence[str],
    blocked_link: dict[str, Any] | None,
    today: date,
) -> RowState:
    """글 하나의 월 표 상태. `blocked_link`는 인시던트/차단 run이 있을 때만 온다."""
    status = getattr(item, "status", None)
    status_value = getattr(status, "value", status)

    if status_value == ContentStatus.PUBLISHED.value:
        if visibility.visible:
            # 공개 중인 글에는 조치할 것이 없다 — 지난 실패 run의 링크를 달지 않는다.
            return RowState("public", None, None)
        return RowState("withheld", " · ".join(visibility.blocker_labels) or None, blocked_link)

    if status_value == ContentStatus.CANCELLED.value:
        return RowState("closed", "종료됨", None)

    # 미발행 글 — 자동 복구가 도는 중인지, 사람이 볼 차단인지를 링크가 가른다.
    blockers_text = " · ".join(compliance_blockers) or None
    if blocked_link is not None:
        return RowState("blocked", blocked_link.get("next_action") or blockers_text, blocked_link)

    if status_value == ContentStatus.REJECTED.value:
        return RowState("generating", "야간 재생성 대기", None)

    if not (getattr(item, "title", None) or "").strip() or not (
        getattr(item, "body", None) or ""
    ).strip():
        return RowState("generating", "발행 전날 23:00 자동 생성", None)

    if compliance_blockers:
        return RowState("blocked", blockers_text, None)

    scheduled_date = getattr(item, "scheduled_date", None)
    if scheduled_date is not None and scheduled_date < today:
        # 08:00 자동 공개가 지나갔는데 아직 초안이다. "예정"으로 두면 이미 놓친 날짜가
        # 앞으로 처리될 일처럼 보인다.
        return RowState("blocked", "발행일이 지났지만 아직 공개되지 않았습니다.", None)

    return RowState("scheduled", None, None)
