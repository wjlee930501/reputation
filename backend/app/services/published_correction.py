"""사후 검수가 FLAGGED한 공개 글의 자동 교정 — 살아 있는 행에 쓰는 규칙(공개 모드).

교정 자체(문장 단위 교정·응급 템플릿·범위 검사·필수 문구 보호)는 `content_minimal_correction`이
메모리에서 한다. 이 모듈은 그 결과를 **공개 중인 행**에 쓸 수 있는 조건과 쓰는 방법만 정한다.

- 쓰기 조건은 교정본 후보 hash에 묶인 독립 재검수 PASS 하나다. PASS가 아니거나 다른 hash의 PASS는
  행을 건드리지 않는다.
- 쓰기는 잠근 행에서 compare-and-swap이다. 읽은 뒤 상태·`content_revision`·본문 hash·FLAGGED 표시가
  하나라도 달라졌으면(사람이 그 사이에 PATCH·확인·비공개 전환) 아무것도 쓰지 않는다.
- 제목·FAQ 질문·참고자료는 바꾸지 않는다. 제목이 바뀌면 대표 이미지의 주제 인증이 풀려 글이 숨는다.
- 쓸 때는 관리자 PATCH가 공개 글의 공개 텍스트를 바꿀 때와 같이 한다: `content_revision` 증가,
  `body_updated_at` 갱신, 최초 공개 사실(`published_at`·`first_published_at`) 보존, 발행 게이트 재평가.
  교정본이 게이트를 통과하지 못하면(그대로 쓰면 공개 글이 숨겨진다) 쓰지 않는다.
- 이 모듈은 커밋·색인·사이트 재검증·인시던트를 하지 않는다. 호출부(스윕)가 같은 트랜잭션에서
  의도(intent)를 더하고 커밋한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.models.content import ContentStatus
from app.services.content_ai_review import ContentAiReviewStatus, candidate_sha256
from app.services.content_engine import FORBIDDEN_CHECK_FIELDS
from app.services.content_minimal_correction import (
    AUTO_CORRECTION_KEY,
    CORRECTABLE_FIELDS,
    UNCHANGED_FIELDS,
    CorrectionOutcome,
)
from app.services.content_publication import (
    apply_publication_assessment,
    assess_content_publication,
)
from app.utils.medical_filter import check_forbidden_content_fields

# 사후 검수 스윕이 남기는 FLAGGED 표시의 열쇠와 자동 교정을 기록하는 행위자.
POST_PUBLISH_FLAG_KEY = "post_publish_ai_review"
POST_PUBLISH_CORRECTION_ACTOR = "system:ai-correction"
POST_PUBLISH_CORRECTION_MODE = "POST_PUBLISH"

_CANDIDATE_FIELDS = (
    "title",
    "body",
    "meta_description",
    "faq_question",
    "faq_answer_summary",
    "references_list",
)
_HISTORY_LIMIT = 3
_MISSING = object()
# 교정본을 쓰다 거절할 때 되돌릴 행의 필드.
_RESTORED_FIELDS = (
    "body",
    "meta_description",
    "faq_answer_summary",
    "content_revision",
    "body_updated_at",
    "essence_check_summary",
    "essence_status",
    "content_philosophy_id",
    "last_reviewed_philosophy_id",
    "post_publish_reviewed_at",
    "post_publish_reviewed_by",
)


def candidate_content(item: Any) -> dict[str, Any]:
    """독립 검수·교정이 보는 공개 텍스트 필드 집합(생성 경로의 저장 본문 재검수와 같다)."""

    return {field: getattr(item, field, None) for field in _CANDIDATE_FIELDS}


# ── FLAGGED 표시(마커) ───────────────────────────────────────────────────────


def structured_review_from_marker(marker: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """표시에 저장된 구조화 지적을 `plan_corrections`가 읽는 모양으로 돌려준다.

    구조화 지적(severity·kind·target·quote·message)이 없는 옛 표시(문구 문자열뿐 — 2026-10-08에
    FLAGGED된 20편)는 ``None``이다. 문구에서 인용을 짐작하지 않는다 — 호출부가 재검수로 구조를 얻는다.
    """

    findings = (marker or {}).get("structured_findings")
    if (
        not isinstance(findings, list)
        or not findings
        or not all(isinstance(finding, Mapping) for finding in findings)
    ):
        return None
    return {"findings": list(findings)}


def correction_state(marker: Mapping[str, Any] | None) -> dict[str, Any]:
    state = (marker or {}).get("correction")
    return dict(state) if isinstance(state, Mapping) else {}


def correction_exhausted(marker: Mapping[str, Any] | None, *, max_passes: int) -> bool:
    """이 글에 더 이상 자동 교정을 시도하지 않는가 — 끝난 표시이거나 패스 상한에 닿았다."""

    if max_passes <= 0:
        return True
    state = correction_state(marker)
    return bool(state.get("finished")) or int(state.get("passes") or 0) >= max_passes


def marker_with_attempt(
    marker: Mapping[str, Any],
    *,
    outcome: CorrectionOutcome,
    finished: bool,
    reason: str | None,
    now: datetime,
) -> dict[str, Any]:
    """시도 기록을 더한 새 표시. 지적·해시는 그대로 두고 `correction`만 갱신한다."""

    previous = correction_state(marker)
    history = list(previous.get("history") or []) + list(outcome.history)
    state: dict[str, Any] = {
        "passes": max(int(previous.get("passes") or 0), outcome.passes),
        "rereviews": max(int(previous.get("rereviews") or 0), outcome.rereviews),
        "finished": finished,
        "last_status": outcome.status,
        "last_attempt_at": now.isoformat(),
        "history": history[-_HISTORY_LIMIT:],
    }
    if reason:
        state["reason"] = reason
    return {**marker, "correction": state}


# ── 살아 있는 행에 쓰기 ──────────────────────────────────────────────────────


def _snapshot(item: Any) -> dict[str, Any]:
    return {name: getattr(item, name, _MISSING) for name in _RESTORED_FIELDS}


def _restore(item: Any, snapshot: Mapping[str, Any]) -> None:
    for name, value in snapshot.items():
        if value is not _MISSING:
            setattr(item, name, value)


def _sentence_records(outcome: CorrectionOutcome) -> tuple[list[dict[str, Any]], list[str]]:
    sentences: list[dict[str, Any]] = []
    templates: list[str] = []
    for entry in outcome.history:
        sentences.extend(entry.get("sentences") or [])
        templates.extend(entry.get("templates") or [])
    return sentences, templates


def apply_published_correction(
    item: Any,
    outcome: CorrectionOutcome,
    *,
    base_sha: str,
    base_revision: int,
    philosophy: Any,
    now: datetime,
) -> tuple[str, str | None]:
    """잠근 공개 행에 PASS 교정본을 쓴다. ``(상태, 사유)`` — 상태는 아래 셋 중 하나다.

    - ``CORRECTED``: 행을 바꿨다. 호출부가 같은 트랜잭션에서 색인·사이트 재검증 intent를 더하고 커밋한다.
    - ``CONFLICT``: 읽은 뒤 행이 바뀌었다(상태·revision·본문·표시). 아무것도 쓰지 않았다.
    - ``REJECTED``: 교정본이 쓸 수 없는 모양이다(PASS 아님·hash 불일치·제목 변경·금지 표현·게이트 미통과).
      아무것도 쓰지 않았다.
    """

    review = outcome.review
    corrected = outcome.content
    if (
        outcome.status != "PASS"
        or review is None
        or corrected is None
        or review.status != ContentAiReviewStatus.PASS
        or review.blocking_findings
    ):
        return "REJECTED", "not_pass"
    if review.candidate_sha256 != candidate_sha256(corrected):
        return "REJECTED", "review_not_bound"

    summary = item.essence_check_summary if isinstance(item.essence_check_summary, dict) else {}
    marker = summary.get(POST_PUBLISH_FLAG_KEY)
    if (
        item.status != ContentStatus.PUBLISHED
        or int(getattr(item, "content_revision", 1) or 1) != int(base_revision)
        or not isinstance(marker, dict)
        or marker.get("status") != "FLAGGED"
        or marker.get("candidate_sha256") != base_sha
        or candidate_sha256(item) != base_sha
        or getattr(item, "post_publish_reviewed_at", None) is not None
    ):
        return "CONFLICT", None

    for name in UNCHANGED_FIELDS:
        if corrected.get(name) != getattr(item, name, None):
            return "REJECTED", "title_changed" if name == "title" else f"{name}_changed"
    effective = {name: corrected.get(name) for name in FORBIDDEN_CHECK_FIELDS}
    # 본문은 렌더 결과 기준, 나머지는 평문 기준 — 관리자 PATCH와 같은 검사다.
    if check_forbidden_content_fields(effective, FORBIDDEN_CHECK_FIELDS):
        return "REJECTED", "forbidden_expression"
    if candidate_sha256({**candidate_content(item), **{n: corrected.get(n) for n in CORRECTABLE_FIELDS}}) != (
        review.candidate_sha256
    ):
        return "REJECTED", "hash_mismatch"

    snapshot = _snapshot(item)
    sentences, templates = _sentence_records(outcome)
    new_revision = int(base_revision) + 1
    for name in CORRECTABLE_FIELDS:
        if corrected.get(name) != getattr(item, name, None):
            setattr(item, name, corrected.get(name))
    after_sha = candidate_sha256(item)
    previous_record = summary.get(AUTO_CORRECTION_KEY)
    record = {
        **(previous_record if isinstance(previous_record, dict) else {}),
        "mode": POST_PUBLISH_CORRECTION_MODE,
        "corrected_at": now.isoformat(),
        "before_sha256": base_sha,
        "after_sha256": after_sha,
        "corrected_sha256": after_sha,
        "human_edited": getattr(item, "human_edited_at", None) is not None,
        "content_revision": new_revision,
        "sentences": sentences,
        "templates": templates,
        "passes": outcome.passes,
        "rereviews": outcome.rereviews,
    }
    new_summary = {k: v for k, v in summary.items() if k != POST_PUBLISH_FLAG_KEY}
    new_summary["ai_review"] = review.payload()
    new_summary[AUTO_CORRECTION_KEY] = record
    item.essence_check_summary = new_summary
    item.content_revision = new_revision
    item.body_updated_at = now
    item.post_publish_reviewed_at = now
    item.post_publish_reviewed_by = POST_PUBLISH_CORRECTION_ACTOR

    assessment = assess_content_publication(item, philosophy)
    if not assessment.publishable:
        # 이 교정본을 쓰면 공개 표면이 글을 숨기거나 게이트가 막는다 — 자동으로 글을 내리지 않는다.
        _restore(item, snapshot)
        return "REJECTED", f"not_publishable:{assessment.code}"
    apply_publication_assessment(item, assessment)
    return "CORRECTED", None
