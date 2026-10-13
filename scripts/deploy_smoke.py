#!/usr/bin/env python3
"""배포 뒤 공개 표면 확인 — API release와 병원 헬스(`/.well-known/reputation-health`).

  baseline --public-domain D --out FILE
      배포 전 지금 정상인 병원 공개 호스트를 FILE에 적는다(이미 깨진 병원으로 롤백하지 않기 위해).
  verify --public-domain D --hosts FILE --api-release SHA [--site-release REV]
      API `/api/v1/health/live`의 release가 SHA인지, FILE의 병원마다 헬스가 200이고 slug·canonical_host가 맞고
      (주어지면) Site 리비전이 REV인지 본다. 문제는 한 줄씩 출력한다. 종료 코드:
        0 = 이상 없음
        1 = 롤백 필요 — API release가 다르거나, 기준선 병원의 절반 이상이 실패했다
        3 = 경고 — 일부(절반 미만) 병원만 실패했다(자기 도메인 인증서 등 병원 한 곳의 문제일 수 있다)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Fetch = Callable[[str], tuple[int, bytes]]


def http_get(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"Cache-Control": "no-cache", "User-Agent": "reputation-deploy-smoke"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — https 고정
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def _json(body: bytes) -> object:
    try:
        return json.loads(body or b"null")
    except ValueError:
        return None


def public_hosts(hospitals: object, public_domain: str) -> list[tuple[str, str]]:
    """병원마다 공개 호스트 하나 — 자기 도메인이 있으면 그것, 없으면 `{slug}.{public_domain}`."""
    hosts: list[tuple[str, str]] = []
    for item in hospitals if isinstance(hospitals, list) else []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        custom = str(item.get("aeo_domain") or "").strip().lower()
        hosts.append((custom or f"{slug}.{public_domain}", slug))
    return hosts


def _health_problem(host: str, slug: str, status: int, body: bytes, site_release: str) -> str | None:
    if status != 200:
        return f"{host}: 헬스가 HTTP {status or '연결 실패'}입니다"
    payload = _json(body)
    if not isinstance(payload, dict):
        return f"{host}: 헬스 응답을 읽지 못했습니다"
    if payload.get("slug") != slug:
        return f"{host}: 다른 병원({payload.get('slug')})이 응답했습니다"
    if payload.get("canonical_host") != host:
        return f"{host}: canonical_host가 {payload.get('canonical_host')}입니다"
    if site_release and payload.get("release") != site_release:
        return f"{host}: Site 리비전이 {site_release}가 아닙니다({payload.get('release')})"
    return None


def _with_retries(check: Callable[[], str | None], attempts: int, delay: float) -> str | None:
    problem: str | None = None
    for attempt in range(attempts):
        problem = check()
        if problem is None:
            return None
        if attempt < attempts - 1:
            time.sleep(delay)
    return problem


def baseline_hosts(public_domain: str, fetch: Fetch, *, attempts: int = 3, delay: float = 5.0) -> list[tuple[str, str]]:
    status, body = fetch(f"https://{public_domain}/api/v1/public/hospitals")
    if status != 200:
        return []
    healthy: list[tuple[str, str]] = []
    for host, slug in public_hosts(_json(body), public_domain):
        problem = _with_retries(
            lambda h=host, s=slug: _health_problem(h, s, *fetch(f"https://{h}/.well-known/reputation-health"), ""),
            attempts,
            delay,
        )
        if problem is None:
            healthy.append((host, slug))
    return healthy


@dataclass(frozen=True)
class SmokeResult:
    api_problem: str | None
    host_problems: tuple[str, ...]
    checked_hosts: int

    @property
    def problems(self) -> list[str]:
        return ([self.api_problem] if self.api_problem else []) + list(self.host_problems)

    @property
    def needs_rollback(self) -> bool:
        """새 API가 서비스되지 않았거나, 병원 절반 이상이 깨졌을 때만 배포 탓으로 보고 되돌린다."""
        if self.api_problem:
            return True
        return bool(self.host_problems) and len(self.host_problems) * 2 >= self.checked_hosts


def exit_code(result: SmokeResult) -> int:
    if result.needs_rollback:
        return 1
    return 3 if result.host_problems else 0


def verify(
    public_domain: str,
    hosts: list[tuple[str, str]],
    *,
    api_release: str,
    site_release: str,
    fetch: Fetch,
    attempts: int = 6,
    delay: float = 5.0,
) -> SmokeResult:
    host_problems: list[str] = []

    def api_problem() -> str | None:
        status, body = fetch(f"https://{public_domain}/api/v1/health/live")
        payload = _json(body)
        release = payload.get("release") if isinstance(payload, dict) else None
        if status == 200 and release == api_release:
            return None
        return f"API release가 {api_release}가 아닙니다(받은 값: {release})"

    api = _with_retries(api_problem, attempts, delay)
    for host, slug in hosts:
        problem = _with_retries(
            lambda h=host, s=slug: _health_problem(
                h, s, *fetch(f"https://{h}/.well-known/reputation-health"), site_release
            ),
            min(attempts, 3),
            delay,
        )
        if problem:
            host_problems.append(problem)
    return SmokeResult(api_problem=api, host_problems=tuple(host_problems), checked_hosts=len(hosts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline")
    base.add_argument("--public-domain", required=True)
    base.add_argument("--out", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--public-domain", required=True)
    check.add_argument("--hosts", required=True)
    check.add_argument("--api-release", required=True)
    check.add_argument("--site-release", default="")
    args = parser.parse_args(argv)

    if args.command == "baseline":
        healthy = baseline_hosts(args.public_domain, http_get)
        Path(args.out).write_text("".join(f"{host} {slug}\n" for host, slug in healthy), encoding="utf-8")
        print(f"배포 전 정상 병원 {len(healthy)}곳", file=sys.stderr)
        return 0

    hosts = [
        tuple(line.split(" ", 1))
        for line in Path(args.hosts).read_text(encoding="utf-8").splitlines()
        if " " in line
    ]
    result = verify(
        args.public_domain,
        hosts,  # type: ignore[arg-type]
        api_release=args.api_release,
        site_release=args.site_release,
        fetch=http_get,
    )
    for problem in result.problems:
        print(problem, file=sys.stderr)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
