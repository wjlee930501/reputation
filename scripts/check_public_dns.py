#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainCheckResult:
    domain: str
    addresses: tuple[str, ...]
    ok: bool
    message: str


def _unique_sorted(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def check_domain_addresses(
    domain: str,
    addresses: Sequence[str],
    expected_addresses: Sequence[str] = (),
) -> DomainCheckResult:
    unique_addresses = _unique_sorted(addresses)
    unique_expected_addresses = _unique_sorted(expected_addresses)
    if not unique_addresses:
        return DomainCheckResult(
            domain=domain,
            addresses=unique_addresses,
            ok=False,
            message=f"{domain} did not resolve to any address.",
        )

    unsafe_addresses: list[str] = []
    for address in unique_addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            unsafe_addresses.append(address)

    if unsafe_addresses:
        return DomainCheckResult(
            domain=domain,
            addresses=unique_addresses,
            ok=False,
            message=(
                f"{domain} resolves to non-public address(es): {', '.join(unsafe_addresses)}. "
                "Fix DNS before deploying customer-facing production URLs."
            ),
        )

    if unique_expected_addresses:
        unexpected_addresses = tuple(
            address for address in unique_addresses if address not in unique_expected_addresses
        )
        if unexpected_addresses:
            return DomainCheckResult(
                domain=domain,
                addresses=unique_addresses,
                ok=False,
                message=(
                    f"{domain} resolves to unexpected address(es): {', '.join(unexpected_addresses)}. "
                    f"Expected: {', '.join(unique_expected_addresses)}."
                ),
            )

    return DomainCheckResult(
        domain=domain,
        addresses=unique_addresses,
        ok=True,
        message=f"{domain} resolves to public address(es): {', '.join(unique_addresses)}",
    )


def resolve_addresses(domain: str) -> tuple[str, ...]:
    infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
    return _unique_sorted([info[4][0] for info in infos])


def check_domain(domain: str, expected_addresses: Sequence[str] = ()) -> DomainCheckResult:
    return check_domain_addresses(domain, resolve_addresses(domain), expected_addresses)


def _split_expected_addresses(raw: str) -> tuple[str, ...]:
    return _unique_sorted([item.strip() for item in raw.split(",") if item.strip()])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if public domains resolve to private DNS.")
    parser.add_argument(
        "--expected-addresses",
        default="",
        help="Comma-separated IP addresses that each resolved domain must target.",
    )
    parser.add_argument("domains", nargs="+", help="Customer-facing domains to check before deploy.")
    args = parser.parse_args(argv)
    expected_addresses = _split_expected_addresses(args.expected_addresses)

    failed = False
    for domain in args.domains:
        try:
            result = check_domain(domain, expected_addresses)
        except socket.gaierror as exc:
            failed = True
            print(f"{domain} did not resolve: {exc}", file=sys.stderr)
            continue

        stream = sys.stdout if result.ok else sys.stderr
        print(result.message, file=stream)
        failed = failed or not result.ok

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
