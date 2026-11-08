"""Report-time publication facts, kept separate from the currently visible edition.

First publication fulfills a contract even after withdrawal or republication.
The report's observation time and exclusive period end are preserved exactly.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import arrow
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.report import MonthlyReport


def first_publication_at(item: ContentItem) -> datetime | None:
    return getattr(item, "first_published_at", None) or item.published_at


def observed_contract_publications(
    items: Iterable[ContentItem], observed_at: datetime
) -> list[ContentItem]:
    """Only publications that had actually happened at report-build time fulfill a contract."""
    return [
        item
        for item in items
        if first_publication_at(item) is not None and first_publication_at(item) <= observed_at
    ]


def contract_publication_timing_counts(
    items: Iterable[ContentItem],
    period_start: datetime,
    period_end: datetime,
) -> tuple[int, int]:
    """Return contract publications before the period and at/after its exclusive end."""
    early = 0
    late = 0
    for item in items:
        first_published_at = first_publication_at(item)
        if first_published_at is None:
            continue
        if first_published_at < period_start:
            early += 1
        elif first_published_at >= period_end:
            late += 1
    return early, late


def load_monthly_publication_facts(
    db: Session,
    hospital_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
    observed_at: datetime,
) -> tuple[list[ContentItem], list[ContentItem], list[ContentItem]]:
    """Load immutable publication facts separately from currently visible rows."""
    first_publication_at = func.coalesce(
        ContentItem.first_published_at,
        ContentItem.published_at,
    )
    actual_publications = list(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at >= period_start,
                first_publication_at < period_end,
                first_publication_at <= observed_at,
            )
        ).scalars()
    )
    visible_publications = [
        item for item in actual_publications if item.status == ContentStatus.PUBLISHED
    ]
    contract_publications = observed_contract_publications(
        db.execute(
            select(ContentItem).where(
                ContentItem.hospital_id == hospital_id,
                first_publication_at.is_not(None),
                first_publication_at <= observed_at,
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                >= period_start.date(),
                func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
                < period_end.date(),
            )
        ).scalars(),
        observed_at,
    )
    return actual_publications, visible_publications, contract_publications


# ── 원장 보고서의 '발행 콘텐츠' 귀속(2026-11-08) ─────────────────────────────
#
# 글 하나는 원장 보고서 한 달에만 실린다. 귀속 달은 그 글의 계약 월(이월 전 예정일의 달)이고,
# 계약 월 보고서를 처음 만든 시각 뒤에 처음 공개된 글만 실제로 처음 공개된 달로 간다(그 달
# 보고서는 이미 나갔기 때문이다). 그래서 10/30 예정·11/1 08:00 공개 글은 10월 보고서가 11/1
# 아침에 만들어졌으면 10월에 실리고 11월에는 다시 실리지 않는다. 다음 달 예정 글을 이번 달에
# 미리 올린 경우(앞당김)는 다음 달 보고서에 실린다. 운영 집계(`load_monthly_publication_facts`)는
# 그대로이고 이 귀속은 원장 보고서의 1면 편수·글 목록·누적 편수만 쓴다.
#
# 알려진 한계(순서가 어긋난 생성): 다음 달 판이 이미 있는 뒤에 (a) 귀속 이전 방식 달을 다시 만들거나
# (b) 빠진 지난달 판을 처음 만들면, 이미 저장된 다음 달 판의 목록은 바뀌지 않으므로 같은 글이 두 달에 실리거나
# 빠질 수 있다. 정기 생성(매월 1일 지난달)과 그 달 안의 재생성은 해당하지 않는다.

KST = "Asia/Seoul"


def _month_key(value: datetime) -> tuple[int, int]:
    local = arrow.get(value).to(KST)
    return local.year, local.month


def _contract_month(item: ContentItem) -> tuple[int, int] | None:
    contract_date = getattr(item, "carried_over_from", None) or getattr(item, "scheduled_date", None)
    if contract_date is None:
        return None
    return contract_date.year, contract_date.month


def _aware(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value
    return None


def _report_observed_at(report: MonthlyReport) -> datetime:
    """그 판을 만든 관측 시각. `MonthlyReport.cutoff_at`은 측정 마감이라 쓰지 않는다(Codex 3)."""
    content = report.content_summary or {}
    observed = _aware((content.get("contract_timing") or {}).get("observed_at"))
    if observed is not None:
        return observed
    period = (((content.get("report_snapshot") or {}).get("render_inputs") or {}).get("period") or {})
    observed = _aware(period.get("cutoff_at"))
    if observed is not None:
        return observed
    return report.created_at


@dataclass(frozen=True)
class _MonthReports:
    """달마다 첫 판의 관측 시각과, 지금 판이 귀속 이전 방식(1면이 처음 공개된 달 기준)인지."""

    first_observed: dict[tuple[int, int], datetime]
    legacy: frozenset[tuple[int, int]]


def _month_reports(db: Session, hospital_id: uuid.UUID) -> _MonthReports:
    reports = db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.hospital_id == hospital_id, MonthlyReport.report_type == "MONTHLY")
        .order_by(MonthlyReport.period_year, MonthlyReport.period_month, MonthlyReport.version)
    ).scalars()
    first_observed: dict[tuple[int, int], datetime] = {}
    latest_is_legacy: dict[tuple[int, int], bool] = {}
    for report in reports:
        key = (report.period_year, report.period_month)
        if key not in first_observed:
            observed = _report_observed_at(report)
            if observed is not None:
                first_observed[key] = observed
        latest_is_legacy[key] = "doctor_publications" not in (report.content_summary or {})
    legacy = frozenset(key for key, is_legacy in latest_is_legacy.items() if is_legacy)
    return _MonthReports(first_observed, legacy)


def _report_month_of(
    item: ContentItem,
    report_month: tuple[int, int],
    observed_at: datetime,
    months: _MonthReports,
) -> tuple[int, int] | None:
    """이 글이 실리는 원장 보고서 달. `observed_at`(지금 만드는 보고서의 관측 시각)에 공개 전이면 None.

    - 계약 월 보고서를 처음 만든 시각까지 공개된 글은 계약 월(지금 만드는 달이 처음이면 지금 관측 시각까지).
      그 뒤에 공개됐거나 계약 월 보고서가 없으면 처음 공개된 달이다. 다음 달 예정 글을 미리 올렸으면 계약 월이다.
    - 귀속 이전 방식으로 만든 판(1면·목록이 처음 공개된 달 기준, `doctor_publications` 없음)이 지금 판인 달은
      그 판이 이미 실은 글을 그대로 존중한다 — 그 달에 처음 공개된 글은 그 달이고, 그 달 계약 글 중 다른 달에
      공개된 글은 그 판 목록에 없었으므로 처음 공개된 달로 간다(Codex 2: 전환기에 겹치거나 빠지지 않게).
    - 지금 만드는 달은 귀속 방식으로 만든다(옛 판이 있어도 다시 만드는 판이 새 방식이다).
    """
    published = first_publication_at(item)
    if published is None or published > observed_at:
        return None
    published_month = _month_key(published)
    if published_month < report_month and published_month in months.legacy:
        return published_month
    contract = _contract_month(item)
    if contract is None:
        return published_month
    if contract > report_month:
        return contract
    if contract < report_month and contract in months.legacy:
        return published_month
    # 다시 만드는 달(Codex 1)도 그 달 첫 판의 관측 시각이 기준이다 — 그 뒤 공개된 글은 이미 다음 달 판에 실렸다.
    cutoff = months.first_observed.get(contract)
    if cutoff is None and contract == report_month:
        cutoff = observed_at
    if cutoff is not None and published <= cutoff:
        return contract
    return published_month


@dataclass(frozen=True)
class ReportMonthPublications:
    """원장 보고서 한 달에 실리는 글. `items`는 공개를 내렸어도 남는 실제 이행, `visible`은 지금 공개 중인 글."""

    items: tuple[ContentItem, ...]
    visible: tuple[ContentItem, ...]
    carried_in_count: int  # 더 이른 계약 월 글(그 달 보고서 뒤에 공개)
    cumulative_count: int  # 이 달까지 원장 보고서에 실린 글 수


def load_report_month_publications(
    db: Session,
    hospital_id: uuid.UUID,
    period_start: datetime,
    observed_at: datetime,
) -> ReportMonthPublications:
    report_month = _month_key(period_start)
    months = _month_reports(db, hospital_id)
    published_at = func.coalesce(ContentItem.first_published_at, ContentItem.published_at)
    candidates = db.execute(
        select(ContentItem).where(
            ContentItem.hospital_id == hospital_id,
            published_at.is_not(None),
            published_at <= observed_at,
        )
    ).scalars()
    items: list[ContentItem] = []
    carried_in = 0
    cumulative = 0
    for item in candidates:
        month = _report_month_of(item, report_month, observed_at, months)
        if month is None or month > report_month:
            continue
        cumulative += 1
        if month != report_month:
            continue
        items.append(item)
        contract = _contract_month(item)
        if contract is not None and contract < report_month:
            carried_in += 1
    items.sort(key=lambda item: (first_publication_at(item), str(item.id)))
    visible = tuple(item for item in items if item.status == ContentStatus.PUBLISHED)
    return ReportMonthPublications(tuple(items), visible, carried_in, cumulative)
