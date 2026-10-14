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
- ``GENERATION_LADDER_KEYS`` / ``released_generation_attempt(summary)`` — 저장된 본문이
  사라졌을 때 억제만 풀고 예산 사다리는 남기는 변환. 순수 함수라 워커와 Admin API가
  같은 규칙을 쓴다.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.services.content_review_feedback import renewal_feedback  # noqa: F401 — 원장 쪽 re-export
from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    environment_attempt_period,
    stored_attempt_period,
)

GENERATION_ATTEMPT_KEY = "generation_attempt"
GENERATION_BUDGET_KEY = "budget"
GENERATION_BUDGET_VERSION = 2
WRITER_RESULT_LIMIT = 6
REVIEWER_RESULT_LIMIT = 6
# 주제 교체는 슬롯당 최대 두 번이다 — Critical 원인(교정 계단을 다 쓴 사실·안전 차단)만 두 번째를
# 쓸 수 있다(2026-10-14 O5, `topic_swap_fallback.topic_swap_limit`). 주제는 처음 것을 합쳐 셋이다.
# 상한을 올리기만 하므로 옛 기록(주제 ≤ 2)은 그대로 유효하다(`_validate_versioned_budget`).
TOPIC_LIMIT = 3
TOPIC_SWAP_LIMIT = 2
SEMANTIC_TRANSPORT_LIMIT = 3
TEXT_HTTP_ATTEMPT_LIMIT = 36
IMAGE_HTTP_ATTEMPT_LIMIT = 12
# 주제 교체는 새 주제에 작가·검수·본문 전송 예산을 처음부터 준다. 슬롯 평생 상한은
# 주제 수(TOPIC_LIMIT)만큼이다 — 교체가 평생 TOPIC_SWAP_LIMIT번뿐이므로 이 곱이 실제 최대 지출이다.
# 이미지 전송은 주제가 아니라 슬롯의 것이라 평생 IMAGE_HTTP_ATTEMPT_LIMIT 그대로다.
LIFETIME_WRITER_RESULT_LIMIT = TOPIC_LIMIT * WRITER_RESULT_LIMIT
LIFETIME_REVIEWER_RESULT_LIMIT = TOPIC_LIMIT * REVIEWER_RESULT_LIMIT
LIFETIME_TEXT_HTTP_ATTEMPT_LIMIT = TOPIC_LIMIT * TEXT_HTTP_ATTEMPT_LIMIT
PRIOR_TOPIC_SPEND_KEY = "prior_topic_spend"
_PRIOR_TOPIC_COUNTERS = ("writer_results", "reviewer_results", "text_http_attempts")
# 비-Critical 일일 갱신 구간(2026-10-15 누락 방지 5 나머지). 교체·평생 상한을 다 쓴 비-Critical 슬롯은
# KST 하루에 한 번 작은 새 예산 구간을 받는다(`noncritical_renewal`). 평생 계수는 지우지 않고, 구간이
# 열린 획득 위치(`acquisition_count`) 뒤의 획득만 그 구간의 지출로 센다 — `prior_topic_spend`와 같은 경계다.
# 구간이 있으면 주제당·평생 상한 대신 구간 상한만 본다(세션 수 × 정상 세션 한도 — 작가 3·검수 3).
RENEWAL_KEY = "renewal"
# 한 세션이 살 수 있는 작가 결과 수 — `tasks.MAX_GENERATIONS_PER_SESSION`과 같은 값(테스트로 묶는다).
RENEWAL_WRITER_RESULTS_PER_SESSION = 3
# 검수 결과도 정상 세션과 같다 — 세션의 검수는 작가 후보마다 한 번(문체 보완·HARD 삭제 재작성의 재검수
# 포함)이라 작가 수와 같다. 상한 때문에 세션 도중 통과 본문·Critical 판정을 버리지 않는다.
RENEWAL_REVIEWER_RESULTS_PER_SESSION = RENEWAL_WRITER_RESULTS_PER_SESSION
# 일일 갱신이 바꾼 주제(교체 예산 밖)를 담는 원장 목록. `topics`(TOPIC_LIMIT, Critical 교체 예산)와 따로 센다.
RENEWAL_TOPICS_KEY = "renewal_topics"
RENEWAL_FEEDBACK_LIMIT = 5

AcquisitionKind = Literal["WRITER", "REVIEWER", "IMAGE"]
AcquisitionOutcome = Literal["RESULT", "ERROR", "UNKNOWN"]


class GenerationBudgetUnknown(RuntimeError):
    """Raised when a legacy slot has no trustworthy lifetime-spend counters."""


class GenerationBudgetExceeded(RuntimeError):
    """Raised before provider I/O when a slot lifetime ceiling is exhausted."""


# 한 획득(작가 호출 하나)의 전송 상한(`SEMANTIC_TRANSPORT_LIMIT`)에 닿은 표시. 슬롯·주제 예산의
# 소진이 아니다 — 한 작가 호출 안의 거절·재작성 회차와 전송 재시도가 그 호출의 몫을 다 쓴 것이다.
ACQUISITION_TRANSPORT_EXHAUSTED = "ACQUISITION_TRANSPORT_EXHAUSTED"


def is_acquisition_transport_exhausted(error: BaseException) -> bool:
    """한 작가 호출의 전송 상한에 닿은 예외인가(슬롯 예산 소진과 구분한다)."""

    return isinstance(error, GenerationBudgetExceeded) and str(error) == (
        ACQUISITION_TRANSPORT_EXHAUSTED
    )


@dataclass(frozen=True, slots=True)
class GenerationBudgetSnapshot:
    version: int
    legacy_state: str
    writer_results: int
    reviewer_results: int
    topics: tuple[str, ...]
    topic_swaps: int
    text_http_attempts: int
    text_http_reserved: int
    image_http_attempts: int
    image_http_reserved: int
    reset_record: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AcquisitionReservation:
    acquisition_id: str
    kind: AcquisitionKind
    cached_payload: dict[str, Any] | None


def _zero_budget(topic_id: str | None = None) -> dict[str, Any]:
    return {
        "version": GENERATION_BUDGET_VERSION,
        "legacy_state": "KNOWN_ZERO",
        "writer_results": 0,
        "reviewer_results": 0,
        "topics": [topic_id] if topic_id else [],
        "topic_swaps": 0,
        "text_http_attempts": 0,
        "text_http_reserved": 0,
        "image_http_attempts": 0,
        "image_http_reserved": 0,
        "acquisitions": [],
        "reset_record": None,
    }


