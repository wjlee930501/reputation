"""E2(2026-10-13): 결정적으로 고칠 수 있는 거절은 작가를 다시 사지 않고 같은 회차에서 고친다.

- 본문 문장에만 든 금지 표현 → 그 문장을 통째로 지운다(고쳐 쓰지 않는다).
- 빠진 승인 필수 문구 → 본문 끝에 원문 그대로 덧붙인다.
- 빠진 병원명 → 승인된 병원명 한 줄을 덧붙인다(원장명 치유와 같은 방식).
- 검사 이름 뒤 낱말 '치료' 제목 → 그 낱말만 지운다.

제목·메타·FAQ의 금지 표현, 문장 하나로 짚을 수 없는 위반, 지울 문장이 너무 많은 응답은 종전대로
직전 사유를 넘겨 다시 쓰게 한다. 공급자 호출은 모두 가짜다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import content_engine
from app.services.question_wellformed import drop_treatment_after_exam
from tests.test_content_engine_must_use_verbatim import (
    MUST_USE_2CM,
    MUST_USE_SCREENING,
    _generate,
    _install_writer,
    _payload,
    _Writer,
)

AD_SENTENCE = "저희 병원은 최고의 의료진이 진료합니다."


async def test_a_forbidden_sentence_in_the_body_is_deleted_without_a_rewrite(monkeypatch):
    writer = _Writer([_payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE} 물을 충분히 드세요.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert AD_SENTENCE not in saved["body"]
    assert "검사 전날 식단을 조절합니다. 물을 충분히 드세요." in saved["body"]


async def test_a_forbidden_title_still_buys_a_complete_rewrite(monkeypatch):
    bad = _payload("검사 전날 식단을 조절합니다.")
    bad["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert saved["title"] == "대장내시경 검사 전 준비 안내"


async def test_too_many_forbidden_sentences_are_not_deleted(monkeypatch):
    promotional = " ".join(
        [AD_SENTENCE, "부작용 없는 검사입니다.", "100% 안전합니다.", "국내 최초 장비입니다."]
    )
    writer = _Writer([_payload(promotional), _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert content_engine.FORBIDDEN_SENTENCE_HEAL_LIMIT == 3
    assert AD_SENTENCE not in saved["body"]


async def test_a_missing_must_use_message_is_appended_verbatim(monkeypatch):
    writer = _Writer([_payload(MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith(MUST_USE_2CM)
    assert saved["body"].count(MUST_USE_SCREENING) == 1


async def test_a_missing_hospital_name_is_appended(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["body"] = payload["body"].replace("테스트의원 ", "")
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith("이 글은 테스트의원에서 안내합니다.")


def test_heal_registry_covers_every_deterministic_error():
    assert set(content_engine._DETERMINISTIC_HEALS) == {
        content_engine.DirectorNameMissingError,
        content_engine.HospitalNameMissingError,
        content_engine.ForbiddenExpressionError,
        content_engine.MissingMustUseMessagesError,
    }
    # 분류기는 종전 문구로 사유를 가른다 — 새 예외도 같은 메시지 머리를 쓴다.
    error = content_engine.ForbiddenExpressionError(
        "Forbidden medical expressions require complete regeneration: ['최고']",
        SimpleNamespace(),
        body_only=True,
    )
    assert isinstance(error, ValueError)


async def test_deletion_that_drops_the_body_below_the_floor_is_not_applied(monkeypatch):
    short = _payload("")
    short["body"] = "## 안내\n테스트의원 김의사 원장이 노원에서 안내합니다. " + AD_SENTENCE + "\n\n## 주의\n짧습니다."
    writer = _Writer([short, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2  # 하한 아래로 줄이는 삭제 대신 작가가 다시 썼다
    assert "Forbidden medical expressions" in writer.calls[1]["messages"][1]["content"]
    assert AD_SENTENCE not in saved["body"]


async def test_an_exam_title_with_a_standalone_treatment_word_is_fixed_before_review(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["title"] = "대장내시경 치료 전 준비 안내"
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["title"] == "대장내시경 전 준비 안내"


@pytest.mark.parametrize(
    ("title", "fixed"),
    [
        ("갑상선초음파 치료 비용, 얼마나 드나요?", "갑상선초음파 비용, 얼마나 드나요?"),
        ("대장내시경 치료 전 준비 안내", "대장내시경 전 준비 안내"),
        ("심장 MRI 치료 안내", "심장 MRI 안내"),
        # 조사·어미가 붙은 꼴은 결정적으로 고치지 않는다(작가 보완 재작성 몫).
        ("위내시경 치료가 필요한 경우", None),
        ("갑상선초음파 치료비 안내", None),
        # 시술은 치료와 자연스럽게 붙는다 — 고칠 것이 없다.
        ("임플란트 치료 비용 안내", None),
        ("허리 디스크 치료 방법", None),
        # 집속 초음파는 치료 시술이다 — 검사가 아니다.
        ("고강도 집속 초음파 치료 비용 안내", None),
        ("자궁근종 집속초음파 치료 안내", None),
        ("집속 초음파 치료와 갑상선초음파 치료 비용", "집속 초음파 치료와 갑상선초음파 비용"),
    ],
)
def test_drop_treatment_after_exam_only_touches_a_standalone_word(title, fixed):
    assert drop_treatment_after_exam(title) == fixed


def test_treatment_ultrasound_is_not_an_exam_with_treatment_wording():
    from app.services.question_wellformed import treatment_applied_to_exam

    assert treatment_applied_to_exam("고강도 집속 초음파 치료 비용") is False
    assert treatment_applied_to_exam("집속초음파 치료 안내") is False
    assert treatment_applied_to_exam("갑상선초음파 치료 비용") is True


def test_drop_treatment_after_exam_logs_the_rewrite(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="app.services.question_wellformed"):
        assert drop_treatment_after_exam("심장 MRI 치료 안내") == "심장 MRI 안내"
    assert any(
        "심장 MRI 치료 안내" in record.getMessage() and "심장 MRI 안내" in record.getMessage()
        for record in caplog.records
    )


# ── E5(2026-10-13): 결정적 거절의 사유를 센다 ───────────────────────────────────────


async def test_rejections_and_heals_are_counted_by_kind(monkeypatch):
    bad_title = _payload("검사 전날 식단을 조절합니다.")
    bad_title["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad_title, _payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert saved[content_engine.GENERATION_REJECTIONS_KEY] == {
        "rejected": {"forbidden": 2},
        "healed": {"forbidden": 1},
    }


@pytest.mark.parametrize(
    ("detail", "kind"),
    [
        ("Provider output was truncated before completion", "truncated"),
        ("Generated content contains an unverified fixed price or coverage claim: 3만원", "price"),
        ("GEO hard-fail: references is empty for FAQ", "references"),
        ("GEO hard-fail: 병원명 '테스트의원' body 미포함", "geo"),
        ("Forbidden medical expressions require complete regeneration: ['최고']", "forbidden"),
        ("FAQ output requires faq_question and faq_answer_summary", "faq"),
        ("Required must_use messages missing verbatim (1): ...", "must_use"),
        ("Exam title with treatment wording survived rewrite; regenerate the title", "exam_title"),
        ("something else", "other"),
    ],
)
def test_rejection_kind_matches_the_operator_message(detail, kind):
    from app.workers.generation_retry_policy import generation_rejection_kind
    from app.workers.generation_run_control import (
        classify_generation_failure,
        rejection_kind_for_message,
    )

    assert generation_rejection_kind(ValueError(detail)) == kind
    code, message = classify_generation_failure(ValueError(detail))
    assert code == "GENERATION_REJECTED"
    assert rejection_kind_for_message(message) == kind


def test_review_session_keeps_the_counts_in_the_saved_summary():
    from app.services.content_generation_review import (
        GENERATION_REJECTIONS_SUMMARY_KEY,
        _merge_rejection_counts,
    )

    total: dict = {}
    _merge_rejection_counts(total, {"rejected": {"forbidden": 1}, "healed": {"forbidden": 1}})
    _merge_rejection_counts(total, {"rejected": {"forbidden": 1, "price": 1}})
    _merge_rejection_counts(total, None)
    assert total == {"rejected": {"forbidden": 2, "price": 1}, "healed": {"forbidden": 1}}
    assert GENERATION_REJECTIONS_SUMMARY_KEY == "generation_rejections"


# ── 삭제 치유의 경계(2026-10-13 검토 반영) ─────────────────────────────────────────


def _forbidden_error(body_line: str) -> content_engine.ForbiddenExpressionError:
    payload = _payload(body_line)
    return content_engine.ForbiddenExpressionError(
        "Forbidden medical expressions require complete regeneration: ['최고']",
        payload,
        body_only=True,
    )


def _heal(error, must_use: list[str] | None = None):
    return content_engine._heal_forbidden_sentences(
        error, SimpleNamespace(name="테스트의원"), must_use_messages=list(must_use or [])
    )


def test_deletion_refuses_one_sentence_of_a_two_sentence_must_use_message():
    """두 문장짜리 필수 문구의 한 문장만 지워도 필수 문구가 깨진다 — 지우지 않는다."""

    message = f"정기 검진이 중요합니다. {AD_SENTENCE}"
    assert content_engine._heal_forbidden_sentences is not None
    assert _heal(_forbidden_error(f"검사 전날 식단을 조절합니다. {message}"), [message]) is None


def test_deletion_refuses_a_line_whose_sentence_carries_a_must_use_message():
    message = "최고의 의료진이 진료합니다"
    assert _heal(_forbidden_error(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}"), [message]) is None


@pytest.mark.parametrize(
    "line",
    [
        f"## {AD_SENTENCE}",
        f"| 항목 | {AD_SENTENCE} |",
        # 2026-10-13 교차 검토: Setext 제목, 앞 `|` 없는 GFM 표 행, 코드 펜스 안쪽.
        "최고의 의료진\n---",
        "최고의 의료진\n===",
        "진료 | 최고의 의료진",
        "```\n최고의 의료진\n```",
    ],
)
def test_deletion_refuses_heading_table_and_code_lines(line):
    assert _heal(_forbidden_error(f"검사 전날 식단을 조절합니다.\n\n{line}\n")) is None


def test_deletion_counts_sentences_without_a_space_after_the_period():
    """문장부호 뒤 공백이 없어도 문장을 끊어 센다 — 네 문장은 상한(3)을 넘는다."""

    assert _heal(_forbidden_error("최고입니다.최고입니다.최고입니다.최고입니다.")) is None


def test_deletion_keeps_the_remaining_sentences_as_original_substrings(caplog):
    import logging

    line = "검사 전날 식단을 조절합니다.최고의 의료진입니다.물을 1.5L 드세요!  다음 날 오세요."
    with caplog.at_level(logging.INFO, logger="app.services.content_engine"):
        healed = _heal(_forbidden_error(line))

    assert healed is not None
    assert "검사 전날 식단을 조절합니다.물을 1.5L 드세요!  다음 날 오세요." in healed["body"]
    assert "최고" not in healed["body"]
    assert any("Deleted 1 sentence" in record.getMessage() for record in caplog.records)


def test_deletion_keeps_the_list_marker_of_the_line():
    healed = _heal(_forbidden_error(f"\n- 검사 전날 식단을 조절합니다. {AD_SENTENCE}"))

    assert healed is not None
    assert "\n- 검사 전날 식단을 조절합니다.\n" in healed["body"]
    assert AD_SENTENCE not in healed["body"]


def _install_validator(monkeypatch, outcomes: list):
    """`_validate_generated_result`를 정해진 결과(예외 또는 dict) 순서로 바꾼다."""

    calls: list[dict] = []

    def fake_validate(result, *_args, **_kwargs):
        calls.append(dict(result))
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(content_engine, "_validate_generated_result", fake_validate)
    return calls


def test_heal_chain_deletion_then_hospital_name_is_validated_again(monkeypatch):
    original = _forbidden_error(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}")
    original.result["body"] = original.result["body"].replace("테스트의원 ", "")
    calls = _install_validator(
        monkeypatch,
        [
            content_engine.HospitalNameMissingError("GEO hard-fail: 병원명 누락", original.result),
            {"ok": True},
        ],
    )

    healed, error, events = content_engine._apply_deterministic_heals(
        original,
        SimpleNamespace(name="테스트의원", director_name="김의사"),
        content_engine.ContentType.NOTICE,
        None,
        must_use_messages=[],
    )

    assert healed == {"ok": True}
    assert error is original
    assert [(bucket, type(event)) for bucket, event in events] == [
        ("rejected", content_engine.HospitalNameMissingError),
        ("healed", content_engine.ForbiddenExpressionError),
        ("healed", content_engine.HospitalNameMissingError),
    ]
    assert len(calls) == 2
    assert AD_SENTENCE not in calls[1]["body"]
    assert calls[1]["body"].rstrip().endswith("이 글은 테스트의원에서 안내합니다.")


def test_heal_chain_failure_after_a_deletion_passes_the_original_rejection_on(monkeypatch):
    """지운 뒤의 거절은 우리가 만든 것이다 — 작가에게는 원래 거절(금지 표현)을 넘긴다."""

    original = _forbidden_error(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}")
    _install_validator(
        monkeypatch,
        [
            content_engine.HospitalNameMissingError("GEO hard-fail: 병원명 누락", original.result),
            ValueError("Generated content contains an unverified fixed price or coverage claim"),
        ],
    )

    healed, error, events = content_engine._apply_deterministic_heals(
        original,
        SimpleNamespace(name="테스트의원", director_name="김의사"),
        content_engine.ContentType.NOTICE,
        None,
        must_use_messages=[],
    )

    assert healed is None
    assert error is original
    # 사슬이 실패하면 저장되는 치유가 없다 — 거절만 센다.
    assert [bucket for bucket, _event in events] == ["rejected"]


async def test_a_heal_chain_counts_every_rejection_and_heal(monkeypatch):
    """금지 표현 문장 삭제와 필수 문구 덧붙이기가 같은 회차에 일어나면 둘 다 센다."""

    writer = _Writer([_payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}", MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == 1
    assert AD_SENTENCE not in saved["body"]
    assert saved["body"].rstrip().endswith(MUST_USE_2CM)
    assert saved[content_engine.GENERATION_REJECTIONS_KEY] == {
        "rejected": {"forbidden": 1, "must_use": 1},
        "healed": {"forbidden": 1, "must_use": 1},
    }


async def test_a_final_generation_failure_keeps_the_cumulative_counts(monkeypatch):
    bad_title = _payload("검사 전날 식단을 조절합니다.")
    bad_title["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad_title])
    _install_writer(monkeypatch, writer)

    with pytest.raises(content_engine.ForbiddenExpressionError) as raised:
        await _generate([])

    rounds = len(writer.calls)
    assert rounds == content_engine.GENERATION_REMEDIATION_ROUNDS
    assert getattr(raised.value, content_engine.GENERATION_REJECTIONS_KEY) == {
        "rejected": {"forbidden": rounds}
    }


def test_the_review_session_merges_counts_from_a_failed_round():
    from app.services.content_generation_review import (
        _attach_rejection_counts,
        _merge_rejection_counts,
    )

    error = ValueError("Forbidden medical expressions require complete regeneration: ['최고']")
    setattr(error, content_engine.GENERATION_REJECTIONS_KEY, {"rejected": {"forbidden": 3}})
    total: dict = {"rejected": {"price": 1}}
    _merge_rejection_counts(total, getattr(error, content_engine.GENERATION_REJECTIONS_KEY))
    _attach_rejection_counts(error, total)

    assert getattr(error, content_engine.GENERATION_REJECTIONS_KEY) == {
        "rejected": {"price": 1, "forbidden": 3}
    }


def test_hard_removal_findings_leave_out_uncertain_findings_without_a_quote():
    from app.services import content_ai_review
    from app.services.content_review_feedback import hard_removal_findings

    def finding(severity: str, message: str, quote: str = ""):
        return content_ai_review.ContentAiFinding(
            severity=content_ai_review.ContentAiFindingSeverity(severity),
            kind=content_ai_review.ContentAiFindingKind("MEDICAL_SAFETY"),
            message=message,
            quote=quote,
        )

    hard = finding("HARD", "응급 안내 없이 출혈을 다뤘습니다.", "피가 나면 지켜보세요.")
    unquoted = finding("UNCERTAIN", "확신이 충분하지 않습니다.")

    removal = hard_removal_findings(SimpleNamespace(blocking_findings=(hard, unquoted)))

    assert removal[1:] == [hard.message]


def test_missing_must_use_heal_does_not_append_a_message_already_in_the_body():
    payload = _payload(MUST_USE_SCREENING)
    error = content_engine.MissingMustUseMessagesError(
        "Required must_use messages missing verbatim (2): ...",
        payload,
        (MUST_USE_2CM, MUST_USE_SCREENING),
    )

    healed = content_engine._heal_missing_must_use(
        error, SimpleNamespace(), must_use_messages=[MUST_USE_2CM, MUST_USE_SCREENING]
    )

    assert healed is not None
    assert healed["body"].count(MUST_USE_SCREENING) == 1
    assert healed["body"].rstrip().endswith(MUST_USE_2CM)

    only_present = content_engine.MissingMustUseMessagesError(
        "Required must_use messages missing verbatim (1): ...",
        _payload(MUST_USE_SCREENING),
        (MUST_USE_SCREENING,),
    )
    assert (
        content_engine._heal_missing_must_use(
            only_present, SimpleNamespace(), must_use_messages=[MUST_USE_SCREENING]
        )
        is None
    )


# ── 삭제 치유의 경계 — 2회차 교차 검토 재현(2026-10-13) ───────────────────────────────


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("````\n```\n최고의 의료진입니다.\n```\n````", id="four-backtick-fence-holds-three"),
        pytest.param("```\n~~~\n최고의 의료진입니다.\n~~~\n```", id="tilde-inside-backtick-fence"),
        pytest.param("```\n```python\n최고의 의료진입니다.\n```", id="info-string-is-not-a-closer"),
        pytest.param("~~~\n```\n최고의 의료진입니다.\n```\n~~~", id="backtick-inside-tilde-fence"),
        pytest.param("> ```\n> 최고의 의료진입니다.\n> ```", id="fence-in-blockquote"),
        pytest.param("- ```\n  최고의 의료진입니다.\n  ```", id="fence-in-list-item"),
        pytest.param("> 최고입니다.\n> ---", id="setext-in-blockquote"),
        pytest.param("- 최고의 의료진\n  ===", id="setext-in-list-item"),
        pytest.param("> ## 최고의 의료진", id="atx-in-blockquote"),
    ],
)
def test_deletion_refuses_fences_and_headings_inside_containers(block):
    assert _heal(_forbidden_error(f"검사 전날 식단을 조절합니다.\n\n{block}\n")) is None


def test_deletion_counts_sentences_that_start_with_a_digit():
    """마침표는 양쪽이 숫자일 때만 소수점이다 — 네 문장은 상한(3)을 넘는다."""

    assert _heal(_forbidden_error("최고입니다.1등입니다.1등입니다.1등입니다.")) is None


def test_deletion_keeps_a_sentence_that_starts_with_a_digit():
    healed = _heal(_forbidden_error("최고입니다.1일 쉬세요. 정상입니다."))

    assert healed is not None
    assert "\n1일 쉬세요. 정상입니다.\n" in healed["body"]
    assert "최고" not in healed["body"]


def test_deletion_takes_the_closing_quote_with_the_sentence():
    healed = _heal(_forbidden_error("안내입니다. “최고입니다.” 다음은 정상입니다."))

    assert healed is not None
    assert "\n안내입니다. 다음은 정상입니다.\n" in healed["body"]
    assert "”" not in healed["body"]


@pytest.mark.parametrize(
    "line",
    [
        "정상입니다. `최고입니다.` 다음입니다.",
        "`정상. 최고입니다.` 다음입니다.",
    ],
)
def test_deletion_refuses_a_line_with_inline_code(line):
    assert _heal(_forbidden_error(line)) is None


def test_deletion_keeps_the_hard_line_break_of_the_remaining_text():
    healed = _heal(_forbidden_error("최고입니다. 정상입니다.  \n다음 줄입니다."))
    assert healed is not None
    assert "\n정상입니다.  \n다음 줄입니다.\n" in healed["body"]

    last_dropped = _heal(_forbidden_error("정상입니다. 최고입니다.  \n다음 줄입니다."))
    assert last_dropped is not None
    assert "\n정상입니다.  \n다음 줄입니다.\n" in last_dropped["body"]


def test_heal_protected_lines_tracks_fence_char_length_and_container():
    lines = ["````", "```", "본문", "```", "````", "다음 문단", "> ~~~", "> 인용 안 코드", "> ~~~", "밖"]
    assert content_engine._heal_protected_lines(lines) == {0, 1, 2, 3, 4, 6, 7, 8}


# ── 치유·예외 계수 — 2회차 교차 검토(2026-10-13) ────────────────────────────────────


async def test_a_provider_error_after_a_rejection_keeps_the_cumulative_counts(monkeypatch):
    bad_title = _payload("검사 전날 식단을 조절합니다.")
    bad_title["title"] = "최고의 대장내시경 준비 안내"
    calls: list[int] = []
    original_attempt = content_engine._generate_content_attempt

    async def flaky_attempt(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise TimeoutError("provider timed out")
        return await original_attempt(*args, **kwargs)

    writer = _Writer([bad_title])
    _install_writer(monkeypatch, writer)
    monkeypatch.setattr(content_engine, "_generate_content_attempt", flaky_attempt)

    with pytest.raises(TimeoutError) as raised:
        await _generate([])

    assert getattr(raised.value, content_engine.GENERATION_REJECTIONS_KEY) == {
        "rejected": {"forbidden": 1}
    }


async def test_the_review_session_attaches_counts_when_the_provider_fails_later():
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from app.models.content import ContentItem, ContentType
    from app.models.essence import HospitalContentPhilosophy
    from app.models.hospital import Hospital
    from app.services.content_generation_review import (
        ContentReviewDependencies,
        GenerationReviewLimits,
        generate_reviewed_content,
    )
    from app.services.cost_guard import CostGuardDecision

    rejected = ValueError("Forbidden medical expressions require complete regeneration: ['최고']")
    setattr(rejected, content_engine.GENERATION_REJECTIONS_KEY, {"rejected": {"forbidden": 2}})
    timeout = TimeoutError("provider timed out")
    setattr(timeout, content_engine.GENERATION_REJECTIONS_KEY, {"rejected": {"price": 1}})
    hospital = Hospital(id=uuid4(), name="격리 검증 의원")
    effects = ContentReviewDependencies(
        generate=AsyncMock(side_effect=[rejected, timeout]),
        review=AsyncMock(),
        screen=Mock(),
        check_cost=AsyncMock(return_value=CostGuardDecision(True)),
    )

    with pytest.raises(TimeoutError) as raised:
        await generate_reviewed_content(
            hospital=hospital,
            item=ContentItem(hospital_id=hospital.id, content_type=ContentType.HEALTH),
            existing_titles=[],
            philosophy=HospitalContentPhilosophy(hospital_id=hospital.id),
            approved_brief=None,
            dependencies=effects,
            limits=GenerationReviewLimits(3, 2, 1, 1),
        )

    assert getattr(raised.value, content_engine.GENERATION_REJECTIONS_KEY) == {
        "rejected": {"forbidden": 2, "price": 1}
    }


# ── 3회차 교차 검토(2026-10-13): 여러 줄 인라인 코드 스팬 ─────────────────────────────


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("정상 `코드\n최고입니다.\n끝` 정상입니다.", id="single-backtick-span"),
        pytest.param("정상 ``코드 ` 안\n최고입니다.\n끝`` 정상입니다.", id="double-backtick-span"),
    ],
)
def test_deletion_refuses_a_violation_inside_a_multi_line_code_span(block):
    assert _heal(_forbidden_error(block)) is None


def test_an_unpaired_backtick_does_not_protect_the_following_lines():
    healed = _heal(_forbidden_error("정상 `리터럴\n\n최고입니다. 정상입니다."))

    assert healed is not None
    assert "\n정상입니다.\n" in healed["body"]


def test_code_span_lines_ignore_fenced_backticks():
    lines = ["```", "코드", "```", "정상 `열기", "최고입니다.", "닫기` 끝"]
    protected = content_engine._heal_protected_lines(lines)
    assert content_engine._heal_code_span_lines(lines, protected) == {3, 4, 5}
