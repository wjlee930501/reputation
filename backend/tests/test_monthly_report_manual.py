"""병원별 월간 리포트 수동 생성.

월말 배치가 실패하면 그 병원은 다음 달 마지막 날까지 리포트가 비고, 종전 복구 경로는
`make monthly-report`(전체 병원 · 마지막 날에만 동작)뿐이었다. 여기서 검증하는 축은
**어느 달을 만드는가** — 배치 실패는 대개 달이 바뀐 뒤 발견되므로 기본값이 '지난달'이
아니면 복구 자체가 엉뚱한 달을 만든다.
"""
import uuid

import arrow
import pytest

from app.workers import tasks


class FakeSession:
    def __init__(self, hospital=None):
        self.hospital = hospital
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, _pk):
        return self.hospital

    def rollback(self):
        self.rolled_back = True


class FakeHospital:
    id = uuid.uuid4()
    name = "장편한외과의원"


@pytest.fixture
def captured_anchor(monkeypatch):
    """_build_monthly_report_for_hospital에 넘어간 anchor를 가로챈다."""
    seen: dict = {}

    def fake_build(_db, hospital, anchor):
        seen["hospital"] = hospital
        seen["anchor"] = anchor
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", fake_build)
    return seen


def _use_session(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)


def test_defaults_to_previous_month(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 3, 4, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id))

    anchor = captured_anchor["anchor"]
    assert (anchor.year, anchor.month) == (2026, 2)
    # 리포트 본문이 anchor.ceil("month")로 기간을 잡으므로 월말이어야 한다.
    assert anchor.day == 28
    assert result == {"status": "created", "year": 2026, "month": 2}


def test_explicit_period_is_honoured(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2025, 12)

    anchor = captured_anchor["anchor"]
    assert (anchor.year, anchor.month, anchor.day) == (2025, 12, 31)
    assert result["year"] == 2025
    assert result["month"] == 12


def test_january_default_rolls_back_to_previous_december(monkeypatch, captured_anchor):
    """연도 경계 — 1월에 지난달을 만들면 전년 12월이어야 한다."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 1, 2, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id))

    assert (result["year"], result["month"]) == (2025, 12)


def test_partial_period_is_rejected(monkeypatch, captured_anchor):
    """잘못된 요청은 예외가 아니라 상태로 돌려준다 — autoretry가 헛돌지 않게."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))

    assert tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, None) == {
        "status": "invalid_period"
    }
    assert tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), None, 2) == {
        "status": "invalid_period"
    }

    assert "anchor" not in captured_anchor


@pytest.mark.parametrize("offset_months", [0, 1, 6])
def test_current_and_future_months_are_rejected(monkeypatch, captured_anchor, offset_months):
    """이번 달 이후를 미리 만들면 빈 리포트 행이 월말 배치를 dedupe로 영구 차단한다."""
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    now = arrow.get(2026, 8, 4, tzinfo="Asia/Seoul")
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: now)
    target = now.shift(months=offset_months)

    result = tasks.generate_monthly_report_for_hospital(
        str(FakeHospital.id), target.year, target.month
    )

    assert result == {"status": "invalid_period"}
    assert "anchor" not in captured_anchor


def test_previous_month_is_still_allowed(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 8, 4, tzinfo="Asia/Seoul")
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, 7)

    assert result["status"] == "created"
    assert (captured_anchor["anchor"].year, captured_anchor["anchor"].month) == (2026, 7)


def test_existing_report_is_not_overwritten(monkeypatch):
    _use_session(monkeypatch, FakeSession(FakeHospital()))
    monkeypatch.setattr(
        tasks, "_build_monthly_report_for_hospital", lambda *_a: "skipped_existing"
    )

    result = tasks.generate_monthly_report_for_hospital(str(FakeHospital.id), 2026, 2)

    assert result["status"] == "skipped_existing"


def test_unknown_hospital_reports_instead_of_raising(monkeypatch, captured_anchor):
    _use_session(monkeypatch, FakeSession(None))

    result = tasks.generate_monthly_report_for_hospital(str(uuid.uuid4()))

    assert result == {"status": "hospital_not_found"}
    assert "anchor" not in captured_anchor


def _run_failing_attempt(monkeypatch, retries: int) -> tuple[FakeSession, list[dict]]:
    session = FakeSession(FakeHospital())
    _use_session(monkeypatch, session)
    alerts: list[dict] = []

    async def fake_ops_alert(**kwargs):
        alerts.append(kwargs)
        return True

    def boom(*_a):
        raise RuntimeError("pdf renderer down")

    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", boom)
    monkeypatch.setattr(tasks.notifier, "notify_ops_alert", fake_ops_alert)

    task = tasks.generate_monthly_report_for_hospital
    task.push_request(retries=retries)
    try:
        with pytest.raises(RuntimeError):
            task(str(FakeHospital.id), 2026, 2)
    finally:
        task.pop_request()
    return session, alerts


def test_final_failure_rolls_back_and_alerts(monkeypatch):
    session, alerts = _run_failing_attempt(
        monkeypatch, tasks.generate_monthly_report_for_hospital.max_retries
    )

    assert session.rolled_back is True
    assert len(alerts) == 1
    assert FakeHospital.name in alerts[0]["message"]


def test_intermediate_failure_stays_silent(monkeypatch):
    """재시도가 남았는데 매번 알리면 일시 장애 한 번에 Slack이 여러 번 울린다."""
    session, alerts = _run_failing_attempt(monkeypatch, 0)

    assert session.rolled_back is True
    assert alerts == []
