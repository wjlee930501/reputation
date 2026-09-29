"""No test or script may fall back to the local docker-compose Postgres port.

A hardcoded default silently points a DB test at whatever happens to listen there (or
skips it). Test DB URLs come only from env vars via tests/db_env.py, which fails when
one is missing. The CI workflow's explicit service URLs are not scanned.
"""

from __future__ import annotations

from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_REPO_ROOT = _THIS_FILE.parents[2]
# Repo-root scripts (some are loaded by tests). Absent when only backend/ is mounted
# (the `make test` container), in which case only the test tree is scanned.
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
# Built from pieces so this file does not match its own needles.
_PORT = "54" + "34"
_NEEDLES = (f"localhost:{_PORT}", f"127.0.0.1:{_PORT}", f":{_PORT}/")


def _scanned_files() -> list[Path]:
    files = [*_TESTS_DIR.rglob("*.py")]
    if _SCRIPTS_DIR.is_dir():
        files.extend(_SCRIPTS_DIR.rglob("*.py"))
    return sorted(path for path in files if path.resolve() != _THIS_FILE)


def test_scan_covers_the_test_tree() -> None:
    scanned = _scanned_files()

    assert _TESTS_DIR / "db_env.py" in scanned
    assert _TESTS_DIR / "integration" / "conftest.py" in scanned
    assert _THIS_FILE not in scanned


def test_no_test_db_url_defaults_to_the_local_compose_port() -> None:
    offenders = [
        f"{path}:{lineno}: {line.strip()}"
        for path in _scanned_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(needle in line for needle in _NEEDLES)
    ]

    assert not offenders, (
        "Test DB URLs must come from env vars (tests/db_env.py require_db_url), "
        "not a hardcoded local port:\n" + "\n".join(offenders)
    )
