"""Fixed LOCAL question sets and staged monthly SoV cohort selection."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.orm import selectinload

from app.models.hospital import Hospital, HospitalStatus
from app.models.sov import AIQueryTarget, AIQueryVariant, SovRecord
from app.services.sov_engine import QUERY_INTENT_LOCAL, classify_query_intent

TRACKING_SET_N_MIN = 10
TRACKING_SET_N_MAX = 15
TRACKING_SET_N_DEFAULT = 15
MEASUREMENT_WINDOW_MONTH_START = "month_start"
MEASUREMENT_WINDOW_MONTH_END = "month_end"
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
    return [
        target
        for target in targets
        if str(getattr(target, "status", "") or "").upper() == "ACTIVE"
        and bool(getattr(target, "in_tracking_set", False))
        and _target_intent(target) == QUERY_INTENT_LOCAL
    ]


def tracking_set_size(members: Iterable[AIQueryTarget]) -> int:
    return len(list(members))


def tracking_set_fingerprint(
    members: Iterable[AIQueryTarget], *, n: int | None = None
) -> str:
    rows = list(members)
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
    size = tracking_set_size(members)
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


def register_tracking_set(
    db, hospital_id: uuid.UUID, n: int = TRACKING_SET_N_DEFAULT
) -> dict[str, object]:
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
