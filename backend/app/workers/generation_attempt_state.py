"""저장된 생성 시도 기록을 **읽기 전용**으로 해석하는 한 곳.

`essence_check_summary["generation_attempt"]`는 야간 생성·복구 스윕이 쓰는 내구 조각이다.
발행 게이트(`services/content_publication`)는 그 조각을 보고 "대표 이미지를 아직
자동 복구가 소유하고 있는가"를 판정해야 하는데, 쓰기 경로(`workers/tasks`)를 import하면
서비스 계층이 워커 전체를 끌어온다. 그래서 해석만 여기에 둔다 — 쓰기는 그대로 워커가 한다.

저장된 조각에 모르는 필드가 더 있어도 무시한다. 이 파일은 스키마를 강제하지 않는다.

공개 API (호출부가 의존해도 되는 것):

- ``GENERATION_ATTEMPT_KEY`` — `essence_check_summary` 안의 조각 키.
- ``read_generation_attempt(item)`` — 조각의 사본(dict). 없거나 모양이 다르면 빈 dict.
- ``image_attempts_exhausted_today(item, now=None)`` — 오늘(KST) 이 글의 대표 이미지
  자동 시도가 더 남아 있지 않은가. 야간 생성이 "이미지를 포기하고 같은 병원의 인증된
  이미지를 빌릴 시점인가"를 이 값으로 판정한다.
- ``IMAGE_ATTEMPT_REASONS`` / ``IMAGE_ATTEMPT_TERMINAL_REASONS`` — 위 판정이 쓰는 원인 집합.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    environment_attempt_period,
    stored_attempt_period,
)

GENERATION_ATTEMPT_KEY = "generation_attempt"

# 대표 이미지 한 건의 시도로 계수되는 원인. 본문 실패와 섞지 않는다 — 본문이 막힌 글은
# 이미지가 없어서가 아니라 본문 게이트에서 이미 걸린다.
IMAGE_ATTEMPT_REASONS = frozenset(
    {
        "IMAGE_GENERATION_FAILED",
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)

# 그날 안에서 더 살 것이 남지 않은 종착. 예산 계수와 무관하게 소진으로 읽는다.
IMAGE_ATTEMPT_TERMINAL_REASONS = frozenset(
    {
        "IMAGE_GENERATION_RETRIES_EXHAUSTED",
        "CONTENT_IMAGE_POLICY_REJECTED",
    }
)

# 비용 가드 보류는 공급자에게 한 번도 묻지 못한 상태다. 예산을 쓴 적이 없으므로
# 소진으로 읽으면 "가드가 잠깐 막았다"가 "이미지 없이 발행"으로 굳는다.
_NEVER_EXHAUSTED_REASONS = frozenset({"COST_BLOCKED"})


def read_generation_attempt(item: Any) -> dict:
    """저장된 시도 조각의 사본. 없거나 모양이 다르면 빈 dict."""

    summary = getattr(item, "essence_check_summary", None)
    if not isinstance(summary, dict):
        return {}
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(attempt, dict):
        return {}
    return dict(attempt)


def _provider_attempt_count(attempt: dict) -> int:
    raw = attempt.get("provider_attempt_count", attempt.get("attempt_count"))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def image_attempts_exhausted_today(item: Any, now: datetime | None = None) -> bool:
    """오늘(KST) 이 글의 대표 이미지 자동 시도가 더 남아 있지 않은가.

    남아 있다면 발행 게이트는 이미지를 기다린다 — 아직 야간 스윕이 소유한 상태를
    사람의 일이나 영구 무이미지 발행으로 바꾸지 않기 위해서다.
    """

    attempt = read_generation_attempt(item)
    reason = str(attempt.get("reason") or "")
    if not reason or reason in _NEVER_EXHAUSTED_REASONS:
        return False
    if reason not in IMAGE_ATTEMPT_REASONS:
        return False
    if reason in IMAGE_ATTEMPT_TERMINAL_REASONS:
        return True
    if stored_attempt_period(attempt) != environment_attempt_period(now):
        # 날이 바뀌면 하루 예산은 다시 열린다. 어제 소진은 오늘의 소진이 아니다.
        return False
    return _provider_attempt_count(attempt) >= ENVIRONMENT_ATTEMPT_BUDGET
