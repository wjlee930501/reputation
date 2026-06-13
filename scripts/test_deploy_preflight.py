from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.sh"


def test_all_target_checks_public_dns_before_backend_mutation() -> None:
    text = DEPLOY_SCRIPT.read_text()
    all_case_start = text.index("  all)")
    first_backend_mutation = text.index("IMAGE_URL=$(build_and_push)", all_case_start)
    dns_preflight = text.index("require_public_dns", all_case_start)

    assert dns_preflight < first_backend_mutation
