"""Operator-safe Slack copy for a hospital's nightly content preparation result."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.services.notification_contracts import SlackMessage
from app.services.notification_milestone_rendering import (
    RenderedSlackMessage,
    action_block,
    admin_url,
    header_block,
    safe_text,
    section_block,
    validated_message,
)


@dataclass(frozen=True, slots=True)
class ContentBatchSummary:
    hospital_id: uuid.UUID
    hospital_name: str
    scheduled_date: str
    generated: int
    failed: int = 0
    skipped: int = 0
    cost_blocked: int = 0
    discarded: int = 0
    image_missing: int = 0

    @property
    def has_activity(self) -> bool:
        return any(
            count > 0
            for count in (
                self.generated,
                self.failed,
                self.skipped,
                self.cost_blocked,
                self.discarded,
                self.image_missing,
            )
        )

    @property
    def needs_action(self) -> bool:
        return any(
            count > 0
            for count in (
                self.failed,
                self.skipped,
                self.cost_blocked,
                self.discarded,
                self.image_missing,
            )
        )


def build_content_batch_message(
    summary: ContentBatchSummary,
    *,
    admin_base_url: str,
) -> SlackMessage:
    """Build one message with one exact, allowlisted operator action."""

    hospital_name = safe_text(summary.hospital_name, 80)
    scheduled_date = safe_text(summary.scheduled_date, 30)
    destination = admin_url(
        admin_base_url,
        f"/hospitals/{summary.hospital_id}/content",
    )
    result_lines = _result_lines(summary)
    if summary.needs_action:
        problem = "일부 콘텐츠가 발행 준비를 마치지 못했습니다."
        impact = "준비되지 않은 콘텐츠는 예정일 공개가 늦어질 수 있습니다."
        next_action = (
            "아래 ‘콘텐츠 상태 확인’을 눌러 실패하거나 대기 중인 항목을 확인해 주세요. "
            "다시 시도한 뒤에도 실패하면 운영 센터에서 개발팀 문의용 정보를 복사해 전달해 주세요."
        )
    else:
        problem = "발행 준비를 막는 문제가 확인되지 않았습니다."
        impact = "현재 확인된 고객 공개 일정 영향은 없습니다."
        next_action = "필요하면 아래 ‘콘텐츠 상태 확인’을 눌러 저장된 초안을 검수해 주세요."

    body = "\n".join(
        (
            f"*{hospital_name}* · 발행 예정일 {scheduled_date}",
            f"*무슨 문제인지:* {problem}",
            f"*고객 영향:* {impact}",
            f"*지금 할 일:* {next_action}",
            f"*처리 기한:* {scheduled_date} 공개 전",
            "",
            "*처리 결과*",
            *result_lines,
        )
    )
    fallback = (
        f"야간 콘텐츠 준비 결과 · {hospital_name} · "
        f"무슨 문제인지: {problem} · 고객 영향: {impact} · "
        f"지금 할 일: 콘텐츠 상태 확인 · 처리 기한: {scheduled_date} 공개 전"
    )
    return validated_message(
        RenderedSlackMessage(
            fallback_text=fallback,
            blocks=(
                header_block("content-batch-header", "야간 콘텐츠 준비 결과"),
                section_block("content-batch-summary", body),
                action_block("content-batch-action", destination, "콘텐츠 상태 확인"),
            ),
            admin_url=destination,
        ),
        admin_base_url,
    )


def _result_lines(summary: ContentBatchSummary) -> tuple[str, ...]:
    labels = (
        (summary.generated, "초안 저장 완료"),
        (summary.failed, "초안 생성 실패"),
        (summary.skipped, "콘텐츠 운영 기준 승인 대기"),
        (summary.cost_blocked, "자동 작업 안전장치로 대기"),
        (summary.discarded, "운영자 변경으로 결과 미적용"),
        (summary.image_missing, "대표 이미지 생성 필요"),
    )
    return tuple(f"• {label}: {count}건" for count, label in labels if count > 0)
