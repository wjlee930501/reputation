import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import sov_engine, sov_tracking_set
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.sov_tracking_set import (
    CONVERSION_HOSPITAL_NAME_TOKENS,
    MEASUREMENT_WINDOW_MONTH_END,
    MEASUREMENT_WINDOW_MONTH_START,
    monthly_sov_guard_units,
    register_convertible_tracking_sets,
    register_tracking_set,
    tracking_set_fingerprint,
    tracking_set_is_valid,
    tracking_set_members,
)
from app.workers import tasks


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


def test_one_shot_matches_only_locked_names_and_reports_blockers(monkeypatch):
    hospitals = [
        SimpleNamespace(id=uuid.uuid4(), name=f"{token} 의원")
        for token in CONVERSION_HOSPITAL_NAME_TOKENS
        if token != "노원탑365"
    ]
    hospitals.append(SimpleNamespace(id=uuid.uuid4(), name="마포성모탑 별관"))
    by_token = {
        token: [hospital for hospital in hospitals if token in hospital.name]
        for token in CONVERSION_HOSPITAL_NAME_TOKENS
    }
    has_record = {hospital.id for hospital in hospitals}
    no_record_hospital = by_token["장편한외과"][0]
    has_record.remove(no_record_hospital.id)
    invalid_hospital = by_token["행복드림"][0]

    monkeypatch.setattr(
        sov_tracking_set,
        "_active_hospital_name_matches",
        lambda _db, token: by_token[token],
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

    result = register_convertible_tracking_sets(object(), n=15)

    assert result["target_count"] == 7
    assert {item["name"] for item in result["registered"]} == {
        "강심장내과 의원",
        "서울W내과의원 위례점 의원",
        "연세속시원 의원",
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
        {"name": "마포성모탑", "reason": "ambiguous"},
        {"name": "노원탑365", "reason": "not found"},
    ]


def test_conversion_name_tokens_are_the_locked_seven():
    assert CONVERSION_HOSPITAL_NAME_TOKENS == (
        "강심장내과",
        "행복드림",
        "장편한외과",
        "마포성모탑",
        "노원탑365",
        "서울W내과의원 위례점",
        "연세속시원",
    )


def test_non_positive_cohort_limit_is_empty_and_positive_limit_is_applied(monkeypatch):
    first = SimpleNamespace(id=uuid.uuid4(), name="강심장내과 의원")
    second = SimpleNamespace(id=uuid.uuid4(), name="행복드림 의원")
    invalid = SimpleNamespace(id=uuid.uuid4(), name="장편한외과 의원")
    outsider = SimpleNamespace(id=uuid.uuid4(), name="다른병원")
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

    assert sov_tracking_set.iter_monthly_sov_cohort(object(), limit=0) == []
    assert sov_tracking_set.iter_monthly_sov_cohort(object(), limit=-1) == []
    assert sov_tracking_set.iter_monthly_sov_cohort(object(), limit=None) == []
    assert sov_tracking_set.iter_monthly_sov_cohort(object(), limit=1) == [first]
    assert sov_tracking_set.iter_monthly_sov_cohort(object(), limit=7) == [first, second]


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


def test_monthly_specs_ignore_priority_and_caps_but_keep_legacy_helpers(monkeypatch):
    targets = [_target(f"지역 병원 질문 {index}") for index in range(15)]
    hospital = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

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
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

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


def test_converted_cohort_is_not_dispatched_by_weekly_beat(monkeypatch):
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
    hospital = SimpleNamespace(id=uuid.uuid4(), name="전환 의원")
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
    hospital = SimpleNamespace(id=uuid.uuid4(), name="측정 대기 의원")

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
    monkeypatch.setattr(
        tasks,
        "_build_monthly_report_for_hospital",
        lambda *_args, **_kwargs: pytest.fail("report built before monthly measurement success"),
    )
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: tasks.arrow.get(
            2026, 8, 31, 12, 0, tzinfo="Asia/Seoul"
        ),
    )

    assert tasks.run_monthly_reports.run() == {
        "status": "SUCCEEDED",
        "total_count": 0,
        "success_count": 0,
        "failure_count": 0,
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


def test_failed_monthly_operation_is_rearmed_for_failed_cell_retry():
    old_task_id = str(uuid.uuid4())
    existing = SimpleNamespace(
        state=tasks.OperationRunState.FAILED,
        task_id=old_task_id,
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
        success_count=0,
        failure_count=1,
        skipped_count=0,
        safe_error_code="MONTHLY_SOV_MEASUREMENT_PARTIAL",
        safe_error_message="failed",
        version=3,
    )

    class _Result:
        def scalar_one_or_none(self):
            return existing

    class _DB:
        commits = 0

        def execute(self, _stmt):
            return _Result()

        def commit(self):
            self.commits += 1

    db = _DB()
    hospital = SimpleNamespace(id=uuid.uuid4())

    run = tasks._ensure_monthly_sov_operation_run(
        db, hospital, "2026-08", datetime.now(UTC)
    )

    assert run is existing
    assert run.state == tasks.OperationRunState.REQUESTED
    assert run.task_id != old_task_id
    assert run.completed_at is None
    assert run.failure_count == 0
    assert run.version == 4
    assert db.commits == 1
