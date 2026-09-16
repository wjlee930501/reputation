"""The release gate must execute its isolated image checks and redelivery proofs."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_smoke():
    spec = importlib.util.spec_from_file_location(
        "backend_image_smoke", ROOT / "scripts/verify_backend_image.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_is_inert(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    before = Path.cwd()
    module = load_smoke()
    assert Path.cwd() == before
    assert callable(module.main)


@pytest.mark.parametrize(
    "app_env,dotenv", [("production", "1"), ("test", "0"), ("", "1")]
)
def test_smoke_refuses_nonisolated_environment(monkeypatch, app_env, dotenv):
    module = load_smoke()
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("REPUTATION_DISABLE_DOTENV", dotenv)
    with pytest.raises(RuntimeError, match="Explicit test environment"):
        module.require_isolated_image()


def test_smoke_reports_failure_without_exception_details(monkeypatch, capsys):
    module = load_smoke()

    def fail():
        raise RuntimeError("sensitive details must not be printed")

    monkeypatch.setattr(module, "require_isolated_image", fail)
    assert module.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report == {"ready": False, "error_type": "RuntimeError"}


@pytest.mark.parametrize("ready,code", [(True, 0), (False, 1)])
def test_smoke_exit_status_tracks_all_checks(monkeypatch, capsys, ready, code):
    module = load_smoke()
    monkeypatch.setattr(module, "require_isolated_image", lambda: None)
    monkeypatch.setattr(module, "verify_application", lambda: {"ready": ready})
    assert module.main() == code
    assert json.loads(capsys.readouterr().out)["ready"] is ready


def test_ci_runs_redelivery_against_dedicated_migrated_database():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "postgres_redelivery:" in workflow
    assert "55432:5432" in workflow
    assert (
        "REDELIVERY_TEST_SYNC_DATABASE_URL: postgresql+psycopg2://postgres:postgres@127.0.0.1:55432/reputation_redelivery_test"
        in workflow
    )
    assert workflow.index("Migrate isolated redelivery database") < workflow.index(
        "Pytest (unit + integration, coverage gate)"
    )
    assert (
        "DATABASE_URL: postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/reputation_redelivery_test"
        in workflow
    )


def test_ci_image_smoke_has_no_network_or_production_credentials():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    gate = workflow.split("- name: Backend image runtime smoke", 1)[1].split(
        "# INFRA-4", 1
    )[0]
    assert "docker run --rm --network none" in gate
    assert "APP_ENV=test" in gate and "REPUTATION_DISABLE_DOTENV=1" in gate
    assert "--entrypoint python" in gate and "verify_backend_image.py:ro" in gate
    assert "--env-file" not in gate and "secrets." not in gate
