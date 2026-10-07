import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.celery_app import celery_app
from app.core.config import Settings
from app.models.operations import OperationRun
from app.services import sov_engine, sov_tracking_set
from app.services.monthly_manifest import (
    ManifestPolicyDrift,
    freeze_dispatch_manifest,
    summarize_manifest,
)
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.sov_tracking_set import (
    MEASUREMENT_WINDOW_MONTH_END,
    MEASUREMENT_WINDOW_MONTH_START,
    monthly_sov_guard_units,
    register_convertible_tracking_sets,
    register_tracking_set,
    tracking_set_fingerprint,
    tracking_set_is_valid,
    tracking_set_members,
)
from app.workers import operation_run_signals, tasks
from tests.operation_run_signal_support import (
    SYNC_DATABASE_URL,
    count_commits,
    seed_closed_monthly_sov_run,
    stored_operation_run,
)
from tests.operation_run_signal_support import (
    signal_store as _signal_store_fixture,  # noqa: F401
)


def _target(text: str, *, tracking: bool = True, intent: str = "LOCAL"):
    query = SimpleNamespace(id=uuid.uuid4(), query_intent=intent, query_text=text)
    variants = [
        SimpleNamespace(
            id=uuid.uuid4(),
            query_matrix_id=query.id,
            query_matrix=query,
            query_text=text,
            platform="CHATGPT",
            is_active=True,
        )
    ]
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=text,
        status="ACTIVE",
        in_tracking_set=tracking,
        priority="HIGH",
        target_month="2026-08",
        variants=variants,
    )


def test_tracking_set_range_default_and_fingerprint_contract():
    members = [_target(f"지역 병원 질문 {index}") for index in range(15)]

    assert Settings.model_fields["SOV_TRACKING_SET_N_DEFAULT"].default == 15
    assert tracking_set_is_valid(members[:10])
    assert tracking_set_is_valid(members)
    assert not tracking_set_is_valid(members[:9])
    assert tracking_set_fingerprint(members, n=15) != tracking_set_fingerprint(
        members, n=14
    )
    changed = [*members[:-1], _target("문구가 바뀐 질문")]
    assert tracking_set_fingerprint(members) != tracking_set_fingerprint(changed)


@pytest.mark.parametrize("value", [9, 16])
def test_tracking_set_setting_rejects_out_of_range_values(value):
    with pytest.raises(ValueError, match="between 10 and 15"):
        Settings(APP_ENV="development", SOV_TRACKING_SET_N_DEFAULT=value)


def test_tracking_members_are_active_flagged_and_local_only():
    local = _target("강남 병원 추천")
    info = _target("치질 초기 증상이 뭔지 알려줘", intent="INFO")
    unflagged = _target("강남 병원 비교", tracking=False)
    paused = _target("강남 전문의 추천")
    paused.status = "PAUSED"

    assert tracking_set_members([local, info, unflagged, paused]) == [local]


def test_register_tracking_set_flags_only_valid_local_members(monkeypatch):
    valid_hospital = SimpleNamespace(id=uuid.uuid4())
    blocked_hospital = SimpleNamespace(id=uuid.uuid4())
    valid_targets = [_target(f"지역 병원 질문 {index}", tracking=False) for index in range(16)]
    valid_targets[-1].in_tracking_set = True
    blocked_targets = [_target(f"부족한 지역 질문 {index}") for index in range(9)]
    targets_by_hospital = {
        valid_hospital.id: valid_targets,
        blocked_hospital.id: blocked_targets,
    }

    class _DB:
        flushes = 0

        def flush(self):
            self.flushes += 1

    db = _DB()
    monkeypatch.setattr(
        sov_tracking_set,
        "_load_targets",
        lambda _db, hospital_id: targets_by_hospital[hospital_id],
    )
    monkeypatch.setattr(
        sov_tracking_set,
        "propose_tracking_set",
        lambda _db, hospital_id, n: targets_by_hospital[hospital_id][:n],
    )

    valid_result = register_tracking_set(db, valid_hospital.id, n=15)
    blocked_result = register_tracking_set(db, blocked_hospital.id, n=15)

    assert [target.in_tracking_set for target in valid_targets] == [True] * 15 + [False]
    assert not any(target.in_tracking_set for target in blocked_targets)
    assert valid_result["valid"] is True
    assert valid_result["registered_size"] == 15
    assert blocked_result["valid"] is False
    assert blocked_result["registered_size"] == 0
    assert blocked_result["reason"] == (
        "not enough LOCAL ACTIVE targets: found 9, requires 10..15"
    )
    assert db.flushes == 2


def test_registration_uses_flagged_hospitals_and_reports_blockers(monkeypatch):
    hospitals = [
        SimpleNamespace(id=uuid.uuid4(), name="이름이 바뀐 첫 병원"),
        SimpleNamespace(id=uuid.uuid4(), name="이름이 바뀐 둘째 병원"),
        SimpleNamespace(id=uuid.uuid4(), name="이름이 바뀐 셋째 병원"),
    ]
    has_record = {hospital.id for hospital in hospitals}
    no_record_hospital = hospitals[2]
    has_record.remove(no_record_hospital.id)
    invalid_hospital = hospitals[1]

    class _DB:
        def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: hospitals)
            )

    monkeypatch.setattr(
        sov_tracking_set,
        "_hospital_has_sov_record",
        lambda _db, hospital_id: hospital_id in has_record,
    )

    def _register(_db, hospital_id, n):
        valid = hospital_id != invalid_hospital.id
        return {
            "hospital_id": str(hospital_id),
            "requested_size": n,
            "registered_size": n if valid else 0,
            "valid": valid,
            "reason": None if valid else "not enough LOCAL ACTIVE targets",
        }

    monkeypatch.setattr(sov_tracking_set, "register_tracking_set", _register)

    result = register_convertible_tracking_sets(_DB(), n=15)

    assert result["target_count"] == 3
    assert {item["name"] for item in result["registered"]} == {
        hospitals[0].name,
    }
    assert result["blocked"] == [
        {
            "name": invalid_hospital.name,
            "reason": "not enough LOCAL ACTIVE targets",
            "hospital_id": str(invalid_hospital.id),
        },
        {
            "name": no_record_hospital.name,
            "reason": "no SovRecord",
            "hospital_id": str(no_record_hospital.id),
        },
    ]


def test_enroll_new_flags_valid_hospitals_and_reports_gaps(monkeypatch):
    """계약이 늦은 병원도 기록+유효 세트가 있으면 편입, 없으면 사유와 함께 not_enrolled."""
    good = SimpleNamespace(id=uuid.uuid4(), name="새 병원", monthly_sov_cohort=False)
    no_record = SimpleNamespace(id=uuid.uuid4(), name="기록 없음", monthly_sov_cohort=False)
    short = SimpleNamespace(id=uuid.uuid4(), name="세트 부족", monthly_sov_cohort=False)

    class _DB:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            rows = [] if self.calls == 1 else [good, no_record, short]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    monkeypatch.setattr(
        sov_tracking_set, "_hospital_has_sov_record", lambda _db, hid: hid != no_record.id
    )
    monkeypatch.setattr(
        sov_tracking_set,
        "register_tracking_set",
        lambda _db, hid, n: {"valid": hid == good.id, "reason": "not enough LOCAL ACTIVE targets"},
    )

    without = register_convertible_tracking_sets(_DB(), n=15)
    assert without["enrolled"] == [] and without["not_enrolled"] == []
    assert good.monthly_sov_cohort is False

    result = register_convertible_tracking_sets(_DB(), n=15, enroll_new=True)

    assert good.monthly_sov_cohort is True
    assert no_record.monthly_sov_cohort is False and short.monthly_sov_cohort is False
    assert [item["name"] for item in result["enrolled"]] == ["새 병원"]
    assert {(item["name"], item["reason"]) for item in result["not_enrolled"]} == {
        ("기록 없음", "no SovRecord"),
        ("세트 부족", "not enough LOCAL ACTIVE targets"),
    }


