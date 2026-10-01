"""Pins the dedicated-DB URL variables: unset or empty must FAIL naming the variable.

OPERATIONS_TEST_DATABASE_URL, MIGRATION_UPGRADE_DATABASE_URL and
REDELIVERY_TEST_SYNC_DATABASE_URL used to skip when unset, which let a CI job that
forgot them go green without running the tests. Each module now reads its URL at
test time through `tests.db_env.require_db_url`. The static guard also catches a
module-level unset-then-skip that the accessor check alone would not see.
No database needed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import pytest
import test_generation_redelivery_postgres
import test_migration_0081_withheld_postgres
import test_migration_upgrade_0064_to_head_postgres
import test_operations_models

_OPERATIONS = "OPERATIONS_TEST_DATABASE_URL"
_MIGRATION = "MIGRATION_UPGRADE_DATABASE_URL"
_REDELIVERY = "REDELIVERY_TEST_SYNC_DATABASE_URL"

_CASES = [
    pytest.param(test_operations_models, _OPERATIONS, id="operations_models"),
    pytest.param(test_migration_0081_withheld_postgres, _MIGRATION, id="migration_0081"),
    pytest.param(
        test_migration_upgrade_0064_to_head_postgres, _MIGRATION, id="migration_0064_to_head"
    ),
    pytest.param(test_generation_redelivery_postgres, _REDELIVERY, id="generation_redelivery"),
]


def _expect_failure(module: ModuleType) -> str:
    """Call the module's URL accessor; return the failure message.

    Skipped is not a Failed, so pytest.raises(pytest.fail.Exception) would let a skip
    through and report the test SKIPPED: catch it and fail instead.
    """
    try:
        module._database_url()
    except pytest.skip.Exception as exc:
        pytest.fail(f"{module.__name__} skipped instead of failing: {exc}")
    except pytest.fail.Exception as exc:
        return str(exc)
    pytest.fail(f"{module.__name__} returned a URL with the variable unset")


@pytest.mark.parametrize(("module", "env_name"), _CASES)
def test_unset_url_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, env_name: str
) -> None:
    monkeypatch.delenv(env_name, raising=False)

    message = _expect_failure(module)

    assert message.startswith(f"{env_name} is not set")


def test_empty_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_REDELIVERY, "")

    message = _expect_failure(test_generation_redelivery_postgres)

    assert message.startswith(f"{_REDELIVERY} is not set")


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    func = node.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


@pytest.mark.parametrize(("module", "env_name"), _CASES)
def test_module_source_has_no_skip_path_for_the_url(module: ModuleType, env_name: str) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert "pytest.mark.skipif(" not in source
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        segment = ast.get_source_segment(source, node) or ""
        if name == "pytest.skip":
            assert env_name not in segment and "_URL_ENV" not in segment, segment
        # The URL must reach the test only through require_db_url, so no read of the
        # variable can back a hand-written unset check.
        if name in {"os.getenv", "os.environ.get"}:
            assert env_name not in segment and "_URL_ENV" not in segment, segment
    assert f'os.environ["{env_name}"]' not in source
