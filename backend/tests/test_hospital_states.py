"""3상태는 한 곳에서만 계산한다 — 목록·헤더·현황이 다른 답을 내면 사람이 화면을 못 믿는다."""

from types import SimpleNamespace

from app.models.hospital import HospitalStatus
from app.services.hospital_states import (
    ContentState,
    DomainState,
    PublicServiceState,
    content_state,
    domain_state,
    public_service_state,
)


def _hospital(**overrides):
    base = dict(
        status=HospitalStatus.ACTIVE,
        site_live=True,
        site_built=True,
        profile_complete=True,
        schedule_set=True,
        aeo_domain=None,
        domain_cert_job_state=None,
        domain_cert_dns_verified_at=None,
        domain_last_check_ok=None,
        domain_last_checked_at=None,
        domain_last_check_reason=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_public_service_mirrors_the_serving_gate():
    assert public_service_state(_hospital()) == PublicServiceState(kind="live", remaining=())
    assert public_service_state(_hospital(status=HospitalStatus.PAUSED)).kind == "paused"
    not_live = public_service_state(
        _hospital(
            status=HospitalStatus.BUILDING,
            site_live=False,
            site_built=False,
            profile_complete=False,
        )
    )
    assert not_live.kind == "not_live"
    assert not_live.remaining == ("profile_complete", "site_built")


def test_content_state_needs_schedule_and_a_current_essence():
    ready = content_state(
        _hospital(),
        essence_current=True,
        unprocessed_sources=0,
        required_sources=1,
        escalated_draft=False,
    )
    assert ready == ContentState(kind="auto", remaining=())
    waiting = content_state(
        _hospital(schedule_set=False),
        essence_current=False,
        unprocessed_sources=2,
        required_sources=3,
        escalated_draft=False,
    )
    assert waiting.kind == "preparing"
    assert waiting.remaining == ("schedule", "sources:2", "essence_review")
    assert (
        content_state(
            _hospital(),
            essence_current=False,
            unprocessed_sources=0,
            required_sources=1,
            escalated_draft=True,
        ).kind
        == "exception"
    )


def test_no_sources_is_human_work_not_an_automatic_review():
    """자료가 없으면 자동 검수는 기다리기만 한다 — 사람이 채워야 할 일을 시스템 몫으로 적지 않는다."""
    state = content_state(
        _hospital(),
        essence_current=False,
        unprocessed_sources=0,
        required_sources=0,
        escalated_draft=False,
    )
    assert state == ContentState(kind="preparing", remaining=("sources_required",))


def test_source_drift_and_legacy_review_draft_do_not_block_an_existing_base():
    state = content_state(
        _hospital(),
        essence_current=True,
        unprocessed_sources=2,
        required_sources=3,
        escalated_draft=True,
    )

    assert state == ContentState(kind="auto", remaining=())


def test_a_hospital_that_is_not_serving_is_never_auto_publishing():
    """야간 생성은 공개 서비스 중인 병원에만 돈다 — 멈춘 병원을 '자동 발행 중'이라 하지 않는다."""
    paused = content_state(
        _hospital(status=HospitalStatus.PAUSED),
        essence_current=True,
        unprocessed_sources=0,
        required_sources=1,
        escalated_draft=False,
    )
    assert paused == ContentState(kind="preparing", remaining=("service_paused",))
    not_live = content_state(
        _hospital(status=HospitalStatus.BUILDING, site_live=False),
        essence_current=True,
        unprocessed_sources=0,
        required_sources=1,
        escalated_draft=False,
    )
    assert not_live == ContentState(kind="preparing", remaining=("public_service",))


def test_domain_state_only_when_a_custom_domain_exists():
    assert domain_state(_hospital()).kind == "unused"
    assert (
        domain_state(_hospital(aeo_domain="clinic.example.com", domain_last_check_ok=True)).kind
        == "connected"
    )
    checking = domain_state(
        _hospital(aeo_domain="clinic.example.com", domain_cert_job_state="ISSUING")
    )
    assert checking.kind == "checking"
    failed = domain_state(
        _hospital(aeo_domain="clinic.example.com", domain_cert_job_state="FAILED")
    )
    assert failed.kind == "problem" and failed.reason


def test_issuing_certificate_outranks_a_passing_live_check():
    """admin `readHospitalDomainStatus`와 같은 순서 — 관측이 정상이어도 발급 중을 가리지 않는다."""
    state = domain_state(
        _hospital(
            aeo_domain="clinic.example.com",
            domain_cert_job_state="ISSUING",
            domain_last_check_ok=True,
        )
    )
    assert state.kind == "checking"


def test_failed_live_check_reports_the_stored_reason():
    state = domain_state(
        _hospital(
            aeo_domain="clinic.example.com",
            domain_last_check_ok=False,
            domain_last_check_reason="A 레코드 불일치",
        )
    )
    assert state == DomainState(
        kind="problem",
        reason="A 레코드 불일치",
        last_checked_at=None,
        last_check_ok=False,
    )


def test_paused_hospital_keeps_its_domain_fact():
    """일시정지는 공개 서비스 카드가 말한다 — 도메인 사실까지 지우면 재개 전에 볼 화면이 없다."""
    state = domain_state(
        _hospital(
            status=HospitalStatus.PAUSED,
            aeo_domain="clinic.example.com",
            domain_cert_job_state="FAILED",
        )
    )
    assert state.kind == "problem"
