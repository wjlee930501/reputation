"""읽기 전용 운영 조회: 지금 공개 중인 글·병원 공개 문구 가운데 금지 표현 필터에 걸리는 것.

필터(`utils/medical_filter.py`)는 공개 가시성(`content_visibility`)과 공개 병원 문구
(`api/public/site.py`)에도 쓰인다. 패턴을 늘리는 배포는 **배포 즉시** 걸리는 공개 글을 숨긴다.
새 이미지로 이 조회를 먼저 돌려, 숨겨질 글·문구를 배포 전에 본다::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.forbidden_impact_scan

새 필터는 막는 것만 늘리지 않는다 — 문맥 예외가 넓어지면 운영 필터가 숨기던 글이 **배포 즉시 다시
보인다**. 그래서 글·문구마다 운영 필터 동결본(`utils/medical_filter_baseline.py`)의 판정과 함께 본다.

출력: 새 필터나 운영 필터 어느 한쪽에라도 걸린 항목마다 한 줄 —
`{"kind": ..., "id": ..., "labels": [...], "baseline_labels": [...], "change": ...}`.
`change`는 `newly_blocked`(새로 숨겨진다)·`newly_unblocked`(다시 보인다)·`still_blocked`다.
끝에 `{"summary": {...}, "changes": {...}, "baseline": "<커밋>"}` 한 줄.
쓰기는 하지 않는다 — 첫 문장이 `SET TRANSACTION READ ONLY`이고 끝나면 롤백한다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any, TextIO

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.services.content_publication import PUBLICATION_CHECK_FIELDS, publication_field_values
from app.services.content_visibility import approved_public_view
from app.utils import medical_filter_baseline as baseline
from app.utils.medical_filter import check_forbidden, check_forbidden_content_fields


def _emit(out: TextIO, payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), file=out, flush=True)


def _treatment_text(entry: object) -> str:
    if isinstance(entry, dict):
        return " ".join(str(value) for value in entry.values())
    return str(entry or "")


def _change(labels: list[str], baseline_labels: list[str]) -> str:
    if labels and not baseline_labels:
        return "newly_blocked"
    if baseline_labels and not labels:
        return "newly_unblocked"
    return "still_blocked"


def scan(db: Any, *, out: TextIO = sys.stdout) -> Counter:
    """공개 중인 글과 병원 공개 문구를 검사한다. 새 필터 기준 라벨별 건수를 돌려준다."""

    labels_seen: Counter = Counter()
    changes: Counter = Counter()

    def _report(payload: dict[str, Any], labels: list[str], baseline_labels: list[str]) -> None:
        if not labels and not baseline_labels:
            return
        labels_seen.update(labels)
        change = _change(labels, baseline_labels)
        changes[change] += 1
        _emit(out, {**payload, "labels": labels, "baseline_labels": baseline_labels,
                    "change": change})

    db.rollback()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        items = db.execute(
            select(ContentItem)
            .where(ContentItem.status == ContentStatus.PUBLISHED)
            .options(selectinload(ContentItem.active_revision))
        ).scalars()
        for item in items:
            view = approved_public_view(item)
            if view is None:
                continue
            values = publication_field_values(view)
            _report(
                {"kind": "content", "id": item.id, "hospital_id": item.hospital_id},
                check_forbidden_content_fields(values, PUBLICATION_CHECK_FIELDS),
                baseline.check_forbidden_content_fields(values, PUBLICATION_CHECK_FIELDS),
            )
        for hospital in db.execute(select(Hospital)).scalars():
            fields = {
                "hero_headline": hospital.hero_headline or "",
                "hero_description": hospital.hero_description or "",
                **{
                    f"treatments[{index}]": _treatment_text(entry)
                    for index, entry in enumerate(hospital.treatments or [])
                },
            }
            for field, value in fields.items():
                _report(
                    {"kind": "hospital", "id": hospital.id, "field": field},
                    check_forbidden(value),
                    baseline.check_forbidden(value),
                )
        philosophies = db.execute(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED
            )
        ).scalars()
        for philosophy in philosophies:
            # 공개 about 문장(api/public/site.py)과, 걸리면 작가 요구에서 빠지고 운영자 기록이 남는
            # 필수 문구(`must_use_verbatim`).
            fields = {
                "positioning_statement": philosophy.positioning_statement or "",
                "patient_promise": philosophy.patient_promise or "",
                **{
                    f"must_use_messages[{index}]": str(message or "")
                    for index, message in enumerate(philosophy.must_use_messages or [])
                },
            }
            for field, value in fields.items():
                _report(
                    {"kind": "philosophy", "id": philosophy.id,
                     "hospital_id": philosophy.hospital_id, "field": field},
                    check_forbidden(value),
                    baseline.check_forbidden(value),
                )
        _emit(out, {"summary": dict(labels_seen), "changes": dict(changes),
                    "baseline": baseline.BASELINE_COMMIT})
    finally:
        db.rollback()
    return labels_seen


def main() -> int:
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        scan(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
