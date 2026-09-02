import os
import re
import shutil
import stat
import subprocess
import sys
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


# Keys where `.env.production.example` intentionally differs from the config.py
# default — each entry is a deliberate prod-vs-dev split, not drift. Add a new key
# here only with a comment explaining why production must diverge; anything else
# that mismatches is almost certainly the kind of stale template value that let
# GOOGLE_IMAGE_MODEL/LEAD_CONSENT_VERSION silently fall behind config.py before.
_ENV_TEMPLATE_INTENTIONAL_OVERRIDES = {
    # Deployment-specific "project:region:instance" placeholder — every real
    # deployment sets its own, so there is no meaningful shared default to match.
    "CLOUD_SQL_CONNECTION_NAME",
    # config.py's defaults are localhost dev conveniences; _validate_production_config()
    # in that same file actively rejects localhost/http/wildcard values in production,
    # so the template must diverge from the default by design.
    "ADMIN_BASE_URL",
    "ALLOWED_ORIGINS",
    "TRUSTED_PROXY_IPS",
    # Empty by default so the local revalidate call is skipped entirely; production
    # points at the real deployed site's revalidate endpoint.
    "SITE_REVALIDATE_URL",
    # Default is False because Terraform owns the shared certificate map by default;
    # production flips this on, and deploy.sh's preflight requires it to be true.
    "CERTIFICATE_MANAGER_AUTO_PROVISION",
}

# Values that mean "replace this before deploying" rather than a real configured
# value — comparing them against config.py's default would be meaningless (an
# empty string is also treated as unset/placeholder).
_SECRET_PLACEHOLDER_MARKERS = ("REPLACE_ME", "REPLACE_WITH")


def _dotenv_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs.append((key.strip(), value.strip()))
    return pairs


def _looks_like_secret_placeholder(value: str) -> bool:
    if not value:
        return True
    return any(marker in value for marker in _SECRET_PLACEHOLDER_MARKERS)


def _env_string_for_default(default: object) -> str:
    """Render a Settings field default the way it would be written in a .env file."""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, list):
        return ",".join(default)
    return str(default)


def _settings_fields() -> dict[str, object]:
    """Return `Settings.model_fields` without triggering production boot validation."""
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    # Settings()는 모듈 임포트 시점에 인스턴스화되고(config.py 하단 `settings = Settings()`),
    # APP_ENV 기본값이 "production"이라 프로덕션 전용 필수 시크릿 검증이 곧장 걸린다 —
    # 이 테스트는 model_fields의 선언된 기본값만 필요하므로 그 분기를 피한다.
    os.environ.setdefault("APP_ENV", "test")
    from app.core.config import Settings  # noqa: PLC0415 — sys.path 준비 후에만 임포트 가능

    return Settings.model_fields


def _deploy_sh_name_arrays() -> tuple[frozenset[str], frozenset[str]]:
    """Read deploy.sh's own key lists so this guard never re-declares them here.

    Returns (Secret Manager 주입 키, deploy.sh가 직접 주입하는 키). Both sets describe
    keys that legitimately have no line in `.env.production.example`, and deploy.sh is
    the single source for both — a hand-copied duplicate here would drift the moment
    someone adds a secret, which is exactly the failure this guard exists to catch.
    """
    text = (PROJECT_ROOT / "scripts" / "deploy.sh").read_text()
    managed: set[str] = set()
    injected: set[str] = set()
    for match in re.finditer(r"^([A-Z_]+)=\(\n(.*?)^\)$", text, re.MULTILINE | re.DOTALL):
        name, body = match.group(1), match.group(2)
        # `"${OTHER[@]}"` 확장 줄은 무시하고 리터럴 키 이름만 모은다.
        values = set(re.findall(r'^\s*"([A-Z][A-Z0-9_]*)"\s*$', body, re.MULTILINE))
        if name.endswith("_SECRET_NAMES"):
            managed |= values
        elif name == "DEPLOY_INJECTED_ENV_KEYS":
            injected |= values
    assert managed, "deploy.sh에서 *_SECRET_NAMES 배열을 찾지 못했다 — 파서가 깨졌다."
    assert injected, "deploy.sh에서 DEPLOY_INJECTED_ENV_KEYS를 찾지 못했다 — 파서가 깨졌다."
    return frozenset(managed), frozenset(injected)


