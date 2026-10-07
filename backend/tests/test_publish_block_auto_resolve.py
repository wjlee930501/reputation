"""발행 차단 자동 해결(PR-A) — 지적 문장 최소 교정·응급 템플릿·주제 교체·상한·검수 우회 불가.

2026-10-03~05 운영에서 사람이 직접 PATCH→재검수→발행으로 풀었던 차단(마포 수련기관 오기·승인되지
않은 진료철학, 노원 응급 안내 누락, 위례 사후관리 문장, 신기한 출혈 응급 안내, 강심장 골밀도검사)을
그대로 재현해 자동 경로가 같은 결과를 내는지, 그리고 그 경로가 검수를 우회하지 않는지 고정한다.
실제 Postgres·실제 발행기 경로는 `tests/integration/test_publish_block_auto_resolve_postgres.py`가 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services import content_minimal_correction as mc
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_publication import (
    _blocking_ai_review_state,
    assess_content_publication,
)
from app.services.specialty_compatibility import (
    target_conflicts_with_hospital,
    target_fits_hospital,
    target_names_unoffered_service,
)
from app.utils.medical_filter import check_forbidden
from app.workers import generation_incident_control, tasks, topic_swap_fallback
from app.workers.generation_retry_policy import (
    AUTO_CORRECTION_EXHAUSTED_KEY,
    GenerationRetryClass,
)

MAPO_PROFILE_SENTENCE = "이현진 원장은 가톨릭대학교 성모병원에서 정형외과 전공의 수련을 마쳤습니다."
WRONG_TRAINING = "이현진 원장은 서울성모병원에서 정형외과 전공의 수련을 마쳤습니다."
UNAPPROVED_PHILOSOPHY = "본원은 진단부터 재활·예방까지 의료진이 정보를 공유하며 보는 것을 진료 원칙으로 합니다."
NEUTRAL_A = "허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다."
NEUTRAL_B = "통증이 오래가면 생활 습관과 자세를 함께 살펴봅니다."
NERVE_SENTENCE = "다리 힘이 빠지거나 대소변 장애가 생기면 가능한 한 빨리 진료를 받으셔야 합니다."


def _body(*sentences: str) -> str:
    return "## 허리 통증 안내\n" + " ".join(sentences) + "\n\n## 내원 전 확인\n" + NEUTRAL_B


def _content(body: str, **overrides) -> dict:
    values = {
        "title": "허리 통증, 언제 병원에 가야 할까요?",
        "body": body,
        "meta_description": "허리 통증의 내원 시점을 안내합니다.",
        "faq_question": "허리 통증은 언제 병원에 가야 하나요?",
        "faq_answer_summary": "통증이 오래가거나 신경 증상이 있으면 진료가 필요합니다.",
        "references_list": [{"title": "요통", "url": "https://health.kdca.go.kr/x"}],
    }
    values.update(overrides)
    return values


def _hospital(**overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "마포성모탑정형외과의원",
        "director_name": "이현진",
        "director_career": "가톨릭대학교 성모병원 정형외과 전공의 수련, 정형외과 전문의",
        "treatments": ["도수치료", "체외충격파"],
        "specialties": ["정형외과"],
        "keywords": ["마포 정형외과"],
        "region": ["서울 마포구"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _philosophy():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="APPROVED",
        positioning_statement="과잉진료를 피하고 1:1 맞춤진료로 현재 상태에 맞는 최소한의 개입을 원칙으로 합니다.",
        doctor_voice="",
        content_principles=[],
        treatment_narratives=[],
        must_use_messages=[],
    )


def _finding(severity, kind, message, quote=""):
    return {"severity": severity, "kind": kind, "message": message, "quote": quote}


def _review_payload(content: dict, *findings: dict) -> dict:
    return {
        "status": "REVISE",
        "blocking": True,
        "findings": list(findings),
        "schema_version": "content-review-v2",
        "candidate_sha256": candidate_sha256(content),
        "coverage": candidate_review_coverage(content),
    }


def _review(content: dict, status=ContentAiReviewStatus.PASS, findings=()) -> ContentAiReview:
    return ContentAiReview(
        status=status,
        confidence=0.9,
        findings=tuple(findings),
        summary="재검수",
        model="reviewer-test",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
        provider_attempted=True,
    )


def _no_provider(**_kwargs):
    raise AssertionError("이 경로는 공급자를 부르지 않는다")


# ── 1. 응급 안내 템플릿 ────────────────────────────────────────────────────────


def test_every_emergency_template_is_a_fixed_safe_sentence():
    """템플릿은 코드 상수다 — 합니다체 한 문장, 119·응급실 안내, 금지 표현 없음."""

    assert set(mc.EMERGENCY_TEMPLATES) >= {
        "cardiac",
        "gi_bleeding",
        "neurologic",
        "allergy_breathing",
        "pediatric_fever",
        "trauma",
        "general",
    }
    for template in mc.EMERGENCY_TEMPLATES.values():
        assert template.endswith("합니다.")
        assert "119" in template and "응급실" in template
        assert check_forbidden(template) == []


@pytest.mark.parametrize(
    ("message", "group"),
    [
        ("다리 힘 빠짐·대소변 장애 같은 응급 신호를 119·응급실 안내 없이 다뤘습니다.", "neurologic"),
        ("토혈·흑색변 등 출혈 의심 시 119 또는 응급실 안내가 없습니다.", "gi_bleeding"),
        ("두근거림과 가슴 조이는 통증·실신 때의 응급 안내가 조건부로만 있습니다.", "cardiac"),
        ("두드러기와 호흡곤란이 있을 때 119 응급 안내가 필요합니다.", "allergy_breathing"),
        ("응급 상황 안내가 부족합니다.", "general"),
    ],
)
async def test_emergency_template_inserted_deterministically_without_llm(message, group):
    body = _body(NEUTRAL_A, NERVE_SENTENCE)
    content = _content(body)
    # 일반 템플릿은 증상 신호가 없을 때다 — 인용 문장도 증상군 신호이므로 그 경우는 인용 없이 낸다.
    quote = "" if group == "general" else NERVE_SENTENCE
    review = _review_payload(
        content, _finding("UNCERTAIN", "MEDICAL_SAFETY", message, quote=quote)
    )
    seen: list[dict] = []

    async def reviewer(**kwargs):
        seen.append(kwargs["content"])
        return _review(kwargs["content"])

    async def no_llm(**_kwargs):
        raise AssertionError("응급 템플릿은 LLM이 쓰지 않는다")

    outcome = await mc.run_minimal_correction(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=no_llm, review=reviewer),
    )

    assert outcome.status == "PASS"
    template = mc.EMERGENCY_TEMPLATES[group]
    corrected = outcome.content["body"]
    assert corrected.count(template) == 1
    # 템플릿은 지적이 가리킨 문장 바로 뒤(인용이 없으면 본문 끝)에 들어가고 나머지는 한 글자도
    # 바뀌지 않는다.
    if quote:
        assert corrected == body.replace(NERVE_SENTENCE, f"{NERVE_SENTENCE} {template}")
    else:
        assert corrected == f"{body} {template}"
    assert seen == [outcome.content], "삽입 뒤 반드시 독립 재검수를 받는다"
    assert outcome.review.candidate_sha256 == candidate_sha256(outcome.content)

    # 같은 템플릿을 두 번 넣지 않는다 — 결정적이고 멱등이다.
    again = mc.plan_corrections(
        outcome.content,
        _review_payload(outcome.content, _finding("UNCERTAIN", "MEDICAL_SAFETY", message)),
    )
    assert again.insertions == ()


# ── 2. 새 사실 미생성·범위 제한 ───────────────────────────────────────────────


def test_unsupported_terms_rejects_new_numbers_and_proper_nouns():
    sources = mc.approved_fact_texts(_hospital(), _philosophy())

    assert mc.unsupported_terms(MAPO_PROFILE_SENTENCE, [WRONG_TRAINING, *sources]) == []
    assert "15년" in mc.unsupported_terms(
        "이현진 원장은 15년 동안 정형외과 진료를 했습니다.", [WRONG_TRAINING, *sources]
    )
    assert any(
        term.startswith("연세")
        for term in mc.unsupported_terms(
            "이현진 원장은 연세세브란스병원에서 수련했습니다.", [WRONG_TRAINING, *sources]
        )
    )


def _profile_with_numbers():
    return _hospital(
        address="서울 마포구 마포대로 120",
        phone="02-1234-5678",
        business_hours={"평일": "09:00-18:30", "토요일": "09:00-13:00"},
        director_career="가톨릭대학교 성모병원 정형외과 전공의 수련, 정형외과 전문의 15년",
    )


@pytest.mark.parametrize(
    ("sentence", "term"),
    [
        # 주소 `마포대로 120`의 20, 전화의 34, 진료시간 `18:30`의 30이 새 경력 수치를 통과시키면 안 된다.
        ("이현진 원장은 20년 수련을 마쳤습니다.", "20년"),
        ("이현진 원장은 정형외과 전문의 34년 원장입니다.", "34년"),
        ("이현진 원장은 30년 수련을 마쳤습니다.", "30년"),
        # 승인 낱말의 가운데 조각(`정형외과`의 `외과`)도 근거가 아니다.
        ("이현진 원장은 외과 전공의 수련을 마쳤습니다.", "외과"),
    ],
)
def test_unsupported_terms_checks_whole_tokens_of_a_real_profile(sentence, term):
    """주소·전화·진료시간이 든 실제 프로필에서도 새 수치·낱말은 근거가 되지 않는다."""

    sources = mc.approved_fact_texts(_profile_with_numbers(), _philosophy())

    assert term in mc.unsupported_terms(sentence, [WRONG_TRAINING, *sources])
    # 승인 자료에 단위까지 그대로 있는 수치와 승인 문장은 그대로 쓸 수 있다.
    assert mc.unsupported_terms(
        "이현진 원장은 정형외과 전문의 15년 경력입니다.", [WRONG_TRAINING, *sources]
    ) == ["경력입니다"]
    assert mc.unsupported_terms(MAPO_PROFILE_SENTENCE, [WRONG_TRAINING, *sources]) == []


@pytest.mark.parametrize(
    "invented",
    [
        "이현진 원장은 20년 수련을 마쳤습니다.",
        "이현진 원장은 정형외과 전문의 34년 원장입니다.",
        "이현진 원장은 30년 수련을 마쳤습니다.",
    ],
)
async def test_new_number_from_profile_digits_is_deleted_not_replaced(invented):
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    content = _content(body)
    review = _review_payload(
        content,
        _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
    )

    async def inventive_llm(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", invented)}

    async def reviewer(**kwargs):
        return _review(kwargs["content"])

    outcome = await mc.run_minimal_correction(
        hospital=_profile_with_numbers(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=inventive_llm, review=reviewer),
    )

    assert invented not in outcome.content["body"]
    assert outcome.content["body"] == body.replace(f" {WRONG_TRAINING}", "")
    assert outcome.history[0]["sentences"][0]["decision"].startswith("rejected:")


def test_scope_check_rejects_change_outside_finding_sentence():
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    content = _content(body)
    plan = mc.plan_corrections(
        content,
        _review_payload(
            content,
            _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
        ),
    )
    sources = mc.approved_fact_texts(_hospital(), _philosophy())
    assert len(plan.targets) == 1

    in_scope = dict(content, body=body.replace(WRONG_TRAINING, MAPO_PROFILE_SENTENCE))
    mc.verify_correction_scope(content, in_scope, plan, sources=sources)

    outside = dict(in_scope, body=in_scope["body"].replace(NEUTRAL_A, "허리 통증은 반드시 낫습니다."))
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(content, outside, plan, sources=sources)

    new_fact = dict(
        content,
        body=body.replace(WRONG_TRAINING, "이현진 원장은 연세세브란스병원에서 20년간 수련했습니다."),
    )
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(content, new_fact, plan, sources=sources)

    retitled = dict(in_scope, title="새 제목")
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(content, retitled, plan, sources=sources)


async def test_llm_new_fact_is_refused_and_sentence_deleted_instead():
    """LLM이 새 수치·고유명사를 넣으면 받아들이지 않고 그 문장을 지운다(새 사실 미생성)."""

    body = _body(NEUTRAL_A, UNAPPROVED_PHILOSOPHY)
    content = _content(body)
    review = _review_payload(
        content,
        _finding(
            "HARD",
            "HOSPITAL_FACT",
            "승인 자료에 없는 진료 원칙입니다.",
            quote=UNAPPROVED_PHILOSOPHY,
        ),
    )

    async def inventive_llm(*, targets, **_kwargs):
        return {
            targets[0].key: ("REPLACE", "본원은 30년 전통의 서울대 협진 시스템으로 진료합니다.")
        }

    async def reviewer(**kwargs):
        return _review(kwargs["content"])

    outcome = await mc.run_minimal_correction(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=inventive_llm, review=reviewer),
    )

    assert outcome.status == "PASS"
    corrected = outcome.content["body"]
    assert UNAPPROVED_PHILOSOPHY not in corrected
    assert "30" not in corrected and "서울대" not in corrected
    assert corrected == body.replace(f" {UNAPPROVED_PHILOSOPHY}", "")
    assert outcome.history[0]["sentences"][0]["decision"].startswith("rejected:")


async def test_llm_correction_from_approved_profile_is_kept():
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    content = _content(body)
    review = _review_payload(
        content,
        _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
    )

    async def faithful_llm(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", MAPO_PROFILE_SENTENCE)}

    async def reviewer(**kwargs):
        return _review(kwargs["content"])

    outcome = await mc.run_minimal_correction(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=faithful_llm, review=reviewer),
    )

    assert outcome.status == "PASS"
    assert outcome.content["body"] == body.replace(WRONG_TRAINING, MAPO_PROFILE_SENTENCE)
    for field in ("title", "faq_question", "references_list", "meta_description"):
        assert outcome.content[field] == content[field]


def test_must_use_sentence_and_unquoted_hard_are_not_taken_by_the_pass():
    must_use = "정확한 진단은 전문의 진료로 확인하는 것이 안전합니다."
    body = _body(NEUTRAL_A, must_use)
    content = _content(body)
    plan = mc.plan_corrections(
        content,
        _review_payload(
            content, _finding("HARD", "MEDICAL_SAFETY", "어미가 끊겼습니다.", quote=must_use)
        ),
        must_use_messages=[must_use],
    )
    assert not plan.applicable, "필수 문구는 원문 그대로 둔다"

    unquoted = mc.plan_corrections(
        content,
        _review_payload(
            content, _finding("HARD", "HOSPITAL_FACT", "승인 자료에서 심장 초음파를 확인할 수 없습니다.")
        ),
    )
    assert not unquoted.applicable, "인용 없는 HARD는 승인 자료 변경이라는 기존 경로가 맡는다"


# ── 3·4. 워커: 교정 → 재검수 PASS / 교정 2회 실패 → 주제 교체 → 사람 ─────────


class _DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _item):
        return None

    def execute(self, _statement):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: []), scalar_one_or_none=lambda: None
        )


def _blocked_item(philosophy, body, finding, *, scheduled=None, history=None):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital=None,
        status=SimpleNamespace(value="DRAFT"),
        content_type=SimpleNamespace(value="FAQ"),
        content_philosophy_id=philosophy.id,
        content_revision=3,
        generation_claim_token=None,
        query_target_id=None,
        content_brief=None,
        scheduled_date=scheduled or date.today() + timedelta(days=1),
        topic_swap_history=history,
        image_url=None,
        **_content(body),
    )
    item.essence_check_summary = {
        "blocking": True,
        "findings": [finding["message"]],
        "ai_review": _review_payload(tasks._stored_candidate(item), finding),
    }
    return item


@pytest.fixture
def worker(monkeypatch):
    """`_generate_single_content_item`의 저장 본문 경로만 남기고 외부 효과를 잡는다."""

    philosophy = _philosophy()
    calls = {
        "llm": 0, "reviews": [], "writes": [], "correction_only": [], "image": 0, "writer": 0
    }

    def write_back(
        _db, *, item_id, expected_revision, expected_claim_token, values, correction_only=False
    ):
        calls["writes"].append(dict(values))
        calls["correction_only"].append(correction_only)
        for name, value in values.items():
            setattr(calls["item"], name, value)
        calls["item"].content_revision += 1
        return 1

    def assess(item, _philosophy):
        state = _blocking_ai_review_state(item)
        if state is None:
            return SimpleNamespace(code=None, message=None)
        return SimpleNamespace(
            code="CONTENT_AI_REVIEW_STALE" if state[0] == "STALE" else "CONTENT_AI_HARD_FINDING",
            message="독립 검수 지적",
        )

    def image(_db, _item, _hospital, _philosophy):
        calls["image"] += 1
        return tasks.GenerationItemState.SUCCEEDED

    async def writer(**_kwargs):
        calls["writer"] += 1
        raise AssertionError("최소 교정은 작가 세션을 사지 않는다")

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "assess_content_publication", assess)
    monkeypatch.setattr(tasks, "write_back_generated_content", write_back)
    monkeypatch.setattr(tasks, "_recover_missing_content_image", image)
    monkeypatch.setattr(tasks, "_persist_publication_readiness", lambda *_a: None)
    monkeypatch.setattr(tasks, "_generate_with_auto_review", writer)
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")
    return philosophy, calls


def _install(monkeypatch, calls, *, propose, review):
    async def counted_propose(**kwargs):
        calls["llm"] += 1
        return await propose(**kwargs)

    async def counted_review(**kwargs):
        calls["reviews"].append(kwargs["content"])
        return await review(**kwargs)

    monkeypatch.setattr(tasks, "propose_sentence_corrections", counted_propose)
    monkeypatch.setattr(tasks, "review_generated_content", counted_review)


def test_hard_finding_corrected_and_rereview_pass_leads_to_publish_gate(worker, monkeypatch):
    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item

    async def propose(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", MAPO_PROFILE_SENTENCE)}

    async def review(**kwargs):
        return _review(kwargs["content"])

    _install(monkeypatch, calls, propose=propose, review=review)
    hospital = _hospital(id=item.hospital_id)

    state, code, _message = tasks._generate_single_content_item(_DB(), item, hospital)

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert item.body == body.replace(WRONG_TRAINING, MAPO_PROFILE_SENTENCE)
    # 교정본과 그 재검수 판정은 한 번의 상태 가드 UPDATE로 함께 저장된다.
    assert len(calls["writes"]) == 1
    # 교정 저장은 발행 이력·사람 편집 글을 0행으로 막는 술어를 함께 건다.
    assert calls["correction_only"] == [True]
    written = calls["writes"][0]
    assert written["essence_check_summary"]["ai_review"]["status"] == "PASS"
    assert written["essence_check_summary"]["ai_review"]["candidate_sha256"] == candidate_sha256(
        tasks._stored_candidate(item)
    )
    assert written["essence_check_summary"][mc.AUTO_CORRECTION_KEY]["corrected_sha256"] == (
        candidate_sha256(tasks._stored_candidate(item))
    )
    assert calls["llm"] == 1 and len(calls["reviews"]) == 1 and calls["writer"] == 0
    assert calls["image"] == 1, "PASS 뒤에는 기존 이미지·발행 준비 판정으로 이어 간다"
    assert _blocking_ai_review_state(item) is None


def test_two_failed_corrections_swap_topic_then_alert_operator(worker, monkeypatch):
    philosophy, calls = worker
    sentences = [WRONG_TRAINING, UNAPPROVED_PHILOSOPHY, NEUTRAL_A]
    body = _body(*sentences)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item

    async def propose(*, targets, **_kwargs):
        return {target.key: ("DELETE", "") for target in targets}

    async def review(**kwargs):
        # 매번 남은 다른 문장을 새로 HARD로 지적한다 — 교정이 끝내 PASS를 받지 못한다.
        content = kwargs["content"]
        remaining = [s for s in sentences if s in content["body"]]
        return _review(
            content,
            ContentAiReviewStatus.REVISE,
            (
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "승인 자료에 없는 문장입니다.",
                    quote=remaining[0],
                ),
            ),
        )

    _install(monkeypatch, calls, propose=propose, review=review)
    hospital = _hospital(id=item.hospital_id)

    state, code, message = tasks._generate_single_content_item(_DB(), item, hospital)

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert len(calls["reviews"]) == settings.CONTENT_AUTO_CORRECTION_MAX_PASSES == 2
    attempt = tasks._stored_generation_attempt(item)
    # 사람이 아니라 다음 스윕의 주제 교체가 소유한다 — RETRYING이고 기한이 있다.
    assert attempt[AUTO_CORRECTION_EXHAUSTED_KEY] is True
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert attempt["next_retry_at"]
    assert "주제" in message
    assert generation_incident_control.scheduled_recovery_owns_blocker(code, item)
    assert not generation_incident_control.generation_block_is_terminal(code, item)
    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "CONTENT_AI_HARD_FINDING"

    # 교체 대기 중에 다시 집혀도(운영자 재시도·같은 스윕) 아무것도 사지 않는다.
    before = (calls["llm"], len(calls["reviews"]))
    tasks._generate_single_content_item(_DB(), item, hospital)
    assert (calls["llm"], len(calls["reviews"])) == before

    # 교체 시각이 지나도 그대로라면 교체 pass가 바꾸지 못한 것이다 → 사람에게 알린다.
    item.essence_check_summary["generation_attempt"]["next_retry_at"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()
    state, code, message = tasks._generate_single_content_item(_DB(), item, hospital)
    attempt = tasks._stored_generation_attempt(item)
    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert attempt["next_retry_at"] is None
    assert generation_incident_control.generation_block_is_terminal(code, item)
    assert not generation_incident_control.scheduled_recovery_owns_blocker(code, item)
    assert (calls["llm"], len(calls["reviews"])) == before


def _expire_swap_wait(item):
    item.essence_check_summary["generation_attempt"]["next_retry_at"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()


def test_unswappable_slot_stays_with_operator_across_sweeps(worker, monkeypatch):
    """교체 pass가 바꾸지 못한 슬롯(사람 편집·후보 없음 등)은 사람의 일에 머문다.

    교체 대기(RETRYING)와 사람의 일(OPEN)을 스윕마다 오가면 07:45·08:00 요약에 실리는지가
    직전 스윕의 홀짝에 달린다.
    """

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 2, "rereviews": 2}
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)
    hospital = _hospital(id=item.hospital_id)

    tasks._generate_single_content_item(_DB(), item, hospital)
    assert (
        tasks._stored_generation_attempt(item)["retry_class"]
        == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    )
    _expire_swap_wait(item)

    classes = []
    for _sweep in range(8):
        state, code, message = tasks._generate_single_content_item(_DB(), item, hospital)
        attempt = tasks._stored_generation_attempt(item)
        classes.append(attempt["retry_class"])
        assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
        assert "확인해 주세요" in message
        assert generation_incident_control.generation_block_is_terminal(code, item)
        assert not generation_incident_control.scheduled_recovery_owns_blocker(code, item)
    assert classes == [GenerationRetryClass.OPERATOR_REQUIRED.value] * 8
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []


def test_exhausted_uncertain_emergency_buys_no_rereview_and_reaches_operator(
    worker, monkeypatch
):
    """교정 상한을 다 쓴 뒤 남은 UNCERTAIN 응급 지적은 유료 재검수를 더 사지 않고 사람에게 간다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, NERVE_SENTENCE)
    finding = _finding(
        "UNCERTAIN",
        "MEDICAL_SAFETY",
        "다리 힘 빠짐·대소변 장애 같은 응급 신호를 119·응급실 안내 없이 다뤘습니다.",
        NERVE_SENTENCE,
    )
    item = _blocked_item(philosophy, body, finding)
    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {
        "passes": 2,
        "rereviews": 2,
        "exhausted": True,
    }
    calls["item"] = item

    async def review(**kwargs):
        return _review(
            kwargs["content"],
            ContentAiReviewStatus.REVISE,
            (
                ContentAiFinding(
                    ContentAiFindingSeverity.UNCERTAIN,
                    ContentAiFindingKind.MEDICAL_SAFETY,
                    finding["message"],
                    quote=NERVE_SENTENCE,
                ),
            ),
        )

    _install(monkeypatch, calls, propose=_no_provider, review=review)
    hospital = _hospital(id=item.hospital_id)

    tasks._generate_single_content_item(_DB(), item, hospital)
    assert (
        tasks._stored_generation_attempt(item)["retry_class"]
        == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    )
    for _sweep in range(10):
        _expire_swap_wait(item)
        state, code, _message = tasks._generate_single_content_item(_DB(), item, hospital)
        attempt = tasks._stored_generation_attempt(item)
        assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
        assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert calls["reviews"] == [], "상한을 다 쓴 주제는 재검수를 더 사지 않는다"
    assert calls["llm"] == 0 and calls["writer"] == 0
    assert item.essence_check_summary[mc.AUTO_CORRECTION_KEY]["swap_requested_at"]