def fresh_generation_attempt(*, topic_id: str | None = None) -> dict[str, Any]:
    """Return the only valid known-zero marker for a genuinely new content slot."""

    return {GENERATION_BUDGET_KEY: _zero_budget(topic_id)}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _legacy_budget(attempt: dict[str, Any]) -> dict[str, Any]:
    raw_count = attempt.get("provider_attempt_count", attempt.get("attempt_count"))
    count = _nonnegative_int(raw_count)
    if count is None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    reason = str(attempt.get("reason") or "")
    budget = _zero_budget(None)
    budget["legacy_state"] = "KNOWN_COUNTERS"
    if reason in IMAGE_ATTEMPT_REASONS:
        budget["image_http_attempts"] = min(count, IMAGE_HTTP_ATTEMPT_LIMIT)
    else:
        # The old counter did not distinguish writer, reviewer, or transport. Charging
        # it to both semantic lanes is deliberately conservative and never grants a
        # legacy slot more allowance than the stored evidence supports.
        budget["writer_results"] = min(count, WRITER_RESULT_LIMIT)
        budget["reviewer_results"] = min(count, REVIEWER_RESULT_LIMIT)
        budget["text_http_attempts"] = min(count, TEXT_HTTP_ATTEMPT_LIMIT)
    budget["legacy_baseline"] = {
        "source": "LEGACY_COUNTERS",
        "writer_results": budget["writer_results"],
        "reviewer_results": budget["reviewer_results"],
        "text_http_attempts": budget["text_http_attempts"],
        "image_http_attempts": budget["image_http_attempts"],
    }
    return budget


_VERSIONED_COUNTER_KEYS = (
    "writer_results",
    "reviewer_results",
    "topic_swaps",
    "text_http_attempts",
    "text_http_reserved",
    "image_http_attempts",
    "image_http_reserved",
)
_LEGACY_STATES = frozenset({"KNOWN_ZERO", "KNOWN_COUNTERS", "REPLACED"})
_ACQUISITION_KINDS = frozenset({"WRITER", "REVIEWER", "IMAGE"})
_ACQUISITION_STATUSES = frozenset({"RESERVED", "RESULT", "ERROR", "UNKNOWN"})


def _strict_nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _valid_reset_record(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("event") == "LEGACY_BUDGET_REPLACED"
        and all(
            isinstance(value.get(key), str) and bool(value[key])
            for key in ("actor", "reason", "idempotency_key", "replaced_at")
        )
        and isinstance(value.get("previous_snapshot"), dict)
    )


def _valid_renewal_segment(segment: Any, raw: dict[str, Any], acquisitions: int) -> bool:
    """일일 갱신 구간의 모양. 구간이 새 예산을 만드는 유일한 표시라 모양이 틀리면 지출 불명이다."""

    if not isinstance(segment, dict):
        return False
    period = segment.get("period")
    count = _strict_nonnegative_int(segment.get("count"))
    sessions = _strict_nonnegative_int(segment.get("sessions"))
    boundary = _strict_nonnegative_int(segment.get("acquisition_count"))
    topic = segment.get("topic_id")
    feedback = segment.get("feedback", [])
    return (
        isinstance(period, str)
        and bool(period)
        and count is not None
        and count >= 1
        and sessions is not None
        and sessions >= 1
        and boundary is not None
        and boundary <= acquisitions
        and (topic is None or (isinstance(topic, str) and bool(topic)))
        and isinstance(feedback, list)
        and len(feedback) <= RENEWAL_FEEDBACK_LIMIT
        and all(isinstance(entry, str) for entry in feedback)
        and all(
            _strict_nonnegative_int(segment.get(key)) is not None
            and segment[key] <= raw[key]
            for key in _PRIOR_TOPIC_COUNTERS
        )
    )


def _validate_versioned_budget(raw: dict[str, Any]) -> dict[str, Any]:
    """Reject partial or malformed v2 state before it can mint fresh allowance."""

    if raw.get("version") != GENERATION_BUDGET_VERSION:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    if raw.get("legacy_state") not in _LEGACY_STATES:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    for key in _VERSIONED_COUNTER_KEYS:
        if _strict_nonnegative_int(raw.get(key)) is None:
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    topics = raw.get("topics")
    if (
        not isinstance(topics, list)
        or len(topics) > TOPIC_LIMIT
        or any(not isinstance(topic, str) or not topic for topic in topics)
        or len(set(topics)) != len(topics)
    ):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    acquisitions = raw.get("acquisitions")
    if not isinstance(acquisitions, list):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    acquisition_ids: set[str] = set()
    computed_writer_results = 0
    computed_reviewer_results = 0
    computed_text_attempts = 0
    computed_text_reserved = 0
    computed_image_attempts = 0
    computed_image_reserved = 0
    for entry in acquisitions:
        if not isinstance(entry, dict):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
        acquisition_id = entry.get("id")
        kind = entry.get("kind")
        status = entry.get("status")
        transport_reserved = _strict_nonnegative_int(entry.get("transport_reserved"))
        transport_attempts = entry.get("transport_attempts", 0)
        if (
            not isinstance(acquisition_id, str)
            or not acquisition_id
            or acquisition_id in acquisition_ids
            or kind not in _ACQUISITION_KINDS
            or status not in _ACQUISITION_STATUSES
            or transport_reserved is None
            or transport_reserved > SEMANTIC_TRANSPORT_LIMIT
            or _strict_nonnegative_int(transport_attempts) is None
            or transport_attempts > SEMANTIC_TRANSPORT_LIMIT
            or not isinstance(entry.get("reserved_at"), str)
            or ("payload" in entry and not isinstance(entry["payload"], dict))
        ):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
        acquisition_ids.add(acquisition_id)
        if status in {"RESULT", "ERROR"} and kind == "WRITER":
            computed_writer_results += 1
        if status in {"RESULT", "ERROR"} and kind == "REVIEWER":
            computed_reviewer_results += 1
        if kind == "IMAGE":
            computed_image_attempts += transport_attempts
            if status == "RESERVED":
                computed_image_reserved += transport_reserved - transport_attempts
        else:
            computed_text_attempts += transport_attempts
            if status == "RESERVED":
                computed_text_reserved += transport_reserved - transport_attempts

    baseline = raw.get("legacy_baseline")
    if raw["legacy_state"] == "KNOWN_COUNTERS":
        if (
            not isinstance(baseline, dict)
            or baseline.get("source") != "LEGACY_COUNTERS"
            or any(
                _strict_nonnegative_int(baseline.get(key)) is None
                for key in (
                    "writer_results",
                    "reviewer_results",
                    "text_http_attempts",
                    "image_http_attempts",
                )
            )
        ):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    elif baseline is not None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    else:
        baseline = {
            "writer_results": 0,
            "reviewer_results": 0,
            "text_http_attempts": 0,
            "image_http_attempts": 0,
        }

    if (
        raw["writer_results"] != baseline["writer_results"] + computed_writer_results
        or raw["reviewer_results"]
        != baseline["reviewer_results"] + computed_reviewer_results
        or raw["text_http_attempts"]
        != baseline["text_http_attempts"] + computed_text_attempts
        or raw["text_http_reserved"] != computed_text_reserved
        or raw["image_http_attempts"]
        != baseline["image_http_attempts"] + computed_image_attempts
        or raw["image_http_reserved"] != computed_image_reserved
    ):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    prior = raw.get(PRIOR_TOPIC_SPEND_KEY)
    if prior is not None:
        count = (
            _strict_nonnegative_int(prior.get("acquisition_count"))
            if isinstance(prior, dict)
            else None
        )
        if (
            count is None
            or count > len(acquisitions)
            or raw["topic_swaps"] < 1
            or any(
                _strict_nonnegative_int(prior.get(key)) is None
                or prior[key] > raw[key]
                for key in _PRIOR_TOPIC_COUNTERS
            )
        ):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    renewal = raw.get(RENEWAL_KEY)
    if renewal is not None and not _valid_renewal_segment(renewal, raw, len(acquisitions)):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    renewal_topics = raw.get(RENEWAL_TOPICS_KEY)
    if renewal_topics is not None and (
        not isinstance(renewal_topics, list)
        or any(not isinstance(topic, str) or not topic for topic in renewal_topics)
        or len(set(renewal_topics)) != len(renewal_topics)
    ):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")

    reset_record = raw.get("reset_record")
    if reset_record is not None and not isinstance(reset_record, dict):
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    if raw["legacy_state"] == "REPLACED":
        if not _valid_reset_record(reset_record):
            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    elif reset_record is not None:
        raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
    return dict(raw)


