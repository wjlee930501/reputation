"""운영 점검용 상담 요청이 목록에서 실제 고객과 구분되는지 검증한다."""

from types import SimpleNamespace

from app.api.admin.leads import _serialize_lead
from app.services.lead_triage import is_operations_test_lead, operations_test_lead_clause


def _lead(**overrides):
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        clinic_name="장편한외과의원",
        clinic_type="외과",
        contact="010-0000-0000",
        question="상담 신청",
        privacy=True,
        source_path="/",
        source="INQUIRY",
        consent_version=None,
        status="NEW",
        converted_hospital_id=None,
        converted_at=None,
        conversion_note=None,
        notification_status=None,
        notification_error=None,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_operations_test_lead_needs_all_three_fixture_markers():
    fixture = _lead(
        source_path="/ops-qa",
        consent_version="ops-qa-v1",
        conversion_note="[OPS-QA-20260810]",
    )
    assert is_operations_test_lead(fixture) is True

    # 정리 스크립트가 삭제 대상으로 인정하지 않는 조합은 점검용이라 부르지 않는다.
    assert is_operations_test_lead(_lead(source_path="/ops-qa")) is False
    assert (
        is_operations_test_lead(_lead(source_path="/ops-qa", consent_version="ops-qa-v1"))
        is False
    )
    assert (
        is_operations_test_lead(
            _lead(consent_version="ops-qa-v1", conversion_note="[OPS-QA-20260810]")
        )
        is False
    )


def test_a_real_lead_with_the_same_clinic_name_is_not_a_test_lead():
    # 점검 픽스처가 실제 고객 이름을 쓰므로, 이름으로 판별하면 실고객이 점검용이 된다.
    assert is_operations_test_lead(_lead(clinic_name="장편한외과의원")) is False


def test_the_list_payload_carries_the_test_marker_and_the_funnel_source():
    payload = _serialize_lead(
        _lead(
            source_path="/ops-qa",
            consent_version="ops-qa-v1",
            conversion_note="[OPS-QA-20260810]",
            source="AI_DIAGNOSIS",
        )
    )

    assert payload["is_operations_test"] is True
    assert payload["source"] == "AI_DIAGNOSIS"


def test_a_normal_lead_payload_reports_false_rather_than_omitting_the_field():
    payload = _serialize_lead(_lead())

    # 필드를 빼면 화면이 "모르면 실고객"으로 다뤄야 할지 판단할 근거가 없다.
    assert payload["is_operations_test"] is False
    assert payload["source"] == "INQUIRY"


def test_operations_test_sql_clause_uses_the_same_three_markers():
    sql = str(operations_test_lead_clause().compile(compile_kwargs={"literal_binds": True}))

    assert "sales_leads.source_path" in sql and "'/ops-qa'" in sql
    assert "sales_leads.consent_version" in sql and "'ops-qa-v1'" in sql
    assert "sales_leads.conversion_note" in sql and "'[OPS-QA-'" in sql
    assert sql.count("coalesce") == 3
