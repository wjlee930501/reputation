"""Prior-month report gaps, told only as often and only as strongly as the facts allow.

공백마다 "시스템이 자동 재측정·마감을 진행합니다"가 참인지 먼저 가른다. 월간 측정 코호트(또는
그 달 월간 RUN_SOV가 있는 병원)의 측정 미완료만 1~7일 catch-up이 실제로 되돌려 놓고, 리포트
미생성은 매일 00:15 배치가 다시 만든다. 코호트 밖 병원의 측정 미완료는 어떤 자동 경로도 없다.
- 자동 복구가 도는 공백(AUTO): 집합이 바뀐 날에만 REPORT 한 건. 매일 같은 요약을 반복하지 않는다.
- 자동 경로가 없는 공백(MANUAL): 병원·월마다 ERROR 한 건.
- 그 달 고정 매니페스트가 없는 공백(MANIFEST_MISSING): 전달 불가 — 병원·월마다 한 건, 다시 만들기를 권하지 않는다.
- 8일 00:15 최종화 뒤(8일 09:00)에도 남은 AUTO 공백: 더 돌 자동 복구가 없으므로 '자동 복구 종료' ERROR 한 건.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.monthly_period import (
    MONTHLY_FINALIZE_DAY,
    MONTHLY_RECOVERY_END_DAY,
    eligible_hospital_ids,
    prior_month_to_close,
)
from app.services.monthly_report_delivery import monthly_report_delivery_gate
from app.services.monthly_sov_cohort import hospital_requires_monthly_sov_success
from app.services.notification_contracts import NotificationIntent
from app.services.notification_labels import prefixed_for_event
from app.services.notification_milestone_rendering import (
    RenderedSlackMessage,
    action_block,
    admin_url,
    header_block,
    safe_text,
    section_block,
    validated_message,
)
from app.services.onboarding_notifications import enqueue_onboarding_notification_sync

KST = ZoneInfo("Asia/Seoul")
MONTHLY_REPORT_GAP_SUMMARY_TYPE = "MONTHLY_REPORT_GAP_SUMMARY"
MONTHLY_REPORT_GAP_AUTO_TYPE = "MONTHLY_REPORT_GAP_AUTO"
_MAX_NAMES = 15
MANIFEST_MISSING = "MANIFEST_MISSING"
# 매니페스트는 있는데 측정이 끝나지 않았거나 다른 달·다른 병원 것에 묶인 상태. 자동 재측정·재생성이 푼다.
_COVERAGE_GATE_CODES = frozenset({"coverage_incomplete", "manifest_open", "manifest_mismatch"})


@dataclass(frozen=True, slots=True)
class MonthlyReportGap:
    hospital_name: str
    state: str
    # 이 공백을 되돌리는 자동 경로가 있는가(날짜와 무관한 성질). 기본값은 '없음'이라 분류를
    # 빠뜨린 호출이 거짓 약속을 하지 않는다.
    recoverable: bool = False


def classify_monthly_report_gap(report, period_manifest, artifact) -> str | None:
    """전달 게이트와 같은 사실로 공백을 가른다. None이면 이 요약이 알릴 공백이 아니다.

    그 달 매니페스트가 없으면 어떤 자동 경로도 지난달 측정을 만들지 못한다(매니페스트는 24일~말일
    창에서만 생긴다). 코호트 밖 병원이 주간 자료로 '최종' 커버리지 보고서를 가져도 전달 게이트는
    매니페스트를 요구하므로 영구히 전달할 수 없다. 원장 PDF 문제는 월간 원장 PDF 대조 작업이 소유한다.
    """
    if period_manifest is None:
        return MANIFEST_MISSING
    if report is None:
        return "MISSING"
    gate = monthly_report_delivery_gate(report, period_manifest, artifact)
    if gate.ready:
        return None
    return "COVERAGE_INCOMPLETE" if gate.code in _COVERAGE_GATE_CODES else None


def load_monthly_report_gaps(db: Session, now: datetime) -> tuple[str, list[MonthlyReportGap]]:
    """Return MISSING, COVERAGE_INCOMPLETE and MANIFEST_MISSING facts for the prior month."""

    period = prior_month_to_close(now)
    period_key = f"{period.year:04d}-{period.month:02d}"
    hospital_ids = list(eligible_hospital_ids(db, period))
    if not hospital_ids:
        return period_key, []
    hospitals = list(
        db.scalars(
            select(Hospital)
            .where(Hospital.id.in_(hospital_ids))
            .order_by(Hospital.name, Hospital.id)
        ).all()
    )
    gaps: list[MonthlyReportGap] = []
    for hospital in hospitals:
        report = db.scalars(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital.id,
                MonthlyReport.period_year == period.year,
                MonthlyReport.period_month == period.month,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.version.desc())
            .limit(1)
        ).first()
        manifest = db.scalars(
            select(MonthlyMeasurementManifest).where(
                MonthlyMeasurementManifest.hospital_id == hospital.id,
                MonthlyMeasurementManifest.period_year == period.year,
                MonthlyMeasurementManifest.period_month == period.month,
            )
        ).first()
        artifact = (
            db.scalars(
                select(MonthlyReportArtifact).where(
                    MonthlyReportArtifact.report_id == report.id,
                    MonthlyReportArtifact.audience == "DOCTOR",
                )
            ).first()
            if report is not None and manifest is not None
            else None
        )
        state = classify_monthly_report_gap(report, manifest, artifact)
        if state is None:
            continue
        # 미생성은 매일 00:15 배치가 다시 만든다. 측정 미완료는 코호트(또는 그 달 월간 RUN_SOV)만
        # 1~7일 catch-up이 되돌린다. 매니페스트 없는 달은 어떤 자동 경로도 없다.
        recoverable = state == "MISSING" or (
            state == "COVERAGE_INCOMPLETE"
            and hospital_requires_monthly_sov_success(db, hospital, period_key)
        )
        gaps.append(MonthlyReportGap(hospital.name, state, recoverable=recoverable))
    return period_key, gaps


def _names(gaps: list[MonthlyReportGap]) -> str:
    names = " · ".join(safe_text(gap.hospital_name, 60) for gap in gaps[:_MAX_NAMES])
    if len(gaps) > _MAX_NAMES:
        names = f"{names} · 외 {len(gaps) - _MAX_NAMES}곳"
    return names


def _counts(gaps: list[MonthlyReportGap]) -> tuple[int, int]:
    missing = sum(1 for gap in gaps if gap.state == "MISSING")
    return missing, len(gaps) - missing


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _message(
    *, event: str, header: str, summary: str, counts_text: str, detail: str, fallback_tail: str
):
    url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=reports")
    return validated_message(
        RenderedSlackMessage(
            fallback_text=prefixed_for_event(
                event, f"{header} | {summary} | {fallback_tail}"
            ),
            blocks=(
                header_block("monthly_report_gap_header", prefixed_for_event(event, header)),
                section_block("monthly_report_gap_counts", counts_text),
                section_block("monthly_report_gap_hospitals", detail),
                action_block("monthly_report_gap_action", url, "운영 센터에서 확인"),
            ),
            admin_url=url,
        ),
        settings.ADMIN_BASE_URL,
    )


def build_monthly_report_gap_auto_summary(
    *, period_key: str, gaps: list[MonthlyReportGap]
) -> NotificationIntent:
    """자동 복구가 돌고 있는 공백 — 사람이 할 일이 없다. 집합이 바뀔 때만 나간다."""

    missing, incomplete = _counts(gaps)
    summary = f"미생성 {missing}곳 · 측정 미완료 {incomplete}곳"
    message = _message(
        event=MONTHLY_REPORT_GAP_AUTO_TYPE,
        header=f"[진행 요약] {period_key} 레포트 준비 중 {len(gaps)}곳",
        summary=summary,
        counts_text=f"*{period_key}* · 총 {len(gaps)}곳\n{summary}",
        detail=(
            f"대상: {_names(gaps)}\n"
            "시스템이 매월 1~7일 자동 재측정·마감을 계속하고 8일 00:15에 최종 마감합니다. "
            "수동으로 반복 실행하지 마세요. 최종 마감 뒤에도 남은 공백은 8일 오전에 별도 조치 알림으로 안내합니다."
        ),
        fallback_tail="시스템이 자동 재측정·마감을 진행합니다. 수동으로 반복 실행하지 마세요.",
    )
    digest = _digest(period_key, *sorted(f"{gap.hospital_name}:{gap.state}" for gap in gaps))
    return NotificationIntent(
        dedupe_key=f"{MONTHLY_REPORT_GAP_AUTO_TYPE}:{period_key}:{digest}",
        notification_type=MONTHLY_REPORT_GAP_AUTO_TYPE,
        message=message,
    )


def build_monthly_report_gap_summary(
    *,
    period_key: str,
    summary_date: str | None = None,
    gaps: list[MonthlyReportGap],
    closing: bool = False,
) -> NotificationIntent:
    """사람이 풀어야 하는 공백 — 병원·월마다 한 건이다(`closing`이면 복구 종료 요약 한 건).

    `summary_date`는 더 이상 중복 키에 들어가지 않는다(매일 반복하지 않는다). 호환을 위해 인자만 남겼다.
    """

    del summary_date
    missing, incomplete = _counts(gaps)
    summary = f"미생성 {missing}곳 · 측정 미완료 {incomplete}곳"
    if closing:
        header = f"[조치 필요] {period_key} 레포트 자동 복구 종료 {len(gaps)}곳"
        detail = (
            f"대상: {_names(gaps)}\n"
            "자동 재측정·마감이 끝났지만 아직 풀리지 않았습니다. 운영 센터에서 원인을 확인해 해결한 뒤 "
            "‘리포트 다시 만들기’를 눌러 주세요."
        )
        tail = "자동 복구가 끝났습니다. 운영 센터에서 확인해 주세요."
        key = f"{MONTHLY_REPORT_GAP_SUMMARY_TYPE}:{period_key}:closed"
    else:
        header = f"[조치 필요] {period_key} 레포트 준비 {len(gaps)}곳"
        detail = (
            f"대상: {_names(gaps)}\n"
            "이 병원은 자동 재측정 대상이 아니라 시스템이 풀지 못합니다. "
            "운영 센터에서 원인을 확인해 해결한 뒤 ‘리포트 다시 만들기’를 눌러 주세요."
        )
        tail = "자동 복구 대상이 아닙니다. 운영 센터에서 확인해 주세요."
        # 병원·월마다 한 건 — 다른 병원이 더해져도 이미 알린 병원은 다시 나가지 않는다.
        key = f"{MONTHLY_REPORT_GAP_SUMMARY_TYPE}:{period_key}:{_digest(*(gap.hospital_name for gap in gaps))}"
    message = _message(
        event=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        header=header,
        summary=summary,
        counts_text=f"*{period_key}* · 총 {len(gaps)}곳\n{summary}",
        detail=detail,
        fallback_tail=tail,
    )
    return NotificationIntent(
        dedupe_key=key,
        notification_type=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        message=message,
    )


def build_monthly_report_manifest_missing_notice(
    *, period_key: str, gap: MonthlyReportGap
) -> NotificationIntent:
    """그 달 고정 측정이 없어 전달 가능한 리포트가 생길 수 없다 — 병원·월마다 한 건.

    다시 만들기를 권하지 않는다(지난달 매니페스트는 어떤 재생성으로도 생기지 않는다).
    """

    name = safe_text(gap.hospital_name, 60)
    message = _message(
        event=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        header=f"[전달 불가] {period_key} {name} 월간 리포트",
        summary="월간 고정 측정 없음",
        counts_text=f"*{period_key}* · {name}\n월간 고정 측정(24일~말일) 대상이 아니었습니다.",
        detail=(
            f"대상: {name}\n"
            "이번 달은 측정이 없어 전달 불가, 다음 달부터 자동으로 측정합니다. "
            "리포트를 다시 만들어도 바뀌지 않습니다. 원장님께 이번 달은 월간 측정이 없었다는 사실을 안내해 주세요. "
            "다음 달 측정 창(24일) 전에 대상에 들어가지 못하면 측정 대상 공백 알림으로 따로 안내합니다."
        ),
        fallback_tail="이번 달은 측정이 없어 전달 불가, 다음 달부터 자동 — 원장님께 안내해 주세요.",
    )
    return NotificationIntent(
        dedupe_key=(
            f"{MONTHLY_REPORT_GAP_SUMMARY_TYPE}:{period_key}:manifest_missing:{_digest(gap.hospital_name)}"
        ),
        notification_type=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        message=message,
    )


def monthly_report_gap_intents(
    *, period_key: str, gaps: list[MonthlyReportGap], day: int
) -> list[NotificationIntent]:
    """오늘 보낼 알림 — 자동 복구 보고, 병원별 조치 필요, 8일 최종화 뒤의 종료 요약."""

    # 7일 23:59:59까지 자동 복구가 돌고 8일 00:15 배치가 마감 사실로 최종화한다.
    # 8일 09:00 판정은 그 최종화 뒤라, 이때 남은 공백만 자동 복구가 끝난 것이다.
    recovery_ahead = day <= MONTHLY_RECOVERY_END_DAY
    auto = [gap for gap in gaps if gap.recoverable and recovery_ahead]
    closing = [gap for gap in gaps if gap.recoverable and not recovery_ahead]
    manifest_missing = [gap for gap in gaps if gap.state == MANIFEST_MISSING]
    manual = [gap for gap in gaps if not gap.recoverable and gap.state != MANIFEST_MISSING]
    intents: list[NotificationIntent] = []
    if auto:
        intents.append(build_monthly_report_gap_auto_summary(period_key=period_key, gaps=auto))
    intents.extend(
        build_monthly_report_gap_summary(period_key=period_key, gaps=[gap]) for gap in manual
    )
    intents.extend(
        build_monthly_report_manifest_missing_notice(period_key=period_key, gap=gap)
        for gap in manifest_missing
    )
    if closing:
        intents.append(
            build_monthly_report_gap_summary(period_key=period_key, gaps=closing, closing=True)
        )
    return intents


def enqueue_monthly_report_gap_summary_sync(db: Session, *, now: datetime) -> bool:
    local = now.astimezone(KST)
    if not 1 <= local.day <= MONTHLY_FINALIZE_DAY:
        return False
    period_key, gaps = load_monthly_report_gaps(db, now)
    if not gaps:
        return False
    for intent in monthly_report_gap_intents(period_key=period_key, gaps=gaps, day=local.day):
        enqueue_onboarding_notification_sync(db, intent, now=now)
    return True
