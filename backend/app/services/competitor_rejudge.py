"""경쟁 병원을 뒤늦게 등록한 뒤, 지난달 저장 답변에 대한 **경쟁사 판정만** 다시 한다.

월간 측정은 다음 달 1일 00:15~07:00 KST 새벽 창에서 지난달을 재고, 07:00 뒤에는 새 유료 측정 단계
(답변 구매·판정 체크포인트)를 하지 않는다(CLAUDE.md '측정·리포트'). 이 모듈은 그 규칙과 충돌하지 않는다.

- 답변을 새로 사지 않는다. 고정 매니페스트의 확정 셀에 묶인 `SovRecord.raw_response`(이미 저장된 답변
  원문)만 읽는다. 측정 슬롯·셀 상태·자사 판정(`is_mentioned`·`mention_verdict`)은 손대지 않는다.
- 다시 묻는 것은 측정 때 `sov_engine._parse_competitors`가 했던 것과 **같은 판정**(2026-11-09부터 이름·위치
  규칙 대조 — 모델 호출 없음)이다. 측정 당시 등록돼 있지 않아 물어보지 못한 이름만 묻는다 —
  이미 판정이 있는 이름은 덮지 않고, 등록에서 빠진 이름은 지우지 않는다(보고서 조립이 등록 목록으로 거른다).
- 그래서 마감이 지난 달도 대상이다. 보고서의 분모·노출률·셀 집계는 바뀌지 않고, 경쟁 현황(04절·1면 카드·
  P6·부록)만 현재 `competitor_mentions`에서 다시 계산된다(`COMPETITOR_REFRESH` 템플릿 갱신).
- 비용은 월간 측정과 같은 `sov` 카테고리로 예약·정산하고, 병원당 상한은 셀(답변) 수 × 1회다. 판정 실패는
  `is_mentioned=null`(미확정, `verdict=UNRESOLVED`)로 남겨 0으로 세지 않으며, 다음 실행이 다시 묻는다.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, SovRecord
from app.services import cost_guard, mention_identity, sov_engine
from app.services.competitor_scope import (
    competitor_key,
    registered_competitor_names,
    scope_competitor_records,
)
from app.services.measurement_manifest_policy import legacy_manifest_is_final
from app.services.monthly_period import monthly_recovery_deadline
from app.services.monthly_report_snapshot import report_render_inputs
from app.services.monthly_sov_repository import load_monthly_sov_manifest

logger = logging.getLogger(__name__)

REJUDGE_OPERATION_TYPE = "REJUDGE_COMPETITORS"
REJUDGE_TASK_NAME = "app.workers.competitor_refresh.rejudge_month_competitors"
REJUDGE_DISPATCH_PURPOSE = "rejudge-month-competitors"
REJUDGE_QUEUE = "sov"
COST_CATEGORY = sov_engine.POOL_SOV
UNRESOLVED_VERDICT = "UNRESOLVED"
JUDGED_VERDICT = "REJUDGED"
# 셀 단위 재시도 — 판정기 장애(형식 오류·공급자 오류)는 같은 실행 안에서 최대 3회까지 다시 묻는다.
MAX_JUDGMENT_ATTEMPTS = 3

JudgeFn = Callable[[list[str], str], Awaitable[list[dict[str, Any]]]]

_tls = threading.local()


def _run_async(coro):
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop.run_until_complete(coro)


# ── 순수 함수: 이름·판정 합치기 ──────────────────────────────────────────────


def _name_key(name: str) -> str:
    return competitor_key(name)


def judged_names(mentions: Iterable[Any] | None) -> set[str]:
    """저장된 `competitor_mentions`에서 **확정 판정**(is_mentioned가 bool)이 있는 이름."""
    judged: set[str] = set()
    for item in mentions or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if name and isinstance(item.get("is_mentioned"), bool):
            judged.add(name)
    return judged


def pending_competitor_names(mentions: Iterable[Any] | None, registered: Sequence[str]) -> list[str]:
    """등록 경쟁사 중 이 답변에 아직 확정 판정이 없는 이름 — 미확정(null)으로 남은 이름도 다시 묻는다."""
    done = {_name_key(name) for name in judged_names(mentions)}
    return [name for name in registered if _name_key(name) not in done]


def unresolved_entry(name: str, *, reason: str, judged_at: datetime) -> dict[str, Any]:
    return {
        "name": name,
        "is_mentioned": None,
        "mention_rank": None,
        "verdict": UNRESOLVED_VERDICT,
        "failure_reason": reason[:200],
        "judged_at": judged_at.isoformat(),
    }


def merge_competitor_mentions(
    existing: Iterable[Any] | None, judged: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """기존 판정은 그대로 두고 새 판정을 더한다.

    - 이미 bool 판정이 있는 이름은 **덮지 않는다**(측정 당시 판정이 정본).
    - 미확정(null)으로 남아 있던 이름은 새 판정으로 바꾼다.
    - 처음 보는 이름은 뒤에 붙인다. 등록에서 빠진 이름은 지우지 않는다.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in (existing or []) if isinstance(item, Mapping)
    ]
    index = {_name_key(str(item.get("name") or "")): position for position, item in enumerate(merged)}
    for item in judged:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = _name_key(name)
        position = index.get(key)
        if position is None:
            index[key] = len(merged)
            merged.append(dict(item))
            continue
        if isinstance(merged[position].get("is_mentioned"), bool):
            continue
        merged[position] = dict(item)
    return merged


