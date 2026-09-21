"""One notification-type label in front of every `#mkt-reputation` projection.

운영 채널의 메시지는 사람이 제목 한 줄만 보고 "지금 읽어야 하는가"를 정한다. 세 라벨은
수신자가 먼저 갈라내는 축(새 영업 기회 / 사람이 고칠 문제 / 읽고 넘길 현황)이며, 라벨
문구와 이벤트 분류는 이 파일 하나에만 있다. Slack fallback text와 Block Kit header에
같은 라벨을 붙여 알림 목록과 펼친 메시지가 같은 분류를 말하게 한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

LEAD_LABEL: Final = "[Lead : 도입 문의]"
ERROR_LABEL: Final = "[Error : 오류 발생]"
REPORT_LABEL: Final = "[Report : 운영 현황 보고]"


class NotificationLabel(StrEnum):
    """수신자가 메시지를 갈라내는 세 축."""

    LEAD = LEAD_LABEL
    ERROR = ERROR_LABEL
    REPORT = REPORT_LABEL


_ALL_LABELS: Final = tuple(label.value for label in NotificationLabel)

# 알림 종류 하나에 라벨 하나. 값은 그 알림이 사람에게 요구하는 것으로 정한다 —
# 실패·차단·조치 필요는 ERROR, 접수는 LEAD, 복구·현황 보고는 REPORT다. 자동 복구
# 사실(`INCIDENT_RECOVERED`)은 사람이 할 일이 없으므로 ERROR가 아니라 REPORT다.
EVENT_LABELS: Final[dict[str, NotificationLabel]] = {
    # 도입 문의·무료 진단 접수
    "LEAD_CREATED": NotificationLabel.LEAD,
    "LEAD_DIAGNOSIS_RECEIVED": NotificationLabel.LEAD,
    # 조치가 필요한 실패·차단
    "INCIDENT_OPEN": NotificationLabel.ERROR,
    "INCIDENT_SUMMARY": NotificationLabel.ERROR,
    "MILESTONE_ACTION": NotificationLabel.ERROR,
    # 마일스톤 요약은 항목별 확인을 요구하는 운영자 큐 투영이다.
    "MILESTONE_SUMMARY": NotificationLabel.ERROR,
    "CONTENT_PUBLISHED": NotificationLabel.ERROR,
    "MISSING_APPROVED_ESSENCE_DIGEST": NotificationLabel.ERROR,
    "GENERATION_BLOCKED_DIGEST": NotificationLabel.ERROR,
    "CONTENT_BATCH_BLOCKED": NotificationLabel.ERROR,
    "ONBOARDING_SITE_BUILT": NotificationLabel.ERROR,
    "MONTHLY_REPORT_GAP_SUMMARY": NotificationLabel.ERROR,
    "COST_GUARD_LIMIT_REACHED": NotificationLabel.ERROR,
    "COST_GUARD_SOFT_WARNING": NotificationLabel.ERROR,
    "PRIVACY_RETENTION_FAILED": NotificationLabel.ERROR,
    "PIPELINE_WATCHDOG_ALERT": NotificationLabel.ERROR,
    # 복구·현황 보고
    "INCIDENT_RECOVERED": NotificationLabel.REPORT,
    "MILESTONE_RECOVERED": NotificationLabel.REPORT,
    # 주간 요약의 본문은 계약 예정 대비 발행 수율이며 차단이 0건이어도 나간다.
    "GENERATION_REJECTION_WEEKLY_ROLLUP": NotificationLabel.REPORT,
    "CONTENT_BATCH_PREPARED": NotificationLabel.REPORT,
    "ONBOARDING_V0_READY": NotificationLabel.REPORT,
    "ONBOARDING_HOSPITAL_ACTIVATED": NotificationLabel.REPORT,
    "FLEET_HEARTBEAT": NotificationLabel.REPORT,
    "NAVER_WEEKLY_HANDOFF": NotificationLabel.REPORT,
    "NAVER_SOURCE_RECOVERED": NotificationLabel.REPORT,
    "PRIVACY_RETENTION_COMPLETED": NotificationLabel.REPORT,
    "PIPELINE_WATCHDOG_RECOVERY": NotificationLabel.REPORT,
}


def label_for_event(notification_type: str) -> NotificationLabel:
    """Return the channel label for one notification kind.

    등록되지 않은 종류는 ERROR로 읽는다. 라벨이 없다고 전달을 실패시키지 않되, 분류가
    빠진 사실을 채널에서 눈에 띄게 남긴다. 새 알림을 추가할 때는 `EVENT_LABELS`에
    함께 등록한다(`tests/test_notification_labels.py`가 누락을 잡는다).
    """

    return EVENT_LABELS.get(notification_type, NotificationLabel.ERROR)


def prefixed(label: NotificationLabel, text: str) -> str:
    """Put the label in front of copy that already says what happened.

    기존 문구는 한 글자도 바꾸지 않고 라벨만 앞에 붙인다. 이미 라벨이 붙은 문구는
    다시 붙이지 않으므로, 한 투영이 다른 투영의 문구를 감싸도 라벨이 겹치지 않는다.
    """

    body = text.lstrip()
    if body.startswith(_ALL_LABELS):
        return body
    return f"{label.value} {body}" if body else label.value


def prefixed_for_event(notification_type: str, text: str) -> str:
    """Prefix copy with the label registered for one notification kind."""

    return prefixed(label_for_event(notification_type), text)