def test_swapped_topic_that_fails_correction_again_goes_to_operator(worker, monkeypatch):
    """교체 상한(기본 1)을 이미 쓴 슬롯은 교정이 소진되면 곧바로 사람의 일이다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding, history=[{"to_target_id": "x"}])
    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 2, "rereviews": 2}
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    attempt = tasks._stored_generation_attempt(item)
    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None
    assert "확인해 주세요" in message
    assert calls["llm"] == 0 and calls["reviews"] == []


# ── 6. 비용 상한 ─────────────────────────────────────────────────────────────


def test_correction_cap_exceeded_hands_over_without_provider_calls(worker, monkeypatch):
    philosophy, calls = worker
    monkeypatch.setattr(settings, "CONTENT_AUTO_CORRECTION_MAX_PASSES", 0)
    monkeypatch.setattr(settings, "CONTENT_AUTO_TOPIC_SWAP_MAX", 0)
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert generation_incident_control.generation_block_is_terminal(code, item)


def test_rereview_cap_is_separate_from_correction_cap(worker, monkeypatch):
    philosophy, calls = worker
    monkeypatch.setattr(settings, "CONTENT_AUTO_CORRECTION_MAX_REREVIEWS", 1)
    body = _body(NEUTRAL_A, WRONG_TRAINING, UNAPPROVED_PHILOSOPHY)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item

    async def propose(*, targets, **_kwargs):
        return {target.key: ("DELETE", "") for target in targets}

    async def review(**kwargs):
        return _review(
            kwargs["content"],
            ContentAiReviewStatus.REVISE,
            (
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "승인 자료에 없는 진료 원칙입니다.",
                    quote=UNAPPROVED_PHILOSOPHY,
                ),
            ),
        )

    _install(monkeypatch, calls, propose=propose, review=review)
    tasks._generate_single_content_item(_DB(), item, _hospital(id=item.hospital_id))

    assert len(calls["reviews"]) == 1
    state = item.essence_check_summary[mc.AUTO_CORRECTION_KEY]
    assert (state["passes"], state["rereviews"], state["exhausted"]) == (1, 1, True)


def test_cost_cap_defaults_are_settings_with_env_names():
    fields = type(settings).model_fields
    assert fields["CONTENT_AUTO_CORRECTION_MAX_PASSES"].default == 2
    assert fields["CONTENT_AUTO_CORRECTION_MAX_REREVIEWS"].default == 2
    assert fields["CONTENT_AUTO_TOPIC_SWAP_MAX"].default == 1


# ── 3. 검수 우회 불가 ────────────────────────────────────────────────────────


def _gate_item(content: dict, summary: dict):
    return SimpleNamespace(
        title=content["title"],
        body=content["body"],
        meta_description=content["meta_description"],
        faq_question=content["faq_question"],
        faq_answer_summary=content["faq_answer_summary"],
        references_list=content["references_list"],
        content_type=SimpleNamespace(value="DISEASE"),
        essence_check_summary=summary,
        image_url=None,
    )


def test_corrected_body_cannot_publish_without_a_bound_rereview_pass():
    original = _content(_body(NEUTRAL_A, WRONG_TRAINING))
    corrected = dict(original, body=original["body"].replace(WRONG_TRAINING, MAPO_PROFILE_SENTENCE))
    marker = {mc.AUTO_CORRECTION_KEY: {"corrected_sha256": candidate_sha256(corrected)}}
    philosophy = SimpleNamespace(
        id=uuid.uuid4(), status="APPROVED", version=1, avoid_messages=[], must_use_messages=[]
    )

    # (a) 재검수 결과 없이
    no_review = _gate_item(corrected, dict(marker))
    assert assess_content_publication(no_review, philosophy).code == "CONTENT_AI_REVIEW_STALE"

    # (b) 교정 전 본문의 PASS가 남아 있어도
    stale_pass = _gate_item(
        corrected, {**marker, "ai_review": _review(original).payload()}
    )
    assert assess_content_publication(stale_pass, philosophy).code == "CONTENT_AI_REVIEW_STALE"

    # (c) 교정본에 묶인 재검수가 FAIL(REVISE)이면
    failed = _gate_item(
        corrected,
        {
            **marker,
            "ai_review": _review(
                corrected,
                ContentAiReviewStatus.REVISE,
                (
                    ContentAiFinding(
                        ContentAiFindingSeverity.HARD,
                        ContentAiFindingKind.HOSPITAL_FACT,
                        "근거 없음",
                        quote=MAPO_PROFILE_SENTENCE,
                    ),
                ),
            ).payload(),
        },
    )
    assert assess_content_publication(failed, philosophy).code == "CONTENT_AI_HARD_FINDING"

    # (d) 재검수 공급자 장애(UNAVAILABLE)도 통과가 아니다
    unavailable = _gate_item(
        corrected,
        {
            **marker,
            "ai_review": {
                **_review(corrected).payload(),
                "status": "UNAVAILABLE",
                "unavailable_reason": "PROVIDER_ERROR",
            },
        },
    )
    assert assess_content_publication(unavailable, philosophy).code == (
        "CONTENT_AI_REVIEW_UNAVAILABLE"
    )

    # 대조군: 교정본 hash에 묶인 PASS만 AI 검수 게이트를 통과한다(다음 게이트로 넘어간다).
    bound_pass = _gate_item(corrected, {**marker, "ai_review": _review(corrected).payload()})
    assert _blocking_ai_review_state(bound_pass) is None


def test_gate_keeps_the_correction_marker_when_it_records_a_verdict():
    from app.services.content_publication import PublicationAssessment, apply_publication_assessment

    item = SimpleNamespace(essence_check_summary={mc.AUTO_CORRECTION_KEY: {"passes": 1}})
    apply_publication_assessment(
        item,
        PublicationAssessment(
            publishable=False,
            code="X",
            message="m",
            violations=(),
            essence_status="NEEDS_REVIEW",
            essence_summary={"blocking": True},
            philosophy_id=None,
        ),
    )
    assert item.essence_check_summary[mc.AUTO_CORRECTION_KEY] == {"passes": 1}


# ── 4. 서비스에 없는 키워드 제외 ────────────────────────────────────────────


def _gangsimjang(**overrides):
    values = {
        "name": "강심장내과의원",
        "specialties": ["내과"],
        "treatments": ["심장초음파", "홀터검사", "고혈압 진료"],
        "keywords": ["마산 내과", "골밀도검사", "홀터검사"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_keyword_only_service_is_excluded_from_topic_selection():
    bone = SimpleNamespace(name="마산 골밀도검사 가능한 병원 찾기", treatment=None, specialty=None)
    holter = SimpleNamespace(name="홀터검사 받을 수 있는 병원", treatment=None, specialty=None)
    structured = SimpleNamespace(name="뼈 건강 검진", treatment="골밀도검사", specialty=None)

    assert target_names_unoffered_service(bone, _gangsimjang())
    assert target_names_unoffered_service(structured, _gangsimjang())
    assert target_conflicts_with_hospital(bone, _gangsimjang())
    assert not target_fits_hospital(bone, _gangsimjang())
    # 진료 항목에 있는 검사는 그대로 후보다.
    assert target_fits_hospital(holter, _gangsimjang())
    # 병원이 진료 항목에 올리면 다시 후보가 된다.
    assert target_fits_hospital(bone, _gangsimjang(treatments=["골밀도검사", "홀터검사"]))
    # 진료 항목이 비어 있으면 판단할 근거가 없어 막지 않는다(보수적).
    assert target_fits_hospital(bone, _gangsimjang(treatments=[], specialties=[]))
    # 키워드에도 없는 검사는 이 규칙의 대상이 아니다.
    assert target_fits_hospital(bone, _gangsimjang(keywords=["마산 내과"]))


def test_topic_swap_never_picks_a_keyword_only_service(monkeypatch):
    from app.services import content_target_planner

    hospital = _gangsimjang(id=uuid.uuid4())
    bone = SimpleNamespace(
        id=uuid.uuid4(), name="마산 골밀도검사 가능한 병원", treatment=None, specialty=None,
        priority="HIGH", target_month=None, variants=[],
    )
    holter = SimpleNamespace(
        id=uuid.uuid4(), name="홀터검사 받을 수 있는 병원", treatment=None, specialty=None,
        priority="NORMAL", target_month=None, variants=[],
    )

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

        def __iter__(self):
            return iter(self._rows)

    class _PlannerDB:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            return _Rows([bone, holter] if self.calls == 1 else [])

    monkeypatch.setattr(content_target_planner, "_lock_target_planning", lambda *_a: None)
    monkeypatch.setattr(content_target_planner, "apply_structure_to_target", lambda _t: None)
    monkeypatch.setattr(content_target_planner, "_mention_gap_rank", lambda *_a, **_k: {})
    monkeypatch.setattr(content_target_planner, "_content_type_affinity", lambda *_a: 0)
    item = SimpleNamespace(id=uuid.uuid4(), scheduled_date=date(2026, 10, 5), content_type=None)

    chosen = content_target_planner._choose_target(
        _PlannerDB(), item=item, hospital_id=hospital.id, hospital=hospital
    )

    assert chosen is holter


# ── 5. 전날 선행 실행: 기존 23:00 스윕 안, 새 스케줄 없음 ─────────────────────


def test_prerun_lives_in_the_existing_2300_sweep_without_a_new_schedule():
    schedule = celery_app.conf.beat_schedule
    nightly = [
        entry for entry in schedule.values()
        if entry["task"] == "app.workers.tasks.nightly_content_generation"
    ]
    assert len(nightly) == 1
    assert nightly[0]["schedule"] == crontab(hour=23, minute=0)
    # 이 PR은 beat 항목·태스크를 새로 만들지 않는다.
    assert not [
        name for name, entry in schedule.items()
        if "correction" in name or "correction" in entry["task"]
    ]
    assert not [name for name in celery_app.tasks if "correction" in name]
    # 23:00 창의 첫날은 내일이다 — 내일 예정 글을 오늘 밤에 검수·교정한다.
    import arrow

    start, end = tasks._nightly_generation_window(arrow.get(2026, 10, 4, 23, 0, tzinfo="Asia/Seoul"))
    assert start == date(2026, 10, 5) and end >= start


def test_2300_sweep_claims_tomorrows_hard_blocked_post():
    """로더 술어가 '본문은 있고 사실 HARD로 막힌' 내일 글을 집는다 — 그 글이 교정 경로로 간다."""

    from sqlalchemy.dialects import postgresql

    from app.workers import nightly_generation_batch

    compiled = nightly_generation_batch._needs_generation_recovery().compile(
        dialect=postgresql.dialect()
    )
    assert "REVISE" in compiled.params.values() and "blocking" in compiled.params.values()
    eligible = tasks._generation_retry_is_eligible(_DB())
    item = SimpleNamespace(body="저장된 본문", essence_check_summary={}, hospital_id=uuid.uuid4())
    assert eligible(item)


# ── 검수 지적 반영: 문장 경계·한 글자 낱말·서비스 표기·교체 불가 종착 ───────────


BOLD_NEIGHBOR = "허리 통증이 6주 넘게 이어지면 진료가 필요합니다."


@pytest.mark.parametrize(("opener", "closer"), [("**", "**"), ("(", ")"), ('"', '"'), ("“", "”")])
def test_closing_mark_after_period_keeps_the_neighbor_sentence_out_of_scope(opener, closer):
    """`.**`·`.)`·`."`로 끝나는 이웃 문장이 지적 문장과 한 구간으로 묶이지 않는다."""

    neighbor = f"{opener}{BOLD_NEIGHBOR}{closer}"
    body = _body(neighbor, WRONG_TRAINING, NEUTRAL_A)
    content = _content(body)
    plan = mc.plan_corrections(
        content,
        _review_payload(
            content,
            _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
        ),
    )
    sources = mc.approved_fact_texts(_hospital(), _philosophy())
    assert [target.sentence for target in plan.targets] == [WRONG_TRAINING]

    deleted = mc.apply_corrections(content, plan, {})
    assert neighbor in deleted["body"] and WRONG_TRAINING not in deleted["body"]
    mc.verify_correction_scope(content, deleted, plan, sources=sources)

    # 지적 문장과 함께 이웃 문장까지 지운 교정본은 거절된다.
    both_gone = dict(content, body=body.replace(f"{neighbor} {WRONG_TRAINING} ", ""))
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(content, both_gone, plan, sources=sources)

    # 범위 검사는 계획의 구간을 믿지 않는다 — 두 문장을 묶은 구간이 들어와도 거절한다.
    start = body.index(neighbor)
    end = body.index(WRONG_TRAINING) + len(WRONG_TRAINING)
    merged = mc.CorrectionPlan(
        targets=(
            mc.SentenceTarget("S1", "body", start, end, body[start:end], ("수련기관",)),
        ),
        insertions=(),
        uncorrectable=(),
        blocks_on_uncorrectable_hard=False,
    )
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(content, both_gone, merged, sources=sources)


def test_neighbor_without_space_after_closing_mark_is_not_taken():
    """`.**이웃`처럼 경계가 애매하면 문장 하나로 자르지 않고 맡지 않는다(보수적)."""

    body = _body(f"**{BOLD_NEIGHBOR}**이현진 원장은 수련 중 다양한 증례를 보았습니다.", NEUTRAL_A)
    content = _content(body)
    plan = mc.plan_corrections(
        content,
        _review_payload(
            content,
            _finding("HARD", "HOSPITAL_FACT", "근거가 없습니다.", quote="수련 중 다양한 증례를 보았습니다."),
        ),
    )
    assert plan.targets == () and not plan.applicable


def test_quote_crossing_a_heading_line_is_not_taken():
    """인용이 문단을 넘으면 제목 줄이 삭제 구간에 들어간다 — 이 패스는 맡지 않는다."""

    body = _body(NEUTRAL_A, WRONG_TRAINING)
    content = _content(body)
    quote = "수련을 마쳤습니다.\n\n## 내원 전 확인\n통증이 오래가면"
    assert quote in body
    plan = mc.plan_corrections(
        content,
        _review_payload(content, _finding("HARD", "HOSPITAL_FACT", "근거가 없습니다.", quote=quote)),
    )
    assert plan.targets == () and not plan.applicable

    start = body.index(WRONG_TRAINING)
    end = body.index(NEUTRAL_B) + len(NEUTRAL_B)
    forged = mc.CorrectionPlan(
        targets=(mc.SentenceTarget("S1", "body", start, end, body[start:end], ("근거",)),),
        insertions=(),
        uncorrectable=(),
        blocks_on_uncorrectable_hard=False,
    )
    removed = dict(content, body=body[:start].rstrip())
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(
            content, removed, forged, sources=mc.approved_fact_texts(_hospital(), _philosophy())
        )


@pytest.mark.parametrize(
    ("replacement", "term"),
    [
        # 다른 성 — `박`은 승인 낱말 `박사` 같은 낱말의 앞부분이라도 근거가 아니다.
        ("박 원장은 가톨릭대학교 성모병원에서 정형외과 전공의 수련을 마쳤습니다.", "박"),
        ("이현진 원장은 뇌 수련을 마쳤습니다.", "뇌"),
        ("본원은 암 진료를 원칙으로 합니다.", "암"),
    ],
)
def test_single_character_new_fact_is_rejected(replacement, term):
    career = "가톨릭대학교 성모병원 정형외과 전공의 수련, 정형외과 박사"
    sources = mc.approved_fact_texts(_hospital(director_career=career), _philosophy())
    target = mc.SentenceTarget("S1", "body", 0, len(WRONG_TRAINING), WRONG_TRAINING, ("x",))

    assert term in mc.unsupported_terms(replacement, [WRONG_TRAINING, *sources])
    problem = mc.replacement_problem(target, replacement, sources)
    assert problem is not None and problem.startswith("unsupported_terms")
    # 승인 문장과 사실을 싣지 않는 한 글자 낱말(`및`·`등`)은 그대로 쓸 수 있다.
    assert mc.replacement_problem(target, MAPO_PROFILE_SENTENCE, sources) is None
    assert mc.unsupported_terms("도수치료 및 체외충격파 등을 합니다.", sources) == []
    # 근거 자료에 같은 한 글자 낱말로 있으면 근거가 된다.
    assert mc.unsupported_terms("암 검진을 합니다.", ["암 검진을 합니다"]) == []
    assert mc.unsupported_terms("암은 검진을 합니다.", ["암 검진을 합니다"]) == []


def _singihan(**overrides):
    values = {
        "name": "신기한속내과연합의원",
        "specialties": ["내과"],
        "treatments": ["위·대장내시경", "복부초음파"],
        "keywords": ["대구 내과", "위내시경", "대장내시경", "갑상선초음파"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ortho(**overrides):
    values = {
        "name": "마포성모탑정형외과의원",
        "specialties": ["정형외과"],
        "treatments": ["도수치료", "체외충격파", "비수술 척추치료"],
        "keywords": ["허리디스크치료", "관절염치료", "어깨통증치료", "골밀도 검사"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target(name, treatment=None):
    return SimpleNamespace(name=name, treatment=treatment, specialty=None)


def test_offered_service_written_differently_is_not_excluded():
    """표기가 다른 실제 제공 서비스(`위·대장내시경`과 `위내시경`)를 키워드 전용으로 오판하지 않는다."""

    assert target_fits_hospital(_target("위내시경 잘하는 병원"), _singihan())
    assert target_fits_hospital(_target("대구 대장내시경 병원 추천"), _singihan())
    assert target_fits_hospital(_target("위내시경 잘하는 병원"), _singihan(treatments=["위/대장 내시경"]))
    assert target_fits_hospital(_target("위내시경 잘하는 병원"), _singihan(treatments=["위대장내시경"]))
    assert target_fits_hospital(_target("검진", treatment="위 내시경"), _singihan())
    # 진료 항목에 없는 같은 계열 검사는 그대로 제외된다.
    assert not target_fits_hospital(_target("갑상선초음파 가능한 내과"), _singihan())
    # 띄어쓰기만 다른 진료 항목은 같은 검사다.
    assert target_fits_hospital(
        _target("마산 골밀도검사 가능한 병원"), _gangsimjang(treatments=["골밀도 검사"])
    )
    # 강심장 사례는 그대로다.
    assert not target_fits_hospital(_target("마산 골밀도검사 가능한 병원 찾기"), _gangsimjang())
    assert target_fits_hospital(_target("홀터검사 받을 수 있는 병원"), _gangsimjang())


@pytest.mark.parametrize("name", ["허리디스크치료 잘하는 곳", "관절염치료 병원", "어깨통증치료 추천"])
def test_disease_treatment_questions_are_not_excluded(name):
    """질환명에 `치료`를 붙인 질문은 특정 검사·시술이 아니다 — 키워드 전용 규칙의 대상이 아니다."""

    assert target_fits_hospital(_target(name), _ortho())
    assert target_fits_hospital(_target("병원 찾기", treatment=name.split()[0]), _ortho())
    # 같은 병원에서도 진료 항목에 없는 검사는 제외된다.
    assert not target_fits_hospital(_target("골밀도검사 가능한 정형외과"), _ortho())


def test_corrected_topic_left_with_uncorrectable_hard_reaches_operator(worker, monkeypatch):
    """교정 뒤 재검수가 인용 없는 HARD를 남기고 교체 pass도 바꾸지 못하면 사람의 일로 굳는다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, MAPO_PROFILE_SENTENCE)
    finding = _finding("HARD", "HOSPITAL_FACT", "승인 자료에서 심장 초음파를 확인할 수 없습니다.")
    hospital = _hospital()
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    # 교정을 거치지 않은 주제의 인용 없는 HARD는 이 패스가 맡지 않는다(기존 경로).
    fresh = _blocked_item(philosophy, body, finding)
    assert tasks._auto_correct_blocked_body(_DB(), fresh, hospital, philosophy) is None

    item = _blocked_item(philosophy, body, finding)
    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 1, "rereviews": 1}
    calls["item"] = item

    state, code, message = tasks._auto_correct_blocked_body(_DB(), item, hospital, philosophy)
    attempt = tasks._stored_generation_attempt(item)
    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert attempt[AUTO_CORRECTION_EXHAUSTED_KEY] is True and "주제" in message

    # 교체 시각이 지났는데도 그대로다 — 교체 pass가 바꾸지 못했다. 스윕이 몇 번 돌아도 사람의 일이다.
    _expire_swap_wait(item)
    for _sweep in range(3):
        state, code, _message = tasks._auto_correct_blocked_body(_DB(), item, hospital, philosophy)
        attempt = tasks._stored_generation_attempt(item)
        assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
        assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
        assert generation_incident_control.generation_block_is_terminal(code, item)
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []


