"""현황 헤더의 남은 조건 문구 — 공개 판정 막힘 코드가 영문으로 새거나 500이 나지 않는다."""

import re
import uuid

from app.api.admin.hospital_overview import _remaining


def test_publication_blocker_codes_have_korean_labels() -> None:
    hospital_id = uuid.uuid4()
    keys = (
        "service_inactive",
        "public_permission_missing",
        "site_not_built",
        "name",
        "slug",
        "address",
        "phone",
        "treatments",
    )
    conditions = _remaining(hospital_id, keys)
    assert [c.key for c in conditions] == list(keys)
    for condition in conditions:
        assert not re.search(r"[A-Za-z_]", condition.label), condition.label
    by_key = {c.key: c for c in conditions}
    assert by_key["name"].actor == "human"
    assert by_key["slug"].label == "기본 주소 이름 입력"
    assert by_key["site_not_built"].actor == "system"


def test_unknown_condition_is_a_human_check_not_a_crash() -> None:
    [condition] = _remaining(uuid.uuid4(), ("brand_new_blocker",))
    assert condition.label == "확인 필요 항목"
    assert condition.actor == "human"
    assert condition.href is None