def _budget_dict(attempt: dict[str, Any]) -> dict[str, Any]:
    raw = attempt.get(GENERATION_BUDGET_KEY)
    if not isinstance(raw, dict):
        return _legacy_budget(attempt)
    return _validate_versioned_budget(raw)


def read_generation_budget(attempt: dict[str, Any]) -> GenerationBudgetSnapshot:
    """Parse a versioned budget or conservatively translate known legacy counters."""

    budget = _budget_dict(attempt)
    return _snapshot_from_budget(budget)


def legacy_generation_budget_replaced(summary: Any) -> bool:
    """Match the mutation gate that rejects a second authenticated replacement."""

    if not isinstance(summary, dict):
        return False
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(attempt, dict):
        return False
    budget = attempt.get(GENERATION_BUDGET_KEY)
    return isinstance(budget, dict) and _valid_reset_record(budget.get("reset_record"))


def _snapshot_from_budget(budget: dict[str, Any]) -> GenerationBudgetSnapshot:
    topics = budget["topics"]
    topic_values = tuple(topics)
    reset = budget.get("reset_record")
    return GenerationBudgetSnapshot(
        version=GENERATION_BUDGET_VERSION,
        legacy_state=str(budget["legacy_state"]),
        writer_results=budget["writer_results"],
        reviewer_results=budget["reviewer_results"],
        topics=topic_values,
        topic_swaps=budget["topic_swaps"],
        text_http_attempts=budget["text_http_attempts"],
        text_http_reserved=budget["text_http_reserved"],
        image_http_attempts=budget["image_http_attempts"],
        image_http_reserved=budget["image_http_reserved"],
        reset_record=dict(reset) if isinstance(reset, dict) else None,
    )


def _active_acquisitions(budget: dict[str, Any], kind: AcquisitionKind) -> int:
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return 0
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("kind") == kind
        and entry.get("status") == "RESERVED"
    )


@dataclass(frozen=True, slots=True)
class _TopicUsage:
    writer_results: int
    reviewer_results: int
    active_writers: int
    active_reviewers: int
    text_http_attempts: int
    text_http_reserved: int


def _entries(budget: dict[str, Any]) -> list[dict[str, Any]]:
    raw = budget.get("acquisitions")
    return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []


def writer_acquisition_prefix(
    context_id: str, topic_id: str, session_scope: str, fingerprint: str
) -> str:
    """작가 획득 id의 접두. `_topic_boundary`가 이 안의 `:{topic_id}:` 토큰을 찾는다."""

    return f"writer:{context_id}:{topic_id}:{session_scope}:{fingerprint}"


# 표시 없는 옛 교체 예산은 `writer_acquisition_prefix`의 id 형식(`:{topic_id}:` 토큰)에 기댄다.
def _topic_boundary(budget: dict[str, Any]) -> int | None:
    """현재 주제의 첫 획득 위치. 교체 전이면 None — 평생 계수가 곧 현재 주제의 계수다.

    교체 표시(`prior_topic_spend`)가 있으면 그 위치를 쓴다. 표시 없이 교체만 기록된 옛
    예산(이 수정 이전의 교체)은 획득 목록이 시간순으로만 늘어난다는 사실로 경계를 찾는다 —
    작가 획득 id에는 주제가 들어가므로(`writer:{context}:{topic}:…`) 새 주제의 첫 작가
    획득이 경계다. 아직 없으면(교체 직후 곧바로 소진된 경우) 목록 전체가 옛 주제의 것이다.
    """

    prior = budget.get(PRIOR_TOPIC_SPEND_KEY)
    if isinstance(prior, dict):
        return int(prior["acquisition_count"])
    if int(budget.get("topic_swaps") or 0) < 1:
        return None
    entries = _entries(budget)
    topics = budget.get("topics") or []
    if topics:
        marker = f":{topics[-1]}:"
        for index, entry in enumerate(entries):
            if entry.get("kind") == "WRITER" and marker in str(entry.get("id") or ""):
                return index
    return len(entries)


def _current_topic_usage(budget: dict[str, Any]) -> _TopicUsage:
    boundary = _topic_boundary(budget)
    if boundary is None:
        return _TopicUsage(
            writer_results=budget["writer_results"],
            reviewer_results=budget["reviewer_results"],
            active_writers=_active_acquisitions(budget, "WRITER"),
            active_reviewers=_active_acquisitions(budget, "REVIEWER"),
            text_http_attempts=budget["text_http_attempts"],
            text_http_reserved=budget["text_http_reserved"],
        )
    return _usage_since(budget, boundary)