def test_essence_revalidation_keeps_the_correction_record(monkeypatch):
    """재승인 재검사가 교정 기록을 지우면 글(주제)당 교정 상한이 초기화된다."""

    from app.services import content_publication

    philosophy = _philosophy()
    item = _blocked_item(
        philosophy,
        _body(NEUTRAL_A),
        _finding("HARD", "HOSPITAL_FACT", "근거가 없습니다.", quote=NEUTRAL_A),
    )
    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 2, "rereviews": 2}
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_a: SimpleNamespace(summary={}, essence_status="ALIGNED"),
    )

    content_publication.apply_essence_revalidation(item, philosophy)

    assert item.essence_check_summary[mc.AUTO_CORRECTION_KEY] == {"passes": 2, "rereviews": 2}


# ── 7. 첫 생성 경로(빈 슬롯)도 교정 패스를 거친다 ───────────────────────────────


class _SlotDB(_DB):
    """`_run_generation_item`의 기존 제목 조회는 빈 목록이다."""

    def execute(self, _statement):
        return SimpleNamespace(all=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: []))

    def expire_all(self):
        return None

    def expire(self, _item):
        return None


class _SlotRecorder:
    def __init__(self):
        self.run = SimpleNamespace(id=uuid.uuid4())
        self.states: list = []

    def record(self, _item_id, state, **_kwargs):
        self.states.append(state)

    def item_run(self, *_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())


