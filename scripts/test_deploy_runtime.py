import os
import shutil
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_all_deploy_path_preserves_preflight_and_runtime_flags(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    (project / "backend").mkdir()
    (project / "site").mkdir()
    (project / "admin").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", scripts_dir / "deploy.sh")
    (project / ".env.production").write_text(
        "\n".join(
            [
                "CLOUD_SQL_CONNECTION_NAME=test-project:asia-northeast3:reputation-db",
                "DB_USER=reputation",
                "GCP_STORAGE_BUCKET=reputation-assets",
                "CUSTOM_DOMAIN_IP_TARGETS=203.0.113.10",
            ]
        )
        + "\n"
    )

    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "gcloud",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "gcloud $*" >> "$FAKE_COMMAND_LOG"',
                'if [[ "$1 $2" == "secrets describe" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3 $4" == "secrets versions describe latest" ]]; then',
                '  echo "ENABLED"',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "sql users list" ]]; then',
                '  echo "reputation"',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run jobs create" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run jobs execute" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-api" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-worker" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-beat" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-site" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-admin" ]]; then',
                "  exit 0",
                "fi",
                "exit 0",
                "",
            ]
        ),
    )
    _write_executable(
        fake_bin / "docker",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "docker $*" >> "$FAKE_COMMAND_LOG"',
                "exit 0",
                "",
            ]
        ),
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_COMMAND_LOG": str(command_log),
            "GCP_PROJECT_ID": "test-project",
            "GCP_REGION": "asia-northeast3",
            "PUBLIC_DOMAIN": "reputation.example.test",
            "ADMIN_DOMAIN": "admin.reputation.example.test",
            "CLOUD_SQL_CONNECTION_NAME": "test-project:asia-northeast3:reputation-db",
            "DB_USER": "reputation",
            "GCP_STORAGE_BUCKET": "reputation-assets",
            "SKIP_PUBLIC_DNS_PREFLIGHT": "1",
        }
    )

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "all"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.index("SKIP_PUBLIC_DNS_PREFLIGHT") < result.stderr.index("Docker 이미지 빌드 중")

    commands = command_log.read_text()
    assert commands.index("gcloud sql users list") < commands.index("docker build")
    assert commands.index("gcloud run jobs create reputation-migrate") < commands.index(
        "gcloud run deploy reputation-api"
    )

    for service in ("reputation-api", "reputation-worker", "reputation-beat"):
        deploy = next(line for line in commands.splitlines() if f"gcloud run deploy {service}" in line)
        assert "--set-cloudsql-instances=test-project:asia-northeast3:reputation-db" in deploy
        assert "--vpc-connector=reputation-vpc-connector" in deploy
        assert "--vpc-egress=private-ranges-only" in deploy

    assert "--build-arg NEXT_PUBLIC_GCP_STORAGE_BUCKET=reputation-assets" in commands
    assert "--set-secrets=SITE_REVALIDATE_SECRET=SITE_REVALIDATE_SECRET:latest,SITE_BFF_SECRET=SITE_BFF_SECRET:latest" in commands


def test_supabase_deploy_path_uses_secret_database_urls_without_cloudsql_flags(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    (project / "backend").mkdir()
    (project / "site").mkdir()
    (project / "admin").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", scripts_dir / "deploy.sh")
    (project / ".env.production").write_text(
        "\n".join(
            [
                "DB_CONNECTION_MODE=supabase",
                "GCP_ATTACH_VPC_CONNECTOR=0",
                "GCP_STORAGE_BUCKET=reputation-assets",
                "CUSTOM_DOMAIN_IP_TARGETS=203.0.113.10",
            ]
        )
        + "\n"
    )

    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "gcloud",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "gcloud $*" >> "$FAKE_COMMAND_LOG"',
                'if [[ "$1 $2" == "secrets describe" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3 $4" == "secrets versions describe latest" ]]; then',
                '  echo "ENABLED"',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run jobs create" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run jobs execute" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-api" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-worker" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-beat" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-site" ]]; then',
                "  exit 0",
                "fi",
                'if [[ "$1 $2 $3" == "run deploy reputation-admin" ]]; then',
                "  exit 0",
                "fi",
                "exit 0",
                "",
            ]
        ),
    )
    _write_executable(
        fake_bin / "docker",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "docker $*" >> "$FAKE_COMMAND_LOG"',
                "exit 0",
                "",
            ]
        ),
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_COMMAND_LOG": str(command_log),
            "GCP_PROJECT_ID": "test-project",
            "GCP_REGION": "asia-northeast3",
            "PUBLIC_DOMAIN": "reputation.example.test",
            "ADMIN_DOMAIN": "admin.reputation.example.test",
            "SKIP_PUBLIC_DNS_PREFLIGHT": "1",
        }
    )

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "backend"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "gcloud sql users list" not in commands
    assert "--set-cloudsql-instances" not in commands
    assert "--vpc-connector" not in commands
    assert "--set-secrets=DATABASE_URL=DATABASE_URL:latest" in commands
    assert "--set-secrets=SYNC_DATABASE_URL=SYNC_DATABASE_URL:latest" in commands
    assert "gcloud run deploy reputation-site" not in commands
    assert "gcloud run deploy reputation-admin" not in commands
