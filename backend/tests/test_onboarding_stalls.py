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