def _empty_slot(philosophy):
    item = _blocked_item(philosophy, "", _finding("HARD", "HOSPITAL_FACT", "x"))
    for name in ("title", "body", "meta_description", "faq_question", "faq_answer_summary"):
        setattr(item, name, None)
    item.references_list = None
    item.essence_check_summary = None
    return item


def _first_generation(monkeypatch, calls, philosophy, item, body, finding):
    """작가가 `body`를 쓰고 독립 검수가 `finding`으로 막는 첫 생성을 건다."""

    written = _content(body)

    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def ignore(*_args, **_kwargs):
        return None

    async def first_writer(**_kwargs):
        calls["first_writer"] = calls.get("first_writer", 0) + 1
        return dict(written, references=written["references_list"]), SimpleNamespace(
            status=None, summary={}
        )

    def summary(*_args):
        return {
            "blocking": True,
            "findings": [finding["message"]],
            "ai_review": _review_payload(written, finding),
        }

    outcomes: list = []

    def record_outcome(_db, _recorder, _item, _hospital, state, code, message, **_kwargs):
        outcomes.append((state, code, message))

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allowed)
    monkeypatch.setattr(tasks, "prepare_automatic_content_brief_sync", lambda *_a, **_k: {})
    monkeypatch.setattr(tasks, "_generate_with_auto_review", first_writer)
    monkeypatch.setattr(tasks, "_generation_summary", summary)
    monkeypatch.setattr(tasks, "recover_generation_incidents", ignore)
    monkeypatch.setattr(tasks, "open_generation_incident", ignore)
    monkeypatch.setattr(tasks, "_record_generation_batch_outcome", record_outcome)
    calls["item"] = item
    return outcomes


