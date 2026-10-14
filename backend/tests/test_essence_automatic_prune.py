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
    MUST_USE_MODE_SOURCE,
    MUST_USE_MODE_SURVEY,
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
        value = payload.get(field)
        # 근거 노트 id(무작위 UUID)는 문장이 아니다 — 숫자 조합이 운영 사실 패턴에 우연히 걸린다.
        if isinstance(value, list):
            value = [
                {k: v for k, v in item.items() if k != "evidence_note_ids"}
                if isinstance(item, dict)
                else item
                for item in value
            ]
        elif isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "evidence_note_ids"}
        parts.extend(essence_engine._iter_field_texts(value))
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
    good = _note(homepage, "진료 전에 충분히 설명합니다. 도수치료는 목이 결리는 분께 안내합니다.")
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
    assert (paraphrase, "MUST_USE_NOT_SOURCE_VERBATIM") in dropped
    assert ("완치를 보장합니다.", "FORBIDDEN_EXPRESSION") in dropped


def test_must_use_is_capped_at_three_survey_sentences() -> None:
    sentences = [f"{word} 저는 환자분과 함께 결정합니다." for word in ("먼저", "그리고", "또", "끝으로", "늘")]
    survey = _source(SourceType.INTERVIEW, " ".join(sentences))
    note = _note(survey, " ".join(sentences), EvidenceNoteType.DOCTOR_PHILOSOPHY)

    pruned, report = prune_automatic_candidate(
        _payload(must_use_messages=sentences, evidence_map={"must_use_messages": [str(note.id)]}),
        [note],
        [survey],
    )

    assert pruned["must_use_messages"] == sentences[:3]
    assert report.must_use["mode"] == MUST_USE_MODE_SURVEY


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


