from app.utils import ops_control_qa_seed


def test_manifest_has_deterministic_fixture_identity() -> None:
    assert ops_control_qa_seed.QA_ADMIN_EMAIL == "ops-qa-20260810@example.invalid"
    assert ops_control_qa_seed.QA_PREFIX == "OPS-QA-20260810"