# Settings 필드 중 두 .env 템플릿 어디에도 줄이 없어도 되는 키. Secret Manager 주입 키와
# deploy.sh 자체 주입 키는 위 파서가 deploy.sh에서 직접 읽어 오므로 여기 적지 않는다 —
# 이 목록은 "그 둘 중 어느 쪽도 아닌데 템플릿에 없는" 예외만 이유와 함께 담는다.
_ENV_TEMPLATE_EXEMPT_FIELDS: dict[str, str] = {}

# 반대 방향의 예외 — Settings 필드가 아닌데 템플릿에 줄이 있어도 되는 키. 백엔드
# 설정이 아니라 컨테이너 실행 인자·프런트엔드 빌드 입력·일회성 CLI 입력이라
# config.py에 대응 필드가 없는 것이 정상이다. 여기 없는 템플릿 전용 키는 대개
# 삭제된 Settings 필드의 잔해다(예: 사라진 GCP_LOCATION).
_TEMPLATE_ONLY_KEYS: dict[str, str] = {
    # backend/docker-entrypoint.sh가 celery 실행 인자로만 읽는다.
    "CELERY_CONCURRENCY": "docker-entrypoint.sh celery -c",
    "CELERY_MAX_TASKS_PER_CHILD": "docker-entrypoint.sh celery --max-tasks-per-child",
    # docker-compose.yml의 flower 서비스 basic-auth.
    "FLOWER_USER": "docker-compose flower basic-auth",
    "FLOWER_PASSWORD": "docker-compose flower basic-auth",
    # google-genai/GCS SDK가 직접 읽는 표준 환경변수. compose가 키 파일을 마운트한다.
    "GOOGLE_APPLICATION_CREDENTIALS": "GCP SDK 표준 변수 (docker-compose 볼륨 마운트)",
    # app/utils/admin_user.py의 create-owner CLI가 os.getenv로 한 번 읽는 부트스트랩 입력.
    "ADMIN_EMAIL": "admin_user.py create-owner CLI",
    "ADMIN_NAME": "admin_user.py create-owner CLI",
    "ADMIN_PASSWORD": "admin_user.py create-owner CLI",
    # Next.js(admin/site) 빌드·런타임 입력. 백엔드 Settings에는 대응 필드가 없다.
    "NEXT_PUBLIC_API_URL": "Next.js 빌드 입력",
    "NEXT_PUBLIC_BACKEND_URL": "Next.js 빌드 입력",
    "NEXT_PUBLIC_SITE_URL": "Next.js 빌드 입력",
    "NEXT_PUBLIC_GA_MEASUREMENT_ID": "Next.js 빌드 입력",
    "NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION": "Next.js 빌드 입력",
}


def test_env_templates_declare_every_settings_field() -> None:
    """새 Settings 필드가 프로덕션 주입 경로 없이 추가되는 것을 막는다.

    기존 가드는 한 방향(템플릿에 있는 키만 검사)이라 `SLACK_WEBHOOK_URL_DEV`처럼
    config.py에만 존재하고 어떤 템플릿·시크릿·배포 스크립트에도 없는 필드를 놓쳤다.
    Secret Manager가 주입하는 키와 deploy.sh가 직접 주입하는 키는 템플릿에 줄이 없는
    것이 정상이므로 deploy.sh의 목록에서 그대로 읽어 면제한다.
    """
    fields = set(_settings_fields())
    managed_secrets, deploy_injected = _deploy_sh_name_arrays()
    exempt = managed_secrets | deploy_injected | set(_ENV_TEMPLATE_EXEMPT_FIELDS)

    missing: list[str] = []
    for template in (".env.example", ".env.production.example"):
        declared = {key for key, _ in _dotenv_pairs((PROJECT_ROOT / template).read_text())}
        for key in sorted(fields - declared - exempt):
            missing.append(f"{template}: {key}")

    assert not missing, (
        "config.py의 Settings 필드가 .env 템플릿에 선언되지 않았다 — 프로덕션에 값을 넣을 "
        "경로가 없다는 뜻이다. 템플릿에 줄을 추가하거나, Secret Manager 주입이면 "
        "scripts/deploy.sh의 *_SECRET_NAMES 배열에, deploy.sh가 직접 넣는 값이면 "
        "DEPLOY_INJECTED_ENV_KEYS에 등록할 것. 셋 중 어느 쪽도 아니면 "
        "_ENV_TEMPLATE_EXEMPT_FIELDS에 이유와 함께 추가:\n  " + "\n  ".join(missing)
    )


