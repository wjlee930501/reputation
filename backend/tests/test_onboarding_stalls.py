import uuid
from datetime import UTC, date, datetime, timedelta

from app.models.hospital import HospitalStatus
from app.services.onboarding_stalls import (
    OnboardingFacts,
    OnboardingStall,
    detect_onboarding_stalls,
)

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _facts(**overrides) -> OnboardingFacts:
    values = dict(
        hospital_id=uuid.uuid4(),
        hospital_name="정체테스트의원",
        status=HospitalStatus.ACTIVE,
        profile_complete=True,
        schedule_set=True,
        handoff_accepted_at=NOW - timedelta(days=30),
        essence_current_since=NOW - timedelta(days=20),
        active_since=NOW - timedelta(days=20),
        first_schedule_start=date(2026, 10, 1),
        published_count=3,
        required_source_count=2,
        channel_source_count=0,
        channel_sources_all_error_since=None,
    )
    values.update(overrides)
    return OnboardingFacts(**values)


def _detect(facts, *, source_alerts=True):
    return detect_onboarding_stalls(facts, NOW, source_fetch_alerts=source_alerts)


def test_healthy_hospital_has_no_stall() -> None:
    assert _detect(_facts()) == ()


def test_profile_incomplete_three_days_after_handoff() -> None:
    assert _detect(
        _facts(status=HospitalStatus.ONBOARDING, profile_complete=False,
               handoff_accepted_at=NOW - timedelta(days=3), schedule_set=False, active_since=None)
    ) == (OnboardingStall.PROFILE_INCOMPLETE,)
    assert _detect(
        _facts(status=HospitalStatus.ONBOARDING, profile_complete=False,
               handoff_accepted_at=NOW - timedelta(days=2, hours=23), schedule_set=False,
               active_since=None)
    ) == ()


def test_schedule_missing_only_for_public_hospitals_after_a_day() -> None:
    stalled = _facts(schedule_set=False, essence_current_since=NOW - timedelta(hours=24),
                     published_count=0)
    assert _detect(stalled) == (OnboardingStall.SCHEDULE_MISSING,)
    # 공개 전이면 원인은 프로필이다 — 일정 사고를 따로 열지 않는다.
    assert OnboardingStall.SCHEDULE_MISSING not in _detect(
        _facts(status=HospitalStatus.BUILDING, schedule_set=False,
               essence_current_since=NOW - timedelta(days=5))
    )
    assert _detect(_facts(schedule_set=False, essence_current_since=NOW - timedelta(hours=23),
                          published_count=0)) == ()


def test_no_publication_counts_from_the_later_of_activation_and_schedule_start() -> None:
    assert _detect(_facts(published_count=0, active_since=NOW - timedelta(days=7),
                          first_schedule_start=date(2026, 10, 1))) == (
        OnboardingStall.NO_PUBLICATION,
    )
    # 다음 달 1일에 시작하는 일정은 아직 기다리는 중이다.
    assert _detect(_facts(published_count=0, active_since=NOW - timedelta(days=30),
                          first_schedule_start=date(2026, 11, 1))) == ()
    # 일정 시작(KST 10/15 00:00) 뒤 7일이 안 됐다.
    assert _detect(_facts(published_count=0, active_since=NOW - timedelta(days=30),
                          first_schedule_start=date(2026, 10, 15))) == ()


def test_unreachable_sources_only_behind_the_flag() -> None:
    facts = _facts(required_source_count=0, channel_source_count=2,
                   channel_sources_all_error_since=NOW - timedelta(hours=24))
    assert _detect(facts, source_alerts=True) == (OnboardingStall.SOURCES_UNREACHABLE,)
    assert _detect(facts, source_alerts=False) == ()
    assert _detect(_facts(required_source_count=0, channel_source_count=2,
                          channel_sources_all_error_since=None)) == ()


def test_paused_hospital_never_stalls() -> None:
    assert _detect(_facts(status=HospitalStatus.PAUSED, profile_complete=False,
                          schedule_set=False, published_count=0)) == ()


async def test_apply_opens_new_stalls_once_and_quietly_closes_solved_ones() -> None:
    from app.services.onboarding_stalls import apply_onboarding_stalls

    stalled = _facts(published_count=0, active_since=NOW - timedelta(days=8))
    healthy = _facts()
    opened: list[dict] = []
    recovered: list[dict] = []
    states = {f"{stalled.hospital_id}:{OnboardingStall.NO_PUBLICATION.value}": None}

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    async def fake_recover(**kwargs):
        recovered.append(kwargs)
        return kwargs["object_id"].startswith(str(healthy.hospital_id)) and kwargs[
            "object_id"
        ].endswith(OnboardingStall.PROFILE_INCOMPLETE.value)

    async def fake_state(object_id: str):
        return states.get(object_id)

    summary = await apply_onboarding_stalls(
        [stalled, healthy], NOW, source_fetch_alerts=False,
        open_incident=fake_open, recover_incident=fake_recover, incident_state=fake_state,
    )

    assert [o["incident_type"] for o in opened] == ["ONBOARDING_NO_PUBLICATION"]
    assert opened[0]["safe_error_code"] == "ONBOARDING_NO_PUBLICATION"
    assert opened[0]["admin_path"] == f"/hospitals/{stalled.hospital_id}/content"
    assert opened[0]["notify"] is True
    assert all(r["notify"] is False for r in recovered)
    assert summary == {"opened": 1, "recovered": 1}


async def test_acknowledged_or_open_stall_is_not_reopened() -> None:
    from app.services.onboarding_stalls import apply_onboarding_stalls

    stalled = _facts(published_count=0, active_since=NOW - timedelta(days=8))
    opened: list = []

    async def fake_open(**kwargs):
        opened.append(kwargs)

    async def fake_recover(**_kwargs):
        return False

    for state in ("OPEN", "ACKNOWLEDGED", "RETRYING"):
        async def fake_state(_object_id, _state=state):
            return _state

        await apply_onboarding_stalls(
            [stalled], NOW, source_fetch_alerts=False,
            open_incident=fake_open, recover_incident=fake_recover, incident_state=fake_state,
        )
    assert opened == []


def test_milestone_task_runs_the_stall_scan_and_survives_its_failure(monkeypatch) -> None:
    from app.workers import milestone_event_tasks as module

    async def fake_project(_now):
        return module.ProjectionResult(0, 0, False)

    async def boom(_now):
        raise RuntimeError("scan down")

    monkeypatch.setattr(module, "_project_once", fake_project)
    monkeypatch.setattr(module, "_scan_onboarding_stalls_once", boom)

    result = module.project_milestone_events.run()

    assert result["onboarding_count"] == 0
    assert result["onboarding_stalls_opened"] == 0
