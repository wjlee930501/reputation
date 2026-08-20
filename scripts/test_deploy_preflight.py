import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"
SETUP_GCP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-gcp.sh"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import check_db_connection_budget  # noqa: E402


def _bash_array_block(text: str, header: str) -> str:
    """header 뒤부터 자체 라인의 닫는 ')'까지를 반환 — 주석 안의 ')'에 걸리지 않는다."""
    start = text.index(header)
    lines = []
    for line in text[start:].splitlines()[1:]:
        if line.strip() == ")":
            break
        lines.append(line)
    return "\n".join(lines)


def _setup_gcp_secret_names() -> set[str]:
    block = _bash_array_block(SETUP_GCP_SCRIPT.read_text(), "declare -A SECRETS=(")
    # 주석 라인은 제외하고 실제 배열 항목(["NAME"]=)만 파싱.
    keys = set()
    for line in block.splitlines():
        if line.lstrip().startswith("#"):
            continue
        keys.update(re.findall(r'\["([^"]+)"\]=', line))
    return keys


def _deploy_base_required_secret_names() -> list[str]:
    block = _bash_array_block(DEPLOY_SCRIPT.read_text(), "BASE_REQUIRED_SECRET_NAMES=(")
    names = []
    for line in block.splitlines():
        if line.lstrip().startswith("#"):
            continue
        names.extend(re.findall(r'"([^"]+)"', line))
    return names


def test_db_connection_budget_within_cloud_sql_limit() -> None:
    budget = check_db_connection_budget.compute_budget()
    assert budget["total"] <= budget["limit"], budget
    # config.py 풀 분리 + terraform 인스턴스 수/CELERY_CONCURRENCY가 실제로 파싱됐는지 확인.
    assert budget["total"] == budget["api_conns"] + budget["worker_conns"]
    assert check_db_connection_budget.main() == 0
    assert budget["total"] < budget["max_connections"]
    assert budget["max_connections"] - budget["total"] >= 20


def test_setup_gcp_creates_all_deploy_required_secret_containers() -> None:
    setup_secrets = _setup_gcp_secret_names()
    required = _deploy_base_required_secret_names()
    # REDIS_URL 누락이 표준 순서 첫 배포를 무조건 실패시키던 회귀를 고정.
    assert "REDIS_URL" in setup_secrets
    missing = [name for name in required if name not in setup_secrets]
    assert missing == [], (
        f"setup-gcp.sh SECRETS missing deploy-required containers: {missing}"
    )


def test_setup_gcp_and_deploy_share_default_region() -> None:
    # 리전이 어긋나면 Artifact Registry/버킷/Cloud SQL이 서로 다른 리전에 흩어진다.
    assert 'REGION="${GCP_REGION:-asia-northeast3}"' in SETUP_GCP_SCRIPT.read_text()
    assert 'REGION="${GCP_REGION:-asia-northeast3}"' in DEPLOY_SCRIPT.read_text()


def test_backend_deploy_paths_run_asset_bucket_preflight() -> None:
    text = DEPLOY_SCRIPT.read_text()
    assert "require_asset_bucket()" in text
    for anchor in ("  backend)", "  api|worker|beat)", "  all)"):
        start = text.index(anchor)
        end = text.index(";;", start)
        assert "require_asset_bucket" in text[start:end], anchor