def _usage_since(budget: dict[str, Any], boundary: int) -> _TopicUsage:
    """경계(획득 위치) 뒤의 본문 획득만 센 지출 — 주제 교체·일일 갱신 구간이 같은 계산을 쓴다."""

    writer = reviewer = active_writers = active_reviewers = attempts = reserved = 0
    for entry in _entries(budget)[boundary:]:
        kind = entry.get("kind")
        if kind == "IMAGE":
            continue
        used = _nonnegative_int(entry.get("transport_attempts")) or 0
        held = _nonnegative_int(entry.get("transport_reserved")) or 0
        attempts += used
        status = entry.get("status")
        if status == "RESERVED":
            reserved += max(0, held - used)
            if kind == "WRITER":
                active_writers += 1
            else:
                active_reviewers += 1
        elif status in {"RESULT", "ERROR"}:
            if kind == "WRITER":
                writer += 1
            else:
                reviewer += 1
    return _TopicUsage(writer, reviewer, active_writers, active_reviewers, attempts, reserved)


def _topic_writers_full(usage: _TopicUsage) -> bool:
    return usage.writer_results + usage.active_writers >= WRITER_RESULT_LIMIT


def renewal_limits(sessions: int) -> tuple[int, int, int]:
    """일일 갱신 구간 하나의 (작가 결과, 검수 결과, 본문 전송) 상한."""

    sessions = max(int(sessions), 1)
    writers = sessions * RENEWAL_WRITER_RESULTS_PER_SESSION
    reviewers = sessions * RENEWAL_REVIEWER_RESULTS_PER_SESSION
    return writers, reviewers, (writers + reviewers) * SEMANTIC_TRANSPORT_LIMIT


def _renewal_segment(budget: dict[str, Any]) -> dict[str, Any] | None:
    segment = budget.get(RENEWAL_KEY)
    return segment if isinstance(segment, dict) else None


def renewal_segment(attempt: dict[str, Any]) -> dict[str, Any] | None:
    """예산의 일일 갱신 구간 사본. 구간이 없거나 예산을 믿을 수 없으면 ``None``."""

    try:
        segment = _renewal_segment(_budget_dict(attempt))
    except GenerationBudgetUnknown:
        return None
    return copy.deepcopy(segment) if segment is not None else None


def _renewal_writers_full(budget: dict[str, Any], segment: dict[str, Any]) -> bool:
    usage = _usage_since(budget, int(segment["acquisition_count"]))
    writers, _reviewers, _text = renewal_limits(int(segment["sessions"]))
    return usage.writer_results + usage.active_writers >= writers


def open_renewal_segment(
    attempt: dict[str, Any],
    *,
    period: str,
    sessions: int,
    topic_id: str | None,
    feedback: list[str] | None = None,
) -> dict[str, Any]:
    """KST 하루의 새 일일 갱신 구간을 연다. 같은 날 구간이 이미 있으면 그대로 돌려준다(멱등).

    평생 계수는 그대로 두고 — 지출 사실은 지우지 않는다 — 여는 시점의 획득 위치와 계수를 구간에
    남긴다. `reserve_acquisition`은 그 위치 뒤의 획득만 이 구간의 지출로 센다.
    """

    budget = _budget_dict(attempt)
    previous = _renewal_segment(budget)
    if previous is not None and str(previous.get("period")) >= period:
        # 같은 날이거나(멱등) 자정을 넘긴 늦은 pass가 어제 날짜로 여는 경우다 — 날짜는 앞으로만 간다.
        # ISO 날짜 문자열은 사전순이 곧 날짜순이다.
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    snapshot = _snapshot_from_budget(budget)
    budget[RENEWAL_KEY] = {
        "period": period,
        "count": int(previous["count"]) + 1 if previous is not None else 1,
        "sessions": max(int(sessions), 1),
        "acquisition_count": len(_entries(budget)),
        "writer_results": snapshot.writer_results,
        "reviewer_results": snapshot.reviewer_results,
        "text_http_attempts": snapshot.text_http_attempts,
        "topic_id": topic_id or None,
        "feedback": [str(entry) for entry in (feedback or []) if str(entry).strip()][
            -RENEWAL_FEEDBACK_LIMIT:
        ],
    }
    _validate_versioned_budget(budget)
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def renewal_segment_spent(attempt: dict[str, Any]) -> bool:
    """열린 일일 갱신 구간이 정상 세션 하나를 더 살 수 없는가(작가·검수·전송 중 하나라도 모자람).

    구간이 없거나 예산을 믿을 수 없으면 거짓이다 — 그 판정은 호출부의 다른 규칙이 맡는다.
    """

    try:
        budget = _budget_dict(attempt)
    except GenerationBudgetUnknown:
        return False
    segment = _renewal_segment(budget)
    if segment is None:
        return False
    usage = _usage_since(budget, int(segment["acquisition_count"]))
    writers, reviewers, text_limit = renewal_limits(int(segment["sessions"]))
    session_writers, session_reviewers, session_text = renewal_limits(1)
    # 정상 세션 하나를 통째로 살 수 없으면 오늘은 끝이다 — 반쪽 세션은 도중에 상한에 걸려 통과 본문을
    # 버릴 수 있고, 전송만 바닥난 구간을 다시 집으면 아무것도 사지 못한 실행만 반복된다.
    return (
        writers - (usage.writer_results + usage.active_writers) < session_writers
        or reviewers - (usage.reviewer_results + usage.active_reviewers) < session_reviewers
        or text_limit - (usage.text_http_attempts + usage.text_http_reserved) < session_text
    )


def writer_purchase_blocked(attempt: dict[str, Any]) -> bool:
    """지금 이 예산으로 작가 결과 하나를 예약할 수 없는가(주제당·평생·구간·전송 상한 중 하나)."""

    try:
        reserve_acquisition(
            copy.deepcopy(attempt), acquisition_id="probe:writer-purchase", kind="WRITER"
        )
    except GenerationBudgetUnknown:
        return False
    except GenerationBudgetExceeded:
        return True
    return False


