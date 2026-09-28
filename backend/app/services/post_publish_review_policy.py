"""Human review policy for content that already passed automatic publication checks."""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Final

from sqlalchemy import and_, false, or_
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus

logger = logging.getLogger(__name__)

AUTO_PUBLISHABLE_STATUSES: Final = (ContentStatus.DRAFT, ContentStatus.READY)
AUTO_PUBLISH_CATCHUP_DAYS: Final = 7

# 08:00~23:00 자동 발행이 한 글을 게이트에서 막을 때마다 남기는 감사 기록의 action.
# 이 행 하나가 "그 시각에 발행기가 실제로 이 글을 열어 보고 막았다"는 유일한 외부
# 증거라, 외부 감시가 '실행되지 않음'과 '실행됐지만 게이트가 남았음'을 구분하는 데
# 쓴다. 07:45 사전 마감 게이트는 이 action을 쓰지 않는다(소유자는 발행기 하나다).
AUTO_PUBLISH_BLOCKED_ACTION: Final = "auto_publish_blocked"

# The automatic review remains the publication gate. Human review is observability sampling,
# not a second approval queue — it never blocks monthly report delivery. Keep the sample small:
# one baseline item per monthly sequence, plus any item whose body was edited after publication
# (an AE actually changed something an automated check would not have caught). An item the
# automatic safety gate already remediated is evidence the gate worked, not a reason to sample
# it again, so remediation counts are excluded here.
POST_PUBLISH_SAMPLE_SEQUENCE: Final = 1


def auto_publish_catchup_start(today):
    """Return the oldest scheduled date the autonomous publisher may still catch up."""
    return today - timedelta(days=AUTO_PUBLISH_CATCHUP_DAYS)


@dataclass(frozen=True, slots=True)
class AutoPublishHold:
    """`AUTO_PUBLISH_HOLD_HOSPITALS`를 해석한 결과 — 예약 자동 발행만 멈춘다."""

    all_hospitals: bool
    hospital_ids: frozenset[uuid.UUID]

    @property
    def active(self) -> bool:
        return self.all_hospitals or bool(self.hospital_ids)

    def holds(self, hospital_id) -> bool:
        return self.all_hospitals or hospital_id in self.hospital_ids


@lru_cache(maxsize=8)
def parse_auto_publish_hold(raw: str | None) -> AutoPublishHold:
    """`""`=꺼짐, `"*"`=전체, 그 밖에는 쉼표로 나열한 병원 UUID.

    공백과 빈 항목은 무시한다. UUID가 아닌 항목은 경고를 남기고 무시한다 — 보류 설정의
    오타 하나가 발행기·운영 센터·감시를 예외로 멈추게 하지 않는다(대신 그 병원은 보류되지
    않으므로 경고 로그로 드러낸다). 같은 값은 프로세스당 한 번만 해석·경고한다.
    """
    hospital_ids: set[uuid.UUID] = set()
    all_hospitals = False
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if token == "*":
            all_hospitals = True
            continue
        try:
            hospital_ids.add(uuid.UUID(token))
        except ValueError:
            logger.warning("AUTO_PUBLISH_HOLD_HOSPITALS: ignoring invalid hospital id %r", token)
    return AutoPublishHold(all_hospitals=all_hospitals, hospital_ids=frozenset(hospital_ids))


def auto_publish_hold() -> AutoPublishHold:
    """현재 설정의 자동 발행 보류 — 호출 시점에 읽는다(워커는 매 실행마다 다시 본다)."""
    return parse_auto_publish_hold(settings.AUTO_PUBLISH_HOLD_HOSPITALS)


def auto_publish_due_predicate(today) -> ColumnElement[bool]:
    """SQL predicate shared by worker and Operations Center for due publish slots.

    자동 발행 보류(`AUTO_PUBLISH_HOLD_HOSPITALS`)도 여기서만 거른다 — 발행기·07:45 게이트·
    감시·운영 센터가 같은 조건을 쓰므로, 보류 중인 글에는 페이지·거짓 누락 알람이 없다.
    보류가 꺼져 있으면 조건은 이전과 똑같다.
    """
    conditions = [
        ContentItem.scheduled_date <= today,
        ContentItem.scheduled_date >= auto_publish_catchup_start(today),
        ContentItem.status.in_(AUTO_PUBLISHABLE_STATUSES),
    ]
    hold = auto_publish_hold()
    if hold.all_hospitals:
        conditions.append(false())
    elif hold.hospital_ids:
        conditions.append(ContentItem.hospital_id.notin_(sorted(hold.hospital_ids, key=str)))
    return and_(*conditions)


def publicly_operational_hospital_predicate() -> ColumnElement[bool]:
    """Hospital boundary shared by publishing, review queues, and escalation."""
    return and_(
        Hospital.status == HospitalStatus.ACTIVE,
        Hospital.site_live.is_(True),
    )


def human_post_publish_review_predicate() -> ColumnElement[bool]:
    """Return the SQL predicate for the small, non-blocking human quality sample."""
    return and_(
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.post_publish_reviewed_at.is_(None),
        ContentItem.published_at.is_not(None),
        or_(
            ContentItem.sequence_no == POST_PUBLISH_SAMPLE_SEQUENCE,
            ContentItem.body_updated_at > ContentItem.published_at,
        ),
    )


def is_human_post_publish_review_sample(item: ContentItem) -> bool:
    """Return whether a published item belongs to the same human review sample.

    The SQL predicate above is for the live Operations queue, so it also excludes
    already-reviewed rows. Monthly reporting needs the same sample boundary but must count
    both reviewed and pending samples at the reporting cutoff.
    """
    if item.status != ContentStatus.PUBLISHED or item.published_at is None:
        return False
    return item.sequence_no == POST_PUBLISH_SAMPLE_SEQUENCE or (
        item.body_updated_at is not None and item.body_updated_at > item.published_at
    )