def test_first_generation_hard_finding_is_corrected_before_image(worker, monkeypatch):
    """야간 배치가 빈 슬롯을 처음 쓸 때 HARD를 받아도 교정·재검수 PASS로 이미지·발행 준비까지 간다.

    Fable 3차 검수 차단 사유: 이 분기가 교정 없이 곧바로 종착 인시던트를 열었다.
    """

    philosophy, calls = worker
    item = _empty_slot(philosophy)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    outcomes = _first_generation(monkeypatch, calls, philosophy, item, body, finding)

    async def propose(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", MAPO_PROFILE_SENTENCE)}

    async def review(**kwargs):
        return _review(kwargs["content"])

    _install(monkeypatch, calls, propose=propose, review=review)
    recorder = _SlotRecorder()

    state, code, _message = tasks._run_generation_item(
        _SlotDB(), recorder, item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert calls["first_writer"] == 1 and calls["llm"] == 1 and len(calls["reviews"]) == 1
    assert item.body == body.replace(WRONG_TRAINING, MAPO_PROFILE_SENTENCE)
    # 첫 저장(작가)과 교정 저장. 교정 저장만 발행 이력·사람 편집 글을 막는 술어를 건다.
    assert calls["correction_only"] == [False, True]
    assert calls["image"] == 1, "교정본이 PASS를 받은 뒤에만 이미지를 산다"
    assert recorder.states == [tasks.GenerationItemState.SUCCEEDED]
    assert outcomes == []
    assert _blocking_ai_review_state(item) is None


def test_first_generation_hard_that_cannot_be_fixed_waits_for_topic_swap_not_operator(
    worker, monkeypatch
):
    """첫 생성의 HARD가 교정 상한까지 안 풀리면 사람의 일이 아니라 주제 교체 대기(RETRYING)다.

    이미지는 사지 않는다.
    """

    philosophy, calls = worker
    item = _empty_slot(philosophy)
    sentences = [WRONG_TRAINING, UNAPPROVED_PHILOSOPHY, NEUTRAL_A]
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    outcomes = _first_generation(
        monkeypatch, calls, philosophy, item, _body(*sentences), finding
    )

    async def propose(*, targets, **_kwargs):
        return {target.key: ("DELETE", "") for target in targets}

    async def review(**kwargs):
        content = kwargs["content"]
        remaining = [s for s in sentences if s in content["body"]]
        return _review(
            content,
            ContentAiReviewStatus.REVISE,
            (
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "승인 자료에 없는 문장입니다.",
                    quote=remaining[0],
                ),
            ),
        )

    _install(monkeypatch, calls, propose=propose, review=review)

    state, code, message = tasks._run_generation_item(
        _SlotDB(), _SlotRecorder(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert outcomes == [(state, code, message)], "배치 결과 기록은 저장 본문 경로와 같은 함수다"
    assert len(calls["reviews"]) == settings.CONTENT_AUTO_CORRECTION_MAX_PASSES
    assert calls["image"] == 0
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert generation_incident_control.scheduled_recovery_owns_blocker(code, item)
    assert not generation_incident_control.generation_block_is_terminal(code, item)


def test_first_generation_respects_zero_correction_cap_without_provider_calls(
    worker, monkeypatch
):
    """상한을 0으로 내리면 첫 생성 경로에서도 교정·재검수 공급자 호출이 0회다."""

    philosophy, calls = worker
    monkeypatch.setattr(settings, "CONTENT_AUTO_CORRECTION_MAX_PASSES", 0)
    monkeypatch.setattr(settings, "CONTENT_AUTO_TOPIC_SWAP_MAX", 0)
    item = _empty_slot(philosophy)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    _first_generation(
        monkeypatch, calls, philosophy, item, _body(NEUTRAL_A, WRONG_TRAINING), finding
    )
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, _message = tasks._run_generation_item(
        _SlotDB(), _SlotRecorder(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["image"] == 0
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert generation_incident_control.generation_block_is_terminal(code, item)


# ── 8. PR #207(검수 장애 물러서기·한도)과의 공존 ───────────────────────────────


def _review_unavailable_twice(item, philosophy):
    """#207 경로가 같은 후보의 검수 장애를 두 번 기록한 상태(물러서기 중)."""

    for _ in range(2):
        tasks._remember_generation_attempt(
            _DB(), item, philosophy, "CONTENT_AI_REVIEW_UNAVAILABLE"
        )
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["review_unavailable_total"] == 2
    return attempt


def test_correction_does_not_run_during_review_unavailable_backoff(worker, monkeypatch):
    """저장 판정이 HARD여도 검수 장애 물러서기 중이면 교정 패스(재검수 구매)를 돌리지 않는다.

    물러서기 기록도 그대로 둔다 — 교정 경로가 #207의 계수·기한을 지우지 않는다.
    """

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item
    before = _review_unavailable_twice(item, philosophy)
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.SKIPPED, "CONTENT_AI_REVIEW_UNAVAILABLE")
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []
    assert tasks._stored_generation_attempt(item) == before
    assert mc.AUTO_CORRECTION_KEY not in item.essence_check_summary


def test_correction_runs_after_backoff_and_its_pass_clears_the_review_record(
    worker, monkeypatch
):
    """물러서기가 지나면 교정이 돈다. 교정 계수는 검수 장애 계수에서 시작하지 않는다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item
    _review_unavailable_twice(item, philosophy)
    attempt = item.essence_check_summary["generation_attempt"]
    attempt["observed_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    attempt["next_retry_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    async def propose(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", MAPO_PROFILE_SENTENCE)}

    async def review(**kwargs):
        return _review(kwargs["content"])

    _install(monkeypatch, calls, propose=propose, review=review)

    state, _code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    assert state == tasks.GenerationItemState.SUCCEEDED
    correction = item.essence_check_summary[mc.AUTO_CORRECTION_KEY]
    assert (correction["passes"], correction["rereviews"]) == (1, 1)
    assert "review_unavailable_total" not in correction
    assert tasks._stored_generation_attempt(item) == {}


def test_correction_and_review_unavailable_caps_keep_separate_counters(worker, monkeypatch):
    """교정 재검수가 검수 장애로 끝나면 두 장부가 각자 한 번씩 센다 — 서로의 키를 쓰지 않는다.

    #207 한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`)에 닿아도 교정 기록은 그대로이고,
    교정 상한 소진(주제 교체 대기)도 검수 장애 계수·한도 표시를 만들지 않는다.
    """

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item

    async def propose(*, targets, **_kwargs):
        return {targets[0].key: ("REPLACE", MAPO_PROFILE_SENTENCE)}

    async def unavailable(**kwargs):
        return _review(kwargs["content"], ContentAiReviewStatus.UNAVAILABLE)

    _install(monkeypatch, calls, propose=propose, review=unavailable)
    hospital = _hospital(id=item.hospital_id)

    state, code, _message = tasks._generate_single_content_item(_DB(), item, hospital)

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_REVIEW_UNAVAILABLE")
    correction = dict(item.essence_check_summary[mc.AUTO_CORRECTION_KEY])
    assert (correction["passes"], correction["rereviews"]) == (1, 1)
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["review_unavailable_total"] == 1
    assert attempt["review_unavailable_candidate"] == candidate_sha256(
        tasks._stored_candidate(item)
    )
    assert AUTO_CORRECTION_EXHAUSTED_KEY not in attempt

    # #207 한도까지 같은 후보의 검수 장애가 이어져도 교정 기록은 바뀌지 않는다.
    for _ in range(settings.CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES):
        tasks._remember_generation_attempt(
            _DB(), item, philosophy, "CONTENT_AI_REVIEW_UNAVAILABLE"
        )
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["review_unavailable_cap_reached"] is True
    assert item.essence_check_summary[mc.AUTO_CORRECTION_KEY] == correction
    assert generation_incident_control.review_retries_exhausted(
        "CONTENT_AI_REVIEW_UNAVAILABLE", item
    )

    # 반대로, 교정 상한 소진은 검수 장애 계수·한도 표시를 만들지 않는다.
    other = _blocked_item(philosophy, body, finding)
    other.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 2, "rereviews": 2}
    calls["item"] = other
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)
    tasks._generate_single_content_item(_DB(), other, _hospital(id=other.hospital_id))
    other_attempt = tasks._stored_generation_attempt(other)
    assert other_attempt[AUTO_CORRECTION_EXHAUSTED_KEY] is True
    assert "review_unavailable_total" not in other_attempt
    assert "review_unavailable_cap_reached" not in other_attempt
    assert not generation_incident_control.review_retries_exhausted(
        "CONTENT_AI_HARD_FINDING", other
    )


def test_review_only_operator_retry_never_runs_the_correction_pass(worker, monkeypatch):
    """운영자 재검수 전용 실행(main #205)은 저장된 HARD를 교정하지 않는다 — 교정은 자동 스윕의 몫이다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id), review_only=True
    )

    assert state in {tasks.GenerationItemState.FAILED, tasks.GenerationItemState.SKIPPED}
    assert code is not None
    assert calls["llm"] == 0 and calls["writes"] == []
    assert mc.AUTO_CORRECTION_KEY not in item.essence_check_summary


# ── 9. 보호 글: 발행·사람 편집 글과 필수 문구는 고치지 않는다 ───────────────────


@pytest.mark.parametrize(
    "protect",
    [
        {"status": SimpleNamespace(value="PUBLISHED")},
        {"status": SimpleNamespace(value="WITHHELD")},
        {"status": SimpleNamespace(value="REJECTED"), "first_published_at": datetime.now(UTC)},
        {"first_published_at": datetime.now(UTC)},
        {"published_at": datetime.now(UTC)},
        {"human_edited_at": datetime.now(UTC)},
    ],
)
def test_correction_never_touches_published_or_human_edited_posts(worker, monkeypatch, protect):
    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    for name, value in protect.items():
        setattr(item, name, value)
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    assert not mc.correction_allowed_for(item)
    result = tasks._auto_correct_blocked_body(
        _DB(), item, _hospital(id=item.hospital_id), philosophy
    )

    assert result is None
    assert item.body == body
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []
    assert mc.AUTO_CORRECTION_KEY not in item.essence_check_summary


def test_correction_write_back_sql_excludes_published_and_human_edited_rows():
    """교정 저장 UPDATE 자체가 발행 이력·사람 편집·DRAFT/READY 밖의 행을 0행으로 만든다."""

    from sqlalchemy.dialects import postgresql

    from app.workers.nightly_generation_batch import write_back_generated_content

    statements = []

    class _Capture:
        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(rowcount=0)

    for correction_only in (False, True):
        write_back_generated_content(
            _Capture(),
            item_id=uuid.uuid4(),
            expected_revision=3,
            values={"body": "x"},
            correction_only=correction_only,
        )
    plain, guarded = (
        str(statement.compile(dialect=postgresql.dialect())) for statement in statements
    )
    for clause in (
        "content_items.first_published_at IS NULL",
        "content_items.published_at IS NULL",
        "content_items.human_edited_at IS NULL",
    ):
        assert clause in guarded and clause not in plain


MUST_USE_TWO = "정확한 진단이 먼저입니다. 필요한 치료만 권합니다."


def test_must_use_sentence_is_never_a_correction_target():
    """여러 문장짜리 필수 문구의 한 문장도 지적 대상이 아니다(인용이 그 문장 하나여도)."""

    body = _body(NEUTRAL_A, MUST_USE_TWO, WRONG_TRAINING)
    content = _content(body)
    review = _review_payload(
        content,
        _finding("HARD", "HOSPITAL_FACT", "근거 없는 문장입니다.", "필요한 치료만 권합니다."),
    )

    plan = mc.plan_corrections(content, review, must_use_messages=[MUST_USE_TWO])

    assert not plan.targets


def test_scope_check_rejects_a_correction_that_breaks_a_must_use_message():
    body = _body(NEUTRAL_A, MUST_USE_TWO, WRONG_TRAINING)
    content = _content(body)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    plan = mc.plan_corrections(
        content, _review_payload(content, finding), must_use_messages=[MUST_USE_TWO]
    )
    assert plan.targets
    corrected = mc.apply_corrections(
        content,
        plan,
        {plan.targets[0].key: mc.SentenceDecision(plan.targets[0].key, None, "test")},
    )
    mc.verify_correction_scope(content, corrected, plan, sources=[], must_use_messages=[MUST_USE_TWO])

    broken = dict(corrected, body=corrected["body"].replace("필요한 치료만 권합니다.", ""))
    with pytest.raises(mc.CorrectionScopeError):
        mc.verify_correction_scope(
            content, broken, plan, sources=[], must_use_messages=[MUST_USE_TWO]
        )


def test_emergency_template_is_not_inserted_inside_a_must_use_message():
    body = _body(NEUTRAL_A, MUST_USE_TWO)
    content = _content(body)
    finding = _finding(
        "UNCERTAIN",
        "MEDICAL_SAFETY",
        "다리 힘 빠짐 같은 응급 신호에 119 안내가 없습니다.",
        "정확한 진단이 먼저입니다.",
    )
    plan = mc.plan_corrections(
        content, _review_payload(content, finding), must_use_messages=[MUST_USE_TWO]
    )
    assert plan.insertions
    corrected = mc.apply_corrections(content, plan, {})
    from app.services.must_use_verbatim import appears_as_standalone_sentence

    assert appears_as_standalone_sentence(corrected["body"], MUST_USE_TWO)
    mc.verify_correction_scope(content, corrected, plan, sources=[], must_use_messages=[MUST_USE_TWO])


# ── 10. Fable 3차 NIT ─────────────────────────────────────────────────────────


def test_ambiguous_quote_found_in_two_sentences_is_not_taken():
    """짧은 인용이 두 문장에 있으면 어느 문장인지 모르므로 고치지 않는다(지적 안 한 문장 보호)."""

    first = "본원은 수술 후 관리를 책임집니다."
    second = "본원은 수술 후 관리를 책임집니다만 일정은 따로 안내합니다."
    for body in (_body(first, NEUTRAL_A, second), _body(first, NEUTRAL_A, first)):
        content = _content(body)
        review = _review_payload(
            content, _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없습니다.", "수술 후 관리를")
        )
        plan = mc.plan_corrections(content, review)
        assert not plan.targets and not plan.applicable
    # 다른 칸(본문·FAQ 요약)에 같은 인용이 있어도 맡지 않는다.
    content = _content(_body(first, NEUTRAL_A), faq_answer_summary=first)
    review = _review_payload(content, _finding("HARD", "HOSPITAL_FACT", "없습니다.", first))
    assert not mc.plan_corrections(content, review).targets
    # 한 곳에만 있으면 그대로 맡는다.
    content = _content(_body(first, NEUTRAL_A))
    review = _review_payload(content, _finding("HARD", "HOSPITAL_FACT", "없습니다.", first))
    assert [target.sentence for target in mc.plan_corrections(content, review).targets] == [first]


def test_spaced_keyword_only_service_is_excluded():
    """띄어 쓴 '골밀도 검사'도 키워드에만 있는 검사면 제외한다."""

    assert not target_fits_hospital(_target("마산 골밀도 검사 가능한 병원"), _gangsimjang())
    assert target_fits_hospital(
        _target("마산 골밀도 검사 가능한 병원"), _gangsimjang(treatments=["골밀도검사"])
    )
    # 앞말이 검사 이름이 아닌 띄어쓰기("허리 수술")는 키워드에 붙여 쓴 전체 이름이 있을 때만 대상이다.
    assert target_fits_hospital(_target("허리 수술 안 하는 병원"), _ortho())


@pytest.mark.parametrize(
    ("name", "treatments", "keywords"),
    [
        ("위내시경검사 잘하는 곳", ["위·대장내시경"], ["위내시경검사"]),
        ("복부초음파 가능한 병원", ["초음파 검사"], ["복부초음파"]),
        ("비수술 치료 잘하는 곳", ["도수치료"], ["비수술치료"]),
        ("비수술치료 병원", ["도수치료"], ["비수술치료"]),
    ],
)
def test_offered_or_non_service_names_are_not_excluded(name, treatments, keywords):
    hospital = _singihan(treatments=treatments, keywords=keywords)
    assert target_fits_hospital(_target(name), hospital)


def test_cost_cap_settings_of_both_prs_coexist():
    """#207의 검수 장애 재검수 한도와 이 PR의 교정·재검수·교체 상한이 모두 설정으로 남는다."""

    assert settings.CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES == 6
    assert (
        settings.CONTENT_AUTO_CORRECTION_MAX_PASSES,
        settings.CONTENT_AUTO_CORRECTION_MAX_REREVIEWS,
        settings.CONTENT_AUTO_TOPIC_SWAP_MAX,
    ) == (2, 2, 1)


# ── 분량 하한: 교정이 글을 1,800자 아래로 줄이면 거절한다 ─────────────────────────


def _long_body(plain_chars: int) -> str:
    """순수 글자 수가 plain_chars인 본문 — 맨 앞 절 끝에 지적 문장(WRONG_TRAINING)이 있다."""
    from app.services.content_engine import body_plain_length

    base = body_plain_length(_body("가.", WRONG_TRAINING))
    # 지적 문장이 앞 문장과 붙지 않게 마침표로 끝낸다.
    return _body("가" * (plain_chars - base + 1) + ".", WRONG_TRAINING)


def _wrong_training_plan(content: dict):
    return mc.plan_corrections(
        content,
        _review_payload(
            content,
            _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
        ),
    )


def test_deleting_a_sentence_below_the_body_minimum_is_a_rejected_correction():
    from app.services.content_engine import CONTENT_BODY_MIN_CHARS, body_plain_length

    content = _content(_long_body(CONTENT_BODY_MIN_CHARS + 20))
    assert body_plain_length(content["body"]) >= CONTENT_BODY_MIN_CHARS
    plan = _wrong_training_plan(content)
    deleted = mc.apply_corrections(
        content, plan, {plan.targets[0].key: mc.SentenceDecision(plan.targets[0].key, None, "d")}
    )
    assert body_plain_length(deleted["body"]) < CONTENT_BODY_MIN_CHARS

    with pytest.raises(mc.CorrectionScopeError, match="correction rejected: body below"):
        mc.verify_correction_scope(content, deleted, plan, sources=[])


def test_correction_that_keeps_the_body_at_the_minimum_is_accepted():
    from app.services.content_engine import CONTENT_BODY_MIN_CHARS, body_plain_length

    content = _content(_long_body(CONTENT_BODY_MIN_CHARS + 400))
    plan = _wrong_training_plan(content)
    deleted = mc.apply_corrections(
        content, plan, {plan.targets[0].key: mc.SentenceDecision(plan.targets[0].key, None, "d")}
    )
    assert body_plain_length(deleted["body"]) >= CONTENT_BODY_MIN_CHARS
    mc.verify_correction_scope(content, deleted, plan, sources=[])


async def test_pass_that_would_shorten_the_body_below_the_minimum_buys_no_rereview():
    from app.services.content_engine import CONTENT_BODY_MIN_CHARS

    content = _content(_long_body(CONTENT_BODY_MIN_CHARS + 20))
    review = _review_payload(
        content,
        _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", quote=WRONG_TRAINING),
    )

    async def no_llm(**_kwargs):
        return {}

    async def reviewer(**_kwargs):
        raise AssertionError("분량 미달 교정본은 재검수로 넘어가면 안 된다")

    outcome = await mc.run_minimal_correction(
        hospital=_hospital(), philosophy=_philosophy(), content=content, review=review,
        content_brief=None, must_use_messages=[], state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=no_llm, review=reviewer),
    )

    assert outcome.status == "REJECTED"
    assert outcome.content is None
