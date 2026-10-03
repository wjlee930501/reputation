"""본문이 있는 글의 독립 검수 공급자 실패를 운영자 “작업 다시 시도”가 재검수만 한 번 다시 산다.

마포성모탑 47b36df6: 10/2 12:00 KST 독립 검수 응답이 잘려(INVALID_RESPONSE) 저장 원인이
CONTENT_AI_REVIEW_UNAVAILABLE(ENVIRONMENT_RECOVERABLE)로 남았다. `regenerate_content_item`은 빈
본문의 억제만 풀었으므로, 운영자가 눌러도 `_generate_single_content_item`의 동일 원인 억제가 검수
0회의 SKIPPED로 끝냈다.

이제 Admin이 만든 실행에서 본문이 있고, 저장 분류가 ENVIRONMENT_RECOVERABLE이고, 저장 원인이
정확히 CONTENT_AI_REVIEW_UNAVAILABLE이고, 본문이 병원의 지금 승인 기준으로 쓰였을 때만 억제를
풀고 재검수만 한다(`review_only=True`). 작가·원고 계획·비용 가드·본문 수리 세션은 쓰지 않는다.

- 같은 KST 날의 클릭은 억제만 풀고 사다리를 남긴다. 새 KST 날의 클릭은 기록을 지운다 — 해제가
  `reason`을 떨궈 하루 초기화가 일어나지 않으면 어제 계수가 오늘 예산을 먹는다.
- 재검수 없이 물러난 실행(SKIPPED·검수 전 FAILED·예외)은 원래 기록을 되돌린다. 다만 그 사이 기록이나
  ai_review가 바뀌었으면 그것이 더 새로운 사실이라 되돌리지 않는다.
- 자동 경로·다른 원인·다른 분류·옛 기준 본문·기준 없음·빈 본문은 종전 그대로다.
"""

from __future__ import annotations

import copy
import logging
import sys
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
import test_operator_retry_gate_record as gate_record
from test_operator_retry_gate_record import _install_item_lease, _ladder, _slot_with_writer
from test_topic_swap_fallback import _freeze, _generate_once, _kst, _WorkerDB

from app.models.operations import OperationRunState
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    ContentAiReviewUnavailableReason,
)
from app.workers import generation_retry_policy, tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_incident_control import operator_retry_releases
from app.workers.generation_retry_policy import (
    BODY_REPAIR_DAILY_BUDGET,
    ENVIRONMENT_ATTEMPT_BUDGET,
    GenerationRetryClass,
)

_RECORDED_AT = _kst(2026, 10, 2, 12, 0)  # 잘린 독립 검수가 기록된 시각(마포성모탑)
_SAME_DAY_PRESS = _kst(2026, 10, 2, 15, 0)
_LATE_RECORD = _kst(2026, 10, 2, 23, 30)
_NEW_DAY_PRESS = _kst(2026, 10, 3, 0, 30)  # 새 KST 날, 저장된 다음 시도 시각(01:00) 전
_SLOT_DATE = date(2026, 10, 3)
_UNAVAILABLE = "CONTENT_AI_REVIEW_UNAVAILABLE"
_WAIT_MESSAGE = "독립 검수의 다음 자동 재검수 조건을 기다립니다."


def _review(status, findings=(), *, reason=None) -> ContentAiReview:
    return ContentAiReview(
        status=status,
        confidence=0.0 if status == ContentAiReviewStatus.UNAVAILABLE else 0.9,
        findings=tuple(findings),
        summary="재검수",
        model="reviewer-test",
        unavailable_reason=reason,
    )


def _unavailable(reason=ContentAiReviewUnavailableReason.INVALID_RESPONSE) -> ContentAiReview:
    return _review(ContentAiReviewStatus.UNAVAILABLE, reason=reason)


def _passing() -> ContentAiReview:
    return _review(ContentAiReviewStatus.PASS)


def _finding(severity, kind) -> ContentAiFinding:
    return ContentAiFinding(severity, kind, "지적이 남아 있습니다.")