def _monthly_window_db():
    class _DB:
        commits = 0

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    return _DB()


def _patch_monthly_window(monkeypatch, db, registration, cohort):
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_a, **_k: tasks.arrow.get(2026, 10, 25, 12, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(tasks, "register_convertible_tracking_sets", lambda *_a, **_k: registration)
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_a, **_k: cohort)
    monkeypatch.setattr(tasks, "_ensure_monthly_sov_operation_run", lambda *_a, **_k: None)


def test_monthly_window_opens_one_gap_incident_per_unenrolled_hospital(monkeypatch):
    hospital_id = uuid.uuid4()
    registration = {
        "registered": [],
        "blocked": [],
        "enrolled": [],
        "not_enrolled": [
            {"name": "기록 없음", "reason": "no SovRecord", "hospital_id": str(hospital_id)}
        ],
    }
    db = _monthly_window_db()
    _patch_monthly_window(monkeypatch, db, registration, [])
    opened: list[dict] = []
    monkeypatch.setattr(tasks, "open_monthly_cohort_gap", lambda **kw: opened.append(kw) or kw)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)

    tasks.run_monthly_sov_measurement.run()

    assert opened == [
        {
            "hospital_id": hospital_id,
            "hospital_name": "기록 없음",
            "period_key": "2026-10",
            "reason": "no SovRecord",
        }
    ]


def test_new_enrollment_is_committed_before_dispatch(monkeypatch):
    db = _monthly_window_db()
    registration = {"enrolled": [{"hospital_id": str(uuid.uuid4()), "name": "새 병원"}]}
    _patch_monthly_window(monkeypatch, db, registration, [])

    tasks.run_monthly_sov_measurement.run()

    assert db.commits == 1


def test_cohort_over_limit_measures_everyone_and_opens_one_warning(monkeypatch, caplog):
    cohort = [SimpleNamespace(id=uuid.uuid4(), name=f"병원{i}") for i in range(3)]
    db = _monthly_window_db()
    _patch_monthly_window(monkeypatch, db, {}, cohort)
    ensured: list[object] = []
    monkeypatch.setattr(
        tasks, "_ensure_monthly_sov_operation_run", lambda _db, hospital, *_a: ensured.append(hospital)
    )
    monkeypatch.setattr(tasks.settings, "SOV_MONTHLY_COHORT_LIMIT", 2)
    opened: list[dict] = []
    monkeypatch.setattr(tasks, "open_monthly_cohort_over_limit", lambda **kw: opened.append(kw) or kw)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        tasks.run_monthly_sov_measurement.run()

    assert ensured == cohort  # 상한 초과여도 한 곳도 빠지지 않는다
    assert opened == [{"period_key": "2026-10", "cohort_size": 3, "limit": 2}]
    assert any("exceeds cost warning threshold" in r.getMessage() for r in caplog.records)


def test_cohort_at_or_under_limit_opens_no_warning(monkeypatch):
    cohort = [SimpleNamespace(id=uuid.uuid4(), name="병원")]
    _patch_monthly_window(monkeypatch, _monthly_window_db(), {}, cohort)
    monkeypatch.setattr(tasks.settings, "SOV_MONTHLY_COHORT_LIMIT", 1)
    monkeypatch.setattr(
        tasks, "open_monthly_cohort_over_limit", lambda **_k: pytest.fail("no warning expected")
    )

    tasks.run_monthly_sov_measurement.run()


def test_monthly_cohort_membership_survives_hospital_rename():
    hospital = SimpleNamespace(monthly_sov_cohort=True, name="완전히 바뀐 병원명")

    assert sov_tracking_set._hospital_matches_monthly_cohort(hospital)


def test_cohort_returns_every_enrolled_valid_hospital_without_cutting(monkeypatch):
    first = SimpleNamespace(id=uuid.uuid4(), name="첫 병원", monthly_sov_cohort=True)
    second = SimpleNamespace(id=uuid.uuid4(), name="둘째 병원", monthly_sov_cohort=True)
    invalid = SimpleNamespace(id=uuid.uuid4(), name="부족 병원", monthly_sov_cohort=True)
    outsider = SimpleNamespace(id=uuid.uuid4(), name="다른 병원", monthly_sov_cohort=False)
    targets = {
        first.id: [_target(f"첫 병원 질문 {index}") for index in range(15)],
        second.id: [_target(f"둘째 병원 질문 {index}") for index in range(10)],
        invalid.id: [_target(f"부족 병원 질문 {index}") for index in range(9)],
        outsider.id: [_target(f"외부 병원 질문 {index}") for index in range(15)],
    }
    monkeypatch.setattr(
        sov_tracking_set,
        "_convertible_hospitals",
        lambda _db: [outsider, first, second, invalid],
    )
    monkeypatch.setattr(
        sov_tracking_set,
        "_load_targets",
        lambda _db, hospital_id: targets[hospital_id],
    )

    # 상한으로 자르지 않는다 — 편입·유효 세트 조건을 만족한 병원은 전부 측정 대상이다.
    # (보호 의도: 외부 병원·세트 부족 병원은 여전히 제외)
    assert sov_tracking_set.iter_monthly_sov_cohort(object()) == [first, second]


class _SpecDB:
    def get(self, _model, item_id):
        return self.rows[item_id]

    def __init__(self, targets, hospital_id):
        self.rows = {
            variant.query_matrix_id: variant.query_matrix
            for target in targets
            for variant in target.variants
        }
        for query in self.rows.values():
            query.hospital_id = hospital_id


class _ManifestSession:
    def __init__(self):
        self.manifest = None
        self.flush_count = 0

    def execute(self, statement):
        manifest = None if "monthly_reports.id" in str(statement) else self.manifest

        class _Result:
            def scalar_one_or_none(self):
                return manifest

        return _Result()

    def add(self, value):
        self.manifest = value

    def flush(self):
        self.flush_count += 1


def _dispatch_specs(target_count: int, *, platforms: tuple[str, ...]) -> list[dict]:
    specs = []
    for index in range(target_count):
        query_id = uuid.uuid4()
        target_id = uuid.uuid4()
        for platform in platforms:
            specs.append(
                {
                    "query_id": query_id,
                    "query_text": f"지역 병원 질문 {index}",
                    "platform": platform,
                    "target_id": target_id,
                    "variant_id": uuid.uuid4(),
                    "query_intent": "LOCAL",
                }
            )
    return specs


