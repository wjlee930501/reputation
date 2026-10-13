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


def test_no_manifest_for_the_period_is_manifest_missing_even_with_a_final_report(monkeypatch):
    _gate(monkeypatch, DeliveryGate(False, "coverage_incomplete", "x"))
    report = SimpleNamespace(manifest_id=None)

    assert notifications.classify_monthly_report_gap(report, None, None) == "MANIFEST_MISSING"
    # 보고서가 아직 없어도 매니페스트가 없으면 자동 경로가 없다.
    assert notifications.classify_monthly_report_gap(None, None, None) == "MANIFEST_MISSING"


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


def test_manifest_missing_is_one_honest_notice_per_hospital_and_never_promises_rebuild():
    gap = notifications.MonthlyReportGap("가의원", "MANIFEST_MISSING")

    intents = notifications.monthly_report_gap_intents(period_key="2026-09", gaps=[gap], day=1)

    [intent] = intents
    assert intent.notification_type == "MONTHLY_REPORT_GAP_SUMMARY"
    assert ":manifest_missing:" in intent.dedupe_key
    text = intent.message.fallback_text
    assert "전달 불가" in text
    assert "다음 달부터 자동" in intent.message.payload_json()
    assert "리포트 다시 만들기" not in intent.message.payload_json()
    assert "자동 재측정·마감을 진행합니다" not in text
    # 날짜가 바뀌어도 같은 병원·같은 달이면 같은 키다. 8일 판정에서도 '자동 복구 종료'에 섞지 않는다.
    for day in (5, 8):
        again = notifications.monthly_report_gap_intents(period_key="2026-09", gaps=[gap], day=day)
        assert [item.dedupe_key for item in again] == [intent.dedupe_key]


def test_manifest_missing_is_never_marked_recoverable(monkeypatch):
    """로더가 매니페스트 없는 달을 자동 복구 대상(AUTO)으로 세지 않는다."""

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
        notifications, "hospital_requires_monthly_sov_success", lambda *_a: True
    )

    period_key, found = notifications.load_monthly_report_gaps(_DB(), datetime(2026, 10, 2, tzinfo=UTC))

    assert period_key == "2026-09"
    assert found == [notifications.MonthlyReportGap("코호트밖의원", "MANIFEST_MISSING")]
