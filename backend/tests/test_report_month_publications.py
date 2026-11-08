"""원장 보고서 '발행 콘텐츠' 귀속(2026-11-08) — 글 하나는 한 달 보고서에만 실린다."""

from datetime import date, datetime
from types import SimpleNamespace

import arrow

from app.services.monthly_publication_facts import _MonthReports, _report_month_of


def _kst(*args) -> datetime:
    return arrow.get(datetime(*args), tzinfo="Asia/Seoul").datetime


def _item(scheduled: date | None, published: datetime | None, carried_over_from: date | None = None):
    return SimpleNamespace(
        scheduled_date=scheduled, carried_over_from=carried_over_from,
        first_published_at=published, published_at=published,
    )


OCT, NOV, SEP = (2026, 10), (2026, 11), (2026, 9)
NEW_OCT = _MonthReports(first_observed={OCT: _kst(2026, 11, 1, 9)}, legacy=frozenset())


def test_late_publication_before_the_contract_month_report_stays_in_the_contract_month():
    late = _item(date(2026, 10, 30), _kst(2026, 11, 1, 8))
    assert _report_month_of(late, OCT, _kst(2026, 11, 1, 9), _MonthReports({}, frozenset())) == OCT
    assert _report_month_of(late, NOV, _kst(2026, 12, 1, 9), NEW_OCT) == OCT  # 11월에 다시 싣지 않는다


def test_publication_after_the_contract_month_report_goes_to_the_month_it_was_published():
    after = _item(date(2026, 10, 30), _kst(2026, 11, 3, 8))
    assert _report_month_of(after, NOV, _kst(2026, 12, 1, 9), NEW_OCT) == NOV


def test_regenerating_a_month_keeps_the_first_reports_cutoff():
    """다시 만드는 10월(11/8)도 11/3 공개 글은 싣지 않는다 — 이미 11월 판의 몫이다(Codex 1)."""
    after = _item(date(2026, 10, 30), _kst(2026, 11, 3, 8))
    assert _report_month_of(after, OCT, _kst(2026, 11, 8, 12), NEW_OCT) == NOV
    late = _item(date(2026, 10, 30), _kst(2026, 11, 1, 8))
    assert _report_month_of(late, OCT, _kst(2026, 11, 8, 12), NEW_OCT) == OCT


def test_early_publication_belongs_to_its_contract_month():
    early = _item(date(2026, 11, 3), _kst(2026, 10, 29, 8))
    assert _report_month_of(early, OCT, _kst(2026, 11, 1, 9), _MonthReports({}, frozenset())) == NOV
    assert _report_month_of(early, NOV, _kst(2026, 12, 1, 9), NEW_OCT) == NOV


def test_legacy_month_report_keeps_what_it_already_listed():
    """10월 지금 판이 귀속 이전 방식이면(Codex 2): 10월에 공개된 글은 10월에 이미 실렸고, 10월 계약 글 중 11월에 공개된 글은
    그 판 목록에 없었으므로 11월에 싣는다."""
    legacy_oct = _MonthReports(first_observed={OCT: _kst(2026, 11, 1, 9)}, legacy=frozenset({OCT}))
    early = _item(date(2026, 11, 3), _kst(2026, 10, 29, 8))
    late = _item(date(2026, 10, 30), _kst(2026, 11, 1, 8))
    assert _report_month_of(early, NOV, _kst(2026, 12, 1, 9), legacy_oct) == OCT
    assert _report_month_of(late, NOV, _kst(2026, 12, 1, 9), legacy_oct) == NOV
    # 그 10월을 새 방식으로 다시 만들면 10월 계약 글이 10월에 실린다(장보고) — 지금 만드는 달은 새 방식이다.
    assert _report_month_of(late, OCT, _kst(2026, 11, 8, 12), legacy_oct) == OCT


def test_unpublished_at_observation_and_unscheduled_items():
    future = _item(date(2026, 10, 30), _kst(2026, 11, 2, 8))
    assert _report_month_of(future, OCT, _kst(2026, 11, 1, 9), _MonthReports({}, frozenset())) is None
    unscheduled = _item(None, _kst(2026, 10, 10, 8))
    assert _report_month_of(unscheduled, OCT, _kst(2026, 11, 1, 9), _MonthReports({}, frozenset())) == OCT
    carried = _item(date(2026, 10, 2), _kst(2026, 10, 2, 8), carried_over_from=date(2026, 9, 29))
    assert _report_month_of(carried, OCT, _kst(2026, 11, 1, 9), _MonthReports({SEP: _kst(2026, 10, 1, 9)}, frozenset())) == OCT
