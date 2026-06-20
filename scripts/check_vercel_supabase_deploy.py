#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = PROJECT_ROOT / "docs" / "plans" / "2026-06-20-vercel-supabase-deployment-prep.md"
ENV_EXAMPLE = PROJECT_ROOT / ".env.vercel-supabase.example"
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"

VERCEL_PROJECT_ROOTS = {
    "reputation-admin": "admin",
    "reputation-site": "site",
}
GCP_BACKEND_SERVICES = ("reputation-api", "reputation-worker", "reputation-beat")

REQUIRED_ENV = {
    "admin": [
        "BACKEND_URL",
        "NEXT_PUBLIC_BACKEND_URL",
        "ADMIN_SECRET_KEY",
        "ADMIN_SESSION_SECRET",
        "SITE_BFF_SECRET",
    ],
    "site": [
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_SITE_URL",
        "NEXT_PUBLIC_BACKEND_URL",
        "BACKEND_URL",
        "SITE_BFF_SECRET",
        "SITE_REVALIDATE_SECRET",
        "NEXT_PUBLIC_GCP_STORAGE_BUCKET",
    ],
    "gcp_backend": [
        "APP_ENV",
        "DB_CONNECTION_MODE",
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "REDIS_URL",
        "ADMIN_SECRET_KEY",
        "SITE_BFF_SECRET",
        "ALLOWED_ORIGINS",
        "TRUSTED_PROXY_IPS",
        "ADMIN_BASE_URL",
        "SITE_BASE_URL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "SLACK_WEBHOOK_URL",
        "GCP_STORAGE_BUCKET",
        "GCS_REPORTS_BUCKET",
    ],
}


def _finding(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _dotenv_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return values


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _package_check(project: str, rel_root: str) -> dict[str, str]:
    package = _load_json(PROJECT_ROOT / rel_root / "package.json")
    scripts = package.get("scripts", {})
    deps = package.get("dependencies", {})
    has_next = "next" in deps
    has_build = "build" in scripts
    status = "pass" if has_next and has_build else "fail"
    return _finding(f"{project}.vercel_root", status, f"root={rel_root}, next={has_next}, build_script={has_build}")


def _cloud_run_services_check(deploy_text: str) -> dict[str, str]:
    missing = [service for service in GCP_BACKEND_SERVICES if service not in deploy_text]
    status = "pass" if not missing else "fail"
    detail = "api, worker, beat present" if not missing else f"missing={','.join(missing)}"
    return _finding("backend.cloud_run_services", status, detail)


def _supabase_secret_mode_check(deploy_text: str) -> dict[str, str]:
    required_fragments = (
        "DB_CONNECTION_MODE",
        'supabase|external)',
        '"DATABASE_URL" "SYNC_DATABASE_URL"',
        '"REDIS_URL"',
        "GCP_ATTACH_VPC_CONNECTOR",
        "build_backend_runtime_args",
        "backend)",
    )
    missing = [fragment for fragment in required_fragments if fragment not in deploy_text]
    status = "pass" if not missing else "fail"
    detail = "Supabase DB URL secret mode present" if not missing else f"missing={','.join(missing)}"
    return _finding("backend.supabase_secret_mode", status, detail)


def _session_pooler_url_ok(value: str, expected_driver: str) -> bool:
    if value == f"{expected_driver}://SUPABASE_SESSION_POOLER_URL":
        return True
    parsed = urlparse(value)
    if parsed.scheme != expected_driver:
        return False
    hostname = parsed.hostname or ""
    return parsed.port == 5432 and hostname.endswith(".pooler.supabase.com")


def _supabase_check(env_values: dict[str, str]) -> dict[str, str]:
    async_url = env_values.get("DATABASE_URL", "")
    sync_url = env_values.get("SYNC_DATABASE_URL", "")
    has_async = _session_pooler_url_ok(async_url, "postgresql+asyncpg")
    has_sync = _session_pooler_url_ok(sync_url, "postgresql+psycopg2")
    status = "pass" if has_async and has_sync else "fail"
    return _finding("supabase.session_pooler_urls", status, f"async_url={has_async}, sync_url={has_sync}")


def _env_template_check(env_values: dict[str, str]) -> dict[str, str]:
    expected = set(REQUIRED_ENV["admin"] + REQUIRED_ENV["site"] + REQUIRED_ENV["gcp_backend"])
    missing = sorted(expected - set(env_values))
    status = "pass" if not missing else "fail"
    detail = "all required env keys present" if not missing else f"missing={','.join(missing)}"
    return _finding("hybrid.env_template", status, detail)


def _runbook_checks(runbook_text: str) -> list[dict[str, str]]:
    checks = []
    landing_note = "No marketing landing project/domain is part of this launch" in runbook_text
    pooler_note = "Supabase session pooler" in runbook_text
    backend_target_note = "bash scripts/deploy.sh backend" in runbook_text and "Do not use `bash scripts/deploy.sh all`" in runbook_text
    checks.append(_finding("landing.exclusion_note", "pass" if landing_note else "fail", str(landing_note)))
    checks.append(_finding("supabase.pooler_note", "pass" if pooler_note else "fail", str(pooler_note)))
    checks.append(_finding("gcp.backend_target_note", "pass" if backend_target_note else "fail", str(backend_target_note)))
    for project, rel_root in VERCEL_PROJECT_ROOTS.items():
        present = project in runbook_text and f"Root directory: `{rel_root}`" in runbook_text
        checks.append(_finding(f"{project}.runbook", "pass" if present else "fail", f"root={rel_root}"))
    for service in GCP_BACKEND_SERVICES:
        checks.append(_finding(f"{service}.runbook", "pass" if service in runbook_text else "fail", service))
    return checks


def _runtime_warnings() -> list[dict[str, str]]:
    return [
        _finding(
            "runtime.cloud_run_worker_beat",
            "warn",
            "Backend automation stays on Cloud Run because Celery worker/beat are persistent processes.",
        ),
        _finding(
            "runtime.redis_required",
            "warn",
            "REDIS_URL is still required for SlowAPI rate limits, Celery broker/result backend, and RedBeat.",
        ),
        _finding(
            "runtime.gcs_storage_required",
            "warn",
            "GCS/Vertex asset and report flows remain in use for same-day onboarding.",
        ),
    ]


def build_report() -> dict:
    env_values = _dotenv_values(_read_text(ENV_EXAMPLE))
    runbook_text = _read_text(RUNBOOK)
    deploy_text = _read_text(DEPLOY_SCRIPT)
    checks = [
        *[_package_check(project, rel_root) for project, rel_root in VERCEL_PROJECT_ROOTS.items()],
        _cloud_run_services_check(deploy_text),
        _supabase_secret_mode_check(deploy_text),
        _env_template_check(env_values),
        _supabase_check(env_values),
        *_runbook_checks(runbook_text),
    ]
    status = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "status": status,
        "selected_architecture": "vercel_frontends_gcp_backend_supabase_postgres",
        "vercel_project_roots": VERCEL_PROJECT_ROOTS,
        "gcp_backend_services": list(GCP_BACKEND_SERVICES),
        "required_env": REQUIRED_ENV,
        "checks": checks,
        "warnings": _runtime_warnings(),
    }


def _print_text(report: dict) -> None:
    print(f"status: {report['status']}")
    print(f"selected_architecture: {report['selected_architecture']}")
    print("checks:")
    for check in report["checks"]:
        print(f"  - {check['status']:4} {check['name']}: {check['detail']}")
    print("warnings:")
    for warning in report["warnings"]:
        print(f"  - {warning['name']}: {warning['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run hybrid Vercel + GCP + Supabase readiness checks.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