def test_env_templates_declare_no_keys_that_settings_no_longer_has() -> None:
    """삭제된 Settings 필드의 잔해가 템플릿에 남는 것을 막는다 — 반대 방향 가드다.

    config→템플릿 한 방향만 보던 가드는 `GCP_LOCATION`처럼 config.py에서 사라졌는데
    두 템플릿에 줄만 남은 키를 통과시켰다. 그 줄을 보고 값을 채운 사람은 아무 데도
    도달하지 않는 환경변수를 프로덕션에 싣게 된다.
    """
    fields = set(_settings_fields())
    managed_secrets, deploy_injected = _deploy_sh_name_arrays()
    allowed_extra = managed_secrets | deploy_injected | set(_TEMPLATE_ONLY_KEYS)

    stale: list[str] = []
    for template in (".env.example", ".env.production.example"):
        declared = {key for key, _ in _dotenv_pairs((PROJECT_ROOT / template).read_text())}
        for key in sorted(declared - fields - allowed_extra):
            stale.append(f"{template}: {key}")

    assert not stale, (
        ".env 템플릿에 config.py의 Settings 필드가 아닌 키가 남아 있다 — 대개 삭제된 "
        "필드의 잔해이고, 채워 넣어도 아무 코드도 읽지 않는다. 줄을 지우거나, 백엔드 "
        "설정이 아닌 정당한 키라면 _TEMPLATE_ONLY_KEYS에 소비처와 함께 등록할 것:\n  "
        + "\n  ".join(stale)
    )


def test_production_env_template_matches_config_defaults_except_intentional_overrides() -> None:
    """`.env.production.example`은 deploy.sh가 env를 통째로 교체하는 실제 배포 입력이다 —
    config.py 기본값과 값이 갈리면 그 드리프트가 그대로 프로덕션에 실려 나간다. 실제로
    GOOGLE_IMAGE_MODEL(2.5 vs 3.1)과 LEAD_CONSENT_VERSION(2026-05 vs 2026-08, 재동의 필요)이
    이렇게 뒤처져 있었다.
    """
    fields = _settings_fields()
    template_text = (PROJECT_ROOT / ".env.production.example").read_text()

    mismatches = []
    for key, value in _dotenv_pairs(template_text):
        if key not in fields or key in _ENV_TEMPLATE_INTENTIONAL_OVERRIDES:
            continue
        if _looks_like_secret_placeholder(value):
            continue
        expected = _env_string_for_default(fields[key].default)
        if value != expected:
            mismatches.append(f"{key}: template={value!r} config.py default={expected!r}")

    assert not mismatches, (
        ".env.production.example이 backend/app/core/config.py 기본값과 어긋난다 — "
        "deploy.sh는 env를 전량 교체하므로 이 드리프트가 그대로 프로덕션에 배포된다. "
        "의도된 값이면 _ENV_TEMPLATE_INTENTIONAL_OVERRIDES에 이유와 함께 추가할 것:\n  "
        + "\n  ".join(mismatches)
    )


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


def test_migration_job_receives_the_release_revision(tmp_path: Path) -> None:
    """Production settings validate the rollout identity before Alembic imports models."""
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")

    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "migrate"],
        cwd=project,
        env=_clean_env(
            fake_bin,
            command_log,
            SKIP_ASSET_BUCKET_PREFLIGHT="1",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "gcloud run jobs create reputation-migrate" in commands
    assert 'env REPUTATION_RELEASE_REVISION: "test-source-revision"' in commands


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