def test_month_end_tracking_freeze_supersedes_weekly_successful_uncapped_denominator():
    session = _ManifestSession()
    hospital_id = uuid.uuid4()
    weekly_specs = _dispatch_specs(105, platforms=("chatgpt", "gemini"))
    tracking_specs = _dispatch_specs(15, platforms=("chatgpt", "gemini"))

    weekly_manifest = freeze_dispatch_manifest(
        session,
        hospital_id,
        2026,
        8,
        weekly_specs,
        gemini_configured=True,
    )
    weekly_manifest.cells[0].state = "SUCCESS"
    assert summarize_manifest(
        weekly_manifest.cells,
        closed=False,
        configured_platforms=weekly_manifest.configured_platforms,
    ).planned_count == 210

    monthly_manifest = freeze_dispatch_manifest(
        session,
        hospital_id,
        2026,
        8,
        tracking_specs,
        gemini_configured=True,
        measurement_protocol_kwargs={
            "measurement_window": MEASUREMENT_WINDOW_MONTH_END,
            "tracking_set_fingerprint": "locked-tracking-set",
            "tracking_set_size": 15,
        },
    )

    summary = summarize_manifest(
        monthly_manifest.cells,
        closed=False,
        configured_platforms=monthly_manifest.configured_platforms,
    )
    assert monthly_manifest is weekly_manifest
    assert summary.planned_count == 15 * 2
    assert summary.failed_count == 15 * 2
    assert monthly_manifest.closed_at is None
    assert monthly_manifest.platform_provenance["measurement_protocol"] == (
        sov_engine.measurement_protocol(
            measurement_window=MEASUREMENT_WINDOW_MONTH_END,
            tracking_set_fingerprint="locked-tracking-set",
            tracking_set_size=15,
        )
    )


def test_month_end_tracking_freeze_rejects_closed_weekly_uncapped_denominator():
    session = _ManifestSession()
    hospital_id = uuid.uuid4()
    weekly_specs = _dispatch_specs(105, platforms=("chatgpt", "gemini"))
    tracking_specs = _dispatch_specs(15, platforms=("chatgpt", "gemini"))

    weekly_manifest = freeze_dispatch_manifest(
        session,
        hospital_id,
        2026,
        8,
        weekly_specs,
        gemini_configured=True,
    )
    weekly_manifest.closed_at = datetime(2026, 9, 1, tzinfo=UTC)
    assert summarize_manifest(
        weekly_manifest.cells,
        closed=True,
        configured_platforms=weekly_manifest.configured_platforms,
    ).planned_count == 210

    with pytest.raises(ManifestPolicyDrift, match="closed"):
        freeze_dispatch_manifest(
            session,
            hospital_id,
            2026,
            8,
            tracking_specs,
            gemini_configured=True,
            measurement_protocol_kwargs={
                "measurement_window": MEASUREMENT_WINDOW_MONTH_END,
                "tracking_set_fingerprint": "locked-tracking-set",
                "tracking_set_size": 15,
            },
        )

    summary = summarize_manifest(
        weekly_manifest.cells,
        closed=True,
        configured_platforms=weekly_manifest.configured_platforms,
    )
    assert summary.planned_count == 105 * 2
    assert weekly_manifest.closed_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert weekly_manifest.platform_provenance["measurement_protocol"] == (
        sov_engine.measurement_protocol()
    )


@pytest.mark.parametrize(
    ("cell_variant", "selected_variant"),
    [(None, uuid.uuid4()), (uuid.uuid4(), None)],
)
def test_monthly_remasure_matches_failed_cell_across_null_variant_identity(
    cell_variant, selected_variant
):
    query_id = uuid.uuid4()
    selected_target_id = uuid.uuid4()
    cell = SimpleNamespace(
        query_matrix_id=query_id,
        query_text="지역 병원 추천",
        platform="chatgpt",
        query_target_id=None,
        query_variant_id=cell_variant,
        state="FAILED",
    )
    selected = [
        {
            "query_id": query_id,
            "query_text": cell.query_text,
            "platform": "chatgpt",
            "target_id": selected_target_id,
            "variant_id": selected_variant,
        }
    ]

    pending = tasks._pending_weekly_manifest_specs(
        SimpleNamespace(cells=[cell]), selected
    )

    assert len(pending) == 1
    assert pending[0]["manifest_cell"] is cell
    assert not tasks._selected_weekly_manifest_is_resolved(
        SimpleNamespace(cells=[cell]), selected
    )

    cell.state = "SUCCESS"
    assert tasks._selected_weekly_manifest_is_resolved(
        SimpleNamespace(cells=[cell]), selected
    )


def test_remasure_does_not_match_different_non_null_tracking_targets():
    query_id = uuid.uuid4()
    cell = SimpleNamespace(
        query_matrix_id=query_id,
        query_text="지역 병원 추천",
        platform="chatgpt",
        query_target_id=uuid.uuid4(),
        query_variant_id=None,
        state="FAILED",
    )
    selected = [
        {
            "query_id": query_id,
            "platform": "chatgpt",
            "target_id": uuid.uuid4(),
            "variant_id": uuid.uuid4(),
        }
    ]

    assert tasks._pending_weekly_manifest_specs(
        SimpleNamespace(cells=[cell]), selected
    ) == []


def test_monthly_specs_ignore_priority_and_caps_but_keep_legacy_helpers(monkeypatch):
    targets = [_target(f"지역 병원 질문 {index}") for index in range(15)]
    hospital = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(tasks.settings, "OPENROUTER_API_KEY", "")

    specs, trimmed = tasks._build_measurement_specs(
        db=_SpecDB(targets, hospital.id),
        hospital=hospital,
        query_targets=targets,
        fallback_queries=[],
        is_even_week=False,
        is_month_start=False,
        high_priority_cap=1,
        total_spec_cap=1,
        measurement_mode="monthly",
    )

    assert len(specs) == 15
    assert trimmed == 0
    assert hasattr(tasks, "_priority_included")
    assert hasattr(tasks, "_apply_high_priority_cap")
    assert hasattr(tasks, "_apply_total_spec_cap")
    assert hasattr(tasks, "adjust_query_priorities")


def test_measurement_specs_exclude_info_for_both_modes(monkeypatch):
    info = _target("치질 초기 증상이 뭔지 알려줘", intent="INFO")
    hospital = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(tasks.settings, "OPENROUTER_API_KEY", "")

    for mode in ("weekly", "monthly"):
        specs, _ = tasks._build_measurement_specs(
            db=_SpecDB([info], hospital.id),
            hospital=hospital,
            query_targets=[info],
            fallback_queries=[],
            measurement_mode=mode,
            high_priority_cap=-1,
            total_spec_cap=-1,
        )
        assert specs == []


def test_monthly_basis_tracks_window_fingerprint_and_size():
    base = sov_engine.measurement_protocol(
        measurement_window=MEASUREMENT_WINDOW_MONTH_END,
        tracking_set_fingerprint="set-a",
        tracking_set_size=15,
    )

    assert sov_engine.same_measurement_basis(base, dict(base))
    assert not sov_engine.same_measurement_basis(
        base, {**base, "measurement_window": MEASUREMENT_WINDOW_MONTH_START}
    )
    assert not sov_engine.same_measurement_basis(
        base, {**base, "tracking_set_fingerprint": "set-b"}
    )
    assert not sov_engine.same_measurement_basis(base, {**base, "tracking_set_size": 14})
    weekly = sov_engine.measurement_protocol()
    assert sov_engine.same_measurement_basis(weekly, dict(weekly))


