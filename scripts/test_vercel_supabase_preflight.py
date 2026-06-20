import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "check_vercel_supabase_deploy.py"
RUNBOOK = PROJECT_ROOT / "docs" / "plans" / "2026-06-20-vercel-supabase-deployment-prep.md"
ENV_EXAMPLE = PROJECT_ROOT / ".env.vercel-supabase.example"


def _run_json() -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    return payload


def test_hybrid_preflight_is_machine_readable_and_warns_runtime_prerequisites() -> None:
    payload = _run_json()
    check_names = {check["name"] for check in payload["checks"]}
    warning_names = {warning["name"] for warning in payload["warnings"]}

    assert "backend.cloud_run_services" in check_names
    assert "backend.supabase_secret_mode" in check_names
    assert "supabase.session_pooler_urls" in check_names
    assert "landing.exclusion_note" in check_names
    assert {
        "runtime.cloud_run_worker_beat",
        "runtime.redis_required",
        "runtime.gcs_storage_required",
        "runtime.live_deployment_not_verified",
    } <= warning_names
    assert payload["scope"] == "repository_deploy_preparation_only"
    assert payload["live_deployment_verified"] is False
    assert "vercel deployment URLs for reputation-admin and reputation-site" in payload["deployment_proof_required"]


def test_hybrid_preflight_runbook_and_env_template_are_complete() -> None:
    payload = _run_json()
    runbook_text = RUNBOOK.read_text()
    env_text = ENV_EXAMPLE.read_text()

    for project in ("reputation-admin", "reputation-site", "reputation-api", "reputation-worker", "reputation-beat"):
        assert project in runbook_text

    for key in payload["required_env"]["admin"] + payload["required_env"]["site"] + payload["required_env"]["gcp_backend"]:
        assert key in env_text

    assert "No marketing landing project/domain is part of this launch" in runbook_text
    assert "Supabase session pooler" in runbook_text
    assert 'DATABASE_URL="$DATABASE_URL"' in runbook_text
    assert 'SYNC_DATABASE_URL="$SYNC_DATABASE_URL"' in runbook_text
    assert "This preflight is a deployment-preparation gate, not live deployment proof" in runbook_text
    assert payload["selected_architecture"] == "vercel_frontends_gcp_backend_supabase_postgres"