def test_api_target_deploys_certificate_worker_before_api() -> None:
    """새 API가 발행하는 전용 큐를 소비할 워커가 먼저 준비되어야 한다."""
    text = DEPLOY_SCRIPT.read_text()
    shared_case_start = text.index("  api|worker|beat)")
    shared_case_end = text.index(";;", shared_case_start)
    block = text[shared_case_start:shared_case_end]

    assert 'capture_rollback_point reputation-worker reputation-beat reputation-api' in block
    api_branch_start = block.rindex('if [[ "$TARGET" == "api" ]]')
    api_branch_end = block.index("\n    else", api_branch_start)
    api_branch = block[api_branch_start:api_branch_end]
    assert api_branch.index('deploy_worker "$IMAGE_URL"') < api_branch.index(
        'run_redbeat_reconcile "$IMAGE_URL"'
    )
    assert api_branch.index('run_redbeat_reconcile "$IMAGE_URL"') < api_branch.index(
        'deploy_beat "$IMAGE_URL"'
    )
    assert api_branch.index('deploy_beat "$IMAGE_URL"') < api_branch.index(
        'run_production_readiness_gate "$IMAGE_URL"'
    )
    assert api_branch.index('run_production_readiness_gate "$IMAGE_URL"') < api_branch.index(
        'deploy_api "$IMAGE_URL"'
    )


def test_all_target_checks_public_dns_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index("IMAGE_URL=$(build_and_push)", all_case_start)
    dns_preflight = text.index("require_public_dns", all_case_start)

    assert dns_preflight < first_backend_mutation


def test_site_bff_secret_is_a_required_managed_secret() -> None:
    text = DEPLOY_SCRIPT.read_text()
    required_block = text[
        text.index("REQUIRED_SECRET_NAMES=(") : text.index("OPTIONAL_SECRET_NAMES=(")
    ]

    assert '"SITE_BFF_SECRET"' in required_block


def _secret_preflight_block() -> str:
    """시크릿 사전 검증 함수들만 떼어낸다 — deploy.sh 전체는 source할 수 없다."""
    text = DEPLOY_SCRIPT.read_text()
    start = text.index('SECRET_LOOKUP_ERROR=""')
    # build_secret_args를 닫는 자체 라인 '}'까지 — 그 뒤 최상위 코드가 딸려오면 안 된다.
    end = text.index("\n}\n", text.index("build_secret_args()")) + len("\n}\n")
    return text[start:end]


# 이름으로 응답을 고르는 가짜 gcloud. 이름에 AUTH가 들어가면 인증 만료를, MISSING이면
# 진짜 부재를 흉내낸다 — 실제 gcloud가 두 경우에 내놓는 문구를 그대로 쓴다.
_FAKE_GCLOUD = """#!/usr/bin/env bash
args="$*"
if [[ "$args" == *"versions describe latest"* ]]; then
  name="${args#*--secret=}"; name="${name%% *}"
else
  name="$3"
fi
case "$name" in
  *AUTH*)
    echo "ERROR: (gcloud.secrets.describe) There was a problem refreshing your current auth tokens: ('invalid_grant: reauth related error (invalid_rapt)',). Please run: \\$ gcloud auth login" >&2
    exit 1 ;;
  *MISSING*)
    echo "ERROR: (gcloud.secrets.describe) NOT_FOUND: Secret [projects/p/secrets/$name] not found." >&2
    exit 1 ;;
  *DISABLED*)
    if [[ "$args" == *"versions describe"* ]]; then echo DISABLED; fi
    exit 0 ;;
  *)
    if [[ "$args" == *"versions describe"* ]]; then echo ENABLED; fi
    exit 0 ;;
esac
"""


def _run_secret_preflight(
    tmp_path: Path, required: list[str], optional: list[str]
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(_FAKE_GCLOUD)
    gcloud.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        "BOLD=''; RESET=''\n"
        "PROJECT_ID=test-project\n"
        'fail() { echo "$1" >&2; exit 1; }\n'
        + _secret_preflight_block()
        + "\nREQUIRED_SECRET_NAMES=({})\n".format(" ".join(f'"{n}"' for n in required))
        + "OPTIONAL_SECRET_NAMES=({})\n".format(" ".join(f'"{n}"' for n in optional))
        + "build_secret_args\n"
        + 'echo "SECRET_ARGS=${SECRET_ARGS[*]:-}"\n'
    )

    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, env=env
    )


