"""측정 질문·제목 문형 가드 — 2026-10-06 비문 사고의 근본 원인 회귀 방지."""

import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")

import logging
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.content import ContentItem, ContentType
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.services.content_ai_review import ContentAiReview, ContentAiReviewStatus
from app.services.content_brief import build_content_brief
from app.services.content_engine import (
    EXAM_TREATMENT_TITLE_FINDING_PREFIX,
    _fill_type_prompt,
    _validate_target_alignment,
)
from app.services.content_generation_review import (
    ContentReviewDependencies,
    GenerationReviewLimits,
    generate_reviewed_content,
)
from app.services.cost_guard import CostGuardDecision
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, EssenceScreeningResult
from app.services.question_wellformed import (
    PROBLEM_FILLER_KEYWORD,
    PROBLEM_REPEATED_TOKEN,
    PROBLEM_TREATMENT_ON_EXAM,
    question_is_wellformed,
    question_problems,
)
from app.services.sov_engine import generate_query_matrix_specs

# ── a) UNKNOWN은 중립 템플릿만 ────────────────────────────────────────────────


@pytest.mark.parametrize("keyword", ["도플러", "홀터"])
def test_unknown_keyword_gets_only_neutral_phrasing(keyword: str) -> None:
    specs = generate_query_matrix_specs(["마산", "마산합포구"], ["내과"], [keyword])
    texts = [text for text, _ in specs if keyword in text]
    assert texts, "중립 템플릿으로는 질문이 만들어져야 한다"
    for text in texts:
        assert "치료" not in text, text
        assert "수술" not in text, text
    assert any("진료 가능한 병원" in text for text in texts)
    assert any("진료를 받으려는데" in text for text in texts)


def test_disease_keyword_still_gets_treatment_phrasing() -> None:
    texts = [t for t, _ in generate_query_matrix_specs(["마산"], ["내과"], ["고혈압"])]
    assert any("고혈압 치료하는 병원" in text for text in texts)


# ── b) question_is_wellformed ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "keyword", "problem"),
    [
        ("마산 마산 심장초음파 진료 가능한 병원", None, PROBLEM_REPEATED_TOKEN),
        ("마산 추천 진료를 받으려는데 마산 어느 병원으로 가야 해?", None, PROBLEM_FILLER_KEYWORD),
        ("추천 진료를 받으려는데 마산 어느 병원으로 가야 해?", "추천", PROBLEM_FILLER_KEYWORD),
        ("갑상선초음파 치료 비용이 얼마나 드는지 알려줘", None, PROBLEM_TREATMENT_ON_EXAM),
        ("마산에서 심장초음파 치료하는 병원 알려줘", "심장초음파", PROBLEM_TREATMENT_ON_EXAM),
    ],
)
def test_malformed_questions_are_rejected(text: str, keyword: str | None, problem: str) -> None:
    assert problem in question_problems(text, keyword=keyword)
    assert question_is_wellformed(text, keyword=keyword) is False


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        ("마산에서 고혈압 치료하는 병원 알려줘", "고혈압"),
        ("임플란트 치료 비용이 얼마나 드는지 알려줘", "임플란트"),  # 시술은 '치료'와 어울린다
        ("마산 심장초음파 진료 가능한 병원", "심장초음파"),
        ("마산 내과 추천해줘", None),
        ("대장내시경 후 회복 기간 얼마나 돼?", "대장내시경"),
    ],
)
def test_normal_questions_pass(text: str, keyword: str | None) -> None:
    assert question_problems(text, keyword=keyword) == []


def test_malformed_question_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.services.question_wellformed"):
        assert question_is_wellformed("마산 마산 심장초음파 진료 가능한 병원", source="t") is False
    assert "malformed question rejected" in caplog.text


def test_matrix_drops_a_malformed_sentence_instead_of_storing_it() -> None:
    # 지역이 키워드에 이미 들어 있어도(전처리가 놓친 경우) 중복 문장은 저장되지 않는다.
    specs = generate_query_matrix_specs(["마산"], ["내과"], ["심장초음파"])
    for text, _ in specs:
        assert question_is_wellformed(text), text


# ── brief: 작가에게 비문을 환자 질문으로 주지 않는다 ───────────────────────────


def _hospital() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="마산내과",
        director_name="김원장",
        region=["마산"],
        keywords=["심장초음파"],
        specialties=["내과"],
        director_philosophy="정확한 진단",
        treatments=[],
    )


def _brief(name: str, condition: str | None = None, treatment: str | None = None) -> dict:
    target = SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        target_intent="증상 탐색",
        region_terms=["마산"],
        specialty=None,
        condition_or_symptom=condition,
        treatment=treatment,
        priority="HIGH",
        target_month="2026-10",
        decision_criteria=[],
        variants=[],
    )
    return build_content_brief(
        hospital=_hospital(),
        content_item=SimpleNamespace(id=uuid.uuid4(), title=None, content_type=ContentType.DISEASE),
        query_target=target,
    )


