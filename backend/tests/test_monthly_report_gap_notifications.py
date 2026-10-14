from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import monthly_report_gap_notifications as notifications
from app.services.monthly_report_delivery import DeliveryGate


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


def test_gap_summary_runs_days_one_through_eight(monkeypatch):
    loaded = []
    monkeypatch.setattr(
        notifications,
        "load_monthly_report_gaps",
        lambda *_args, **_kwargs: loaded.append(True) or ("2026-08", []),
    )
    monkeypatch.setattr(
        notifications, "enqueue_onboarding_notification_sync", lambda *_a, **_k: None
    )

    # UTC 9/8 00:00 = KST 9/8 09:00 — 8일 최종화 뒤의 마지막 판정 날이다.
    assert not notifications.enqueue_monthly_report_gap_summary_sync(
        object(), now=datetime(2026, 9, 8, tzinfo=UTC)
    )
    # UTC 9/9 00:00 = KST 9/9 09:00 — 더 돌지 않는다.
    assert not notifications.enqueue_monthly_report_gap_summary_sync(
        object(), now=datetime(2026, 9, 9, tzinfo=UTC)
    )

    assert loaded == [True]


def _gate(monkeypatch, gate):
    monkeypatch.setattr(notifications, "monthly_report_delivery_gate", lambda *_args: gate)


def test_no_manifest_month_is_judged_by_the_same_gate_not_marked_undeliverable(monkeypatch):
    """D38: 매니페스트가 없는 달은 '기준 설정' 보고서로 전달한다 — 전달 불가로 가르지 않는다."""
    assert notifications.classify_monthly_report_gap(None, None, None) == "MISSING"
    _gate(monkeypatch, DeliveryGate(True, None, None))
    assert notifications.classify_monthly_report_gap(SimpleNamespace(), None, None) is None
    # '기준 설정' 이전에 만든 옛 판(전달 게이트 미통과)은 8일부터 안전망이 다시 만든다.
    _gate(monkeypatch, DeliveryGate(False, "coverage_incomplete", "x"))
    assert (
        notifications.classify_monthly_report_gap(SimpleNamespace(), None, None)
        == "COVERAGE_INCOMPLETE"
    )


def test_gate_decides_the_remaining_states(monkeypatch):
    manifest = SimpleNamespace(id="m")
    assert notifications.classify_monthly_report_gap(None, manifest, None) == "MISSING"

    _gate(monkeypatch, DeliveryGate(True, None, None))
    assert notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None) is None

    for code in ("coverage_incomplete", "manifest_open", "manifest_mismatch"):
        _gate(monkeypatch, DeliveryGate(False, code, "x"))
        assert (
            notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None)
            == "COVERAGE_INCOMPLETE"
        )

    # 원장 PDF 문제는 원장 PDF 대조 작업의 몫이다 — 이 요약에서 중복으로 알리지 않는다.
    for code in ("doctor_artifact_missing", "doctor_artifact_invalid", "report_blocked"):
        _gate(monkeypatch, DeliveryGate(False, code, "x"))
        assert notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None) is None


def test_manifest_missing_month_with_an_old_report_is_recoverable(monkeypatch):
    """매니페스트 없는 달의 옛 판은 8일부터 안전망이 '기준 설정'으로 다시 만든다 — 자동 복구(AUTO)다."""

    hospital = SimpleNamespace(id="h1", name="코호트밖의원")
    period = SimpleNamespace(year=2026, month=9)

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    class _DB:
        def __init__(self):
            self.calls = 0

        def scalars(self, _stmt):
            self.calls += 1
            if self.calls == 1:
                return _Scalars([hospital])
            if self.calls == 2:  # 최신 월간 보고서 — 주간 자료로 최종 커버리지
                return _Scalars([SimpleNamespace(id="r1", manifest_id=None)])
            return _Scalars([])  # 매니페스트 없음, 원장 PDF 없음

    monkeypatch.setattr(notifications, "prior_month_to_close", lambda _now: period)
    monkeypatch.setattr(notifications, "eligible_hospital_ids", lambda *_a: ["h1"])
    monkeypatch.setattr(
        notifications, "hospital_requires_monthly_sov_success", lambda *_a: False
    )
    _gate(monkeypatch, DeliveryGate(False, "coverage_incomplete", "x"))

    period_key, found = notifications.load_monthly_report_gaps(_DB(), datetime(2026, 10, 2, tzinfo=UTC))

    assert period_key == "2026-09"
    assert found == [
        notifications.MonthlyReportGap("코호트밖의원", "COVERAGE_INCOMPLETE", recoverable=True)
    ]
