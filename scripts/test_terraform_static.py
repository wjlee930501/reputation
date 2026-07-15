from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHA256_DIGEST_PATTERN = re.compile(r"@sha256:\[0-9a-f\]\{64\}")


def terraform_block(text: str, header: str) -> str:
    start = text.index(header)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated Terraform block: {header}")


def test_cloud_run_image_locals_do_not_fall_back_to_latest_tags() -> None:
    backend = (PROJECT_ROOT / "terraform" / "cloudrun.tf").read_text()
    frontend = (PROJECT_ROOT / "terraform" / "cloudrun_frontend.tf").read_text()

    assert ":latest" not in backend
    assert ":latest" not in frontend
    assert "app_image = var.api_image" in backend
    assert "site_image  = var.site_image" in frontend
    assert "admin_image = var.admin_image" in frontend


def test_cloud_run_image_variables_are_required_inputs() -> None:
    variables = (PROJECT_ROOT / "terraform" / "variables.tf").read_text()

    for name in ("api_image", "site_image", "admin_image"):
        block = terraform_block(variables, f'variable "{name}"')
        assert "default" not in block
        assert "validation" in block
        assert SHA256_DIGEST_PATTERN.search(block)


def test_gcloud_revision_fields_are_not_reconciled_away_by_terraform() -> None:
    backend = (PROJECT_ROOT / "terraform" / "cloudrun.tf").read_text()
    frontend = (PROJECT_ROOT / "terraform" / "cloudrun_frontend.tf").read_text()

    # deploy.sh passes a complete env-vars file on every gcloud rollout. Terraform
    # must retain bootstrap env declarations without treating that runtime-owned
    # env list as drift and deleting production-only settings on a later apply.
    service_env_ignore = r"(?m)^\s+template\[0\]\.containers\[0\]\.env,$"
    job_env_ignore = r"(?m)^\s+template\[0\]\.template\[0\]\.containers\[0\]\.env,$"
    assert len(re.findall(service_env_ignore, backend)) == 3
    assert len(re.findall(job_env_ignore, backend)) == 1
    assert len(re.findall(service_env_ignore, frontend)) == 2
    assert backend.count("client_version,") == 4
    assert frontend.count("client_version,") == 2


def test_certificate_map_cutover_requires_legacy_domains_in_map_entries() -> None:
    certmanager = (PROJECT_ROOT / "terraform" / "certmanager.tf").read_text()
    loadbalancer = (PROJECT_ROOT / "terraform" / "loadbalancer.tf").read_text()

    assert re.search(
        r"legacy_customer_domain_set\s*=\s*toset\(var\.customer_domains\)", certmanager
    )
    assert re.search(
        r"certificate_map_customer_domain_set\s*=\s*toset\(var\.certificate_map_customer_domains\)",
        certmanager,
    )
    assert "certificate_map_missing_legacy_domains = setsubtract(" in certmanager
    assert (
        "!var.use_certificate_map || length(local.certificate_map_missing_legacy_domains) == 0"
        in loadbalancer
    )


def test_customer_domains_have_external_uptime_checks_and_default_alert_channel() -> (
    None
):
    monitoring = (PROJECT_ROOT / "terraform" / "monitoring.tf").read_text()

    assert 'google_monitoring_uptime_check_config" "customer_site"' in monitoring
    assert 'google_monitoring_uptime_check_config" "admin"' in monitoring
    assert 'path         = "/api/v1/health/ready"' in monitoring
    assert "local.certificate_map_customer_domain_set" in monitoring
    assert 'google_monitoring_alert_policy" "customer_site_uptime"' in monitoring
    assert 'google_monitoring_alert_policy" "site_5xx"' in monitoring
    assert 'google_monitoring_alert_policy" "operator_5xx"' in monitoring
    assert "google_monitoring_notification_channel.email[0].id" in monitoring
    assert (
        'for_each     = var.alert_email != "" ? '
        "local.certificate_map_customer_domain_set : toset([])"
        in monitoring
    )


def test_certificate_manager_runtime_scaffolding_is_enabled_for_api_and_worker() -> (
    None
):
    cloudrun = (PROJECT_ROOT / "terraform" / "cloudrun.tf").read_text()
    serviceaccount = (PROJECT_ROOT / "terraform" / "serviceaccount.tf").read_text()

    # API/worker뿐 아니라 같은 production Settings를 import하는 beat/migrate Job도
    # fail-fast 계약을 만족해야 한다.
    assert cloudrun.count('name  = "CERTIFICATE_MANAGER_AUTO_PROVISION"') == 4
    assert cloudrun.count('name  = "CERTIFICATE_MAP_NAME"') == 4
    assert "var.certificate_manager_role" in serviceaccount