def _cell(key: str, mentioned: bool) -> ManifestCellInput:
    attempt = CellAttempt(
        record_id=uuid.uuid4(),
        measured_at=None,
        succeeded=True,
        is_mentioned=mentioned,
        answer_model="fixed-model",
        search_calls=1,
    )
    return ManifestCellInput(
        query_key=key,
        query_text=key,
        platform="chatgpt",
        query_intent="LOCAL",
        state="SUCCESS",
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
        query_intent_source="FROZEN",
        attempts=(attempt,),
    )


def test_window_mismatch_uses_existing_policy_changed_reason():
    current = sov_engine.measurement_protocol(
        measurement_window=MEASUREMENT_WINDOW_MONTH_END,
        tracking_set_fingerprint="same",
        tracking_set_size=15,
    )
    prior = {**current, "measurement_window": MEASUREMENT_WINDOW_MONTH_START}
    summary = build_monthly_sov(
        (_cell("q", True),),
        ("chatgpt",),
        prior_cells=(_cell("q", False),),
        prior_platforms=("chatgpt",),
        current_protocol=current,
        prior_protocol=prior,
    )

    assert summary.comparison.reason == "MEASUREMENT_POLICY_CHANGED"
    assert summary.comparison.change_pct is None


def test_guard_defaults_pin_locked_august_conversion_envelope():
    assert Settings.model_fields["COST_GUARD_MONTHLY_SOV_QUERIES"].default == 4260
    assert Settings.model_fields["COST_GUARD_DAILY_SOV_QUERIES"].default == 1260
    assert Settings.model_fields["SOV_MONTHLY_COHORT_LIMIT"].default == 7
    assert 7 * 15 * 2 * 5 + 210 == 1260
    assert 3000 + 1260 == 4260
    assert monthly_sov_guard_units(
        3,
        15,
        v0_new=1,
        retry=45,
        weekly_remaining_hospitals=2,
    ) == 1145


def test_converted_cohort_is_not_dispatched_by_weekly_beat_outside_month_end_window(
    monkeypatch,
):
    """월말 창 밖에서도 전환 코호트는 주간 배치에서 항상 빠진다."""
    converted = SimpleNamespace(id=uuid.uuid4())

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [converted]

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(
            2026, 8, 10, 12, tzinfo="Asia/Seoul"
        ),
    )
    monkeypatch.setattr(
        tasks, "register_convertible_tracking_sets", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_args, **_kwargs: [converted])
    monkeypatch.setattr(tasks.settings, "SOV_MONTHLY_COHORT_LIMIT", 7)
    monkeypatch.setattr(
        tasks,
        "_ensure_weekly_sov_operation_run",
        lambda *_args, **_kwargs: pytest.fail("converted hospital reached weekly freeze path"),
    )
    monkeypatch.setattr(
        tasks.adjust_query_priorities,
        "apply_async",
        lambda **_kwargs: pytest.fail("empty weekly cohort adjusted priorities"),
    )

    tasks.run_weekly_monitoring.run()


@pytest.mark.parametrize("beat", ["weekly", "monthly"])
def test_measurement_beats_log_each_blocked_tracking_set(
    monkeypatch, caplog, beat
):
    hospital_id = str(uuid.uuid4())
    registration = {
        "registered": [],
        "blocked": [
            {"name": "노원탑365", "reason": "not found"},
            {"name": "마포성모탑", "reason": "ambiguous"},
            {
                "name": "행복드림 의원",
                "reason": "not enough LOCAL ACTIVE targets",
                "hospital_id": hospital_id,
            },
            {"name": "잘못된 세트", "reason": "invalid SoV set"},
        ],
    }

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks,
        "register_convertible_tracking_sets",
        lambda *_args, **_kwargs: registration,
    )
    monkeypatch.setattr(tasks, "iter_monthly_sov_cohort", lambda *_args, **_kwargs: [])
    if beat == "monthly":
        monkeypatch.setattr(
            tasks.arrow,
            "now",
            lambda *_args, **_kwargs: tasks.arrow.get(
                2026, 8, 31, 12, tzinfo="Asia/Seoul"
            ),
        )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        if beat == "weekly":
            tasks.run_weekly_monitoring.run()
        else:
            tasks.run_monthly_sov_measurement.run()

    warnings = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Conversion tracking set blocked:")
    ]
    assert len(warnings) == len(registration["blocked"])
    assert [record.getMessage() for record in warnings] == [
        "Conversion tracking set blocked: name=노원탑365 reason=not found",
        "Conversion tracking set blocked: name=마포성모탑 reason=ambiguous",
        (
            "Conversion tracking set blocked: name=행복드림 의원 "
            "reason=not enough LOCAL ACTIVE targets"
        ),
        "Conversion tracking set blocked: name=잘못된 세트 reason=invalid visibility set",
    ]
    assert all("SoV" not in record.getMessage() for record in warnings)
    assert [hasattr(record, "hospital_id") for record in warnings] == [
        False,
        False,
        True,
        False,
    ]
    assert warnings[2].hospital_id == hospital_id


def test_monthly_measurement_registers_locked_names_before_cohort(monkeypatch):
    order: list[object] = []

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 8, 31, 12, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks,
        "register_convertible_tracking_sets",
        lambda *_args, **_kwargs: order.append("register") or {"registered": [], "blocked": []},
    )
    monkeypatch.setattr(
        tasks,
        "iter_monthly_sov_cohort",
        lambda *_args, **_kwargs: order.append("cohort") or [],
    )

    tasks.run_monthly_sov_measurement.run()

    assert order == ["register", "cohort"]


def test_august_conversion_batch_uses_august_and_preserves_july(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="장편한외과", monthly_sov_cohort=True
    )
    july = SimpleNamespace(id=uuid.uuid4(), version=3, pdf_path="gs://reports/july.pdf")
    july_snapshot = (july.id, july.version, july.pdf_path)
    run_id = uuid.uuid4()
    built: list[tuple[int, int]] = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [hospital]

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def get(self, _model, item_id):
            if item_id == run_id:
                return SimpleNamespace(state=tasks.OperationRunState.SUCCEEDED)
            return None

        def rollback(self):
            pytest.fail("August conversion batch rolled back")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "eligible_hospital_ids", lambda *_args: [hospital.id])
    monkeypatch.setattr(tasks, "_monthly_sov_measurement_succeeded", lambda *_args: True)
    monkeypatch.setattr(
        tasks, "_start_scheduled_monthly_operation_run", lambda *_args: (run_id, False)
    )
    monkeypatch.setattr(tasks, "_latest_monthly_report_operation_run", lambda *_args: None)

    def _latest(_db, hospital_id, year, month):
        assert hospital_id == hospital.id
        assert (year, month) == (2026, 8)
        return None

    def _build(_db, observed_hospital, anchor, **_kwargs):
        assert observed_hospital is hospital
        built.append((anchor.year, anchor.month))
        return "created"

    monkeypatch.setattr(tasks, "_latest_monthly_report", _latest)
    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", _build)
    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", lambda *_args: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(
            2026, 8, 31, 12, 0, tzinfo="Asia/Seoul"
        ),
    )

    result = tasks.run_monthly_reports.run()

    assert result["status"] == "SUCCEEDED"
    assert built == [(2026, 8)]
    assert (july.id, july.version, july.pdf_path) == july_snapshot


