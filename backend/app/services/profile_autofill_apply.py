"""자동 입력 초안을 병원 행에 적용한다 — 빈 칸만, 마케터가 손대지 않은 칸만(D30).

순서(계획 §3.1b):

1. 병원 advisory 잠금(사람 PATCH와 같은 잠금) 아래서 병원 행을 다시 읽고, **같은 트랜잭션에서** 감사
   로그로 출처 원장을 복원한다 — 사람이 바꾸거나 비운 칸은 원장 백필 전이라도 마케터 소유다.
2. 대상 = 초안 키 ∩ 적용 허용 목록 중 **현재 값이 비어 있고** 마케터 소유(OPERATOR)가 아닌 필드.
   확인되지 않은 네이버 값(E4)은 적용하지 않는다. 값이 있는 칸·마케터 칸에서 자동 수집 값이 다르면
   원장에 ``conflict``로만 남긴다(사고·Slack 없음, Q1).
3. 적용할 모든 공개 텍스트에 의료광고 필터 — 걸린 단일 필드는 버리고, 목록은 걸린 원소만, 진료 항목은
   걸린 설명만 비운다(이름이 걸리면 그 항목을 뺀다). 지역이 섞인 핵심 키워드도 뺀다.
4. 의료진 행이 없을 때만 원장 값을 채우고, 원장명이 새로 생기면 대표 의료진 1명을 만든다.
5. 주소를 채우면 좌표를 지오코딩한다(실패는 좌표만 비워 둔다).
6. 홈페이지·블로그를 채우면 같은 트랜잭션에서 근거 자료 행을 등록한다.
7. 원장에 AUTOFILL·감사행 ``apply_profile_autofill``.
8. 완료 재판정(승격만) — 인수 수락이 없으면 조용히 건너뛴다.
9. 공개 중이면 공개 표면 재검증 intent(``SITE_REVALIDATION``)를 같은 트랜잭션에 남긴다.
10. 커밋 뒤: 채널 fetch, 완료 전환이면 사이트 준비 + V0, 공개 중이면 즉시 재검증(최선 노력 — 놓쳐도
    intent를 reconcile이 보낸다). 커밋 뒤 부수효과의 실패는 이미 커밋된 적용을 실패로 바꾸지 않는다.

``conflicts``(수집 단계에서 출처끼리 다른 값)와 ``profile_conflicts``(자동 수집 값 vs 지금 병원 값)는
다른 사실이라 실행 요약에 따로 둔다.

일시정지 병원은 손대지 않는다(배경 작업이 PAUSED를 바꾸지 않는다). 공개 중인 병원은 ``allow_live``가
없으면 적용하지 않고 적용할 필드만 계획으로 돌려준다 — 빈 칸이 공개 페이지에 새로 나타나기 때문이다.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun
from app.models.physician import HospitalPhysician
from app.services import profile_channel_sources, profile_completion
from app.services import profile_provenance as provenance
from app.services.audit_log import write_audit_log
from app.services.hospital_geocoding import GeocodingError, geocode_address
from app.services.hospital_lifecycle import (
    missing_profile_requirement_keys,
    profile_publication_verdict,
)
from app.services.hospital_physicians import derive_director_fields
from app.services.keyword_analysis import region_mixed_keywords
from app.services.naver_place import VERIFIED_IDENTITIES, stored_place_id
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.utils.db_locks import acquire_hospital_advisory_lock
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

APPLY_ACTOR = "system:profile-autofill"

STATUS_APPLIED = "APPLIED"
STATUS_NOTHING = "NOTHING_TO_APPLY"
STATUS_SKIPPED_LIVE = "SKIPPED_LIVE"
STATUS_SKIPPED_PAUSED = "SKIPPED_PAUSED"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_DRY_RUN = "DRY_RUN"

_TEXT_LIMITS: dict[str, int] = {
    "director_name": 100,
    "director_career": 2000,
    "director_philosophy": 1000,
    "address": 500,
    "phone": 50,
    "website_url": 500,
    "blog_url": 500,
    "kakao_channel_url": 500,
    "naver_place_url": 500,
    "naver_place_id": 100,
}
_URL_FIELDS = frozenset({"website_url", "blog_url", "kakao_channel_url", "naver_place_url"})
_LIST_FIELDS = frozenset({"region", "specialties", "keywords", "competitors"})
_DIRECTOR_FIELDS = ("director_name", "director_career", "director_philosophy")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_SURVEY_MAX_CHARS = 18_000


@dataclass
class AutofillPlan:
    """병원 행을 바꾸기 전의 결정(순수 계산). dry-run 출력도 이 모양이다."""

    updates: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    dropped_fields: list[dict] = field(default_factory=list)
    skipped_operator_fields: list[str] = field(default_factory=list)
    skipped_filled_fields: list[str] = field(default_factory=list)
    skipped_unverified_fields: list[str] = field(default_factory=list)
    profile_conflicts: list[dict] = field(default_factory=list)
    create_representative: bool = False


@dataclass
class ApplyOutcome:
    status: str
    applied_fields: list[str] = field(default_factory=list)
    would_apply_fields: list[str] = field(default_factory=list)
    dropped_fields: list[dict] = field(default_factory=list)
    skipped_operator_fields: list[str] = field(default_factory=list)
    skipped_filled_fields: list[str] = field(default_factory=list)
    skipped_unverified_fields: list[str] = field(default_factory=list)
    profile_conflicts: list[dict] = field(default_factory=list)
    geocode_warning: str | None = None
    channel_sources: list[dict] = field(default_factory=list)
    completion: dict[str, Any] | None = None
    followup_needed: bool = False

    def summary(self) -> dict[str, Any]:
        """실행 요약에 합칠 모양. 수집 단계의 ``conflicts``(출처 간)는 덮지 않는다."""

        payload = asdict(self)
        payload["apply_status"] = payload.pop("status")
        return payload


# ── 값 정리 ────────────────────────────────────────────────────────


def _clean_text(field_name: str, value: Any, dropped: list[dict]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        dropped.append({"field": field_name, "reason": "INVALID_VALUE"})
        return None
    text = value.strip()[: _TEXT_LIMITS.get(field_name, 500)]
    if field_name in _URL_FIELDS and not _URL_RE.match(text):
        dropped.append({"field": field_name, "reason": "INVALID_URL"})
        return None
    if field_name == "naver_place_id" and not text.isdigit():
        dropped.append({"field": field_name, "reason": "INVALID_VALUE"})
        return None
    found = check_forbidden(text)
    if found:
        dropped.append({"field": field_name, "reason": "MEDICAL_AD_FILTER", "expressions": found})
        return None
    return text


def _clean_list(
    field_name: str, value: Any, dropped: list[dict], *, regions: list[str]
) -> list[str] | None:
    if not isinstance(value, list):
        dropped.append({"field": field_name, "reason": "INVALID_VALUE"})
        return None
    kept: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        text = item.strip()[:100]
        found = check_forbidden(text)
        if found:
            dropped.append(
                {
                    "field": field_name,
                    "item": text,
                    "reason": "MEDICAL_AD_FILTER",
                    "expressions": found,
                }
            )
            continue
        if text not in kept:
            kept.append(text)
    if field_name == "keywords" and kept:
        mixed = {item["keyword"] for item in region_mixed_keywords(kept, regions)}
        for keyword in [item for item in kept if item in mixed]:
            dropped.append({"field": field_name, "item": keyword, "reason": "REGION_KEYWORD_MIXED"})
        kept = [item for item in kept if item not in mixed]
    if not kept:
        dropped.append({"field": field_name, "reason": "EMPTY_AFTER_FILTER"})
        return None
    return kept


def _clean_treatments(value: Any, dropped: list[dict]) -> list[dict] | None:
    if not isinstance(value, list):
        dropped.append({"field": "treatments", "reason": "INVALID_VALUE"})
        return None
    kept: list[dict] = []
    for item in value:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()[:200]
        if not name:
            continue
        found = check_forbidden(name)
        if found:
            dropped.append(
                {"field": "treatments", "item": name, "reason": "MEDICAL_AD_FILTER", "expressions": found}
            )
            continue
        description = str(item.get("description") or "").strip()[:500] or None
        if description:
            found = check_forbidden(description)
            if found:
                dropped.append(
                    {
                        "field": "treatments.description",
                        "item": name,
                        "reason": "MEDICAL_AD_FILTER",
                        "expressions": found,
                    }
                )
                description = None
        kept.append({"name": name, "description": description})
    if not kept:
        dropped.append({"field": "treatments", "reason": "EMPTY_AFTER_FILTER"})
        return None
    return kept


def _clean_business_hours(value: Any, dropped: list[dict]) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        dropped.append({"field": "business_hours", "reason": "INVALID_VALUE"})
        return None
    hours = {
        day: str(value[day]).strip()[:100]
        for day in _DAYS
        if isinstance(value.get(day), str) and str(value[day]).strip()
    }
    if not hours:
        dropped.append({"field": "business_hours", "reason": "INVALID_VALUE"})
        return None
    found = check_forbidden(" ".join(hours.values()))
    if found:
        dropped.append({"field": "business_hours", "reason": "MEDICAL_AD_FILTER", "expressions": found})
        return None
    return hours


def _clean_value(field_name: str, value: Any, dropped: list[dict], *, regions: list[str]) -> Any:
    if field_name in _LIST_FIELDS:
        return _clean_list(field_name, value, dropped, regions=regions)
    if field_name == "treatments":
        return _clean_treatments(value, dropped)
    if field_name == "business_hours":
        return _clean_business_hours(value, dropped)
    return _clean_text(field_name, value, dropped)


# ── 계획 ───────────────────────────────────────────────────────────


def summary_naver_verified(summary: Mapping[str, Any], hospital: Any) -> bool:
    """이 초안의 네이버 값이 확인된 것인가(E4).

    신원 기록이 없는 옛 초안(E4 이전)은 확인되지 않은 것으로 본다 — 초안의 place id가 병원 행에
    저장된 id와 같을 때만 예외다.
    """

    if "naver_identity" in summary:
        return summary.get("naver_identity") in VERIFIED_IDENTITIES
    draft = summary.get("draft") if isinstance(summary.get("draft"), Mapping) else {}
    draft_id = str(draft.get("naver_place_id") or summary.get("naver_place_id") or "").strip()
    stored = stored_place_id(
        getattr(hospital, "naver_place_id", None), getattr(hospital, "naver_place_url", None)
    )
    return bool(draft_id) and draft_id == stored


def plan_autofill(
    hospital: Any, summary: Mapping[str, Any], *, has_physicians: bool
) -> AutofillPlan:
    """초안을 이 병원에 어떻게 적용할지 정한다(병원 행은 바꾸지 않는다)."""

    plan = AutofillPlan()
    draft = summary.get("draft") if isinstance(summary.get("draft"), Mapping) else {}
    field_meta = summary.get("field_meta") if isinstance(summary.get("field_meta"), Mapping) else {}
    naver_verified = summary_naver_verified(summary, hospital)
    draft_regions = draft.get("region") if isinstance(draft.get("region"), list) else []
    current_regions = list(getattr(hospital, "region", None) or [])

    for field_name in provenance.AUTOFILL_APPLICABLE_FIELDS:
        if field_name not in draft:
            continue
        value = draft[field_name]
        if provenance.is_empty_value(value):
            continue
        meta = field_meta.get(field_name) if isinstance(field_meta.get(field_name), Mapping) else {}
        source = str(meta.get("source") or "unknown")
        verified = meta.get("verified")
        if verified is None:
            verified = source != "naver" or naver_verified
        if not verified:
            plan.skipped_unverified_fields.append(field_name)
            continue
        current = getattr(hospital, field_name, None)
        operator_owned = provenance.is_operator_owned(hospital, field_name)
        if operator_owned or not provenance.is_empty_value(current):
            if operator_owned:
                plan.skipped_operator_fields.append(field_name)
            else:
                plan.skipped_filled_fields.append(field_name)
            # 마케터가 비운 칸도 자동 수집 값과 다르면 충돌이다 — 값은 바꾸지 않는다(Q1).
            if provenance.semantically_changed(current, value):
                plan.profile_conflicts.append(
                    {"field": field_name, "source": source, "value": value}
                )
            continue
        if field_name in _DIRECTOR_FIELDS and has_physicians:
            plan.dropped_fields.append({"field": field_name, "reason": "PHYSICIANS_PRESENT"})
            continue
        regions = current_regions or [item for item in draft_regions if isinstance(item, str)]
        cleaned = _clean_value(field_name, value, plan.dropped_fields, regions=regions)
        if cleaned is None:
            continue
        plan.updates[field_name] = cleaned
        plan.sources[field_name] = source

    plan.create_representative = (
        not has_physicians
        and "director_name" in plan.updates
        and provenance.is_empty_value(getattr(hospital, "director_name", None))
    )
    return plan


# ── 실행 ───────────────────────────────────────────────────────────


async def load_survey_text(db: AsyncSession, hospital_id: uuid.UUID) -> str | None:
    """원장 인터뷰 설문 본문(저장 전 개인정보 가림이 된 raw_text) — 자동 입력의 설문 출처."""

    rows = (
        await db.execute(
            select(HospitalSourceAsset.raw_text)
            .where(
                HospitalSourceAsset.hospital_id == hospital_id,
                HospitalSourceAsset.source_type == SourceType.INTERVIEW,
                HospitalSourceAsset.status != SourceStatus.EXCLUDED,
                HospitalSourceAsset.raw_text.is_not(None),
            )
            .order_by(HospitalSourceAsset.created_at)
        )
    ).scalars().all()
    text = "\n\n".join(item.strip() for item in rows if item and item.strip())
    return text[:_SURVEY_MAX_CHARS] or None


def _is_live(hospital: Hospital) -> bool:
    return profile_publication_verdict(hospital).allowed


async def _has_physicians(db: AsyncSession, hospital_id: uuid.UUID) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(HospitalPhysician)
        .where(HospitalPhysician.hospital_id == hospital_id)
    )
    return bool(count)


async def _store_run_summary(
    db: AsyncSession, run_id: uuid.UUID | None, outcome: ApplyOutcome
) -> None:
    if run_id is None:
        return
    run = await db.get(OperationRun, run_id)
    if run is None:
        return
    summary = dict(run.result_summary or {})
    summary.update(outcome.summary())
    summary["apply_attempts"] = int(summary.get("apply_attempts") or 0) + 1
    summary["applied_at"] = datetime.now(UTC).isoformat()
    run.result_summary = summary


async def apply_autofill_draft(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    run_id: uuid.UUID | None = None,
    allow_live: bool = False,
    dry_run: bool = False,
    collected_without_channels: bool = False,
) -> ApplyOutcome:
    """초안을 적용하고 커밋한다. 커밋 뒤 부수효과(채널 fetch·완료 전환·재검증)까지 낸다.

    ``dry_run``이면 아무것도 쓰지 않고 계획만 돌려준다. ``collected_without_channels``는 이 초안을
    홈페이지·블로그 없이(네이버만) 모았다는 뜻 — 이번 적용이 주소를 새로 채우면 ``followup_needed``.
    """

    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await db.get(Hospital, hospital_id, populate_existing=True)
    if hospital is None:
        return ApplyOutcome(status=STATUS_NOT_FOUND)
    if hospital.status == HospitalStatus.PAUSED:
        outcome = ApplyOutcome(status=STATUS_SKIPPED_PAUSED)
        if not dry_run:
            await _store_run_summary(db, run_id, outcome)
            await db.commit()
        return outcome

    # 원장 백필 CLI를 기다리지 않는다 — 감사 로그의 사람 변경(칸 비우기 포함)을 먼저 원장에 올린 뒤
    # 계획한다. 같은 advisory 잠금 안이라 PATCH와 엇갈리지 않는다.
    await provenance.backfill_provenance_from_audit(db, hospital)
    has_physicians = await _has_physicians(db, hospital_id)
    plan = plan_autofill(hospital, summary, has_physicians=has_physicians)
    live = _is_live(hospital)
    base = dict(
        dropped_fields=plan.dropped_fields,
        skipped_operator_fields=plan.skipped_operator_fields,
        skipped_filled_fields=plan.skipped_filled_fields,
        skipped_unverified_fields=plan.skipped_unverified_fields,
        profile_conflicts=plan.profile_conflicts,
    )
    if dry_run:
        # 복원한 원장으로 계획만 하고 아무것도 남기지 않는다.
        await db.rollback()
        return ApplyOutcome(status=STATUS_DRY_RUN, would_apply_fields=sorted(plan.updates), **base)
    if live and not allow_live:
        outcome = ApplyOutcome(
            status=STATUS_SKIPPED_LIVE, would_apply_fields=sorted(plan.updates), **base
        )
        await _store_run_summary(db, run_id, outcome)
        await db.commit()
        return outcome

    now = datetime.now(UTC)
    missing_before = set(missing_profile_requirement_keys(hospital))
    for item in plan.profile_conflicts:
        provenance.record_conflict(
            hospital, item["field"], source=item["source"], value=item["value"], at=now
        )
    if not plan.updates:
        outcome = ApplyOutcome(status=STATUS_NOTHING, **base)
        await _store_run_summary(db, run_id, outcome)
        await db.commit()
        return outcome

    for field_name, value in plan.updates.items():
        setattr(hospital, field_name, value)
    if plan.create_representative:
        from app.api.admin.physicians import PhysicianInput, replace_physicians

        stored = await replace_physicians(
            db,
            hospital.id,
            [
                PhysicianInput(
                    name=plan.updates["director_name"],
                    career=plan.updates.get("director_career"),
                    is_representative=True,
                )
            ],
        )
        for field_name, value in derive_director_fields(stored).items():
            setattr(hospital, field_name, value)

    geocode_warning = None
    if (
        "address" in plan.updates
        and hospital.latitude is None
        and hospital.longitude is None
    ):
        try:
            coordinates = await geocode_address(plan.updates["address"])
        except GeocodingError as exc:
            geocode_warning = str(exc)
            logger.warning("Autofill geocoding failed for hospital %s: %s", hospital.id, exc)
        else:
            hospital.latitude = coordinates.latitude
            hospital.longitude = coordinates.longitude

    provenance.record_autofill_fields(hospital, plan.sources, run_id=run_id, at=now)

    submitted = {
        field_name: (plan.updates[field_name], True)
        for field_name in profile_channel_sources.CHANNEL_SOURCE_TYPES
        if field_name in plan.updates
    }
    channel_entries, fetch_ids = await profile_channel_sources.register_channel_sources(
        db, hospital, submitted, actor=APPLY_ACTOR
    )

    completion = await profile_completion.evaluate_profile_completion(
        db, hospital, missing_before=missing_before, promote_only=True
    )
    if completion.blocked:
        logger.info(
            "Autofill left hospital %s incomplete: %s",
            hospital.id,
            (completion.blocker or {}).get("code"),
        )
    completion_detail = {
        **profile_completion.completion_audit_detail(completion),
        "blocked": (completion.blocker or {}).get("code"),
    }

    outcome = ApplyOutcome(
        status=STATUS_APPLIED,
        applied_fields=sorted(plan.updates),
        geocode_warning=geocode_warning,
        channel_sources=[
            {**entry, "source_id": str(entry["source_id"]) if entry.get("source_id") else None}
            for entry in channel_entries
        ],
        completion=completion_detail,
        followup_needed=collected_without_channels and bool(submitted),
        **base,
    )
    await write_audit_log(
        db,
        action="apply_profile_autofill",
        hospital_id=hospital.id,
        actor=APPLY_ACTOR,
        target_type="hospital",
        target_id=hospital.id,
        detail={
            "run_id": str(run_id) if run_id else None,
            "applied_fields": outcome.applied_fields,
            "dropped_fields": outcome.dropped_fields,
            "skipped_operator_fields": outcome.skipped_operator_fields,
            "skipped_unverified_fields": outcome.skipped_unverified_fields,
            "profile_conflicts": [item["field"] for item in outcome.profile_conflicts],
            **completion_detail,
        },
    )
    await _store_run_summary(db, run_id, outcome)
    if live:
        # 공개 값이 바뀌었다 — 재검증 의도를 공개 변경과 같은 커밋에 남긴다(커밋 직후 죽어도 남는다).
        enqueue_public_surface_intent(db, hospital)
    slug, treatments, name = hospital.slug, hospital.treatments, hospital.name
    await db.commit()

    # 커밋 뒤에만 보낸다 — 워커가 아직 없는 행을 찾아 헛돌지 않게 한다. 여기서의 실패는 이미 커밋된
    # 적용을 되돌리지 않는다(실패 기록으로 바꾸면 스윕이 같은 초안을 다시 적용하려 든다).
    profile_channel_sources.dispatch_channel_source_fetches(fetch_ids)
    if completion.transitioned:
        await _after_commit(
            "completion side effects",
            hospital_id,
            profile_completion.dispatch_completion_side_effects(
                db, hospital_id, actor=APPLY_ACTOR
            ),
        )
    if live:
        from app.services.site_revalidate import trigger_hospital_site_revalidate_safe

        await _after_commit(
            "site revalidation",
            hospital_id,
            trigger_hospital_site_revalidate_safe(slug, treatments, hospital_name=name),
        )
    return outcome


async def _after_commit(label: str, hospital_id: uuid.UUID, step: Awaitable[Any]) -> None:
    try:
        await step
    except Exception:  # noqa: BLE001 — 커밋된 적용은 성공이다. 놓친 부수효과는 각자의 스윕이 회수한다.
        logger.warning(
            "Autofill post-commit %s failed for hospital %s", label, hospital_id, exc_info=True
        )


__all__ = (
    "APPLY_ACTOR",
    "ApplyOutcome",
    "AutofillPlan",
    "apply_autofill_draft",
    "load_survey_text",
    "plan_autofill",
    "summary_naver_verified",
)
