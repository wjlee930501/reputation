"""공개된 글의 사후 검수 FLAGGED 자동 교정(published mode).

2026-10-08 운영에서 사후 검수 스윕이 공개 글 20편을 모두 FLAGGED(차단 지적 86건)로 표시했다.
대표 결정으로 기존 최소 교정 패스(`content_minimal_correction`)를 공개 글에도 적용한다. 이 파일은
그 경로의 안전 보증을 고정한다 — 교정본은 그 hash에 묶인 독립 재검수 PASS가 있을 때만 살아 있는 행에
쓰이고, 제목은 절대 바뀌지 않으며, 동시 편집은 덮이지 않고, 시도에는 상한이 있다.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings, settings
from app.models.content import ContentStatus
from app.services import content_minimal_correction as mc
from app.services import content_publication
from app.services import published_correction as pc
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.workers import post_publish_ai_review as sweep
from tests.test_ai_review_hash_binding import _item

NOW = datetime(2026, 10, 9, 3, 10, tzinfo=timezone.utc)
NEUTRAL_A = "허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다."
NEUTRAL_B = "통증이 오래가면 생활 습관과 자세를 함께 살펴봅니다."
BAD = "본원은 국내 최초로 도입한 장비로 모든 환자의 통증을 줄입니다."
MUST_USE = "본원은 과잉진료를 피하고 현재 상태에 맞는 최소한의 개입을 원칙으로 합니다."
TITLE = "허리 통증, 언제 병원에 가야 할까요?"


def _body(*sentences: str) -> str:
    return "## 허리 통증 안내\n" + " ".join(sentences) + "\n\n## 내원 전 확인\n" + NEUTRAL_B


def _flagged_item(*, quote=BAD, human_edited=False, **overrides):
    body = _body(NEUTRAL_A, quote if quote != TITLE else NEUTRAL_A, MUST_USE)
    item = _item(
        status=ContentStatus.PUBLISHED,
        title=TITLE,
        body=body,
        meta_description="허리 통증의 내원 시점을 안내합니다.",
        faq_question="허리 통증은 언제 병원에 가야 하나요?",
        faq_answer_summary="통증이 오래가거나 신경 증상이 있으면 진료가 필요합니다.",
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        first_published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        body_updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        human_edited_at=datetime(2026, 9, 5, tzinfo=timezone.utc) if human_edited else None,
        content_revision=3,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
        last_reviewed_philosophy_id=None,
        id=uuid.uuid4(),
        **overrides,
    )
    finding = {
        "severity": "HARD",
        "kind": "HOSPITAL_FACT",
        "message": "승인되지 않은 병원 주장입니다.",
        "target": "CANDIDATE_TEXT",
        "quote": quote,
    }
    item.essence_check_summary = {
        "generation_provenance": {"model": "x"},
        sweep.POST_PUBLISH_FLAG_KEY: {
            "status": "FLAGGED",
            "candidate_sha256": candidate_sha256(item),
            "checked_at": NOW.isoformat(),
            "findings": [finding["message"]],
            "structured_findings": [finding],
        },
    }
    return item


def _content(item) -> dict:
    return sweep._candidate_content(item)


def _pass_review(content: dict) -> ContentAiReview:
    return ContentAiReview(
        status=ContentAiReviewStatus.PASS,
        confidence=0.95,
        findings=(),
        summary="재검수",
        model="m",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
        provider_attempted=True,
    )


def _blocked_review(content: dict) -> ContentAiReview:
    return ContentAiReview(
        status=ContentAiReviewStatus.REVISE,
        confidence=0.9,
        findings=(
            ContentAiFinding(
                ContentAiFindingSeverity.HARD, ContentAiFindingKind.HOSPITAL_FACT, "여전히 근거 없음"
            ),
        ),
        summary="재검수",
        model="m",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
        provider_attempted=True,
    )


def _unavailable_review(content: dict) -> ContentAiReview:
    return ContentAiReview(
        status=ContentAiReviewStatus.UNAVAILABLE,
        confidence=0.0,
        findings=(),
        summary="",
        model="m",
        candidate_sha256=candidate_sha256(content),
        coverage={},
    )


def _hospital():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트정형외과",
        director_name="김원장",
        director_career="",
        treatments=["도수치료"],
        specialties=["정형외과"],
        keywords=[],
        region=["서울"],
        slug="test",
        aeo_domain=None,
    )


def _philosophy(must_use=()):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="APPROVED",
        positioning_statement="과잉진료를 피하고 최소한의 개입을 원칙으로 합니다.",
        doctor_voice="",
        content_principles=[],
        treatment_narratives=[],
        must_use_messages=list(must_use),
    )


async def _delete_everything(**kwargs):
    return {target.key: ("DELETE", "") for target in kwargs["targets"]}


def _deps(review):
    async def reviewer(**kwargs):
        return review(kwargs["content"])

    return mc.CorrectionDependencies(propose=_delete_everything, review=reviewer)


async def _run(item, review=_pass_review, must_use=(), state=None):
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    return await mc.run_published_correction(
        hospital=_hospital(),
        philosophy=_philosophy(must_use),
        content=_content(item),
        review={"findings": marker["structured_findings"]},
        content_brief=None,
        must_use_messages=list(must_use),
        state=state,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=_deps(review),
    )


def _aligned(monkeypatch):
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_a: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )


def _snapshot(item):
    return copy.deepcopy(vars(item))


def _apply(item, outcome, *, sha=None, revision=None):
    return pc.apply_published_correction(
        item,
        outcome,
        base_sha=sha or item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["candidate_sha256"],
        base_revision=revision if revision is not None else item.content_revision,
        philosophy=_philosophy(),
        now=NOW,
    )


# ── 1. 공개 모드의 허용 범위 ──────────────────────────────────────────────────


def test_published_mode_allows_published_even_when_human_edited_but_draft_mode_does_not():
    item = _flagged_item(human_edited=True)

    # 발행 전 모드는 여전히 공개·사람 편집 글을 거절한다.
    assert mc.correction_allowed_for(item) is False
    assert mc.published_correction_allowed_for(item) is True
    item.status = ContentStatus.WITHHELD
    assert mc.published_correction_allowed_for(item) is False
    item.status = ContentStatus.DRAFT
    assert mc.published_correction_allowed_for(item) is False


async def test_body_finding_is_corrected_and_title_is_untouched():
    item = _flagged_item()

    outcome = await _run(item)

    assert outcome.status == "PASS"
    assert BAD not in outcome.content["body"]
    assert outcome.content["title"] == TITLE
    assert outcome.review.candidate_sha256 == candidate_sha256(outcome.content)


async def test_title_finding_is_left_for_the_human_without_any_provider_call():
    item = _flagged_item(quote=TITLE)
    calls = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return {}

    async def reviewer(**kwargs):
        calls.append(kwargs)
        return _pass_review(kwargs["content"])

    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    outcome = await mc.run_published_correction(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=_content(item),
        review={"findings": marker["structured_findings"]},
        content_brief=None,
        must_use_messages=[],
        state=None,
        limits=mc.CorrectionLimits(max_passes=2, max_rereviews=2),
        dependencies=mc.CorrectionDependencies(propose=spy, review=reviewer),
    )

    assert outcome.status == "NEEDS_HUMAN"
    assert outcome.reason == "TITLE_FINDING"
    assert outcome.content is None and calls == []


async def test_title_finding_mixed_with_body_finding_is_not_half_corrected():
    """제목을 못 고치므로 재검수가 다시 막는다 — 돈을 쓰지 않고 사람에게 넘긴다."""
    item = _flagged_item()
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    marker["structured_findings"].append(
        {"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "제목 과장", "quote": TITLE}
    )

    outcome = await _run(item)

    assert (outcome.status, outcome.reason) == ("NEEDS_HUMAN", "TITLE_FINDING")


async def test_must_use_sentence_is_protected():
    item = _flagged_item(quote=MUST_USE)

    outcome = await _run(item, must_use=[MUST_USE])

    assert outcome.status == "NEEDS_HUMAN"
    assert outcome.content is None


async def test_must_use_sentence_survives_correction_of_another_sentence():
    item = _flagged_item()

    outcome = await _run(item, must_use=[MUST_USE])

    assert outcome.status == "PASS"
    assert MUST_USE in outcome.content["body"]


async def test_non_pass_rereview_is_not_a_success():
    item = _flagged_item()

    outcome = await _run(item, review=_blocked_review)

    assert outcome.status != "PASS"


# ── 2. 살아 있는 행에 쓰기(CAS) ───────────────────────────────────────────────


async def test_pass_bound_to_candidate_hash_is_written_like_a_published_patch(monkeypatch):
    _aligned(monkeypatch)
    item = _flagged_item(human_edited=True)
    before_identity = (item.published_at, item.first_published_at, item.title, item.human_edited_at)
    outcome = await _run(item)

    status, reason = _apply(item, outcome)

    assert (status, reason) == ("CORRECTED", None)
    assert BAD not in item.body and MUST_USE in item.body
    assert item.content_revision == 4  # PATCH와 같이 공개 판의 revision을 올린다
    assert item.body_updated_at == NOW
    assert (item.published_at, item.first_published_at, item.title, item.human_edited_at) == before_identity
    summary = item.essence_check_summary
    assert summary["ai_review"]["candidate_sha256"] == candidate_sha256(item)
    assert summary["ai_review"]["status"] == "PASS"
    assert item.post_publish_reviewed_at == NOW
    assert item.post_publish_reviewed_by == "system:ai-correction"
    assert sweep.POST_PUBLISH_FLAG_KEY not in summary and summary["generation_provenance"] == {"model": "x"}
    record = summary[mc.AUTO_CORRECTION_KEY]
    assert record["mode"] == "POST_PUBLISH"
    assert record["human_edited"] is True
    assert record["before_sha256"] != record["after_sha256"] == candidate_sha256(item)
    assert record["corrected_sha256"] == candidate_sha256(item)
    assert record["sentences"][0]["before"] == BAD
    # 게이트도 같은 기록을 본다 — 교정본에 묶인 PASS가 있으니 공개 표면이 숨기지 않는다.
    assert content_publication.public_candidate_review_safe(item) is True


async def test_non_pass_outcome_leaves_the_live_row_untouched(monkeypatch):
    _aligned(monkeypatch)
    item = _flagged_item()
    outcome = await _run(item, review=_blocked_review)
    before = _snapshot(item)

    status, _reason = _apply(item, outcome)

    assert status == "REJECTED"
    assert vars(item) == before


async def test_pass_for_a_different_hash_is_not_written(monkeypatch):
    _aligned(monkeypatch)
    item = _flagged_item()
    outcome = await _run(item)
    outcome.review = _pass_review({**outcome.content, "body": "다른 본문입니다."})
    before = _snapshot(item)

    assert _apply(item, outcome)[0] == "REJECTED"
    assert vars(item) == before


@pytest.mark.parametrize("mutation", ["revision", "body", "status", "marker", "reviewed"])
async def test_cas_conflict_aborts_without_overwriting(monkeypatch, mutation):
    _aligned(monkeypatch)
    item = _flagged_item()
    outcome = await _run(item)
    base_sha = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["candidate_sha256"]
    base_revision = item.content_revision
    if mutation == "revision":
        item.content_revision += 1
    elif mutation == "body":
        item.body = item.body + " 사람이 그 사이에 덧붙인 문장입니다."
    elif mutation == "status":
        item.status = ContentStatus.WITHHELD
    elif mutation == "marker":
        del item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    else:
        item.post_publish_reviewed_at = NOW
    before = _snapshot(item)

    status, _reason = _apply(item, outcome, sha=base_sha, revision=base_revision)

    assert status == "CONFLICT"
    assert vars(item) == before


async def test_forbidden_expression_in_corrected_text_is_rejected(monkeypatch):
    _aligned(monkeypatch)
    item = _flagged_item()
    outcome = await _run(item)
    outcome.content = {**outcome.content, "meta_description": "국내 최고의 병원입니다."}
    outcome.review = _pass_review(outcome.content)
    before = _snapshot(item)

    status, reason = _apply(item, outcome)

    assert status == "REJECTED" and reason == "forbidden_expression"
    assert vars(item) == before


async def test_changed_title_is_never_written(monkeypatch):
    _aligned(monkeypatch)
    item = _flagged_item()
    outcome = await _run(item)
    outcome.content = {**outcome.content, "title": "바뀐 제목"}
    outcome.review = _pass_review(outcome.content)
    before = _snapshot(item)

    status, reason = _apply(item, outcome)

    assert (status, reason) == ("REJECTED", "title_changed")
    assert vars(item) == before and item.title == TITLE


async def test_correction_that_would_hide_the_post_is_not_written(monkeypatch):
    """교정본이 게이트(예: 승인 기준 정렬)를 통과하지 못하면 공개 글을 숨기게 된다 — 쓰지 않는다."""
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_a: SimpleNamespace(status="NEEDS_REVIEW", summary={"blocking": True}),
    )
    item = _flagged_item()
    outcome = await _run(item)
    before = _snapshot(item)

    status, reason = _apply(item, outcome)

    assert status == "REJECTED" and reason.startswith("not_publishable")
    assert vars(item) == before


# ── 3. 마커: 구조화된 지적·시도 상한 ──────────────────────────────────────────


def test_new_flag_persists_structured_blocking_findings():
    item = _item(status=ContentStatus.PUBLISHED, post_publish_reviewed_at=None)
    finding = ContentAiFinding(
        ContentAiFindingSeverity.HARD,
        ContentAiFindingKind.HOSPITAL_FACT,
        "승인되지 않은 병원 주장입니다.",
        quote="본원은 국내 최초입니다.",
    )
    soft = ContentAiFinding(ContentAiFindingSeverity.SOFT, ContentAiFindingKind.STYLE, "문체")
    review = ContentAiReview(
        status=ContentAiReviewStatus.REVISE,
        confidence=0.9,
        findings=(finding, soft),
        summary="s",
        model="m",
        candidate_sha256=candidate_sha256(item),
        coverage=candidate_review_coverage(item),
    )

    assert sweep.apply_review_outcome(item, review, now=NOW) == "FLAGGED"

    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    assert marker["structured_findings"] == [finding.payload()]
    assert marker["structured_findings"][0]["quote"] == "본원은 국내 최초입니다."
    assert marker["findings"] == ["승인되지 않은 병원 주장입니다."]


def test_string_only_marker_has_no_structure_so_nothing_is_guessed():
    legacy = {"status": "FLAGGED", "findings": ["승인되지 않은 병원 주장입니다."], "candidate_sha256": "x"}

    assert pc.structured_review_from_marker(legacy) is None
    assert pc.structured_review_from_marker({**legacy, "structured_findings": []}) is None
    assert pc.structured_review_from_marker({**legacy, "structured_findings": ["문자열"]}) is None
    structured = {**legacy, "structured_findings": [{"severity": "HARD", "message": "m", "quote": "q"}]}
    assert pc.structured_review_from_marker(structured) == {"findings": structured["structured_findings"]}


def test_correction_attempts_are_capped_per_post():
    assert pc.correction_exhausted({}, max_passes=2) is False
    assert pc.correction_exhausted({"correction": {"passes": 1}}, max_passes=2) is False
    assert pc.correction_exhausted({"correction": {"passes": 2}}, max_passes=2) is True
    assert pc.correction_exhausted({"correction": {"finished": True, "rules_version": pc.CORRECTION_RULES_VERSION}}, max_passes=2) is True
    assert pc.correction_exhausted({}, max_passes=0) is True


def test_rereview_of_a_flagged_post_keeps_its_attempt_history():
    item = _flagged_item()
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    marker["correction"] = {"passes": 1, "finished": False}
    review = ContentAiReview(
        status=ContentAiReviewStatus.REVISE,
        confidence=0.9,
        findings=(
            ContentAiFinding(
                ContentAiFindingSeverity.HARD,
                ContentAiFindingKind.HOSPITAL_FACT,
                "m",
                quote="q문장입니다.",
            ),
        ),
        summary="s",
        model="m",
        candidate_sha256=candidate_sha256(item),
        coverage=candidate_review_coverage(item),
    )

    assert sweep.apply_review_outcome(item, review, now=NOW) == "FLAGGED"

    new_marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    assert new_marker["correction"] == {"passes": 1, "finished": False}
    assert new_marker["structured_findings"][0]["quote"] == "q문장입니다."


def test_settings_for_the_correction_budget():
    assert settings.POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP == 20
    assert settings.POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES == 2
    with pytest.raises(ValueError):
        Settings(POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP=-1)
    with pytest.raises(ValueError):
        Settings(POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES=-1)


def test_flagged_selection_skips_finished_markers_and_unpublished_rows():
    sql = str(sweep._flagged_stmt(10).compile(compile_kwargs={"literal_binds": True}))

    assert "post_publish_reviewed_at IS NULL" in sql
    assert "finished" in sql
    assert "ORDER BY" in sql


# ── 4. 스윕 드라이버 ─────────────────────────────────────────────────────────


class _Result:
    def __init__(self, item):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class _FakeDb:
    def __init__(self, item):
        self.item = item
        self.commits = 0
        self.rollbacks = 0

    def execute(self, _stmt):
        return _Result(self.item)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def get_bind(self):
        return None


class _Hooks:
    def __init__(self):
        self.indexnow = []
        self.intents = []
        self.revalidated = []
        self.recovered = []
        self.incidents = []


@pytest.fixture
def hooks(monkeypatch):
    hooks = _Hooks()
    monkeypatch.setattr(
        sweep.indexnow, "enqueue_content_published_sync", lambda db, **kw: hooks.indexnow.append(kw)
    )
    monkeypatch.setattr(
        sweep, "enqueue_public_surface_intent", lambda db, hospital, **kw: hooks.intents.append(kw)
    )

    async def revalidated(slug, content_id, **_kw):
        hooks.revalidated.append(content_id)
        return True

    monkeypatch.setattr(sweep, "trigger_content_site_revalidate_safe", revalidated)
    monkeypatch.setattr(sweep, "_recover_flag_incident", lambda item, *a, **k: hooks.recovered.append(item.id))
    monkeypatch.setattr(
        sweep, "_open_flag_incident", lambda item, hospital, run_async, **k: hooks.incidents.append(k)
    )
    monkeypatch.setattr(sweep, "write_audit_log_sync", lambda *a, **k: None)
    return hooks


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patch_providers(monkeypatch, *, review=_pass_review, propose=_delete_everything):
    async def reviewer(**kwargs):
        return review(kwargs["content"])

    monkeypatch.setattr(sweep, "propose_sentence_corrections", propose)
    monkeypatch.setattr(sweep, "review_generated_content", reviewer)


def _process(item, philosophy=None):
    return sweep.process_flagged_post(
        _FakeDb(item),
        item,
        _hospital(),
        philosophy or _philosophy([MUST_USE]),
        run_async=_run_async,
        now=NOW,
    )


def test_sweep_writes_a_passing_correction_and_publishes_side_effects(monkeypatch, hooks):
    _aligned(monkeypatch)
    _patch_providers(monkeypatch)
    item = _flagged_item()
    db = _FakeDb(item)

    result = sweep.process_flagged_post(
        db, item, _hospital(), _philosophy([MUST_USE]), run_async=_run_async, now=NOW
    )

    assert result == "CORRECTED"
    assert BAD not in item.body and db.commits == 1
    # PATCH와 같은 공개 표면 갱신: 색인 intent + 사이트 재검증 intent, 커밋 뒤 즉시 재검증.
    assert [entry["revision"] for entry in hooks.indexnow] == [4]
    assert hooks.intents == [{"content_ids": [item.id]}]
    assert hooks.revalidated == [item.id]
    assert hooks.recovered == [item.id]
    assert hooks.incidents == []


def test_sweep_reviews_the_candidate_with_the_post_publish_model(monkeypatch, hooks):
    _aligned(monkeypatch)
    models = []

    async def reviewer(**kwargs):
        models.append(kwargs.get("model"))
        return _pass_review(kwargs["content"])

    monkeypatch.setattr(sweep, "propose_sentence_corrections", _delete_everything)
    monkeypatch.setattr(sweep, "review_generated_content", reviewer)

    _process(_flagged_item())

    assert models == [settings.POST_PUBLISH_AI_REVIEW_MODEL]


def test_failed_rereview_keeps_live_post_flag_and_incident_and_records_the_attempt(monkeypatch, hooks):
    _aligned(monkeypatch)
    _patch_providers(monkeypatch, review=_blocked_review)
    item = _flagged_item()
    before_body = item.body

    result = _process(item)

    assert result == "BLOCKED"
    assert item.body == before_body and item.content_revision == 3
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    assert marker["status"] == "FLAGGED"
    assert marker["correction"]["finished"] is True
    assert marker["correction"]["passes"] >= 1
    assert hooks.indexnow == [] and hooks.intents == [] and hooks.revalidated == [] and hooks.recovered == []
    assert hooks.incidents and hooks.incidents[-1]["needs_human_reason"]
    # 다음 실행은 이 글에 더 쓰지 않는다.
    assert pc.correction_exhausted(marker, max_passes=2) is True


def test_unavailable_rereview_records_nothing_so_tomorrow_retries(monkeypatch, hooks):
    _aligned(monkeypatch)
    _patch_providers(monkeypatch, review=_unavailable_review)
    item = _flagged_item()
    before = _snapshot(item)

    result = _process(item)

    assert result == "UNAVAILABLE"
    assert vars(item) == before
    assert hooks.incidents == [] and hooks.recovered == []


def test_title_only_flag_stays_open_for_the_human(monkeypatch, hooks):
    _aligned(monkeypatch)
    _patch_providers(monkeypatch, propose=None)  # 공급자를 부르면 실패한다
    item = _flagged_item(quote=TITLE)
    before_title, before_body = item.title, item.body

    result = _process(item)

    assert result == "NEEDS_HUMAN"
    assert (item.title, item.body) == (before_title, before_body)
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    assert marker["correction"]["finished"] is True
    assert marker["correction"]["reason"] == "TITLE_FINDING"
    assert hooks.incidents[-1]["needs_human_reason"] == "TITLE_FINDING"
    assert hooks.recovered == []


def test_concurrent_edit_during_correction_aborts_the_write(monkeypatch, hooks):
    _aligned(monkeypatch)
    item = _flagged_item()

    async def edit_midway(**kwargs):
        item.content_revision += 1  # 사람이 그 사이에 PATCH했다
        item.body = item.body + " 사람이 덧붙인 문장입니다."
        return _pass_review(kwargs["content"])

    monkeypatch.setattr(sweep, "propose_sentence_corrections", _delete_everything)
    monkeypatch.setattr(sweep, "review_generated_content", edit_midway)

    result = _process(item)

    assert result == "CONFLICT"
    assert BAD in item.body and item.body.endswith("사람이 덧붙인 문장입니다.")
    assert hooks.indexnow == [] and hooks.recovered == []


def test_string_only_flag_is_rereviewed_for_structure_not_guessed(monkeypatch, hooks):
    _aligned(monkeypatch)
    item = _flagged_item()
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    del marker["structured_findings"]  # 2026-10-08에 FLAGGED된 20편의 모양
    reviews: list[str] = []

    async def reviewer(**kwargs):
        content = kwargs["content"]
        first = not reviews
        reviews.append(content["body"])
        if not first:
            return _pass_review(content)
        return ContentAiReview(
            status=ContentAiReviewStatus.REVISE,
            confidence=0.9,
            findings=(
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "승인되지 않은 병원 주장입니다.",
                    quote=BAD,
                ),
            ),
            summary="s",
            model="m",
            candidate_sha256=candidate_sha256(content),
            coverage=candidate_review_coverage(content),
        )

    async def propose(**kwargs):
        # 구조화된 지적(인용)을 얻은 뒤에만 교정 제안이 나간다.
        stored = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
        assert stored["structured_findings"][0]["quote"] == BAD
        return {target.key: ("DELETE", "") for target in kwargs["targets"]}

    monkeypatch.setattr(sweep, "propose_sentence_corrections", propose)
    monkeypatch.setattr(sweep, "review_generated_content", reviewer)

    result = _process(item)

    assert result == "CORRECTED"
    assert BAD in reviews[0], "구조 확보 재검수는 현재 공개 본문을 본다"
    assert BAD not in reviews[1], "교정본은 별도의 독립 재검수를 받는다"
    assert BAD not in item.body


def test_string_only_flag_that_now_passes_is_just_reviewed(monkeypatch, hooks):
    _aligned(monkeypatch)
    _patch_providers(monkeypatch, propose=None)
    item = _flagged_item()
    del item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["structured_findings"]

    result = _process(item)

    assert result == "REVIEWED"
    assert sweep.POST_PUBLISH_FLAG_KEY not in item.essence_check_summary
    assert item.post_publish_reviewed_by == "system:ai-review"
    assert hooks.recovered == [item.id]


def _with_uncertain(item, *, quote=NEUTRAL_B, kind="MEDICAL_SAFETY"):
    marker = item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]
    marker["structured_findings"].append(
        {
            "severity": "UNCERTAIN",
            "kind": kind,
            "message": "근거가 불분명한 단정입니다.",
            "target": "CANDIDATE_TEXT",
            "quote": quote,
        }
    )
    return item


def test_published_mode_takes_located_uncertain_sentences_too():
    """2026-10-08 운영: 거의 모든 글에 UNCERTAIN이 섞여 있어 HARD만 고치면 전부 사람에게 갔다."""
    item = _with_uncertain(_flagged_item())
    review = {"findings": item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["structured_findings"]}

    assert mc.published_needs_human_reason(_content(item), review, must_use_messages=[MUST_USE]) is None
    plan = mc.plan_corrections(_content(item), review, must_use_messages=[MUST_USE], include_uncertain=True)
    assert {target.sentence.strip() for target in plan.targets} == {BAD, NEUTRAL_B}
    assert plan.uncorrectable == ()


def test_draft_mode_still_leaves_uncertain_sentences_alone():
    item = _with_uncertain(_flagged_item())
    review = {"findings": item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["structured_findings"]}

    plan = mc.plan_corrections(_content(item), review, must_use_messages=[MUST_USE])

    assert [target.sentence.strip() for target in plan.targets] == [BAD]
    assert plan.uncorrectable == ("근거가 불분명한 단정입니다.",)


def test_published_mode_unlocated_uncertain_still_goes_to_the_human():
    item = _with_uncertain(_flagged_item(), quote="본문에 없는 문장입니다.")
    review = {"findings": item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["structured_findings"]}

    assert (
        mc.published_needs_human_reason(_content(item), review, must_use_messages=[MUST_USE])
        == "PARTLY_UNCORRECTABLE"
    )


def test_needs_human_judged_under_older_rules_without_spend_is_reopened():
    """규칙이 바뀌면 돈을 쓰지 않고 사람에게 넘긴 글은 새 규칙으로 다시 본다(2026-10-08 18편)."""
    old = {"correction": {"finished": True, "passes": 0, "last_status": "NEEDS_HUMAN"}}
    spent = {"correction": {"finished": True, "passes": 1, "last_status": "BLOCKED"}}
    current = {
        "correction": {
            "finished": True,
            "passes": 0,
            "last_status": "NEEDS_HUMAN",
            "rules_version": pc.CORRECTION_RULES_VERSION,
        }
    }

    assert pc.correction_exhausted(old, max_passes=2) is False
    assert pc.correction_exhausted(spent, max_passes=2) is True
    assert pc.correction_exhausted(current, max_passes=2) is True


def test_attempt_records_the_rules_version():
    marker = pc.marker_with_attempt(
        {"status": "FLAGGED"},
        outcome=mc.CorrectionOutcome(status="NEEDS_HUMAN"),
        finished=True,
        reason="NOT_CORRECTABLE",
        now=NOW,
    )

    assert marker["correction"]["rules_version"] == pc.CORRECTION_RULES_VERSION


def test_flagged_selection_reopens_free_older_rule_verdicts():
    from sqlalchemy.dialects import postgresql

    sql = str(
        sweep._flagged_stmt(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "rules_version" in sql
    assert "passes" in sql
