import os
import shutil
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backend_image_uses_locked_production_dependencies() -> None:
    dockerfile = (PROJECT_ROOT / "backend" / "Dockerfile").read_text()

    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "pip install --no-cache-dir ." not in dockerfile


def test_repo_preserves_the_deployed_migration_head() -> None:
    migration = (
        PROJECT_ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "0031_add_hospital_visual_theme.py"
    )

    assert migration.is_file()
    text = migration.read_text()
    assert 'revision: str = "0031_add_hospital_visual_theme"' in text
    assert 'down_revision: Union[str, None] = "0030_unique_ai_query_target_hospital_name"' in text


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
                "OPENAI_CHATGPT_USE_WEB_SEARCH=false",
                "CERTIFICATE_MANAGER_AUTO_PROVISION=false",
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
                'for arg in "$@"; do',
                '  if [[ "$arg" == --env-vars-file=* ]]; then',
                '    sed "s/^/env /" "${arg#*=}" >> "$FAKE_COMMAND_LOG"',
                "  fi",
                "done",
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
            "OPENAI_CHATGPT_USE_WEB_SEARCH": "true",
            "CERTIFICATE_MANAGER_AUTO_PROVISION": "true",
            "SKIP_PUBLIC_DNS_PREFLIGHT": "1",
            "SKIP_ASSET_BUCKET_PREFLIGHT": "1",
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
    assert result.stderr.index("SKIP_PUBLIC_DNS_PREFLIGHT") < result.stderr.index(
        "Docker 이미지 빌드 중"
    )

    commands = command_log.read_text()
    assert commands.index("gcloud sql users list") < commands.index("docker build")
    assert commands.index("/site/Dockerfile") < commands.index(
        "gcloud run jobs create reputation-migrate"
    )
    assert commands.index("/admin/Dockerfile") < commands.index(
        "gcloud run jobs create reputation-migrate"
    )
    assert commands.index("gcloud run jobs create reputation-migrate") < commands.index(
        "gcloud run deploy reputation-api"
    )
    assert commands.index("gcloud run deploy reputation-worker") < commands.index(
        "gcloud run jobs create reputation-redbeat-reconcile"
    )
    assert commands.index(
        "gcloud run jobs create reputation-redbeat-reconcile"
    ) < commands.index("gcloud run deploy reputation-beat")

    for service in ("reputation-api", "reputation-worker", "reputation-beat"):
        deploy = next(
            line
            for line in commands.splitlines()
            if f"gcloud run deploy {service}" in line
        )
        assert (
            "--set-cloudsql-instances=test-project:asia-northeast3:reputation-db"
            in deploy
        )
        assert "--vpc-connector=reputation-vpc-connector" in deploy
        assert "--vpc-egress=private-ranges-only" in deploy

    assert "--build-arg NEXT_PUBLIC_GCP_STORAGE_BUCKET=reputation-assets" in commands
    site_deploy = next(
        line for line in commands.splitlines() if line.startswith("gcloud run deploy reputation-site")
    )
    site_secrets = next(
        part for part in site_deploy.split() if part.startswith("--set-secrets=")
    )
    # site 필수 시크릿은 반드시 배선된다. 단일 플래그에 쉼표로 합쳐 전달한다.
    assert "SITE_REVALIDATE_SECRET=SITE_REVALIDATE_SECRET:latest" in site_secrets
    assert "SITE_BFF_SECRET=SITE_BFF_SECRET:latest" in site_secrets
    assert 'env OPENAI_CHATGPT_USE_WEB_SEARCH: "true"' in commands
    assert 'env CERTIFICATE_MANAGER_AUTO_PROVISION: "true"' in commands
    assert 'env OPENAI_CHATGPT_USE_WEB_SEARCH: "false"' not in commands
    assert 'env CERTIFICATE_MANAGER_AUTO_PROVISION: "false"' not in commands


def test_supabase_deploy_path_uses_secret_database_urls_without_cloudsql_flags(
    tmp_path: Path,
) -> None:
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
                "OPENAI_CHATGPT_USE_WEB_SEARCH=true",
                "CERTIFICATE_MANAGER_AUTO_PROVISION=true",
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
            "SKIP_ASSET_BUCKET_PREFLIGHT": "1",
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
    # 시크릿은 쉼표로 합친 단일 --set-secrets 플래그로 전달된다(분리 전달 시 gcloud가
    # 덮어쓰기로 해석하면 마지막 하나만 주입되므로). 여기서는 배선 여부만 확인한다.
    assert "DATABASE_URL=DATABASE_URL:latest" in commands
    assert "SYNC_DATABASE_URL=SYNC_DATABASE_URL:latest" in commands
    assert "gcloud run deploy reputation-site" not in commands
    assert "gcloud run deploy reputation-admin" not in commands