def test_brief_replaces_a_malformed_patient_question_with_neutral_phrasing() -> None:
    brief = _brief("마산 마산 심장초음파 진료 가능한 병원", treatment="심장초음파")
    assert brief["target_question"] == "심장초음파 관련 진료는 어느 병원에서 받을 수 있나요?"
    assert "마산 마산" not in brief["target_question"]


def test_brief_leaves_a_wellformed_question_untouched() -> None:
    brief = _brief("마산에서 고혈압 치료하는 병원 알려줘", condition="고혈압")
    assert brief["target_question"] == "마산에서 고혈압 치료하는 병원 알려주시겠어요?"


def test_brief_has_no_question_when_even_the_keyword_is_unusable() -> None:
    brief = _brief("마산 추천 진료를 받으려는데 마산 어느 병원으로 가야 해?", treatment="추천")
    assert brief["target_question"] == ""


def test_writer_prompt_omits_the_malformed_raw_query() -> None:
    brief = _brief("마산 마산 심장초음파 진료 가능한 병원", treatment="심장초음파")
    prompt = _fill_type_prompt(ContentType.DISEASE, _hospital(), brief)
    assert "마산 마산" not in prompt


# ── c) 검사 제목의 '치료'는 한 번의 재작성 뒤 거절 ────────────────────────────


def test_title_with_treatment_on_exam_yields_a_finding() -> None:
    brief = {"target_keyword": "갑상선초음파", "target_query": "q"}
    result = {"title": "갑상선초음파 치료 비용, 얼마일까", "body": "## 갑상선초음파\n", "faq_question": ""}
    findings = _validate_target_alignment(result, brief, ContentType.DISEASE)
    assert any(f.startswith(EXAM_TREATMENT_TITLE_FINDING_PREFIX) for f in findings)


def test_exam_title_without_treatment_has_no_such_finding() -> None:
    brief = {"target_keyword": "갑상선초음파", "target_query": "q"}
    result = {"title": "갑상선초음파 검사는 언제 받나요", "body": "## 갑상선초음파\n", "faq_question": ""}
    findings = _validate_target_alignment(result, brief, ContentType.DISEASE)
    assert not any(f.startswith(EXAM_TREATMENT_TITLE_FINDING_PREFIX) for f in findings)


LIMITS = GenerationReviewLimits(3, 2, 1, 1)


def _context() -> dict:
    hospital = Hospital(id=uuid.uuid4(), name="격리 검증 의원")
    return {
        "hospital": hospital,
        "item": ContentItem(hospital_id=hospital.id, content_type=ContentType.DISEASE),
        "existing_titles": [],
        "philosophy": HospitalContentPhilosophy(hospital_id=hospital.id),
        "approved_brief": None,
    }


def _dependencies(candidates: list[dict]) -> ContentReviewDependencies:
    queue = [deepcopy(c) for c in candidates]

    async def generate(*args, **kwargs):
        return queue.pop(0) if len(queue) > 1 else deepcopy(queue[0])

    return ContentReviewDependencies(
        generate=AsyncMock(side_effect=generate),
        review=AsyncMock(
            return_value=ContentAiReview(ContentAiReviewStatus.PASS, 0.95, (), "r", "m")
        ),
        screen=Mock(return_value=EssenceScreeningResult(ESSENCE_STATUS_ALIGNED, {})),
        check_cost=AsyncMock(return_value=CostGuardDecision(True)),
    )


_BAD = {
    "title": "갑상선초음파 치료 비용",
    "body": "내용",
    "target_alignment_findings": [f"{EXAM_TREATMENT_TITLE_FINDING_PREFIX}: 제목 '갑상선초음파 치료 비용'"],
}
_GOOD = {"title": "갑상선초음파 검사 안내", "body": "내용", "target_alignment_findings": []}


async def test_surviving_exam_treatment_title_is_rejected_after_one_rewrite() -> None:
    effects = _dependencies([_BAD])
    with pytest.raises(ValueError, match="Exam title"):
        await generate_reviewed_content(**_context(), dependencies=effects, limits=LIMITS)
    # 보완 재작성은 정확히 한 번 시도했다.
    assert effects.generate.await_count == 2


async def test_rewritten_exam_title_is_accepted() -> None:
    effects = _dependencies([_BAD, _GOOD])
    candidate, _ = await generate_reviewed_content(
        **_context(), dependencies=effects, limits=LIMITS
    )
    assert candidate["title"] == "갑상선초음파 검사 안내"


def test_rejection_is_classified_as_a_sample_failure() -> None:
    from app.workers.generation_retry_policy import SAMPLE_BODY_CODES
    from app.workers.generation_run_control import classify_generation_failure

    code, _ = classify_generation_failure(ValueError("Exam title with treatment wording"))
    assert code == "GENERATION_REJECTED"
    assert code in SAMPLE_BODY_CODES
