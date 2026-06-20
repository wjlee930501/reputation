from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"


def test_all_target_checks_public_dns_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index("IMAGE_URL=$(build_and_push)", all_case_start)
    dns_preflight = text.index("require_public_dns", all_case_start)

    assert dns_preflight < first_backend_mutation


def test_site_bff_secret_is_a_required_managed_secret() -> None:
    text = DEPLOY_SCRIPT.read_text()
    required_block = text[text.index("REQUIRED_SECRET_NAMES=("):text.index("OPTIONAL_SECRET_NAMES=(")]

    assert '"SITE_BFF_SECRET"' in required_block


def test_secret_preflight_requires_enabled_latest_versions() -> None:
    text = DEPLOY_SCRIPT.read_text()
    build_secret_args = text[text.index("build_secret_args()"):text.index("prepare_non_secret_env_file", text.index("build_secret_args()"))]

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


def test_supabase_mode_requires_database_url_secrets_without_cloudsql_user_gate() -> None:
    text = DEPLOY_SCRIPT.read_text()
    mode_block = text[text.index('case "$DB_CONNECTION_MODE" in'):text.index("BACKEND_RUNTIME_ARGS=()")]
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
    api_block = text[text.index("  api:"):text.index("  worker:")]

    assert "SERVICE: worker" not in api_block
    assert "SERVICE: worker" in text
    assert "SERVICE: beat" in text
    assert "SERVICE: flower" in text
    assert 'CELERY_CONCURRENCY: "4"' in text
