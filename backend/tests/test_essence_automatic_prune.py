"""자동 요약 정리(Phase 4 D33·D34·E5·E8, 대표 답변 Q2)와 병원 정보(자동) 자료(PROFILE_FACTS).

정리는 보류하지 않고 버린다. 정리 뒤 근거 검사·오류 잔재·금지 표현·운영 사실이 남지 않는 것이
불변식이고, 필수 문구는 설문 원문 그대로인 원장 문장(최대 3개)이 먼저다.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.essence import EvidenceNoteType, SourceType
from app.services import essence_engine
from app.services.essence_engine import (
    MANDATORY_AVOID_MESSAGES,
    MUST_USE_EVIDENCE_KEY,
    MUST_USE_MODE_NONE,
    MUST_USE_MODE_SURVEY,
    MUST_USE_MODE_SYNTHESIZED,
    apply_mandatory_safety_policy,
    contains_operational_fact,
    find_error_marker_fields,
    order_notes_for_synthesis,
    process_source_asset,
    prune_automatic_candidate,
    validate_philosophy_grounding,
)
from app.services.profile_facts_source import (
    PROFILE_FACTS_SYSTEM_KIND,
    profile_fact_note_payloads,
    render_profile_fact_lines,
    render_profile_facts_text,
)
from app.utils.medical_filter import check_forbidden

SURVEY_SENTENCE = "저는 환자분이 이해하실 때까지 몇 번이고 설명드립니다."


def _source(source_type, raw_text="", *, profile_facts=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_type=SourceType.INTERNAL_NOTE if profile_facts else source_type,
        source_metadata={"system_kind": PROFILE_FACTS_SYSTEM_KIND} if profile_facts else {},
        raw_text=raw_text,
        operator_note=None,
        content_hash="hash",
        status="PROCESSED",
        processed_at=None,
    )


def _note(source, excerpt, note_type=EvidenceNoteType.KEY_MESSAGE, **metadata):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_asset_id=source.id,
        note_type=note_type,
        source_excerpt=excerpt,
        claim=excerpt,
        note_metadata=metadata,
    )


def _payload(**fields):
    base = {
        "positioning_statement": None,
        "doctor_voice": None,
        "patient_promise": None,
        "content_principles": [],
        "tone_guidelines": [],
        "must_use_messages": [],
        "avoid_messages": [],
        "treatment_narratives": [],
        "local_context": {"region_terms": [], "local_patient_context": []},
        "medical_ad_risk_rules": [],
        "evidence_map": {},
        "source_asset_ids": [],
        "unsupported_gaps": [],
        "conflict_notes": [],
        "synthesis_notes": "test",
        "source_snapshot_hash": "snapshot",
    }
    base.update(fields)
    return apply_mandatory_safety_policy(base)


def _positive_text(payload) -> str:
    parts = []
    for field in (
        "positioning_statement",
        "doctor_voice",
        "patient_promise",
        "content_principles",
        "tone_guidelines",
        "must_use_messages",
        "treatment_narratives",
        "local_context",
    ):
        parts.extend(essence_engine._iter_field_texts(payload.get(field)))
    return " ".join(parts)


def _assert_invariants(payload, notes) -> None:
    assert validate_philosophy_grounding(payload, notes) == []
    assert find_error_marker_fields(payload) == []
    assert check_forbidden(_positive_text(payload)) == []
    assert not contains_operational_fact(_positive_text(payload))
    assert MUST_USE_EVIDENCE_KEY not in payload
    assert payload["conflict_notes"] == []


def test_ungrounded_forbidden_residue_and_operational_facts_are_dropped_not_escalated() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    good = _note(homepage, "진료 전에 충분히 설명합니다.")
    unknown = str(uuid.uuid4())
    payload = _payload(
        positioning_statement="충분한 설명을 중시하는 동네 의원",
        doctor_voice="최고의 실력으로 완치를 보장하는 원장",
        patient_promise="평일 09:00~18:00 진료, 02-123-4567로 문의",
        content_principles=["근거 안에서 설명합니다.", "Title: 403 Forbidden 접근 거부"],
        tone_guidelines=["차분한 설명형"],
        treatment_narratives=[
            {"treatment": "도수치료", "patient_language": ["목이 결려요"], "evidence_note_ids": [str(good.id)]},
            {"treatment": "체외충격파", "patient_language": [], "evidence_note_ids": [unknown]},
        ],
        local_context={"region_terms": ["강남"], "local_patient_context": [], "evidence_note_ids": [unknown]},
        evidence_map={
            "positioning_statement": [str(good.id)],
            "doctor_voice": [str(good.id)],
            "patient_promise": [str(good.id)],
            "content_principles": [str(good.id)],
            "tone_guidelines": [unknown],
            "unknown_field": [unknown],
        },
    )

    pruned, report = prune_automatic_candidate(payload, [good], [homepage])

    _assert_invariants(pruned, [good])
    assert pruned["positioning_statement"] == "충분한 설명을 중시하는 동네 의원"
    assert pruned["doctor_voice"] is None
    assert pruned["patient_promise"] is None
    assert pruned["content_principles"] == ["근거 안에서 설명합니다."]
    assert pruned["tone_guidelines"] == []
    assert [item["treatment"] for item in pruned["treatment_narratives"]] == ["도수치료"]
    assert pruned["local_context"]["region_terms"] == []
    reasons = {(item["field"], item["reason"]) for item in report.dropped}
    assert ("doctor_voice", "FORBIDDEN_EXPRESSION") in reasons
    assert ("patient_promise", "OPERATIONAL_FACT") in reasons
    assert ("content_principles", "ERROR_PAGE_RESIDUE") in reasons
    assert ("tone_guidelines", "UNGROUNDED") in reasons
    assert ("treatment_narratives", "UNGROUNDED") in reasons
    assert ("local_context", "UNGROUNDED") in reasons
    # 플랫폼 공통 안전 규칙은 근거 없이도 남는다.
    assert set(MANDATORY_AVOID_MESSAGES) <= set(pruned["avoid_messages"])


def test_must_use_prefers_verbatim_survey_sentences_up_to_three() -> None:
    survey = _source(
        SourceType.INTERVIEW,
        f"Q. 진료 철학은?\nA. {SURVEY_SENTENCE} 검사 결과는 화면으로 함께 봅니다.",
    )
    homepage = _source(SourceType.HOMEPAGE, "홈페이지 소개")
    survey_note = _note(survey, SURVEY_SENTENCE, EvidenceNoteType.DOCTOR_PHILOSOPHY)
    second = _note(survey, "검사 결과는 화면으로 함께 봅니다.", EvidenceNoteType.TONE_SIGNAL)
    home_note = _note(homepage, "홈페이지 소개")
    paraphrase = "원장은 충분히 설명합니다"
    payload = _payload(
        must_use_messages=[
            f"“{SURVEY_SENTENCE}”",
            paraphrase,
            "검사 결과는 화면으로 함께 봅니다.",
            "완치를 보장합니다.",
        ],
        evidence_map={"must_use_messages": [str(home_note.id)]},
        **{
            MUST_USE_EVIDENCE_KEY: [
                {"text": f"“{SURVEY_SENTENCE}”", "evidence_note_ids": [str(survey_note.id)]},
                {"text": paraphrase, "evidence_note_ids": [str(home_note.id)]},
            ]
        },
    )

    pruned, report = prune_automatic_candidate(
        payload, [survey_note, second, home_note], [survey, homepage]
    )

    _assert_invariants(pruned, [survey_note, second, home_note])
    assert pruned["must_use_messages"] == [SURVEY_SENTENCE, "검사 결과는 화면으로 함께 봅니다."]
    assert report.must_use["mode"] == MUST_USE_MODE_SURVEY
    assert report.must_use["items"][0]["evidence_note_ids"] == [str(survey_note.id)]
    assert set(pruned["evidence_map"]["must_use_messages"]) == {str(survey_note.id), str(second.id)}
    dropped = {(item["text"], item["reason"]) for item in report.dropped}
    assert (paraphrase, "MUST_USE_NOT_SURVEY_VERBATIM") in dropped
    assert ("완치를 보장합니다.", "FORBIDDEN_EXPRESSION") in dropped


def test_must_use_is_capped_at_three_survey_sentences() -> None:
    sentences = [f"{index}번째로 저는 환자분과 함께 결정합니다." for index in range(1, 6)]
    survey = _source(SourceType.INTERVIEW, " ".join(sentences))
    note = _note(survey, " ".join(sentences), EvidenceNoteType.DOCTOR_PHILOSOPHY)

    pruned, report = prune_automatic_candidate(
        _payload(must_use_messages=sentences, evidence_map={"must_use_messages": [str(note.id)]}),
        [note],
        [survey],
    )

    assert pruned["must_use_messages"] == sentences[:3]
    assert sum(item["reason"] == "MUST_USE_LIMIT" for item in report.dropped) == 2


def test_survey_philosophy_note_is_used_verbatim_when_the_model_paraphrased() -> None:
    survey = _source(SourceType.INTERVIEW, SURVEY_SENTENCE)
    note = _note(survey, SURVEY_SENTENCE, EvidenceNoteType.DOCTOR_PHILOSOPHY)

    pruned, report = prune_automatic_candidate(
        _payload(
            must_use_messages=["설명을 중시합니다"],
            evidence_map={"must_use_messages": [str(note.id)]},
        ),
        [note],
        [survey],
    )

    assert pruned["must_use_messages"] == [SURVEY_SENTENCE]
    assert report.must_use["mode"] == MUST_USE_MODE_SURVEY


def test_without_a_survey_grounded_synthesized_must_use_is_allowed_with_note_ids() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    note = _note(homepage, "검사 전에 충분히 설명합니다.")
    messages = ["검사 전에 충분히 설명드립니다.", "치료 계획은 함께 정합니다.", "무리한 권유를 하지 않습니다.", "네 번째 문장입니다."]
    payload = _payload(
        must_use_messages=[*messages, "[마케터 입력] 원장 약력: 서울대", "원장: 홍길동 원장"],
        evidence_map={"must_use_messages": [str(note.id)]},
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    _assert_invariants(pruned, [note])
    assert pruned["must_use_messages"] == messages[:3]
    assert report.must_use["mode"] == MUST_USE_MODE_SYNTHESIZED
    assert all(item["evidence_note_ids"] == [str(note.id)] for item in report.must_use["items"])
    reasons = [item["reason"] for item in report.dropped]
    assert "MUST_USE_NOT_SENTENCE" in reasons and "MUST_USE_LIMIT" in reasons


def test_ungrounded_synthesized_must_use_is_dropped() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    note = _note(homepage, "자료")
    pruned, report = prune_automatic_candidate(
        _payload(must_use_messages=["근거 없는 문구입니다."]), [note], [homepage]
    )
    assert pruned["must_use_messages"] == []
    assert report.must_use["mode"] == MUST_USE_MODE_NONE
    assert "must_use_messages" not in pruned["evidence_map"]


def test_conflicts_are_resolved_silently_in_metadata_by_narrative_priority() -> None:
    survey = _source(SourceType.INTERVIEW)
    blog = _source(SourceType.NAVER_BLOG)
    survey_note = _note(survey, "설문")
    blog_note = _note(blog, "블로그")
    payload = _payload(
        conflict_notes=[
            {"text": "설문과 블로그의 치료 방침이 다름", "evidence_note_ids": [str(blog_note.id), str(survey_note.id)]}
        ]
    )

    pruned, report = prune_automatic_candidate(payload, [survey_note, blog_note], [survey, blog])

    assert pruned["conflict_notes"] == []
    assert report.conflicts == [
        {
            "text": "설문과 블로그의 치료 방침이 다름",
            "evidence_note_ids": [str(blog_note.id), str(survey_note.id)],
            "source_kinds": ["INTERVIEW", "NAVER_BLOG"],
            "preferred_source": "INTERVIEW",
            "resolved_by": "D34_NARRATIVE_PRIORITY",
        }
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("평일 09:00 진료", True),
        ("오후 2시부터 상담", True),
        ("대표번호 02-1234-5678", True),
        ("1588-1234로 예약", True),
        ("토요일은 휴진합니다", True),
        ("진료시간 안내", True),
        ("3개월 단위로 경과를 봅니다", False),
        ("1:1 상담으로 설명합니다", False),
    ],
)
def test_operational_fact_detection(text: str, expected: bool) -> None:
    assert contains_operational_fact(text) is expected


# ── 합성 프롬프트(P4-12a) ─────────────────────────────────────────────────


def test_synthesis_prompt_tags_source_kinds_and_puts_the_survey_first(monkeypatch) -> None:
    survey = _source(SourceType.INTERVIEW, "가" * 500)
    homepage = _source(SourceType.HOMEPAGE, "나" * 500)
    profile = _source(SourceType.INTERNAL_NOTE, "", profile_facts=True)
    home_note = _note(homepage, "나" * 300)
    survey_note = _note(survey, "가" * 450, EvidenceNoteType.DOCTOR_PHILOSOPHY)
    marketer = _note(profile, "[마케터 입력] 진료 철학: 설명", tag="마케터 입력", system_kind=PROFILE_FACTS_SYSTEM_KIND)
    naver = _note(profile, "[자동 수집·네이버] 지역: 강남", tag="자동 수집·네이버", system_kind=PROFILE_FACTS_SYSTEM_KIND)
    captured: dict[str, str] = {}

    def fake_call(system, user, **_kwargs):
        captured["system"], captured["user"] = system, user
        return {
            "entries": [
                {
                    "kind": "must_use_message",
                    "text": "가가가",
                    "detail": "",
                    "patient_language": [],
                    "cautions": [],
                    "evidence_note_ids": [str(survey_note.id)],
                }
            ]
        }

    monkeypatch.setattr(essence_engine, "_call_llm_json", fake_call)
    payload = essence_engine._synthesize_philosophy_llm(
        SimpleNamespace(name="테스트 병원"),
        [homepage, survey, profile],
        [home_note, naver, survey_note, marketer],
        None,
    )

    lines = [line for line in captured["user"].splitlines() if line.startswith("- id=")]
    assert [line.split("src=")[1].split(" ")[0] for line in lines] == [
        "INTERVIEW",
        "PROFILE_FACTS",
        "PROFILE_FACTS",
        "HOMEPAGE",
    ]
    assert str(marketer.id) in lines[1]  # 마케터 입력 줄이 자동 수집 줄보다 먼저다.
    assert "가" * 399 in lines[0] and "나" * 200 not in lines[3]
    assert "src=INTERVIEW 근거를\n   최우선" in captured["system"]
    assert "운영 사실은 어떤 필드에도 쓰지 않습니다" in captured["system"]
    assert "must_use_message는 최대 3개" in captured["system"]
    assert payload[MUST_USE_EVIDENCE_KEY] == [
        {"text": "가가가", "evidence_note_ids": [str(survey_note.id)]}
    ]


def test_note_ordering_follows_the_d34_narrative_priority() -> None:
    other = _source(SourceType.BROCHURE)
    blog = _source(SourceType.NAVER_BLOG)
    survey = _source(SourceType.INTERVIEW)
    notes = [_note(other, "a"), _note(blog, "b"), _note(survey, "c")]
    ordered = order_notes_for_synthesis(notes, [other, blog, survey])
    assert [note.source_excerpt for note in ordered] == ["c", "b", "a"]


def test_public_synthesize_philosophy_never_leaks_private_keys() -> None:
    homepage = _source(SourceType.HOMEPAGE, "진료 전에 충분히 설명합니다.")
    note = _note(homepage, "진료 전에 충분히 설명합니다.", EvidenceNoteType.DOCTOR_PHILOSOPHY)
    payload = essence_engine.synthesize_philosophy(
        SimpleNamespace(name="병원"), [homepage], [note], use_llm=False
    )
    assert not [key for key in payload if key.startswith("_")]
    with_mode, mode = essence_engine.synthesize_philosophy_with_mode(
        SimpleNamespace(name="병원"), [homepage], [note], use_llm=False
    )
    assert mode == essence_engine.SYNTHESIS_MODE_DETERMINISTIC
    assert with_mode[MUST_USE_EVIDENCE_KEY]


# ── 병원 정보(자동) 자료(P4-11) ─────────────────────────────────────────────


def _hospital(**fields):
    base = {
        "director_name": "홍길동",
        "director_career": "서울대학교 의과대학 졸업\n정형외과 전문의",
        "director_philosophy": "환자분이 이해하실 때까지 설명합니다.",
        "specialties": ["정형외과"],
        "treatments": [{"name": "도수치료", "description": "손으로 근육을 풉니다"}, {"name": "주사치료"}],
        "region": ["서울 강남구"],
        "keywords": ["허리 통증", "목 통증"],
        "address": "서울시 강남구 테헤란로 1",
        "phone": "02-1234-5678",
        "business_hours": {"mon": "09:00-18:00"},
        "profile_field_provenance": {
            "director_name": {"origin": "OPERATOR"},
            "director_career": {"origin": "AUTOFILL", "source": "homepage"},
            "region": {"origin": "AUTOFILL", "source": "naver"},
        },
    }
    base.update(fields)
    return SimpleNamespace(**base)


def test_profile_facts_render_narrative_fields_with_origin_tags_and_no_operational_facts() -> None:
    text = render_profile_facts_text(_hospital())
    assert text.splitlines() == [
        "[마케터 입력] 원장: 홍길동",
        "[자동 수집·홈페이지] 원장 약력: 서울대학교 의과대학 졸업 정형외과 전문의",
        "[병원 정보] 진료 철학: 환자분이 이해하실 때까지 설명합니다.",
        "[병원 정보] 전문 과목: 정형외과",
        "[병원 정보] 진료 항목: 도수치료 — 손으로 근육을 풉니다",
        "[병원 정보] 진료 항목: 주사치료",
        "[자동 수집·네이버] 지역: 서울 강남구",
        "[병원 정보] 핵심 키워드: 허리 통증, 목 통증",
    ]
    assert "테헤란로" not in text and "02-1234" not in text and "09:00" not in text
    assert render_profile_facts_text(
        _hospital(
            director_name=None,
            director_career=None,
            director_philosophy=None,
            specialties=[],
            treatments=[],
            region=[],
            keywords=[],
        )
    ) is None
    assert len(render_profile_fact_lines(_hospital(treatments=[{"name": "도수치료"}, {"name": "도수치료"}]))) == 7


def test_profile_fact_notes_are_deterministic_verbatim_lines() -> None:
    text = render_profile_facts_text(
        _hospital(keywords=["최고의 허리 치료"])
    )
    notes = profile_fact_note_payloads(text)
    assert profile_fact_note_payloads(text) == notes
    for note in notes:
        assert text[note.excerpt_start : note.excerpt_end] == note.source_excerpt
    by_label = {}
    for note in notes:
        by_label.setdefault(note.note_metadata["label"], note)
    assert by_label["진료 철학"].note_type == EvidenceNoteType.DOCTOR_PHILOSOPHY
    assert by_label["원장 약력"].note_type == EvidenceNoteType.PROOF_POINT
    assert by_label["지역"].note_type == EvidenceNoteType.LOCAL_CONTEXT
    assert by_label["진료 항목"].note_metadata["treatment"] == "도수치료"
    # 금지 표현이 든 줄은 위험 신호 노트다 — 긍정 운영 기준의 근거가 되지 않는다.
    assert by_label["핵심 키워드"].note_type == EvidenceNoteType.RISK_SIGNAL
    assert by_label["핵심 키워드"].note_metadata["violations"]


def test_processing_a_profile_facts_source_never_calls_the_llm(monkeypatch) -> None:
    text = render_profile_facts_text(_hospital())
    source = _source(SourceType.INTERNAL_NOTE, text, profile_facts=True)
    monkeypatch.setattr(essence_engine, "llm_enabled", lambda: True)
    monkeypatch.setattr(
        essence_engine,
        "_process_source_asset_llm",
        lambda _asset: (_ for _ in ()).throw(AssertionError("LLM must not run")),
    )
    payloads = process_source_asset(source)
    assert [payload.source_excerpt for payload in payloads] == text.splitlines()


def test_deterministic_fallback_never_turns_profile_fact_lines_into_must_use() -> None:
    text = render_profile_facts_text(_hospital())
    profile = _source(SourceType.INTERNAL_NOTE, text, profile_facts=True)
    notes = [
        SimpleNamespace(
            id=uuid.uuid4(),
            source_asset_id=profile.id,
            note_type=payload.note_type,
            source_excerpt=payload.source_excerpt,
            claim=payload.claim,
            note_metadata=payload.note_metadata,
        )
        for payload in profile_fact_note_payloads(text)
    ]
    payload, mode = essence_engine.synthesize_philosophy_with_mode(
        SimpleNamespace(name="병원"), [profile], notes, use_llm=False
    )
    pruned, report = prune_automatic_candidate(payload, notes, [profile])

    _assert_invariants(pruned, notes)
    assert pruned["must_use_messages"] == ["환자분이 이해하실 때까지 설명합니다."]
    assert "[" not in (pruned["positioning_statement"] or "")
    assert mode == essence_engine.SYNTHESIS_MODE_DETERMINISTIC
    assert report.must_use["mode"] == MUST_USE_MODE_SYNTHESIZED
