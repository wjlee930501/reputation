"""서버 소유 gap field 이름은 한 곳에서만 정의된다.

옛 자동 검수가 남긴 보류 초안을 읽는 쪽(essence_auto_review의 옛 초안 판정)과 모델이 같은 상수를
가리키는지 확인한다(H-03). Phase 4(D33)부터 판 PATCH 경로는 410이라 클라이언트가 이 값을 쓸 길이
없다.
"""

from app.models import essence as essence_model


def test_server_owned_gap_fields_keep_the_old_review_markers() -> None:
    assert set(essence_model.SERVER_OWNED_GAP_FIELDS) == {
        essence_model.AUTO_REVIEW_GAP_FIELD,
        essence_model.AUTO_RECOVERY_CYCLE_GAP_FIELD,
        essence_model.AUTO_RECOVERY_LAST_AT_GAP_FIELD,
        essence_model.EXCLUDED_ERROR_SOURCE_GAP_FIELD,
        essence_model.AUTO_RECOVERY_TERMINAL_GAP_FIELD,
    }