def current_topic_writers_exhausted(attempt: dict[str, Any]) -> bool:
    """현재 주제가 작가 결과를 더 살 수 없는가 — `reserve_acquisition`의 주제당 작가 상한.

    주제당 작가 6회는 하루 예산(2세션 × 3회)으로 첫날에 닿는다. 그 뒤의 이틀은 지출 없는
    공회전이므로 주제 교체 판정(`topic_swap_fallback.exhausted_body_sample_reason`)이 이
    값으로 소진 일수를 기다리지 않는다. 신뢰할 수 없는 예산은 소진으로 보지 않는다 —
    그 슬롯은 교체 상한 판정(`topic_swap_budget_left`)이 따로 막는다.
    """

    try:
        budget = _budget_dict(attempt)
    except GenerationBudgetUnknown:
        return False
    segment = _renewal_segment(budget)
    if segment is not None:
        # 일일 갱신 구간에서는 그 구간의 작가 상한이 "더 살 수 없다"의 기준이다.
        return _renewal_writers_full(budget, segment)
    return _topic_writers_full(_current_topic_usage(budget))


def reserve_acquisition(
    attempt: dict[str, Any], *, acquisition_id: str, kind: AcquisitionKind
) -> dict[str, Any]:
    """Reserve worst-case provider attempts before I/O; safe under redelivery."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    prior = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if prior is not None:
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    snapshot = _snapshot_from_budget(budget)
    segment = _renewal_segment(budget)
    if segment is not None and kind != "IMAGE":
        return _reserve_in_renewal(attempt, budget, entries, segment, acquisition_id, kind)
    usage = _current_topic_usage(budget)
    # 평생 상한(주제 수 × 주제당 상한)은 추정한 주제 경계가 틀려도 지출을 막는 마지막 방어선이다.
    if kind == "WRITER" and (
        _topic_writers_full(usage)
        or snapshot.writer_results + _active_acquisitions(budget, kind)
        >= LIFETIME_WRITER_RESULT_LIMIT
    ):
        raise GenerationBudgetExceeded("WRITER_RESULTS_EXHAUSTED")
    if kind == "REVIEWER" and (
        usage.reviewer_results + usage.active_reviewers >= REVIEWER_RESULT_LIMIT
        or snapshot.reviewer_results + _active_acquisitions(budget, kind)
        >= LIFETIME_REVIEWER_RESULT_LIMIT
    ):
        raise GenerationBudgetExceeded("REVIEWER_RESULTS_EXHAUSTED")
    attempt_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    spent = _nonnegative_int(budget.get(spent_key)) or 0
    reserved = _nonnegative_int(budget.get(attempt_key)) or 0
    if kind == "IMAGE":
        exhausted = spent + reserved + SEMANTIC_TRANSPORT_LIMIT > IMAGE_HTTP_ATTEMPT_LIMIT
    else:
        exhausted = (
            usage.text_http_attempts + usage.text_http_reserved + SEMANTIC_TRANSPORT_LIMIT
            > TEXT_HTTP_ATTEMPT_LIMIT
            or spent + reserved + SEMANTIC_TRANSPORT_LIMIT > LIFETIME_TEXT_HTTP_ATTEMPT_LIMIT
        )
    if exhausted:
        raise GenerationBudgetExceeded(
            "IMAGE_HTTP_ATTEMPTS_EXHAUSTED" if kind == "IMAGE" else "TEXT_HTTP_ATTEMPTS_EXHAUSTED"
        )
    budget[attempt_key] = reserved + SEMANTIC_TRANSPORT_LIMIT
    entries.append(
        {
            "id": acquisition_id,
            "kind": kind,
            "status": "RESERVED",
            "transport_reserved": SEMANTIC_TRANSPORT_LIMIT,
            "reserved_at": datetime.now(UTC).isoformat(),
        }
    )
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def _append_reservation(
    attempt: dict[str, Any],
    budget: dict[str, Any],
    entries: list[Any],
    acquisition_id: str,
    kind: AcquisitionKind,
) -> dict[str, Any]:
    attempt_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    reserved = _nonnegative_int(budget.get(attempt_key)) or 0
    budget[attempt_key] = reserved + SEMANTIC_TRANSPORT_LIMIT
    entries.append(
        {
            "id": acquisition_id,
            "kind": kind,
            "status": "RESERVED",
            "transport_reserved": SEMANTIC_TRANSPORT_LIMIT,
            "reserved_at": datetime.now(UTC).isoformat(),
        }
    )
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def _reserve_in_renewal(
    attempt: dict[str, Any],
    budget: dict[str, Any],
    entries: list[Any],
    segment: dict[str, Any],
    acquisition_id: str,
    kind: AcquisitionKind,
) -> dict[str, Any]:
    """일일 갱신 구간 안의 본문 예약. 구간 뒤의 획득만 세고 주제당·평생 상한은 보지 않는다.

    구간은 KST 하루에 한 번만 열리므로(`noncritical_renewal`) 하루 지출의 끝은 이 상한이다.
    """

    usage = _usage_since(budget, int(segment["acquisition_count"]))
    writers, reviewers, text_limit = renewal_limits(int(segment["sessions"]))
    if kind == "WRITER" and usage.writer_results + usage.active_writers >= writers:
        raise GenerationBudgetExceeded("WRITER_RESULTS_EXHAUSTED")
    if kind == "REVIEWER" and usage.reviewer_results + usage.active_reviewers >= reviewers:
        raise GenerationBudgetExceeded("REVIEWER_RESULTS_EXHAUSTED")
    if usage.text_http_attempts + usage.text_http_reserved + SEMANTIC_TRANSPORT_LIMIT > text_limit:
        raise GenerationBudgetExceeded("TEXT_HTTP_ATTEMPTS_EXHAUSTED")
    return _append_reservation(attempt, budget, entries, acquisition_id, kind)


def acquisition_completed(
    attempt: dict[str, Any],
    *,
    acquisition_id: str,
    transport_attempts: int,
    outcome: AcquisitionOutcome,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Checkpoint a reserved acquisition without treating ambiguous calls as free."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    index = next(
        (
            position
            for position, entry in enumerate(entries)
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if index is None:
        raise GenerationBudgetExceeded("ACQUISITION_NOT_RESERVED")
    entry = entries[index]
    if entry.get("status") != "RESERVED":
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    used = max(0, min(int(transport_attempts), SEMANTIC_TRANSPORT_LIMIT))
    kind = str(entry.get("kind") or "")
    reserved_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    recorded = _nonnegative_int(entry.get("transport_attempts")) or 0
    final_transport_attempts = max(recorded, used)
    remaining_reservation = max(0, SEMANTIC_TRANSPORT_LIMIT - recorded)
    budget[reserved_key] = max(
        0, (_nonnegative_int(budget.get(reserved_key)) or 0) - remaining_reservation
    )
    budget[spent_key] = (
        _nonnegative_int(budget.get(spent_key)) or 0
    ) + max(0, used - recorded)
    if outcome in {"RESULT", "ERROR"} and kind == "WRITER":
        budget["writer_results"] = (_nonnegative_int(budget.get("writer_results")) or 0) + 1
    if outcome in {"RESULT", "ERROR"} and kind == "REVIEWER":
        budget["reviewer_results"] = (_nonnegative_int(budget.get("reviewer_results")) or 0) + 1
    entries[index] = {
        **entry,
        "status": outcome,
        "transport_attempts": final_transport_attempts,
        "completed_at": datetime.now(UTC).isoformat(),
        **({"payload": payload} if payload is not None else {}),
    }
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def transport_attempt_started(
    attempt: dict[str, Any], *, acquisition_id: str
) -> dict[str, Any]:
    """Move one reserved transport attempt to consumed before provider I/O."""

    budget = _budget_dict(attempt)
    entries = list(budget.get("acquisitions") or [])
    index = next(
        (
            position
            for position, entry in enumerate(entries)
            if isinstance(entry, dict) and entry.get("id") == acquisition_id
        ),
        None,
    )
    if index is None or entries[index].get("status") != "RESERVED":
        raise GenerationBudgetExceeded("ACQUISITION_NOT_RESERVED")
    entry = entries[index]
    recorded = _nonnegative_int(entry.get("transport_attempts")) or 0
    if recorded >= SEMANTIC_TRANSPORT_LIMIT:
        raise GenerationBudgetExceeded(ACQUISITION_TRANSPORT_EXHAUSTED)
    kind = str(entry.get("kind") or "")
    reserved_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
    spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
    budget[reserved_key] = max(
        0, (_nonnegative_int(budget.get(reserved_key)) or 0) - 1
    )
    budget[spent_key] = (_nonnegative_int(budget.get(spent_key)) or 0) + 1
    entries[index] = {**entry, "transport_attempts": recorded + 1}
    budget["acquisitions"] = entries
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def record_topic_swap(
    attempt: dict[str, Any],
    *,
    from_topic_id: str | None,
    to_topic_id: str,
    renewal: bool = False,
) -> dict[str, Any]:
    """Record one lifetime topic swap (at most TOPIC_SWAP_LIMIT); the new topic gets a fresh budget.

    ``renewal=True``는 일일 갱신의 주제 변경이다 — 교체 예산 밖의 `renewal_topics`에만 남긴다.

    평생 계수(writer_results 등)는 그대로 두고 — 지출 사실은 지우지 않는다 — 교체 시점의
    획득 위치와 계수를 `prior_topic_spend`로 남긴다. `reserve_acquisition`은 그 위치 뒤의
    획득만 현재 주제의 지출로 센다. 평생 상한은 TOPIC_LIMIT × 주제당 상한이다.
    """

    budget = _budget_dict(attempt)
    snapshot = _snapshot_from_budget(budget)
    renewal_topics = [str(topic) for topic in (budget.get(RENEWAL_TOPICS_KEY) or [])]
    if renewal:
        # 일일 갱신의 주제 변경 — 교체 예산(`topic_swaps`·`topics`, Critical W6 계단)을 쓰지 않고 자기 목록에만
        # 남긴다. 지출 경계는 곧이어 여는 갱신 구간(`open_renewal_segment`)이 정한다.
        if to_topic_id and to_topic_id not in renewal_topics and to_topic_id not in snapshot.topics:
            renewal_topics.append(to_topic_id)
        budget[RENEWAL_TOPICS_KEY] = renewal_topics
        _validate_versioned_budget(budget)
        return {**attempt, GENERATION_BUDGET_KEY: budget}
    if snapshot.topic_swaps >= TOPIC_SWAP_LIMIT:
        raise GenerationBudgetExceeded("TOPIC_SWAP_EXHAUSTED")
    topics = list(snapshot.topics)
    for topic in (from_topic_id, to_topic_id):
        # 갱신이 고른 주제에서 떠나는 교체는 그 주제를 다시 세지 않는다(갱신 목록에 이미 있다).
        if topic and topic not in topics and not (topic == from_topic_id and topic in renewal_topics):
            topics.append(topic)
    if len(topics) > TOPIC_LIMIT:
        raise GenerationBudgetExceeded("TOPIC_LIMIT_EXHAUSTED")
    budget["topics"] = topics
    budget["topic_swaps"] = snapshot.topic_swaps + 1
    # 정규 교체(Critical 교정 소진의 W6 계단 등)는 일일 갱신 구간을 닫는다 — 새 주제는 주제당·평생
    # 상한으로 산다. 살 수 없으면 비-Critical 사다리가 다시 갱신으로 넘긴다(`noncritical_renewal`).
    budget.pop(RENEWAL_KEY, None)
    budget[PRIOR_TOPIC_SPEND_KEY] = {
        "acquisition_count": len(_entries(budget)),
        "writer_results": snapshot.writer_results,
        "reviewer_results": snapshot.reviewer_results,
        "text_http_attempts": snapshot.text_http_attempts,
    }
    return {**attempt, GENERATION_BUDGET_KEY: budget}


def replace_unknown_legacy_budget(
    attempt: dict[str, Any],
    *,
    actor: str,
    reason: str,
    idempotency_key: str,
    replaced_at: datetime | None = None,
) -> dict[str, Any]:
    """Mint one replacement lifetime after an authenticated, audited decision."""

    raw_budget = attempt.get(GENERATION_BUDGET_KEY)
    if isinstance(raw_budget, dict):
        if _valid_reset_record(raw_budget.get("reset_record")):
            raise GenerationBudgetExceeded("LEGACY_BUDGET_ALREADY_REPLACED")
        try:
            _validate_versioned_budget(raw_budget)
        except GenerationBudgetUnknown:
            legacy_spend_is_unknown = True
        else:
            raise GenerationBudgetExceeded("LEGACY_BUDGET_ALREADY_REPLACED")
    else:
        try:
            _legacy_budget(attempt)
        except GenerationBudgetUnknown:
            legacy_spend_is_unknown = True
        else:
            raise GenerationBudgetExceeded("LEGACY_BUDGET_IS_KNOWN")
    if not legacy_spend_is_unknown:  # pragma: no cover - the else branch raises
        raise GenerationBudgetExceeded("LEGACY_BUDGET_IS_KNOWN")
    budget = _zero_budget(None)
    budget["legacy_state"] = "REPLACED"
    budget["reset_record"] = {
        "event": "LEGACY_BUDGET_REPLACED",
        "actor": actor,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "replaced_at": (replaced_at or datetime.now(UTC)).isoformat(),
        "previous_snapshot": copy.deepcopy(attempt),
    }
    return {**attempt, GENERATION_BUDGET_KEY: budget}


LEGACY_SPEND_UNKNOWN_CODE = "LEGACY_SPEND_UNKNOWN"


def attempt_spend_stays_unknown(previous: dict[str, Any], reason: str) -> bool:
    """이번 시도 기록이 지출 계수 없이 남아야 하는가(모르는 지출을 0으로 바꾸지 않는다).

    예산 표시가 없고 옛 계수도 없는 기록은 지출을 모른다(`_legacy_budget`). 그 기록 위에
    `LEGACY_SPEND_UNKNOWN` 실패나 그 기록을 덮는 결정(07:45 게이트의 CONTENT_NOT_GENERATED 등)을
    쓰면서 계수 0을 함께 쓰면, 다음 읽기가 KNOWN_COUNTERS(0)로 읽어 교체·감사 없이 새 예산을
    만든다. 기록이 아예 없는 슬롯의 다른 원인은 종전대로 계수를 쓴다.
    """

    if GENERATION_BUDGET_KEY in previous:
        return False
    try:
        _legacy_budget(previous)
    except GenerationBudgetUnknown:
        return reason == LEGACY_SPEND_UNKNOWN_CODE or bool(previous.get("reason"))
    return False


def legacy_budget_is_replaceable(
    attempt: dict[str, Any], *, legacy_incident_open: bool = False
) -> bool:
    """감사된 레거시 예산 교체 대상인가.

    두 경우다. (1) 지출을 정말 모른다(`GenerationBudgetUnknown`). (2) 예산 표시가 없고 옛 계수가
    모두 0인데, 그 0이 지출 사실이 아니라 `LEGACY_SPEND_UNKNOWN` 실패 기록이 남긴 값이다 —
    저장 원인이 그 코드이거나, 기록을 게이트가 덮었어도 같은 글의 열린 사고가 그 코드다.
    이미 교체한 예산과 버전 예산은 대상이 아니다(한 번만 교체한다).
    """

    raw_budget = attempt.get(GENERATION_BUDGET_KEY)
    if isinstance(raw_budget, dict):
        if _valid_reset_record(raw_budget.get("reset_record")):
            return False
        try:
            _validate_versioned_budget(raw_budget)
        except GenerationBudgetUnknown:
            return True
        return False
    try:
        _legacy_budget(attempt)
    except GenerationBudgetUnknown:
        return True
    if not (attempt.get("reason") == LEGACY_SPEND_UNKNOWN_CODE or legacy_incident_open):
        return False
    counters = [
        attempt[key] for key in ("provider_attempt_count", "attempt_count") if key in attempt
    ]
    return all(_nonnegative_int(value) == 0 for value in counters)


def replace_legacy_budget_and_release(
    summary: Any,
    *,
    actor: str,
    reason: str,
    idempotency_key: str,
    legacy_incident_open: bool = False,
    replaced_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """레거시 예산을 한 번 교체하고 같은 원인의 억제를 푼 `essence_check_summary`를 돌려준다.

    교체 예산은 `legacy_state=REPLACED`와 감사 기록(`reset_record`: actor·사유·멱등 키·시각·
    교체 전 시도 기록 전체)을 갖는다. 억제(reason·retry_class·next_retry_at)는
    `released_generation_attempt`로 걷어 다음 스윕·운영자 재생성이 이 슬롯을 다시 쓴다.
    반환값은 (새 summary, reset_record)이며 호출부가 행 잠금 안에서 저장하고 감사 로그
    `LEGACY_BUDGET_REPLACED`를 같은 트랜잭션에 남긴다.
    """

    updated = dict(summary) if isinstance(summary, dict) else {}
    raw_attempt = updated.get(GENERATION_ATTEMPT_KEY)
    attempt = dict(raw_attempt) if isinstance(raw_attempt, dict) else {}
    if not legacy_budget_is_replaceable(attempt, legacy_incident_open=legacy_incident_open):
        raw_budget = attempt.get(GENERATION_BUDGET_KEY)
        if isinstance(raw_budget, dict) and _valid_reset_record(raw_budget.get("reset_record")):
            raise GenerationBudgetExceeded("LEGACY_BUDGET_ALREADY_REPLACED")
        raise GenerationBudgetExceeded("LEGACY_BUDGET_IS_KNOWN")
    budget = _zero_budget(None)
    budget["legacy_state"] = "REPLACED"
    budget["reset_record"] = {
        "event": "LEGACY_BUDGET_REPLACED",
        "actor": actor,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "replaced_at": (replaced_at or datetime.now(UTC)).isoformat(),
        "previous_snapshot": copy.deepcopy(attempt),
    }
    replaced = released_generation_attempt(
        {**updated, GENERATION_ATTEMPT_KEY: {**attempt, GENERATION_BUDGET_KEY: budget}}
    )
    return replaced, copy.deepcopy(budget["reset_record"])


def acquisition_payload(
    attempt: dict[str, Any], acquisition_id: str
) -> dict[str, Any] | None:
    """Return a durable semantic result so redelivery does not repurchase it."""

    budget = _budget_dict(attempt)
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return None
    entry = next(
        (
            value
            for value in entries
            if isinstance(value, dict)
            and value.get("id") == acquisition_id
            and value.get("status") == "RESULT"
        ),
        None,
    )
    payload = entry.get("payload") if isinstance(entry, dict) else None
    return dict(payload) if isinstance(payload, dict) else None


def _acquisition_status(attempt: dict[str, Any], acquisition_id: str) -> str | None:
    budget = _budget_dict(attempt)
    entries = budget.get("acquisitions")
    if not isinstance(entries, list):
        return None
    entry = next(
        (
            value
            for value in entries
            if isinstance(value, dict) and value.get("id") == acquisition_id
        ),
        None,
    )
    return str(entry.get("status")) if isinstance(entry, dict) else None


class GenerationBudgetLedger:
    """Durably reserve and checkpoint one slot's provider acquisitions.

    The synchronous worker session owns the content row lease. Each method commits
    before returning so a worker crash or redelivery sees the reservation/result.
    """

    def __init__(self, db: Any, item: Any) -> None:
        self._db = db
        self._item = item

    def _attempt(self) -> dict[str, Any]:
        item_id = getattr(self._item, "id", None)
        if item_id is not None and isinstance(self._db, Session):
            current = self._db.execute(
                select(ContentItem)
                .where(ContentItem.id == item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if current is not None:
                self._item = current
        return budget_generation_attempt(self._item)

    def _write(self, attempt: dict[str, Any]) -> None:
        summary = getattr(self._item, "essence_check_summary", None)
        updated = dict(summary) if isinstance(summary, dict) else {}
        updated[GENERATION_ATTEMPT_KEY] = attempt
        self._item.essence_check_summary = updated
        self._db.commit()

    def reserve(
        self, *, acquisition_id: str, kind: AcquisitionKind
    ) -> AcquisitionReservation:
        attempt = self._attempt()
        cached = acquisition_payload(attempt, acquisition_id)
        if cached is not None:
            return AcquisitionReservation(acquisition_id, kind, cached)
        status = _acquisition_status(attempt, acquisition_id)
        if status in {"RESULT", "ERROR", "UNKNOWN"}:
            raise GenerationBudgetExceeded("DURABLE_ACQUISITION_ALREADY_COMPLETED")
        reserved = reserve_acquisition(attempt, acquisition_id=acquisition_id, kind=kind)
        self._write(reserved)
        return AcquisitionReservation(acquisition_id, kind, None)

    def complete(
        self,
        reservation: AcquisitionReservation,
        *,
        transport_attempts: int,
        outcome: AcquisitionOutcome,
        payload: dict[str, Any] | None = None,
    ) -> None:
        completed = acquisition_completed(
            self._attempt(),
            acquisition_id=reservation.acquisition_id,
            transport_attempts=transport_attempts,
            outcome=outcome,
            payload=payload,
        )
        self._write(completed)

    def transport_started(self, reservation: AcquisitionReservation) -> None:
        started = transport_attempt_started(
            self._attempt(), acquisition_id=reservation.acquisition_id
        )
        self._write(started)

    def snapshot(self) -> GenerationBudgetSnapshot:
        return read_generation_budget(self._attempt())

    def next_sequence(self, kind: AcquisitionKind) -> int:
        budget = _budget_dict(self._attempt())
        entries = budget.get("acquisitions")
        matching = [
            entry
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and entry.get("kind") == kind
        ]
        if matching and matching[-1].get("status") == "RESERVED":
            return len(matching)
        return len(matching) + 1

    def latest_payload(self, acquisition_prefix: str) -> dict[str, Any] | None:
        budget = _budget_dict(self._attempt())
        entries = budget.get("acquisitions")
        if not isinstance(entries, list):
            return None
        for entry in reversed(entries):
            if (
                isinstance(entry, dict)
                and str(entry.get("id") or "").startswith(acquisition_prefix)
                and entry.get("status") == "RESULT"
                and isinstance(entry.get("payload"), dict)
            ):
                return dict(entry["payload"])
        return None

    def acquisition_status(self, acquisition_id: str) -> str | None:
        return _acquisition_status(self._attempt(), acquisition_id)

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

# 억제를 만드는 것은 `reason`·`retry_class`·`next_retry_at`이다. 예산 사다리는 그 셋이
# 아니라 아래 계수들이 가진다. 둘을 갈라 두면 "다시 한 번 시도할 자격"만 돌려주면서
# 하루 예산·소진 일수·최초 관측 시각은 그대로 이어갈 수 있다.
GENERATION_LADDER_KEYS = (
    GENERATION_BUDGET_KEY,
    "context",
    "attempt_period",
    "exhausted_days",
    "attempt_count",
    "provider_attempt_count",
    "guard_deferral_count",
    "first_observed_at",
    "approved_facts",
)


def released_generation_attempt(summary: Any) -> dict:
    """억제만 푼 `essence_check_summary`. 예산 계수는 그대로 남긴다.

    기록을 통째로 지우면 하루 예산과 소진 일수가 0에서 다시 시작해 3일 소진도,
    그 소진이 여는 주제 교체도 영영 오지 않는다. 반대로 그대로 두면 저장된 사유가
    이미 사라진 본문을 계속 설명하며 다음 시도를 막는다.
    """

    updated = dict(summary) if isinstance(summary, dict) else {}
    previous = updated.get(GENERATION_ATTEMPT_KEY)
    if not isinstance(previous, dict):
        return updated
    carried = {key: previous[key] for key in GENERATION_LADDER_KEYS if key in previous}
    if carried:
        updated[GENERATION_ATTEMPT_KEY] = carried
    else:
        updated.pop(GENERATION_ATTEMPT_KEY, None)
    return updated


# 생성이 한 번이라도 지나간 슬롯에 남는 흔적. 하나라도 있으면 지출 기록 없이 0으로 읽지 않는다.
_GENERATION_TRACE_FIELDS = (
    "title",
    "body",
    "faq_question",
    "image_url",
    "image_prompt",
    "generated_at",
    "generation_philosophy_id",
    "essence_status",
    "body_updated_at",
    "published_at",
    "first_published_at",
    "human_edited_at",
    "active_revision_id",
    "topic_swap_history",
)


def never_attempted_slot(item: Any) -> bool:
    """시도 기록이 아예 없고 생성 흔적도 없는 슬롯인가 — 생성 지출이 0임이 확실하다.

    예산 표시(0)를 슬롯 생성 때만 붙이던 시기(10/9 이전)에 만든 슬롯은, 한 번도 생성 창에
    들지 않았으면 시도 기록이 없다. 그 슬롯을 `LEGACY_SPEND_UNKNOWN`으로 읽으면 첫 생성이
    곧바로 사람의 일로 굳는다(2026-10-14 10/16 슬롯 6건). 기록이 있는데 예산이 없는 진짜
    레거시 기록, 그리고 본문·제목·생성 시각·공개 이력·주제 교체 같은 흔적이 남은 슬롯은
    종전대로 지출 불명이다.
    """

    summary = getattr(item, "essence_check_summary", None)
    if isinstance(summary, dict) and summary.get(GENERATION_ATTEMPT_KEY):
        return False
    return all(not getattr(item, field, None) for field in _GENERATION_TRACE_FIELDS)


def budget_generation_attempt(item: Any) -> dict:
    """예산을 읽을 때 쓰는 시도 조각. 한 번도 시도하지 않은 슬롯은 지출 0의 새 예산이다."""

    attempt = read_generation_attempt(item)
    if not attempt and never_attempted_slot(item):
        topic = getattr(item, "query_target_id", None)
        return fresh_generation_attempt(topic_id=str(topic) if topic else None)
    return attempt


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
