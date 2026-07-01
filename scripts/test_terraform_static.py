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