def align_judgments(
    names: Sequence[str], parsed: Iterable[Mapping[str, Any]], *, judged_at: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """판정기 출력에서 요청한 이름만 등록 표기로 골라낸다. 빠진 이름은 미확정 목록으로 돌려준다."""
    by_key: dict[str, Mapping[str, Any]] = {}
    for item in parsed:
        if isinstance(item, Mapping) and isinstance(item.get("is_mentioned"), bool):
            by_key.setdefault(_name_key(str(item.get("name") or "")), item)
    judged: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        item = by_key.get(_name_key(name))
        if item is None:
            missing.append(name)
            continue
        rank = item.get("mention_rank")
        judged.append(
            {
                "name": name,
                "is_mentioned": bool(item["is_mentioned"]),
                "mention_rank": rank if type(rank) is int and rank > 0 else None,
                "verdict": JUDGED_VERDICT,
                "judged_at": judged_at.isoformat(),
            }
        )
    return judged, missing


# ── 저장 요약(귀속)의 경쟁 집계 ─────────────────────────────────────────────


def refresh_competitor_outcomes(
    summary: Mapping[str, Any] | None,
    records: Sequence[Any],
    query_targets: Sequence[Any],
    *,
    registered_names: Sequence[str] = (),
) -> dict[str, Any] | None:
    """저장된 전략 요약(`content_summary.strategy`·AE 스냅샷의 `strategy`)의 측정 키워드별 `competitor_outcomes`만 지금 판정으로 다시 센다.

    측정 수·언급률 등 다른 값은 그대로다. 기록→키워드 연결은 보고서 생성(`report_engine.build_strategy_summary`)과
    같다 — 기록의 `ai_query_target_id`, 없으면 키워드 변형의 `query_matrix_id`.
    """
    attribution = summary
    if not isinstance(attribution, Mapping):
        return None
    from app.services.report_engine import _competitor_outcomes

    query_to_target: dict[str, str] = {}
    for target in query_targets:
        for variant in getattr(target, "variants", None) or []:
            query_id = getattr(variant, "query_matrix_id", None)
            if query_id:
                query_to_target[str(query_id)] = str(target.id)
    by_target: dict[str, list[Any]] = {}
    for record in records:
        if not sov_engine.record_is_confirmed(record):
            continue
        target_id = getattr(record, "ai_query_target_id", None)
        key = str(target_id) if target_id else query_to_target.get(str(getattr(record, "query_id", "")))
        if key:
            by_target.setdefault(key, []).append(record)
    refreshed = copy.deepcopy(dict(attribution))
    rows = refreshed.get("query_targets")
    if not isinstance(rows, list):
        return refreshed
    for row in rows:
        if isinstance(row, dict) and row.get("id") is not None:
            row["competitor_outcomes"] = _competitor_outcomes(
                scope_competitor_records(by_target.get(str(row["id"]), []), registered_names)
            )
    return refreshed


# ── 그 달의 고정 기록 ───────────────────────────────────────────────────────


def latest_monthly_report(db, hospital_id: uuid.UUID, year: int, month: int) -> MonthlyReport | None:
    return (
        db.execute(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital_id,
                MonthlyReport.period_year == year,
                MonthlyReport.period_month == month,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.version.desc())
        )
        .scalars()
        .first()
    )


def frozen_record_ids(report: MonthlyReport | None) -> frozenset[str] | None:
    """보고서가 점수에 쓴 관측(스냅샷의 attempt record_id). 스냅샷이 없는 보고서는 None — 근거가 없으면 묻지 않는다."""
    if report is None:
        return None
    render_inputs = report_render_inputs(report.content_summary or {})
    if render_inputs is None:
        return None
    return frozenset(
        str(attempt.get("record_id"))
        for cell in (render_inputs.measurement.cells or [])
        for attempt in (cell.get("attempts") or [])
        if isinstance(attempt, dict) and attempt.get("record_id")
    )


