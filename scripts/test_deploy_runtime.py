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


# 인자 개수에 상관없이 "$*" 접두사로 분기하는 gcloud 스텁. 배포 경로가 실제로 호출하는
# 하위 명령만 흉내 내고 나머지는 성공으로 흘린다.
_FAKE_GCLOUD = "\n".join(
    [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'echo "gcloud $*" >> "$FAKE_COMMAND_LOG"',
        'for arg in "$@"; do',
        '  if [[ "$arg" == --env-vars-file=* ]]; then',
        '    sed "s/^/env /" "${arg#*=}" >> "$FAKE_COMMAND_LOG"',
        "  fi",
        "done",
        'case "$*" in',
        '  "run services describe "*)',
        '    echo "${FAKE_READY_REVISION:-}"',
        "    exit 0",
        "    ;;",
        '  "secrets versions describe latest"*)',
        '    echo "ENABLED"',
        "    exit 0",
        "    ;;",
        '  "sql users list"*)',
        '    echo "reputation"',
        "    exit 0",
        "    ;;",
        '  "run jobs create "*)',
        '    if [[ "${FAKE_JOBS_CREATE_FAILS:-0}" == "1" ]]; then',
        "      exit 1",
        "    fi",
        "    exit 0",
        "    ;;",
        "esac",
        "exit 0",
        "",
    ]
)

_FAKE_NOOP_TOOL = "\n".join(
    [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'echo "TOOL $*" >> "$FAKE_COMMAND_LOG"',
        "exit 0",
        "",
    ]
)


def _make_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """deploy.sh + terraform/cloudrun.tf를 그대로 복사한 임시 프로젝트를 만든다.

    cloudrun.tf를 복사하는 이유: deploy.sh의 Terraform env 드롭 preflight가 그 파일을
    env 키의 단일 출처로 읽기 때문이다. 복사하지 않으면 검사가 건너뛰어져 회귀를 못 잡는다.
    """
    project = tmp_path / "project"
    fake_bin = tmp_path / "bin"
    (project / "scripts").mkdir(parents=True)
    (project / "terraform").mkdir()
    (project / "backend").mkdir()
    (project / "site").mkdir()
    (project / "admin").mkdir()
    fake_bin.mkdir()

    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", project / "scripts" / "deploy.sh")
    shutil.copy2(
        PROJECT_ROOT / "terraform" / "cloudrun.tf", project / "terraform" / "cloudrun.tf"
    )

    _write_executable(fake_bin / "gcloud", _FAKE_GCLOUD)
    for tool in ("docker", "gsutil"):
        _write_executable(fake_bin / tool, _FAKE_NOOP_TOOL.replace("TOOL", tool))

    return project, fake_bin, tmp_path / "commands.log"


def _clean_env(fake_bin: Path, command_log: Path, **extra: str) -> dict[str, str]:
    """상속된 셸 환경이 검사 대상 값을 대신 채워주지 못하도록 최소 환경만 넘긴다."""
    env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "HOME": os.environ.get("HOME", str(fake_bin)),
        "FAKE_COMMAND_LOG": str(command_log),
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "asia-northeast3",
        "REPUTATION_RELEASE_REVISION": "test-source-revision",
    }
    env.update(extra)
    return env