def _assessment(item, _philosophy):
    """저장된 ai_review만 보는 발행 판정(실제 `_unavailable_ai_review_code`와 같은 대응)."""

    review = (item.essence_check_summary or {}).get("ai_review") or {}
    if review.get("status") == "UNAVAILABLE":
        code = {
            "COST_BLOCKED": "COST_BLOCKED",
            "PROVIDER_UNCONFIGURED": "CONTENT_AI_REVIEW_CONFIG_ERROR",
        }.get(review.get("unavailable_reason"), _UNAVAILABLE)
    elif review.get("blocking"):
        code = "CONTENT_AI_HARD_FINDING"
    else:
        code = None
    return SimpleNamespace(code=code, message=f"{code} 판정" if code else None)


def _written_slot(
    monkeypatch,
    *,
    reason: str = _UNAVAILABLE,
    stored_review: ContentAiReview | None = None,
    recorded_at=(_RECORDED_AT,),
    responses=None,
    assess=_assessment,
):
    """지금 기준으로 쓰인 본문과, 실제 `_remember_generation_attempt`가 남긴 검수 실패 기록."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    item.scheduled_date = _SLOT_DATE
    item.title = "대장내시경 전 준비할 점"
    item.body = "검사 전날의 식사와 복용약을 의료진과 미리 확인합니다."
    item.meta_description = "대장내시경 전 준비할 점을 정리했습니다."
    item.faq_question = "대장내시경 전에 무엇을 준비하나요?"
    item.faq_answer_summary = "식사 조절과 복용약 확인이 필요합니다."
    item.references_list = []
    item.essence_check_summary = {"ai_review": (stored_review or _unavailable()).payload()}
    for moment in recorded_at:
        _freeze(monkeypatch, moment)
        tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, reason)
    record = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])

    spy = SimpleNamespace(
        philosophy=philosophy,
        item=item,
        record=record,
        original_review=copy.deepcopy(item.essence_check_summary["ai_review"]),
        writer=writer_calls,
        reviews=[],
        seen_at_review=[],
        seen_at_assess=[],
        cost=[],
        briefs=[],
        write_backs=[],
        repairs=[],
        images=[],
        readiness=[],
        releases=[],
    )
    queue = list(responses if responses is not None else [_passing()])

    async def reviewer(**kwargs):
        spy.reviews.append(kwargs["content"]["body"])
        spy.seen_at_review.append(tasks._stored_generation_attempt(item))
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def guard(*_args, **_kwargs):
        spy.cost.append("content")
        return SimpleNamespace(allowed=True)

    def brief(*_args, **_kwargs):
        spy.briefs.append(item.id)
        return {}

    write_back = tasks.write_back_generated_content

    def counted_write_back(*args, **kwargs):
        spy.write_backs.append(item.id)
        return write_back(*args, **kwargs)

    spend = tasks._spend_body_repair_session

    def counted_spend(db, target):
        spy.repairs.append(target.id)
        return spend(db, target)

    image = tasks._recover_missing_content_image

    def counted_image(*args):
        spy.images.append(item.id)
        return image(*args)

    def readiness(_db, target, _philosophy):
        spy.readiness.append(target.id)
        return None

    def judged(target, current):
        spy.seen_at_assess.append(tasks._stored_generation_attempt(target))
        return assess(target, current)

    release = tasks._release_generation_attempt_for_repair

    def observed_release(db, target):
        spy.releases.append(sys._getframe(1).f_code.co_name)
        return release(db, target)

    monkeypatch.setattr(tasks, "review_generated_content", reviewer)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", guard)
    monkeypatch.setattr(tasks, "prepare_automatic_content_brief_sync", brief)
    monkeypatch.setattr(tasks, "write_back_generated_content", counted_write_back)
    monkeypatch.setattr(tasks, "_spend_body_repair_session", counted_spend)
    monkeypatch.setattr(tasks, "_recover_missing_content_image", counted_image)
    monkeypatch.setattr(tasks, "_persist_publication_readiness", readiness)
    monkeypatch.setattr(tasks, "assess_content_publication", judged)
    monkeypatch.setattr(tasks, "_release_generation_attempt_for_repair", observed_release)
    return spy


def _record(item) -> dict:
    return dict((item.essence_check_summary or {}).get(GENERATION_ATTEMPT_KEY) or {})


def _assert_no_writer_session(spy) -> None:
    assert spy.writer == []
    assert spy.cost == []
    assert spy.briefs == []
    assert spy.write_backs == []
    assert spy.repairs == []


def _press(monkeypatch, spy, moment, *, operator=True, finished=None, session=None) -> list:
    """`_press_retry`와 같은 실행이다. 예외로 끝나도 실행 종결 기록을 읽도록 목록을 받는다."""

    finished = [] if finished is None else finished
    _freeze(monkeypatch, moment)
    _install_item_lease(monkeypatch, spy.item)
    monkeypatch.setattr(
        tasks, "SyncSessionLocal", session or (lambda: gate_record._TaskDB(spy.item))
    )
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(tasks, "explicit_run_matches", lambda *_args, **_kwargs: True)

    def finish(_db, task, _item_id, state, **kwargs):
        finished.append((state, kwargs.get("safe_error_code")))
        return uuid.uuid4() if tasks.explicit_run_context(task) is not None else None

    monkeypatch.setattr(tasks, "finish_explicit_run", finish)
    headers = {"operation_run_id": str(uuid.uuid4())} if operator else {}
    task = tasks.regenerate_content_item
    task.push_request(
        id="worker-1", headers=headers, operation_run_claim_version=2 if operator else None
    )
    try:
        task.run(str(spy.item.id))
    finally:
        task.pop_request()
    return finished


def _assert_untouched_skip(spy, finished, code: str = _UNAVAILABLE) -> None:
    assert finished == [(OperationRunState.FAILED, code)]
    assert spy.reviews == []
    _assert_no_writer_session(spy)


# ── 재검수 전용 실행 ─────────────────────────────────────────────────────────


def test_a_passing_rereview_publishes_through_the_image_step_without_the_writer(monkeypatch):
    spy = _written_slot(monkeypatch)
    assert datetime.fromisoformat(spy.record["next_retry_at"]) > _SAME_DAY_PRESS  # 기한 전이다
    assert not operator_retry_releases(spy.item)  # 빈 슬롯 판정은 본문 글을 다루지 않는다

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert spy.reviews == [spy.item.body]  # 저장 본문 그대로 정확히 한 번
    _assert_no_writer_session(spy)
    assert spy.images == [spy.item.id]  # 종전 이미지 재사용·복구 단계를 그대로 거친다
    assert spy.readiness == [spy.item.id]
    assert spy.item.essence_check_summary["ai_review"]["status"] == "PASS"
    assert GENERATION_ATTEMPT_KEY not in spy.item.essence_check_summary


def test_a_same_day_press_releases_the_suppression_and_keeps_the_ladder(monkeypatch):
    spy = _written_slot(monkeypatch)

    _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert spy.releases == ["_release_for_review_only"]
    assert spy.seen_at_review == [_ladder(spy.record)]
    assert "reason" not in spy.seen_at_review[0]


def test_an_unavailable_rereview_fails_and_counts_on_the_released_ladder(monkeypatch):
    spy = _written_slot(monkeypatch, responses=[_unavailable()])

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert len(spy.reviews) == 1
    _assert_no_writer_session(spy)
    stored = _record(spy.item)
    assert stored["reason"] == _UNAVAILABLE
    assert stored["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert stored["provider_attempt_count"] == spy.record["provider_attempt_count"] + 1 == 2
    assert stored["attempt_period"] == spy.record["attempt_period"] == "2026-10-02"
    assert stored["first_observed_at"] == spy.record["first_observed_at"]
    assert spy.item.essence_check_summary["ai_review"] == spy.original_review


def test_an_exhausted_same_day_budget_is_still_rereviewed_once_on_press(monkeypatch):
    """오늘 예산을 다 쓴 기록은 내일 01:00까지 어떤 스윕도 다시 검수하지 않는다."""

    moments = [_kst(2026, 10, 2, hour) for hour in (1, 4, 7)] + [_RECORDED_AT]
    spy = _written_slot(monkeypatch, recorded_at=moments)
    assert spy.record["provider_attempt_count"] == ENVIRONMENT_ATTEMPT_BUDGET
    assert not generation_retry_policy.retry_is_due(spy.record, _SAME_DAY_PRESS)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert len(spy.reviews) == 1
    _assert_no_writer_session(spy)


def test_a_new_kst_day_press_clears_the_record_and_restarts_the_day_count(monkeypatch):
    moments = (_RECORDED_AT, _kst(2026, 10, 2, 16), _LATE_RECORD)
    spy = _written_slot(monkeypatch, recorded_at=moments, responses=[_unavailable()])
    assert spy.record["provider_attempt_count"] == 3
    assert datetime.fromisoformat(spy.record["next_retry_at"]) > _NEW_DAY_PRESS  # 기한 전이다

    finished = _press(monkeypatch, spy, _NEW_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert spy.releases == []  # 풀지 않고 지웠다
    assert spy.seen_at_review == [{}]
    stored = _record(spy.item)
    assert stored["provider_attempt_count"] == 1  # 어제의 3이 넘어와 4(예산)가 되지 않는다
    assert stored["attempt_period"] == "2026-10-03"
    assert isinstance(stored["next_retry_at"], str)  # 오늘의 자동 재검수가 남아 있다
    _assert_no_writer_session(spy)


def test_a_blocking_hard_finding_after_the_rereview_fails_without_the_writer(monkeypatch):
    hard = _review(
        ContentAiReviewStatus.REVISE,
        [_finding(ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY)],
    )
    spy = _written_slot(monkeypatch, responses=[hard])

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, "CONTENT_AI_HARD_FINDING")]
    assert len(spy.reviews) == 1
    _assert_no_writer_session(spy)
    assert spy.images == []
    # 재검수 구간의 종전 계수는 그대로 돈다.
    assert _record(spy.item)["reason"] == "CONTENT_AI_HARD_FINDING"


def test_a_repairable_style_finding_after_the_rereview_spends_no_repair_session(monkeypatch):
    style = _review(
        ContentAiReviewStatus.REVISE,
        [_finding(ContentAiFindingSeverity.HARD, ContentAiFindingKind.STYLE)],
    )
    spy = _written_slot(monkeypatch, responses=[style])

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert tasks._stored_ai_review_is_remediable(spy.item)  # 자동 경로였다면 작가로 갔다
    assert finished == [(OperationRunState.FAILED, "CONTENT_AI_HARD_FINDING")]
    assert len(spy.reviews) == 1
    _assert_no_writer_session(spy)
    assert spy.images == []


# ── 해제하지 않는 경우(종전 그대로) ──────────────────────────────────────────


def test_an_automatic_dispatch_keeps_the_suppression(monkeypatch):
    spy = _written_slot(monkeypatch)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS, operator=False)

    _assert_untouched_skip(spy, finished)
    assert _record(spy.item) == spy.record


def test_a_review_configuration_error_keeps_the_suppression(monkeypatch):
    spy = _written_slot(
        monkeypatch,
        stored_review=_unavailable(ContentAiReviewUnavailableReason.PROVIDER_UNCONFIGURED),
    )
    # 분류·기한은 검수 공급자 실패 기록 그대로 두고 원인 조건 하나만 바꿔 본다.
    stored = {**spy.record, "reason": "CONTENT_AI_REVIEW_CONFIG_ERROR"}
    assert stored["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    spy.item.essence_check_summary[GENERATION_ATTEMPT_KEY] = stored

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    _assert_untouched_skip(spy, finished, "CONTENT_AI_REVIEW_CONFIG_ERROR")
    assert _record(spy.item) == stored


def test_a_cost_guard_deferral_keeps_the_suppression(monkeypatch):
    spy = _written_slot(
        monkeypatch,
        reason="COST_BLOCKED",
        stored_review=_unavailable(ContentAiReviewUnavailableReason.COST_BLOCKED),
    )
    assert spy.record["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    _assert_untouched_skip(spy, finished, "COST_BLOCKED")
    assert _record(spy.item) == spy.record


def test_another_retry_class_keeps_the_suppression(monkeypatch):
    spy = _written_slot(monkeypatch)
    stored = {**spy.record, "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value}
    spy.item.essence_check_summary[GENERATION_ATTEMPT_KEY] = stored

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    _assert_untouched_skip(spy, finished)
    assert _record(spy.item) == stored


def test_a_body_written_under_another_philosophy_keeps_the_suppression(monkeypatch):
    spy = _written_slot(monkeypatch)
    spy.item.content_philosophy_id = uuid.uuid4()

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    _assert_untouched_skip(spy, finished)
    assert spy.releases == []
    assert _record(spy.item) == spy.record


def test_no_approved_philosophy_keeps_the_existing_missing_essence_path(monkeypatch):
    spy = _written_slot(monkeypatch)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: None)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    _assert_untouched_skip(spy, finished, "MISSING_APPROVED_ESSENCE")
    assert spy.releases == []


def test_an_empty_body_still_takes_the_empty_slot_release(monkeypatch):
    spy = _written_slot(monkeypatch)
    spy.item.body = None
    seen_at_writer: list = []
    writer = tasks._generate_with_auto_review

    async def observe(**kwargs):
        seen_at_writer.append(tasks._stored_generation_attempt(spy.item))
        return await writer(**kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", observe)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert spy.writer == [spy.item.id]
    assert spy.reviews == []
    assert spy.releases == ["regenerate_content_item"]
    assert seen_at_writer == [_ladder(spy.record)]


# ── 재검수 없이 물러난 실행의 원래 기록 복원 ─────────────────────────────────


def _repairable_body_assessment(item, philosophy):
    return SimpleNamespace(code="ESSENCE_NOT_ALIGNED", message="운영 기준과 어긋납니다.")


def test_a_skipped_review_only_run_restores_the_original_record(monkeypatch):
    """재검수 구간에 들지 않는 수리 대상 본문 — 수리 세션을 쓰지 않고 물러나 기록을 되돌린다."""

    spy = _written_slot(monkeypatch, assess=_repairable_body_assessment)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert spy.reviews == []
    _assert_no_writer_session(spy)
    assert spy.seen_at_assess == [_ladder(spy.record)]  # 실제로 풀었다가
    assert _record(spy.item) == spy.record  # 되돌렸다


def test_a_failed_exit_before_the_rereview_restores_the_original_record(monkeypatch):
    """오늘 수리 예산을 이미 쓴 본문의 차단은 검수 전에 FAILED로 끝난다."""

    spy = _written_slot(monkeypatch, assess=_repairable_body_assessment)
    spy.item.essence_check_summary[tasks._BODY_REPAIR_KEY] = {
        "period": "2026-10-02",
        "count": BODY_REPAIR_DAILY_BUDGET,
        "exhausted_days": 0,
    }

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, "ESSENCE_NOT_ALIGNED")]
    assert spy.reviews == []
    _assert_no_writer_session(spy)
    assert spy.seen_at_assess == [_ladder(spy.record)]
    assert _record(spy.item) == spy.record


def _tracking_session(item):
    """커밋한 값만 rollback 뒤에 남는 세션. 호출 순서를 기록한다."""

    events: list = []
    committed = {"summary": copy.deepcopy(item.essence_check_summary)}

    class TrackingDB(gate_record._TaskDB):
        def commit(self) -> None:
            events.append("commit")
            committed["summary"] = copy.deepcopy(item.essence_check_summary)

        def rollback(self) -> None:
            events.append("rollback")
            item.essence_check_summary = copy.deepcopy(committed["summary"])

        def refresh(self, _item) -> None:
            events.append("refresh")

    return events, lambda: TrackingDB(item)


def test_an_exception_restores_the_original_record_and_still_fails_the_run(monkeypatch):
    spy = _written_slot(monkeypatch, responses=[TimeoutError("review timed out")])
    events, session = _tracking_session(spy.item)
    incidents: list = []

    async def incident(**kwargs):
        incidents.append(kwargs["code"])

    monkeypatch.setattr(tasks, "open_generation_incident", incident)
    finished: list = []

    with pytest.raises(TimeoutError):
        _press(monkeypatch, spy, _SAME_DAY_PRESS, finished=finished, session=session)

    assert len(spy.reviews) == 1
    assert spy.seen_at_review == [_ladder(spy.record)]
    assert _record(spy.item) == spy.record
    # rollback 뒤 다시 읽은 행으로 판정해 되돌리고, 그 뒤 종전 실패 처리를 그대로 한다.
    assert events.index("refresh") > events.index("rollback")
    assert events[-1] == "commit"
    assert finished == [(OperationRunState.FAILED, "PROVIDER_TIMEOUT")]
    assert incidents == ["PROVIDER_TIMEOUT"]
    _assert_no_writer_session(spy)


def test_a_failed_restore_does_not_hide_the_original_exception(monkeypatch, caplog):
    spy = _written_slot(monkeypatch, responses=[TimeoutError("review timed out")])
    events, tracking = _tracking_session(spy.item)

    class BrokenRefresh(type(tracking())):
        def refresh(self, _item) -> None:
            events.append("refresh")
            raise ConnectionError("db went away")

    finished: list = []

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        with pytest.raises(TimeoutError):
            _press(
                monkeypatch,
                spy,
                _SAME_DAY_PRESS,
                finished=finished,
                session=lambda: BrokenRefresh(spy.item),
            )

    assert finished == [(OperationRunState.FAILED, "PROVIDER_TIMEOUT")]
    assert "ConnectionError" in caplog.text
    assert "db went away" not in caplog.text  # 예외 이름만 남긴다


def test_a_changed_ai_review_is_not_overwritten_by_the_restore(monkeypatch):
    newer = _passing().payload()

    def concurrent_review(item, philosophy):
        if not spy.seen_at_assess[1:]:
            item.essence_check_summary = {**item.essence_check_summary, "ai_review": newer}
        return _repairable_body_assessment(item, philosophy)

    spy = _written_slot(monkeypatch, assess=concurrent_review)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert spy.seen_at_assess[0] == _ladder(spy.record)
    assert _record(spy.item) == _ladder(spy.record)  # 해제 상태 그대로 — 되돌리지 않았다
    assert spy.item.essence_check_summary["ai_review"] == newer


def test_a_changed_record_is_not_overwritten_by_the_restore(monkeypatch):
    newer_record: dict = {}

    def concurrent_record(item, philosophy):
        if not newer_record:
            newer_record.update(
                {**tasks._stored_generation_attempt(item), "reason": "PROVIDER_TIMEOUT"}
            )
            item.essence_check_summary = {
                **item.essence_check_summary,
                GENERATION_ATTEMPT_KEY: dict(newer_record),
            }
        return _repairable_body_assessment(item, philosophy)

    spy = _written_slot(monkeypatch, assess=concurrent_record)

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert spy.seen_at_assess[0] == _ladder(spy.record)
    assert _record(spy.item) == newer_record
    assert spy.item.essence_check_summary["ai_review"] == spy.original_review


def test_a_philosophy_change_during_the_run_skips_and_restores(monkeypatch):
    """조건 판정 뒤 기준이 바뀌면 저장 본문 구간을 벗어난다 — 작가로 가지 않고 물러난다."""

    spy = _written_slot(monkeypatch)
    newer = SimpleNamespace(**{**vars(spy.philosophy), "id": uuid.uuid4()})
    answers = iter([spy.philosophy, newer])
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: next(answers))

    finished = _press(monkeypatch, spy, _SAME_DAY_PRESS)

    assert finished == [(OperationRunState.FAILED, _UNAVAILABLE)]
    assert spy.releases == ["_release_for_review_only"]
    assert spy.reviews == []
    _assert_no_writer_session(spy)
    assert _record(spy.item) == spy.record


# ── `_generate_single_content_item`의 기본 동작과 review_only ─────────────────


def test_the_scheduled_path_still_skips_the_unchanged_record(monkeypatch):
    spy = _written_slot(monkeypatch)

    state, code, message = _generate_once(monkeypatch, spy.item, _SAME_DAY_PRESS)

    assert (state, code, message) == (tasks.GenerationItemState.SKIPPED, _UNAVAILABLE, _WAIT_MESSAGE)
    assert spy.reviews == []
    _assert_no_writer_session(spy)
    assert _record(spy.item) == spy.record


def test_review_only_never_falls_through_to_the_writer(monkeypatch):
    """지금 기준이 아닌 본문·빈 슬롯은 작가 경로다 — 재검수 전용 실행은 기존 문구로 물러난다."""

    spy = _written_slot(monkeypatch)
    spy.item.content_philosophy_id = uuid.uuid4()
    tasks._release_generation_attempt_for_repair(_WorkerDB(), spy.item)
    _freeze(monkeypatch, _SAME_DAY_PRESS)

    written_elsewhere = tasks._generate_single_content_item(
        _WorkerDB(), spy.item, spy.item.hospital, review_only=True
    )
    spy.item.body = None
    empty = tasks._generate_single_content_item(
        _WorkerDB(), spy.item, spy.item.hospital, review_only=True
    )

    expected = (tasks.GenerationItemState.SKIPPED, _UNAVAILABLE, _WAIT_MESSAGE)
    assert written_elsewhere == expected
    assert empty == expected
    assert spy.reviews == []
    _assert_no_writer_session(spy)
