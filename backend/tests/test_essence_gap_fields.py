"""서버 소유 gap field 이름은 한 곳에서만 정의된다.

자동 검수(essence_auto_review)가 기록하는 field 이름과 Admin API가 PATCH로부터
보호하는 이름이 어긋나면 예외 승인 게이트가 조용히 뚫린다. 두 모듈이 같은 상수를
가리키는지 확인한다(H-03).
"""

from app.api.admin import essence as essence_api
from app.models import essence as essence_model
from app.services import essence_auto_review


def test_gap_field_constants_are_shared_objects():
    assert essence_api.AUTO_REVIEW_GAP_FIELD is essence_model.AUTO_REVIEW_GAP_FIELD
    assert essence_auto_review.AUTO_REVIEW_GAP_FIELD is essence_model.AUTO_REVIEW_GAP_FIELD
    assert (
        essence_auto_review.AUTO_RECOVERY_CYCLE_GAP_FIELD
        is essence_model.AUTO_RECOVERY_CYCLE_GAP_FIELD
    )
    assert essence_api.SERVER_OWNED_GAP_FIELDS is essence_model.SERVER_OWNED_GAP_FIELDS


def test_server_owned_gap_fields_cover_both_automatic_writers():
    assert set(essence_model.SERVER_OWNED_GAP_FIELDS) == {
        essence_auto_review.AUTO_REVIEW_GAP_FIELD,
        essence_auto_review.AUTO_RECOVERY_CYCLE_GAP_FIELD,
    }
