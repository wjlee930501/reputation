"""DB/Redis test modules may not skip: a missing or dead server must fail the test.

A skip in a DB test lets CI go green while its Postgres/Redis is down or unset, which
is exactly what tests/db_env.py exists to prevent. Per-file skips are easy to
reintroduce (`except OperationalError: pytest.skip(...)`), so this scans the source.

A DB/Redis test module is any .py under backend/tests that imports `tests.db_env` /
`db_env`, or contains a string literal that is a `*DATABASE_URL` / `*REDIS_URL` env var
name, or is one of the shared support modules in `_SHARED_SUPPORT`.

In those modules these are forbidden: calls to `pytest.skip`, `pytest.xfail`,
`unittest.skip*` and `TestCase.skipTest`; the marks `pytest.mark.skip`,
`pytest.mark.skipif` and `pytest.mark.xfail` (as decorators, in `pytestmark` or in
`pytest.param(marks=...)`); and importing `skip`/`xfail` names from pytest/unittest.
`except pytest.skip.Exception` is allowed — it is how a test catches a helper that
skips instead of failing, not a skip.

Exemption rule for `pytest.importorskip`: allowed only with a string-literal module
name that is NOT a DB/Redis driver or ORM (sqlalchemy, asyncpg, psycopg2, psycopg,
redis, alembic) — e.g. an optional PDF/native lib may still importorskip; drivers must
hard-fail. Any other exemption must be an explicit path entry in `_ALLOWED_PATHS` with
a reason (currently none).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_URL_VAR = re.compile(r"[A-Z0-9_]*(DATABASE_URL|REDIS_URL)")
_SHARED_SUPPORT = frozenset(
    {
        "integration/conftest.py",
        "operation_run_signal_support.py",
        "milestone_projector_support.py",
    }
)
_DRIVERS = frozenset({"sqlalchemy", "asyncpg", "psycopg2", "psycopg", "redis", "alembic"})
_FORBIDDEN = frozenset(
    {
        "pytest.skip",
        "pytest.xfail",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
    }
)
_FORBIDDEN_IMPORTS = frozenset({"skip", "xfail", "skipIf", "skipUnless", "expectedFailure"})
# path relative to backend/tests → reason. Prefer none.
_ALLOWED_PATHS: dict[str, str] = {}


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _rel(path: Path) -> str:
    return path.resolve().relative_to(_TESTS_DIR).as_posix()


def is_db_test_module(path: Path, tree: ast.Module) -> bool:
    if _rel(path) in _SHARED_SUPPORT:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name in {"tests.db_env", "db_env"} for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module in {"tests.db_env", "db_env"}:
                return True
            if node.module == "tests" and any(alias.name == "db_env" for alias in node.names):
                return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _URL_VAR.fullmatch(node.value)
        ):
            return True
    return False


def skip_offenders(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = _dotted(node)
            parent = parents.get(node)
            # `except pytest.skip.Exception` references the class, it does not skip.
            if isinstance(parent, ast.Attribute) and parent.attr == "Exception":
                continue
            if name in _FORBIDDEN or name.endswith(".skipTest"):
                found.append(f"{label}:{node.lineno}: {name}")
            elif name == "pytest.importorskip" and isinstance(parent, ast.Call):
                args = parent.args
                module = (
                    args[0].value
                    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)
                    else None
                )
                if module is None or module.split(".")[0] in _DRIVERS:
                    found.append(f"{label}:{node.lineno}: pytest.importorskip({module!r})")
        elif isinstance(node, ast.ImportFrom) and node.module in {"pytest", "unittest"}:
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORTS or alias.name == "importorskip":
                    found.append(f"{label}:{node.lineno}: from {node.module} import {alias.name}")
    return found


def _db_test_modules() -> list[Path]:
    modules = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if is_db_test_module(path, tree):
            modules.append(path)
    return modules


def test_classifier_picks_up_the_known_db_test_modules() -> None:
    classified = {_rel(path) for path in _db_test_modules()}

    assert {
        "integration/conftest.py",
        "operation_run_signal_support.py",
        "milestone_projector_support.py",
        "test_monthly_slots.py",
        "test_incident_service.py",
        "test_operations_models.py",
        "test_migration_0081_withheld_postgres.py",
        "test_migration_upgrade_0064_to_head_postgres.py",
        "test_generation_redelivery_postgres.py",
        "test_cost_guard_redis_integration.py",
        "integration/test_redis_queue_priority.py",
    } <= classified


def test_allowlisted_paths_exist() -> None:
    assert all((_TESTS_DIR / path).is_file() for path in _ALLOWED_PATHS)


def test_db_and_redis_test_modules_never_skip() -> None:
    offenders = [
        found
        for path in _db_test_modules()
        if _rel(path) not in _ALLOWED_PATHS
        for found in skip_offenders(path.read_text(encoding="utf-8"), _rel(path))
    ]

    assert not offenders, (
        "DB/Redis test modules must fail, not skip, when their server is missing or "
        "unreachable (tests/db_env.py require_db_url / require_redis_url / "
        "fail_unreachable):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytest.skip('db down')\n",
        "import pytest\n@pytest.mark.skipif(True, reason='x')\ndef test(): pass\n",
        "import pytest\n@pytest.mark.skip\ndef test(): pass\n",
        "import pytest\npytestmark = [pytest.mark.xfail(reason='x')]\n",
        "import pytest\npytest.param(1, marks=pytest.mark.skip)\n",
        "import pytest\npytest.xfail('x')\n",
        "import pytest\npytest.importorskip('sqlalchemy')\n",
        "import pytest\npytest.importorskip('redis.asyncio')\n",
        "import pytest\nname = 'asyncpg'\npytest.importorskip(name)\n",
        "import unittest\n@unittest.skipIf(True, 'x')\ndef test(): pass\n",
        "from pytest import skip\n",
        "class T:\n    def test(self):\n        self.skipTest('x')\n",
    ],
)
def test_checker_reports_each_skip_shape(source: str) -> None:
    assert skip_offenders(source, "probe")


def test_checker_allows_catching_skips_and_optional_non_driver_imports() -> None:
    source = (
        "import pytest\n"
        "try:\n    helper()\nexcept pytest.skip.Exception as exc:\n    pytest.fail(str(exc))\n"
        "weasyprint = pytest.importorskip('weasyprint')\n"
    )

    assert skip_offenders(source, "probe") == []
