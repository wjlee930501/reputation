"""마감된 달의 저장 답변을 지금 판정 방식(규칙 대조)으로 다시 판정한다 — 답변을 새로 사지 않는다.

2026-11-09 언급 판정이 규칙 대조(`mention_identity`)로 바뀌었다. 그 이전에 잰 달은 옛 판정(이름 사전 필터 +
gpt-4o-mini)이라 실제 언급을 놓친 기록이 많다(9월 97건·10월 53건 — 공식 등록명·'연합' 표기 등). 이 모듈은
그 달 측정표에 묶인 저장 답변을 지금 규칙으로 다시 판정해 같은 판정 기준으로 만든다.

- **확정 여부를 바꾸지 않는다.** 커버리지(측정 완료도)는 슬롯 상태로, 점수는 기록 판정으로 세므로 기록이
  확정↔보류로 바뀌면 둘이 어긋난다. 그래서 확정 기록(MATCHED·NOT_MATCHED)만 언급 여부를 다시 정하고
  보류(AMBIGUOUS)·실패 기록은 그대로 둔다.
- 옛 판정이 언급이라 한 기록은 규칙도 모두 언급이었다(9·10월 전수 확인). 규칙이 언급이라 하면 MATCHED로
  올리고, 아니라고 하면 기존 판정을 둔다 — 그래서 보조 판정(모델) 호출이 없다.
- 경쟁 병원은 지금 등록된 이름 전부를 같은 규칙으로 다시 판정해 덮는다(한 달 안에 두 판정 기준이 섞이지 않게).
  등록에서 빠진 이름의 옛 판정은 지우지 않는다(보고서 조립이 등록 목록으로 거른다).
- 끝나면 `REJUDGE_MONTH_MENTIONS` 실행 기록(SUCCEEDED)에 쓴 판정 정책 지문을 남긴다. 측정표는 고칠 수 없으므로
  (DB 트리거) 이 기록이 그 달의 **실제 판정 기준**이다 — 지난달 비교(`effective_measurement_protocol`)와
  경쟁 병원 반영 차단이 이 기록을 읽는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.models.hospital import Hospital
from app.models.monthly_control import (
    MonthlyMeasurementAttempt,
    MonthlyMeasurementCell,
    MonthlyMeasurementManifest,
)
from app.models.operations import OperationRun
from app.models.sov import SovRecord
from app.services import mention_identity, sov_engine
from app.services.audit_log import write_audit_log_sync
from app.services.competitor_scope import competitor_key, registered_competitor_names

OPERATION_TYPE = "REJUDGE_MONTH_MENTIONS"
ACTOR = "system:month-mention-rejudge"
_JUDGE_KEYS = ("judge_prompt_fingerprint", "judge_model")


def current_judge_policy() -> dict[str, Any]:
    protocol = sov_engine.measurement_protocol()
    return {key: protocol.get(key) for key in _JUDGE_KEYS}


def month_manifest(db, hospital_id: uuid.UUID, year: int, month: int) -> MonthlyMeasurementManifest | None:
    return (
        db.execute(
            select(MonthlyMeasurementManifest)
            .where(
                MonthlyMeasurementManifest.hospital_id == hospital_id,
                MonthlyMeasurementManifest.period_year == year,
                MonthlyMeasurementManifest.period_month == month,
            )
            .order_by(MonthlyMeasurementManifest.frozen_at.desc())
        )
        .scalars()
        .first()
    )


def latest_rejudge_run(db, manifest_id: uuid.UUID) -> OperationRun | None:
    """이 측정표를 다시 판정한 가장 최근의 성공 실행."""
    return (
        db.execute(
            select(OperationRun)
            .where(
                OperationRun.operation_type == OPERATION_TYPE,
                OperationRun.state == "SUCCEEDED",
                OperationRun.result_summary["manifest_id"].as_string() == str(manifest_id),
            )
            .order_by(OperationRun.completed_at.desc().nullslast(), OperationRun.created_at.desc())
        )
        .scalars()
        .first()
    )


def effective_measurement_protocol(db, manifest: MonthlyMeasurementManifest | None) -> dict | None:
    """그 달 측정의 실제 기준 — 측정표의 동결 기준에, 재판정했다면 그 판정 정책을 덮어쓴 것.

    측정표의 동결 기준은 고칠 수 없다(DB 트리거). 저장 답변을 지금 규칙으로 다시 판정한 달은 판정 정책만
    지금 것이므로, 지난달 비교·경쟁 병원 반영 차단은 이 함수로 그 달의 기준을 읽는다.
    """
    if manifest is None:
        return None
    provenance = manifest.platform_provenance if isinstance(manifest.platform_provenance, dict) else {}
    protocol = provenance.get("measurement_protocol")
    if not isinstance(protocol, dict):
        return None
    run = latest_rejudge_run(db, manifest.id)
    summary = run.result_summary if run is not None and isinstance(run.result_summary, dict) else None
    if summary is None:
        return dict(protocol)
    policy = summary.get("judge_policy")
    if not isinstance(policy, Mapping) or any(not isinstance(policy.get(key), str) for key in _JUDGE_KEYS):
        return dict(protocol)
    return {**protocol, **{key: policy[key] for key in _JUDGE_KEYS}}


def month_identity(manifest: MonthlyMeasurementManifest, hospital: Hospital) -> mention_identity.MentionIdentity:
    """측정표에 고정한 판정 신원(있으면) — 없으면 지금 병원 정보."""
    provenance = manifest.platform_provenance if isinstance(manifest.platform_provenance, dict) else {}
    frozen = mention_identity.MentionIdentity.from_material(provenance.get("judgment_identity"))
    if frozen is not None:
        return frozen
    terms = [str(term) for term in (hospital.region or []) if str(term or "").strip()]
    return mention_identity.MentionIdentity.build(
        hospital.name, address=hospital.address, phone=hospital.phone, region_terms=terms
    )


def manifest_records(db, manifest: MonthlyMeasurementManifest) -> list[SovRecord]:
    """측정표 셀에 묶인 저장 답변 전부(중복 없이)."""
    rows = (
        db.execute(
            select(SovRecord)
            .join(MonthlyMeasurementAttempt, MonthlyMeasurementAttempt.sov_record_id == SovRecord.id)
            .join(MonthlyMeasurementCell, MonthlyMeasurementCell.id == MonthlyMeasurementAttempt.cell_id)
            .where(MonthlyMeasurementCell.manifest_id == manifest.id)
            .order_by(SovRecord.measured_at, SovRecord.id)
        )
        .scalars()
        .unique()
        .all()
    )
    return list(rows)


@dataclass(slots=True)
class RecordChange:
    record_id: uuid.UUID
    self_to_matched: bool
    matched_text: str | None
    context: str | None
    rank: int | None
    competitors: list[dict[str, Any]] | None


@dataclass(slots=True)
class MonthRejudgePlan:
    hospital_id: uuid.UUID
    hospital_name: str
    year: int
    month: int
    manifest_id: uuid.UUID | None
    records_total: int = 0
    confirmed_records: int = 0
    self_flips: int = 0
    competitor_changes: int = 0
    already_current: bool = False
    changes: list[RecordChange] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "hospital_id": str(self.hospital_id),
            "hospital": self.hospital_name,
            "period": f"{self.year:04d}-{self.month:02d}",
            "manifest_id": str(self.manifest_id) if self.manifest_id else None,
            "records_total": self.records_total,
            "confirmed_records": self.confirmed_records,
            "self_flips": self.self_flips,
            "competitor_changes": self.competitor_changes,
            "already_current": self.already_current,
        }


def _merged_competitors(
    existing: Iterable[Any] | None, judged: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    judged_by_key = {competitor_key(item["name"]): item for item in judged}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in existing or []:
        if not isinstance(entry, dict):
            continue
        key = competitor_key(str(entry.get("name") or ""))
        if key in judged_by_key:
            if key not in seen:
                merged.append(dict(judged_by_key[key]))
                seen.add(key)
            continue
        merged.append(dict(entry))
    for key, item in judged_by_key.items():
        if key not in seen:
            merged.append(dict(item))
    return merged


def _competitor_signature(entries: Iterable[Any] | None, keys: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            key = competitor_key(str(entry.get("name") or ""))
            if key in keys:
                out[key] = (entry.get("is_mentioned"), entry.get("mention_rank"))
    return out


def build_plan(db, hospital: Hospital, year: int, month: int) -> MonthRejudgePlan:
    """읽기 전용 — 무엇이 바뀌는지. 공급자 호출·쓰기 없음."""
    manifest = month_manifest(db, hospital.id, year, month)
    plan = MonthRejudgePlan(hospital.id, hospital.name, year, month, manifest.id if manifest else None)
    if manifest is None:
        return plan
    effective = effective_measurement_protocol(db, manifest) or {}
    current = current_judge_policy()
    plan.already_current = all(effective.get(key) == current[key] for key in _JUDGE_KEYS)
    identity = month_identity(manifest, hospital)
    registered = registered_competitor_names(hospital)
    registered_keys = {competitor_key(name) for name in registered}
    records = manifest_records(db, manifest)
    plan.records_total = len(records)
    for record in records:
        raw = str(record.raw_response or "")
        if not sov_engine.record_is_confirmed(record) or not raw.strip():
            continue
        plan.confirmed_records += 1
        resolution = mention_identity.resolve(identity, raw)
        to_matched = resolution.matched and record.is_mentioned is not True
        competitors = None
        if registered:
            judged = [
                {
                    "name": name,
                    "is_mentioned": (found := mention_identity.resolve(identity.for_competitor(name), raw)).matched,
                    "mention_rank": found.rank if found.matched else None,
                }
                for name in registered
            ]
            merged = _merged_competitors(record.competitor_mentions, judged)
            if _competitor_signature(merged, registered_keys) != _competitor_signature(
                record.competitor_mentions, registered_keys
            ):
                competitors = merged
        if to_matched or competitors is not None:
            plan.changes.append(
                RecordChange(
                    record_id=record.id,
                    self_to_matched=to_matched,
                    matched_text=resolution.matched_text if to_matched else None,
                    context=" ".join((resolution.context or "").split())[:400] if to_matched else None,
                    rank=resolution.rank if to_matched else None,
                    competitors=competitors,
                )
            )
            plan.self_flips += int(to_matched)
            plan.competitor_changes += int(competitors is not None)
    return plan


def execute(db, hospital: Hospital, year: int, month: int, *, now: datetime | None = None) -> MonthRejudgePlan:
    """계획대로 기록을 고치고 실행 기록(SUCCEEDED)에 판정 정책을 남긴다 — 한 트랜잭션.

    이미 지금 판정 정책으로 다시 판정한 달은 아무것도 하지 않는다(멱등).
    """
    plan = build_plan(db, hospital, year, month)
    if plan.manifest_id is None or plan.already_current:
        return plan
    completed = now or datetime.now(UTC)
    for change in plan.changes:
        record = db.execute(
            select(SovRecord).where(SovRecord.id == change.record_id).with_for_update()
        ).scalar_one()
        if not sov_engine.record_is_confirmed(record):
            continue  # 그 사이 바뀐 기록은 건드리지 않는다
        if change.self_to_matched and record.is_mentioned is not True:
            record.is_mentioned = True
            record.mention_verdict = sov_engine.VERDICT_MATCHED
            record.mention_context = change.context
            record.mention_rank = change.rank
        if change.competitors is not None:
            record.competitor_mentions = change.competitors
    policy = current_judge_policy()
    manifest = db.get(MonthlyMeasurementManifest, plan.manifest_id)
    db.add(
        OperationRun(
            hospital_id=hospital.id,
            operation_type=OPERATION_TYPE,
            state="SUCCEEDED",
            idempotency_key=f"{OPERATION_TYPE}:{plan.manifest_id}:{policy['judge_prompt_fingerprint']}",
            total_count=plan.confirmed_records,
            success_count=len(plan.changes),
            skipped_count=0,
            request_payload={"year": year, "month": month, "actor": ACTOR},
            result_summary={
                **plan.summary(),
                "judge_policy": policy,
                "resolver": mention_identity.RESOLVER_VERSION,
                "identity": month_identity(manifest, hospital).material(),
            },
            started_at=completed,
            completed_at=completed,
        )
    )
    write_audit_log_sync(
        db,
        action="month_mention_rejudge",
        hospital_id=hospital.id,
        actor=ACTOR,
        target_type="monthly_measurement_manifest",
        target_id=plan.manifest_id,
        detail={**plan.summary(), "judge_policy": policy},
    )
    db.flush()
    return plan
