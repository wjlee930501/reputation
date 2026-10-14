"""누락 방지 3(2026-10-14): 등급(`critical`) 없는 옛 차단 검수는 v3 검수로 본문 hash당 한 번 다시 산다.

옛 저장 지적은 등급이 없어 Critical로 읽혀(`classify_finding`) 결정적 삭제도 교정도 맡지 않은 채 표본
예산의 재검수만 며칠씩 기다렸다(예: ff66dcde). 표본 예산이 오늘 다 찼어도 한 번은 사고, 같은 본문으로는
다시 사지 않으며 시도 계수를 올리지 않는다. 외부 호출은 없다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.content_ai_review import ContentAiReviewStatus
from app.workers import tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY


class _DB:
    def add(self, _value):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None


def _legacy_review() -> dict:
    return {
        "status": "REVISE",
        "blocking": True,
        "schema_version": "content-review-v2",
        "findings": [
            {
                "kind": "HOSPITAL_FACT",
                "severity": "UNCERTAIN",
                "target": "CANDIDATE_TEXT",
                "quote": "근거 없는 병원 문장입니다.",
                "message": "근거가 없습니다.",
            }
        ],
    }


def _v3_review() -> dict:
    finding = {**_legacy_review()["findings"][0], "critical": False}
    return {**_legacy_review(), "schema_version": "content-review-v3", "findings": [finding]}


def _spent_today_attempt() -> dict:
    now = datetime.now(UTC)
    return {
        "reason": "CONTENT_AI_HARD_FINDING",
        "retry_class": "SAMPLE_RECOVERABLE",
        "observed_at": now.isoformat(),
        "next_retry_at": (now + timedelta(hours=10)).isoformat(),
        "exhausted_days": 1,
        "attempt_count": 3,
        "provider_attempt_count": 3,
    }


@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    philosophy = SimpleNamespace(id=uuid.uuid4(), must_use_messages=[])
    hospital = SimpleNamespace(id=uuid.uuid4(), name="등급재검수의원")
    attempt = _spent_today_attempt()
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        title="골다공증 안내",
        body="## 본문\n근거 없는 병원 문장입니다.",
        meta_description="골다공증 안내",
        faq_question=None,
        faq_answer_summary=None,
        references_list=[],
        image_url="https://example.invalid/image.webp",
        content_type=SimpleNamespace(value="DISEASE"),
        query_target_id=None,
        scheduled_date=datetime.now(UTC).date(),
        content_philosophy_id=philosophy.id,
        content_brief={},
        essence_check_summary={"ai_review": _legacy_review(), GENERATION_ATTEMPT_KEY: attempt},
    )
    calls: list[dict] = []

    async def review(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=ContentAiReviewStatus.REVISE, payload=_v3_review)

    monkeypatch.setattr(tasks, "review_generated_content", review)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_a: SimpleNamespace(code="CONTENT_AI_HARD_FINDING", message="막힘"),
    )
    for name in (
        "_stored_ai_review_is_remediable",
        "_stored_block_is_sample_remediable",
        "_approved_facts_changed_since_block",
    ):
        monkeypatch.setattr(tasks, name, lambda *_a, **_k: False)
    monkeypatch.setattr(tasks, "_resolve_noncritical_block", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "_auto_correct_blocked_body", lambda *_a, **_k: None)
    return SimpleNamespace(item=item, hospital=hospital, calls=calls, attempt=attempt)


def test_unrated_block_buys_one_v3_review_outside_the_spent_daily_budget(scenario):
    item = scenario.item
    assert tasks.legacy_tier_rereview_due(item) is True

    tasks._generate_single_content_item(_DB(), item, scenario.hospital)

    assert len(scenario.calls) == 1
    summary = item.essence_check_summary
    assert summary["ai_review"]["findings"][0]["critical"] is False
    assert summary[tasks.LEGACY_TIER_REREVIEW_KEY] == tasks.candidate_sha256(item)
    # 표본 예산 밖의 한 번이다 — 시도 계수를 올리지 않는다.
    assert summary[GENERATION_ATTEMPT_KEY]["provider_attempt_count"] == 3
    assert tasks.legacy_tier_rereview_due(item) is False


def test_same_body_is_not_bought_twice(scenario):
    item = scenario.item
    tasks._generate_single_content_item(_DB(), item, scenario.hospital)
    # 새 검수가 또 등급 없이 돌아왔다고 해도(가정) 같은 본문으로는 다시 사지 않는다.
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _legacy_review()}
    tasks._generate_single_content_item(_DB(), item, scenario.hospital)
    assert len(scenario.calls) == 1


def test_flag_off_or_rated_review_is_not_a_legacy_rereview(scenario, monkeypatch):
    item = scenario.item
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _v3_review()}
    assert tasks.legacy_tier_rereview_due(item) is False
    item.essence_check_summary = {**item.essence_check_summary, "ai_review": _legacy_review()}
    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", False)
    assert tasks.legacy_tier_rereview_due(item) is False
