import pytest

from scripts.check_public_dns import check_domain_addresses


def test_public_dns_rejects_loopback_addresses():
    result = check_domain_addresses("reputation.co.kr", ("127.0.0.1",))

    assert not result.ok
    assert "127.0.0.1" in result.message


def test_public_dns_accepts_global_addresses():
    result = check_domain_addresses("reputation.co.kr", ("8.8.8.8", "2001:4860:4860::8888"))

    assert result.ok
    assert result.message == "reputation.co.kr resolves to public address(es): 2001:4860:4860::8888, 8.8.8.8"


def test_public_dns_rejects_empty_resolution():
    result = check_domain_addresses("reputation.co.kr", ())

    assert not result.ok
    assert "did not resolve" in result.message


def test_public_dns_rejects_invalid_ip_literals():
    with pytest.raises(ValueError, match="not-an-ip"):
        check_domain_addresses("reputation.co.kr", ("not-an-ip",))
