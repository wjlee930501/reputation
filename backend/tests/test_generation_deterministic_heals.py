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


@pytest.mark.parametrize("line", [f"## {AD_SENTENCE}", f"| 항목 | {AD_SENTENCE} |"])
def test_deletion_refuses_heading_and_table_lines(line):
    assert _heal(_forbidden_error(f"검사 전날 식단을 조절합니다.\n{line}")) is None


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

    healed, error = content_engine._apply_deterministic_heals(
        original,
        SimpleNamespace(name="테스트의원", director_name="김의사"),
        content_engine.ContentType.NOTICE,
        None,
        must_use_messages=[],
    )

    assert healed == {"ok": True}
    assert error is original
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

    healed, error = content_engine._apply_deterministic_heals(
        original,
        SimpleNamespace(name="테스트의원", director_name="김의사"),
        content_engine.ContentType.NOTICE,
        None,
        must_use_messages=[],
    )

    assert healed is None
    assert error is original


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