def test_secret_preflight_requires_enabled_latest_versions(tmp_path: Path) -> None:
    ok = _run_secret_preflight(tmp_path / "ok", ["OKSECRET"], [])
    assert ok.returncode == 0, ok.stderr
    assert "OKSECRET=OKSECRET:latest" in ok.stdout

    disabled = _run_secret_preflight(tmp_path / "dis", ["DISABLEDSECRET"], [])
    assert disabled.returncode == 1
    assert "must be ENABLED" in disabled.stderr


def test_missing_secret_is_reported_as_missing(tmp_path: Path) -> None:
    result = _run_secret_preflight(tmp_path, ["MISSINGSECRET"], [])
    assert result.returncode == 1
    assert "is missing" in result.stderr


def test_lookup_failure_is_not_reported_as_a_missing_secret(tmp_path: Path) -> None:
    """인증 만료를 '시크릿이 없다'로 안내하면, 안내를 따라간 운영자가 멀쩡히 있는
    로테이션 금지 시크릿에 새 버전을 덧씌운다. 그 문구가 나오면 안 된다."""
    result = _run_secret_preflight(tmp_path, ["AUTHSECRET"], [])
    assert result.returncode == 1
    assert "is missing" not in result.stderr
    assert "gcloud auth login" in result.stderr
    assert "새로 만들지 마세요" in result.stderr


def test_optional_secret_is_dropped_only_when_confirmed_absent(tmp_path: Path) -> None:
    absent = _run_secret_preflight(tmp_path / "absent", ["OKSECRET"], ["MISSINGOPT"])
    assert absent.returncode == 0, absent.stderr
    assert "MISSINGOPT" not in absent.stdout

    # 조회 실패까지 부재로 삼키면 시크릿이 조용히 빠진 채 배포가 초록색으로 끝난다.
    unknown = _run_secret_preflight(tmp_path / "unknown", ["OKSECRET"], ["AUTHOPT"])
    assert unknown.returncode == 1
    assert "AUTHOPT" in unknown.stderr


def test_backend_deploys_use_conditional_database_and_network_args() -> None:
    text = DEPLOY_SCRIPT.read_text()
    runtime_args_start = text.index("build_backend_runtime_args()")
    runtime_args_end = text.index("\n}\n", runtime_args_start)
    runtime_args = text[runtime_args_start:runtime_args_end]
    assert "--set-cloudsql-instances=$CLOUDSQL_CONNECTION" in runtime_args
    assert "--vpc-connector=$VPC_CONNECTOR" in runtime_args
    assert "--vpc-egress=$VPC_EGRESS" in runtime_args

    for function_name in ("deploy_api()", "deploy_worker()", "deploy_beat()"):
        start = text.index(function_name)
        end = text.index("\n}\n", start)
        block = text[start:end]
        assert '"${BACKEND_RUNTIME_ARGS[@]}"' in block


def test_supabase_mode_requires_database_url_secrets_without_cloudsql_user_gate() -> (
    None
):
    text = DEPLOY_SCRIPT.read_text()
    mode_block = text[
        text.index('case "$DB_CONNECTION_MODE" in') : text.index(
            "BACKEND_RUNTIME_ARGS=()"
        )
    ]
    assert "supabase|external)" in mode_block
    assert '"DATABASE_URL" "SYNC_DATABASE_URL"' in mode_block


def test_cloudsql_app_user_is_gated_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    assert "gcloud sql users list" in text
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index("IMAGE_URL=$(build_and_push)", all_case_start)
    user_preflight = text.index("require_cloudsql_app_user", all_case_start)
    assert user_preflight < first_backend_mutation


def test_shell_e2e_scripts_exit_nonzero_when_fail_count_is_positive() -> None:
    for rel in ("scripts/test_e2e.sh", "scripts/test_full.sh"):
        text = (PROJECT_ROOT / rel).read_text()
        assert "[[ $FAIL -gt 0 ]]" in text
        assert "exit 1" in text


