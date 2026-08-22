"""A-1: 살아 있는 커스텀 도메인이 '확인 대기'로 남지 않게 하는 상태 갱신."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models.hospital import DomainCertJobState
from app.services.domain_live_status import (
    LiveDomainCheck,
    apply_live_domain_check,
    clear_live_domain_check,
)

CHECKED_AT = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)


def _hospital(**overrides):
    base = {
        "aeo_domain": "ai.no1top365.co.kr",
        "domain_cert_job_state": None,
        "domain_cert_job_started_at": None,
        "domain_cert_job_token": None,
        "domain_cert_job_domain": None,
        "domain_cert_dns_verified_at": None,
        "domain_last_checked_at": None,
        "domain_last_check_ok": None,
        "domain_last_check_reason": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _check(**overrides):
    base = {
        "domain": "ai.no1top365.co.kr",
        "healthy": True,
        "reason": "tenant_marker_ok",
        "checked_at": CHECKED_AT,
        "proves_certificate": True,
    }
    base.update(overrides)
    return LiveDomainCheck(**base)


def test_live_https_response_clears_the_dns_unverified_state():
    """CEO 화면 리포트의 상황 — HTTPS 200 + CNAME 정상인데 '저장됨 · DNS 미확인'."""
    hospital = _hospital()

    assert apply_live_domain_check(hospital, _check()) is True

    assert hospital.domain_last_checked_at == CHECKED_AT
    assert hospital.domain_last_check_ok is True
    assert hospital.domain_last_check_reason == "tenant_marker_ok"
    assert hospital.domain_cert_dns_verified_at == CHECKED_AT
    assert hospital.domain_cert_job_state == DomainCertJobState.DONE.value
    assert hospital.domain_cert_job_domain == "ai.no1top365.co.kr"


def test_existing_dns_verified_timestamp_is_not_overwritten():
    earlier = CHECKED_AT - timedelta(days=3)
    hospital = _hospital(domain_cert_dns_verified_at=earlier)

    apply_live_domain_check(hospital, _check())

    assert hospital.domain_cert_dns_verified_at == earlier


def test_failed_check_records_the_attempt_without_undoing_verified_state():
    hospital = _hospital(
        domain_cert_dns_verified_at=CHECKED_AT,
        domain_cert_job_state=DomainCertJobState.DONE.value,
    )

    assert apply_live_domain_check(hospital, _check(healthy=False, reason="timeout")) is True

    assert hospital.domain_last_check_ok is False
    assert hospital.domain_last_check_reason == "timeout"
    assert hospital.domain_cert_dns_verified_at == CHECKED_AT
    assert hospital.domain_cert_job_state == DomainCertJobState.DONE.value


def test_issuing_certificate_lease_is_left_to_the_worker():
    """진행 중인 발급 리스를 DONE으로 덮으면 워커의 종료 갱신이 무음 실패한다."""
    hospital = _hospital(
        domain_cert_job_state=DomainCertJobState.ISSUING.value,
        domain_cert_job_token="lease-token",
        domain_cert_job_domain="ai.no1top365.co.kr",
        domain_cert_job_started_at=CHECKED_AT,
    )

    apply_live_domain_check(hospital, _check())

    assert hospital.domain_cert_job_state == DomainCertJobState.ISSUING.value
    assert hospital.domain_cert_job_token == "lease-token"
    assert hospital.domain_last_check_ok is True


def test_dns_only_check_does_not_declare_the_domain_healthy_or_the_certificate_ready():
    """CNAME 조회는 TLS도 라우팅도 증명하지 않는다 — '서빙 중'이라고 말할 수 없다."""
    hospital = _hospital()

    apply_live_domain_check(hospital, _check(reason="dns_ok", proves_certificate=False))

    assert hospital.domain_cert_dns_verified_at == CHECKED_AT
    assert hospital.domain_cert_job_state is None
    # 판단 보류 — True로 접으면 발급 중/실패한 인증서를 '운영 중'으로 덮는다.
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_checked_at == CHECKED_AT
    assert hospital.domain_last_check_reason == "dns_ok"


def test_dns_only_check_never_overrides_a_certificate_that_is_issuing_or_failed():
    for state in (DomainCertJobState.ISSUING.value, DomainCertJobState.FAILED.value):
        hospital = _hospital(domain_cert_job_state=state, domain_cert_job_started_at=CHECKED_AT)

        apply_live_domain_check(hospital, _check(reason="dns_ok", proves_certificate=False))

        assert hospital.domain_cert_job_state == state
        assert hospital.domain_last_check_ok is None


def test_a_dns_only_check_retracts_a_previous_serving_confirmation():
    """마지막 관측이 DNS 조회뿐이면 그 시각의 사실도 DNS까지다."""
    hospital = _hospital(domain_last_check_ok=True, domain_last_check_reason="tenant_marker_ok")

    apply_live_domain_check(hospital, _check(reason="dns_ok", proves_certificate=False))

    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_check_reason == "dns_ok"


def test_clearing_forgets_the_observation_so_a_new_domain_cannot_inherit_it():
    hospital = _hospital(
        domain_last_checked_at=CHECKED_AT,
        domain_last_check_ok=True,
        domain_last_check_reason="tenant_marker_ok",
    )

    clear_live_domain_check(hospital)

    assert hospital.domain_last_checked_at is None
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_check_reason is None


def test_result_for_a_different_domain_is_discarded():
    hospital = _hospital(aeo_domain="jangclinic.kr")

    assert apply_live_domain_check(hospital, _check()) is False

    assert hospital.domain_last_checked_at is None
    assert hospital.domain_cert_dns_verified_at is None


def test_check_is_discarded_when_no_custom_domain_is_connected():
    hospital = _hospital(aeo_domain=None)

    assert apply_live_domain_check(hospital, _check()) is False
    assert hospital.domain_last_checked_at is None


def test_trailing_dot_and_case_still_match_the_connected_domain():
    hospital = _hospital(aeo_domain="AI.No1Top365.co.kr")

    assert apply_live_domain_check(hospital, _check(domain="ai.no1top365.co.kr.")) is True
    assert hospital.domain_last_check_ok is True


def test_reason_is_truncated_to_the_column_width():
    hospital = _hospital()

    apply_live_domain_check(hospital, _check(healthy=False, reason="x" * 300))

    assert len(hospital.domain_last_check_reason) == 100
