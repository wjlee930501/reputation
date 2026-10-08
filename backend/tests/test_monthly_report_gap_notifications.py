from datetime import UTC, datetime

from app.services import monthly_report_gap_notifications as notifications


def test_gap_summary_dedupe_does_not_repeat_by_day_and_has_one_admin_link():
    intent = notifications.build_monthly_report_gap_summary(
        period_key="2026-08",
        summary_date="2026-09-02",
        gaps=[
            notifications.MonthlyReportGap("미생성 병원", "MISSING"),
            notifications.MonthlyReportGap(
                "측정 미완료 병원", "COVERAGE_INCOMPLETE"
            ),
        ],
    )

    # 날짜는 키에 들어가지 않는다 — 같은 공백이 매일 반복해서 나가지 않는다.
    assert intent.dedupe_key.startswith("MONTHLY_REPORT_GAP_SUMMARY:2026-08:")
    assert "2026-09-02" not in intent.dedupe_key
    assert intent.notification_type == "MONTHLY_REPORT_GAP_SUMMARY"
    assert intent.message.admin_url.endswith("/operations?queue=reports")
    assert len(intent.message.payload()["blocks"]) == 4


def test_gap_summary_is_edge_limited_to_days_one_through_seven(monkeypatch):
    loaded = []
    enqueued = []
    monkeypatch.setattr(
        notifications,
        "load_monthly_report_gaps",
        lambda *_args, **_kwargs: loaded.append(True) or ("2026-08", []),
    )
    monkeypatch.setattr(
        notifications,
        "enqueue_onboarding_notification_sync",
        lambda *_args, **_kwargs: enqueued.append(True),
    )

    assert not notifications.enqueue_monthly_report_gap_summary_sync(
        object(), now=datetime(2026, 9, 8, tzinfo=UTC)
    )
    assert not notifications.enqueue_monthly_report_gap_summary_sync(
        object(), now=datetime(2026, 9, 2, tzinfo=UTC)
    )
    assert loaded == [True]
    assert enqueued == []
