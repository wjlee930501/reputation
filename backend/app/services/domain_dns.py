import importlib.util
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import assert_never

import anyio

from app.core.config import settings
from app.models.hospital import DomainDnsStrategy, Hospital

CnameResolver = Callable[[str], Awaitable[str | None]]
AddressResolver = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class DomainDnsCheck:
    cname_value: str | None
    address_values: list[str]
    expected_cname: str
    expected_addresses: list[str]
    verified: bool
    verification_method: str | None


async def resolve_cname(domain: str) -> str | None:
    return await anyio.to_thread.run_sync(_resolve_cname_blocking, domain)


def _resolve_cname_blocking(domain: str) -> str | None:
    if importlib.util.find_spec("dns") is not None:
        import dns.exception
        import dns.resolver

        try:
            answers = dns.resolver.resolve(domain, "CNAME", lifetime=5.0)
            return str(answers[0].target).rstrip(".")
        except (dns.exception.DNSException, IndexError, AttributeError, TypeError):
            return None

    try:
        resolved = socket.getfqdn(domain)
        return resolved if resolved != domain else None
    except (OSError, UnicodeError):
        return None


async def resolve_addresses(domain: str) -> list[str]:
    return await anyio.to_thread.run_sync(_resolve_addresses_blocking, domain)


def _resolve_addresses_blocking(domain: str) -> list[str]:
    if importlib.util.find_spec("dns") is not None:
        import dns.exception
        import dns.resolver

        values: list[str] = []
        for record_type in ("A", "AAAA"):
            try:
                answers = dns.resolver.resolve(domain, record_type, lifetime=5.0)
            except dns.exception.DNSException:
                continue
            values.extend(str(answer).rstrip(".") for answer in answers)
        return sorted(set(values))

    try:
        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
        return sorted({info[4][0] for info in infos})
    except (OSError, UnicodeError):
        return []


def normalize_dns_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip(".").lower()


def configured_custom_domain_ips(raw_targets: str) -> list[str]:
    values = []
    for raw in raw_targets.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        ipaddress.ip_address(candidate)
        values.append(candidate)
    return sorted(set(values))


async def check_domain_dns(
    domain: str,
    strategy: DomainDnsStrategy = DomainDnsStrategy.CNAME,
    *,
    cname_resolver: CnameResolver = resolve_cname,
    address_resolver: AddressResolver = resolve_addresses,
    expected_cname: str | None = None,
    custom_domain_ip_targets: str | None = None,
) -> DomainDnsCheck:
    cname_value: str | None = None
    address_values: list[str] = []

    async def resolve_cname_value() -> None:
        nonlocal cname_value
        cname_value = await cname_resolver(domain)

    async def resolve_address_values() -> None:
        nonlocal address_values
        address_values = await address_resolver(domain)

    async with anyio.create_task_group() as group:
        group.start_soon(resolve_cname_value)
        group.start_soon(resolve_address_values)

    cname_target = expected_cname or settings.CNAME_TARGET
    ip_targets = (
        settings.CUSTOM_DOMAIN_IP_TARGETS
        if custom_domain_ip_targets is None
        else custom_domain_ip_targets
    )
    expected_addresses = configured_custom_domain_ips(ip_targets)
    cname_matches = normalize_dns_name(cname_value) == normalize_dns_name(cname_target)
    address_matches = bool(set(address_values) & set(expected_addresses))
    match strategy:
        case DomainDnsStrategy.CNAME:
            verified = cname_matches
            verification_method = "cname" if cname_matches else None
        case DomainDnsStrategy.APEX_ADDRESS:
            verified = address_matches and cname_value is None
            verification_method = "address" if verified else None
        case unreachable:
            assert_never(unreachable)

    return DomainDnsCheck(
        cname_value=cname_value,
        address_values=address_values,
        expected_cname=cname_target,
        expected_addresses=expected_addresses,
        verified=verified,
        verification_method=verification_method,
    )


def strategy_for_hospital(hospital: Hospital) -> DomainDnsStrategy:
    value = getattr(hospital, "domain_dns_strategy", DomainDnsStrategy.CNAME)
    if value is None:
        return DomainDnsStrategy.CNAME
    if isinstance(value, str):
        try:
            return DomainDnsStrategy(value)
        except ValueError:
            return DomainDnsStrategy.CNAME
    return value


def failure_message(
    domain: str,
    strategy: DomainDnsStrategy,
    dns_check: DomainDnsCheck,
) -> str:
    match strategy:
        case DomainDnsStrategy.CNAME:
            return (
                "DNS 검증 실패. CNAME 레코드를 추가해 주세요: "
                f"{domain} → {dns_check.expected_cname}"
            )
        case DomainDnsStrategy.APEX_ADDRESS:
            target = (
                ", ".join(dns_check.expected_addresses)
                or "운영자가 설정한 글로벌 로드밸런서 IP"
            )
            return f"DNS 검증 실패. A/AAAA 레코드를 설정해 주세요: {domain} → {target}"
        case unreachable:
            assert_never(unreachable)
