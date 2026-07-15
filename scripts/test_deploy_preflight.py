import re
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


def test_secret_preflight_requires_enabled_latest_versions() -> None:
    text = DEPLOY_SCRIPT.read_text()
    build_secret_args = text[
        text.index("build_secret_args()") : text.index(
            "prepare_non_secret_env_file", text.index("build_secret_args()")
        )
    ]

    assert "gcloud secrets versions describe latest" in build_secret_args
    assert "ENABLED" in build_secret_args


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