def test_production_env_template_passes_every_deploy_guard(tmp_path: Path) -> None:
    """`cp .env.production.example .env.production && deploy.sh all`이 실제로 돈다.

    setup-gcp.sh가 안내하는 정식 경로다. 템플릿이 preflight(기능 플래그·측정 모델 핀·
    Terraform env 드롭)에서 막히면 신규 환경은 이 저장소만으로 배포를 시작할 수 없다.
    """
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "all"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            PUBLIC_DOMAIN="reputation.example.test",
            ADMIN_DOMAIN="admin.reputation.example.test",
            SKIP_PUBLIC_DNS_PREFLIGHT="1",
            SKIP_ASSET_BUCKET_PREFLIGHT="1",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    commands = command_log.read_text()
    assert "gcloud run deploy reputation-api" in commands
    assert "gcloud run deploy reputation-site" in commands
    assert "gcloud run deploy reputation-admin" in commands

    # --env-vars-file YAML에 중복 키가 있으면 gcloud가 파일을 거부한다.
    env_keys = [
        line[len("env ") :].split(":", 1)[0]
        for line in commands.splitlines()
        if line.startswith("env ")
    ]
    first_service_env = env_keys[: len(env_keys) // max(env_keys.count("SERVICE"), 1)]
    assert len(first_service_env) == len(set(first_service_env)), sorted(first_service_env)

    release_lines = [
        line for line in commands.splitlines() if line.startswith("env REPUTATION_RELEASE_REVISION:")
    ]
    # api + worker + beat, plus the beat-image reconciliation job, all use one rollout id.
    assert len(release_lines) >= 3
    assert len(set(release_lines)) == 1
    assert release_lines[0].endswith('"')
    assert release_lines[0] == 'env REPUTATION_RELEASE_REVISION: "test-source-revision"'


def test_independent_backend_deploys_keep_one_source_revision(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    for target in ("worker", "api", "beat"):
        result = subprocess.run(
            ["bash", "scripts/deploy.sh", target],
            cwd=project,
            env=_clean_env(
                fake_bin,
                command_log,
                SKIP_ASSET_BUCKET_PREFLIGHT="1",
                REPUTATION_RELEASE_REVISION="same-source-sha",
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    release_lines = [
        line
        for line in command_log.read_text().splitlines()
        if line.startswith("env REPUTATION_RELEASE_REVISION:")
    ]
    assert len(release_lines) >= 3
    assert set(release_lines) == {'env REPUTATION_RELEASE_REVISION: "same-source-sha"'}


def test_backend_deploy_rejects_unknown_release_revision_before_mutation(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            REPUTATION_RELEASE_REVISION="invalid revision with spaces",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "배포 소스 버전 형식" in result.stderr
    assert not command_log.exists() or "run deploy" not in command_log.read_text()


def test_backend_deploy_rejects_too_short_release_revision_before_mutation(
    tmp_path: Path,
) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            REPUTATION_RELEASE_REVISION="short",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "배포 소스 버전 형식" in result.stderr
    assert not command_log.exists() or "run deploy" not in command_log.read_text()


def test_backend_deploy_fails_closed_when_source_revision_is_unavailable(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    env = _clean_env(fake_bin, command_log)
    env["REPUTATION_RELEASE_REVISION"] = ""

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "worker"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "배포 소스 버전을 확인할 수 없습니다" in result.stderr
    assert not command_log.exists()


def test_backend_deploy_fails_closed_for_dirty_source_without_override(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    env_file = project / ".env.production"
    shutil.copy2(PROJECT_ROOT / ".env.production.example", env_file)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "task20@example.test"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Task20"], cwd=project, check=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=project, check=True)
    env_file.write_text(env_file.read_text() + "\n# uncommitted\n")
    env = _clean_env(fake_bin, command_log)
    env["REPUTATION_RELEASE_REVISION"] = ""

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "커밋되지 않은 변경" in result.stderr
    assert not command_log.exists()


def test_deploy_refuses_to_drop_a_terraform_declared_env_key(tmp_path: Path) -> None:
    """Terraform이 선언한 키가 .env.production에 없으면 mutation 전에 멈춘다.

    `gcloud run deploy --env-vars-file`은 기존 env를 전부 지우고 파일 내용만 적용한다.
    cloudrun.tf의 ignore_changes가 그 삭제를 drift로 보지 않으므로 terraform apply로도
    복구되지 않는다 — 배포 시점이 유일한 검문소다.
    """
    project, fake_bin, command_log = _make_project(tmp_path)
    template = (PROJECT_ROOT / ".env.production.example").read_text()
    stripped = "\n".join(
        line for line in template.splitlines() if not line.startswith("SENTRY_DSN=")
    )
    (project / ".env.production").write_text(stripped + "\n")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "backend"],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "SENTRY_DSN" in result.stderr
    assert not command_log.exists() or "docker build" not in command_log.read_text()


def test_migration_job_never_retries_on_create_or_update(tmp_path: Path) -> None:
    """alembic 재실행은 half-applied 스키마를 만든다 — 두 분기 모두 재시도 0이어야 한다.

    기존 Job이 이미 있으면 create가 실패하고 update 폴백이 돈다. 폴백에 플래그가
    빠져 있으면 예전에 max-retries=1로 만들어진 Job이 그대로 재시도한다.
    """
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "migrate"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            FAKE_JOBS_CREATE_FAILS="1",
            SKIP_ASSET_BUCKET_PREFLIGHT="1",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    update = next(
        line
        for line in commands.splitlines()
        if line.startswith("gcloud run jobs update reputation-migrate")
    )
    assert "--max-retries=0" in update
    assert "--max-retries=1" not in commands

    # Terraform 선언과 어긋나면 어느 쪽이 진실인지 알 수 없게 된다.
    cloudrun = (PROJECT_ROOT / "terraform" / "cloudrun.tf").read_text()
    assert "max_retries = 0" in cloudrun


def test_deploy_records_rollback_revisions_and_rollback_restores_traffic(
    tmp_path: Path,
) -> None:
    """중간 실패로 서비스 버전이 섞였을 때 되돌릴 좌표가 남아야 한다."""
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    deploy = subprocess.run(
        ["bash", "scripts/deploy.sh", "backend"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            FAKE_READY_REVISION="reputation-api-00042-abc",
            SKIP_ASSET_BUCKET_PREFLIGHT="1",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert deploy.returncode == 0, deploy.stderr

    state = (project / ".deploy-rollback").read_text()
    for service in ("reputation-api", "reputation-worker", "reputation-beat"):
        assert f"{service}=reputation-api-00042-abc" in state

    # 좌표 기록은 첫 mutation(이미지 빌드) 이전이어야 한다.
    commands = command_log.read_text()
    assert commands.index("run services describe reputation-api") < commands.index(
        "docker build"
    )

    rollback_log = tmp_path / "rollback.log"
    rollback = subprocess.run(
        ["bash", "scripts/deploy.sh", "rollback"],
        cwd=project,
        env=_clean_env(fake_bin, rollback_log),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert rollback.returncode == 0, rollback.stderr

    traffic = [
        line
        for line in rollback_log.read_text().splitlines()
        if line.startswith("gcloud run services update-traffic")
    ]
    assert len(traffic) == 3
    for line in traffic:
        assert "--to-revisions=reputation-api-00042-abc=100" in line


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
                "REPUTATION_RELEASE_REVISION": "test-source-revision",
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
    assert commands.index("gcloud run deploy reputation-beat") < commands.index(
        "gcloud run jobs create reputation-production-readiness"
    )
    assert commands.index("gcloud run jobs create reputation-production-readiness") < commands.index(
        "gcloud run deploy reputation-api"
    )
    assert "--args=-m,app.utils.wait_production_readiness" in commands

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
                "REPUTATION_RELEASE_REVISION": "test-source-revision",
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
                # 실제 gcloud처럼 stderr에 NOT_FOUND를 남긴다. 조용히 exit 1만 하면
                # deploy.sh는 (옳게) "부재인지 조회 실패인지 모르겠다"로 판단해 멈춘다.
                '    *) echo "ERROR: (gcloud.secrets.describe) NOT_FOUND: Secret [projects/p/secrets/$3] not found." >&2; exit 1 ;;',
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
                # 실제 gcloud처럼 stderr에 NOT_FOUND를 남긴다. 조용히 exit 1만 하면
                # deploy.sh는 (옳게) "부재인지 조회 실패인지 모르겠다"로 판단해 멈춘다.
                '    *) echo "ERROR: (gcloud.secrets.describe) NOT_FOUND: Secret [projects/p/secrets/$3] not found." >&2; exit 1 ;;',
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