def frozen_month_records(
    db,
    hospital: Hospital,
    year: int,
    month: int,
    *,
    report: MonthlyReport | None,
    now: datetime | None = None,
) -> tuple[MonthlyMeasurementManifest | None, list[Any]]:
    """보고서가 얼린 관측(스냅샷의 attempt record_id)에 해당하는 그 달 매니페스트 기록(원본 ORM 행).

    보고서나 스냅샷이 없으면 빈 목록이다 — 얼린 근거 밖의 답변에 유료 판정을 사지 않는다(재생성 로더와 같은 엄격한
    포함 판정). 얼린 집합이 비어 있는 보고서(점수에 쓴 관측 0)도 빈 목록이다.
    """
    manifest = None
    if report is not None and report.manifest_id is not None:
        manifest = db.get(MonthlyMeasurementManifest, report.manifest_id)
    if manifest is None:
        manifest = (
            db.execute(
                select(MonthlyMeasurementManifest)
                .where(
                    MonthlyMeasurementManifest.hospital_id == hospital.id,
                    MonthlyMeasurementManifest.period_year == year,
                    MonthlyMeasurementManifest.period_month == month,
                )
                .order_by(MonthlyMeasurementManifest.frozen_at.desc())
            )
            .scalars()
            .first()
        )
    if manifest is None:
        return None, []
    observed = (now or datetime.now(UTC)).astimezone(monthly_recovery_deadline(year, month).tzinfo)
    loaded = load_monthly_sov_manifest(
        db,
        manifest,
        legacy_final=legacy_manifest_is_final(
            manifest, deadline_reached=observed >= monthly_recovery_deadline(year, month)
        ),
    )
    frozen = frozen_record_ids(report)
    if not frozen:
        return manifest, []
    records = [
        frozen_record.record for frozen_record in loaded.scored_records if str(frozen_record.id) in frozen
    ]
    return manifest, records


def rejudge_target_records(records: Iterable[Any]) -> list[Any]:
    """재판정 대상 — 확정 성공 답변(자사 판정이 분모에 든 기록)이고 원문이 있는 기록."""
    return [
        record
        for record in records
        if sov_engine.record_is_confirmed(record) and str(getattr(record, "raw_response", "") or "").strip()
    ]


@dataclass(frozen=True, slots=True)
class RejudgeTarget:
    record_id: uuid.UUID
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RejudgePlan:
    hospital_id: uuid.UUID
    year: int
    month: int
    registered_names: tuple[str, ...]
    manifest_id: uuid.UUID | None
    report_id: uuid.UUID | None
    report_version: int | None
    records_total: int
    targets: tuple[RejudgeTarget, ...]
    # 그 달을 지금과 같은 판정 방식으로 쟀는가. 2026-11-09 규칙 대조 이전에 잰 달은 자사·경쟁사 판정이
    # 옛 방식이라, 새로 등록한 경쟁사만 새 방식으로 판정하면 한 보고서에 두 판정 기준이 섞인다.
    judge_policy_current: bool = True

    @property
    def pending_records(self) -> int:
        return len(self.targets)

    @property
    def pending_names(self) -> list[str]:
        names: list[str] = []
        for target in self.targets:
            for name in target.names:
                if name not in names:
                    names.append(name)
        return names

    def summary(self) -> dict[str, Any]:
        return {
            "registered_competitors": list(self.registered_names),
            "records_total": self.records_total,
            "pending_records": self.pending_records,
            "pending_names": self.pending_names,
            "report_id": str(self.report_id) if self.report_id else None,
            "report_version": self.report_version,
            "manifest_id": str(self.manifest_id) if self.manifest_id else None,
            "judge_policy_current": self.judge_policy_current,
        }


def month_judge_policy_is_current(
    manifest: MonthlyMeasurementManifest | None, db=None
) -> bool:
    """그 달을 지금과 같은 판정 정책(규칙 대조 판·보조 판정 문구)으로 판정했는가.

    측정표의 동결 기준이거나, 저장 답변을 지금 규칙으로 다시 판정한 기록(`month_mention_rejudge`)이 기준이다.
    """
    if manifest is None:
        return False
    if db is not None:
        from app.services.month_mention_rejudge import month_judge_policy

        protocol = month_judge_policy(db, manifest)
    else:
        provenance = (
            manifest.platform_provenance if isinstance(manifest.platform_provenance, dict) else {}
        )
        protocol = provenance.get("measurement_protocol")
    if not isinstance(protocol, dict):
        return False
    return protocol.get("judge_prompt_fingerprint") == sov_engine.measurement_protocol().get(
        "judge_prompt_fingerprint"
    )