def test_without_a_survey_must_use_is_copied_verbatim_from_other_source_notes() -> None:
    """설문이 없으면 다른 근거 노트의 원문 문장만 쓴다 — 모델이 쓴 문장은 절대 쓰지 않는다."""

    homepage = _source(SourceType.HOMEPAGE)
    note = _note(
        homepage,
        "검사 전에 충분히 설명합니다. 치료 계획은 함께 정하고 무리한 권유를 하지 않습니다.",
        EvidenceNoteType.DOCTOR_PHILOSOPHY,
    )
    payload = _payload(
        must_use_messages=["검사 전에 충분히 설명드립니다.", "무리한 권유를 하지 않는 병원입니다."],
        evidence_map={"must_use_messages": [str(note.id)]},
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    _assert_invariants(pruned, [note])
    assert pruned["must_use_messages"] == [
        "검사 전에 충분히 설명합니다.",
        "치료 계획은 함께 정하고 무리한 권유를 하지 않습니다.",
    ]
    assert report.must_use["mode"] == MUST_USE_MODE_SOURCE
    assert all(item["evidence_note_ids"] == [str(note.id)] for item in report.must_use["items"])
    reasons = {(item["text"], item["reason"]) for item in report.dropped}
    assert ("검사 전에 충분히 설명드립니다.", "MUST_USE_NOT_SOURCE_VERBATIM") in reasons


def test_must_use_is_empty_when_no_source_sentence_is_clean() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    note = _note(homepage, "통증이 사라집니다.", EvidenceNoteType.KEY_MESSAGE)
    pruned, report = prune_automatic_candidate(
        _payload(must_use_messages=["근거 없는 문구입니다."]), [note], [homepage]
    )
    assert pruned["must_use_messages"] == []
    assert report.must_use["mode"] == MUST_USE_MODE_NONE
    assert "must_use_messages" not in pruned["evidence_map"]


@pytest.mark.parametrize(
    "claim",
    [
        "진료 전에 충분히 설명하며 암을 없앱니다.",
        "진료 전에 충분히 설명하면 통증이 사라집니다.",
        "Our injection treatment eliminates cancer.",
        "We explain before treatment and the cancer is cured.",
        "진료 전에 백 번 충분히 설명합니다.",
        "진료 전에 충분히 설명하면 재발하지 않습니다.",
    ],
)
def test_codex_claim_variants_never_reach_positioning_must_use_or_public_about(claim: str) -> None:
    """재현(Codex 2차 P1): 활용형·영문·수량·의미 반전 주장은 원문에 없으면 어디에도 남지 않는다."""

    homepage = _source(SourceType.HOMEPAGE)
    note = _note(
        homepage,
        "진료 전에 한 번 충분히 설명합니다. 재발 위험은 개인마다 다릅니다.",
        EvidenceNoteType.DOCTOR_PHILOSOPHY,
    )
    payload = _payload(
        positioning_statement=f"진료 전에 충분히 설명합니다. {claim}",
        patient_promise=claim,
        must_use_messages=[claim, f"설명합니다. {claim}"],
        evidence_map={
            "positioning_statement": [str(note.id)],
            "patient_promise": [str(note.id)],
            "must_use_messages": [str(note.id)],
        },
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    everything = " ".join(
        [
            str(pruned["positioning_statement"] or ""),
            str(pruned["patient_promise"] or ""),
            *pruned["must_use_messages"],
            *report.public_about,
        ]
    )
    assert claim not in everything
    for word in ("없앱", "사라집", "eliminat", "cured", "백 번", "재발하지"):
        assert word not in everything
    assert pruned["positioning_statement"] == "진료 전에 충분히 설명합니다."
    assert pruned["must_use_messages"] == ["진료 전에 한 번 충분히 설명합니다."] or (
        pruned["must_use_messages"] == []
    )


def test_public_about_uses_only_verbatim_clean_source_sentences() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    note = _note(homepage, "진료 전에 충분히 설명합니다.")
    payload = _payload(
        positioning_statement="진료 전에 충분히 설명합니다. 충분한 설명을 중시하는 의원입니다.",
        evidence_map={"positioning_statement": [str(note.id)]},
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    assert "충분한 설명을 중시하는 의원입니다." in pruned["positioning_statement"]
    # 모델이 쓴 문장은 쓰기 힌트로만 남고 공개 소개에는 원문 문장만 간다.
    assert report.public_about == ["진료 전에 충분히 설명합니다."]


def test_survey_wins_a_voice_conflict_and_the_homepage_sentence_is_dropped() -> None:
    """D34 서술 우선순위를 실제로 적용한다 — 기록만 하지 않는다(재현: 수술 먼저 vs 비수술 먼저)."""

    survey = _source(SourceType.INTERVIEW, "비수술 치료를 먼저 설명합니다.")
    homepage = _source(SourceType.HOMEPAGE, "수술을 먼저 권합니다.")
    survey_note = _note(survey, "비수술 치료를 먼저 설명합니다.", EvidenceNoteType.DOCTOR_PHILOSOPHY)
    home_note = _note(homepage, "수술을 먼저 권합니다.")
    payload = _payload(
        positioning_statement="수술을 먼저 권합니다. 비수술 치료를 먼저 설명합니다.",
        content_principles=["수술을 먼저 권합니다.", "비수술 치료를 먼저 설명합니다."],
        evidence_map={
            "positioning_statement": [str(home_note.id), str(survey_note.id)],
            "content_principles": [str(home_note.id), str(survey_note.id)],
        },
        conflict_notes=[
            {
                "text": "홈페이지와 설문의 첫 치료 방침이 다름",
                "evidence_note_ids": [str(home_note.id), str(survey_note.id)],
            }
        ],
    )

    pruned, report = prune_automatic_candidate(
        payload, [survey_note, home_note], [survey, homepage]
    )

    _assert_invariants(pruned, [survey_note, home_note])
    assert pruned["positioning_statement"] == "비수술 치료를 먼저 설명합니다."
    assert pruned["content_principles"] == ["비수술 치료를 먼저 설명합니다."]
    assert str(home_note.id) not in [
        note_id for ids in pruned["evidence_map"].values() for note_id in ids
    ]
    [conflict] = report.conflicts
    assert conflict["preferred_source"] == "INTERVIEW"
    assert conflict["dropped_note_ids"] == [str(home_note.id)]
    assert conflict["dropped_sentences"] == 2
    assert conflict["resolved_by"] == "D34_NARRATIVE_PRIORITY"


def test_marketer_fact_beats_the_homepage_and_undecidable_conflicts_drop_both() -> None:
    profile = _source(SourceType.INTERNAL_NOTE, profile_facts=True)
    homepage = _source(SourceType.HOMEPAGE)
    blog_a = _source(SourceType.NAVER_BLOG)
    blog_b = _source(SourceType.NAVER_BLOG)
    marketer = _note(
        profile,
        "[마케터 입력] 원장 약력: 정형외과 전문의 15년",
        EvidenceNoteType.PROOF_POINT,
        system_kind=PROFILE_FACTS_SYSTEM_KIND,
        label="원장 약력",
        tag="마케터 입력",
    )
    home_fact = _note(homepage, "원장은 정형외과 전문의 20년 경력입니다.", EvidenceNoteType.PROOF_POINT)
    blog_one = _note(blog_a, "주말에도 상담을 이어갑니다.")
    blog_two = _note(blog_b, "주말에는 상담을 쉽니다.")
    payload = _payload(
        positioning_statement="원장은 정형외과 전문의 20년 경력입니다.",
        tone_guidelines=["주말에도 상담을 이어갑니다.", "주말에는 상담을 쉽니다."],
        evidence_map={
            "positioning_statement": [str(home_fact.id), str(marketer.id)],
            "tone_guidelines": [str(blog_one.id), str(blog_two.id)],
        },
        conflict_notes=[
            {"text": "경력 연수", "evidence_note_ids": [str(home_fact.id), str(marketer.id)]},
            {"text": "주말 상담", "evidence_note_ids": [str(blog_one.id), str(blog_two.id)]},
        ],
    )

    pruned, report = prune_automatic_candidate(
        payload,
        [marketer, home_fact, blog_one, blog_two],
        [profile, homepage, blog_a, blog_b],
    )

    assert pruned["positioning_statement"] is None
    assert pruned["tone_guidelines"] == []
    assert [item["resolved_by"] for item in report.conflicts] == ["D34_FACT_PRIORITY"]
    assert report.conflicts[0]["kept_note_ids"] == [str(marketer.id)]
    reasons = {item["reason"] for item in report.dropped}
    assert {"CONFLICT_LOWER_PRIORITY", "CONFLICT_UNDECIDED"} <= reasons


def test_a_losing_career_sentence_never_returns_through_must_use_or_fallback() -> None:
    """재현(Codex 2차 P1): 마케터 "전문의 15년"에 진 설문 "20년" 문장은 판 어디에도 없다."""

    profile = _source(SourceType.INTERNAL_NOTE, profile_facts=True)
    survey = _source(SourceType.INTERVIEW)
    marketer = _note(
        profile,
        "[마케터 입력] 원장 약력: 정형외과 전문의 15년",
        EvidenceNoteType.PROOF_POINT,
        system_kind=PROFILE_FACTS_SYSTEM_KIND,
        label="원장 약력",
        tag="마케터 입력",
    )
    loser = "저는 정형외과 전문의로 20년 동안 진료했습니다."
    survey_note = _note(survey, loser, EvidenceNoteType.DOCTOR_PHILOSOPHY)
    calm = _note(survey, "진료 전에 충분히 설명합니다.", EvidenceNoteType.TONE_SIGNAL)
    payload = _payload(
        positioning_statement=loser,
        must_use_messages=[loser],
        evidence_map={
            "positioning_statement": [str(survey_note.id), str(marketer.id)],
            "must_use_messages": [str(survey_note.id)],
        },
        conflict_notes=[
            {"text": "경력 연수", "evidence_note_ids": [str(marketer.id), str(survey_note.id)]}
        ],
    )

    pruned, report = prune_automatic_candidate(
        payload, [marketer, survey_note, calm], [profile, survey]
    )

    flat = repr(pruned) + repr(report.must_use) + repr(report.public_about)
    assert "20년" not in flat
    assert str(survey_note.id) not in flat
    assert pruned["must_use_messages"] == ["진료 전에 충분히 설명합니다."]
    assert report.conflicts[0]["dropped_note_ids"] == [str(survey_note.id)]


def test_unsupported_medical_claim_never_survives_on_an_unrelated_note() -> None:
    """재현(Codex P1): 근거 노트 id만 맞는 효능 주장은 포지셔닝·필수 문구에 남지 않는다."""

    homepage = _source(SourceType.HOMEPAGE, "진료 전에 충분히 설명합니다.")
    note = _note(homepage, "진료 전에 충분히 설명합니다.")
    claim = "본원의 주사 치료는 암세포를 제거합니다."
    payload = _payload(
        positioning_statement=claim,
        patient_promise="진료 전에 충분히 설명합니다.",
        must_use_messages=[claim, "진료 전에 충분히 설명드립니다."],
        evidence_map={
            "positioning_statement": [str(note.id)],
            "patient_promise": [str(note.id)],
            "must_use_messages": [str(note.id)],
        },
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    _assert_invariants(pruned, [note])
    assert pruned["positioning_statement"] is None
    assert pruned["patient_promise"] == "진료 전에 충분히 설명합니다."
    # 필수 문구는 원문 문장만 — 모델의 두 문구 모두 버리고 노트 원문을 쓴다.
    assert pruned["must_use_messages"] == ["진료 전에 충분히 설명합니다."]
    reasons = {(item["field"], item["reason"]) for item in report.dropped}
    assert ("positioning_statement", "UNSUPPORTED_CLAIM") in reasons
    assert ("must_use_messages", "MUST_USE_NOT_SOURCE_VERBATIM") in reasons


def test_verbatim_medical_claims_stay_as_hints_but_never_become_must_use() -> None:
    homepage = _source(SourceType.HOMEPAGE)
    note = _note(homepage, "체외충격파 치료로 통증 완화를 기대할 수 있습니다.")
    sentence = "체외충격파 치료로 통증 완화를 기대할 수 있습니다."
    payload = _payload(
        positioning_statement=sentence,
        must_use_messages=[sentence],
        evidence_map={
            "positioning_statement": [str(note.id)],
            "must_use_messages": [str(note.id)],
        },
    )

    pruned, report = prune_automatic_candidate(payload, [note], [homepage])

    assert pruned["positioning_statement"] == sentence
    assert pruned["must_use_messages"] == []
    assert report.must_use["mode"] == MUST_USE_MODE_NONE


def test_deterministic_fallback_picks_the_survey_over_the_homepage() -> None:
    survey = _source(SourceType.INTERVIEW, "비수술 치료를 먼저 설명합니다.")
    homepage = _source(SourceType.HOMEPAGE, "수술을 먼저 권합니다.")
    notes = [
        _note(homepage, "수술을 먼저 권합니다."),
        _note(survey, "비수술 치료를 먼저 설명합니다."),
    ]

    payload, _mode = essence_engine.synthesize_philosophy_with_mode(
        SimpleNamespace(name="병원"), [homepage, survey], notes, use_llm=False
    )

    assert "비수술 치료를 먼저 설명합니다." in payload["positioning_statement"]


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
        "name": "서울바른의원",
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
        "[병원 정보] 병원명: 서울바른의원",
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
            name=None,
            director_name=None,
            director_career=None,
            director_philosophy=None,
            specialties=[],
            treatments=[],
            region=[],
            keywords=[],
        )
    ) is None
    assert len(render_profile_fact_lines(_hospital(treatments=[{"name": "도수치료"}, {"name": "도수치료"}]))) == 8


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
    assert report.must_use["mode"] == MUST_USE_MODE_SOURCE
