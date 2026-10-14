"""본문 표본 실패의 마지막 폴백 계단 — 슬롯의 주제를 바꿔 다시 쓴다(기본 한 번).

이미지에는 "인증 이미지 빌려 쓰기" 폴백이 있는데 본문에는 없었다. 그래서 같은 주제로
3일 예산을 다 쓴 슬롯은 `OPERATOR_REQUIRED`에서 멈췄다. LLM 출력은 확률적이지만 어떤
주제는 그 병원의 승인 자료로 끝내 쓸 수 없다 — 그럴 때 사람을 부르기 전에 **같은 슬롯·
같은 예정일로 다른 측정 질문**을 한 번 답하게 한다.

세 가지 계약을 지킨다.

1. **공개 이력·사람 편집이 있는 글은 손대지 않는다.** `first_published_at IS NULL`과
   `human_edited_at IS NULL`이 그 경계다.
2. **교체 횟수에 상한이 있다.** `topic_swap_history` 길이가 `CONTENT_AUTO_TOPIC_SWAP_MAX`
   (기본 1) 미만인 행만 후보이므로, 새 주제가 같은 코드로 다시 소진되면 그때는 사람의 일이다.
   지적 문장 최소 교정을 상한까지 거친 사실·안전 차단도 같은 계단을 탄다
   (`AUTO_CORRECTION_EXHAUSTED_KEY`).
3. **인시던트 epoch가 바뀐다.** 옛 주제의 인시던트는 `RECOVERED`로 닫고, 새 주제의
   실패는 새 key로 열린다(`generation_incident_control.content_generation_object_id`).

로더보다 **앞선 별도 pass**다. 로더는 고른 행에 claim을 찍고 커밋한 채 돌려주므로 그
뒤에서는 언제나 활성 claim이 있다. 이 pass는 자기 트랜잭션에서 claim이 없거나 만료된
행만 다루고, 쓰기는 판·상태·claim을 가드하는 조건부 UPDATE 한 번으로 한다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, cast, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.content import ContentItem
from app.models.operations import Incident, IncidentState
from app.models.sov import ExposureAction
from app.services.content_brief import PLANNING_REASON_KEY
from app.services.content_similarity import DUPLICATE_TITLE_THRESHOLD, topic_similarity
from app.services.content_target_planner import _choose_target
from app.services.incidents import mark_recovered, mark_retrying
from app.services.reference_requirement import (
    references_required_for,
    topic_without_authoritative_source,
)
from app.services.reference_verification import curated_sources_for_topic
from app.services.specialty_compatibility import target_conflicts_with_hospital
from app.services.sync_async_bridge import SyncAsyncBridge
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    budget_generation_attempt,
    current_topic_writers_exhausted,
    read_generation_attempt,
    read_generation_budget,
    record_topic_swap,
)
from app.workers.generation_incident_control import (
    _body_repair_budget_remains,
    generation_incident_dedupe_key,
)
from app.workers.generation_retry_policy import (
    AUTO_CORRECTION_EXHAUSTED_KEY,
    BODY_REPAIR_STATE_KEY,
    GENERATION_BUDGET_EXHAUSTED_CODE,
    OPERATOR_DECIDES_KEY,
    SAMPLE_BODY_CODES,
    SAMPLE_EXHAUSTED_DAY_LIMIT,
    GenerationRetryClass,
    environment_attempt_period,
    has_model_declared_hard_finding,
    next_recovery_deadline,
    sample_daily_budget,
    slot_is_urgent,
    stored_attempt_period,
)
from app.workers.nightly_generation_batch import (
    GENERATION_WRITE_BACK_STATUSES,
    NIGHTLY_GENERATION_CLAIM_TTL_HOURS,
    _nightly_generation_claim_filter,
)

logger = logging.getLogger(__name__)

TOPIC_SWAP_ACTOR = "system:topic-swap"
TOPIC_SWAP_REASON = "topic swapped"
# 교체 직후 슬롯에 남기는 시도 기록의 원인. 실패가 아니라 "새 주제로 다시 쓴다"는 자동 복구
# 상태다 — 오늘 하루 예산이 남았거나 임박 슬롯이면 같은 스윕이, 아니면 내일 첫 적격 스윕이
# 쓴다(`generation_incident_control`이 운영자 문구를 가진다).
TOPIC_SWAPPED_REASON = "TOPIC_SWAPPED"
# 한 페이지가 읽는 행 상한과 그 페이지 수. 교체는 드문 종착 사건이지만, SQL이 거르지
# 못하는 비후보가 한 페이지를 통째로 채울 수 있으므로 정렬 키로 그 뒤를 이어 읽는다.
TOPIC_SWAP_SELECT_LIMIT = 50
TOPIC_SWAP_MAX_PAGES = 5
# 저장 본문 수리 세션 예산(3일)을 다 쓴 뒤 주제 교체 계단을 타는 코드. 같은 주제로 작가가 매일
# 다시 써도 풀리지 않은 참고자료·FAQ 필드·운영 기준 불일치다. 금지 표현(FORBIDDEN_EXPRESSION)과
# 검수 지문 불일치(CONTENT_AI_REVIEW_STALE)는 입력 변경 종착(INPUT_CHANGE_REQUIRED)이라 뺀다.
BODY_REPAIR_SWAP_CODES = frozenset({"MISSING_REFERENCES", "FAQ_FIELDS_MISSING", "ESSENCE_NOT_ALIGNED"})
REFERENCE_SWAP_REASON = "MISSING_REFERENCES"
# 한 슬롯을 교체할 때 살펴보는 측정 질문 후보 수. 고른 질문이 진료과 불일치·수기 목록 문서 없음·
# 기존 주제와 유사로 맞지 않으면 그 질문을 빼고 다음 후보를 고른다(`_choose_target`은 결정적이라
# 빼지 않으면 같은 질문을 다시 고른다).
TOPIC_SWAP_CANDIDATE_LIMIT = 5
# 인시던트 종결 수렴은 창 전체가 아니라 미완료 이력만 읽으므로 더 넉넉히 둔다. 남은 행은
# 다음 pass가 이어서 닫는다(멱등).
TOPIC_SWAP_RECONCILE_LIMIT = 200
# `@>` 담음 조건. 이 모양을 담은 행에만 아직 닫지 못한 옛 인시던트가 있다.
UNRECONCILED_HISTORY = [{"incident_recovered": False}]
# 생성 예산 소진의 시도 기록 코드(`generation_run_control.classify_generation_failure`).
# 현재 주제의 작가 예산이 다 찬 소진은 소진 일수를 기다리지 않고 교체 후보다.
BUDGET_EXHAUSTED_REASON = GENERATION_BUDGET_EXHAUSTED_CODE


@dataclass(frozen=True, slots=True)
class SwapReport:
    """한 pass가 한 일. 상태를 바꾸지 않는 읽기 값이다."""

    considered: int = 0
    swapped: int = 0
    no_candidate_target: int = 0
    # 방어용 라벨이다. `_choose_target`이 이미 진료과 불일치를 거르므로 실제로는 거의 오지 않는다.
    incompatible_specialty: int = 0
    similar_topic: int = 0
    write_conflicts: int = 0
    incidents_recovered: int = 0


def _history_length(column):
    """JSONB `null`은 스칼라라 jsonb_array_length가 예외를 낸다. 배열일 때만 센다."""

    return case(
        (func.jsonb_typeof(column) == "array", func.jsonb_array_length(column)),
        else_=0,
    )


def _claim_expiry(now: datetime) -> datetime:
    return now - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)


def _inactive_claim_filter(expiry: datetime):
    # 로더와 같은 술어다 — 토큰만 남고 claim 시각이 빈 행(운영센터의 claim 해제)도 비활성이다.
    return _nightly_generation_claim_filter(expiry)


def _order_columns():
    return (ContentItem.scheduled_date, ContentItem.sequence_no, ContentItem.id)


def _order_key(item: ContentItem) -> tuple:
    return (item.scheduled_date, item.sequence_no, item.id)


def _stored_attempt_json():
    return ContentItem.essence_check_summary[GENERATION_ATTEMPT_KEY]


def _exhausted_body_sample_filter():
    """SQL로 표현할 수 있는 후보 조건. 판정의 정본은 파이썬 술어다.

    상한을 비후보가 먼저 채우면 그 뒤의 적격 슬롯이 굶는다. 시도 기록의 종착 여부와
    원인 코드는 JSONB에서 바로 읽을 수 있으므로 여기서 거른다. 기록이 없거나 키가
    없으면 `->>`가 NULL이라 비교가 NULL이 되어 자연히 제외된다(원하는 결과다).

    최소 교정 상한을 다 쓴 사실·안전 차단(`AUTO_CORRECTION_EXHAUSTED_KEY`)도 후보다 — 그
    기록은 교체를 기다리는 동안 RETRYING(`SAMPLE_RECOVERABLE`)으로 남는다. 생성 예산
    소진도 RETRYING인 채로 올린다 — 현재 주제의 작가 예산이 찼는지는 파이썬 술어가 본다.
    """

    attempt = _stored_attempt_json()
    repair_state = ContentItem.essence_check_summary[BODY_REPAIR_STATE_KEY]
    clauses = [
        and_(
            attempt["retry_class"].as_string() == GenerationRetryClass.OPERATOR_REQUIRED.value,
            attempt["reason"].as_string().in_(sorted(SAMPLE_BODY_CODES)),
        ),
        and_(
            attempt["reason"].as_string() == "CONTENT_AI_HARD_FINDING",
            attempt[AUTO_CORRECTION_EXHAUSTED_KEY].as_boolean().is_(True),
        ),
        and_(
            attempt["reason"].as_string() == BUDGET_EXHAUSTED_REASON,
            attempt["retry_class"].as_string()
            == GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        ),
        # 저장 본문 수리 예산을 3일 다 쓴 참고자료·FAQ·운영 기준 차단(사람이 정하는 보류 제외).
        and_(
            attempt["reason"].as_string().in_(sorted(BODY_REPAIR_SWAP_CODES)),
            attempt["retry_class"].as_string()
            != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
            attempt[OPERATOR_DECIDES_KEY].as_boolean().is_not(True),
            repair_state["exhausted_days"].as_integer() >= SAMPLE_EXHAUSTED_DAY_LIMIT,
        ),
    ]
    if settings.CONTENT_TOPIC_SWAP_OPERATOR_DECIDES:
        clauses.append(
            and_(
                attempt["reason"].as_string() == REFERENCE_SWAP_REASON,
                attempt[OPERATOR_DECIDES_KEY].as_boolean().is_(True),
            )
        )
    return or_(*clauses)


def topic_swap_limit() -> int:
    """같은 슬롯의 자동 주제 교체 상한(`CONTENT_AUTO_TOPIC_SWAP_MAX`, 기본 1)."""

    return max(0, int(settings.CONTENT_AUTO_TOPIC_SWAP_MAX))


def topic_swap_budget_left(item: ContentItem) -> bool:
    """이 슬롯이 자동 주제 교체를 한 번 더 쓸 수 있는가 — 교체 상한의 단일 판정."""

    history = getattr(item, "topic_swap_history", None)
    if (len(history) if isinstance(history, list) else 0) >= topic_swap_limit():
        return False
    try:
        budget = read_generation_budget(budget_generation_attempt(item))
    except GenerationBudgetUnknown:
        return False
    return budget.topic_swaps < 1 and len(budget.topics) < 2


def _candidate_stmt(
    window_start: date, window_end: date, expiry: datetime, *, after: tuple | None = None
):
    order_columns = _order_columns()
    predicates = [
        ContentItem.scheduled_date >= window_start,
        ContentItem.scheduled_date <= window_end,
        ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
        ContentItem.first_published_at.is_(None),
        ContentItem.human_edited_at.is_(None),
        _history_length(ContentItem.topic_swap_history) < topic_swap_limit(),
        _inactive_claim_filter(expiry),
        _exhausted_body_sample_filter(),
    ]
    if after is not None:
        # 값의 타입을 정렬 컬럼에서 가져온다 — 타입 없는 바인드는 row 비교에서 매개변수
        # 타입을 정하지 못한다(야간 로더의 keyset 페이징과 같은 이유).
        predicates.append(
            tuple_(*order_columns)
            > tuple_(
                *(
                    literal(value, column.type)
                    for column, value in zip(order_columns, after, strict=True)
                )
            )
        )
    return (
        select(ContentItem)
        .where(*predicates)
        .order_by(*order_columns)
        .with_for_update(skip_locked=True, of=ContentItem)
        .limit(TOPIC_SWAP_SELECT_LIMIT)
        # 워커 세션은 `expire_on_commit=False`다. 이 pass의 판단은 전부 행의 현재 값에
        # 달려 있으므로 identity map에 남은 옛 값을 그대로 쓰지 않게 한다.
        .execution_options(populate_existing=True)
    )


def _unreconciled_stmt():
    """인시던트 종결이 끝나지 않은 교체 이력. **스윕 창과 무관하게** 수렴시킨다.

    창으로 좁히면 예정일이 창 밖으로 옮겨 간 슬롯(백로그 복구가 날짜를 미룬 경우 등)의
    옛 인시던트가 영원히 열린 채 남는다. Postgres는 JSONB 담음(`@>`)으로 미완료 항목이
    있는 행만 읽고, 그 밖의 방언에서도 `_has_unreconciled_entry`가 파이썬에서 한 번 더
    거른다 — 판정의 정본은 언제나 파이썬 쪽이다.
    """

    return (
        select(ContentItem)
        .where(cast(ContentItem.topic_swap_history, JSONB).contains(UNRECONCILED_HISTORY))
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no, ContentItem.id)
        .limit(TOPIC_SWAP_RECONCILE_LIMIT)
        # 방금 교체한 행은 Core UPDATE로 썼으므로 추적 객체에는 아직 옛 값이 있다.
        .execution_options(populate_existing=True)
    )


def _stored_review(item: ContentItem) -> Any:
    summary = getattr(item, "essence_check_summary", None)
    return summary.get("ai_review") if isinstance(summary, dict) else None


def exhausted_body_sample_reason(item: ContentItem) -> str | None:
    """소진된 본문 표본 실패의 코드. 교체 대상이 아니면 ``None``.

    모델이 HARD로 단정한 사실·안전 지적은 승인 자료 자체가 틀렸다는 뜻이라 주제를
    바꿔도 해결되지 않는다(`INPUT_CHANGE_REQUIRED`). 이미지 코드도 자기 폴백이 있다.
    저장 본문 수리 예산(3일)을 다 쓴 참고자료·FAQ·운영 기준 차단도 후보다. 진료비·병원 선택
    참고자료 보류는 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES`가 켜졌을 때만 후보다.
    """

    if not topic_swap_budget_left(item):
        # 교체 상한(기본 한 번)을 다 썼다. 후보 SQL과 같은 규칙을 파이썬에도 둔 방어선이며,
        # 그 뒤의 소진은 사람의 일이다.
        return None
    attempt = read_generation_attempt(item)
    reason = str(attempt.get("reason") or "")
    if attempt.get(OPERATOR_DECIDES_KEY):
        # 진료비·병원 선택 글의 참고자료 보류는 사람이 정한다. 대표가 켠 경우에만 교체한다.
        if reason == REFERENCE_SWAP_REASON and settings.CONTENT_TOPIC_SWAP_OPERATOR_DECIDES:
            return reason
        return None
    if (
        reason in BODY_REPAIR_SWAP_CODES
        and attempt.get("retry_class") != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    ):
        # 수리 예산이 남았으면(오늘 또는 내일) 아직 작가가 고친다. 3일을 다 쓴 뒤에만 바꾼다.
        # 수리 기록이 없으면 예산이 남은 것이다(인시던트 소유 판정과 같은 술어).
        if not _body_repair_budget_remains(item):
            return reason
        return None
    if reason == "CONTENT_AI_HARD_FINDING" and attempt.get(AUTO_CORRECTION_EXHAUSTED_KEY):
        # 지적 문장 최소 교정을 상한까지 거친 사실·안전 차단이다. 승인 자료 변경만 기다리지
        # 않고 같은 슬롯·예정일로 다른 질문을 답하게 한다(`tasks._escalate_exhausted_correction`).
        return reason
    # 현재 주제의 작가 예산(6회)은 하루 예산으로 첫날에 다 찬다. 남은 소진 일수는 지출 없는
    # 공회전이라 기다리지 않고 곧바로 교체한다. 교체 상한을 다 쓴 뒤의 소진은 위 방어선이
    # 막아 종전 사다리대로 `OPERATOR_REQUIRED`로 간다.
    # 사흘짜리 공급자 장애도 주제당 HTTP 상한(36)을 다 써 생성 예산 소진으로 이 계단에 들어와
    # 한 번뿐인 주제 교체를 쓸 수 있다. HTTP 예산이 남지 않은 주제는 교체로만 이어갈 수 있으므로
    # 의도적으로 받아들인 결정이다.
    writers_spent = reason == BUDGET_EXHAUSTED_REASON and current_topic_writers_exhausted(
        attempt
    )
    if not writers_spent and (
        attempt.get("retry_class") != GenerationRetryClass.OPERATOR_REQUIRED.value
    ):
        return None
    if reason not in SAMPLE_BODY_CODES:
        return None
    if has_model_declared_hard_finding(_stored_review(item)):
        return None
    return reason


def _existing_topic_terms(item: ContentItem) -> list[str]:
    brief = item.content_brief if isinstance(item.content_brief, dict) else {}
    return [
        str(value)
        for value in (
            item.title,
            brief.get("target_query"),
            brief.get("target_keyword"),
        )
        if value
    ]


def _topic_is_too_similar(new_topic: str, item: ContentItem) -> bool:
    """새 주제가 기존 제목·brief와 사실상 같으면 교체할 이유가 없다."""

    return any(
        topic_similarity(new_topic, term) >= DUPLICATE_TITLE_THRESHOLD
        for term in _existing_topic_terms(item)
    )


def _carried_brief(item: ContentItem) -> dict[str, Any]:
    """운영자 메모와 슬롯 계획 근거만 새 brief로 이월한다.

    사실 필드는 새 타깃·현재 승인 Essence에서 다시 만들어야 한다 — 철회된 주장이 옛
    승인 brief를 타고 다시 들어오지 못하게 하는 기존 계약과 같다.
    """

    previous = item.content_brief if isinstance(item.content_brief, dict) else {}
    carried: dict[str, Any] = {"operator_notes": list(previous.get("operator_notes") or [])}
    planning_reason = previous.get(PLANNING_REASON_KEY)
    if planning_reason:
        carried[PLANNING_REASON_KEY] = planning_reason
    return carried


def _reset_values(item: ContentItem, target_id: uuid.UUID, history_entry: dict) -> dict:
    """새 주제로 다시 쓰기 위해 비워야 할 것 전부. 한 UPDATE로 나간다.

    `scheduled_date`·`sequence_no`·`content_type`·`schedule_id`·`carried_over_from`은
    그대로 둔다 — 계약 월과 이월 회계는 주제와 무관하다.
    """

    swapped_attempt = record_topic_swap(
        budget_generation_attempt(item),
        from_topic_id=str(item.query_target_id) if item.query_target_id else None,
        to_topic_id=str(target_id),
    )
    return {
        "query_target_id": target_id,
        "exposure_action_id": None,
        "content_brief": _carried_brief(item),
        "brief_status": None,
        "brief_approved_at": None,
        "brief_approved_by": None,
        "title": None,
        "body": None,
        "meta_description": None,
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": None,
        # 참고자료 검증 기록도 옛 주제의 것이다(주제 적합성을 옛 제목으로 판정했다).
        "reference_checks": None,
        # 독립 검수 메타와 저장된 생성 시도 기록이 한 JSON에 있다. 새 주제에는 둘 다
        # 근거가 없으므로 통째로 비운다 — 시도 기록이 지워져야 로더 필터를 통과한다.
        "essence_check_summary": {GENERATION_ATTEMPT_KEY: swapped_attempt},
        "essence_status": None,
        "generated_at": None,
        # 이미지는 옛 주제에 결합된 인증이다. 빌린 이미지의 주제 hash를 새 제목으로
        # 만들지 않고 전부 비워, 새 발행이 엄격한 인증 gate를 그대로 통과하게 한다.
        "image_url": None,
        "image_prompt": None,
        "image_policy_verified_at": None,
        "image_content_hash": None,
        "image_subject_hash": None,
        "image_policy_version": None,
        "image_reused_from_content_id": None,
        "image_fallback_source": None,
        "topic_swap_history": [*list(item.topic_swap_history or []), history_entry],
        "content_revision": ContentItem.content_revision + 1,
        "generation_claimed_at": None,
        "generation_claim_token": None,
    }


def _superseded_incident(db, item: ContentItem, reason: str) -> Incident | None:
    """교체가 대체하는 옛 epoch의 생성 인시던트."""

    swap_count = len(list(getattr(item, "topic_swap_history", None) or []))
    return db.execute(
        select(Incident).where(
            Incident.dedupe_key
            == generation_incident_dedupe_key(item.id, reason, topic_swap_count=swap_count)
        )
    ).scalar_one_or_none()


def _target_rejection(target, item: ContentItem, hospital, reason: str) -> str | None:
    """이 측정 질문으로 바꾸면 안 되는 이유(결과 라벨). 바꿔도 되면 ``None``."""

    # 병원 대표 진료과와 어울리지 않는 질문(내과 병원의 영상의학과 질문 등)은 교체 후보에서
    # 뺀다. 61810ef4 → d7a5603e처럼 같은 계열로 바꿔 봐야 같은 이유로 다시 소진된다.
    if target_conflicts_with_hospital(target, hospital):
        return "incompatible_specialty"
    new_topic = str(target.name or "")
    if reason == REFERENCE_SWAP_REASON and topic_without_authoritative_source(new_topic):
        # 참고자료 때문에 막힌 슬롯을 다시 진료비·병원 선택 질문으로 바꾸면 같은 보류로 돌아온다.
        return "no_candidate_target"
    if references_required_for(item.content_type, query_target_id=target.id) and not (
        curated_sources_for_topic([new_topic])
    ):
        return "no_candidate_target"
    if _topic_is_too_similar(new_topic, item):
        return "similar_topic"
    return None


def _swap_target(db, item: ContentItem, reason: str) -> tuple[Any, str]:
    """교체할 측정 질문과 결과 라벨. 맞는 질문이 없으면 `(None, 마지막 거절 라벨)`."""

    hospital = getattr(item, "hospital", None)
    excluded: set = {item.query_target_id}
    label = "no_candidate_target"
    for _attempt in range(TOPIC_SWAP_CANDIDATE_LIMIT):
        target = _choose_target(
            db,
            item=item,
            hospital_id=item.hospital_id,
            exclude_target_ids=excluded,
            hospital=hospital,
        )
        if target is None:
            return None, label
        rejection = _target_rejection(target, item, hospital, reason)
        if rejection is None:
            return target, "swapped"
        label = rejection
        excluded.add(target.id)
    return None, label


def _swap_one(db, item: ContentItem, reason: str, *, now: datetime) -> tuple[dict | None, str]:
    """한 슬롯의 주제를 바꾼다. `(history 항목 또는 None, 결과 라벨)`."""

    target, label = _swap_target(db, item, reason)
    if target is None:
        return None, label

    superseded = _superseded_incident(db, item, reason)
    revision_before = int(item.content_revision or 1)
    entry = {
        "from_target_id": str(item.query_target_id) if item.query_target_id else None,
        "from_title": item.title,
        "to_target_id": str(target.id),
        "reason_code": reason,
        "swapped_at": now.isoformat(),
        "revision_before": revision_before,
        "revision_after": revision_before + 1,
        "superseded_incident_id": str(superseded.id) if superseded is not None else None,
        "superseded_episode_seq": (
            int(superseded.episode_seq) if superseded is not None else None
        ),
        "incident_recovered": False,
    }
    previous_action_id = item.exposure_action_id
    # 교체 UPDATE가 수리 세션 계수까지 비우므로 오늘 쓴 작가 세션 수는 그 전에 읽는다.
    spent_today = writer_sessions_spent_today(item, now)
    try:
        reset_values = _reset_values(item, target.id, entry)
    except (GenerationBudgetUnknown, GenerationBudgetExceeded):
        return None, "no_candidate_target"
    updated = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item.id,
            ContentItem.content_revision == revision_before,
            ContentItem.status == item.status,
            ContentItem.first_published_at.is_(None),
            ContentItem.human_edited_at.is_(None),
            _inactive_claim_filter(_claim_expiry(now)),
        )
        .values(**reset_values)
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        # 그 사이 생성·편집·취소가 끼어들었다. 다음 스윕이 다시 판단한다.
        return None, "write_conflict"
    if previous_action_id is not None:
        db.execute(
            update(ExposureAction)
            .where(
                ExposureAction.id == previous_action_id,
                ExposureAction.linked_content_id == item.id,
            )
            .values(linked_content_id=None)
            .execution_options(synchronize_session=False)
        )
    # 교체한 행의 현재 값으로 시도 기록을 남겨야 한다 — 시도 지문에는 새 타깃이 들어간다.
    db.refresh(item)
    _record_topic_swapped_attempt(db, item, now=now, spent_today=spent_today)
    return entry, "swapped"


def _tasks():
    """스윕 진입점(`workers.tasks`)이 이 모듈을 import하므로 호출 시점에 늦게 가져온다."""

    from app.workers import tasks

    return tasks


def writer_sessions_spent_today(item: ContentItem, now: datetime) -> int:
    """이 슬롯이 오늘(KST) 이미 산 작가 세션 수 — 표본 예산 계수와 본문 수리 세션 계수 중 큰 값.

    날이 바뀐 기록은 0이다(하루 예산은 KST 날마다 다시 열린다). 계수나 기록의 날짜를 읽지
    못하면 오늘 예산을 다 쓴 것으로 본다 — 모르는 지출을 0으로 바꾸지 않는다.
    """

    today = environment_attempt_period(now)
    attempt = read_generation_attempt(item)
    period = stored_attempt_period(attempt)
    if attempt.get("reason") and period is None:
        return sample_daily_budget(TOPIC_SWAPPED_REASON, scheduled_date=None)
    spent = 0
    if period == today:
        try:
            spent = int(attempt.get("provider_attempt_count", attempt.get("attempt_count")) or 0)
        except (TypeError, ValueError):
            return sample_daily_budget(TOPIC_SWAPPED_REASON, scheduled_date=None)
    summary = getattr(item, "essence_check_summary", None)
    repair = summary.get(BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
    if isinstance(repair, dict) and repair.get("period") == today:
        try:
            spent = max(spent, int(repair.get("count") or 0))
        except (TypeError, ValueError):
            return sample_daily_budget(TOPIC_SWAPPED_REASON, scheduled_date=None)
    return max(spent, 0)


def _record_topic_swapped_attempt(
    db, item: ContentItem, *, now: datetime, spent_today: int | None = None
) -> None:
    """교체한 슬롯의 다음 시도 시각을 **오늘 남은 예산**으로 정한다.

    시도 기록을 그냥 지우면 로더의 unchanged/미도래 필터를 그대로 통과해 그날 남은 스윕이
    새 주제로 하루 예산을 처음부터 다시 산다 — 소진된 하루 예산이 교체 한 번으로 되살아난다.
    그래서 옛 주제가 오늘 이미 산 작가 세션 수(`spent_today`, 교체 전에 읽는다)를 오늘 계수로
    이어받는 결정 1건을 정본 경로로 남긴다(예산은 쓰지 않으므로 `count_attempt=False`).

    - 오늘 예산이 남았으면(교체가 자정 뒤 01시 스윕에서 일어난 경우 등) 기한은 **지금**이다 —
      교체 pass 바로 뒤의 같은 스윕 로더가 새 주제를 쓴다. 내일로 미루면 새 주제의 예산이
      남아 있는데도 하루를 통째로 버린다(2026-10-14 abe0b28e: 01:00 교체 → 10/15 01:00).
    - 임박 슬롯(예정일이 오늘·내일이거나 지난 슬롯, `slot_is_urgent`)은 옛 주제가 오늘 예산을
      다 썼어도 오늘 계수를 하루 예산 − 1로 묶어 같은 스윕에서 한 번은 쓴다. 그 1회가 실패하면
      정상 예산 규칙이 오늘 예산을 소진으로 읽고(다음은 내일), 교체가 슬롯 평생 한 번이라
      이 1회도 다시 생기지 않는다.
    - 그 밖(사흘 뒤 슬롯이 오늘 예산을 다 쓴 경우)은 내일의 첫 적격 스윕이다.

    새 주제는 자기 작가·검수·전송 예산을 새로 받으므로(`prior_topic_spend`) 같은 날 다시 써도
    주제당·평생 상한은 그대로다. 날이 바뀌면 하루 예산이 초기화되므로 새 주제의 첫 실패는
    1회차부터 다시 센다. 예정일을 읽지 못한 행은 종전처럼 오늘 예산 소진·기한 없음이다.
    """

    tasks = _tasks()
    philosophy = tasks._generation_philosophy_sync(db, item.hospital_id)
    scheduled_date = getattr(item, "scheduled_date", None)
    budget = sample_daily_budget(TOPIC_SWAPPED_REASON, scheduled_date=scheduled_date, observed=now)
    if scheduled_date is None:
        spent_count = budget
    else:
        sessions = writer_sessions_spent_today(item, now) if spent_today is None else spent_today
        spent_count = min(max(int(sessions), 0), budget)
        if slot_is_urgent(scheduled_date, now):
            spent_count = min(spent_count, budget - 1)
    spent = {
        "reason": TOPIC_SWAPPED_REASON,
        "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        "attempt_period": environment_attempt_period(now),
        "provider_attempt_count": spent_count,
        # 구형 판독기가 읽는 이름도 같은 값으로 맞춘다.
        "attempt_count": spent_count,
        "exhausted_days": 0,
    }
    deadline = (
        now
        if scheduled_date is not None and spent_count < budget
        else next_recovery_deadline(spent, scheduled_date=scheduled_date, now=now)
    )
    tasks._remember_generation_attempt(
        db,
        item,
        philosophy,
        TOPIC_SWAPPED_REASON,
        count_attempt=False,
        extra={
            **{key: value for key, value in spent.items() if key != "reason"},
            "next_retry_at": deadline.isoformat() if deadline else None,
        },
    )


async def _recover_incident_async(incident_id: uuid.UUID) -> bool:
    """옛 epoch의 인시던트를 닫는다. 이미 닫혔거나 사라졌으면 수렴으로 본다."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.get(Incident, incident_id)
        if incident is None:
            return True
        open_states = (IncidentState.OPEN.value, IncidentState.RETRYING.value)
        if incident.state not in open_states:
            return True
        current = incident
        if current.state == IncidentState.OPEN.value:
            retrying = await mark_retrying(
                db,
                current.id,
                expected_version=current.version,
                actor=TOPIC_SWAP_ACTOR,
                reason=TOPIC_SWAP_REASON,
            )
            if not isinstance(retrying, Incident):
                return False
            current = retrying
        recovered = await mark_recovered(
            db,
            current.id,
            expected_version=current.version,
            observed_success=True,
            actor=TOPIC_SWAP_ACTOR,
            reason=TOPIC_SWAP_REASON,
        )
        await db.commit()
        return isinstance(recovered, Incident)


def _mark_history_recovered(db, item_id: uuid.UUID, history: list) -> None:
    db.execute(
        update(ContentItem)
        .where(ContentItem.id == item_id)
        .values(topic_swap_history=history)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _has_unreconciled_entry(item: ContentItem) -> bool:
    return any(
        isinstance(entry, dict) and not entry.get("incident_recovered")
        for entry in (item.topic_swap_history or [])
    )


def _reconcile_history(db, items) -> int:
    """커밋 뒤 크래시로 남은 미완료 history를 수렴시킨다.

    인시던트 종결은 콘텐츠 트랜잭션 밖의 별도 상태다. 그래서 history에 의도를 먼저
    남기고, 종결이 끝나야 `incident_recovered`를 올린다 — 중간에 죽어도 다음 pass가
    같은 항목을 보고 이어서 닫는다(멱등).
    """

    pending = [item for item in items if _has_unreconciled_entry(item)]
    if not pending:
        return 0
    recovered = 0
    with SyncAsyncBridge() as bridge:
        for item in pending:
            history = list(item.topic_swap_history or [])
            changed = False
            for index, entry in enumerate(history):
                if not isinstance(entry, dict) or entry.get("incident_recovered"):
                    continue
                incident_id = entry.get("superseded_incident_id")
                closed = True
                if incident_id:
                    closed = bridge.run(
                        _recover_incident_async(uuid.UUID(str(incident_id)))
                    )
                    if closed:
                        recovered += 1
                if closed:
                    history[index] = {**entry, "incident_recovered": True}
                    changed = True
            if changed:
                _mark_history_recovered(db, item.id, history)
    return recovered


def swap_exhausted_topics(
    db,
    *,
    window_start: date,
    window_end: date,
    now: datetime | None = None,
) -> SwapReport:
    """소진된 본문 슬롯의 주제를 한 번씩 바꾼다. 로더보다 **앞에서** 돈다."""

    observed_at = now or datetime.now(timezone.utc)
    expiry = _claim_expiry(observed_at)
    outcomes: dict[str, int] = {}
    after: tuple | None = None
    # SQL이 거르지 못하는 판정(모델 HARD 단정)이 남아 있으므로, 한 페이지가 통째로
    # 비후보일 수 있다. 정렬 키로 그 뒤를 이어 읽어 적격 슬롯이 굶지 않게 한다.
    for _page in range(TOPIC_SWAP_MAX_PAGES):
        rows = list(
            db.execute(
                _candidate_stmt(window_start, window_end, expiry, after=after)
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for item in rows:
            reason = exhausted_body_sample_reason(item)
            if reason is None:
                continue
            outcomes["considered"] = outcomes.get("considered", 0) + 1
            entry, label = _swap_one(db, item, reason, now=observed_at)
            outcomes[label] = outcomes.get(label, 0) + 1
            if entry is None:
                continue
            logger.info(
                "topic swap fallback replaced slot %s (target %s → %s, reason %s)",
                item.id,
                entry["from_target_id"],
                entry["to_target_id"],
                reason,
            )
        after = _order_key(rows[-1])
        # `FOR UPDATE SKIP LOCKED`가 잡은 잠금은 커밋까지 풀리지 않는다. 페이지마다
        # 커밋해 비후보 행이 pass 내내 잠기지 않게 한다(야간 로더와 같은 규칙).
        db.commit()
        if len(rows) < TOPIC_SWAP_SELECT_LIMIT:
            break
    db.commit()

    # 크래시로 남은 미완료 항목도 수렴시켜야 하므로 이번 pass가 아무것도 바꾸지 않아도 돈다.
    swapped_rows = list(db.execute(_unreconciled_stmt()).scalars().all())
    return SwapReport(
        considered=outcomes.get("considered", 0),
        swapped=outcomes.get("swapped", 0),
        no_candidate_target=outcomes.get("no_candidate_target", 0),
        incompatible_specialty=outcomes.get("incompatible_specialty", 0),
        similar_topic=outcomes.get("similar_topic", 0),
        write_conflicts=outcomes.get("write_conflict", 0),
        incidents_recovered=_reconcile_history(db, swapped_rows),
    )
