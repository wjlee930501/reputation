"""No test or script may fall back to a local Postgres/Redis port.

A hardcoded default silently points a DB test at whatever happens to listen there (or
skips it). Test DB/Redis URLs come only from env vars via tests/db_env.py, which fails
when one is missing. The CI workflow's explicit service URLs are not scanned.

Two layers:
- a text scan for loopback host:port literals of the compose/CI Postgres and Redis
  ports (5432, 5434, 6379);
- an AST scan for the shapes the text scan cannot see: a `*DATABASE_URL`/`*REDIS_URL`
  env read with a default, a `port=` keyword with one of those ports, and an f-string
  that formats a port right after a loopback host.

A legitimate literal goes into `_ALLOWED` with its reason, keyed by path and exact
line content — never obfuscate a literal to dodge the scan.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_REPO_ROOT = _THIS_FILE.parents[2]
# Repo-root scripts (some are loaded by tests). Absent when only backend/ is mounted
# (the `make test` container), in which case only the test tree is scanned.
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
# Built from pieces so this file does not match its own needles.
_PG_PORT, _AUX_PORT, _REDIS_PORT = "54" + "32", "54" + "34", "63" + "79"
_NEEDLES = (
    f"localhost:{_AUX_PORT}",
    f"127.0.0.1:{_AUX_PORT}",
    f":{_AUX_PORT}/",
    f"localhost:{_PG_PORT}",
    f"127.0.0.1:{_PG_PORT}",
    f"localhost:{_REDIS_PORT}",
    f"127.0.0.1:{_REDIS_PORT}",
)
_FORBIDDEN_PORTS = frozenset({int(_PG_PORT), int(_AUX_PORT), int(_REDIS_PORT)})
_URL_VAR = re.compile(r"[A-Z0-9_]*(DATABASE_URL|REDIS_URL)")
_LOOPBACK_PREFIXES = ("localhost:", "127.0.0.1:")

_RELEASE_REHEARSAL = (
    "release rehearsal: builds URLs for the disposable loopback servers it provisioned "
    "on ports from its own context/--port argument, never a default"
)
# (repo-relative path, exact stripped line) → reason.
_ALLOWED: dict[tuple[str, str], str] = {
    (
        "backend/tests/test_config_security.py",
        f'Settings(**_valid_prod_kwargs(REDIS_URL="redis://localhost:{_REDIS_PORT}/0"))',
    ): "asserts that production Settings reject a localhost REDIS_URL; nothing connects",
    (
        "scripts/release_e2e/runtime.py",
        'assert os.environ.get("DATABASE_URL", "").endswith("@qa-db:5432/reputation_release_e2e")',
    ): "asserts the E2E container got the disposable qa-db; '' only makes unset fail the assert",
    (
        "scripts/verify_redelivery_postgres.py",
        'base = f"postgresql://geo_test@127.0.0.1:{args.port}/reputation_redelivery_test"',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_broker.py",
        'os.environ["REDIS_URL"] = f"redis://127.0.0.1:{REDIS_PORT}/8"',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_candidate.py",
        'BASE = f"postgresql://geo_test@127.0.0.1:{PG_PORT}/reputation_release_test"',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_candidate.py",
        'SITE_BASE_URL=f"http://127.0.0.1:{SITE_PORT}", '
        'ADMIN_BASE_URL=f"http://127.0.0.1:{ADMIN_PORT}",',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_candidate.py",
        'REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/0",',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_candidate.py",
        'COST_GUARD_REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/2",',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/verify_release_candidate.py",
        'INTEGRATION_REDIS_URL=f"redis://127.0.0.1:{REDIS_PORT}/3",',
    ): _RELEASE_REHEARSAL,
    (
        "scripts/release_candidate_frontend.py",
        """allowed = " ".join(f'(remote ip "localhost:{context[k]}")'""",
    ): "sandbox egress allowlist for the rehearsal's own frontend ports, not a DB/Redis URL",
    (
        "backend/tests/test_admin_session_revocation.py",
        'revocation_service.settings, "REDIS_URL", '
        "f\"redis://127.0.0.1:{port}{target.path or '/0'}\"",
    ): "points the app at the test's own in-process proxy on an OS-assigned port",
    (
        "backend/tests/test_admin_session_revocation.py",
        'monkeypatch.setattr(revocation_service.settings, "REDIS_URL", '
        'f"redis://127.0.0.1:{port}/0")',
    ): "points the app at the test's own in-process proxy on an OS-assigned port",
    (
        "backend/tests/test_operation_run_signal_support.py",
        'f"postgresql+asyncpg://reputation:reputation@127.0.0.1:{port}/reputation_test",',
    ): "unreachable probe on a just-closed OS-assigned loopback port",
    (
        "backend/tests/test_operation_run_signal_support.py",
        'f"postgresql+psycopg2://reputation:reputation@127.0.0.1:{port}/reputation_test",',
    ): "unreachable probe on a just-closed OS-assigned loopback port",
    (
        "backend/tests/integration/test_content_revision_publish_postgres.py",
        'yield f"http://127.0.0.1:{server.server_port}/reference"',
    ): "test-owned HTTP reference server on an OS-assigned port; not a DB/Redis URL",
    (
        "backend/tests/integration/test_generation_budget_postgres.py",
        'yield f"http://127.0.0.1:{server.server_port}"',
    ): "test-owned HTTP provider server on an OS-assigned port; not a DB/Redis URL",
}


def _scanned_files() -> list[Path]:
    files = [*_TESTS_DIR.rglob("*.py")]
    if _SCRIPTS_DIR.is_dir():
        files.extend(_SCRIPTS_DIR.rglob("*.py"))
    return sorted(path for path in files if path.resolve() != _THIS_FILE)


def _rel(path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(_REPO_ROOT):
        return resolved.as_posix()
    return resolved.relative_to(_REPO_ROOT).as_posix()


def _allowed(path: Path, line: str) -> bool:
    return (_rel(path), line.strip()) in _ALLOWED


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


_ENV_READS_WITH_DEFAULT = {
    "os.getenv": 2,
    "getenv": 2,
    "os.environ.get": 2,
    "environ.get": 2,
    "os.environ.setdefault": 1,
    "environ.setdefault": 1,
}


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _ast_offenders(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    lines = source.splitlines()
    found: list[str] = []

    def report(node: ast.AST, why: str) -> None:
        line = lines[node.lineno - 1]
        if not _allowed(path, line):
            found.append(f"{_rel(path)}:{node.lineno}: {why}: {line.strip()}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            min_args = _ENV_READS_WITH_DEFAULT.get(name)
            if min_args is not None and node.args:
                first = node.args[0]
                var = (
                    first.value
                    if isinstance(first, ast.Constant) and isinstance(first.value, str)
                    else constants.get(first.id) if isinstance(first, ast.Name) else None
                )
                has_default = len(node.args) >= min_args or any(
                    kw.arg == "default" for kw in node.keywords
                )
                if var and _URL_VAR.fullmatch(var) and has_default:
                    report(node, f"{name}({var!r}, <default>)")
            for keyword in node.keywords:
                value = keyword.value
                if (
                    keyword.arg == "port"
                    and isinstance(value, ast.Constant)
                    and value.value in _FORBIDDEN_PORTS
                ):
                    report(node, f"port={value.value}")
        elif isinstance(node, ast.JoinedStr):
            for part, following in zip(node.values, node.values[1:]):
                if (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and part.value.endswith(_LOOPBACK_PREFIXES)
                    and isinstance(following, ast.FormattedValue)
                ):
                    report(node, "f-string formats a port after a loopback host")
    return found


def test_scan_covers_the_test_tree() -> None:
    scanned = _scanned_files()

    assert _TESTS_DIR / "db_env.py" in scanned
    assert _TESTS_DIR / "integration" / "conftest.py" in scanned
    assert _TESTS_DIR / "conftest.py" in scanned
    assert _THIS_FILE not in scanned


def test_allowlist_entries_still_exist() -> None:
    """A stale entry would silently allow the same line to come back elsewhere."""
    stale = [
        f"{path}: {line}"
        for path, line in _ALLOWED
        if (_REPO_ROOT / path).is_file()
        and line not in {
            text.strip() for text in (_REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
        }
    ]

    assert not stale, "Remove allowlist entries that no longer match:\n" + "\n".join(stale)


def test_no_test_db_url_defaults_to_a_local_port() -> None:
    offenders = [
        f"{_rel(path)}:{lineno}: {line.strip()}"
        for path in _scanned_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(needle in line for needle in _NEEDLES) and not _allowed(path, line)
    ]

    assert not offenders, (
        "Test DB/Redis URLs must come from env vars (tests/db_env.py require_db_url / "
        "require_redis_url), not a hardcoded local port:\n" + "\n".join(offenders)
    )


def test_no_url_env_default_port_keyword_or_formatted_loopback_port() -> None:
    offenders = [found for path in _scanned_files() for found in _ast_offenders(path)]

    assert not offenders, (
        "Test DB/Redis URLs must come from env vars without a default "
        "(tests/db_env.py), not a default, a port= literal or a formatted loopback "
        "port:\n" + "\n".join(offenders)
    )


def test_ast_scan_catches_each_shape(tmp_path: Path) -> None:
    """Sanity: the reviewer's bypass mutations (URL.create port, f-string port, 5432
    env default) are each reported."""
    probe = tmp_path / "probe.py"
    samples = {
        "env default": 'import os\nos.getenv("TASK13_DATABASE_URL", "x")\n',
        "environ.get default": 'import os\nos.environ.get("REDIS_URL", "")\n',
        "setdefault": 'import os\nos.environ.setdefault("SYNC_DATABASE_URL", "x")\n',
        "constant name": 'import os\n_ENV = "X_DATABASE_URL"\nos.getenv(_ENV, "x")\n',
        "port keyword": f"URL.create('postgresql', host='h', port={_AUX_PORT})\n",
        "f-string port": 'port = 1\nurl = f"localhost:{port}"\n',
    }
    for label, sample in samples.items():
        probe.write_text(sample, encoding="utf-8")
        assert _ast_offenders(probe), label
    probe.write_text(
        'import os\nos.getenv("TASK13_DATABASE_URL")\nos.getenv("OTHER", "x")\n'
        'url = f"redis://{host}:{port}"\n',
        encoding="utf-8",
    )
    assert _ast_offenders(probe) == []
