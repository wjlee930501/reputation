"""Safe evidence projection for the Admin report review surface."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import NotificationOutbox, NotificationOutboxState, OperationRun
from app.models.report import MonthlyReport


@dataclass(frozen=True, slots=True)
class OperatorCopy:
    label: str
    problem: str
    customer_impact: str
    next_action: str


def _measurement_copy(quality: str) -> tuple[str, OperatorCopy]:
    if quality == "COMPLETE":
        return quality, OperatorCopy(
            "필수 측정 완료",
            "계획한 질문과 AI 서비스 측정이 모두 끝났습니다.",
            "측정 근거가 모두 있어 원장 보고 자료를 검토할 수 있습니다.",
            "아래 측정 근거와 원장 전달용 PDF를 확인해 주세요.",
        )
    if quality == "DEGRADED":
        return quality, OperatorCopy(
            "일부 측정 미완료",
            "계획한 측정 중 일부가 실패했습니다.",
            "이번 달 수치가 전체 계획을 충분히 대표하지 못해 원장님께 전달할 수 없습니다.",
            "운영 센터에서 실패한 측정을 확인한 뒤 리포트를 다시 만들어 주세요.",
        )
    if quality == "BLOCKED":
        return quality, OperatorCopy(
            "필수 측정 차단",
            "필수 질문 또는 AI 서비스 측정을 끝내지 못했습니다.",
            "이번 달 결과를 원장님께 전달할 수 없습니다.",
            "운영 센터에서 차단 사유를 해결한 뒤 리포트를 다시 만들어 주세요.",
        )
    return "LEGACY_UNVERIFIED", OperatorCopy(
        "측정 기준 확인 필요",
        "이전 방식으로 만든 리포트라 측정 완료 여부를 확인할 수 없습니다.",
        "이 화면만으로 수치가 전체 계획을 대표한다고 단정할 수 없습니다.",
        "운영 센터에서 측정 기록을 확인하고, 필요하면 최신 리포트를 만들어 주세요.",
    )


def _notification_copy(state: str | None) -> tuple[str, OperatorCopy]:
    if state == NotificationOutboxState.SENT.value:
        return state, OperatorCopy(
            "운영팀 알림 전달 완료",
            "이 리포트 작업에 연결된 Slack 알림이 전달됐습니다.",
            "운영팀이 알림을 받았지만, 리포트의 최종 전달 가능 여부와는 별개입니다.",
            "아래 근거를 확인한 뒤 원장 전달 여부를 결정해 주세요.",
        )
    if state in {
        NotificationOutboxState.PENDING.value,
        NotificationOutboxState.SENDING.value,
        NotificationOutboxState.RETRYING.value,
    }:
        normalized = state or NotificationOutboxState.PENDING.value
        return normalized, OperatorCopy(
            "운영팀 알림 전달 중",
            "이 리포트 작업에 연결된 Slack 알림이 아직 전달 중입니다.",
            "운영팀이 알림을 아직 확인하지 못했을 수 있습니다.",
            "운영 센터에서 최신 알림 상태를 확인해 주세요.",
        )
    if state in {NotificationOutboxState.HOLD.value, NotificationOutboxState.FAILED.value}:
        normalized = state or NotificationOutboxState.FAILED.value
        return normalized, OperatorCopy(
            "운영팀 알림 확인 필요",
            "이 리포트 작업에 연결된 Slack 알림을 전달하지 못했습니다.",
            "운영팀이 새 리포트나 복구 결과를 놓칠 수 있습니다.",
            "운영 센터에서 알림을 다시 시도하고, 계속 실패하면 개발팀에 문의해 주세요.",
        )
    return "NOT_INDIVIDUALLY_LINKED", OperatorCopy(
        "개별 알림 연결 기록 없음",
        "여러 병원을 묶은 요약 알림이라 이 리포트와 개별 연결 기록이 없습니다.",
        "이 화면만으로 Slack 알림 발송 성공을 단정할 수 없습니다.",
        "운영 센터에서 최신 Slack 알림을 확인해 주세요.",
    )


async def _linked_run(db: AsyncSession, report: MonthlyReport) -> OperationRun | None:
    result = await db.execute(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == report.hospital_id,
            OperationRun.operation_type.in_(
                ("SCHEDULED_MONTHLY_REPORT", "GENERATE_MONTHLY_REPORT")
            ),
            OperationRun.result_summary["report_id"].as_string() == str(report.id),
            OperationRun.result_summary["period_year"].as_integer()
            == report.period_year,
            OperationRun.result_summary["period_month"].as_integer()
            == report.period_month,
        )
        .order_by(OperationRun.updated_at.desc(), OperationRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _linked_notification(
    db: AsyncSession, report: MonthlyReport
) -> NotificationOutbox | None:
    run = await _linked_run(db, report)
    if run is None:
        return None
    result = await db.execute(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.operation_run_id == run.id,
            NotificationOutbox.channel == "SLACK",
            NotificationOutbox.notification_type.in_(
                ("INCIDENT_OPEN", "INCIDENT_RECOVERED", "MILESTONE_ACTION", "MILESTONE_RECOVERED")
            ),
        )
        .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
        .limit(10)
    )
    allowed = {"INCIDENT_OPEN", "INCIDENT_RECOVERED", "MILESTONE_ACTION", "MILESTONE_RECOVERED"}
    return next(
        (
            row
            for row in result.scalars().all()
            if row.notification_type in allowed and getattr(row, "channel", None) == "SLACK"
        ),
        None,
    )


async def build_report_review_evidence(
    db: AsyncSession, report: MonthlyReport
) -> dict[str, object]:
    """Project evidence without Slack payloads, paths, task IDs, or raw responses."""

    notification = await _linked_notification(db, report)
    notification_state, notification_copy = _notification_copy(
        notification.state if notification is not None else None
    )
    quality, measurement_copy = _measurement_copy(report.quality)
    operations_url = f"/operations?queue=REPORTS&hospital_id={report.hospital_id}"
    supersedes = str(report.supersedes_report_id) if report.supersedes_report_id else None
    return {
        "version": report.version,
        "version_label": (
            f"새 버전 {report.version} · 이전 리포트 보존"
            if supersedes
            else f"버전 {report.version}"
        ),
        "supersedes_report_id": supersedes,
        "measurement": {
            "quality": quality,
            "quality_label": measurement_copy.label,
            "planned_count": report.planned_count,
            "success_count": report.success_count,
            "failed_count": report.failed_count,
            "excluded_count": report.excluded_count,
            "problem": measurement_copy.problem,
            "customer_impact": measurement_copy.customer_impact,
            "next_action": measurement_copy.next_action,
        },
        "notification": {
            "state": notification_state,
            "state_label": notification_copy.label,
            "problem": notification_copy.problem,
            "customer_impact": notification_copy.customer_impact,
            "next_action": notification_copy.next_action,
            "sent_at": notification.sent_at if notification is not None else None,
            "operations_url": operations_url,
        },
    }