def build_rejudge_plan(
    db, hospital: Hospital, year: int, month: int, *, now: datetime | None = None
) -> RejudgePlan:
    """읽기 전용 — 어떤 기록의 어떤 이름을 다시 물을지. 공급자 호출·쓰기 없음."""
    registered = registered_competitor_names(hospital)
    report = latest_monthly_report(db, hospital.id, year, month)
    manifest, records = frozen_month_records(db, hospital, year, month, report=report, now=now)
    candidates = rejudge_target_records(records)
    policy_current = month_judge_policy_is_current(manifest, db)
    targets = tuple(
        RejudgeTarget(record.id, tuple(pending))
        for record in candidates
        if policy_current
        and registered
        and (pending := pending_competitor_names(record.competitor_mentions, registered))
    )
    return RejudgePlan(
        hospital_id=hospital.id,
        year=year,
        month=month,
        registered_names=tuple(registered),
        manifest_id=manifest.id if manifest is not None else None,
        report_id=report.id if report is not None else None,
        report_version=report.version if report is not None else None,
        records_total=len(candidates),
        targets=targets,
        judge_policy_current=policy_current,
    )


# ── 실행 ─────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class RejudgeOutcome:
    records_total: int = 0
    pending_records: int = 0
    judged_records: int = 0
    unresolved_records: int = 0
    judged_names: int = 0
    unresolved_names: int = 0
    provider_calls: int = 0
    cost_blocked: bool = False
    cost_block_reason: str | None = None
    registered_competitors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "records_total": self.records_total,
            "pending_records": self.pending_records,
            "judged_records": self.judged_records,
            "unresolved_records": self.unresolved_records,
            "judged_names": self.judged_names,
            "unresolved_names": self.unresolved_names,
            "provider_calls": self.provider_calls,
            "cost_blocked": self.cost_blocked,
            "cost_block_reason": self.cost_block_reason,
            "registered_competitors": list(self.registered_competitors),
        }


