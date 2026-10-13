import json

import pytest

from scripts import deploy_smoke
from scripts.deploy_smoke import (
    BaselineError,
    SmokeResult,
    baseline_hosts,
    exit_code,
    public_hosts,
    verify,
)


def _hospitals() -> bytes:
    return json.dumps([
        {"slug": "jangpyeon", "aeo_domain": None},
        {"slug": "custom", "aeo_domain": "Clinic.Example.TEST"},
        {"slug": "", "aeo_domain": None},
    ]).encode()


def _health(slug: str, host: str, release: str = "site-00143-mvd") -> bytes:
    return json.dumps({"hospital_id": "h", "slug": slug, "canonical_host": host, "release": release}).encode()


def _fetcher(responses: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    def fetch(url: str) -> tuple[int, bytes]:
        calls.append(url)
        return responses.get(url, (0, b""))

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_public_hosts_prefer_the_custom_domain() -> None:
    assert public_hosts(json.loads(_hospitals()), "reputation.example.test") == [
        ("jangpyeon.reputation.example.test", "jangpyeon"),
        ("clinic.example.test", "custom"),
    ]


def test_baseline_keeps_only_hosts_that_are_healthy_now() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/public/hospitals": (200, _hospitals()),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test")),
        "https://clinic.example.test/.well-known/reputation-health": (503, b""),
    })
    assert baseline_hosts("reputation.example.test", fetch, attempts=1, delay=0) == [
        ("jangpyeon.reputation.example.test", "jangpyeon")
    ]


def test_verify_passes_when_api_release_and_hospitals_match() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"status":"ok","release":"sha-new"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test")),
    })
    result = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="site-00143-mvd", fetch=fetch, attempts=1, delay=0,
    )
    assert result.problems == []
    assert exit_code(result) == 0


def test_verify_reports_an_old_api_release_and_a_broken_hospital() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"status":"ok","release":"sha-old"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("other", "jangpyeon.reputation.example.test")),
    })
    result = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="", fetch=fetch, attempts=2, delay=0,
    )
    assert result.needs_rollback
    assert result.problems == [
        "API release가 sha-new가 아닙니다(받은 값: sha-old)",
        "jangpyeon.reputation.example.test: 다른 병원(other)이 응답했습니다",
    ]
    # API 확인은 재시도한다(LB가 새 리비전으로 옮겨 가는 동안).
    assert fetch.calls.count("https://reputation.example.test/api/v1/health/live") == 2


def test_verify_checks_the_site_release_when_given() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"release":"sha-new"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test", release="site-00142-old")),
    })
    result = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="site-00143-mvd", fetch=fetch, attempts=1, delay=0,
    )
    assert result.problems == ["jangpyeon.reputation.example.test: Site 리비전이 site-00143-mvd가 아닙니다(site-00142-old)"]


def test_rollback_only_for_the_api_release_or_at_least_half_of_the_hospitals() -> None:
    one_of_three = SmokeResult(api_problem=None, host_problems=("a: x",), checked_hosts=3)
    two_of_three = SmokeResult(api_problem=None, host_problems=("a: x", "b: x"), checked_hosts=3)
    half = SmokeResult(api_problem=None, host_problems=("a: x",), checked_hosts=2)
    api = SmokeResult(api_problem="API release", host_problems=(), checked_hosts=3)
    clean = SmokeResult(api_problem=None, host_problems=(), checked_hosts=0)

    # 한 곳의 실패(자기 도메인 인증서 등)는 경고(3) — 트래픽을 되돌리지 않는다.
    assert (one_of_three.needs_rollback, exit_code(one_of_three)) == (False, 3)
    assert (two_of_three.needs_rollback, exit_code(two_of_three)) == (True, 1)
    assert (half.needs_rollback, exit_code(half)) == (True, 1)
    assert (api.needs_rollback, exit_code(api)) == (True, 1)
    assert (clean.needs_rollback, exit_code(clean)) == (False, 0)


@pytest.mark.parametrize(
    "response",
    [
        (0, b""),  # 연결 실패
        (503, b"unavailable"),  # LB·API 장애
        (200, b"<html>maintenance</html>"),  # 목록이 아닌 응답
        (200, b'{"items": []}'),  # 형식이 바뀐 응답
    ],
)
def test_baseline_refuses_a_failed_or_malformed_hospital_list(response) -> None:
    fetch = _fetcher({"https://reputation.example.test/api/v1/public/hospitals": response})
    with pytest.raises(BaselineError):
        baseline_hosts("reputation.example.test", fetch, attempts=2, delay=0)
    # 일시 장애일 수 있어 목록 조회는 재시도한다.
    assert fetch.calls.count("https://reputation.example.test/api/v1/public/hospitals") == 2


def test_baseline_accepts_a_genuinely_empty_list() -> None:
    fetch = _fetcher({"https://reputation.example.test/api/v1/public/hospitals": (200, b"[]")})
    assert baseline_hosts("reputation.example.test", fetch, attempts=1, delay=0) == []


def _run_baseline(monkeypatch, tmp_path, response, *extra: str) -> tuple[int, str]:
    fetch = _fetcher({"https://reputation.example.test/api/v1/public/hospitals": response})
    monkeypatch.setattr(deploy_smoke, "http_get", fetch)
    monkeypatch.setattr(deploy_smoke.time, "sleep", lambda _s: None)
    out = tmp_path / "hosts.txt"
    code = deploy_smoke.main(
        ["baseline", "--public-domain", "reputation.example.test", "--out", str(out), *extra]
    )
    return code, out.read_text() if out.exists() else ""


def test_baseline_cli_fails_when_the_list_cannot_be_fetched(monkeypatch, tmp_path) -> None:
    code, written = _run_baseline(monkeypatch, tmp_path, (503, b""))
    assert code == 2
    assert written == ""


def test_baseline_cli_refuses_an_empty_baseline_without_the_explicit_flag(
    monkeypatch, tmp_path
) -> None:
    code, written = _run_baseline(monkeypatch, tmp_path, (200, b"[]"))
    assert code == 4
    assert written == ""


def test_baseline_cli_accepts_an_empty_baseline_with_the_explicit_flag(monkeypatch, tmp_path) -> None:
    code, written = _run_baseline(monkeypatch, tmp_path, (200, b"[]"), "--allow-empty-baseline")
    assert code == 0
    assert written == ""
