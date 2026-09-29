"""The backend test URL variables are listed identically in CI, the Makefile and README.

Test DB/Redis URLs have no default (backend/tests/db_env.py), so a variable missing
from one list makes its tests fail there — or, for the release rehearsal, never run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL_VAR = re.compile(r"\b[A-Z0-9_]*(?:DATABASE_URL|REDIS_URL)\b")
# `make test` runs in the compose api container, which cannot reach the loopback-only
# dedicated databases these two tests assert (see the Makefile comment).
NOT_IN_CONTAINER = {"MIGRATION_UPGRADE_DATABASE_URL", "REDELIVERY_TEST_SYNC_DATABASE_URL"}


def _ci_backend_env() -> set[str]:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("\n  backend:\n", 1)[1]
    env = job.split("\n    env:\n", 1)[1].split("\n    steps:\n", 1)[0]
    return set(re.findall(r"^      ([A-Z0-9_]+):", env, re.MULTILINE)) & set(
        URL_VAR.findall(env)
    )


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def _make_list(name: str) -> set[str]:
    match = re.search(rf"^{name} := ((?:.*\\\n)*.*)$", _makefile(), re.MULTILINE)
    assert match, name
    return set(URL_VAR.findall(match.group(1)))


def _make_test_target_env() -> set[str]:
    recipe = _makefile().split("\ntest: test-db-setup\n", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r'-e ([A-Z0-9_]+)="', recipe)) & set(URL_VAR.findall(recipe))


def _readme() -> set[str]:
    readme = (ROOT / "README.md").read_text()
    section = readme.split("테스트 DB·Redis URL에는", 1)[1].split("\n## ", 1)[0]
    return set(URL_VAR.findall(section))


def test_ci_makefile_and_readme_name_the_same_test_url_variables():
    ci = _ci_backend_env()
    local = _make_list("TEST_APP_URL_VARS") | _make_list("TEST_DB_URL_VARS")

    assert "REDIS_URL" in ci and "DATABASE_URL" in ci
    assert local == ci
    assert _readme() == ci
    assert _make_test_target_env() == ci - NOT_IN_CONTAINER


def test_release_rehearsal_sets_every_ci_test_url_variable():
    rehearsal = set(URL_VAR.findall((ROOT / "scripts/verify_release_candidate.py").read_text()))

    assert _ci_backend_env() <= rehearsal