async def _judge_with_retries(
    judge: JudgeFn, names: list[str], raw: str, *, judged_at: datetime
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """빠진 이름이 없을 때까지 최대 3회 묻는다 — 장애(예외)와 불완전한 답([]·요청한 이름 누락) 모두 재시도 대상이다.

    확정된 판정은 회차를 넘겨 보존하고, 다음 회차는 아직 빠진 이름만 다시 묻는다.
    """
    judged: list[dict[str, Any]] = []
    missing = list(names)
    last_error: str | None = None
    for _attempt in range(MAX_JUDGMENT_ATTEMPTS):
        try:
            parsed = await judge(missing, raw)
        except Exception as exc:  # noqa: BLE001 — 판정기 장애는 셀 단위 미확정으로 남긴다.
            last_error = f"{type(exc).__name__}:{str(exc)[:80]}"
            continue
        aligned, still_missing = align_judgments(missing, parsed, judged_at=judged_at)
        judged.extend(aligned)
        missing = still_missing
        if not missing:
            return judged, [], None
        last_error = "competitor_name_missing_in_judgment"
    return judged, missing, last_error or "competitor_judgment_failed"


def execute_rejudge(
    db,
    hospital: Hospital,
    year: int,
    month: int,
    *,
    run_id: uuid.UUID | None,
    judge: JudgeFn | None = None,
    now: datetime | None = None,
) -> RejudgeOutcome:
    """계획대로 기록마다 빠진 이름을 묻고 `competitor_mentions`에 합쳐 **기록 단위로 커밋**한다.

    예약은 대상 기록 수 × 1회(판정은 규칙 대조라 실제 공급자 호출은 없다). 쓰기 직전에 행을 잠그고
    빠진 이름을 다시 계산하므로 같은 병원을 두 실행이 겹쳐 돌아도 같은 이름을 두 번 묻지 않고, 나중 쓰기가
    먼저 쓴 bool 판정을 덮지 않는다. 다시 돌리면 미확정으로 남은 이름만 다시 묻는다(멱등).
    """
    judged_at = now or datetime.now(UTC)
    plan = build_rejudge_plan(db, hospital, year, month, now=judged_at)
    if judge is None:
        # 측정 때와 같은 규칙 대조 — 경쟁 병원의 생활권은 이 병원과 같다고 본다(`sov_engine._parse_competitors`).
        # 그 달 측정표가 고정한 신원을 쓴다(측정 뒤 바뀐 병원 정보로 판정하지 않는다).
        manifest = db.get(MonthlyMeasurementManifest, plan.manifest_id) if plan.manifest_id else None
        provenance = getattr(manifest, "platform_provenance", None)
        home = (
            mention_identity.MentionIdentity.from_material(provenance.get("judgment_identity"))
            if isinstance(provenance, dict)
            else None
        ) or mention_identity.MentionIdentity.build(
            hospital.name,
            address=hospital.address,
            phone=hospital.phone,
            region_terms=hospital.region or [],
        )

        async def judge(names: list[str], raw: str) -> list[dict[str, Any]]:
            return await sov_engine._parse_competitors(names, raw, home)

    outcome = RejudgeOutcome(
        records_total=plan.records_total,
        pending_records=plan.pending_records,
        registered_competitors=list(plan.registered_names),
    )
    if not plan.targets:
        return outcome
    # 예약 id는 실행(배달)마다 새로 만든다 — 재시도·인수는 남은 이름만 다시 세므로 같은 id에 다른 수를
    # 예약하면 거절되고, 이미 정산된 영수증을 다시 만나면 거짓 비용 보류가 된다. 예약은 아래 finally가 정산한다.
    reservation_id = f"competitor-rejudge:{run_id or hospital.id}:{year:04d}-{month:02d}:{uuid.uuid4().hex[:12]}"
    decision = _run_async(
        cost_guard.reserve(
            COST_CATEGORY,
            count=len(plan.targets),
            reservation_id=reservation_id,
            hospital_id=hospital.id,
        )
    )
    if not decision.allowed:
        outcome.cost_blocked = True
        outcome.cost_block_reason = decision.reason
        outcome.unresolved_records = plan.pending_records
        outcome.unresolved_names = sum(len(target.names) for target in plan.targets)
        return outcome
    calls = 0
    try:
        for target in plan.targets:
            # 1) 잠금 없이 읽고 판정한다 — LLM 호출 동안 행 잠금을 쥐지 않는다.
            record = db.get(SovRecord, target.record_id)
            if record is None:
                continue
            names = pending_competitor_names(record.competitor_mentions, plan.registered_names)
            raw = record.raw_response or ""
            if not names:
                continue
            with sov_engine.provider_execution_context(
                pool=COST_CATEGORY,
                hospital_id=hospital.id,
                workflow="competitor_rejudge",
                run_id=str(run_id) if run_id is not None else None,
                item_id=str(record.id),
            ):
                judged, missing, error = _run_async(_judge_with_retries(judge, names, raw, judged_at=judged_at))
                calls += sov_engine._provider_calls_made()
            reason = error or "competitor_judgment_failed"
            # 2) 쓰기 직전에 행을 잠그고 빠진 이름을 다시 계산한다 — 그 사이 다른 실행이 쓴 bool 판정은 덮지 않는다.
            locked = db.execute(
                select(SovRecord)
                .where(SovRecord.id == target.record_id)
                .with_for_update()
                # identity map의 옛 값이 아니라 잠근 뒤의 행을 읽는다 — 다른 실행이 그 사이 쓴 판정을 본다.
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if locked is None:
                db.rollback()
                continue
            still_pending = set(pending_competitor_names(locked.competitor_mentions, plan.registered_names))
            judged = [item for item in judged if item["name"] in still_pending]
            missing = [name for name in missing if name in still_pending]
            entries = [*judged, *(unresolved_entry(name, reason=reason, judged_at=judged_at) for name in missing)]
            locked.competitor_mentions = merge_competitor_mentions(locked.competitor_mentions, entries)
            db.commit()
            outcome.judged_names += len(judged)
            outcome.unresolved_names += len(missing)
            if missing:
                outcome.unresolved_records += 1
            elif judged:
                outcome.judged_records += 1
    finally:
        outcome.provider_calls = calls
        _run_async(
            cost_guard.settle_reservation(
                decision.receipt, consumed_units=min(len(plan.targets), calls)
            )
        )
    return outcome


def load_query_targets(db, hospital_id: uuid.UUID) -> list[AIQueryTarget]:
    return list(
        db.execute(
            select(AIQueryTarget)
            .where(AIQueryTarget.hospital_id == hospital_id)
            .options(selectinload(AIQueryTarget.variants))
        )
        .scalars()
        .all()
    )
