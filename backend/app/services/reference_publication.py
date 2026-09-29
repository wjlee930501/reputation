"""발행 직전 참고자료 재검증 — 07:45 게이트·08:00 발행기·관리자 수동 발행·restore가 같은 규칙을 쓴다.

발행 게이트의 계약(2026-09-29 참고자료 전수 점검 후속):

- 모든 참고자료에 **같은 URL**(지문)·**같은 글 주제**(지문)의 **신선한**(`REFERENCE_CHECK_MAX_AGE`
  이내, 미래 시각은 `REFERENCE_CHECK_CLOCK_SKEW`까지만) 통과 기록이 있어야 공개한다. 없거나
  오래됐거나 글 주제가 바뀌었으면 워커가 다시 검증한다 — 표시 전용 순수 함수는 GET하지 않는다.
  형식이 깨진 항목(매핑이 아님·빈 주소)도 통과하지 못한다.
- 다시 검증해 떨어진 항목은 빼고, 남은 것이 1개 이상이면 그대로 발행한다.
- 전부 떨어지고(또는 처음부터 비어 있고) 참고자료가 필수인 글이면 이 글의 주제로 채점한 수기
  목록에서 채운다(같은 검증을 거친다). 그래도 없으면 참고자료를 비워 기존 `MISSING_REFERENCES`
  차단(발행 보류 + 인시던트 + 자동 본문 수리 세션)으로 보낸다. 실패한 URL과 사유는
  `reference_checks`에 남는다. "필수"는 `reference_requirement.references_required` 한 규칙이다
  — 의료 안내 유형과 의료 주제(측정 질문)를 실은 NOTICE. 필수인 글이 비어 있으면
  `publication_references_settled`가 False라 발행 경로가 반드시 치유를 먼저 시도한다(2af00d02).
- 진료비·병원 선택 글(`reference_requirement.references_left_to_operator`)은 수기 목록에서 채우지
  않는다 — 그 주제의 공신력 있는 문서가 본질적으로 없어 채우면 가짜 근거가 된다. 전부 빠지면
  `operator_decides`로 비워 `MISSING_REFERENCES` 보류로 보내고, 그 보류는 자동 본문 수리가 아니라
  사람의 결정(`OPERATOR_REQUIRED`)이다. 수기 목록 문서(`is_curated_source_url`)는 작가가 직접
  인용해 통과했어도 발행 전(DRAFT·READY) 글에서 뺀다 — 그 통과는 GET이 아니라 치유와 같은 카탈로그
  키워드 대조라 채우기와 다르지 않다(2026-09-29 실장 결정). 목록 밖 참고자료는 실제 GET으로
  통과했으면 그대로 남는다. 공개된 글의 참고자료는 그대로다(아래 불변).
- 기관 사이트 장애(연결 오류·시간 초과·프로토콜·5xx·408/429)는 검증기 쪽에서 같은 URL·같은
  주제의 직전 통과 판정을 재사용한다. 재사용할 통과가 없는 목록 밖 URL이면 그 글을 **미룬다**
  (`deferred`, `site_unreachable_urls`) — 제거·치유·`MISSING_REFERENCES`·인시던트가 없고 다음
  시간대 발행기(08~23시 매시)가 다시 연다. 미룸이 발행 예정일의 마지막 발행기(23시) 또는 그
  뒤에도 이어지면 08:00 발행 요약에 `REFERENCE_SITE_UNREACHABLE`("기관 사이트에 접속하지 못해")
  한 줄로 한 번 알린다(같은 상태는 요약 dedupe가 다시 보내지 않는다). 발행기 catch-up 기간
  (`AUTO_PUBLISH_CATCHUP_DAYS`)이 재시도의 끝이다.
- 실행당 GET 상한을 넘으면 그 글은 이번 실행에서 미룬다 — 차단·인시던트·알림이 아니다.
- 네트워크 GET은 행 잠금 밖에서 한다. 재검증 결과는 시작 시점의 스냅샷(상태·판·참고자료·주제
  지문)을 들고 다니고, 잠금 뒤 `apply_publication_reference_refresh`가 스냅샷이 그대로일 때만 쓴다
  — 그 사이 편집·재생성·발행이 있었으면 아무것도 덮어쓰지 않고 False를 돌려준다. 발행 전
  (DRAFT·READY)이 아닌 행의 참고자료는 어떤 경우에도 바꾸지 않는다.
- 아직 생성되지 않은 슬롯(제목·본문 없음)은 재검증·치유하지 않는다. 판이 오르면 진행 중인
  생성의 저장이 판 불일치로 버려진다.
- restore(비공개 보존 글 재공개)는 `verify_publication_references`로 **검증만** 한다. 공개됐던
  글의 참고자료는 빼지도, 채우지도, 바꾸지도 않는다 — 통과하지 못하면 restore를 거절한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.post_publish_review_policy import AUTO_PUBLISHABLE_STATUSES
from app.services.reference_requirement import (
    references_left_to_operator,
    references_required,
)
from app.services.reference_verification import (
    REASON_MALFORMED_ENTRY,
    VERDICT_PASS,
    ReferenceVerifier,
    curated_sources_for_topic,
    index_reference_checks,
    item_topic_terms,
    merge_reference_checks,
    reason_label,
    reference_gate_status,
    reference_url_fingerprint,
    split_reference_entries,
    topic_fingerprint,
)
from app.utils.authority_sources import is_curated_source_url

# 08:00 발행 요약에 실리는, 기관 사이트 장애로 발행을 미룬 글의 코드와 원인 문구.
# '참고 자료 확보 실패'가 아니다 — 문서가 없다는 증거가 아니라 사이트가 열리지 않았다.
REFERENCE_SITE_UNREACHABLE_CODE = "REFERENCE_SITE_UNREACHABLE"
REFERENCE_SITE_UNREACHABLE_CAUSE = (
    "참고 자료 기관 사이트에 접속하지 못해 주소를 확인하지 못했습니다 — 발행을 미루고 "
    "다음 발행 시간대에 자동으로 다시 확인합니다."
)
# 발행기는 08~23시 매시 돈다(`celery_app` beat). 이 시각 실행이 예정일의 마지막 기회다.
REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR = 23


# 참고자료를 고칠 수 있는 상태(발행 전). 공개·보존된 글의 참고자료는 자동 경로가 바꾸지 않는다.
_REFERENCE_WRITABLE_STATUSES = frozenset(
    getattr(status, "value", status) for status in AUTO_PUBLISHABLE_STATUSES
)


def _status_value(item: object) -> str | None:
    status = getattr(item, "status", None)
    return None if status is None else str(getattr(status, "value", status))


def _references_key(references: object) -> str:
    try:
        return json.dumps(references, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(references)


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    """재검증을 시작할 때의 행 — 잠금 뒤 이 값이 그대로여야 결과를 쓴다."""

    content_revision: int | None
    references_key: str
    topic_fingerprint: str
    # 발행은 판·참고자료·주제를 바꾸지 않는다 — GET 사이에 공개된 글을 상태로 알아본다.
    status: str | None = None


def reference_snapshot(item: object) -> ReferenceSnapshot:
    revision = getattr(item, "content_revision", None)
    return ReferenceSnapshot(
        content_revision=int(revision) if revision is not None else None,
        status=_status_value(item),
        references_key=_references_key(getattr(item, "references_list", None)),
        topic_fingerprint=topic_fingerprint(item_topic_terms(item)),
    )


def reference_snapshot_matches(item: object, snapshot: ReferenceSnapshot) -> bool:
    return reference_snapshot(item) == snapshot


@dataclass(frozen=True, slots=True)
class PublicationReferenceRefresh:
    """재검증 한 번의 결과. 상태를 바꾸지 않는 값 — 적용은 `apply_publication_reference_refresh`."""

    snapshot: ReferenceSnapshot
    references: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    references_changed: bool = False
    healed: bool = False
    deferred: bool = False
    already_current: bool = False
    # 일시 장애로 미룬 목록 밖 URL(비어 있으면 실행당 GET 한도로 미룬 것).
    site_unreachable_urls: tuple[str, ...] = ()
    # 진료비·병원 선택 글이라 치유하지 않고 비웠다 — 보류는 사람이 정한다.
    operator_decides: bool = False


def publication_references_current(item: object, *, now: datetime | None = None) -> bool:
    """발행 게이트 — 모든 참고자료에 같은 URL·같은 주제의 신선한 통과 기록이 있는가."""

    return reference_gate_status(
        getattr(item, "references_list", None),
        getattr(item, "reference_checks", None),
        topic_terms=item_topic_terms(item),
        now=now,
    ).current


def publication_references_missing(item: object) -> bool:
    """참고자료가 필수인 글에 주소가 있는 항목이 하나도 없는가(치유 또는 MISSING_REFERENCES 대상)."""

    if not references_required(item):
        return False
    entries, _malformed = split_reference_entries(getattr(item, "references_list", None))
    return not entries


def _strips_curated_references(item: object) -> bool:
    """발행 전 진료비·병원 선택 글인가 — 수기 목록 문서를 남기지 않는다.

    공개·보존된 글(DRAFT·READY 밖)은 거짓이다. 그 참고자료는 어떤 자동 경로도 바꾸지 않는다.
    """

    status = _status_value(item)
    if status is not None and status not in _REFERENCE_WRITABLE_STATUSES:
        return False
    return references_left_to_operator(item)


def _is_curated_entry(entry: Mapping[str, Any]) -> bool:
    return is_curated_source_url(entry.get("url"))


def publication_references_settled(item: object, *, now: datetime | None = None) -> bool:
    """다시 검증할 일이 없는가 — 모든 항목이 신선한 통과이고, 필수인 글이 비어 있지 않다.

    발행 경로(07:45·08:00·catch-up·수동 발행)는 이 값이 False일 때 재검증·치유를 돈다.
    비어 있는 필수 글을 "통과"로 두면 치유를 건너뛰고 곧장 보류된다 — 또는 게이트가 그 글을
    필수로 보지 않으면 0개로 공개된다(2af00d02).
    """

    if not publication_references_current(item, now=now) or publication_references_missing(item):
        return False
    if _strips_curated_references(item):
        entries, _malformed = split_reference_entries(getattr(item, "references_list", None))
        # 발행 전 진료비·병원 선택 글의 수기 목록 문서는 통과 기록이 있어도 빼야 한다.
        return not any(_is_curated_entry(entry) for entry in entries)
    return True


def _has_generated_text(item: object) -> bool:
    return bool(str(getattr(item, "title", None) or "").strip()) and bool(
        str(getattr(item, "body", None) or "").strip()
    )


def _deferred_urls(checks: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(str(check.get("url") or "") for check in checks)


async def refresh_publication_references(
    item: object,
    verifier: ReferenceVerifier,
    *,
    now: datetime | None = None,
) -> PublicationReferenceRefresh:
    """발행 직전 재검증. 행을 바꾸지 않는다 — 네트워크 GET 전에 필요한 값을 모두 읽어 둔다."""

    observed = now or datetime.now(timezone.utc)
    snapshot = reference_snapshot(item)
    raw_references = getattr(item, "references_list", None)
    references, malformed = split_reference_entries(raw_references)
    previous_checks = getattr(item, "reference_checks", None)
    topic_terms = item_topic_terms(item)
    required = references_required(item)
    # 발행 전 진료비·병원 선택 글: 작가가 인용해 통과한 수기 목록 문서도 남기지 않는다(GET 없이 뺀다).
    strip_curated = _strips_curated_references(item) and any(
        _is_curated_entry(entry) for entry in references
    )
    if (
        reference_gate_status(
            raw_references, previous_checks, topic_terms=topic_terms, now=observed
        ).current
        and not (required and not references)
        and not strip_curated
    ):
        return PublicationReferenceRefresh(
            snapshot=snapshot,
            references=references,
            checks=merge_reference_checks(previous_checks),
            already_current=True,
        )
    if not _has_generated_text(item):
        # 아직 생성되지 않은 슬롯 — 빼거나 채우면 판이 올라가 진행 중인 생성의 저장이 버려진다.
        # 호출부가 먼저 거르지만, 여기서도 아무것도 바꾸지 않는다.
        return PublicationReferenceRefresh(
            snapshot=snapshot,
            references=references,
            checks=merge_reference_checks(previous_checks),
        )
    outcome = await verifier.verify(
        [entry for entry in references if not (strip_curated and _is_curated_entry(entry))],
        topic_terms=topic_terms,
        previous_checks=previous_checks,
        reuse_fresh_checks=True,
        defer_transient=True,
        now=observed,
    )
    checks = merge_reference_checks(previous_checks, outcome.checks)
    if outcome.deferred:
        # 한 항목이라도 일시 장애·GET 한도로 판정하지 못했으면 아무것도 빼지 않고 미룬다.
        return PublicationReferenceRefresh(
            snapshot=snapshot,
            references=references,
            checks=checks,
            deferred=True,
            site_unreachable_urls=_deferred_urls(outcome.deferred_checks()),
        )
    kept = outcome.kept
    healed = False
    operator_decides = not kept and references_left_to_operator(item)
    if not kept and required and not operator_decides:
        failed_urls = {
            str(check.get("url") or "")
            for check in checks
            if check.get("verdict") != VERDICT_PASS
        }
        candidates = curated_sources_for_topic(topic_terms, exclude_urls=failed_urls)
        if candidates:
            filled = await verifier.verify(
                candidates, topic_terms=topic_terms, defer_transient=True, now=observed
            )
            checks = merge_reference_checks(checks, filled.checks)
            if filled.deferred and not filled.kept:
                return PublicationReferenceRefresh(
                    snapshot=snapshot,
                    references=references,
                    checks=checks,
                    deferred=True,
                    site_unreachable_urls=_deferred_urls(filled.deferred_checks()),
                )
            kept = filled.kept
            healed = bool(kept)
    return PublicationReferenceRefresh(
        snapshot=snapshot,
        references=kept,
        checks=checks,
        references_changed=bool(malformed) or kept != references,
        healed=healed,
        operator_decides=operator_decides,
    )


async def verify_publication_references(
    item: object,
    verifier: ReferenceVerifier,
    *,
    now: datetime | None = None,
) -> PublicationReferenceRefresh:
    """검증만 한다 — 참고자료를 빼지도 채우지도 않는다(restore 전용).

    결과를 적용해도 `reference_checks`만 바뀐다. 게이트가 current가 아니면 호출부가 거절한다.
    """

    observed = now or datetime.now(timezone.utc)
    snapshot = reference_snapshot(item)
    raw_references = getattr(item, "references_list", None)
    references, _malformed = split_reference_entries(raw_references)
    previous_checks = getattr(item, "reference_checks", None)
    topic_terms = item_topic_terms(item)
    if reference_gate_status(
        raw_references, previous_checks, topic_terms=topic_terms, now=observed
    ).current:
        return PublicationReferenceRefresh(
            snapshot=snapshot,
            references=references,
            checks=merge_reference_checks(previous_checks),
            already_current=True,
        )
    outcome = await verifier.verify(
        references,
        topic_terms=topic_terms,
        previous_checks=previous_checks,
        reuse_fresh_checks=True,
        defer_transient=True,
        now=observed,
    )
    return PublicationReferenceRefresh(
        snapshot=snapshot,
        references=references,
        checks=merge_reference_checks(previous_checks, outcome.checks),
        deferred=bool(outcome.deferred),
        site_unreachable_urls=_deferred_urls(outcome.deferred_checks()),
    )


def apply_publication_reference_refresh(
    item: Any, refresh: PublicationReferenceRefresh
) -> bool:
    """잠금 뒤 비교 후 적용. 스냅샷 이후 행이 바뀌었으면 아무것도 쓰지 않고 False.

    호출부가 행 잠금과 커밋을 소유한다. 참고자료가 바뀌는 적용은 판을 올린다. 발행 전
    (DRAFT·READY)이 아닌 행의 참고자료는 스냅샷이 같아도 바꾸지 않는다(False).
    """

    if not reference_snapshot_matches(item, refresh.snapshot):
        return False
    status = _status_value(item)
    if (
        refresh.references_changed
        and status is not None
        and status not in _REFERENCE_WRITABLE_STATUSES
    ):
        return False
    item.reference_checks = refresh.checks
    if refresh.deferred or not refresh.references_changed:
        return True
    item.references_list = refresh.references
    if hasattr(item, "content_revision"):
        item.content_revision = int(getattr(item, "content_revision", 1) or 1) + 1
    return True


def unverified_reference_details(item: object, *, now: datetime | None = None) -> list[dict]:
    """게이트를 통과하지 못한 참고자료와 마지막 판정 사유(운영자 문구용)."""

    status = reference_gate_status(
        getattr(item, "references_list", None),
        getattr(item, "reference_checks", None),
        topic_terms=item_topic_terms(item),
        now=now,
    )
    indexed = index_reference_checks(getattr(item, "reference_checks", None))
    details: list[dict] = []
    for url in status.unverified_urls:
        check = indexed.get(reference_url_fingerprint(url)) or {}
        reason = str(check.get("reason") or "") if check.get("verdict") != VERDICT_PASS else ""
        details.append(
            {
                "url": url,
                "reason": reason or None,
                "reason_label": reason_label(reason) if reason else "확인 기록 없음",
            }
        )
    details.extend(
        {"url": None, "reason": REASON_MALFORMED_ENTRY, "reason_label": reason_label(REASON_MALFORMED_ENTRY)}
        for _ in range(status.malformed_entries)
    )
    return details


def reference_outage_alert_due(scheduled_date: object, now_kst: Any) -> bool:
    """기관 사이트 장애로 미룬 글을 알릴 때인가 — 예정일의 마지막 발행기(23시)부터."""

    if scheduled_date is None:
        return False
    today = now_kst.date()
    return scheduled_date < today or (
        scheduled_date == today and now_kst.hour >= REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR
    )