def test_august_conversion_batch_skips_without_successful_measurement(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="행복드림의원", monthly_sov_cohort=True
    )

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [hospital]

    class _DB:
        def execute(self, _stmt):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", _DB)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "eligible_hospital_ids", lambda *_args: [hospital.id])
    monkeypatch.setattr(tasks, "_monthly_sov_measurement_succeeded", lambda *_args: False)
    monkeypatch.setattr(tasks, "_latest_monthly_report_operation_run", lambda *_args: None)
    monkeypatch.setattr(
        tasks,
        "_build_monthly_report_for_hospital",
        lambda *_args, **_kwargs: pytest.fail("report built before monthly measurement success"),
    )
    monkeypatch.setattr(tasks, "_record_weekly_sov_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(
            2026, 8, 31, 12, 0, tzinfo="Asia/Seoul"
        ),
    )

    assert tasks.run_monthly_reports.run() == {
        "status": "PARTIAL",
        "total_count": 1,
        "success_count": 0,
        "failure_count": 1,
    }


def test_august_conversion_does_not_load_july_measurement_cells():
    class _DB:
        def execute(self, _stmt):
            pytest.fail("August conversion queried a prior monthly manifest")

    august = tasks.arrow.get(2026, 8, 31, 12, tzinfo="Asia/Seoul")

    assert tasks._prior_monthly_manifest(_DB(), uuid.uuid4(), august) is None


def test_monthly_measurement_copy_guard_strings_are_preserved():
    source = Path(tasks.__file__).read_text()

    assert '"Hospital %s is no longer in the monthly measurement cohort"' in source
    assert '"Monthly measurement window is not open: %s"' in source


def test_partial_monthly_operation_is_rearmed_for_failed_cell_retry(signal_store):
    # Real PG: the PARTIAL re-arm is a conditional UPDATE, not an ORM mutation.
    _factory, hospital_id = signal_store
    seeded = seed_closed_monthly_sov_run(
        hospital_id,
        "2026-08",
        tasks.OperationRunState.PARTIAL,
        safe_error_code="MONTHLY_SOV_MEASUREMENT_PARTIAL",
    )

    with operation_run_signals.SyncSessionLocal() as db:
        commits = count_commits(db)
        run = tasks._ensure_monthly_sov_operation_run(
            db,
            SimpleNamespace(id=hospital_id),
            "2026-08",
            datetime(2026, 9, 7, 14, 59, 59, tzinfo=UTC),
        )

    assert run is not None
    assert run.id == seeded.id
    assert run.state == tasks.OperationRunState.REQUESTED
    assert run.task_id != seeded.task_id
    assert run.completed_at is None
    assert run.failure_count == 0
    assert run.version == 4
    assert len(commits) == 1
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.REQUESTED
    assert stored.task_id == run.task_id
    assert stored.lease_owner is None
    assert stored.lease_expires_at is None
    assert stored.safe_error_code is None
    assert stored.version == 4


def _running_monthly_operation(lease_expires_at: datetime):
    return SimpleNamespace(
        state=tasks.OperationRunState.RUNNING,
        task_id=str(uuid.uuid4()),
        queued_at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
        started_at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
        completed_at=None,
        lease_owner="chunk-worker",
        lease_expires_at=lease_expires_at,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        safe_error_code=None,
        safe_error_message=None,
        version=7,
    )


class _SingleRunDB:
    def __init__(self, existing):
        self.existing = existing
        self.commits = 0

    def execute(self, _stmt):
        existing = self.existing

        class _Result:
            def scalar_one_or_none(self):
                return existing

        return _Result()

    def commit(self):
        self.commits += 1


def _seed_running_monthly_operation(
    hospital_id: uuid.UUID, period_key: str, lease_expires_at: datetime
) -> OperationRun:
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=tasks.OperationRunState.RUNNING,
        idempotency_key=f"monthly-sov:{hospital_id}:{period_key}",
        task_id=str(uuid.uuid4()),
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={},
        queued_at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
        started_at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
        lease_owner="chunk-worker",
        lease_expires_at=lease_expires_at,
        version=7,
    )
    with operation_run_signals.SyncSessionLocal() as db:
        db.add(run)
        db.commit()
    return run


def _stored_operation(run_id: uuid.UUID) -> OperationRun:
    with operation_run_signals.SyncSessionLocal() as db:
        return db.execute(
            select(OperationRun).where(OperationRun.id == run_id)
        ).scalar_one()


def test_monthly_run_left_running_after_its_lease_expired_is_rearmed(signal_store):
    # A chunk whose continuation publish failed (Celery raises Reject, postrun state
    # REJECTED) or whose RETRY requeue write failed keeps RUNNING with its lease.
    _factory, hospital_id = signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )

    with operation_run_signals.SyncSessionLocal() as db:
        run = tasks._ensure_monthly_sov_operation_run(
            db, SimpleNamespace(id=hospital_id), "2026-09", observed_at
        )

    assert run is not None
    assert run.id == seeded.id
    assert run.state == tasks.OperationRunState.REQUESTED
    assert run.task_id != seeded.task_id
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    assert run.version == 8
    stored = _stored_operation(seeded.id)
    assert stored.state == tasks.OperationRunState.REQUESTED
    assert stored.task_id == run.task_id
    assert stored.version == 8


def _run_with_timeout(target, timeout: float = 10.0):
    outcome: dict[str, object] = {}

    def _target():
        try:
            outcome["value"] = target()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "concurrent session did not finish in time"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def test_monthly_rearm_and_redelivered_lease_reclaim_race_exactly_one_wins(
    signal_store, monkeypatch, caplog
):
    # The re-arm reads the expired RUNNING row; before it writes, a redelivered
    # message re-claims the expired lease on another connection and commits.
    _factory, hospital_id = signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )
    claim_results: list[int | None] = []
    rearm_read = threading.Event()
    lease_expired = tasks._operation_lease_expired

    def _reclaim_between_read_and_write(run, at):
        expired = lease_expired(run, at)
        if not rearm_read.is_set():
            rearm_read.set()
            claim_results.append(
                _run_with_timeout(
                    lambda: operation_run_signals._claim_safely(
                        seeded.id, seeded.task_id, observed_at, redelivered=True
                    )
                )
            )
        return expired

    monkeypatch.setattr(tasks, "_operation_lease_expired", _reclaim_between_read_and_write)

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        rearmed = _run_with_timeout(
            lambda: _ensure_in_new_session(hospital_id, observed_at)
        )

    assert rearm_read.is_set()
    assert claim_results == [8]
    assert rearmed is None
    assert [rearmed is not None, claim_results[0] is not None].count(True) == 1
    stored = _stored_operation(seeded.id)
    assert stored.state == tasks.OperationRunState.RUNNING
    assert stored.task_id == seeded.task_id
    assert stored.lease_owner == seeded.task_id
    assert stored.lease_expires_at > observed_at
    assert stored.version == 8
    assert f"run {seeded.id} was re-claimed or changed concurrently" in caplog.text


def test_monthly_rearm_committed_first_fences_out_the_redelivered_reclaim(signal_store):
    _factory, hospital_id = signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )

    rearmed = _ensure_in_new_session(hospital_id, observed_at)
    claimed = _run_with_timeout(
        lambda: operation_run_signals._claim_safely(
            seeded.id, seeded.task_id, observed_at, redelivered=True
        )
    )

    assert rearmed is not None
    assert claimed is None
    stored = _stored_operation(seeded.id)
    assert stored.state == tasks.OperationRunState.REQUESTED
    assert stored.task_id == rearmed.task_id != seeded.task_id
    assert stored.lease_owner is None
    assert stored.version == 8