def test_docker_compose_worker_beat_flower_select_non_api_services() -> None:
    text = (PROJECT_ROOT / "docker-compose.yml").read_text()
    api_block = text[text.index("  api:") : text.index("  worker:")]

    assert "SERVICE: worker" not in api_block
    assert "SERVICE: worker" in text
    assert "SERVICE: beat" in text
    assert "SERVICE: flower" in text
    assert 'CELERY_CONCURRENCY: "4"' in text


def test_all_target_requires_admin_domain_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index("IMAGE_URL=$(build_and_push)", all_case_start)
    admin_domain_preflight = text.index("require_admin_domain", all_case_start)

    assert admin_domain_preflight < first_backend_mutation


def test_all_target_builds_all_images_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index('run_migration "$IMAGE_URL"', all_case_start)
    site_image_build = text.index(
        "SITE_IMAGE_URL=$(build_and_push_site)", all_case_start
    )
    admin_image_build = text.index(
        "ADMIN_IMAGE_URL=$(build_and_push_admin)", all_case_start
    )

    assert site_image_build < first_backend_mutation
    assert admin_image_build < first_backend_mutation


def test_admin_target_requires_admin_domain_before_build() -> None:
    text = DEPLOY_SCRIPT.read_text()
    admin_case_start = text.index("  admin)")
    first_admin_build = text.index(
        "ADMIN_IMAGE_URL=$(build_and_push_admin)", admin_case_start
    )
    admin_domain_preflight = text.index("require_admin_domain", admin_case_start)

    assert admin_domain_preflight < first_admin_build


def test_public_dns_preflight_checks_custom_domain_targets() -> None:
    text = DEPLOY_SCRIPT.read_text()
    setup_start = text.index("read_env_file_value()")
    preflight_start = text.index("require_public_dns()")
    preflight_end = text.index("build_and_push_site()", preflight_start)
    setup_block = text[setup_start:preflight_start]
    preflight_block = text[preflight_start:preflight_end]

    assert (
        'CNAME_TARGET="${CNAME_TARGET:-$(read_env_file_value CNAME_TARGET || true)}"'
        in setup_block
    )
    assert (
        'WILDCARD_PUBLIC_DOMAIN_CHECK="${WILDCARD_PUBLIC_DOMAIN_CHECK:-}"'
        in setup_block
    )
    assert 'domains+=("$CNAME_TARGET")' in preflight_block
    assert 'domains+=("$WILDCARD_PUBLIC_DOMAIN_CHECK")' in preflight_block
    assert (
        'WILDCARD_PUBLIC_DOMAIN_CHECK="dns-preflight.${PUBLIC_DOMAIN}"'
        in preflight_block
    )


def test_mso_platform_tfvars_preserves_current_customer_domains_on_certificate_map() -> (
    None
):
    text = (
        PROJECT_ROOT / "terraform" / "terraform.mso-platform.example.tfvars"
    ).read_text()

    assert 'customer_domains = ["jangclinic.kr"]' in text
    assert 'certificate_map_customer_domains = ["jangclinic.kr"]' in text
    assert "use_certificate_map = true" in text
    assert "api_min_instances    = 1" in text
    assert "site_min_instances   = 1" in text


def test_backend_deploy_requires_truthful_search_and_domain_automation_flags() -> None:
    text = DEPLOY_SCRIPT.read_text()
    assert "require_production_feature_flags()" in text
    assert 'OPENAI_CHATGPT_USE_WEB_SEARCH_VALUE" == "true"' in text
    assert 'CERTIFICATE_MANAGER_AUTO_PROVISION_VALUE" == "true"' in text

    for anchor in ("  backend)", "  api|worker|beat)", "  all)", "  migrate)"):
        start = text.index(anchor)
        end = text.index(";;", start)
        assert "require_production_feature_flags" in text[start:end], anchor


