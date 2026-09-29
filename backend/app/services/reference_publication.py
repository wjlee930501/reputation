"""발행 직전 참고자료 재검증 — 07:45 게이트·08:00 발행기·관리자 수동 발행·restore가 같은 규칙을 쓴다.

발행 게이트의 계약(2026-09-29 참고자료 전수 점검 후속):

- 모든 참고자료에 **같은 URL**(지문)·**같은 글 주제**(지문)의 **신선한**(`REFERENCE_CHECK_MAX_AGE`
  이내, 미래 시각은 `REFERENCE_CHECK_CLOCK_SKEW`까지만) 통과 기록이 있어야 공개한다. 없거나
  오래됐거나 글 주제가 바뀌었으면 워커가 다시 검증한다 — 표시 전용 순수 함수는 GET하지 않는다.
  형식이 깨진 항목(매핑이 아님·빈 주소)도 통과하지 못한다.
- 다시 검증해 떨어진 항목은 빼고, 남은 것이 1개 이상이면 그대로 발행한다.
- 전부 떨어지고 참고자료가 필수인 유형이면 이 글의 주제로 채점한 수기 목록에서 채운다(같은
  검증을 거친다). 그래도 없으면 참고자료를 비워 기존 `MISSING_REFERENCES` 차단(발행 보류 +
  인시던트 + 자동 본문 수리 세션)으로 보낸다. 실패한 URL과 사유는 `reference_checks`에 남는다.
- 기관 사이트 장애(연결 오류·시간 초과·프로토콜·5xx·408/429)는 검증기 쪽에서 같은 URL·같은
  주제의 직전 통과 판정을 재사용한다. 재사용할 통과가 없는 목록 밖 URL이면 그 글을 **미룬다**
  (`deferred`, `site_unreachable_urls`) — 제거·치유·`MISSING_REFERENCES`·인시던트가 없고 다음
  시간대 발행기(08~23시 매시)가 다시 연다. 미룸이 발행 예정일의 마지막 발행기(23시) 또는 그
  뒤에도 이어지면 08:00 발행 요약에 `REFERENCE_SITE_UNREACHABLE`("기관 사이트에 접속하지 못해")
  한 줄로 한 번 알린다(같은 상태는 요약 dedupe가 다시 보내지 않는다). 발행기 catch-up 기간
  (`AUTO_PUBLISH_CATCHUP_DAYS`)이 재시도의 끝이다.
- 실행당 GET 상한을 넘으면 그 글은 이번 실행에서 미룬다 — 차단·인시던트·알림이 아니다.
- 네트워크 GET은 행 잠금 밖에서 한다. 재검증 결과는 시작 시점의 스냅샷(판·참고자료·주제 지문)을
  들고 다니고, 잠금 뒤 `apply_publication_reference_refresh`가 스냅샷이 그대로일 때만 쓴다 —
  그 사이 편집·재생성이 있었으면 아무것도 덮어쓰지 않고 False를 돌려준다.
- restore(비공개 보존 글 재공개)는 `verify_publication_references`로 **검증만** 한다. 공개됐던
  글의 참고자료는 빼지도, 채우지도, 바꾸지도 않는다 — 통과하지 못하면 restore를 거절한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.content_engine import REFERENCES_REQUIRED_TYPES
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

# 08:00 발행 요약에 실리는, 기관 사이트 장애로 발행을 미룬 글의 코드와 원인 문구.
# '참고 자료 확보 실패'가 아니다 — 문서가 없다는 증거가 아니라 사이트가 열리지 않았다.
REFERENCE_SITE_UNREACHABLE_CODE = "REFERENCE_SITE_UNREACHABLE"
REFERENCE_SITE_UNREACHABLE_CAUSE = (
    "참고 자료 기관 사이트에 접속하지 못해 주소를 확인하지 못했습니다 — 발행을 미루고 "
    "다음 발행 시간대에 자동으로 다시 확인합니다."
)
# 발행기는 08~23시 매시 돈다(`celery_app` beat). 이 시각 실행이 예정일의 마지막 기회다.
REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR = 23

_REQUIRED_TYPE_VALUES = frozenset(
    str(getattr(content_type, "value", content_type)).upper()
    for content_type in REFERENCES_REQUIRED_TYPES
)


def references_required(item: object) -> bool:
    """생성·발행 게이트와 같은 유형 집합. 유형을 못 읽으면 요구하는 쪽으로 둔다."""

    content_type = getattr(item, "content_type", None)
    if content_type is None:
        return True
    return str(getattr(content_type, "value", content_type) or "").upper() in _REQUIRED_TYPE_VALUES


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


def reference_snapshot(item: object) -> ReferenceSnapshot:
    revision = getattr(item, "content_revision", None)
    return ReferenceSnapshot(
        content_revision=int(revision) if revision is not None else None,
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


def publication_references_current(item: object, *, now: datetime | None = None) -> bool:
    """발행 게이트 — 모든 참고자료에 같은 URL·같은 주제의 신선한 통과 기록이 있는가."""

    return reference_gate_status(
        getattr(item, "references_list", None),
        getattr(item, "reference_checks", None),
        topic_terms=item_topic_terms(item),
        now=now,
    ).current


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
    if not kept and required:
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

    호출부가 행 잠금과 커밋을 소유한다. 참고자료가 바뀌는 적용은 판을 올린다.
    """

    if not reference_snapshot_matches(item, refresh.snapshot):
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
