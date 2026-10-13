"""코호트 공백은 미리 알리고, 기록이 없어 못 들어온 병원은 기다리지 않고 1회 측정한다(Phase 1-7b)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.models.hospital import HospitalStatus
from app.services.incident_types import SWEEP_OWNED_INCIDENT_TYPES, incident_policy
from app.workers import tasks
from app.workers.incident_backlog import _RETRYING_RESOLVABLE

NO_RECORD = {"name": "새의원", "reason": "no SovRecord", "hospital_id": str(uuid.uuid4())}
SHORT_SET = {
    "name": "질문부족의원",
    "reason": "not enough LOCAL ACTIVE targets: found 4, requires 10..15",
    "hospital_id": str(uuid.uuid4()),
}
OBSERVED = tasks.datetime(2026, 10, 24, 0, 0, tzinfo=tasks.timezone.utc)


def _capture_gaps(monkeypatch):
    opened = []
    monkeypatch.setattr(tasks, "open_monthly_cohort_gap", lambda **kw: opened.append(kw) or kw)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    return opened


def test_weekly_reports_only_human_cohort_gaps(monkeypatch):
    """기록 없는 병원은 같은 주간 실행이 곧 측정한다 — 그 사유로 사고를 열면 오탐이다."""
    opened = _capture_gaps(monkeypatch)

    tasks._report_monthly_cohort_gaps(
        {"not_enrolled": [NO_RECORD, SHORT_SET]},
        "2026-10",
        skip_reasons=frozenset({tasks.NO_SOV_RECORD_REASON}),
    )

    assert [item["hospital_name"] for item in opened] == ["질문부족의원"]
    assert opened[0]["retrying_until"] is None


class _BootstrapDB:
    def __init__(self, hospital, existing_requested_at=None):
        self.hospital = hospital
        self.existing_requested_at = existing_requested_at

    def get(self, _model, hospital_id):
        return self.hospital if hospital_id == self.hospital.id else None

    def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.existing_requested_at)


def _hospital(status=HospitalStatus.ACTIVE):
    return SimpleNamespace(id=uuid.UUID(NO_RECORD["hospital_id"]), name="새의원", status=status)


def test_window_bootstrap_measures_unrecorded_hospital_and_gap_is_retrying(monkeypatch):
    hospital = _hospital()
    dispatched = []
    monkeypatch.setattr(
        tasks,
        "_dispatch_weekly_sov_measurement",
        lambda _db, h, week_key, _at: dispatched.append((h.id, week_key)) or True,
    )
    opened = _capture_gaps(monkeypatch)
    registration = {"not_enrolled": [NO_RECORD, SHORT_SET]}

    owned = tasks._bootstrap_unmeasured_hospitals(
        _BootstrapDB(hospital), registration, tasks.date(2026, 10, 24), OBSERVED
    )
    tasks._report_monthly_cohort_gaps(registration, "2026-10", retrying_until=owned)

    assert dispatched == [(hospital.id, "2026-W43")]
    by_name = {item["hospital_name"]: item for item in opened}
    assert by_name["새의원"]["retrying_until"] == OBSERVED + tasks.COHORT_BOOTSTRAP_GRACE
    assert by_name["질문부족의원"]["retrying_until"] is None


def test_an_existing_weekly_run_is_not_sent_again_and_anchors_the_deadline(monkeypatch):
    """같은 주의 측정 실행이 이미 있으면 다시 보내지 않고, 그 요청 시각부터 24시간만 기다린다.

    tick마다 기한을 새로 밀면 끝내 편입되지 못한 병원이 영원히 '복구 중'에 남는다.
    """
    recent = OBSERVED - timedelta(hours=6)
    monkeypatch.setattr(
        tasks,
        "_dispatch_weekly_sov_measurement",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not redispatch")),
    )

    owned = tasks._bootstrap_unmeasured_hospitals(
        _BootstrapDB(_hospital(), existing_requested_at=recent),
        {"not_enrolled": [NO_RECORD]},
        tasks.date(2026, 10, 24),
        OBSERVED,
    )

    assert owned == {uuid.UUID(NO_RECORD["hospital_id"]): recent + tasks.COHORT_BOOTSTRAP_GRACE}


def test_a_passed_bootstrap_deadline_is_human_work(monkeypatch):
    """그 주 측정 요청에서 24시간이 지나도 편입되지 못했으면 자동 복구가 아니다 — 사람에게 연다."""
    monday = OBSERVED - timedelta(days=5)
    monkeypatch.setattr(
        tasks,
        "_dispatch_weekly_sov_measurement",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not redispatch")),
    )
    opened = _capture_gaps(monkeypatch)
    registration = {"not_enrolled": [NO_RECORD]}

    owned = tasks._bootstrap_unmeasured_hospitals(
        _BootstrapDB(_hospital(), existing_requested_at=monday),
        registration,
        tasks.date(2026, 10, 24),
        OBSERVED,
    )
    tasks._report_monthly_cohort_gaps(registration, "2026-10", retrying_until=owned)

    assert owned == {}
    assert opened[0]["retrying_until"] is None


def test_paused_or_failed_dispatch_is_not_owned(monkeypatch):
    monkeypatch.setattr(tasks, "_dispatch_weekly_sov_measurement", lambda *_a: False)
    registration = {"not_enrolled": [NO_RECORD]}

    paused = tasks._bootstrap_unmeasured_hospitals(
        _BootstrapDB(_hospital(HospitalStatus.PAUSED)), registration, tasks.date(2026, 10, 24), OBSERVED
    )
    not_sent = tasks._bootstrap_unmeasured_hospitals(
        _BootstrapDB(_hospital()), registration, tasks.date(2026, 10, 24), OBSERVED
    )

    assert paused == {} and not_sent == {}


def test_cohort_gap_is_sweep_owned_only_with_a_caller_deadline():
    """기한은 호출자가 준다(부트스트랩 요청 + 24시간). 없으면 사람의 일이다."""
    assert "MONTHLY_SOV_COHORT_GAP" in SWEEP_OWNED_INCIDENT_TYPES
    assert incident_policy("MONTHLY_SOV_COHORT_GAP").retry_window is None
    # 편입되면 1분 자율 복구의 근거 정리가 RETRYING도 닫는다.
    assert "MONTHLY_SOV_COHORT_GAP" in _RETRYING_RESOLVABLE


def test_weekly_monitoring_reports_human_gaps_before_the_window(monkeypatch):
    registration = {"enrolled": [], "not_enrolled": [NO_RECORD, SHORT_SET]}

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: tasks.arrow.get(2026, 10, 12, 9, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks, "register_convertible_tracking_sets", lambda *_a, **_k: registration)
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_a, **_k: [])
    opened = _capture_gaps(monkeypatch)

    tasks.run_weekly_monitoring.run()

    assert [(item["hospital_name"], item["period_key"]) for item in opened] == [
        ("질문부족의원", "2026-10")
    ]