def test_backend_deploy_reconciles_redbeat_before_new_beat_rollout() -> None:
    text = DEPLOY_SCRIPT.read_text()
    assert "run_redbeat_reconcile()" in text
    assert "app.utils.reconcile_redbeat_schedule,--apply" in text

    for anchor in ("  backend)", "  all)"):
        start = text.index(anchor)
        end = text.index(";;", start)
        block = text[start:end]
        assert block.index('run_redbeat_reconcile "$IMAGE_URL"') < block.index(
            'deploy_beat "$IMAGE_URL"'
        )


def _model_gate(value: str, key: str = "OPENAI_MODEL_QUERY") -> int:
    """require_pinned_measurement_models만 떼어내 실행. 0=통과, 1=차단."""
    text = DEPLOY_SCRIPT.read_text()
    start = text.index("require_pinned_measurement_models() {")
    end = text.index("\n}\n", start) + 3
    harness = (
        "set -euo pipefail\n"
        'fail() { echo "$1" >&2; exit 1; }\n'
        f'OPENAI_MODEL_QUERY_VALUE=""\nOPENAI_MODEL_PARSE_VALUE=""\nGEMINI_MODEL_VALUE=""\n'
        f'{key}_VALUE={value!r}\n'
        + text[start:end]
        + "\nrequire_pinned_measurement_models\n"
    )
    return subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, timeout=30
    ).returncode


def test_model_gate_accepts_the_current_pinned_models() -> None:
    """회귀 방지: 날짜 접미사가 없는 최신 정식 모델도 통과해야 한다.

    예전 게이트는 `-YYYY-MM-DD`를 요구해 gpt-5.6-luna/sol/terra를 전부 막았다 —
    측정 무결성을 지키려던 검사가 무결성 개선을 막았다.
    """
    for value in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-4o-mini-2024-07-18"):
        assert _model_gate(value) == 0, f"{value} 가 차단됐다"
    assert _model_gate("gemini-3.6-flash", "GEMINI_MODEL") == 0


def test_model_gate_still_blocks_provider_reinterpreted_names() -> None:
    """공급자가 새 스냅샷으로 옮기는 형태는 계속 막아야 한다."""
    for value in ("gpt-5-chat-latest", "gpt-5", "gpt-5-mini", "gpt-4o"):
        assert _model_gate(value) == 1, f"{value} 가 통과했다 (부동 별칭인데)"
    for value in ("gemini-flash-latest", "gemini-flash"):
        assert _model_gate(value, "GEMINI_MODEL") == 1, f"{value} 가 통과했다"


def test_site_revalidate_secret_is_required_for_backend_not_optional() -> None:
    """런타임이 필수로 요구하는 시크릿을 배포가 선택으로 두면 안 된다.

    아침 자동 발행(morning_content_auto_publish)은 발행 루프 진입 전에
    ensure_site_revalidate_configured()로 배치 전체를 중단한다. 배포는 통과하는데
    매일 아침 발행만 0건이 되는 조합이 성립했다.
    """
    text = DEPLOY_SCRIPT.read_text()
    required = _bash_array_block(text, "BACKEND_BASE_REQUIRED_SECRET_NAMES=(")
    optional = _bash_array_block(text, "BACKEND_OPTIONAL_SECRET_NAMES=(")

    assert '"SITE_REVALIDATE_SECRET"' in required, "backend 필수 시크릿에 없다"
    assert '"SITE_REVALIDATE_SECRET"' not in optional, "여전히 선택으로도 남아 있다"


def test_runtime_fail_closed_secrets_are_all_required_at_deploy() -> None:
    """런타임이 프로덕션에서 부재를 이유로 실패시키는 설정은 배포가 보장해야 한다."""
    revalidate = (
        PROJECT_ROOT / "backend" / "app" / "services" / "site_revalidate.py"
    ).read_text()
    assert "SITE_REVALIDATE_SECRET" in revalidate and "must be configured in production" in revalidate

    required = _bash_array_block(DEPLOY_SCRIPT.read_text(), "BACKEND_BASE_REQUIRED_SECRET_NAMES=(")
    assert '"SITE_REVALIDATE_SECRET"' in required
