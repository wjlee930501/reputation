"""npm 취약점 게이트 판정 로직.

게이트의 값어치는 **막아야 할 때 막는다**에 전부 있다. 예외 목록이 있는 게이트는
조용히 전부 통과시키는 방향으로 망가지기 쉬워서, 통과 경로보다 차단 경로를 더 촘촘히 본다.
"""
from datetime import date, timedelta

import pytest

import check_npm_audit as gate


def _report(vulns: dict) -> dict:
    return {"vulnerabilities": vulns}


def _vuln(severity: str, *, advisories=(), via_packages=()):
    via: list = [
        {"url": f"https://github.com/advisories/{a}", "title": a} for a in advisories
    ]
    via.extend(via_packages)
    return {"severity": severity, "via": via}


ALLOWED_ID = gate.ALLOWED[0].advisory


@pytest.fixture
def audited(monkeypatch):
    """_audit을 고정 보고서로 바꿔치기하고 판정만 검사한다."""

    def _install(report):
        monkeypatch.setattr(gate, "_audit", lambda _dir: report)

    return _install


# ── 차단 경로 ────────────────────────────────────────────────────────
def test_an_unlisted_high_blocks(audited, tmp_path):
    audited(_report({"leftpad": _vuln("high", advisories=["GHSA-aaaa-bbbb-cccc"])}))
    blockers, _ = gate._check(tmp_path)
    assert len(blockers) == 1
    assert "GHSA-aaaa-bbbb-cccc" in blockers[0]


def test_a_critical_blocks(audited, tmp_path):
    audited(_report({"leftpad": _vuln("critical", advisories=["GHSA-dddd-eeee-ffff"])}))
    blockers, _ = gate._check(tmp_path)
    assert len(blockers) == 1


def test_a_package_with_one_allowed_and_one_new_advisory_still_blocks(audited, tmp_path):
    """예외가 붙은 패키지에 **새 권고가 추가되면** 그건 막아야 한다 —
    패키지 단위로 면제하면 sharp에 새 CVE가 나도 조용히 통과한다."""
    audited(_report({"sharp": _vuln("high", advisories=[ALLOWED_ID, "GHSA-new0-new0-new0"])}))
    blockers, _ = gate._check(tmp_path)
    assert len(blockers) == 1
    assert "GHSA-new0-new0-new0" in blockers[0]
    assert ALLOWED_ID not in blockers[0]


def test_a_transitive_entry_whose_parent_is_not_allowed_blocks(audited, tmp_path):
    audited(
        _report(
            {
                "next": _vuln("high", via_packages=["leftpad"]),
                "leftpad": _vuln("high", advisories=["GHSA-aaaa-bbbb-cccc"]),
            }
        )
    )
    blockers, _ = gate._check(tmp_path)
    assert len(blockers) == 2


def test_a_transitive_entry_with_no_known_parent_blocks(audited, tmp_path):
    """부모를 찾을 수 없는 경유 항목은 판정 불가다 — 통과시키면 사각지대가 된다."""
    audited(_report({"next": _vuln("high", via_packages=["ghost-package"])}))
    blockers, _ = gate._check(tmp_path)
    assert len(blockers) == 1


# ── 통과 경로 ────────────────────────────────────────────────────────
def test_the_allowed_advisory_passes(audited, tmp_path):
    audited(_report({"sharp": _vuln("high", advisories=[ALLOWED_ID])}))
    blockers, observed = gate._check(tmp_path)
    assert blockers == []
    assert observed == {ALLOWED_ID}


def test_a_transitive_entry_passes_when_its_parent_is_allowed(audited, tmp_path):
    """`next`는 sharp를 물고 있어서만 뜬다 — 부모가 허용됐으면 같은 사유로 허용된 것이다."""
    audited(
        _report(
            {
                "next": _vuln("high", via_packages=["sharp"]),
                "sharp": _vuln("high", advisories=[ALLOWED_ID]),
            }
        )
    )
    blockers, _ = gate._check(tmp_path)
    assert blockers == []


def test_moderate_and_low_do_not_block(audited, tmp_path):
    audited(
        _report(
            {
                "a": _vuln("moderate", advisories=["GHSA-mmmm-mmmm-mmmm"]),
                "b": _vuln("low", advisories=["GHSA-llll-llll-llll"]),
            }
        )
    )
    blockers, _ = gate._check(tmp_path)
    assert blockers == []


def test_a_clean_report_passes(audited, tmp_path):
    audited(_report({}))
    blockers, observed = gate._check(tmp_path)
    assert blockers == []
    assert observed == set()


# ── 예외의 기한 ──────────────────────────────────────────────────────
def test_every_shipped_exception_has_a_future_review_date():
    """기한 없는 예외는 영원히 열린 문이 된다. upstream이 고친 뒤에도 그렇다."""
    today = date.today()
    for item in gate.ALLOWED:
        assert item.review_by > today, (
            f"{item.advisory} 예외 기한({item.review_by})이 지났습니다 — "
            "upstream 수정 여부를 다시 확인하세요."
        )


def test_every_shipped_exception_states_a_reason():
    for item in gate.ALLOWED:
        assert len(item.reason) > 40, f"{item.advisory}: 예외 근거가 너무 짧습니다."


def test_review_dates_are_not_set_absurdly_far_out():
    """기한을 5년 뒤로 적으면 기한이 없는 것과 같다."""
    limit = date.today() + timedelta(days=400)
    for item in gate.ALLOWED:
        assert item.review_by <= limit, f"{item.advisory}: 기한이 너무 멉니다."