def _ensure_in_new_session(hospital_id: uuid.UUID, observed_at: datetime):
    with operation_run_signals.SyncSessionLocal() as db:
        return tasks._ensure_monthly_sov_operation_run(
            db, SimpleNamespace(id=hospital_id), "2026-09", observed_at
        )


def test_monthly_run_with_a_live_chunk_lease_is_not_redispatched():
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    existing = _running_monthly_operation(observed_at + timedelta(minutes=30))
    db = _SingleRunDB(existing)

    run = tasks._ensure_monthly_sov_operation_run(
        db, SimpleNamespace(id=uuid.uuid4()), "2026-09", observed_at
    )

    assert run is None
    assert existing.state == tasks.OperationRunState.RUNNING
    assert existing.lease_owner == "chunk-worker"
    assert db.commits == 0


# ── real row-lock contention: monthly re-arm vs. redelivered lease claim ────────

_LOCK_WAIT_SECONDS = 10.0
# Each session gives up on a lock or statement long before the suite could hang; the
# holder is always released (finally) well before these fire in a passing run.
_SESSION_TIMEOUT_MS = 15_000


@pytest.fixture(name="bounded_signal_store")
def bounded_signal_store(signal_store, monkeypatch):
    engine = create_engine(
        SYNC_DATABASE_URL,
        connect_args={
            "options": f"-c lock_timeout={_SESSION_TIMEOUT_MS} "
            f"-c statement_timeout={_SESSION_TIMEOUT_MS}"
        },
    )
    factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)
    try:
        yield engine, factory, signal_store[1]
    finally:
        engine.dispose()


class _TrackedSessions:
    """Session factory that records backend pids and can hold the first commit."""

    def __init__(self, factory, *, hold_commit: bool = False):
        self._factory = factory
        self._hold_commit = hold_commit
        self.pids: list[int] = []
        self.holding = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> Session:
        db = self._factory()
        self.pids.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
        if self._hold_commit:
            commit = db.commit

            def _held_commit() -> None:
                # The caller's UPDATE already ran, so its row lock is held until here.
                self.holding.set()
                if not self.release.wait(_SESSION_TIMEOUT_MS / 1000):
                    raise AssertionError("row-lock holder was never released")
                commit()

            db.commit = _held_commit
        return db


class _Concurrent:
    def __init__(self, target):
        self._outcome: dict[str, object] = {}
        self._thread = threading.Thread(target=self._run, args=(target,), daemon=True)
        self._thread.start()

    def _run(self, target) -> None:
        try:
            self._outcome["value"] = target()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            self._outcome["error"] = exc

    def result(self):
        self._thread.join(_LOCK_WAIT_SECONDS)
        assert not self._thread.is_alive(), "concurrent session did not finish in time"
        if "error" in self._outcome:
            raise self._outcome["error"]
        return self._outcome["value"]

    def join(self) -> None:
        self._thread.join(_LOCK_WAIT_SECONDS)


def _await_row_lock_wait(engine, waiter: _TrackedSessions, holder_pid: int):
    """Poll from a third connection until the waiter is blocked on the holder."""
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    observed = None
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        while time.monotonic() < deadline:
            if waiter.pids:
                observed = (
                    conn.execute(
                        text(
                            "SELECT a.wait_event_type, pg_blocking_pids(a.pid) AS blockers, "
                            "EXISTS (SELECT 1 FROM pg_locks l "
                            "WHERE l.pid = a.pid AND NOT l.granted) AS ungranted "
                            "FROM pg_stat_activity a WHERE a.pid = :pid"
                        ),
                        {"pid": waiter.pids[0]},
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    observed is not None
                    and observed["wait_event_type"] == "Lock"
                    and holder_pid in observed["blockers"]
                    and observed["ungranted"]
                ):
                    return dict(observed)
            time.sleep(0.01)
    raise AssertionError(f"waiter never blocked on the holder's row lock: {observed}")


def _ensure_monthly_in(sessions: _TrackedSessions, hospital_id, observed_at):
    with sessions() as db:
        return tasks._ensure_monthly_sov_operation_run(
            db, SimpleNamespace(id=hospital_id), "2026-09", observed_at
        )


def test_claim_waits_on_the_rearm_row_lock_then_finds_the_run_rearmed(
    bounded_signal_store, monkeypatch, caplog
):
    engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )
    rearm_sessions = _TrackedSessions(factory, hold_commit=True)
    claim_sessions = _TrackedSessions(factory)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", claim_sessions)
    rearm = claim = None
    try:
        with caplog.at_level(logging.WARNING):
            rearm = _Concurrent(
                lambda: _ensure_monthly_in(rearm_sessions, hospital_id, observed_at)
            )
            assert rearm_sessions.holding.wait(_LOCK_WAIT_SECONDS), "re-arm never wrote"
            claim = _Concurrent(
                lambda: operation_run_signals._claim_safely(
                    seeded.id, seeded.task_id, observed_at, redelivered=True
                )
            )
            observed = _await_row_lock_wait(engine, claim_sessions, rearm_sessions.pids[0])
            rearm_sessions.release.set()
            rearmed = rearm.result()
            claimed = claim.result()
    finally:
        rearm_sessions.release.set()
        for worker in (rearm, claim):
            if worker is not None:
                worker.join()
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)

    assert observed["wait_event_type"] == "Lock"
    assert rearm_sessions.pids[0] in observed["blockers"]
    # _claim_safely swallows SQLAlchemyError (a lock timeout included) as None.
    assert "lifecycle claim unavailable" not in caplog.text
    assert claimed is None
    assert rearmed is not None
    assert rearmed.state == tasks.OperationRunState.REQUESTED
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.REQUESTED
    assert stored.task_id == rearmed.task_id != seeded.task_id
    assert stored.lease_owner is None
    assert stored.lease_expires_at is None
    assert stored.attempt_count == 1
    assert stored.version == 8


def test_rearm_waits_on_the_claim_row_lock_then_skips_the_reclaimed_run(
    bounded_signal_store, monkeypatch, caplog
):
    # 행 잠금 대기 뒤 skip을 확인하며, lease와 version 중 어느 쪽 가드인지는 구분하지 않음
    engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )
    claim_sessions = _TrackedSessions(factory, hold_commit=True)
    rearm_sessions = _TrackedSessions(factory)
    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", claim_sessions)
    claim = rearm = None
    try:
        with caplog.at_level(logging.WARNING):
            claim = _Concurrent(
                lambda: operation_run_signals._claim_safely(
                    seeded.id, seeded.task_id, observed_at, redelivered=True
                )
            )
            assert claim_sessions.holding.wait(_LOCK_WAIT_SECONDS), "claim never wrote"
            monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", factory)
            rearm = _Concurrent(
                lambda: _ensure_monthly_in(rearm_sessions, hospital_id, observed_at)
            )
            observed = _await_row_lock_wait(engine, rearm_sessions, claim_sessions.pids[0])
            claim_sessions.release.set()
            claimed = claim.result()
            rearmed = rearm.result()
    finally:
        claim_sessions.release.set()
        for worker in (claim, rearm):
            if worker is not None:
                worker.join()

    assert observed["wait_event_type"] == "Lock"
    assert claim_sessions.pids[0] in observed["blockers"]
    assert claimed == 8
    assert rearmed is None
    assert f"RUNNING run {seeded.id} was re-claimed or changed concurrently" in caplog.text
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.RUNNING
    assert stored.task_id == seeded.task_id
    assert stored.lease_owner == seeded.task_id
    assert stored.lease_expires_at > observed_at
    assert stored.attempt_count == 2
    assert stored.version == 8


