from app.api.admin.hospitals import (
    ReadinessCheck,
    _readiness_status_label,
    _serialize_readiness_check,
)


def test_readiness_status_labels_are_operator_friendly():
    assert _readiness_status_label("READY") == "운영 준비 완료"
    assert _readiness_status_label("NEEDS_WORK") == "보완 필요"
    assert _readiness_status_label("UNKNOWN") == "UNKNOWN"


def test_serialize_readiness_check_exposes_display_state_label():
    passed = ReadinessCheck(
        key="published_content",
        label="발행 콘텐츠",
        passed=True,
        weight=12,
        next_action="초안 콘텐츠를 검수하고 최소 1편 이상 발행하세요.",
    )
    pending = ReadinessCheck(
        key="sov_data",
        label="AI 답변 언급률 측정 데이터",
        passed=False,
        weight=8,
        next_action="환자 질문 측정을 실행하세요.",
    )

    assert _serialize_readiness_check(passed)["display"] == {"state_label": "완료"}
    assert _serialize_readiness_check(pending)["display"] == {"state_label": "필요"}
