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
    assert "15" in mc.unsupported_terms(
        "이현진 원장은 15년 동안 정형외과 진료를 했습니다.", [WRONG_TRAINING, *sources]
    )
    assert any(
        term.startswith("연세")
        for term in mc.unsupported_terms(
            "이현진 원장은 연세세브란스병원에서 수련했습니다.", [WRONG_TRAINING, *sources]
        )
    )


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
    calls = {"llm": 0, "reviews": [], "writes": [], "image": 0, "writer": 0}

    def write_back(_db, *, item_id, expected_revision, expected_claim_token, values):
        calls["writes"].append(dict(values))
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
