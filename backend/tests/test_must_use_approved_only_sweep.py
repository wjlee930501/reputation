"""저장 본문 재검수 스윕: 필수 문구 면제는 현재 승인본 문구만 근거로 한다.

승인본 필수 문구가 2cm→1cm로 바뀐 뒤에도 옛 가이드(brief)에는 2cm 문구가 남아 있을 수 있다.
스윕은 저장된 가이드를 그대로 독립 검수에 넘긴다. 그 옛 문구가 면제 근거가 되면 철회된 2cm
문장에 대한 HARD가 SOFT로 내려가 게시될 수 있다. 1cm(현재 승인본) 문장 지적만 기록용이 되고
2cm 문장 지적은 차단으로 남아야 한다.

스윕 진입점(`_generate_single_content_item`)과 실제 독립 검수 판정을 태우고, 공급자 응답만
고정 판정으로 바꾼다. 외부 호출은 없다.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.content_publication import public_candidate_review_safe
from app.workers import tasks
from tests.must_use_review_support import (
    APPROVED_1CM,
    LEAD,
    STALE_BRIEF_2CM,
    approved_and_stale_verdict,
    findings_by_quote,
    install_fake_reviewer,
)
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


class _DB:
    def __init__(self):
        self.commits = 0

    def add(self, _value):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None


def test_stored_rereview_keeps_hard_on_a_stale_brief_must_use_sentence(monkeypatch) -> None:
    calls = install_fake_reviewer(monkeypatch, [approved_and_stale_verdict()])
    philosophy = SimpleNamespace(
        id=uuid.uuid4(),
        status="APPROVED",
        version=3,
        must_use_messages=[APPROVED_1CM],
        avoid_messages=[],
        medical_ad_risk_rules=[],
        positioning_statement="",
        doctor_voice="",
        content_principles=[],
        treatment_narratives=[],
    )
    hospital = SimpleNamespace(id=uuid.uuid4(), name="승인본기준의원")
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        title="대장 선종 안내",
        body=f"{LEAD} {APPROVED_1CM} {STALE_BRIEF_2CM}",
        meta_description="대장 선종과 검진 안내",
        faq_question=None,
        faq_answer_summary=None,
        references_list=[],
        image_url="https://example.invalid/image.webp",
        content_type=SimpleNamespace(value="DISEASE"),
        query_target_id=None,
        content_philosophy_id=philosophy.id,
        # 승인본이 1cm로 바뀌기 전에 만든 가이드. 옛 2cm 문구가 남아 있다.
        content_brief={"must_use_messages": [STALE_BRIEF_2CM]},
        essence_check_summary={},
    )
    assessments: list[str] = []

    def assess(stored, _philosophy):
        if "ai_review" not in (stored.essence_check_summary or {}):
            code = "CONTENT_AI_REVIEW_STALE"
        elif public_candidate_review_safe(stored):
            code = "TEST_REVIEW_CLEARED"
        else:
            code = "CONTENT_AI_HARD_FINDING"
        assessments.append(code)
        return SimpleNamespace(code=code, message=code)

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(tasks, "assess_content_publication", assess)
    # 차단 뒤 재작성 판단은 이 테스트의 범위 밖이다 — 저장된 검수 결과만 본다.
    for name in (
        "_stored_ai_review_is_remediable",
        "_stored_block_is_sample_remediable",
        "_approved_facts_changed_since_block",
    ):
        monkeypatch.setattr(tasks, name, lambda *_args, **_kwargs: False)
    # 남은 HARD를 지적 문장 교정으로 푸는 다음 단계도 범위 밖이다(`test_publish_block_auto_resolve`).
    monkeypatch.setattr(tasks, "_auto_correct_blocked_body", lambda *_args, **_kwargs: None)

    state, code, _message = tasks._generate_single_content_item(_DB(), item, hospital)

    assert len(calls) == 1
    assert assessments == ["CONTENT_AI_REVIEW_STALE", "CONTENT_AI_HARD_FINDING"]
    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    stored = item.essence_check_summary["ai_review"]
    assert stored["blocking"] is True
    assert public_candidate_review_safe(item) is False
    by_quote = findings_by_quote(stored)
    # 현재 승인본 1cm 문장 지적은 기록용이고, 옛 가이드 2cm 문장 지적은 차단으로 남는다.
    assert by_quote[APPROVED_1CM]["target"] == "MUST_USE_MESSAGE"
    assert by_quote[APPROVED_1CM]["severity"] == "SOFT"
    assert by_quote[STALE_BRIEF_2CM]["target"] == "CANDIDATE_TEXT"
    assert by_quote[STALE_BRIEF_2CM]["severity"] == "HARD"