def test_site_only_deploy_does_not_require_backend_only_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    (project / "backend").mkdir()
    (project / "site").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", scripts_dir / "deploy.sh")
    (project / ".env.production").write_text("GCP_STORAGE_BUCKET=reputation-assets\n")

    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "gcloud",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "gcloud $*" >> "$FAKE_COMMAND_LOG"',
                'if [[ "$1 $2" == "secrets describe" ]]; then',
                '  case "$3" in',
                "    SITE_REVALIDATE_SECRET|SITE_BFF_SECRET) exit 0 ;;",
                "    *) exit 1 ;;",
                "  esac",
                "fi",
                'if [[ "$1 $2 $3 $4" == "secrets versions describe latest" ]]; then',
                '  echo "ENABLED"',
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
    _write_executable(
        fake_bin / "gsutil",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "gsutil $*" >> "$FAKE_COMMAND_LOG"',
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
            "SKIP_PUBLIC_DNS_PREFLIGHT": "1",
        }
    )

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "site"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "secrets describe SITE_REVALIDATE_SECRET" in commands
    assert "secrets describe SITE_BFF_SECRET" in commands
    assert "secrets describe ANTHROPIC_API_KEY" not in commands
    assert "secrets describe REDIS_URL" not in commands
    assert "gsutil ls -b gs://reputation-assets" in commands
    assert commands.index("gsutil ls -b gs://reputation-assets") < commands.index("docker build")
    assert "gcloud run deploy reputation-site" in commands
    # INDEXNOW_KEY가 없는 환경이어도 site 배포는 성공해야 하고, 없는 시크릿을
    # Cloud Run에 주입하려 들면 안 된다.
    assert "INDEXNOW_KEY=INDEXNOW_KEY:latest" not in commands


def test_site_deploy_wires_indexnow_key_when_the_secret_exists(tmp_path: Path) -> None:
    """선택 시크릿이라도 존재하면 반드시 배선된다.

    IndexNow는 backend가 제출한 URL과 같은 호스트에서 site가 같은 키를 응답해야
    소유가 증명된다. 존재하는데 주입되지 않으면 색인 제출이 조용히 무력화된다.
    """
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    (project / "backend").mkdir()
    (project / "site").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", scripts_dir / "deploy.sh")
    (project / ".env.production").write_text("GCP_STORAGE_BUCKET=reputation-assets\n")

    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "gcloud",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'echo "gcloud $*" >> "$FAKE_COMMAND_LOG"',
                'if [[ "$1 $2" == "secrets describe" ]]; then',
                '  case "$3" in',
                "    SITE_REVALIDATE_SECRET|SITE_BFF_SECRET|INDEXNOW_KEY) exit 0 ;;",
                "    *) exit 1 ;;",
                "  esac",
                "fi",
                'if [[ "$1 $2 $3 $4" == "secrets versions describe latest" ]]; then',
                '  echo "ENABLED"',
                "  exit 0",
                "fi",
                "exit 0",
                "",
            ]
        ),
    )
    for tool in ("docker", "gsutil"):
        _write_executable(
            fake_bin / tool,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    f'echo "{tool} $*" >> "$FAKE_COMMAND_LOG"',
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
            "SKIP_PUBLIC_DNS_PREFLIGHT": "1",
        }
    )

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "site"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    site_deploy = next(
        line for line in commands.splitlines() if line.startswith("gcloud run deploy reputation-site")
    )
    site_secrets = next(
        part for part in site_deploy.split() if part.startswith("--set-secrets=")
    )
    assert "INDEXNOW_KEY=INDEXNOW_KEY:latest" in site_secrets
    # 필수 시크릿이 선택 시크릿 때문에 밀려나지 않는다 — 단일 플래그에 모두 들어간다.
    assert "SITE_REVALIDATE_SECRET=SITE_REVALIDATE_SECRET:latest" in site_secrets
    assert "SITE_BFF_SECRET=SITE_BFF_SECRET:latest" in site_secrets
