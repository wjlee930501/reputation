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
    selected_ids = {target.id for target in proposed}
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
        "registered_size": len(proposed),
        "valid": tracking_set_is_valid(proposed),
        "changed": changed,
        "fingerprint": tracking_set_fingerprint(proposed, n=n),
    }


def iter_monthly_sov_cohort(db, *, limit: int | None) -> list[Hospital]:
    if limit is None or limit <= 0:
        return []
    hospitals = list(
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
    cohort: list[Hospital] = []
    for hospital in hospitals:
        if tracking_set_is_valid(tracking_set_members(_load_targets(db, hospital.id))):
            cohort.append(hospital)
            if len(cohort) >= limit:
                break
    return cohort


def hospital_in_monthly_cohort(
    db, hospital_id: uuid.UUID, *, limit: int | None
) -> bool:
    return any(hospital.id == hospital_id for hospital in iter_monthly_sov_cohort(db, limit=limit))


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
