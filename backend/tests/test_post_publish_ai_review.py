"""공개 글 사후 표본의 자동 독립 검수 스윕 — 결과 적용 규칙과 운영 등록."""

from datetime import datetime, timezone

import pytest

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app
from app.core.config import settings
from app.models.content import ContentStatus
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
from app.workers.dispatch_envelope import PURPOSE_HEADER, expected_purpose
from tests.test_ai_review_hash_binding import _item

NOW = datetime(2026, 10, 7, 3, 10, tzinfo=timezone.utc)


def _published(**overrides):
    item = _item(
        status=ContentStatus.PUBLISHED,
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
        **overrides,
    )
    return item


def _review(item, *, status=ContentAiReviewStatus.PASS, findings=(), reviewed=None):
    target = reviewed or item
    return ContentAiReview(
        status=status,
        confidence=0.95,
        findings=tuple(findings),
        summary="요약",
        model="m",
        candidate_sha256=candidate_sha256(target),
        coverage=candidate_review_coverage(target),
    )


def _hard():
    return ContentAiFinding(
        ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, "근거 없는 효과 단정"
    )


def test_pass_on_current_body_marks_reviewed_by_system():
    item = _published()
    item.essence_check_summary = {"ai_review": {"status": "PASS", "candidate_sha256": "old"}}

    outcome = sweep.apply_review_outcome(item, _review(item), now=NOW)

    assert outcome == "REVIEWED"
    assert item.post_publish_reviewed_at == NOW
    assert item.post_publish_reviewed_by == "system:ai-review"
    # 편집으로 낡은 PASS를 현재 해시로 되돌린다.
    assert item.essence_check_summary["ai_review"]["candidate_sha256"] == candidate_sha256(item)


def test_soft_only_review_is_still_reviewed():
    item = _published()
    soft = ContentAiFinding(
        ContentAiFindingSeverity.SOFT, ContentAiFindingKind.STYLE, "문체를 다듬어 보세요"
    )

    outcome = sweep.apply_review_outcome(
        item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[soft]), now=NOW
    )

    assert outcome == "REVIEWED"


def test_blocking_finding_flags_without_touching_public_state():
    """차단 지적이어도 자동으로 내리지 않는다 — ai_review를 쓰면 공개 가시성이 숨긴다."""
    from app.services.content_publication import public_candidate_review_safe

    item = _published()
    item.essence_check_summary = {"keep": 1}

    outcome = sweep.apply_review_outcome(
        item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[_hard()]), now=NOW
    )

    assert outcome == "FLAGGED"
    assert item.post_publish_reviewed_at is None
    assert "ai_review" not in item.essence_check_summary
    assert item.essence_check_summary["keep"] == 1
    flag = item.essence_check_summary["post_publish_ai_review"]
    assert flag["status"] == "FLAGGED"
    assert flag["findings"] == ["근거 없는 효과 단정"]
    assert flag["candidate_sha256"] == candidate_sha256(item)
    assert public_candidate_review_safe(item) is True


def test_unavailable_changes_nothing():
    item = _published()
    item.essence_check_summary = {"keep": 1}

    outcome = sweep.apply_review_outcome(
        item, _review(item, status=ContentAiReviewStatus.UNAVAILABLE), now=NOW
    )

    assert outcome == "UNAVAILABLE"
    assert item.post_publish_reviewed_at is None
    assert item.essence_check_summary == {"keep": 1}


def test_review_of_a_body_edited_meanwhile_is_not_written():
    item = _published()
    reviewed_body = _review(item)
    item.body = "검수 중에 사람이 고친 본문"

    assert sweep.apply_review_outcome(item, reviewed_body, now=NOW) == "SKIPPED"
    assert item.post_publish_reviewed_at is None


def test_already_reviewed_or_unpublished_rows_are_left_alone():
    reviewed = _published()
    reviewed.post_publish_reviewed_at = NOW
    assert sweep.apply_review_outcome(reviewed, _review(reviewed), now=NOW) == "SKIPPED"

    withheld = _published()
    withheld.status = ContentStatus.WITHHELD
    assert sweep.apply_review_outcome(withheld, _review(withheld), now=NOW) == "SKIPPED"


def test_selection_orders_edited_first_and_skips_flagged_rows():
    sql = str(sweep._review_stmt(10).compile(compile_kwargs={"literal_binds": False}))

    assert "ORDER BY CASE WHEN (content_items.body_updated_at > content_items.published_at)" in sql
    assert "IS DISTINCT FROM" in sql
    assert "content_items.post_publish_reviewed_at IS NULL" in sql


def test_sweep_is_registered_with_signed_purpose_and_content_queue():
    task_name = "app.workers.post_publish_ai_review.review_post_publish_samples"
    entry = celery_app.conf.beat_schedule["post-publish-ai-review"]

    assert entry["task"] == task_name
    assert entry["schedule"].hour == {3} and entry["schedule"].minute == {10}
    assert celery_app.amqp.router.route({}, task_name)["queue"].name == "content"
    assert expected_purpose(task_name) == "post-publish-ai-review"
    assert entry["options"]["headers"][PURPOSE_HEADER] == "post-publish-ai-review"
    assert "app.workers.post_publish_ai_review" in celery_app.conf.include
    assert REDBEAT_SCHEDULE_VERSION >= "2026-10-07.1"
    task = celery_app.tasks[task_name]
    assert task.time_limit > task.soft_time_limit


def test_readiness_expects_the_new_sweep():
    from app.utils.production_readiness import EXPECTED_BEAT_SCHEDULES, EXPECTED_TASKS

    assert "post-publish-ai-review" in EXPECTED_BEAT_SCHEDULES
    assert "app.workers.post_publish_ai_review.review_post_publish_samples" in EXPECTED_TASKS


def test_daily_cap_setting_is_bounded():
    assert settings.POST_PUBLISH_AI_REVIEW_DAILY_CAP == 20
    with pytest.raises(ValueError):
        type(settings)(POST_PUBLISH_AI_REVIEW_DAILY_CAP=-1)
