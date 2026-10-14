"""D23 뒤 진료비·병원 선택 글의 빈 참고자료는 고칠 것이 아니다 — 스윕이 매번 다시 집지 않는다.

SQL 복구 술어(`_needs_generation_recovery`)는 유형만 보고 빈 참고자료를 수리 대상으로 고른다(제목
판정은 SQL로 옮길 수 없다). 그래서 본문이 있는 진료비 DRAFT가 하루 7번 claim·실행 기록을 남기며
상한 50의 자리를 내일 슬롯보다 먼저 차지했다. 로더(claim 전)와 워커가 같은 파이썬 술어로 거른다.
다른 수리 이유(이미지·FAQ·검수·Essence)가 있으면 종전대로 집는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.content import ContentType
from app.workers import tasks
from app.workers.nightly_generation_batch import (
    _needs_generation_recovery,
    stored_body_awaits_only_optional_references,
)

_VERIFIED = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
COST_TITLE = "도수치료 비용, 얼마나 드나요?"
MEDICAL_TITLE = "허리 디스크 초기 증상과 진료 흐름"


def _philosophy(philosophy_id: str = "p1"):
    return SimpleNamespace(id=philosophy_id, director_delta_ids=[])


def _item(**overrides):
    values = {
        "id": "i1",
        "hospital_id": "h1",
        "title": COST_TITLE,
        "body": "## 안내\n본문",
        "content_type": ContentType.TREATMENT,
        "content_brief": None,
        "query_target_id": None,
        "references_list": [],
        "image_url": "https://img.example/a.png",
        "image_policy_verified_at": _VERIFIED,
        "faq_question": None,
        "faq_answer_summary": None,
        "content_philosophy_id": "p1",
        "essence_status": "ALIGNED",
        "essence_check_summary": {"ai_review": {"status": "PASS", "schema_version": "3"}},
        "human_edited_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _eligible(monkeypatch, item, philosophy=None) -> bool:
    philosophy = philosophy or _philosophy()
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    return tasks._generation_retry_is_eligible(object())(item)


def test_cost_draft_with_no_references_and_nothing_else_to_repair_is_not_claimed(monkeypatch):
    item = _item()

    assert stored_body_awaits_only_optional_references(item, _philosophy()) is True
    assert _eligible(monkeypatch, item) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"image_url": None},
        {"image_policy_verified_at": None},
        {"content_philosophy_id": "old"},
        {"essence_status": "NEEDS_REVIEW"},
        {"essence_check_summary": {"ai_review": {"status": "UNAVAILABLE"}}},
        {"essence_check_summary": {"ai_review": {"status": "PASS", "edited_after_review": True}}},
        {"essence_check_summary": {"ai_review": {"status": "REVISE", "blocking": True, "schema_version": "3"}}},
        {"essence_check_summary": {"ai_review": {"status": "REVISE", "blocking": False}}},
        {"content_type": ContentType.FAQ, "faq_question": "비용은?", "faq_answer_summary": ""},
        {"content_type": ContentType.FAQ, "faq_question": "비용은", "faq_answer_summary": "답"},
        # 범용 서술 각도 글의 hash 없는 검수 — 발행 게이트가 STALE로 막으므로 재검수 대상이다.
        {"content_brief": {"treatment_narrative": {"source": "fallback", "angle": "각도"}}},
    ],
)
def test_the_same_row_with_another_repair_reason_is_still_claimed(monkeypatch, overrides):
    """조건마다 `_needs_generation_recovery`의 참고자료 밖 조건과 짝이다."""

    item = _item(**overrides)

    assert stored_body_awaits_only_optional_references(item, _philosophy()) is False
    assert _eligible(monkeypatch, item) is True


def test_missing_image_alone_is_claimed():
    assert stored_body_awaits_only_optional_references(_item(image_url=None), _philosophy()) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"references_list": [{"url": "https://www.kdca.go.kr/x", "title": "질병관리청"}]},
        {"body": None},  # 쓰이지 않은 슬롯은 종전 경로
        {"content_type": ContentType.NOTICE},  # 질문 연결 없는 공지는 참고자료 때문에 고른 행이 아니다
    ],
)
def test_rows_not_selected_for_optional_references_are_untouched(overrides):
    assert stored_body_awaits_only_optional_references(_item(**overrides), _philosophy()) is False


def test_medical_post_with_only_empty_references_is_not_reclaimed_either():
    """D36(2026-10-14): 의료 글도 참고자료가 비어 있는 것만으로는 고칠 것이 없다 — 다시 사지 않는다."""

    assert stored_body_awaits_only_optional_references(_item(title=MEDICAL_TITLE), _philosophy())


def test_python_mirror_names_every_non_reference_condition_of_the_sql_predicate():
    """SQL 술어에 조건이 늘면 파이썬 짝(`_stored_body_needs_non_reference_repair`)도 늘려야 한다."""

    sql = str(_needs_generation_recovery().compile(dialect=postgresql.dialect()))
    for column in (
        "body",
        "image_url",
        "image_policy_verified_at",
        "faq_question",
        "faq_answer_summary",
        "content_philosophy_id",
        "essence_status",
        "references_list",
    ):
        assert f"content_items.{column}" in sql
    # 본문·이미지·인증·FAQ·참고자료·Essence 승인본·독립 검수(범용 각도 포함)·Essence 상태를 or_가 펼친 갈래 수.
    # 수가 바뀌면 파이썬 짝을 함께 고친 뒤 이 값을 맞춘다.
    assert len(_needs_generation_recovery().clauses) == 12


def test_worker_skips_the_row_without_generating(monkeypatch):
    philosophy = _philosophy()
    item = _item()
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)

    def _must_not_generate(*_a, **_k):
        raise AssertionError("must not generate")

    monkeypatch.setattr(tasks, "_generate_single_content_item", _must_not_generate)
    recorded = []
    recorder = SimpleNamespace(record=lambda *a, **k: recorded.append((a, k)))

    state, code, _message = tasks._run_generation_item(
        object(), recorder, item, SimpleNamespace(id="h1", name="병원")
    )

    assert state == tasks.GenerationItemState.SKIPPED
    assert code == "REFERENCES_OPTIONAL"
    assert recorded
