"""종착 상태의 슬롯을 스윕이 매번 다시 집지 않는다 (2026-10 운영: 30일 GENERATE_CONTENT_ITEM
FAILED 267건·REGENERATE_CONTENT 309건이 모두 `CONTENT_AI_HARD_FINDING` 종착 슬롯의 재집기였다).

입력이 그대로인 종착 기록은 로더(claim 전)와 워커가 같은 술어로 거른다. 입력이 바뀌었거나
판정 뒤 사람이 고쳤거나 표본 예산 안이면 종전처럼 다시 시도한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.models.content import ContentType
from app.services.reference_requirement import (
    QUERY_TARGET_TOPIC_FIELDS,
    REFERENCES_REQUIRED_TYPES,
    references_required_for,
)
from app.workers import tasks
from app.workers.generation_attempt_state import fresh_generation_attempt
from app.workers.generation_retry_policy import attempt_is_terminal
from app.workers.nightly_generation_batch import _needs_generation_recovery
from app.workers.topic_swap_fallback import topic_swap_budget_left, topic_swap_limit

_OBSERVED = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
_TERMINAL = {"reason": "CONTENT_AI_HARD_FINDING", "retry_class": "OPERATOR_REQUIRED"}


def _philosophy(philosophy_id: str = "p1"):
    return SimpleNamespace(id=philosophy_id, director_delta_ids=[])


def _item(attempt: dict, *, philosophy, human_edited_at=None, history=None):
    context = tasks._generation_attempt_context(
        SimpleNamespace(content_type=SimpleNamespace(value="FAQ"), query_target_id=None),
        philosophy,
    )
    return SimpleNamespace(
        id="i1",
        hospital_id="h1",
        body="본문",
        content_type=SimpleNamespace(value="FAQ"),
        query_target_id=None,
        human_edited_at=human_edited_at,
        topic_swap_history=history,
        essence_check_summary={
            "generation_attempt": {
                "context": context,
                "observed_at": _OBSERVED.isoformat(),
                **attempt,
            }
        },
    )


def _eligible(monkeypatch, item, philosophy) -> bool:
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    return tasks._generation_retry_is_eligible(object())(item)


def test_terminal_unchanged_slot_is_not_eligible(monkeypatch):
    philosophy = _philosophy()
    item = _item(_TERMINAL, philosophy=philosophy)

    assert tasks._terminal_attempt_is_unchanged(item, philosophy) is True
    assert _eligible(monkeypatch, item, philosophy) is False


def test_openrouter_key_limit_is_terminal_only_for_its_billing_period(monkeypatch):
    philosophy = _philosophy()
    item = _item(
        {
            "reason": "OPENROUTER_KEY_LIMIT_EXCEEDED",
            "retry_class": "OPERATOR_REQUIRED",
            "provider_limit_period": "2026-10",
        },
        philosophy=philosophy,
    )
    monkeypatch.setattr(tasks.openrouter_key_limit, "billing_period", lambda *_a: "2026-10")

    assert attempt_is_terminal(item.essence_check_summary["generation_attempt"]) is True
    assert _eligible(monkeypatch, item, philosophy) is False

    monkeypatch.setattr(tasks.openrouter_key_limit, "billing_period", lambda *_a: "2026-11")
    assert _eligible(monkeypatch, item, philosophy) is True


def test_operator_decides_references_and_rejected_body_are_terminal(monkeypatch):
    philosophy = _philosophy()
    for attempt in (
        {
            "reason": "MISSING_REFERENCES",
            "retry_class": "OPERATOR_REQUIRED",
            "operator_decides": True,
        },
        {"reason": "GENERATION_REJECTED", "retry_class": "OPERATOR_REQUIRED"},
    ):
        item = _item(attempt, philosophy=philosophy)
        assert _eligible(monkeypatch, item, philosophy) is False


def test_changed_input_makes_terminal_slot_eligible_again(monkeypatch):
    item = _item(_TERMINAL, philosophy=_philosophy("p1"))
    # 새로 승인된 자료(philosophy id가 바뀐다)는 지문을 바꾼다.
    assert _eligible(monkeypatch, item, _philosophy("p2")) is True


def test_human_edit_after_verdict_makes_terminal_slot_eligible(monkeypatch):
    philosophy = _philosophy()
    edited = _item(
        _TERMINAL, philosophy=philosophy, human_edited_at=_OBSERVED + timedelta(hours=1)
    )
    older = _item(
        _TERMINAL, philosophy=philosophy, human_edited_at=_OBSERVED - timedelta(hours=1)
    )

    assert _eligible(monkeypatch, edited, philosophy) is True
    assert _eligible(monkeypatch, older, philosophy) is False


def test_sample_recoverable_within_budget_stays_eligible(monkeypatch):
    philosophy = _philosophy()
    attempt = {
        "reason": "CONTENT_AI_HARD_FINDING",
        "retry_class": "SAMPLE_RECOVERABLE",
        "auto_correction_exhausted": True,
    }
    assert attempt_is_terminal(attempt) is False
    assert _eligible(monkeypatch, _item(attempt, philosophy=philosophy), philosophy) is True


def test_repair_owned_codes_are_never_terminal():
    # 본문 수리·교정·이미지 재사용은 재집기가 실제로 일을 한다.
    for reason, retry_class in (
        ("FORBIDDEN_EXPRESSION", "INPUT_CHANGE_REQUIRED"),
        ("CONTENT_AI_HARD_FINDING", "INPUT_CHANGE_REQUIRED"),
        ("MISSING_REFERENCES", "OPERATOR_REQUIRED"),  # operator_decides 표시 없음 = 수리 대상
        ("IMAGE_GENERATION_RETRIES_EXHAUSTED", "OPERATOR_REQUIRED"),
    ):
        assert attempt_is_terminal({"reason": reason, "retry_class": retry_class}) is False


def test_worker_skips_terminal_unchanged_without_generating(monkeypatch):
    philosophy = _philosophy()
    item = _item(_TERMINAL, philosophy=philosophy)
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
    assert code == "CONTENT_AI_HARD_FINDING"
    assert recorded


# ── 참고자료 필수 규칙의 단일 정본 ───────────────────────────────────────────────


def _recovery_sql() -> str:
    return str(
        _needs_generation_recovery().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_loader_reference_types_come_from_the_canonical_set():
    in_clause = _recovery_sql().split("content_items.content_type IN")[1].split(")")[0]
    for content_type in ContentType:
        assert (f"'{content_type.name}'" in in_clause) == (
            content_type in REFERENCES_REQUIRED_TYPES
        ), content_type


def test_loader_selects_notice_with_linked_query_target():
    sql = _recovery_sql()
    assert "content_items.content_type = 'NOTICE'" in sql
    assert "content_items.query_target_id IS NOT NULL" in sql
    for field in QUERY_TARGET_TOPIC_FIELDS:
        assert f"'{field}'" in sql
    assert "'exposure_action'" in sql
    # 정본이 NOTICE를 연결 여부로 가르는 것과 같은 전제.
    assert references_required_for(ContentType.NOTICE, query_target_id="q") is True
    assert references_required_for(ContentType.NOTICE) is False


# ── 주제 교체 상한의 단일 판정 ───────────────────────────────────────────────────


def test_topic_swap_budget_is_one_function(monkeypatch):
    monkeypatch.setattr("app.workers.topic_swap_fallback.settings.CONTENT_AUTO_TOPIC_SWAP_MAX", 1)
    assert topic_swap_limit() == 1
    fresh = SimpleNamespace(
        topic_swap_history=None,
        essence_check_summary={
            "generation_attempt": fresh_generation_attempt(topic_id="topic-a")
        },
    )
    assert topic_swap_budget_left(fresh) is True
    assert topic_swap_budget_left(
        SimpleNamespace(
            topic_swap_history=[{}], essence_check_summary=fresh.essence_check_summary
        )
    ) is False
    # 기록 없이 생성 흔적만 남은 슬롯은 지출을 모른다 — 교체하지 않는다.
    assert topic_swap_budget_left(
        SimpleNamespace(topic_swap_history=None, essence_check_summary=None, body="## 본문")
    ) is False
    # 기록도 흔적도 없는 슬롯은 지출 0의 새 예산이다(2026-10-14 hotfix).
    assert topic_swap_budget_left(
        SimpleNamespace(topic_swap_history=None, essence_check_summary=None)
    ) is True
    assert tasks.topic_swap_budget_left is topic_swap_budget_left


def test_every_cause_gets_a_second_swap_but_no_third(monkeypatch):
    """누락 방지 5(2026-10-14): Critical이 아닌 원인도 Critical과 같은 두 번. 세 번째는 없다."""

    monkeypatch.setattr("app.workers.topic_swap_fallback.settings.CONTENT_AUTO_TOPIC_SWAP_MAX", 2)
    assert (topic_swap_limit(), topic_swap_limit(critical=False)) == (2, 2)
    summary = {"generation_attempt": fresh_generation_attempt(topic_id="topic-a")}
    once = SimpleNamespace(topic_swap_history=[{}], essence_check_summary=summary)
    twice = SimpleNamespace(topic_swap_history=[{}, {}], essence_check_summary=summary)
    assert topic_swap_budget_left(once, critical=True) is True
    assert topic_swap_budget_left(once) is True
    assert topic_swap_budget_left(twice, critical=True) is False
    assert topic_swap_budget_left(twice) is False
    # 설정을 예산 기록의 상한보다 크게 줘도 두 번을 넘지 않는다.
    monkeypatch.setattr("app.workers.topic_swap_fallback.settings.CONTENT_AUTO_TOPIC_SWAP_MAX", 5)
    assert topic_swap_limit() == 2
    monkeypatch.setattr("app.workers.topic_swap_fallback.settings.CONTENT_AUTO_TOPIC_SWAP_MAX", 0)
    assert (topic_swap_limit(), topic_swap_limit(critical=False)) == (0, 0)


# ── E1(2026-10-13): 아무것도 맡지 못한 HARD 차단은 입력이 바뀔 때까지 다시 집지 않는다 ─────
# 배포 전 측정(계획 3-5 Step 0)이 공회전을 보여 줄 때만 켜는 플래그 뒤에 있다(기본 꺼짐).


_IDLE_HARD = {
    "reason": "CONTENT_AI_HARD_FINDING",
    "retry_class": "INPUT_CHANGE_REQUIRED",
    "hard_finding_idle": True,
    "approved_facts": "facts-v1",
}


def _idle_terminal(monkeypatch, enabled: bool) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CONTENT_HARD_FINDING_IDLE_TERMINAL", enabled)


def test_idle_model_declared_hard_is_terminal_until_its_input_changes(monkeypatch):
    _idle_terminal(monkeypatch, True)
    philosophy = _philosophy()
    item = _item(_IDLE_HARD, philosophy=philosophy)
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")

    assert attempt_is_terminal(_IDLE_HARD) is True
    assert _eligible(monkeypatch, item, philosophy) is False
    # 사람이 승인 자료(병원 사실)를 채우면 다시 집는다.
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v2")
    assert _eligible(monkeypatch, item, philosophy) is True
    # 운영 기준이 새로 승인돼도 다시 집는다.
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")
    assert _eligible(monkeypatch, item, _philosophy("p2")) is True


def test_idle_mark_is_ignored_while_the_flag_is_off(monkeypatch):
    """플래그를 끄면(기본) 남아 있던 표시도 종착으로 읽지 않는다 — 끄는 것이 곧 롤백이다."""

    _idle_terminal(monkeypatch, False)
    philosophy = _philosophy()
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")
    assert attempt_is_terminal(_IDLE_HARD) is False
    assert _eligible(monkeypatch, _item(_IDLE_HARD, philosophy=philosophy), philosophy) is True


def test_hard_without_the_idle_mark_is_still_picked_for_correction(monkeypatch):
    _idle_terminal(monkeypatch, True)
    philosophy = _philosophy()
    attempt = {k: v for k, v in _IDLE_HARD.items() if k != "hard_finding_idle"}
    assert attempt_is_terminal(attempt) is False
    assert _eligible(monkeypatch, _item(attempt, philosophy=philosophy), philosophy) is True


def test_missing_approved_essence_is_never_terminal():
    """Phase 4(D33): Essence는 승인 없이 자동 적용되므로 판이 없는 것은 잠깐의 준비 상태다."""

    assert (
        attempt_is_terminal(
            {"reason": "MISSING_APPROVED_ESSENCE", "retry_class": "INPUT_CHANGE_REQUIRED"}
        )
        is False
    )
