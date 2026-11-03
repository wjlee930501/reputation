"""1일 새벽 마감(00:15~07:00 KST) — 측정 마감 울타리·최종 적정성·동결 프로토콜 회귀."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.services import measurement_slots as slots_module
from app.services.measurement_manifest_policy import (
    manifest_observation_adequacy,
    slotted_observation_adequacy,
)
from app.services.measurement_slots import (
    checkpoint_answer,
    checkpoint_judgment,
    claim_slot_stage,
)
from app.services.monthly_manifest import (
    reopen_incomplete_manifest_for_recovery,
    summarize_manifest,
)
from app.services.monthly_period import (
    MonthlyPeriodError,
    is_monthly_recovery_window,
    monthly_final_judgment_at,
    monthly_recovery_deadline,
    monthly_report_finalization_at,
    reporting_period,
)
from app.services.sov_engine import same_measurement_basis
from app.workers.monthly_report_safety_net import REPAIR_SPACING
from app.workers.tasks import _resolve_monthly_measurement_period

KST = ZoneInfo("Asia/Seoul")
AUGUST = (2026, 8)
# 8월 달의 새벽 창: KST 9/1 00:15 ~ 07:00(배타) = UTC 8/31 15:15 ~ 22:00.
DEADLINE = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)


def _slot(
    repeat_no: int,
    *,
    answer_status: str = "PENDING",
    judgment_status: str = "PENDING",
    answer_attempt_count: int = 0,
    judgment_attempt_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        repeat_no=repeat_no,
        answer_status=answer_status,
        judgment_status=judgment_status,
        answer_attempt_count=answer_attempt_count,
        judgment_attempt_count=judgment_attempt_count,
    )


def _cell(slots, state="PENDING") -> SimpleNamespace:
    return SimpleNamespace(state=state, observation_slots=list(slots))


def _manifest(cells, repeat_count=2) -> SimpleNamespace:
    return SimpleNamespace(
        cells=list(cells),
        platform_provenance={"observation_slots": {"repeat_count": repeat_count}},
    )


def test_missing_repeats_stay_pending_and_finalize_limited_at_deadline() -> None:
    manifest = _manifest(
        [
            _cell([_slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED")]),
            _cell([_slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED")]),
        ]
    )

    before = manifest_observation_adequacy(manifest, deadline_reached=False)
    assert before.status == "IN_PROGRESS"
    assert (before.planned_slots, before.pending_slots) == (4, 2)

    after = manifest_observation_adequacy(manifest, deadline_reached=True)
    # 만들어지지 않은 반복은 실패한 답변이 아니라 pending이다 — 사기 결과가 아니다.
    assert after.status == "LIMITED"
    assert after.planned_slots == 4
    assert after.confirmed_slots == 2
    assert after.pending_slots == 2
    assert after.answer_failed_slots == 0


def test_no_confirmed_slots_finalize_unavailable_at_deadline() -> None:
    manifest = _manifest([_cell([_slot(1)])])
    adequacy = manifest_observation_adequacy(manifest, deadline_reached=True)
    assert adequacy.status == "UNAVAILABLE"
    assert adequacy.planned_slots == 2


def test_all_terminal_early_is_final_without_the_deadline() -> None:
    manifest = _manifest(
        [
            _cell(
                [
                    _slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                    _slot(2, answer_status="RECEIVED", judgment_status="AMBIGUOUS"),
                ]
            ),
        ]
    )
    adequacy = manifest_observation_adequacy(manifest, deadline_reached=False)
    assert adequacy.status == "LIMITED"


def test_corrupt_repeat_set_is_rejected_not_fabricated() -> None:
    manifest = _manifest(
        [
            _cell(
                [
                    _slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                    _slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                    _slot(9),
                ]
            ),
        ]
    )
    assert manifest_observation_adequacy(manifest, deadline_reached=True) is None


def test_empty_frozen_plan_is_not_a_fabricated_complete() -> None:
    manifest = _manifest([])
    assert manifest_observation_adequacy(manifest, deadline_reached=True) is None


def test_claim_refuses_at_and_after_the_deadline_before_touching_the_db() -> None:
    import uuid

    # DEADLINE은 과거다 — 세션을 건드리기 전에 거절돼야 한다(object()를 넘겨도 안전).
    assert (
        claim_slot_stage(object(), uuid.uuid4(), stage="ANSWER", deadline=DEADLINE)
        is None
    )
    assert (
        claim_slot_stage(object(), uuid.uuid4(), stage="JUDGMENT", deadline=DEADLINE)
        is None
    )


def test_late_answer_and_judgment_checkpoints_are_discarded() -> None:
    import uuid

    assert (
        checkpoint_answer(
            object(),
            uuid.uuid4(),
            {"citations": []},
            lease_token=uuid.uuid4(),
            deadline=DEADLINE,
        )
        is None
    )
    assert (
        checkpoint_judgment(
            object(),
            uuid.uuid4(),
            fingerprint="fp",
            result={"judgment": "CONFIRMED"},
            sov_record=SimpleNamespace(),
            lease_token=uuid.uuid4(),
            deadline=DEADLINE,
        )
        is None
    )


def test_no_deadline_keeps_claim_path_unchanged() -> None:
    """V0·주간(deadline=None) 호출은 울타리가 없다 — 슬롯이 없으면 None."""
    import uuid

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _DB:
        def execute(self, _stmt):
            return _Result()

    assert (
        claim_slot_stage(_DB(), uuid.uuid4(), stage="ANSWER", deadline=None) is None
    )


def test_month_start_dawn_is_a_new_measurement_basis() -> None:
    shared = {
        "policy_version": "v1",
        "provider_gateway": "g",
        "prompt_fingerprint": "p",
        "prompt_delivery": "d",
        "openai_tool_choice": "auto",
        "openai_use_web_search": True,
        "openai_model_query": "m",
        "gemini_model": "g",
        "judge_prompt_fingerprint": "jp",
        "judge_model": "jm",
        "query_design_version": "q1",
        "template_fingerprint": "t",
        "lexicon_fingerprint": "l",
        "slot_count": 10,
        "tracking_set_fingerprint": "f",
        "tracking_set_size": 15,
    }
    month_end = {**shared, "measurement_window": "month_end"}
    dawn = {**shared, "measurement_window": "month_start_dawn"}

    assert not same_measurement_basis(month_end, dawn)
    assert same_measurement_basis(dawn, dict(dawn))


def test_resolve_period_defaults_to_prior_month_only_inside_dawn() -> None:
    kst = lambda *a: datetime(*a, tzinfo=KST)  # noqa: E731

    inside = kst(2026, 9, 1, 5, 0)
    assert _resolve_monthly_measurement_period(
        inside.date(), None, None, observed_at=inside
    ) == AUGUST
    assert _resolve_monthly_measurement_period(
        inside.date(), 2026, 8, observed_at=inside
    ) == AUGUST
    # 부분 날짜·이번 달·미래 달·창 밖 시각은 모두 거절이다.
    assert _resolve_monthly_measurement_period(
        inside.date(), 2026, None, observed_at=inside
    ) is None
    assert _resolve_monthly_measurement_period(
        inside.date(), 2026, 9, observed_at=inside
    ) is None
    assert _resolve_monthly_measurement_period(
        inside.date(), 2027, 1, observed_at=inside
    ) is None
    after_deadline = kst(2026, 9, 1, 7, 0)
    assert _resolve_monthly_measurement_period(
        after_deadline.date(), 2026, 8, observed_at=after_deadline
    ) is None
    day_two = kst(2026, 9, 2, 0, 15)
    assert _resolve_monthly_measurement_period(
        day_two.date(), 2026, 8, observed_at=day_two
    ) is None
    month_end_day = kst(2026, 9, 26, 6, 0)
    assert _resolve_monthly_measurement_period(
        month_end_day.date(), 2026, 8, observed_at=month_end_day
    ) is None
    before_open = kst(2026, 9, 1, 0, 14)
    assert _resolve_monthly_measurement_period(
        before_open.date(), 2026, 8, observed_at=before_open
    ) is None


# ── 추가 회귀: 마감 울타리·종결 매니페스트·최종 적정성 일치·캘린더 경계 ──

def _fake_manifest(cells, *, repeat_count=2, period=(2026, 8), closed=True) -> SimpleNamespace:
    manifest = SimpleNamespace(
        cells=list(cells),
        configured_platforms={"chatgpt", "gemini"},
        platform_provenance={
            "observation_slots": {"version": 1, "repeat_count": repeat_count}
        },
        period_year=period[0],
        period_month=period[1],
        closed_at=datetime(2026, 8, 31, 22, 30, tzinfo=UTC) if closed else None,
    )
    for cell in manifest.cells:
        cell.manifest = manifest
    return manifest


def _stub_session():
    return SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        execute=lambda *a, **k: None,
        flush=lambda: None,
    )


def test_closed_terminal_manifest_never_reopens_in_the_dawn_window() -> None:
    inside = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)  # KST 9/1 05:00 — 새벽 창 안.
    limited = _fake_manifest(
        [
            _cell(
                [
                    _slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                    _slot(2, answer_status="FAILED", answer_attempt_count=3),
                ],
                state="SUCCESS",
            ),
            _cell(
                [
                    _slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                    _slot(2, answer_status="RECEIVED", judgment_status="CONFIRMED"),
                ],
                state="SUCCESS",
            ),
        ]
    )
    snapshot = (limited.closed_at, [c.state for c in limited.cells])
    for _ in range(3):
        assert (
            reopen_incomplete_manifest_for_recovery(_stub_session(), limited, now=inside)
            is False
        )
    assert (limited.closed_at, [c.state for c in limited.cells]) == snapshot


def test_closed_unavailable_manifest_never_reopens() -> None:
    inside = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    unavailable = _fake_manifest(
        [
            _cell(
                [
                    _slot(1, answer_status="FAILED", answer_attempt_count=3),
                    _slot(2, answer_status="FAILED", answer_attempt_count=3),
                ],
                state="FAILURE",
            )
        ]
    )
    assert (
        reopen_incomplete_manifest_for_recovery(_stub_session(), unavailable, now=inside)
        is False
    )
    assert unavailable.closed_at is not None


def test_closed_unresolved_incomplete_manifest_still_reopens() -> None:
    inside = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    incomplete = _fake_manifest(
        [
            _cell(
                [
                    _slot(1, answer_status="FAILED"),
                    _slot(2, answer_status="FAILED"),
                ],
                state="FAILURE",
            )
        ]
    )
    assert (
        reopen_incomplete_manifest_for_recovery(_stub_session(), incomplete, now=inside)
        is True
    )
    assert incomplete.closed_at is None


def test_summarize_manifest_agrees_with_shared_final_adequacy() -> None:
    cells = [
        _cell(
            [_slot(1, answer_status="RECEIVED", judgment_status="CONFIRMED")],
            state="SUCCESS",
        ),
        _cell([_slot(1, answer_status="RECEIVED", judgment_status="AMBIGUOUS")]),
    ]
    manifest = _fake_manifest(cells)
    adequacy = manifest_observation_adequacy(manifest, deadline_reached=True)
    assert adequacy is not None and adequacy.status == "LIMITED"
    summary = summarize_manifest(cells, closed=True, configured_platforms={"chatgpt"})
    assert summary.quality == "DEGRADED"
    assert "MEASUREMENT_SAMPLE_LIMITED" in summary.blockers


def test_empty_plan_is_blocked_not_fabricated_in_summary() -> None:
    assert slotted_observation_adequacy([], repeat_count=2, deadline_reached=True) is None


def test_recovery_window_predicate_is_safe_for_invalid_periods() -> None:
    moment = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    for bad_year, bad_month in ((2026, 0), (2026, 13), (1999, 12), (2201, 1)):
        assert is_monthly_recovery_window(moment, bad_year, bad_month) is False
    with pytest.raises(MonthlyPeriodError):
        reporting_period(2026, 13)


def test_claim_row_lock_wait_crossing_the_deadline_mutates_nothing() -> None:
    """락 대기 중 07:00을 넘으면 같은 호출 안에서도 거절 — 슬롯은 byte-identical."""
    import uuid as _uuid

    slot = SimpleNamespace(
        id=_uuid.uuid4(),
        lease_token=None,
        lease_expires_at=None,
        answer_status="PENDING",
        judgment_status="PENDING",
        answer_attempt_count=0,
        judgment_attempt_count=0,
        stage="PENDING",
        version=0,
    )
    before = dict(vars(slot))
    clock = {"t": datetime(2026, 8, 31, 21, 59, 59, tzinfo=UTC)}
    deadline = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)  # KST 9/1 07:00

    class _Result:
        def scalar_one_or_none(self):
            return slot

    class _DB:
        calls = 0
        flushes = 0

        def execute(self, _stmt):
            _DB.calls += 1
            # for-update 락 대기가 끝나니 이미 마감 시각을 지났다.
            clock["t"] = deadline
            return _Result()

        def add(self, _obj):
            pass

        def flush(self):
            _DB.flushes += 1

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return clock["t"].astimezone(tz) if tz else clock["t"]

        def __call__(self, *a, **k):
            return datetime(*a, **k)

    with patch.object(slots_module, "datetime", _FakeDt()):
        db = _DB()
        assert claim_slot_stage(db, slot.id, stage="ANSWER", deadline=deadline) is None
    assert _DB.calls == 1  # 선검사는 통과했고 락 뒤 재검사가 막았다.
    assert _DB.flushes == 0
    assert vars(slot) == before


def test_checkpoint_lock_wait_crossing_the_deadline_persists_nothing() -> None:
    """체크포인트 락이 마감을 넘기면 답변·판정·SovRecord가 새지 않는다."""
    import uuid as _uuid

    token = _uuid.uuid4()
    slot = SimpleNamespace(
        id=_uuid.uuid4(),
        lease_token=token,
        lease_expires_at=datetime(2026, 8, 31, 22, 15, tzinfo=UTC),
        answer_status="PENDING",
        judgment_status="PENDING",
        answer_attempt_count=1,
        judgment_attempt_count=0,
        stage="ANSWER",
        version=1,
    )
    before = dict(vars(slot))
    clock = {"t": datetime(2026, 8, 31, 21, 59, 59, tzinfo=UTC)}
    deadline = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    added: list[object] = []
    flushes = {"n": 0}

    class _Result:
        def scalar_one_or_none(self):
            clock["t"] = deadline  # 락 대기 종료 = 마감 통과
            return slot

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def add(self, obj):
            added.append(obj)

        def flush(self):
            flushes["n"] += 1

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return clock["t"].astimezone(tz) if tz else clock["t"]

        def __call__(self, *a, **k):
            return datetime(*a, **k)

    with patch.object(slots_module, "datetime", _FakeDt()):
        db = _DB()
        assert (
            checkpoint_answer(
                db, slot.id, {"citations": []}, lease_token=token, deadline=deadline
            )
            is None
        )
        assert (
            checkpoint_judgment(
                db,
                slot.id,
                fingerprint="fp",
                result={"judgment": "CONFIRMED"},
                sov_record=SimpleNamespace(),
                lease_token=token,
                deadline=deadline,
            )
            is None
        )
    assert added == [] and flushes["n"] == 0
    assert vars(slot) == before


@pytest.mark.parametrize(
    "year,month,deadline_utc,finalize_utc,judgment_utc",
    [
        (2026, 4, (2026, 4, 30, 22, 0), (2026, 4, 30, 22, 15), (2026, 5, 1, 0, 0)),
        (2027, 2, (2027, 2, 28, 22, 0), (2027, 2, 28, 22, 15), (2027, 3, 1, 0, 0)),
        (2028, 2, (2028, 2, 29, 22, 0), (2028, 2, 29, 22, 15), (2028, 3, 1, 0, 0)),
        (2026, 12, (2026, 12, 31, 22, 0), (2026, 12, 31, 22, 15), (2027, 1, 1, 0, 0)),
    ],
)
def test_calendar_boundaries_four_cases(year, month, deadline_utc, finalize_utc, judgment_utc) -> None:
    assert monthly_recovery_deadline(year, month) == datetime(
        *deadline_utc, tzinfo=UTC
    )
    assert monthly_report_finalization_at(year, month) == datetime(
        *finalize_utc, tzinfo=UTC
    )
    assert monthly_final_judgment_at(year, month) == datetime(
        *judgment_utc, tzinfo=UTC
    )


def test_safety_net_spacing_is_thirty_minutes_and_gated_at_finalization() -> None:
    assert REPAIR_SPACING == timedelta(minutes=30)
    # 안전망은 07:15 예정 최종화 시각부터 연다.
    assert monthly_report_finalization_at(2026, 8) == datetime(2026, 8, 31, 22, 15, tzinfo=UTC)


def test_report_copy_and_action_before_and_after_nine() -> None:
    from app.api.admin.operations_center_report_queries import (
        _report_action,
        _report_operator_copy,
    )

    before = _report_operator_copy("MISSING", preparing=True)
    assert "준비" in before[0] and "09:00" in before[1]
    after = _report_operator_copy("MISSING", preparing=False)
    assert after != before
    import uuid as _uuid

    action_before = _report_action(
        _uuid.uuid4(), state="MISSING", year=2026, month=8, preparing=True
    )
    action_after = _report_action(
        _uuid.uuid4(), state="MISSING", year=2026, month=8, preparing=False
    )
    assert action_after.kind == "GENERATE_MONTHLY_REPORT"
    assert action_before.kind != "GENERATE_MONTHLY_REPORT" or action_before is None


def test_registration_filter_bounds_period_consumers_to_eligible_hospitals(monkeypatch) -> None:
    """1일 가입 병원은 지난달 부트스트랩·코호트 공백에 들어가지 않는다."""
    import uuid as _uuid

    from app.workers import tasks

    eligible_id = _uuid.uuid4()
    day_one_id = _uuid.uuid4()
    eligible_h = SimpleNamespace(id=eligible_id, name="오래된 환자병원")
    day_one_h = SimpleNamespace(id=day_one_id, name="1일 가입병원")

    registration = {
        "blocked": [
            {"hospital_id": str(eligible_id), "reason": "no_set"},
            {"hospital_id": str(day_one_id), "reason": "no_set"},
        ],
        "not_enrolled": [
            {"hospital_id": str(eligible_id), "reason": "NO_SOV_RECORD"},
            {"hospital_id": str(day_one_id), "reason": "NO_SOV_RECORD"},
        ],
        "enrolled": [{"hospital_id": str(eligible_id)}],
    }
    seen: dict[str, object] = {}

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_a, **_k: tasks.arrow.get(2026, 9, 1, 5, 0, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", _Ctx)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "is_monthly_recovery_window", lambda *a, **k: True)
    monkeypatch.setattr(tasks, "register_convertible_tracking_sets", lambda *a, **k: registration)
    monkeypatch.setattr(tasks, "_commit_new_cohort_enrollments", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "_log_blocked_convertible_tracking_sets", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "eligible_hospital_ids", lambda *a, **k: {eligible_id})
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda _db: iter([eligible_h, day_one_h]))
    monkeypatch.setattr(tasks, "_round_robin_hospitals", lambda hs, _c: list(hs))
    monkeypatch.setattr(tasks, "_warn_monthly_cohort_over_limit", lambda *a, **k: None)
    dispatched: dict[str, object] = {}

    def _capture_sweep(*args, **kwargs):
        dispatched.update(kwargs)
        dispatched["hospitals"] = args[1] if len(args) > 1 else None

    monkeypatch.setattr(tasks, "_dispatch_monthly_sov_period_sweep", _capture_sweep)
    monkeypatch.setattr(
        tasks,
        "_bootstrap_unmeasured_hospitals",
        lambda _db, reg, *a, **k: seen.setdefault("bootstrap", reg) or {},
    )
    monkeypatch.setattr(
        tasks,
        "_report_monthly_cohort_gaps",
        lambda reg, period_key, **k: seen.setdefault("gaps", reg),
    )

    tasks.run_monthly_sov_measurement.run()

    for consumer in ("bootstrap", "gaps"):
        reg = seen[consumer]
        assert {str(i["hospital_id"]) for i in reg["not_enrolled"]} == {str(eligible_id)}
        assert {str(i["hospital_id"]) for i in reg["blocked"]} == {str(eligible_id)}
    # 전역 편입 결과는 그대로 쓰되, 스윕은 코호트∩eligible만 받았다.
    assert dispatched.get("year") == 2026 and dispatched.get("month") == 8
    hospitals_seen = dispatched.get("hospitals") or []
    assert {h.id for h in hospitals_seen} == {eligible_id}
