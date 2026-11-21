"""Fixed LOCAL question sets and staged monthly SoV cohort selection."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.hospital import Hospital, HospitalStatus
from app.models.sov import AIQueryTarget, AIQueryVariant, SovRecord
from app.services import measurement_set
from app.services.sov_engine import QUERY_INTENT_LOCAL, classify_query_intent

TRACKING_SET_N_MIN = 10
TRACKING_SET_N_MAX = 15
TRACKING_SET_N_DEFAULT = 15
MEASUREMENT_WINDOW_MONTH_START = "month_start"
MEASUREMENT_WINDOW_MONTH_END = "month_end"
# 1일 새벽 창(00:15~07:00 KST)에 지난달을 재는 동결 프로토콜. 옛 month_end·month_start
# 창과 같은 측정 기준이 아니다 — 기간 비교가 서로 억제된다.
MEASUREMENT_WINDOW_MONTH_START_DAWN = "month_start_dawn"
def _target_query_text(target: AIQueryTarget) -> str:
    return str(getattr(target, "name", "") or "").strip()


def _target_question_texts(target: AIQueryTarget) -> list[str]:
    texts = {
        str(getattr(variant, "query_text", "") or "").strip()
        for variant in (getattr(target, "variants", ()) or ())
        if bool(getattr(variant, "is_active", True))
    }
    texts.discard("")
    if not texts and _target_query_text(target):
        texts.add(_target_query_text(target))
    return sorted(texts)


def _target_intent(target: AIQueryTarget) -> str:
    """Prefer a linked matrix snapshot, then classify the stored question text."""

    for variant in getattr(target, "variants", ()) or ():
        query_matrix = getattr(variant, "query_matrix", None)
        stored = str(getattr(query_matrix, "query_intent", "") or "").upper()
        if stored:
            return stored
    texts = [
        str(getattr(variant, "query_text", "") or "").strip()
        for variant in (getattr(target, "variants", ()) or ())
    ]
    text = next((value for value in texts if value), _target_query_text(target))
    return classify_query_intent(text)


def tracking_set_members(targets: Iterable[AIQueryTarget]) -> list[AIQueryTarget]:
    """이번 달 측정할 질문. 30개 방식 병원은 역할 있는 질문(A~D) 전부, 옛 방식은 LOCAL 플래그 질문."""

    rows = list(targets)
    if measurement_set.uses_curated_set(rows):
        return [target for target in rows if measurement_set.is_set_member(target)]
    return [
        target
        for target in rows
        if str(getattr(target, "status", "") or "").upper() == "ACTIVE"
        and bool(getattr(target, "in_tracking_set", False))
        and _target_intent(target) == QUERY_INTENT_LOCAL
    ]


def tracking_set_size(members: Iterable[AIQueryTarget]) -> int:
    return len(list(members))


def tracking_set_basis_size(members: Iterable[AIQueryTarget]) -> int:
    """측정 기준(`tracking_set_size`)에 넣는 크기 — 30개 방식은 고정 질문 수, 옛 방식은 세트 크기."""

    rows = list(members)
    if measurement_set.uses_curated_set(rows):
        return measurement_set.set_fingerprint(rows)[1]
    return len(rows)


def tracking_set_fingerprint(
    members: Iterable[AIQueryTarget], *, n: int | None = None
) -> str:
    rows = list(members)
    if measurement_set.uses_curated_set(rows):
        # 고정 질문만 — 교체 질문을 바꿔도 다음 달 비교가 이어진다.
        return measurement_set.set_fingerprint(rows)[0]
    size = len(rows) if n is None else n
    texts = sorted(
        {
            text
            for target in rows
            for text in _target_question_texts(target)
        }
    )
    material = f"{size}\n" + "\n".join(texts)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def tracking_set_is_valid(members: Iterable[AIQueryTarget]) -> bool:
    rows = list(members)
    if measurement_set.uses_curated_set(rows):
        return not measurement_set.set_problems(rows)
    size = tracking_set_size(rows)
    return TRACKING_SET_N_MIN <= size <= TRACKING_SET_N_MAX


def _stored_tracking_set_is_valid(targets: Iterable[AIQueryTarget]) -> bool:
    flagged = [target for target in targets if bool(target.in_tracking_set)]
    return tracking_set_is_valid(flagged) and len(tracking_set_members(flagged)) == len(
        flagged
    )


def _validate_n(n: int) -> None:
    if not TRACKING_SET_N_MIN <= n <= TRACKING_SET_N_MAX:
        raise ValueError(
            f"tracking set size must be {TRACKING_SET_N_MIN}..{TRACKING_SET_N_MAX}"
        )


def _load_targets(db, hospital_id: uuid.UUID) -> list[AIQueryTarget]:
    return list(
        db.execute(
            select(AIQueryTarget)
            .options(
                selectinload(AIQueryTarget.variants).selectinload(AIQueryVariant.query_matrix)
            )
            .where(AIQueryTarget.hospital_id == hospital_id)
        )
        .scalars()
        .all()
    )


def propose_tracking_set(
    db, hospital_id: uuid.UUID, n: int = TRACKING_SET_N_DEFAULT
) -> list[AIQueryTarget]:
    _validate_n(n)
    targets = [
        target
        for target in _load_targets(db, hospital_id)
        if str(target.status).upper() == "ACTIVE" and _target_intent(target) == QUERY_INTENT_LOCAL
    ]
    measured_ids = set(
        db.execute(
            select(SovRecord.ai_query_target_id).where(
                SovRecord.hospital_id == hospital_id,
                SovRecord.ai_query_target_id.is_not(None),
            )
        ).scalars()
    )
    measured_query_ids = set(
        db.execute(
            select(SovRecord.query_id).where(SovRecord.hospital_id == hospital_id)
        ).scalars()
    )

    def _has_history(target: AIQueryTarget) -> bool:
        if target.id in measured_ids:
            return True
        return any(
            getattr(variant, "query_matrix_id", None) in measured_query_ids
            for variant in (target.variants or ())
        )

    return sorted(
        targets,
        key=lambda target: (
            not _has_history(target),
            _target_query_text(target),
            str(target.id),
        ),
    )[:n]


def _register_curated_set(
    db, hospital_id: uuid.UUID, targets: list[AIQueryTarget], n: int
) -> dict[str, object]:
    """30개 방식 병원: 역할을 다시 고르지 않고 검사만 한다. `in_tracking_set`은 역할에 맞춘다.

    월간·주간 배치가 매번 이 함수를 부른다 — 측정 이력 기준 자동 제안이 마케터가 정한 세트를
    덮어쓰면 안 된다(측정 키워드 30개 설계 §2-2).
    """

    members = [target for target in targets if measurement_set.is_set_member(target)]
    problems = measurement_set.set_problems(targets)
    member_ids = {target.id for target in members}
    changed = 0
    for target in targets:
        selected = target.id in member_ids
        if bool(target.in_tracking_set) != selected:
            target.in_tracking_set = selected
            changed += 1
    db.flush()
    valid = not problems
    return {
        "hospital_id": str(hospital_id),
        "requested_size": n,
        "proposed_size": len(members),
        "registered_size": len(members) if valid else 0,
        "valid": valid,
        "changed": changed,
        "fingerprint": measurement_set.set_fingerprint(members)[0] if valid else None,
        "reason": None if valid else "measurement set invalid: " + "; ".join(problems),
        "curated": True,
    }


def register_tracking_set(
    db, hospital_id: uuid.UUID, n: int = TRACKING_SET_N_DEFAULT
) -> dict[str, object]:
    current = _load_targets(db, hospital_id)
    if measurement_set.uses_curated_set(current):
        return _register_curated_set(db, hospital_id, current, n)
    proposed = propose_tracking_set(db, hospital_id, n=n)
    valid = tracking_set_is_valid(proposed) and all(
        str(getattr(target, "status", "") or "").upper() == "ACTIVE"
        and _target_intent(target) == QUERY_INTENT_LOCAL
        for target in proposed
    )
    selected_ids = {target.id for target in proposed} if valid else set()
    targets = _load_targets(db, hospital_id)
    changed = 0
    for target in targets:
        selected = target.id in selected_ids
        if bool(target.in_tracking_set) != selected:
            target.in_tracking_set = selected
            changed += 1
    db.flush()
    return {
        "hospital_id": str(hospital_id),
        "requested_size": n,
        "proposed_size": len(proposed),
        "registered_size": len(proposed) if valid else 0,
        "valid": valid,
        "changed": changed,
        "fingerprint": tracking_set_fingerprint(proposed, n=n) if valid else None,
        "reason": (
            None
            if valid
            else (
                "not enough LOCAL ACTIVE targets: "
                f"found {len(proposed)}, requires {TRACKING_SET_N_MIN}..{TRACKING_SET_N_MAX}"
            )
        ),
    }


def _convertible_hospitals(db) -> list[Hospital]:
    return list(
        db.execute(
            select(Hospital)
            .where(
                Hospital.status == HospitalStatus.ACTIVE,
                exists().where(SovRecord.hospital_id == Hospital.id),
            )
            .order_by(Hospital.created_at, Hospital.id)
        )
        .scalars()
        .all()
    )


def _hospital_has_sov_record(db, hospital_id: uuid.UUID) -> bool:
    return (
        db.execute(
            select(SovRecord.id).where(SovRecord.hospital_id == hospital_id).limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _enroll_new_hospitals(
    db, n: int, registered: list[dict[str, str]], not_enrolled: list[dict[str, str]]
) -> list[dict[str, str]]:
    """마이그레이션 0063 이후 계약된 ACTIVE 병원을 월간 코호트에 편입한다.

    플래그가 없으면 월간 측정에서 조용히 빠지고 보고서가 주간 자료로만 만들어져 전달이
    영구히 막힌다(2026-10 9/7·9/15 계약 병원). 플래그는 저장된 진실로 유지하므로 비교 가능성·
    manifest 로직은 그대로이고, 한 번 편입한 병원은 자동으로 빼지 않는다.
    기록(SovRecord)이 없거나 유효한 고정 세트를 만들 수 없는 병원은 편입하지 않고 사유를 돌려준다.
    """

    hospitals = list(
        db.execute(
            select(Hospital)
            .where(
                Hospital.status == HospitalStatus.ACTIVE,
                Hospital.monthly_sov_cohort.is_(False),
            )
            .order_by(Hospital.created_at, Hospital.id)
        )
        .scalars()
        .all()
    )
    enrolled: list[dict[str, str]] = []
    for hospital in hospitals:
        if not _hospital_has_sov_record(db, hospital.id):
            not_enrolled.append(
                {
                    "name": hospital.name,
                    "reason": "no SovRecord",
                    "hospital_id": str(hospital.id),
                }
            )
            continue
        result = register_tracking_set(db, hospital.id, n=n)
        if bool(result["valid"]):
            hospital.monthly_sov_cohort = True
            item = {"hospital_id": str(hospital.id), "name": hospital.name}
            enrolled.append(item)
            registered.append(item)
        else:
            not_enrolled.append(
                {
                    "name": hospital.name,
                    "reason": str(result["reason"]),
                    "hospital_id": str(hospital.id),
                }
            )
    return enrolled


def register_convertible_tracking_sets(
    db, n: int = TRACKING_SET_N_DEFAULT, *, enroll_new: bool = False
) -> dict[str, object]:
    """Register tracking sets for the cohort; optionally enroll newly contracted hospitals."""

    _validate_n(n)
    registered: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    hospitals = list(
        db.execute(
            select(Hospital)
            .where(
                Hospital.status == HospitalStatus.ACTIVE,
                Hospital.monthly_sov_cohort.is_(True),
            )
            .order_by(Hospital.created_at, Hospital.id)
        )
        .scalars()
        .all()
    )
    for hospital in hospitals:
        if not _hospital_has_sov_record(db, hospital.id):
            blocked.append(
                {
                    "name": hospital.name,
                    "reason": "no SovRecord",
                    "hospital_id": str(hospital.id),
                }
            )
            continue
        result = register_tracking_set(db, hospital.id, n=n)
        if bool(result["valid"]):
            registered.append({"hospital_id": str(hospital.id), "name": hospital.name})
        else:
            blocked.append(
                {
                    "name": hospital.name,
                    "reason": str(result["reason"]),
                    "hospital_id": str(hospital.id),
                }
            )
    not_enrolled: list[dict[str, str]] = []
    enrolled = (
        _enroll_new_hospitals(db, n, registered, not_enrolled) if enroll_new else []
    )
    return {
        "target_count": len(hospitals),
        "registered": registered,
        "blocked": blocked,
        "enrolled": enrolled,
        "not_enrolled": not_enrolled,
    }


def _hospital_matches_monthly_cohort(hospital: Hospital) -> bool:
    return bool(getattr(hospital, "monthly_sov_cohort", False))


def iter_monthly_sov_cohort(db) -> list[Hospital]:
    """편입된 모든 병원을 돌려준다. 질문 수 변화로 서비스 기간에서 빼지 않는다.

    예전에는 SOV_MONTHLY_COHORT_LIMIT에서 잘라 초과 병원이 조용히 측정되지 않았다.
    상한은 이제 비용 경고 기준일 뿐이다(`run_monthly_sov_measurement`가 경고·인시던트).
    같은 이유로 등록 뒤 질문이 10→9 또는 0개가 되어도 해당 월의 보고 대상은 유지한다.
    현재 질문 집합의 유효성은 측정 가능성/표본 크기 사실이지 계약 코호트 판정이 아니다.
    """

    convertible = _convertible_hospitals(db)
    execute = getattr(db, "execute", None)
    if not callable(execute):
        return [hospital for hospital in convertible if _hospital_matches_monthly_cohort(hospital)]
    enrolled = list(
        execute(
            select(Hospital)
            .where(
                Hospital.status == HospitalStatus.ACTIVE,
                Hospital.monthly_sov_cohort.is_(True),
            )
            .order_by(Hospital.created_at, Hospital.id)
        )
        .scalars()
        .all()
    )
    by_id = {
        hospital.id: hospital
        for hospital in (*convertible, *enrolled)
        if _hospital_matches_monthly_cohort(hospital)
    }
    return list(by_id.values())


def hospital_in_monthly_cohort(db, hospital_id: uuid.UUID) -> bool:
    return any(hospital.id == hospital_id for hospital in iter_monthly_sov_cohort(db))


def monthly_sov_guard_units(
    hospital_count: int,
    n: int,
    *,
    v0_new: int = 0,
    retry: int = 0,
    weekly_remaining_hospitals: int = 0,
    weekly_specs: int = 50,
) -> int:
    """Coexistence envelope: monthly fixed set + V0 + legacy weekly + retry."""

    return (
        hospital_count * n * 2 * 5
        + v0_new * 150
        + weekly_remaining_hospitals * weekly_specs * 5
        + retry
    )


# 관측 칸 하나의 비용 단위: 답변 1회 + 자사 보조 판정(규칙 대조가 '아니다'라고 했는데 이름 조각이
# 있을 때만 Haiku 1회). 경쟁 병원은 규칙 대조만 쓴다(2026-11-09). 보조 판정 비율 실측이 없어
# 4칸 중 1칸으로 보수적으로 잡는다 — 예전의 ×3(답변 + 자사 판정 + 경쟁 판정 묶음)은 규칙 판정
# 이전 값이라, 30문항이면 일 9,000으로 10곳만 잡혀 12/1에 거짓 '한도 초과' 사고가 열린다.
MONTHLY_SLOT_COST_UNITS = 1.25


def monthly_sov_cohort_cost_threshold(n: int = measurement_set.SET_TOTAL_MAX) -> int:
    """Conservative one-dawn capacity before retries; actual spending stays guarded.

    `n`은 병원 한 곳의 질문 수다. 기본은 30개 세트의 최대치 — 옛 15개 병원이 섞여 있으면 실제
    여유는 더 크다(경고선은 자르는 상한이 아니라 경고 기준일 뿐이다).
    """
    platforms = 2 if settings.OPENROUTER_API_KEY else 1
    repeats = min(settings.SOV_REPEAT_COUNT_WEEKLY, 20)
    units = max(1, int(n * platforms * repeats * MONTHLY_SLOT_COST_UNITS))
    budget = min(
        settings.COST_GUARD_DAILY_SOV_QUERIES,
        settings.COST_GUARD_MONTHLY_SOV_QUERIES,
    )
    return max(1, budget // units)