def _bump_run_after_read(monkeypatch, factory, run_id, hook_name: str, values):
    """Commit ``values`` on another session right after the re-arm read ``existing``."""
    original = getattr(tasks, hook_name)
    rowcounts: list[int] = []

    def _hook(*args):
        outcome = original(*args)
        if not rowcounts:
            with factory() as other:
                rowcounts.append(
                    other.execute(
                        update(OperationRun)
                        .where(OperationRun.id == run_id)
                        .values(**values)
                    ).rowcount
                )
                other.commit()
        return outcome

    monkeypatch.setattr(tasks, hook_name, _hook)
    return rowcounts


def test_running_rearm_skips_a_run_whose_version_alone_changed(
    bounded_signal_store, monkeypatch, caplog
):
    # Lease still expired and state still RUNNING: only the version predicate fences.
    _engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )
    bumped = _bump_run_after_read(
        monkeypatch,
        factory,
        seeded.id,
        "_operation_lease_expired",
        {"version": OperationRun.version + 1},
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        rearmed = _run_with_timeout(
            lambda: _ensure_in_new_session(hospital_id, observed_at)
        )

    assert bumped == [1]
    assert rearmed is None
    assert (
        f"monthly RUN_SOV re-arm skipped: RUNNING run {seeded.id} was re-claimed or changed "
        "concurrently"
    ) in caplog.text
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.RUNNING
    assert stored.lease_expires_at == seeded.lease_expires_at
    assert stored.lease_expires_at <= observed_at
    assert stored.task_id == seeded.task_id
    assert stored.lease_owner == "chunk-worker"
    assert stored.version == 8


def test_running_rearm_skips_a_run_whose_lease_alone_was_renewed(
    bounded_signal_store, monkeypatch, caplog
):
    # Synthetic lease renewal without a version bump (no current writer does this): state
    # and version still match, so only the lease predicate fences the re-arm.
    _engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    renewed_until = observed_at + timedelta(minutes=10)
    seeded = _seed_running_monthly_operation(
        hospital_id, "2026-09", observed_at - timedelta(minutes=1)
    )
    # The retry-window check runs after the expired lease was read on ``existing``.
    renewed = _bump_run_after_read(
        monkeypatch,
        factory,
        seeded.id,
        "_monthly_sov_retry_window",
        {"lease_expires_at": renewed_until},
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        rearmed = _run_with_timeout(
            lambda: _ensure_in_new_session(hospital_id, observed_at)
        )

    assert renewed == [1]
    assert rearmed is None
    assert f"RUNNING run {seeded.id} was re-claimed or changed concurrently" in caplog.text
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.RUNNING
    assert stored.lease_expires_at == renewed_until
    assert stored.task_id == seeded.task_id
    assert stored.lease_owner == "chunk-worker"
    assert stored.version == 7


_PARTIAL_RETRY = (
    tasks.OperationRunState.PARTIAL,
    "MONTHLY_SOV_MEASUREMENT_PARTIAL",
    datetime(2026, 9, 7, 14, 59, 59, tzinfo=UTC),
)
_FAILED_RETRY = (
    tasks.OperationRunState.FAILED,
    "MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
    datetime(2026, 8, 28, tzinfo=UTC),
)
_VERSION_BUMP = {"version": OperationRun.version + 1}
# No real writer changes a monthly RUN_SOV row's state without bumping its version: the
# lifecycle signals (_claim_safely, _finish_from_signal, _requeue_from_signal), the task-body
# CAS finishes, _mark_weekly_sov_operation_queued, _mark_monthly_measurement_incomplete,
# autonomous_recovery's redispatch and unsafe-dispatch failure, operation_run_transitions,
# and both writes in _ensure_monthly_sov_operation_run (the REQUESTED payload refresh and
# the re-arm) all bump it. This synthetic state-only change isolates the state predicate.
_STATE_CHANGE_SAME_VERSION = {"state": tasks.OperationRunState.SUCCEEDED}


@pytest.mark.parametrize(
    ("closed", "change"),
    [
        (_PARTIAL_RETRY, _VERSION_BUMP),
        (_FAILED_RETRY, _VERSION_BUMP),
        (_PARTIAL_RETRY, _STATE_CHANGE_SAME_VERSION),
        (_FAILED_RETRY, _STATE_CHANGE_SAME_VERSION),
    ],
    ids=["partial-version", "failed-version", "partial-state", "failed-state"],
)
def test_closed_monthly_rearm_skips_a_concurrently_changed_run(
    bounded_signal_store, monkeypatch, caplog, closed, change
):
    _engine, factory, hospital_id = bounded_signal_store
    state, code, observed_at = closed
    seeded = seed_closed_monthly_sov_run(
        hospital_id, "2026-08", state, safe_error_code=code
    )
    # The retry-window check is the first call after ``existing`` is read, for both
    # the PARTIAL and FAILED branches.
    changed = _bump_run_after_read(
        monkeypatch, factory, seeded.id, "_monthly_sov_retry_window", change
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        rearmed = _run_with_timeout(
            lambda: _ensure_closed_monthly_in_new_session(hospital_id, observed_at)
        )

    assert changed == [1]
    assert rearmed is None
    assert f"{state.value} run {seeded.id} was re-claimed or changed concurrently" in (
        caplog.text
    )
    stored = stored_operation_run(seeded.id)
    if change is _VERSION_BUMP:
        assert (stored.state, stored.version) == (state, 4)
    else:
        assert (stored.state, stored.version) == (tasks.OperationRunState.SUCCEEDED, 3)
    assert stored.task_id == seeded.task_id
    assert stored.completed_at == seeded.completed_at
    assert stored.lease_owner == "worker"
    assert stored.failure_count == 1
    assert stored.safe_error_code == code


def _ensure_closed_monthly_in_new_session(hospital_id: uuid.UUID, observed_at: datetime):
    with operation_run_signals.SyncSessionLocal() as db:
        return tasks._ensure_monthly_sov_operation_run(
            db, SimpleNamespace(id=hospital_id), "2026-08", observed_at
        )


# ── REQUESTED payload refresh: conditional UPDATE on the row that was read ──────

_STALE_PAYLOAD = {"stale": True}
_STALE_SUMMARY = {"measurement_month": "stale"}


def _seed_requested_monthly_operation(hospital_id: uuid.UUID) -> OperationRun:
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="RUN_SOV",
        state=tasks.OperationRunState.REQUESTED,
        idempotency_key=f"monthly-sov:{hospital_id}:2026-09",
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=_STALE_PAYLOAD,
        result_summary=_STALE_SUMMARY,
        requested_at=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
        version=5,
    )
    with operation_run_signals.SyncSessionLocal() as db:
        db.add(run)
        db.commit()
    return run


def _change_run_after_first_read(monkeypatch, factory, run_id, values):
    """Commit ``values`` on another session right after the caller's first statement.

    The REQUESTED branch calls nothing between its SELECT and its UPDATE, so the hook
    sits on the session: the first ``execute`` is that SELECT.
    """
    rowcounts: list[int] = []

    def _session() -> Session:
        db = factory()
        execute = db.execute

        def _execute(*args, **kwargs):
            outcome = execute(*args, **kwargs)
            if not rowcounts:
                with factory() as other:
                    rowcounts.append(
                        other.execute(
                            update(OperationRun)
                            .where(OperationRun.id == run_id)
                            .values(**values)
                        ).rowcount
                    )
                    other.commit()
            return outcome

        db.execute = _execute
        return db

    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", _session)
    return rowcounts


@pytest.mark.parametrize(
    ("change", "expected_state", "expected_version"),
    [
        ({"version": OperationRun.version + 1}, tasks.OperationRunState.REQUESTED, 6),
        # Synthetic state-only change, as for the closed re-arm: isolates the state predicate.
        ({"state": tasks.OperationRunState.QUEUED}, tasks.OperationRunState.QUEUED, 5),
    ],
    ids=["version", "state"],
)
def test_requested_refresh_skips_a_concurrently_changed_run(
    bounded_signal_store, monkeypatch, caplog, change, expected_state, expected_version
):
    _engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_requested_monthly_operation(hospital_id)
    changed = _change_run_after_first_read(monkeypatch, factory, seeded.id, change)

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        refreshed = _run_with_timeout(
            lambda: _ensure_in_new_session(hospital_id, observed_at)
        )

    assert changed == [1]
    assert refreshed is None
    assert (
        f"monthly RUN_SOV payload refresh skipped: REQUESTED run {seeded.id} was "
        "re-claimed or changed concurrently"
    ) in caplog.text
    stored = stored_operation_run(seeded.id)
    assert (stored.state, stored.version) == (expected_state, expected_version)
    assert stored.request_payload == _STALE_PAYLOAD
    assert stored.result_summary == _STALE_SUMMARY
    assert stored.task_id == seeded.task_id


def test_requested_refresh_rewrites_the_payload_and_bumps_the_version(
    bounded_signal_store, caplog
):
    _engine, _factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_requested_monthly_operation(hospital_id)
    hospital_key = str(hospital_id)
    expected_payload = {
        "source_type": "hospital",
        "source_id": hospital_key,
        "_dispatch": {
            "target_type": "hospital",
            "target_id": hospital_key,
            "queue": "sov",
            "task_args": [hospital_key, "monthly", 2026, 9],
        },
    }
    expected_summary = {"measurement_month": "2026-09", "measurement_mode": "monthly"}

    with operation_run_signals.SyncSessionLocal() as db:
        commits = count_commits(db)
        with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
            run = tasks._ensure_monthly_sov_operation_run(
                db, SimpleNamespace(id=hospital_id), "2026-09", observed_at
            )

    assert run is not None
    assert run.id == seeded.id
    assert run.state == tasks.OperationRunState.REQUESTED
    assert run.task_id == seeded.task_id
    assert run.request_payload == expected_payload
    assert run.result_summary == expected_summary
    assert run.version == 6
    assert len(commits) == 1
    assert "was re-claimed or changed concurrently" not in caplog.text
    stored = stored_operation_run(seeded.id)
    assert stored.state == tasks.OperationRunState.REQUESTED
    assert stored.task_id == seeded.task_id
    assert stored.request_payload == expected_payload
    assert stored.result_summary == expected_summary
    assert stored.requested_at == seeded.requested_at
    assert stored.attempt_count == 0
    assert stored.version == 6


def _record_monthly_dispatch(monkeypatch) -> tuple[list[dict], list[uuid.UUID]]:
    """Record broker publishes and the REQUESTED->QUEUED mark both monthly callers use."""
    published: list[dict] = []
    queued: list[uuid.UUID] = []
    mark_queued = tasks._mark_weekly_sov_operation_queued

    def _mark(db, run_id, observed_at):
        queued.append(run_id)
        return mark_queued(db, run_id, observed_at)

    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda *_args, **kwargs: published.append(kwargs),
    )
    monkeypatch.setattr(tasks, "_mark_weekly_sov_operation_queued", _mark)
    return published, queued


def _assert_requested_run_left_undispatched(seeded: OperationRun) -> None:
    stored = stored_operation_run(seeded.id)
    assert (stored.state, stored.version) == (tasks.OperationRunState.REQUESTED, 6)
    assert stored.queued_at is None
    assert stored.task_id == seeded.task_id
    assert stored.request_payload == _STALE_PAYLOAD


def test_monthly_catchup_does_not_dispatch_when_the_requested_refresh_loses(
    bounded_signal_store, monkeypatch
):
    _engine, factory, hospital_id = bounded_signal_store
    observed_at = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    seeded = _seed_requested_monthly_operation(hospital_id)
    changed = _change_run_after_first_read(
        monkeypatch, factory, seeded.id, {"version": OperationRun.version + 1}
    )
    published, queued = _record_monthly_dispatch(monkeypatch)

    def _catchup():
        with operation_run_signals.SyncSessionLocal() as db:
            return tasks._dispatch_monthly_sov_catchup(
                db, SimpleNamespace(id=hospital_id), "2026-09", observed_at
            )

    dispatched = _run_with_timeout(_catchup)

    assert changed == [1]
    assert dispatched is None
    assert published == []
    assert queued == []
    _assert_requested_run_left_undispatched(seeded)


def test_monthly_beat_does_not_dispatch_when_the_requested_refresh_loses(
    bounded_signal_store, monkeypatch
):
    _engine, factory, hospital_id = bounded_signal_store
    seeded = _seed_requested_monthly_operation(hospital_id)
    changed = _change_run_after_first_read(
        monkeypatch, factory, seeded.id, {"version": OperationRun.version + 1}
    )
    published, queued = _record_monthly_dispatch(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", operation_run_signals.SyncSessionLocal)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(2026, 9, 26, 15, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(
        tasks,
        "register_convertible_tracking_sets",
        lambda *_args, **_kwargs: {"registered": [], "blocked": []},
    )
    monkeypatch.setattr(
        tasks,
        "iter_monthly_sov_cohort",
        lambda *_args, **_kwargs: [SimpleNamespace(id=hospital_id)],
    )

    _run_with_timeout(tasks.run_monthly_sov_measurement.run)

    assert changed == [1]
    assert published == []
    assert queued == []
    _assert_requested_run_left_undispatched(seeded)


def test_sov_task_hard_time_limit_stays_below_the_claim_lease():
    # The RUNNING re-arm assumes no live worker still holds an expired claim: Celery
    # kills run_sov_for_hospital before the lease _claim_safely grants can run out.
    task = tasks.run_sov_for_hospital
    lease_seconds = operation_run_signals._LEASE_SECONDS

    assert task.time_limit is not None
    assert task.time_limit < lease_seconds
    # The worker falls back to the global hard limit if the decorator value is dropped.
    assert celery_app.conf.task_time_limit < lease_seconds


@pytest.mark.parametrize("enrolled,expected", [(True, "enrolled_in_monthly_cohort"), (False, None)])
def test_cohort_gap_incident_closes_only_once_hospital_is_enrolled(enrolled, expected):
    from app.workers.incident_backlog import RESOLVERS

    hospital = SimpleNamespace(monthly_sov_cohort=enrolled)
    db = SimpleNamespace(get=lambda _model, _id: hospital)
    incident = SimpleNamespace(hospital_id=uuid.uuid4())

    resolver = RESOLVERS["MONTHLY_SOV_COHORT_GAP"]

    assert resolver(db, incident, datetime.now(UTC)) == expected
