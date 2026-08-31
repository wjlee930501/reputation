from app.services.lead_diagnosis_engine import _Measurement, is_confirmed


def _measurement(*, is_mentioned: bool | None) -> _Measurement:
    return _Measurement(
        platform="chatgpt",
        query_slot=1,
        query_text="강남 외과 추천",
        repeat_no=1,
        requested_model="model",
        is_mentioned=is_mentioned,
        mention_verdict=None,
        measurement_status="SUCCESS",
    )


def test_confirmation_excludes_missing_boolean_and_keeps_legacy_boolean() -> None:
    assert is_confirmed(_measurement(is_mentioned=None)) is False
    assert is_confirmed(_measurement(is_mentioned=False)) is True
