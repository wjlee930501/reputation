"""이미지 진단기의 순수 부분 — 마스킹·원문 보존·원인 선택 규칙을 고정한다.

공급자 호출과 GCS·Redis·DB 접근은 실제 자격 증명이 있어야 하므로 여기서 확인하지 않는다.
확인하는 것은 CEO가 실제로 읽는 세 가지다: 비밀값이 출력에 그대로 새지 않는 것, 공급자
오류 원문이 가공되지 않고 그대로 남는 것, 그리고 "most likely cause"가 **처음 실패한**
단계를 고르는 것(뒤 단계의 실패는 대개 앞 단계의 결과다).
"""

from app.utils.check_image_provider import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    StageResult,
    format_error,
    mask_secret,
    most_likely_cause,
    render,
    summarize_cost_guard,
)


def test_mask_secret_keeps_only_the_tail_and_marks_absence():
    assert mask_secret("sk-proj-abcdefgh") == "************efgh"
    assert mask_secret("") == "(unset)"
    assert mask_secret(None) == "(unset)"
    # 짧은 값의 꼬리를 보여주면 값 자체가 드러난다.
    assert mask_secret("abc") == "***"


def test_format_error_preserves_the_raw_provider_message_and_status():
    class _ApiError(Exception):
        status_code = 429

    detail = format_error(_ApiError("RESOURCE_EXHAUSTED: quota exceeded for imagegeneration"))

    assert "RESOURCE_EXHAUSTED: quota exceeded for imagegeneration" in detail
    assert "_ApiError" in detail
    assert "status_code=429" in detail


def test_most_likely_cause_picks_the_first_failing_stage():
    results = [
        StageResult("settings", "settings/secrets resolved", PASS),
        StageResult("model_access", "image model reachable", FAIL, "404 not found"),
        StageResult("image_generation", "one image generation", FAIL, "downstream"),
    ]

    cause = most_likely_cause(results)

    assert "image model reachable" in cause
    assert "404 not found" in cause
    # 뒤따르는 실패는 원인이 아니다.
    assert "downstream" not in cause


def test_most_likely_cause_falls_back_to_a_warning_then_to_skips():
    warned = most_likely_cause(
        [
            StageResult("settings", "settings", PASS),
            StageResult("cost_guard", "cost guard", WARN, "image 일일 상한 도달(250/250)"),
        ]
    )
    assert "일일 상한 도달" in warned

    skipped = most_likely_cause(
        [
            StageResult("settings", "settings", PASS),
            StageResult("image_generation", "one image generation", SKIP, "--skip-paid"),
        ]
    )
    assert "one image generation" in skipped

    clean = most_likely_cause([StageResult("settings", "settings", PASS)])
    assert "정상" in clean


def test_failing_stage_prints_its_remedy_but_a_passing_one_does_not():
    output = render(
        [
            StageResult("gcs_upload", "GCS write+delete round-trip", FAIL, "Forbidden"),
            StageResult("cost_guard", "cost guard", PASS, "", {"availability": "AVAILABLE"}),
        ]
    )

    assert "[1] FAIL GCS write+delete round-trip" in output
    assert "storage.objects.create" in output
    assert "availability: AVAILABLE" in output
    assert output.splitlines()[-1].startswith("most likely cause:")


def _snapshot(*, availability="AVAILABLE", kill_switch=False, image_used=3, image_limit=250):
    return {
        "availability": availability,
        "enabled": True,
        "kill_switch_active": kill_switch,
        "categories": [
            {
                "category": "image",
                "daily_used": image_used,
                "daily_limit": image_limit,
            },
            {"category": "content", "daily_used": 10, "daily_limit": 250},
            {"category": "sov", "daily_used": 999, "daily_limit": 10},
        ],
    }


def test_cost_guard_summary_names_an_exhausted_image_budget():
    result = summarize_cost_guard(_snapshot(image_used=250))

    assert result.status == WARN
    assert "image 일일 상한 도달(250/250)" in result.detail
    assert "COST_BLOCKED" in result.detail


def test_cost_guard_summary_ignores_categories_the_image_path_does_not_use():
    """sov 상한이 꽉 차도 이미지 실패의 원인이 아니다."""
    result = summarize_cost_guard(_snapshot())

    assert result.status == PASS
    assert result.detail == ""


def test_unreachable_redis_is_reported_as_unknown_not_as_healthy():
    """관측 실패를 0건/정상으로 위장하지 않는다 — 가드는 fail-open이다."""
    result = summarize_cost_guard(_snapshot(availability="UNAVAILABLE"))

    assert result.status == WARN
    assert "Redis에 닿지 못해" in result.detail
    assert "fail-open" in result.remedy


def test_kill_switch_is_named_before_any_counter():
    result = summarize_cost_guard(_snapshot(kill_switch=True))

    assert result.status == WARN
    assert "킬스위치" in result.detail
