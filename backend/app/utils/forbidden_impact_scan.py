"""읽기 전용 운영 조회: 지금 공개 중인 글·병원 공개 문구 가운데 금지 표현 필터에 걸리는 것.

필터(`utils/medical_filter.py`)는 공개 가시성(`content_visibility`)과 공개 병원 문구
(`api/public/site.py`)에도 쓰인다. 패턴을 늘리는 배포는 **배포 즉시** 걸리는 공개 글을 숨긴다.
새 이미지로 이 조회를 먼저 돌려, 숨겨질 글·문구를 배포 전에 본다::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.forbidden_impact_scan

출력: 걸린 항목마다 `{"kind": ..., "id": ..., "labels": [...]}` 한 줄, 끝에 `{"summary": ...}` 한 줄.
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
from app.utils.medical_filter import check_forbidden, check_forbidden_content_fields


def _emit(out: TextIO, payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), file=out, flush=True)


def _treatment_text(entry: object) -> str:
    if isinstance(entry, dict):
        return " ".join(str(value) for value in entry.values())
    return str(entry or "")


def scan(db: Any, *, out: TextIO = sys.stdout) -> Counter:
    """공개 중인 글과 병원 공개 문구를 검사한다. 라벨별 건수를 돌려준다."""

    labels_seen: Counter = Counter()
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
            labels = check_forbidden_content_fields(
                publication_field_values(view), PUBLICATION_CHECK_FIELDS
            )
            if labels:
                labels_seen.update(labels)
                _emit(out, {"kind": "content", "id": item.id, "hospital_id": item.hospital_id,
                            "labels": labels})
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
                labels = check_forbidden(value)
                if labels:
                    labels_seen.update(labels)
                    _emit(out, {"kind": "hospital", "id": hospital.id, "field": field,
                                "labels": labels})
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
                labels = check_forbidden(value)
                if labels:
                    labels_seen.update(labels)
                    _emit(out, {"kind": "philosophy", "id": philosophy.id,
                                "hospital_id": philosophy.hospital_id, "field": field,
                                "labels": labels})
        _emit(out, {"summary": dict(labels_seen)})
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
